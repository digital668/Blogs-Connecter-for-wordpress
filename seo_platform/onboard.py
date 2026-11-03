"""Adds a website from just its address.

Reads the homepage, the site's pages/categories (public WordPress API) and
sitemap, detects the page builder, then asks Claude (with web search for
real competitors) to fill in name, niche, audience, tone, keywords,
competitors, service pages to link, call-to-action and writing rules.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import llm
from .config import Site, read_raw, save_raw
from .textutil import html_to_text

UA = "Mozilla/5.0 (compatible; SEO-Autopilot/1.0)"

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Brand name as the site shows it"},
        "niche": {"type": "string", "description": "What the business does — specific services/products, 1–2 sentences"},
        "audience": {"type": "string", "description": "Who the blog should be written for"},
        "tone": {"type": "string"},
        "country": {"type": "string", "description": "2-letter code of the main market the content should target (US, IN, GB…)"},
        "seed_keywords": {"type": "array", "items": {"type": "string"}, "description": "4–6 real search phrases buyers use"},
        "competitors": {"type": "array", "items": {"type": "string"}, "description": "2–3 competitor DOMAINS that rank for those keywords (bare domain, e.g. example.com)"},
        "ymyl": {"type": "boolean", "description": "true if content is health, medical, finance, legal or safety advice"},
        "service_pages": {"type": "array", "items": {"type": "string"}, "description": "Up to 10 of the site's own service/product page URLs worth linking from blogs"},
        "contact_url": {"type": "string", "description": "The site's contact / enquiry / booking page URL"},
        "cta_title": {"type": "string"}, "cta_text": {"type": "string"}, "cta_button": {"type": "string"},
        "writing_rules": {"type": "array", "items": {"type": "string"},
                          "description": "3–6 short rules specific to this business (what to emphasise, what never to claim)"},
    },
    "required": ["name", "niche", "audience", "tone", "country", "seed_keywords", "competitors", "ymyl",
                 "service_pages", "contact_url", "cta_title", "cta_text", "cta_button", "writing_rules"],
}


def normalise_url(url: str) -> str:
    url = url.strip().split("#")[0].split("?")[0].rstrip("/")
    if not re.match(r"^https?://", url):
        url = "https://" + url
    return url


def site_key(url: str, taken: set[str]) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    core = parts[:-2] if len(parts) > 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") else parts[:-1]
    key = re.sub(r"[^a-z0-9]+", "-", "-".join(p for p in core if p not in ("blog", "blogs")) or parts[0]).strip("-")[:30]
    base, i = key, 2
    while key in taken:
        key, i = f"{base}-{i}", i + 1
    return key


def _get(url: str, **kw) -> requests.Response | None:
    try:
        return requests.get(url, timeout=20, headers={"User-Agent": UA}, **kw)
    except requests.RequestException:
        return None


def gather(url: str) -> dict:
    out: dict = {"url": url, "home_text": "", "title": "", "description": "", "pages": [], "categories": [],
                 "builder": "auto", "wordpress": False}
    r = _get(url + "/")
    if r is not None and r.status_code == 200:
        html = r.text
        soup = BeautifulSoup(html, "lxml")
        out["title"] = (soup.title.string or "").strip() if soup.title else ""
        md = soup.find("meta", attrs={"name": "description"})
        out["description"] = (md.get("content") or "").strip() if md else ""
        out["home_text"] = html_to_text(html)[:5000]
        low = html.lower()
        out["wordpress"] = "wp-content" in low or "wp-json" in low
        out["builder"] = ("divi" if ("/themes/divi" in low or "et_pb_" in low or "elegant themes" in low)
                          else "elementor" if "elementor" in low else "auto")
        host = urlparse(url).netloc
        links = {a["href"].split("#")[0] for a in soup.find_all("a", href=True)
                 if urlparse(a["href"]).netloc in ("", host) and len(a["href"]) > 1}
        out["nav_links"] = sorted(l if l.startswith("http") else url + "/" + l.lstrip("/") for l in links)[:80]
    for kind in ("pages", "categories"):
        fields = "link,title" if kind == "pages" else "name,count"
        rr = _get(f"{url}/wp-json/wp/v2/{kind}", params={"per_page": 100, "_fields": fields})
        if rr is not None and rr.status_code == 200:
            try:
                data = rr.json()
            except ValueError:
                continue
            out["wordpress"] = True
            if kind == "pages":
                out["pages"] = [{"url": p["link"], "title": html_to_text(p["title"]["rendered"])} for p in data][:80]
            else:
                out["categories"] = [{"name": c["name"], "posts": c.get("count", 0)} for c in data]
    return out


def profile(url: str, info: dict, form: dict) -> tuple[dict, dict]:
    prompt = f"""Set up an SEO blog-writing profile for this website. Use web search to confirm what the
business does and to find 2–3 REAL competitor domains that rank on Google for its main services
in its market.

Website: {url}
Page title: {info.get('title')}
Meta description: {info.get('description')}
Homepage text (truncated): {info.get('home_text', '')[:3500]}
Site pages: {[p['url'] for p in info.get('pages', [])][:60] or info.get('nav_links', [])[:60]}
Owner's target country (may be blank): {form.get('country') or ''}
Owner's notes: {form.get('notes') or ''}
Competitors the owner named (keep them): {form.get('competitors') or ''}

Rules: be specific to THIS business. Keywords must be real phrases buyers search (not "benefits of X").
service_pages and contact_url must be URLs that exist on this site (from the lists above).
writing_rules must forbid inventing results/statistics/testimonials and, if relevant, guaranteed outcomes."""
    return llm.structured(system="You are a senior SEO strategist onboarding a new client website.",
                          prompt=prompt, tool_name="submit_profile", schema=PROFILE_SCHEMA,
                          web_search=True, max_searches=4, max_tokens=4000)


def find_existing(raw: dict, url: str) -> dict | None:
    target = Site(key="x", name="x", url=url)
    for e in raw["sites"]:
        if Site(key="x", name="x", url=e["url"]).domain == target.domain:
            return e
    return None


def add_site(form: dict, issue: int | None = None) -> tuple[dict, str, dict]:
    """Returns (entry, status, info). status = 'added' | 'linked' (already configured)."""
    url = normalise_url(form["url"])
    raw = read_raw()
    existing = find_existing(raw, url)
    if existing:
        if issue:
            existing["issue"] = issue
        save_raw(raw)
        return existing, "linked", {}

    info = gather(url)
    prof, _usage = profile(url, info, form)
    host_ok = lambda u: isinstance(u, str) and Site(key="x", name="x", url=url).owns(u)
    service_pages = [u for u in prof.get("service_pages", []) if host_ok(u)][:10]
    contact = prof.get("contact_url") if host_ok(prof.get("contact_url")) else url + "/"
    competitors = [c.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
                   for c in (form.get("competitors") or "").replace("\n", ",").split(",") if c.strip()]
    competitors += [c.lower().removeprefix("www.").split("/")[0] for c in prof.get("competitors", [])]
    competitors = list(dict.fromkeys(c for c in competitors if c and c not in url))[:4]
    rules = list(prof.get("writing_rules", []))
    if service_pages:
        rules.append("Where relevant, link to these pages of ours: " + " , ".join(service_pages))
    if form.get("notes"):
        rules.append("Owner's instructions: " + form["notes"].strip())
    cats = sorted(info.get("categories", []), key=lambda c: -c["posts"])
    blog_cat = next((c["name"] for c in cats if re.search(r"blog|news|article|insight", c["name"], re.I)),
                    cats[0]["name"] if cats and cats[0]["name"].lower() != "uncategorized" else "Blog")

    country = (form.get("country") or prof.get("country") or "US").upper()[:2]
    ymyl = bool(form.get("ymyl")) or bool(prof.get("ymyl"))
    entry = {
        "key": site_key(url, {e["key"] for e in raw["sites"]}),
        "name": prof.get("name") or info.get("title") or urlparse(url).netloc,
        "url": url,
        "country": country,
        "builder": info.get("builder", "auto"),
        "publish_mode": form.get("publish_mode") or "draft",
        "interval_days": int(form.get("interval_days") or 2),
        "ymyl": ymyl,
        "default_category": blog_cat,
        "niche": prof["niche"],
        "audience": prof["audience"],
        "tone": prof["tone"],
        "seed_keywords": prof["seed_keywords"][:6],
        "competitors": competitors,
        "writing_rules": "\n".join(f"- {r}" for r in rules),
        "cta": {"title": prof["cta_title"], "text": prof["cta_text"], "button": prof["cta_button"], "url": contact},
        "gsc_property": f"sc-domain:{Site(key='x', name='x', url=url).root_domain}",
        "issue": issue,
        "enabled": True,
    }
    raw["sites"].append(entry)
    save_raw(raw)
    return entry, "added", info
