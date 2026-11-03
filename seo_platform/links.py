"""Link opportunities that need no paid tools.

1. Internal links: posts that get few internal links + which related posts
   should link to them (and with what anchor text).
2. Backlink prospecting: ready-made Google search queries to find guest-post,
   resource-page and directory opportunities in each niche. A person reviews
   and does outreach — automated link building gets sites penalised.
"""
from __future__ import annotations

import json
from collections import Counter
from urllib.parse import quote_plus

from .config import Site
from .textutil import similarity


def internal_suggestions(site: Site, limit: int = 30) -> list[dict]:
    p = site.data_dir / "inventory.json"
    if not p.exists():
        return []
    pages = [x for x in json.loads(p.read_text())["pages"] if x.get("status") == 200]
    inbound = Counter(l.rstrip("/") for x in pages for l in x.get("internal_links", []))
    posts = [x for x in pages if x["type"] == "post"]
    out = []
    for target in sorted(posts, key=lambda x: inbound[x["url"].rstrip("/")]):
        if inbound[target["url"].rstrip("/")] >= 3:
            continue
        t_title = target.get("wp_title") or target.get("title", "")
        sources = []
        for src in pages:
            if src["url"] == target["url"] or target["url"] in src.get("internal_links", []):
                continue
            s = similarity(t_title, src.get("wp_title") or src.get("title", ""))
            if s >= 0.15:
                sources.append((s, src))
        for s, src in sorted(sources, key=lambda x: -x[0])[:3]:
            out.append({"from": src["url"], "from_title": src.get("wp_title") or src.get("title"),
                        "to": target["url"], "anchor": t_title, "relevance": round(s, 2)})
        if len(out) >= limit:
            break
    return out[:limit]


PROSPECT_PATTERNS = [
    ('"{kw}" "write for us"', "Guest post"),
    ('"{kw}" "guest post" OR "guest article"', "Guest post"),
    ('"{kw}" "submit a guest post"', "Guest post"),
    ('"{kw}" intitle:resources OR inurl:resources', "Resource page"),
    ('"{kw}" "useful links" OR "helpful links"', "Resource page"),
    ('"{kw}" directory {country}', "Directory / citation"),
    ('"{kw}" "best blogs" OR "top blogs"', "Roundup to pitch"),
    ('"{kw}" podcast "guest"', "Podcast guest"),
]


def prospect_queries(site: Site) -> list[dict]:
    seeds = site.seed_keywords[:3] or [site.niche.split(",")[0]]
    out = []
    for kw in seeds:
        for pat, kind in PROSPECT_PATTERNS:
            q = pat.format(kw=kw, country=site.country)
            out.append({"type": kind, "query": q,
                        "google_url": f"https://www.google.com/search?q={quote_plus(q)}"})
    for comp in site.competitors:
        q = f'"{comp}" -site:{comp}'
        out.append({"type": "Competitor mentions (pitch the same sites)", "query": q,
                    "google_url": f"https://www.google.com/search?q={quote_plus(q)}"})
    return out


def build(site: Site) -> dict:
    data = {"internal": internal_suggestions(site), "prospecting": prospect_queries(site)}
    (site.data_dir / "links.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data
