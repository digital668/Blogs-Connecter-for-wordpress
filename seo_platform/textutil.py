"""Small text helpers shared by the crawler, planner and originality check."""
from __future__ import annotations

import hashlib
import re
from html import unescape

from bs4 import BeautifulSoup

WORD = re.compile(r"[a-z0-9']+")
STOP = set("""a an the and or but if of to in on for with at by from as is are was were be been
being it its this that these those you your we our they their he she his her them i me my
how what why when where which who whom can will would should could do does did not no so
than then too very just about into over under more most best top vs guide""".split())


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return re.sub(r"\s+", " ", unescape(soup.get_text(" "))).strip()


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def keyword_tokens(text: str) -> set[str]:
    return {w for w in words(text) if w not in STOP and len(w) > 2}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of meaningful tokens — used for topic overlap."""
    ta, tb = keyword_tokens(a), keyword_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def shingles(text: str, n: int = 8) -> set[int]:
    w = words(text)
    out = set()
    for i in range(max(0, len(w) - n + 1)):
        h = hashlib.blake2b(" ".join(w[i:i + n]).encode(), digest_size=4).digest()
        out.add(int.from_bytes(h, "big"))
    return out


def fingerprint(text: str) -> list[int]:
    """Sampled shingle hashes (1 in 4) — compact enough to store for every page."""
    return sorted(h for h in shingles(text) if h % 4 == 0)


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:70].rstrip("-")
