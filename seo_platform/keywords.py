"""Free keyword discovery: Google Autocomplete expansion of seed keywords.

Autocomplete shows what people are actually typing right now, so it is a
good free proxy for demand (it has no search-volume numbers). Calls are
throttled and failures are skipped quietly.
"""
from __future__ import annotations

import json
import time

import requests

from .config import Site

ENDPOINT = "https://suggestqueries.google.com/complete/search"
MODIFIERS = ["", "how to", "what is", "why", "best", "vs", "cost", "near me", "for",
             "without", "can i", "is it worth", "alternatives", "checklist", "mistakes"]


def suggest(q: str, country: str = "US", lang: str = "en") -> list[str]:
    try:
        r = requests.get(ENDPOINT, params={"client": "firefox", "q": q, "hl": lang, "gl": country.lower()},
                         timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        return [s for s in r.json()[1] if s.lower() != q.lower()]
    except (requests.RequestException, ValueError, IndexError):
        return []


def discover(site: Site, max_calls: int = 45) -> list[dict]:
    seen: dict[str, dict] = {}
    calls = 0
    for seed in site.seed_keywords:
        for mod in MODIFIERS:
            if calls >= max_calls:
                break
            q = seed if not mod else (f"{seed} {mod}" if mod in ("vs", "cost", "near me", "for",
                                       "alternatives", "checklist", "mistakes") else f"{mod} {seed}")
            for rank, s in enumerate(suggest(q, site.country)):
                k = s.lower().strip()
                if k not in seen:
                    seen[k] = {"keyword": k, "source": "autocomplete", "seed": seed,
                               "question": k.split()[0] in ("how", "what", "why", "can", "is", "does", "should", "when", "which"),
                               "rank": rank}
            calls += 1
            time.sleep(0.4)
    kws = sorted(seen.values(), key=lambda k: k["rank"])
    (site.data_dir / "keywords.json").write_text(json.dumps(kws, indent=1), encoding="utf-8")
    return kws


def load(site: Site) -> list[dict]:
    p = site.data_dir / "keywords.json"
    return json.loads(p.read_text()) if p.exists() else []
