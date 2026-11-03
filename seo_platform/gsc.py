"""Google Search Console data via one service account.

Mixed Google accounts are fine: add the service account's e-mail as a
(Restricted/Full) user in every Search Console property, from whichever
account owns it. See README → "Search Console setup".
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from .config import Site

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
_service = None


class GSCUnavailable(RuntimeError):
    pass


def _svc():
    global _service
    if _service is None:
        raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON")
        if not raw:
            raise GSCUnavailable("GSC_SERVICE_ACCOUNT_JSON secret not set")
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
        _service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    return _service


def service_account_email() -> str | None:
    raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON")
    return json.loads(raw).get("client_email") if raw else None


def _query(prop: str, start: date, end: date, dims: list[str], limit: int = 5000) -> list[dict]:
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": dims, "rowLimit": limit, "dataState": "final"}
    resp = _svc().searchanalytics().query(siteUrl=prop, body=body).execute()
    out = []
    for r in resp.get("rows", []):
        row = dict(zip(dims, r["keys"]))
        row.update(clicks=r["clicks"], impressions=r["impressions"],
                   ctr=round(r["ctr"], 4), position=round(r["position"], 1))
        out.append(row)
    return out


def fetch(site: Site) -> dict:
    if not site.gsc_property:
        raise GSCUnavailable("no gsc_property in sites.yaml")
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=27)
    prev_start = start - timedelta(days=28)
    try:
        data = {
            "period": [start.isoformat(), end.isoformat()],
            "queries": _query(site.gsc_property, start, end, ["query", "page"]),
            "daily": _query(site.gsc_property, prev_start, end, ["date"], 100),
        }
    except Exception as e:  # googleapiclient.errors.HttpError etc.
        msg = str(e)
        if "403" in msg or "permission" in msg.lower():
            raise GSCUnavailable(
                f"no access to {site.gsc_property}. Add {service_account_email()} as a user "
                "in Search Console → Settings → Users and permissions") from e
        raise
    (site.data_dir / "gsc.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def load(site: Site) -> dict | None:
    p = site.data_dir / "gsc.json"
    return json.loads(p.read_text()) if p.exists() else None


def summarise(data: dict | None) -> dict:
    if not data:
        return {}
    days = sorted(data.get("daily", []), key=lambda r: r["date"])
    half = len(days) // 2
    prev, cur = days[:half], days[half:]
    s = lambda rows, k: round(sum(r[k] for r in rows), 1)
    by_query: dict[str, dict] = {}
    for r in data.get("queries", []):
        q = by_query.setdefault(r["query"], {"query": r["query"], "clicks": 0, "impressions": 0,
                                             "pos_x_impr": 0.0, "page": r["page"]})
        q["clicks"] += r["clicks"]
        q["impressions"] += r["impressions"]
        q["pos_x_impr"] += r["position"] * r["impressions"]
    queries = []
    for q in by_query.values():
        q["position"] = round(q.pop("pos_x_impr") / max(q["impressions"], 1), 1)
        queries.append(q)
    queries.sort(key=lambda q: -q["impressions"])
    striking = [q for q in queries if 8 <= q["position"] <= 20 and q["impressions"] >= 20]
    return {
        "clicks": s(cur, "clicks"), "clicks_prev": s(prev, "clicks"),
        "impressions": s(cur, "impressions"), "impressions_prev": s(prev, "impressions"),
        "daily": [{"date": r["date"], "clicks": r["clicks"], "impressions": r["impressions"]} for r in days],
        "top_queries": queries[:25],
        "striking_distance": striking[:25],
    }


def inspect(site: Site, url: str) -> dict:
    """Google URL Inspection: is this page actually in Google's index? (2,000 checks/day per site)"""
    body = {"inspectionUrl": url, "siteUrl": site.gsc_property}
    r = _svc().urlInspection().index().inspect(body=body).execute()
    st = (r.get("inspectionResult") or {}).get("indexStatusResult") or {}
    return {"verdict": st.get("verdict"), "coverage": st.get("coverageState"),
            "last_crawl": st.get("lastCrawlTime"), "google_canonical": st.get("googleCanonical")}
