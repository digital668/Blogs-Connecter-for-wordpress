"""Re-creates Rank Math's content-analysis tests and fixes what it can.

The tests mirror Rank Math's four groups: Basic SEO, Additional, Title
Readability and Content Readability. Rank Math computes its own score in
the WordPress editor; this checker makes sure every test is already green
before the post is sent, so the score you see there should be 80–100.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .config import Site
from .textutil import html_to_text, slugify, words

POWER_WORDS = set("""amazing best biggest boost breakthrough brilliant cheap complete critical definitive
easy effective essential exclusive expert fast free guaranteed hidden honest instant
latest little-known mistakes powerful proven quick ready revealed secret simple smart
step-by-step surprising top truth ultimate unbelievable urgent vital warning effortless
affordable avoid insider practical real simple-to-follow""".split())
POSITIVE = set("""best easy free proven simple smart top ultimate win wins winning effortless great
amazing perfect affordable fast quick save saving love happy better reliable worth""".split())
NEGATIVE = set("""avoid mistake mistakes never stop warning worst fail fails failing problem problems
risk risks costly hidden wrong bad trap traps don't""".split())


def _kw_rx(kw: str) -> re.Pattern:
    return re.compile(r"\b" + r"\s+".join(map(re.escape, kw.lower().split())) + r"\b", re.I)


def _count(kw: str, text: str) -> int:
    return len(_kw_rx(kw).findall(text))


def analyse(site: Site, a: dict, used_keywords: set[str] | None = None) -> dict:
    kw = a["focus_keyword"].strip()
    html = a["content_html"]
    soup = BeautifulSoup(html, "lxml")
    text = html_to_text(html)
    wl = words(text)
    seo_title = a.get("meta_title") or a["title"]
    first10 = " ".join(wl[: max(30, len(wl) // 10)])
    links = [x.get("href", "") for x in soup.find_all("a", href=True)]
    internal = [l for l in links if site.owns(l) or l.startswith("/")]
    external = [x for x in soup.find_all("a", href=True)
                if x["href"].startswith("http") and not site.owns(x["href"])]
    dofollow = [x for x in external if "nofollow" not in (x.get("rel") or [])]
    subheads = " ".join(h.get_text(" ") for h in soup.find_all(["h2", "h3", "h4"]))
    alts = " ".join((i.get("alt") or "") for i in soup.find_all("img"))
    density = 100 * _count(kw, text) / max(len(wl), 1)   # Rank Math: occurrences ÷ words
    title_words = set(words(seo_title))
    paras = [len(p.get_text().split()) for p in soup.find_all("p")]
    has_toc = site.toc_plugin or bool(soup.find(attrs={"class": re.compile("toc|table-of-contents")}))
    url = f"{site.base}/{a['slug']}/"
    kw_pos = seo_title.lower().find(kw.lower())

    tests = {
        # Basic SEO
        "kw_in_seo_title": (kw_pos >= 0, "Focus keyword in SEO title"),
        "kw_in_meta": (kw.lower() in a.get("meta_description", "").lower(), "Focus keyword in meta description"),
        "kw_in_url": (slugify(kw) in a["slug"] or all(w in a["slug"] for w in slugify(kw).split("-")),
                      "Focus keyword in URL"),
        "kw_at_start": (bool(_kw_rx(kw).search(first10)), "Focus keyword at the beginning of content"),
        "kw_in_content": (_count(kw, text) > 0, "Focus keyword in content"),
        "content_length": (len(wl) >= max(600, int(site.min_words * 0.9)), f"Content length ({len(wl)} words)"),
        # Additional
        "kw_in_subheadings": (bool(_kw_rx(kw).search(subheads)), "Focus keyword in subheading(s)"),
        "kw_in_image_alt": (bool(_kw_rx(kw).search(alts)), "Focus keyword in image alt"),
        "kw_density": (0.75 <= density <= 2.0, f"Keyword density {density:.2f}% (aim ~1–1.5%)"),
        "url_length": (len(url) <= 75, f"URL length {len(url)} chars (≤ 75)"),
        "external_links": (bool(external), "Links to external resources"),
        "dofollow_external": (bool(dofollow), "At least one DoFollow external link"),
        "internal_links": (bool(internal), "Internal links"),
        "kw_unique": (kw.lower() not in (used_keywords or set()), "Focus keyword not used before on this site"),
        # Title readability
        "kw_title_start": (0 <= kw_pos <= len(seo_title) * 0.5, "Focus keyword near the start of the SEO title"),
        "title_sentiment": (bool(title_words & (POSITIVE | NEGATIVE)), "Title has a positive/negative sentiment word"),
        "title_power_word": (bool(title_words & POWER_WORDS), "Title has a power word"),
        "title_number": (bool(re.search(r"\d", seo_title)), "Title contains a number"),
        # Content readability
        "toc": (has_toc, "Table of contents"),
        "short_paragraphs": (all(n <= 120 for n in paras), "Short paragraphs (≤ 120 words)"),
        "media": (bool(soup.find_all(["img", "iframe", "video"])), "Images or videos in content"),
    }
    passed = sum(1 for ok, _ in tests.values() if ok)
    return {
        "score": round(100 * passed / len(tests)),
        "density": round(density, 2),
        "failed": {k: label for k, (ok, label) in tests.items() if not ok},
        "passed": [k for k, (ok, _) in tests.items() if ok],
    }


# ── deterministic fixes ────────────────────────────────────
def _split_long_paragraphs(soup: BeautifulSoup) -> None:
    for p in soup.find_all("p"):
        if len(p.get_text().split()) <= 120:
            continue
        sents = re.split(r"(?<=[.!?])\s+", p.decode_contents())
        chunks, cur = [], []
        for s in sents:
            cur.append(s)
            if len(" ".join(cur).split()) >= 60:
                chunks.append(" ".join(cur))
                cur = []
        if cur:
            chunks.append(" ".join(cur))
        if len(chunks) == 1 and not p.find(True):   # no sentence breaks and no inner tags: split by words
            w = p.get_text().split()
            chunks = [" ".join(w[i:i + 80]) for i in range(0, len(w), 80)]
        if len(chunks) > 1:
            new = [BeautifulSoup(f"<p>{c}</p>", "lxml").p for c in chunks]
            p.replace_with(*new)


def _add_toc(soup: BeautifulSoup) -> None:
    heads = soup.find_all("h2")
    if len(heads) < 3:
        return
    items = []
    for h in heads:
        if h.get_text(strip=True).lower().startswith("frequently asked"):
            continue
        hid = h.get("id") or slugify(h.get_text())[:50]
        h["id"] = hid
        items.append(f'<li><a href="#{hid}">{h.get_text(" ", strip=True)}</a></li>')
    toc = BeautifulSoup(f'<nav class="seo-toc table-of-contents"><p><strong>In this article</strong></p>'
                        f'<ul>{"".join(items)}</ul></nav>', "lxml").nav
    first_h2 = heads[0]
    first_h2.insert_before(toc)


def _body(soup: BeautifulSoup) -> str:
    return "".join(str(c) for c in soup.body.contents) if soup.body else str(soup)


def autofix(site: Site, a: dict, sources: list[str], used_keywords: set[str]) -> dict:
    kw = a["focus_keyword"].strip()
    r = analyse(site, a, used_keywords)
    f = r["failed"]
    if "kw_in_url" in f or "url_length" in f:
        a["slug"] = slugify(kw)
        while len(f"{site.base}/{a['slug']}/") > 75 and "-" in a["slug"]:
            a["slug"] = a["slug"].rsplit("-", 1)[0]
    if "kw_in_seo_title" in f or "kw_title_start" in f:
        rest = re.sub(_kw_rx(kw), "", a["meta_title"]).strip(" :-–|")
        a["meta_title"] = f"{kw[:1].upper() + kw[1:]}: {rest}" if rest else kw.title()
    if "title_number" in f and not re.search(r"\d", a["meta_title"]):
        from datetime import date
        a["meta_title"] = f"{a['meta_title']} ({date.today().year})"
    soup = BeautifulSoup(a["content_html"], "lxml")
    if "short_paragraphs" in f:
        _split_long_paragraphs(soup)
    if "toc" in f:
        _add_toc(soup)
    if ("external_links" in f or "dofollow_external" in f) and sources:
        def host(u: str) -> str:
            return re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
        lis = "".join(f'<li><a href="{u}" target="_blank" rel="noopener">{host(u)}</a></li>'
                      for u in sources[:3])
        faq = soup.find(lambda t: t.name == "h2" and "frequently asked" in t.get_text().lower())
        block = BeautifulSoup(f"<h3>Sources</h3><ul>{lis}</ul>", "lxml")
        nodes = list(block.body.contents)
        if faq:
            for n in nodes:
                faq.insert_before(n)
        else:
            for n in nodes:
                soup.body.append(n)
    a["content_html"] = _body(soup)
    a["rankmath"] = analyse(site, a, used_keywords)
    return a


# Failed tests that only a content rewrite can fix
REWRITE_TESTS = {"kw_at_start", "kw_in_content", "kw_in_subheadings", "kw_density", "content_length",
                 "title_sentiment", "title_power_word", "internal_links", "kw_in_seo_title"}
