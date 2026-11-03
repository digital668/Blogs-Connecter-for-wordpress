"""Builds the JSON files the dashboard (docs/index.html) reads."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import gsc
from .config import DASHBOARD_DATA, DATA_DIR, Site, paused
from .crawler import health_score
from .planner import history


def _load(path):
    return json.loads(path.read_text()) if path.exists() else None


def site_report(site: Site) -> dict:
    inv = _load(site.data_dir / "inventory.json") or {"pages": [], "issues": [], "site_checks": {}}
    opp = _load(site.data_dir / "opportunities.json") or {}
    links = _load(site.data_dir / "links.json") or {}
    status = _load(site.data_dir / "status.json") or {}
    pages = inv["pages"]
    issues = inv["issues"]
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in ("high", "medium", "low")}
    hist = history(site)
    return {
        "key": site.key, "name": site.name, "url": site.url, "enabled": site.enabled,
        "publish_mode": "draft (YMYL)" if site.ymyl else site.publish_mode,
        "interval_days": site.interval_days, "builder": status.get("builder") or site.builder,
        "group": site.group, "ymyl": site.ymyl, "issue": site.issue,
        "crawled_at": inv.get("crawled_at"),
        "pages": len(pages), "posts": sum(1 for p in pages if p["type"] == "post"),
        "health": health_score(issues, len(pages)) if pages else None,
        "issue_counts": counts, "issues": issues[:200],
        "site_checks": inv.get("site_checks", {}),
        "gsc": gsc.summarise(gsc.load(site)),
        "candidates": opp.get("candidates", [])[:30],
        "competitor_gaps": (_load(site.data_dir / "competitor_gaps.json") or [])[:40],
        "refresh": opp.get("refresh", []),
        "internal_links": links.get("internal", []),
        "prospecting": links.get("prospecting", []),
        "articles": list(reversed(hist))[:50],
        "status": status,
    }


def build(sites: list[Site]) -> dict:
    DASHBOARD_DATA.mkdir(parents=True, exist_ok=True)
    summary, details = [], {}
    for s in sites:
        r = site_report(s)
        details[s.key] = r
        g = r["gsc"] or {}
        summary.append({k: r[k] for k in ("key", "name", "url", "enabled", "publish_mode", "health", "interval_days",
                                          "builder", "pages", "posts", "issue_counts", "crawled_at", "status", "issue")}
                       | {"clicks": g.get("clicks"), "clicks_prev": g.get("clicks_prev"),
                          "impressions": g.get("impressions"), "impressions_prev": g.get("impressions_prev"),
                          "articles_total": len(r["articles"]),
                          "indexed": sum(1 for a in r["articles"] if (a.get("index") or {}).get("verdict") == "PASS"),
                          "index_checked": sum(1 for a in r["articles"] if a.get("index")),
                          "needs_review": sum(1 for a in r["articles"] if a.get("needs_review") and a.get("status") == "draft"),
                          "avg_rankmath": (round(sum(a.get("rankmath_score") or 0 for a in r["articles"][:10]) /
                                           max(1, len(r["articles"][:10]))) if r["articles"] else None),
                          "next_scheduled": next((a.get("scheduled_for") for a in sorted(
                              r["articles"], key=lambda a: a.get("scheduled_for") or "")
                              if (a.get("scheduled_for") or "") > datetime.now(timezone.utc).isoformat()), None)})
    runs = _load(DATA_DIR / "runs.json") or []
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "paused": paused(),
           "repo": __import__("seo_platform.github_ops", fromlist=["x"]).repo_url(),
           "sites": summary, "runs": runs[-30:], "details": details}
    payload = json.dumps(out, separators=(",", ":"))
    (DASHBOARD_DATA / "all.json").write_text(payload, encoding="utf-8")
    # Standalone copy with the data baked in — opens by double-click, no server needed.
    template = (DASHBOARD_DATA.parent / "index.html")
    if template.exists():
        html = template.read_text(encoding="utf-8").replace(
            "window.EMBED = null;", "window.EMBED = " + payload.replace("</", "<\\/") + ";", 1)
        (DATA_DIR / "dashboard.html").write_text(html, encoding="utf-8")
    return out
