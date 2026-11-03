"""Decides which sites are due for a new blog today.

Every site gets one blog every `interval_days` (default 2 → 5 blogs per
10-day cycle, repeating). Sites are staggered by a fixed offset so the
network doesn't all post on the same day: with 20 sites and interval 2,
about 10 sites write each day.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta, timezone

from .config import Site
from .planner import history


def offset(site: Site) -> int:
    h = int(hashlib.sha1(site.key.encode()).hexdigest(), 16)
    return h % site.interval_days


def last_written(site: Site) -> date | None:
    dates = [h.get("written_at") for h in history(site) if h.get("written_at")]
    return datetime.fromisoformat(max(dates)).date() if dates else None


def is_due(site: Site, today: date | None = None) -> tuple[bool, str]:
    today = today or datetime.now(timezone.utc).date()
    last = last_written(site)
    if last is not None:
        gap = (today - last).days
        if gap < site.interval_days:
            return False, f"last blog {gap} day(s) ago, next in {site.interval_days - gap}"
        if gap >= site.interval_days * 2:
            return True, f"overdue ({gap} days since last blog)"
    if (today.toordinal() + offset(site)) % site.interval_days == 0:
        return True, "scheduled day"
    return False, "not this site's day in the cycle"


def publish_time(site: Site, written: datetime | None = None) -> datetime:
    """Goes live after the review window, at a normal working-hours time in the site's market.

    US sites: 9 am – 1 pm US-Eastern (13:00–17:00 UTC). India sites: 9 am – 1 pm IST.
    The minute is varied so posts don't land at an identical timestamp every time.
    """
    written = written or datetime.now(timezone.utc)
    day = written.date() + timedelta(days=site.review_days)
    start_utc = {"IN": 3, "GB": 9, "AU": 23, "CA": 13}.get(site.country.upper(), 13)
    rnd = random.Random(f"{site.key}{day.isoformat()}")
    minutes = start_utc * 60 + rnd.randint(30, 240)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(minutes=minutes)
