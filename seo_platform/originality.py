"""Duplicate-content check against every page on ALL connected sites.

This matters when several of your sites share a niche (e.g. three US
internet sites): Google treats near-identical posts as duplicates. It does
not replace a web plagiarism checker — see README.
"""
from __future__ import annotations

import json

from .config import DATA_DIR
from .textutil import html_to_text, shingles

MAX_OVERLAP = 0.12   # >12% shared 8-word phrases with any page = flagged


def _index() -> list[tuple[str, set[int]]]:
    idx = []
    for inv_path in DATA_DIR.glob("*/inventory.json"):
        inv = json.loads(inv_path.read_text())
        for p in inv["pages"]:
            if p.get("fingerprint"):
                idx.append((p["url"], set(p["fingerprint"])))
    for art in DATA_DIR.glob("*/articles/*.json"):
        a = json.loads(art.read_text())
        idx.append((a.get("link") or str(art), set(a.get("fingerprint", []))))
    return idx


def check(content_html: str, extra: list[tuple[str, set[int]]] | None = None) -> dict:
    sh = shingles(html_to_text(content_html))
    sample = {h for h in sh if h % 4 == 0}
    if not sample:
        return {"overlap": 0.0, "closest": None, "ok": True, "fingerprint": []}
    worst, closest = 0.0, None
    for url, fp in _index() + (extra or []):
        if not fp:
            continue
        ov = len(sample & fp) / len(sample)
        if ov > worst:
            worst, closest = ov, url
    return {"overlap": round(worst, 3), "closest": closest, "ok": worst <= MAX_OVERLAP,
            "fingerprint": sorted(sample)}
