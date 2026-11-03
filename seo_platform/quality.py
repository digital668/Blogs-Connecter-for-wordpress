"""Content-quality signals: invisible-character cleanup, scaled-content-abuse
scoring (Google Quality Rater Guidelines §4.6), and AI-search (GEO) readiness.

Parts of this module are adapted from AgriciDaniel/claude-seo
(scripts/content_humanize.py, scripts/content_quality.py), MIT License,
Copyright (c) 2026 agricidaniel — see THIRD_PARTY_NOTICES.md. Their phrase
lists in turn align with the Wikipedia "AI Cleanup" catalogue.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from bs4 import BeautifulSoup

from .textutil import html_to_text

# ── 1. invisible characters ────────────────────────────────
_INVISIBLE_DELETE = set("­᠎​‎‏‪‫‬‭‮"
                        "⁠⁡⁢⁣⁤⁦⁧⁨⁩ㅤ﻿ﾠ")
_INVISIBLE_SPACE = {c: " " for c in "         "
                                     "     "}
_EMOJI_GLUE = set("‌‍︎️")


def _emojiish(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return unicodedata.category(ch) == "So" or 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or ch == "⃣"


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove hidden zero-width / direction / tag characters that AI tools and
    copy-paste leave behind (they can fingerprint text and break formatting)."""
    out, removed = [], 0
    for i, ch in enumerate(text):
        if ch in _INVISIBLE_DELETE or 0xE0000 <= ord(ch) <= 0xE007F:   # incl. Unicode tag characters
            removed += 1
            continue
        if ch in _INVISIBLE_SPACE:
            out.append(" ")
            removed += 1
            continue
        if ch in _EMOJI_GLUE:
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if not (_emojiish(prev) or _emojiish(nxt)):
                removed += 1
                continue
        out.append(ch)
    return "".join(out), removed


# ── 2. scaled-content-abuse signals (QRG §4.6) ─────────────
FILLER_PHRASES = (
    "it's important to note that", "in this article, we'll explore", "in this article we will explore",
    "in today's fast-paced world", "in today's digital age", "in today's competitive landscape",
    "needless to say", "at the end of the day", "when it comes to", "when all is said and done",
    "in the realm of", "in the world of", "the bottom line is", "without further ado",
    "first and foremost", "last but not least", "for what it's worth", "it goes without saying",
    "as we all know", "the truth is that", "the fact of the matter is", "more often than not",
    "let's dive in", "let's dive into", "let's take a closer look", "let's take a deeper look",
)
AI_PATTERNS = (
    "delve into", "delve deeper into", "in the ever-evolving", "ever-evolving landscape",
    "ever-changing landscape", "in the dynamic landscape", "navigate the complexities", "tapestry of",
    "rich tapestry", "embark on a journey", "embarking on this", "a testament to", "a beacon of",
    "the cornerstone of", "a cornerstone of", "at the heart of", "at its core", "in essence,",
    "in conclusion,", "ultimately,", "moreover,", "furthermore,", "it's worth noting that",
    "by leveraging", "leverage the power of", "harness the power of", "unlock the potential",
    "unlock the full potential", "open up a world of", "a world of possibilities", "elevate your",
    "transform your", "revolutionize the way", "game-changer", "game-changing", "cutting-edge",
    "state-of-the-art", "in summary,", "to summarize,", "to put it simply,", "in a nutshell,",
)
_TOKEN = re.compile(r"[A-Za-z0-9']+")
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?(?:%|st|nd|rd|th)?\b")
_ENTITY = re.compile(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")


def content_quality(html: str) -> dict:
    text = html_to_text(html)
    low = text.lower().replace("’", "'")
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    n = len(tokens)
    scale = max(1.0, n / 1000.0)
    filler = [p for p in FILLER_PHRASES if p in low]
    ai = [p for p in AI_PATTERNS if p in low]
    filler_score = min(100, round(len(filler) / scale * 25))
    ai_score = min(100, round(len(ai) / scale * 15))
    bigrams = Counter(f"{tokens[i]} {tokens[i + 1]}" for i in range(n - 1))
    rep = sum(1 for v in bigrams.values() if v > 1) / max(1, len(bigrams))
    rep_score = round(rep * 100)
    density = min(1.0, (len(_ENTITY.findall(text)) + len(_NUMBER.findall(text))) * 100 / max(1, n) / 10)
    overall = round((100 - filler_score) * 0.25 + (100 - ai_score) * 0.25 + density * 100 * 0.25
                    + (100 - rep_score) * 0.15 + min(100, n / 10) * 0.10)
    flags = []
    if filler_score >= 50:
        flags.append("filler-heavy")
    if ai_score >= 40:
        flags.append("ai-patterns")
    if density < 0.2:
        flags.append("low information density (few specifics, names or numbers)")
    if rep_score >= 30:
        flags.append("repetitive")
    if n < 300:
        flags.append("thin")
    return {"overall": overall, "filler_score": filler_score, "ai_pattern_score": ai_score,
            "repetition_score": rep_score, "information_density": round(density, 2),
            "flags": flags, "filler_hits": filler[:8], "ai_hits": ai[:8]}


# ── 3. AI-search (GEO) readiness ───────────────────────────
QUESTION_START = re.compile(r"^(how|what|why|when|where|which|who|is|are|can|does|do|should|will)\b", re.I)


def geo_readiness(html: str, keyword: str) -> dict:
    """Checks that make sections easy for Google AI Overviews / ChatGPT to quote.

    Grounded in Google's guidance that AI-search optimisation is still SEO:
    clear answers, good structure, specifics and sources.
    """
    soup = BeautifulSoup(html, "lxml")
    h2s = [h for h in soup.find_all("h2") if "frequently asked" not in h.get_text().lower()]
    answer_first, total = 0, 0
    for h in h2s:
        nxt = h.find_next_sibling()
        while nxt is not None and nxt.name not in ("p", "ul", "ol", "table", "h2"):
            nxt = nxt.find_next_sibling()
        if nxt is None or nxt.name == "h2":
            continue
        total += 1
        if nxt.name in ("ul", "ol", "table") or 8 <= len(nxt.get_text().split()) <= 70:
            answer_first += 1
    question_heads = sum(1 for h in soup.find_all(["h2", "h3"]) if QUESTION_START.match(h.get_text(strip=True))
                         or h.get_text(strip=True).endswith("?"))
    text = html_to_text(html)
    first = " ".join(text.split()[:80]).lower()
    checks = {
        "answer_first_sections": (total == 0 or answer_first / total >= 0.7,
                                  f"{answer_first}/{total} sections open with a direct answer (≤70 words)"),
        "question_headings": (question_heads >= 2, f"{question_heads} question-style headings (aim ≥2)"),
        "early_answer": (keyword.lower().split()[0] in first, "Main question answered in the first 80 words"),
        "structured_data": (bool(soup.find(["table", "ol", "ul"])), "Has a list or table"),
        "specifics": (len(_NUMBER.findall(text)) >= 5, "Uses concrete numbers/specifics (≥5)"),
        "sources": (bool([a for a in soup.find_all("a", href=True) if a["href"].startswith("http")]),
                    "Cites at least one source"),
    }
    passed = sum(1 for ok, _ in checks.values() if ok)
    return {"score": round(100 * passed / len(checks)),
            "failed": {k: v for k, (ok, v) in checks.items() if not ok}}
