"""Guardrails that keep the network of sites out of trouble with Google.

See SAFETY.md for the reasoning behind each rule.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from . import gsc
from .config import Site, all_domains

# outbound links are only kept to sources that are recognised as trustworthy references
TRUSTED_SUFFIXES = (".gov", ".edu", ".gov.in", ".nic.in", ".ac.in", ".gov.uk", ".int")
TRUSTED_DOMAINS = {
    "fcc.gov", "broadbandmap.fcc.gov", "census.gov", "consumerfinance.gov", "ftc.gov", "irs.gov",
    "who.int", "nih.gov", "cdc.gov", "nhs.uk", "mayoclinic.org", "clevelandclinic.org",
    "wikipedia.org", "britannica.com", "reuters.com", "apnews.com", "bbc.com", "nytimes.com",
    "forbes.com", "investopedia.com", "experian.com", "equifax.com", "transunion.com",
    "myfico.com", "bankrate.com", "nerdwallet.com", "speedtest.net", "ookla.com", "ieee.org",
    "pewresearch.org", "statista.com", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
    "rbi.org.in", "trai.gov.in", "who.int", "acsm.org", "nsca.com", "healthline.com", "webmd.com",
}


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()


def trusted(url: str, site: Site) -> bool:
    h = _host(url)
    allowed = TRUSTED_DOMAINS | set(site.external_links_ok)
    return h.endswith(TRUSTED_SUFFIXES) or any(h == d or h.endswith("." + d) for d in allowed)


def clean_links(site: Site, html: str) -> tuple[str, list[str]]:
    """Remove links to your OTHER sites (network cross-linking looks like a link scheme)
    and to untrusted/unknown domains. Link text is kept, only the <a> is unwrapped."""
    network = {d for d in all_domains() if not site.owns(d)}
    soup = BeautifulSoup(html, "lxml")
    removed = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") or href.startswith("/") or site.owns(href):
            continue
        h = _host(href)
        if any(h == d or h.endswith("." + d) for d in network) or not trusted(href, site):
            removed.append(href)
            a.unwrap()
        else:
            a["target"] = "_blank"
            a["rel"] = "noopener"
    body = "".join(str(c) for c in soup.body.contents) if soup.body else html
    return body, removed


def unsourced_claims(html: str, limit: int = 8) -> list[str]:
    """Sentences with numbers/prices/percentages but no link in the same paragraph — verify these."""
    soup = BeautifulSoup(html, "lxml")
    flags = []
    for p in soup.find_all(["p", "li"]):
        if p.find("a"):
            continue
        for s in re.split(r"(?<=[.!?])\s+", p.get_text(" ", strip=True)):
            if re.search(r"(\d+(\.\d+)?\s?%|\$\s?\d|₹\s?\d|\b\d{2,}(,\d{3})*\s?(mbps|gbps|users|people|percent|million|billion)\b|\b(19|20)\d{2}\b.*\b(study|survey|report)\b)", s, re.I):
                flags.append(s[:220])
    return flags[:limit]


def traffic_drop(site: Site) -> tuple[bool, str]:
    """True if clicks in the last 7 days fell sharply vs the 7 days before."""
    data = gsc.load(site)
    if site.pause_on_traffic_drop <= 0 or not data or not data.get("daily"):
        return False, ""
    days = sorted(data["daily"], key=lambda r: r["date"])
    if len(days) < 14:
        return False, ""
    last, prev = sum(d["clicks"] for d in days[-7:]), sum(d["clicks"] for d in days[-14:-7])
    if prev >= 30 and last < prev * (1 - site.pause_on_traffic_drop):
        return True, (f"Clicks fell {round(100 * (1 - last / prev))}% week-on-week ({prev} → {last}). "
                      "Writing paused for this site until you check Search Console (manual actions, "
                      "core update, technical issue). It resumes automatically once clicks recover; to override, set "
                      "pause_on_traffic_drop: 0 for this site in sites.yaml.")
    return False, ""


def gate(site: Site, a: dict) -> tuple[str, list[str]]:
    """Decide schedule vs draft. Returns (mode, reasons_for_review)."""
    reasons = []
    if site.ymyl:
        reasons.append("YMYL site (money/health) — a person must review every post")
    if not a.get("originality", {}).get("ok", True):
        o = a["originality"]
        reasons.append(f"{round(o['overlap'] * 100)}% overlap with {o['closest']}")
    rm = a.get("rankmath", {})
    if rm and rm.get("score", 100) < 80:
        reasons.append(f"Rank Math checks {rm['score']}% — failing: {', '.join(rm['failed'].values())}")
    hm = a.get("humanize", {})
    if hm and not hm.get("ok", True):
        after = hm.get("after", {})
        why = []
        if after.get("ai_phrases_left"):
            why.append("AI phrases: " + ", ".join(after["ai_phrases_left"][:5]))
        if after.get("flesch", 100) < 45:
            why.append(f"hard to read (Flesch {after['flesch']})")
        if after.get("sentence_variation", 99) < 5:
            why.append("monotonous sentence rhythm")
        if after.get("passive_pct", 0) > 15:
            why.append(f"{after['passive_pct']}% passive")
        reasons.append("Humanize check: " + "; ".join(why or ["below target"]))
    q = a.get("quality")
    if q and q["overall"] < 60:
        reasons.append(f"Content quality {q['overall']}/100 (Google scaled-content signals: {', '.join(q['flags']) or 'low score'})")
    if len(a.get("unsourced_claims", [])) >= 4:
        reasons.append(f"{len(a['unsourced_claims'])} numbers/claims without a source — verify")
    mode = site.publish_mode
    if mode == "schedule" and reasons:
        mode = "draft"
    return mode, reasons


def recent_group_keywords(sites: list[Site], site: Site, days: int = 120) -> set[str]:
    """Keywords used by OTHER sites in the same niche group recently — never reuse them."""
    if not site.group:
        return set()
    from .planner import history
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out = set()
    for s in sites:
        if s.group == site.group and s.key != site.key:
            out |= {h["keyword"] for h in history(s) if (h.get("written_at") or "") >= cutoff and h.get("keyword")}
    return out
