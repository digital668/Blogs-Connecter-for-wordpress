"""Free, commercially-usable images with SEO alt text.

Sources (no Canva, no AI images):
1. Pexels API — free key (PEXELS_API_KEY secret), Pexels licence: free for
   commercial use, no attribution required (credit added anyway).
2. Openverse (WordPress.org's open media search) — no key needed; only
   CC0 / Public Domain results are used, so no attribution is required.

The chosen photo is uploaded to the site's own Media Library (not hot-
linked), gets alt text containing the focus keyword, becomes the featured
image, and one copy is placed in the article after the introduction.
"""
from __future__ import annotations

import os
import re

import requests

from .config import Site
from .textutil import slugify

UA = "SEO-Autopilot/1.0"


def _pexels(query: str) -> list[dict]:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    try:
        r = requests.get("https://api.pexels.com/v1/search", timeout=15,
                         headers={"Authorization": key},
                         params={"query": query, "per_page": 10, "orientation": "landscape"})
        if r.status_code != 200:
            return []
        return [{"url": p["src"]["large"], "credit": f"Photo: {p['photographer']} / Pexels",
                 "source": p["url"]} for p in r.json().get("photos", [])]
    except (requests.RequestException, ValueError, KeyError):
        return []


def _openverse(query: str) -> list[dict]:
    try:
        r = requests.get("https://api.openverse.org/v1/images/", timeout=15, headers={"User-Agent": UA},
                         params={"q": query, "license": "cc0,pdm", "page_size": 10,
                                 "aspect_ratio": "wide", "mature": "false"})
        if r.status_code != 200:
            return []
        return [{"url": x["url"], "credit": f"Image: {x.get('creator') or 'Public domain'} / Openverse ({x['license'].upper()})",
                 "source": x.get("foreign_landing_url") or x["url"]}
                for x in r.json().get("results", []) if x.get("url")]
    except (requests.RequestException, ValueError, KeyError):
        return []


def find(site: Site, keyword: str, used: set[str]) -> dict | None:
    queries = [keyword, " ".join(keyword.split()[:3]), (site.seed_keywords or [site.niche])[0]]
    for q in queries:
        for cand in _pexels(q) + _openverse(q):
            if cand["source"] not in used:
                return cand
    return None


def download(url: str) -> tuple[bytes, str] | None:
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": UA})
        ctype = r.headers.get("content-type", "").split(";")[0]
        if r.status_code == 200 and ctype.startswith("image/") and len(r.content) < 8_000_000:
            return r.content, ctype
    except requests.RequestException:
        pass
    return None


def alt_text(keyword: str, title: str) -> str:
    return f"{keyword[:1].upper() + keyword[1:]} – {title}"[:120]


def insert_after_intro(content_html: str, img_html: str) -> str:
    m = re.search(r"</p>", content_html)
    return content_html[:m.end()] + "\n" + img_html + content_html[m.end():] if m else img_html + content_html


def figure(src: str, alt: str, credit: str, width: int | None = None, height: int | None = None) -> str:
    dims = f' width="{width}" height="{height}"' if width and height else ""
    return (f'<figure class="seo-autopilot-image"><img src="{src}" alt="{alt}" loading="lazy"{dims}>'
            f'<figcaption>{credit}</figcaption></figure>')


def attach(wp, site: Site, article: dict, used_sources: set[str]) -> dict:
    """Upload a photo and put it in the article. `wp` is None when publish_mode is off."""
    if not site.images:
        return article
    cand = find(site, article["focus_keyword"], used_sources)
    if not cand:
        article.setdefault("warnings", []).append("No free image found — add one manually")
        return article
    alt = alt_text(article["focus_keyword"], article["title"])
    if wp is None:
        article["content_html"] = insert_after_intro(article["content_html"], figure(cand["url"], alt, cand["credit"]))
        return article
    got = download(cand["url"])
    if not got:
        return article
    data, ctype = got
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(ctype, "jpg")
    media = wp.upload_media(data, f"{slugify(article['focus_keyword'])}.{ext}", ctype,
                            alt=alt, caption=cand["credit"], title=article["title"])
    details = media.get("media_details", {}) or {}
    article["featured_media"] = media["id"]
    article["image"] = {"id": media["id"], "url": media.get("source_url"), "credit": cand["credit"],
                        "source": cand["source"]}
    article["content_html"] = insert_after_intro(
        article["content_html"], figure(media.get("source_url", cand["url"]), alt, cand["credit"],
                                        details.get("width"), details.get("height")))
    return article
