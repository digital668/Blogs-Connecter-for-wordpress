"""Chooses what to write next for a site.

Inputs: Search Console queries (real demand you already almost rank for),
Google Autocomplete keywords, existing posts, and the history of what the
platform already wrote. Claude then picks the final topics with fresh angles.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import gsc, keywords, llm
from .config import Site
from .textutil import similarity

USED_THRESHOLD = 0.55   # token overlap above which a topic counts as "already covered"


def history(site: Site) -> list[dict]:
    p = site.data_dir / "history.json"
    return json.loads(p.read_text()) if p.exists() else []


def save_history(site: Site, rows: list[dict]) -> None:
    (site.data_dir / "history.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def existing_titles(site: Site) -> list[dict]:
    p = site.data_dir / "inventory.json"
    if not p.exists():
        return []
    inv = json.loads(p.read_text())
    return [{"title": it.get("wp_title") or it.get("title", ""), "url": it["url"]} for it in inv["pages"]]


def covered(kw: str, titles: list[str]) -> str | None:
    for t in titles:
        if similarity(kw, t) >= USED_THRESHOLD:
            return t
    return None


def candidates(site: Site, claimed: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Return (new_topic_candidates, refresh_opportunities)."""
    claimed = claimed or set()
    pages = existing_titles(site)
    titles = [p["title"] for p in pages] + [h["title"] for h in history(site)] + \
             [h["keyword"] for h in history(site)]
    out: dict[str, dict] = {}
    refresh: list[dict] = []

    summary = gsc.summarise(gsc.load(site))
    for q in summary.get("striking_distance", []) + summary.get("top_queries", []):
        kw = q["query"]
        hit = covered(kw, titles)
        if hit:
            if 8 <= q["position"] <= 20:
                refresh.append({"keyword": kw, "page": q["page"], "position": q["position"],
                                "impressions": q["impressions"],
                                "action": "Refresh/expand this page — it is on page 1–2 for this query"})
            continue
        score = q["impressions"] * (2 if 8 <= q["position"] <= 30 else 1)
        out[kw] = {"keyword": kw, "source": "search_console", "score": score,
                   "impressions": q["impressions"], "position": q["position"]}

    for k in keywords.load(site):
        kw = k["keyword"]
        if kw in out or covered(kw, titles):
            continue
        out[kw] = {"keyword": kw, "source": "autocomplete", "score": 30 - k["rank"] + (5 if k["question"] else 0)}

    gaps_p = site.data_dir / "competitor_gaps.json"
    if gaps_p.exists():
        for g in json.loads(gaps_p.read_text())[:80]:
            kw = g["keyword"]
            if kw not in out and not covered(kw, titles):
                out[kw] = {"keyword": kw, "source": "competitor_gap", "score": 18, "competitor": g["competitor"]}

    cands = [c for c in out.values() if all(similarity(c["keyword"], x) < USED_THRESHOLD for x in claimed)]
    cands.sort(key=lambda c: -c["score"])
    return cands[:60], refresh[:15]


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "focus_keyword": {"type": "string"},
                    "working_title": {"type": "string"},
                    "angle": {"type": "string", "description": "What makes this post different/useful"},
                    "search_intent": {"type": "string", "enum": ["informational", "commercial", "transactional", "local"]},
                    "secondary_keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["focus_keyword", "working_title", "angle", "search_intent", "secondary_keywords"],
            },
        }
    },
    "required": ["topics"],
}


def plan(site: Site, n: int | None = None, claimed: set[str] | None = None) -> list[dict]:
    n = n or 1
    cands, refresh = candidates(site, claimed)
    (site.data_dir / "opportunities.json").write_text(json.dumps(
        {"candidates": cands, "refresh": refresh,
         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=1), encoding="utf-8")
    if not cands and not site.seed_keywords:
        return []

    existing = [p["title"] for p in existing_titles(site)][:150]
    done = [h["title"] for h in history(site)][-60:]
    prompt = f"""You are the SEO strategist for {site.name} ({site.url}).
Niche: {site.niche}
Audience: {site.audience}
Country / language: {site.country} / {site.language}

Pick exactly {n} blog topics to write next.

Rules:
- Prefer keywords from Search Console (real impressions), then competitor gaps (topics competitors rank
  with that we lack), then question-style Autocomplete searches.
- NEVER pick a keyword in the AVOID list (used recently by our sister sites) or a close variant of it.
- Each topic must target a DIFFERENT search intent/keyword than the existing posts below — no cannibalisation.
- Avoid the tired "Benefits of X / Uses of X / Everything about X" formula. Find specific angles people
  actually search: comparisons, costs, mistakes, how-to for a specific situation, local angles, checklists,
  "is it worth it", troubleshooting, myths, timelines.
- Titles must be natural, not clickbait. Focus keyword must be a realistic search phrase.

CANDIDATE KEYWORDS (score = priority):
{json.dumps([{k: c[k] for k in ('keyword', 'source', 'score')} for c in cands], indent=0)}

SEED KEYWORDS: {site.seed_keywords}

AVOID (used by sister sites): {sorted(claimed or [])[:80]}

EXISTING POSTS/PAGES (do not duplicate):
{json.dumps(existing + done, indent=0)}
"""
    try:
        result, usage = llm.structured(system="You are a senior SEO content strategist.",
                                       prompt=prompt, tool_name="submit_plan", schema=PLAN_SCHEMA,
                                       max_tokens=3000)
        topics = result.get("topics", [])[:n]
    except llm.LLMUnavailable:
        topics = [{"focus_keyword": c["keyword"], "working_title": c["keyword"].capitalize(),
                   "angle": "", "search_intent": "informational", "secondary_keywords": []}
                  for c in cands[:n]]
    avoid = list(claimed or []) + [h["keyword"] for h in history(site) if h.get("keyword")]
    safe = [t for t in topics if all(similarity(t["focus_keyword"], x) < USED_THRESHOLD for x in avoid)]
    if not safe and cands:
        c = cands[0]
        safe = [{"focus_keyword": c["keyword"], "working_title": c["keyword"].capitalize(), "angle": "",
                 "search_intent": "informational", "secondary_keywords": []}]
    return safe[:n]
