"""Command line entry point.

    python -m seo_platform test         # check WordPress, builder, Search Console access
    python -m seo_platform audit        # crawl + on-page audit + link + competitor-gap research
    python -m seo_platform research     # Search Console + Autocomplete keywords
    python -m seo_platform plan         # show the next topic per site (no writing, no posting)
    python -m seo_platform due          # show which sites are due a blog today
    python -m seo_platform daily        # write 1 blog for every site that is due today
    python -m seo_platform write        # write 1 blog now for the selected site(s), ignoring the schedule
    python -m seo_platform indexcheck   # ask Google whether published blogs are indexed (also runs Thursdays)
    python -m seo_platform report       # rebuild dashboard data
    python -m seo_platform auto         # what the daily schedule runs (audit Mon, research Mon+Thu, index check Thu, daily, report)

Add --site <key> to run for one site only.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from . import competitors, crawler, formats, gsc, keywords, links, llm, pipeline, planner, report, safety, scheduler
from .config import CLAUDE_MODEL, DATA_DIR, Site, load_sites, paused
from .wordpress import WordPress

# Rough list prices (USD per million tokens) used only for the dashboard's cost estimate.
PRICE_IN, PRICE_OUT, PRICE_SEARCH = 3.0, 15.0, 0.01
WORKERS = 4   # sites written in parallel — keeps GitHub Actions minutes low


def log(msg: str) -> None:
    print(msg, flush=True)


def annotate(title: str, msg: str, level: str = "error") -> None:
    """Show the message on the GitHub Actions run summary page (not just inside the log)."""
    clean = str(msg).replace("%", "%25").replace("\r", "").replace("\n", " ")
    print(f"::{level} title={title}::{clean}", flush=True)


def hint(e: Exception) -> str:
    """Add a plain-English fix to common errors."""
    m = str(e)
    low = m.lower()
    if "is not set" in low and "secret" in low:
        return m + " → add it in Settings → Secrets and variables → Actions (name must match exactly)"
    if "seo_autopilot" in low or "seo autopilot key" in low:
        return m + " → open Settings → SEO Autopilot in WordPress (create a new key and paste it into the GitHub secret)"
    if " 401" in m or "incorrect_password" in low or "rest_not_logged_in" in low or "invalid_username" in low:
        return m + " → the key in the GitHub secret doesn't match: in WordPress open Settings → SEO Autopilot → Create new key, and paste it into the secret"
    if " 403" in m or "rest_forbidden" in low or "rest_cannot" in low:
        return m + " → a security plugin / host firewall is blocking the REST API, or the user lacks Editor rights"
    if " 404" in m and "wp-json" in m:
        return m + " → check the url in sites.yaml; the WordPress REST API (/wp-json/) must be reachable"
    if "name or service not known" in low or "failed to resolve" in low or "nameresolution" in low:
        return m + " → the site url in sites.yaml can't be found — check spelling / https://"
    if "anthropic_api_key" in low:
        return m + " → add the ANTHROPIC_API_KEY secret"
    if "credit balance" in low or "billing" in low:
        return m + " → add credit at console.anthropic.com → Billing"
    return m


def set_status(site: Site, **kw) -> None:
    p = site.data_dir / "status.json"
    st = json.loads(p.read_text()) if p.exists() else {}
    st.update(kw)
    p.write_text(json.dumps(st, indent=1), encoding="utf-8")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def each(sites: list[Site], label: str, fn) -> dict:
    errors = {}
    for s in sites:
        log(f"\n── {label}: {s.name} ({s.key})")
        try:
            fn(s)
            set_status(s, **{f"{label}_ok": now(), f"{label}_error": None})
        except Exception as e:  # keep going with the other sites
            log(f"   ✗ {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)
            annotate(f"{s.key} ({label})", f"{type(e).__name__}: {hint(e)}")
            errors[s.key] = str(e)
            set_status(s, **{f"{label}_error": str(e)[:300]})
    return errors


# ── commands ────────────────────────────────────────────────
TEST_RESULTS: dict[str, dict] = {}


def cmd_test(sites, args):
    def run(s: Site):
        r = TEST_RESULTS.setdefault(s.key, {"name": s.name})
        if not s.wp_password:
            r["secret"] = f"❌ add secret `{s.secret_name}`"
            raise RuntimeError(f"secret {s.secret_name} is not set")
        r["secret"] = "✅"
        wp = WordPress(s)
        info = wp.test_connection()
        r["wordpress"] = f"✅ {info['via']}"
        log(f"   ✓ WordPress ({info['via']}): {info['user']} {info['roles']} publish={info['can_publish']} "
            f"upload={info['can_upload']} scripts={info['unfiltered_html']}")
        if not info["can_publish"]:
            raise RuntimeError("this WordPress user cannot publish posts — use an Editor or Administrator")
        b = formats.detect(s, wp)
        r["builder"] = b
        log(f"   ✓ Page builder: {b}" + ("  (detected automatically)" if s.builder == "auto" else ""))
        set_status(s, builder=b, can_scripts=info["unfiltered_html"])
        if not info["unfiltered_html"]:
            log("   i FAQ schema is skipped (needs an Administrator user); FAQ text is still added")
        try:
            g = gsc.fetch(s)
            log(f"   ✓ Search Console: {len(g['queries'])} query rows")
            r["search_console"] = "✅"
            set_status(s, gsc_error=None)
        except gsc.GSCUnavailable as e:
            log(f"   ! Search Console: {e}")
            r["search_console"] = "⚠️ " + str(e)[:120]
            annotate(f"{s.key} (Search Console)", str(e), "warning")
            set_status(s, gsc_error=str(e))
    return each(sites, "test", run)


def cmd_audit(sites, args):
    def run(s):
        inv = crawler.crawl(s)
        lk = links.build(s)
        titles = [p.get("wp_title") or p.get("title", "") for p in inv["pages"]]
        gaps = competitors.content_gaps(s, titles) if s.competitors else []
        log(f"   ✓ {len(inv['pages'])} pages, {len(inv['issues'])} issues, "
            f"{len(lk['internal'])} internal-link ideas, {len(gaps)} competitor gaps")
    return each(sites, "audit", run)


def cmd_research(sites, args):
    def run(s):
        try:
            g = gsc.fetch(s)
            log(f"   ✓ Search Console: {len(g['queries'])} rows")
        except gsc.GSCUnavailable as e:
            log(f"   ! Search Console skipped: {e}")
        kws = keywords.discover(s)
        log(f"   ✓ {len(kws)} autocomplete keywords")
    return each(sites, "research", run)


def cmd_plan(sites, args):
    all_sites = load_sites(include_disabled=True)
    claimed: set[str] = set()

    def run(s):
        t = planner.plan(s, n=1, claimed=claimed | safety.recent_group_keywords(all_sites, s))
        for x in t:
            claimed.add(x["focus_keyword"])
            log(f"   • {x['working_title']}  [{x['focus_keyword']}]")
    return each(sites, "plan", run)


def cmd_indexcheck(sites, args):
    """For blogs the platform published 5+ days ago: ask Google whether each one is indexed."""
    def run(s: Site):
        if not s.gsc_property:
            log("   ! no gsc_property — skipped")
            return
        hist = planner.history(s)
        wp = WordPress(s) if s.wp_password else None
        cutoff = datetime.now(timezone.utc).timestamp() - 5 * 86400
        checked = indexed = 0
        for h in hist:
            live_at = h.get("scheduled_for") or h.get("written_at")
            if not h.get("post_id") or not live_at or h.get("index", {}).get("verdict") == "PASS":
                continue
            if datetime.fromisoformat(live_at).timestamp() > cutoff or checked >= 40:
                continue
            if wp:
                try:
                    p = wp.post(h["post_id"])
                except Exception:
                    continue
                if p.get("status") != "publish":
                    continue
                h["link"] = p.get("link") or h.get("link")
            res = gsc.inspect(s, h["link"])
            h["index"] = res | {"checked_at": now()}
            checked += 1
            indexed += res.get("verdict") == "PASS"
        planner.save_history(s, hist)
        live = [h for h in hist if h.get("index")]
        not_idx = [h for h in live if h["index"].get("verdict") != "PASS"]
        log(f"   ✓ checked {checked}: {indexed} indexed now · {len(not_idx)} of {len(live)} checked blogs not indexed yet")
        if len(live) >= 6 and len(not_idx) / len(live) > 0.5:
            annotate(f"{s.key} (indexing)", f"{len(not_idx)} of {len(live)} auto-written blogs are not indexed by Google. "
                     "That usually means Google sees them as low value — slow down (raise interval_days) and improve them.",
                     "warning")
    return each(sites, "indexcheck", run)


def due_sites(sites: list[Site]) -> list[Site]:
    out = []
    for s in sites:
        ok, why = scheduler.is_due(s)
        drop, msg = safety.traffic_drop(s)
        if drop:
            set_status(s, paused_reason=msg)
            log(f"   ⏸ {s.key}: {msg}")
            continue
        set_status(s, paused_reason=None, next_due=why)
        log(f"   {'✓' if ok else '·'} {s.key}: {why}")
        if ok:
            out.append(s)
    return out


def cmd_due(sites, args):
    due_sites(sites)
    return {}


def _notify(site: Site, rec: dict) -> None:
    """Post the new blog to the site's control-panel issue (GitHub e-mails you about it)."""
    from . import github_ops as gh
    if not site.issue or not gh.available():
        return
    from .issues import article_line
    from .planner import history
    h = history(site)
    if h:
        gh.comment(site.issue, article_line(site, h[-1]))


RUN_RESULTS: list[dict] = []   # collected for the Actions run summary


def _write(sites: list[Site], notify: bool = True) -> dict:
    if paused():
        log("⏸ AUTOPILOT_PAUSED is set — no blogs will be written.")
        return {}
    all_sites = load_sites(include_disabled=True)
    claimed: set[str] = set()
    run_fps: list = []
    usage: dict = {}
    written, errors = [], {}

    def job(s: Site):
        if s.publish_mode != "off" and not s.wp_password:
            raise RuntimeError(f"secret {s.secret_name} is not set")
        return pipeline.write_one(s, all_sites, claimed, run_fps, usage, log=log)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(job, s): s for s in sites}
        for f in as_completed(futs):
            s = futs[f]
            try:
                rec = f.result()
                if rec:
                    written.append({"site": s.key, "title": rec["title"], "status": rec.get("status")})
                    RUN_RESULTS.append({"site": s.name, "title": rec["title"], "status": rec.get("status"),
                                        "edit_url": rec.get("edit_url"), "review": rec.get("review_reasons") or []})
                    if notify:
                        try:
                            _notify(s, rec)
                        except Exception as ne:  # never fail a run over a notification
                            log(f"   ! notify failed: {ne}")
                set_status(s, write_ok=now(), write_error=None)
            except Exception as e:
                log(f"   ✗ {s.key}: {type(e).__name__}: {e}")
                traceback.print_exc(limit=3)
                annotate(f"{s.key} (write)", f"{type(e).__name__}: {hint(e)}")
                errors[s.key] = str(e)[:300]
                set_status(s, write_error=str(e)[:300])

    cost = (usage.get("input_tokens", 0) * PRICE_IN + usage.get("output_tokens", 0) * PRICE_OUT) / 1e6 \
        + usage.get("searches", 0) * PRICE_SEARCH
    runs_p = DATA_DIR / "runs.json"
    runs = json.loads(runs_p.read_text()) if runs_p.exists() else []
    runs.append({"at": now(), "model": CLAUDE_MODEL, "articles": len(written), "errors": errors,
                 "usage": usage, "est_cost_usd": round(cost, 2),
                 "drafts": sum(1 for w in written if w["status"] == "draft")})
    runs_p.parent.mkdir(parents=True, exist_ok=True)
    runs_p.write_text(json.dumps(runs[-300:], indent=1), encoding="utf-8")
    log(f"\nWrote {len(written)} blog(s). Est. API cost ≈ ${cost:.2f}")
    return errors


def cmd_daily(sites, args):
    log("── Which sites are due today")
    return _write(due_sites(sites))


def cmd_write(sites, args):
    return _write(sites)


def cmd_report(sites, args):
    report.build(load_sites(include_disabled=True))
    log("✓ dashboard data rebuilt")
    return {}


def cmd_auto(sites, args):
    wd = datetime.now(timezone.utc).weekday()   # 0 = Monday
    errs = {}
    if wd == 0:
        errs.update(cmd_audit(sites, args))
    if wd in (0, 3):
        errs.update(cmd_research(sites, args))
    if wd == 3:
        errs.update(cmd_indexcheck(sites, args))
    errs.update(cmd_daily(sites, args))
    return errs


def cmd_issue(sites, args):
    from . import issues
    issues.main()
    return {}


COMMANDS = {"issue": cmd_issue, "test": cmd_test, "audit": cmd_audit, "research": cmd_research, "plan": cmd_plan,
            "due": cmd_due, "daily": cmd_daily, "indexcheck": cmd_indexcheck, "write": cmd_write, "report": cmd_report, "auto": cmd_auto}


def write_summary(command: str, sites: list[Site], errors: dict) -> None:
    """Readable results on the GitHub Actions run page (no download needed)."""
    import os
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    out = [f"## SEO Autopilot · `{command}`\n"]
    if command == "test":
        glob = [("Claude API key (`ANTHROPIC_API_KEY`)", bool(os.environ.get("ANTHROPIC_API_KEY"))),
                ("Search Console (`GSC_SERVICE_ACCOUNT_JSON`)", bool(os.environ.get("GSC_SERVICE_ACCOUNT_JSON"))),
                ("Free photos (`PEXELS_API_KEY`, optional)", bool(os.environ.get("PEXELS_API_KEY")))]
        out.append("| Repo setting | |\n|---|---|")
        out += [f"| {n} | {'✅' if ok else ('⚠️ optional' if 'optional' in n else '❌ missing')} |" for n, ok in glob]
        out.append("\n| Website | Secret | WordPress | Builder | Search Console |\n|---|---|---|---|---|")
        for k, r in TEST_RESULTS.items():
            err = errors.get(k)
            wpcell = r.get("wordpress") or (f"❌ {hint(RuntimeError(err))[:160]}" if err and r.get("secret") == "✅" else "—")
            out.append(f"| {r['name']} | {r.get('secret', '—')} | {wpcell} | {r.get('builder', '—')} | {r.get('search_console', '—')} |")
    if RUN_RESULTS:
        out.append("\n### Blogs written\n| Website | Title | Status | |\n|---|---|---|---|")
        for r in RUN_RESULTS:
            note = ("⚠️ review: " + "; ".join(r["review"])[:200]) if r["review"] else ""
            link = f"[Edit]({r['edit_url']})" if r.get("edit_url") else ""
            out.append(f"| {r['site']} | {r['title']} | {r['status']} {note} | {link} |")
    elif command in ("daily", "auto", "write"):
        out.append("\nNo blogs were due this run." if not errors else "")
    if errors and command != "test":
        out.append("\n### Problems\n" + "\n".join(f"- **{k}**: {hint(RuntimeError(v))}" for k, v in errors.items()))
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seo_platform", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=list(COMMANDS))
    ap.add_argument("--site", help="run for one site key only")
    args = ap.parse_args(argv)
    if args.command == "issue":
        from . import issues
        return issues.main()
    try:
        sites = load_sites(only=args.site or None)
    except Exception as e:
        annotate("sites.yaml", f"{type(e).__name__}: {e} → fix sites.yaml (check indentation and field names)")
        return 1
    if not sites:
        annotate("sites.yaml", "No enabled sites — set enabled: true on at least one site, or check the site key you typed")
        return 1
    try:
        errors = COMMANDS[args.command](sites, args)
    except llm.LLMUnavailable as e:
        annotate("Claude API", hint(e))
        return 1
    except Exception as e:
        traceback.print_exc()
        annotate("Unexpected error", f"{type(e).__name__}: {hint(e)}")
        return 1
    if args.command != "report":
        report.build(load_sites(include_disabled=True))
    write_summary(args.command, sites, errors or {})
    return 1 if errors and len(errors) == len(sites) else 0


if __name__ == "__main__":
    sys.exit(main())
