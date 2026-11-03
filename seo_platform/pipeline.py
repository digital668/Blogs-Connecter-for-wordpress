"""Writes ONE blog for one site, end to end.

plan → Google research → competitor analysis → write → humanize → link
safety → free image → Rank Math check & fix → duplicate check → safety
gate → format for Elementor/Divi/Gutenberg → schedule (or draft).
"""
from __future__ import annotations

import threading

from . import competitors, formats, humanize, images, originality, planner, quality, rankmath, safety, writer
from .config import Site
from .textutil import html_to_text, words
from .wordpress import WordPress

_lock = threading.Lock()


def _add(total: dict, u: dict) -> None:
    with _lock:
        for k in ("input_tokens", "output_tokens", "searches"):
            total[k] = total.get(k, 0) + u.get(k, 0)


def write_one(site: Site, all_sites: list[Site], claimed: set[str], run_fps: list, usage: dict,
              log=print) -> dict | None:
    wp = WordPress(site) if site.publish_mode != "off" else None
    caps = wp.test_connection() if wp else {"unfiltered_html": False}
    builder = formats.detect(site, wp) if wp else (site.builder if site.builder != "auto" else "gutenberg")

    with _lock:
        avoid = set(claimed) | safety.recent_group_keywords(all_sites, site)
    topics = planner.plan(site, n=1, claimed=avoid)
    if not topics:
        log(f"   ! {site.key}: no topic candidates — add seed_keywords / competitors")
        return None
    t = topics[0]
    with _lock:
        claimed.add(t["focus_keyword"])
    log(f"   → {site.key}: {t['working_title']}  [{t['focus_keyword']}]  ({builder})")

    brief, u = writer.research(site, t)
    _add(usage, u)
    comp_urls = list(brief.get("top_ranking_urls") or [])
    comp_urls += competitors.related_competitor_posts(site, t["focus_keyword"], {})
    comp = competitors.analyse(comp_urls, t["focus_keyword"], site.domain)

    a, u = writer.write(site, t, brief, comp)
    _add(usage, u)
    a["builder"] = builder
    a["competitors_analysed"] = [p["url"] for p in comp.get("pages", [])]

    a, u = humanize.humanize(site, a)
    _add(usage, u)

    a["content_html"], a["links_removed"] = safety.clean_links(site, a["content_html"])
    sources = [f["source_url"] for f in brief.get("key_facts", [])
               if f.get("source_url", "").startswith("http") and safety.trusted(f["source_url"], site)]

    used_imgs = {h.get("image", {}).get("source") for h in planner.history(site) if h.get("image")}
    a = images.attach(wp, site, a, used_imgs)

    used_kws = {h["keyword"].lower() for h in planner.history(site) if h.get("keyword")}
    if wp:
        used_kws |= wp.used_focus_keywords()
    a = rankmath.autofix(site, a, sources, used_kws)
    geo = quality.geo_readiness(a["content_html"], a["focus_keyword"])
    to_fix = {k: v for k, v in a["rankmath"]["failed"].items() if k in rankmath.REWRITE_TESTS}
    to_fix.update({f"geo_{k}": v for k, v in geo["failed"].items() if k in ("answer_first_sections", "question_headings")})
    if to_fix:
        a, u = writer.revise(site, a, to_fix)
        _add(usage, u)
        a["content_html"], more = safety.clean_links(site, a["content_html"])
        a["links_removed"] += more
        a["content_html"], _ = humanize.scrub(a["content_html"])
        a = rankmath.autofix(site, a, sources, used_kws)
        a["humanize"]["after"] = humanize.metrics(a["content_html"])
        a["humanize"]["ok"] = humanize.passes(a["humanize"]["after"])
    a["geo"] = quality.geo_readiness(a["content_html"], a["focus_keyword"])
    a["quality"] = quality.content_quality(a["content_html"])

    a["word_count"] = len(words(html_to_text(a["content_html"])))
    a["unsourced_claims"] = safety.unsourced_claims(a["content_html"])
    with _lock:
        orig = originality.check(a["content_html"], extra=list(run_fps))
        run_fps.append((a["slug"], set(orig["fingerprint"])))
    a["originality"] = {k: orig[k] for k in ("overlap", "closest", "ok")}
    a["fingerprint"] = orig["fingerprint"]

    mode, reasons = safety.gate(site, a)
    a["review_reasons"] = reasons
    from .publisher import publish
    rec = publish(site, a, wp, mode, builder, allow_scripts=caps.get("unfiltered_html", False))
    rec["needs_review"] = bool(reasons)

    hist = planner.history(site)
    hist.append({k: rec.get(k) for k in ("title", "slug", "focus_keyword", "post_id", "link", "edit_url",
                                         "status", "scheduled_for", "written_at", "word_count", "needs_review",
                                         "review_reasons", "originality", "builder", "image", "unsourced_claims")}
                | {"keyword": t["focus_keyword"], "geo_score": a["geo"]["score"],
                   "geo_failed": list(a["geo"]["failed"].values()), "quality": a["quality"],
                   "rankmath_score": a["rankmath"]["score"], "rankmath_failed": list(a["rankmath"]["failed"].values()),
                   "humanize": a["humanize"]["after"], "humanize_ok": a["humanize"]["ok"]})
    planner.save_history(site, hist)
    flag = f"  ⚠ draft for review: {'; '.join(reasons)}" if reasons else ""
    log(f"     ✓ {site.key}: {rec.get('status')} {rec.get('scheduled_for') or ''} "
        f"RankMath {a['rankmath']['score']}% · AI-search {a['geo']['score']}% · quality {a['quality']['overall']}{flag}")
    return rec
