"""Free competitor research.

Two free sources:
1. The pages currently ranking on Google for the focus keyword (URLs come
   from the research step's web search).
2. Your listed competitors' own sitemaps — their posts on the same topic,
   and topics they cover that you don't (content gaps).

Each page is fetched politely (robots.txt respected, identifiable
user-agent, one request at a time per site) and reduced to STATISTICS
only: word count, headings, FAQ/table/image/video usage, schema types and
the terms most pages share. Their sentences are never passed to the
writer, which keeps the new article original.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import urllib.robotparser
from collections import Counter
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import Site
from .textutil import STOP, html_to_text, similarity, words

UA = "Mozilla/5.0 (compatible; SEO-Autopilot-Research/1.0)"
SKIP_DOMAINS = ("youtube.com", "reddit.com", "quora.com", "facebook.com", "instagram.com",
                "tiktok.com", "x.com", "twitter.com", "pinterest.com", "linkedin.com", "amazon.")
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _allowed(url: str) -> bool:
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(host + "/robots.txt", timeout=8, headers={"User-Agent": UA})
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            _robots[host] = rp
        except requests.RequestException:
            _robots[host] = None
    rp = _robots[host]
    return True if rp is None else rp.can_fetch(UA, url)


def _get(url: str, timeout: int = 15) -> str:
    if any(d in url for d in SKIP_DOMAINS) or not _allowed(url):
        return ""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            return r.text
    except requests.RequestException:
        pass
    return ""


def analyse_page(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        if t.name == "script" and t.get("type") == "application/ld+json":
            continue
        t.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    schema_types = set()
    for s in soup.find_all("script", type="application/ld+json"):
        for m in re.finditer(r'"@type"\s*:\s*"([A-Za-z]+)"', s.get_text()):
            schema_types.add(m.group(1))
    text = html_to_text(str(main))
    heads = [(h.name, h.get_text(" ", strip=True)) for h in main.find_all(["h2", "h3"])]
    return {
        "url": url,
        "title": (soup.title.string or "").strip() if soup.title else "",
        "words": len(words(text)),
        "h2": [t for n, t in heads if n == "h2"][:25],
        "h3_count": sum(1 for n, _ in heads if n == "h3"),
        "has_faq": "FAQPage" in schema_types or bool(re.search(r"frequently asked|faq", text[-4000:], re.I)),
        "tables": len(main.find_all("table")),
        "images": len(main.find_all("img")),
        "videos": len(main.find_all(["iframe", "video"])),
        "lists": len(main.find_all(["ul", "ol"])),
        "schema": sorted(schema_types),
        "_text": text,
    }


def _terms(text: str) -> Counter:
    w = [x for x in words(text) if len(x) > 2]
    c = Counter()
    for i in range(len(w) - 1):
        if w[i] not in STOP and w[i + 1] not in STOP:
            c[f"{w[i]} {w[i + 1]}"] += 1
    for x in w:
        if x not in STOP and len(x) > 4:
            c[x] += 1
    return c


def analyse(urls: list[str], keyword: str, our_domain: str, limit: int = 6) -> dict:
    pages = []
    seen = set()
    for u in urls:
        host = urlparse(u).netloc.replace("www.", "")
        if not u.startswith("http") or host in seen or our_domain in host:
            continue
        seen.add(host)
        html = _get(u)
        if html:
            p = analyse_page(u, html)
            if p["words"] > 250:
                pages.append(p)
        time.sleep(1)
        if len(pages) >= limit:
            break
    if not pages:
        return {"pages": [], "note": "no competitor pages could be fetched"}

    doc_freq = Counter()
    for p in pages:
        doc_freq.update(set(_terms(p["_text"]).keys()))
    kw_tokens = set(words(keyword))
    shared = [t for t, n in doc_freq.most_common(200)
              if n >= max(2, len(pages) // 2) and not set(t.split()) <= kw_tokens][:40]
    wc = [p["words"] for p in pages]
    summary = {
        "pages_analysed": len(pages),
        "median_words": int(statistics.median(wc)),
        "max_words": max(wc),
        "share_with_faq": round(sum(p["has_faq"] for p in pages) / len(pages), 2),
        "share_with_tables": round(sum(1 for p in pages if p["tables"]) / len(pages), 2),
        "median_images": int(statistics.median(p["images"] for p in pages)),
        "median_h2": int(statistics.median(len(p["h2"]) for p in pages)),
        "schema_types_used": sorted({s for p in pages for s in p["schema"]}),
        "terms_most_competitors_use": shared,
        "competitor_headings": [{"url": p["url"], "title": p["title"], "h2": p["h2"]} for p in pages],
    }
    for p in pages:
        p.pop("_text")
    return {"summary": summary, "pages": pages}


# ── competitor sitemaps ────────────────────────────────────
def sitemap_urls(domain: str, max_urls: int = 3000) -> list[str]:
    base = domain if domain.startswith("http") else f"https://{domain}"
    out, queue, seen = [], [], set()
    for path in ("/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap.xml", "/post-sitemap.xml"):
        queue.append(base.rstrip("/") + path)
    while queue and len(out) < max_urls and len(seen) < 25:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = requests.get(sm, timeout=12, headers={"User-Agent": UA})
        except requests.RequestException:
            continue
        if r.status_code != 200 or "<" not in r.text[:200]:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        for loc in locs:
            if loc.endswith(".xml") or ".xml?" in loc:
                if any(k in loc for k in ("post", "blog", "article", "sitemap_index", "wp-sitemap-posts", "sitemap-")) \
                        and not any(k in loc for k in ("image", "video", "tag", "author", "category")):
                    queue.append(loc)
            else:
                out.append(loc)
        time.sleep(0.5)
    return out[:max_urls]


def slug_title(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    return re.sub(r"[-_]+", " ", re.sub(r"\.\w+$", "", slug))


def related_competitor_posts(site: Site, keyword: str, cache: dict) -> list[str]:
    out = []
    for comp in site.competitors:
        if comp not in cache:
            cache[comp] = sitemap_urls(comp)
        scored = sorted(((similarity(keyword, slug_title(u)), u) for u in cache[comp]), reverse=True)
        out += [u for s, u in scored[:2] if s >= 0.3]
    return out


def content_gaps(site: Site, our_titles: list[str], limit: int = 40) -> list[dict]:
    """Competitor topics with nothing similar on our site — used by the planner."""
    gaps = []
    for comp in site.competitors:
        for u in sitemap_urls(comp, max_urls=1500):
            t = slug_title(u)
            if len(t.split()) < 3:
                continue
            if all(similarity(t, ours) < 0.4 for ours in our_titles):
                gaps.append({"keyword": t, "competitor": comp, "url": u})
    seen, out = set(), []
    for g in gaps:
        k = " ".join(sorted(set(words(g["keyword"])) - STOP))
        if k not in seen:
            seen.add(k)
            out.append(g)
    (site.data_dir / "competitor_gaps.json").write_text(json.dumps(out[:300], indent=1), encoding="utf-8")
    return out[:limit]
