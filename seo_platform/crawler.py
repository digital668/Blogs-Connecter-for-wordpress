"""Crawls a site (via the WP API + live HTML) and produces an on-page SEO audit."""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import Site
from .textutil import fingerprint, html_to_text, words
from .wordpress import UA, WordPress

MAX_PAGES = 400
MAX_LINK_CHECKS = 300

SEVERITY = {"high": 3, "medium": 2, "low": 1}


def _fetch(session: requests.Session, url: str) -> tuple[int, str, float]:
    t0 = time.time()
    try:
        r = session.get(url, timeout=25, allow_redirects=True)
        return r.status_code, r.text if "html" in r.headers.get("content-type", "") else "", time.time() - t0
    except requests.RequestException:
        return 0, "", time.time() - t0


def _analyse_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    md = soup.find("meta", attrs={"name": "description"})
    robots = soup.find("meta", attrs={"name": "robots"})
    canon = soup.find("link", rel="canonical")
    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    imgs = soup.find_all("img")
    host = urlparse(url).netloc
    internal, external = set(), set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        if not href.startswith("http"):
            continue
        (internal if urlparse(href).netloc == host else external).add(href)
    return {
        "title": title,
        "meta_description": (md.get("content") or "").strip() if md else "",
        "noindex": bool(robots and "noindex" in (robots.get("content") or "").lower()),
        "canonical": canon.get("href") if canon else "",
        "h1": h1s,
        "images": len(imgs),
        "images_no_alt": sum(1 for i in imgs if not (i.get("alt") or "").strip()),
        "has_schema": bool(soup.find("script", type="application/ld+json")),
        "internal_links": sorted(internal),
        "external_links": sorted(external),
    }


def crawl(site: Site) -> dict:
    wp = WordPress(site)
    items = []
    for kind, rows in (("post", wp.posts()), ("page", wp.pages())):
        for p in rows:
            text = html_to_text(p.get("content", {}).get("rendered", ""))
            items.append({
                "id": p["id"], "type": kind, "url": p["link"], "slug": p.get("slug", ""),
                "wp_title": html_to_text(p.get("title", {}).get("rendered", "")),
                "date": p.get("date"), "modified": p.get("modified"),
                "words": len(words(text)), "fingerprint": fingerprint(text),
            })
    items = items[:MAX_PAGES]

    session = requests.Session()
    session.headers["User-Agent"] = UA
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda it: _fetch(session, it["url"]), items))
    for it, (status, html, secs) in zip(items, results):
        it["status"], it["load_seconds"] = status, round(secs, 2)
        it.update(_analyse_html(it["url"], html) if html else {})

    # Site-level checks
    site_checks = {}
    for name, path in (("robots_txt", "/robots.txt"), ("sitemap", "/wp-sitemap.xml"),
                       ("sitemap_index", "/sitemap_index.xml")):
        status, _, _ = _fetch(session, site.base + path)
        site_checks[name] = status == 200
    site_checks["https"] = site.base.startswith("https://")

    # Broken internal links
    all_links = Counter(l for it in items for l in it.get("internal_links", []))
    known = {it["url"].rstrip("/") for it in items}
    to_check = [l for l, _ in all_links.most_common() if l.rstrip("/") not in known
                and not any(x in l for x in ("/wp-admin", "/wp-login", "?", "/feed", "/tag/", "/category/", "/author/", "/page/"))]
    to_check = to_check[:MAX_LINK_CHECKS]
    with ThreadPoolExecutor(max_workers=6) as ex:
        statuses = dict(zip(to_check, ex.map(lambda u: _fetch(session, u)[0], to_check)))
    broken = {u for u, s in statuses.items() if s >= 400}

    issues = audit(items, broken, site_checks)
    inventory = {
        "site": site.key,
        "crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "site_checks": site_checks,
        "pages": items,
        "issues": issues,
    }
    (site.data_dir / "inventory.json").write_text(json.dumps(inventory, indent=1), encoding="utf-8")
    return inventory


def audit(items: list[dict], broken: set[str], site_checks: dict) -> list[dict]:
    issues: list[dict] = []

    def add(sev, code, url, msg):
        issues.append({"severity": sev, "code": code, "url": url, "message": msg})

    titles, descs = defaultdict(list), defaultdict(list)
    inbound = Counter()
    for it in items:
        for l in it.get("internal_links", []):
            inbound[l.rstrip("/")] += 1

    for it in items:
        u = it["url"]
        if it.get("status", 0) >= 400 or it.get("status") == 0:
            add("high", "page_error", u, f"Page returns HTTP {it.get('status')}")
            continue
        if it.get("noindex"):
            add("high", "noindex", u, "Page is set to noindex — Google will not show it")
        t = it.get("title", "")
        if not t:
            add("high", "title_missing", u, "Missing <title>")
        elif len(t) > 60:
            add("low", "title_long", u, f"Title is {len(t)} chars (aim ≤ 60)")
        elif len(t) < 30:
            add("medium", "title_short", u, f"Title is only {len(t)} chars (aim 30–60)")
        titles[t.lower()].append(u)
        d = it.get("meta_description", "")
        if not d:
            add("medium", "meta_missing", u, "Missing meta description")
        elif len(d) > 160:
            add("low", "meta_long", u, f"Meta description is {len(d)} chars (aim ≤ 160)")
        if d:
            descs[d.lower()].append(u)
        h1 = it.get("h1", [])
        if not h1:
            add("medium", "h1_missing", u, "No H1 heading")
        elif len(h1) > 1:
            add("low", "h1_multiple", u, f"{len(h1)} H1 headings")
        if it.get("images_no_alt"):
            add("low", "img_alt", u, f"{it['images_no_alt']} image(s) missing alt text")
        if it["type"] == "post" and it["words"] < 600:
            add("medium", "thin_content", u, f"Thin content: {it['words']} words")
        if not it.get("canonical"):
            add("low", "canonical_missing", u, "No canonical tag")
        if it["type"] == "post" and inbound[u.rstrip("/")] == 0:
            add("medium", "orphan", u, "No other page links here (orphan post)")
        if it.get("load_seconds", 0) > 3:
            add("low", "slow", u, f"HTML took {it['load_seconds']}s to load")
        for l in it.get("internal_links", []):
            if l in broken:
                add("high", "broken_link", u, f"Broken internal link → {l}")

    for t, urls in titles.items():
        if t and len(urls) > 1:
            for u in urls:
                add("medium", "title_duplicate", u, f"Same title used on {len(urls)} pages")
    for d, urls in descs.items():
        if len(urls) > 1:
            for u in urls:
                add("low", "meta_duplicate", u, f"Same meta description on {len(urls)} pages")

    if not site_checks.get("robots_txt"):
        add("medium", "robots_missing", "/robots.txt", "robots.txt not found")
    if not (site_checks.get("sitemap") or site_checks.get("sitemap_index")):
        add("high", "sitemap_missing", "/sitemap", "No XML sitemap found")
    if not site_checks.get("https"):
        add("high", "no_https", "/", "Site is not on HTTPS")

    issues.sort(key=lambda i: -SEVERITY[i["severity"]])
    return issues


def health_score(issues: list[dict], pages: int) -> int:
    if pages == 0:
        return 0
    penalty = sum(SEVERITY[i["severity"]] for i in issues)
    return max(0, round(100 - penalty * 25 / max(pages, 4)))
