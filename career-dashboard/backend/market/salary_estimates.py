"""Market pay estimates from advertised Irish salaries, for postings that state no pay.

An estimate needs at least ``MIN_OBSERVATIONS`` advertised annual salaries in euro from at
least ``MIN_EMPLOYERS`` different employers, for the same role family and level, seen in the
last ``WINDOW_DAYS`` days. It is always labelled as a market estimate: it describes what similar
postings advertise, never this vacancy's pay, and it never proves a permit threshold is met.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median, quantiles

MIN_OBSERVATIONS = 5
MIN_EMPLOYERS = 3
WINDOW_DAYS = 180


def estimate(role_family: str, level: str, *, store=None, now: datetime | None = None) -> dict | None:
    """P25, median and P75 of advertised annual base pay for similar postings, or None."""
    from backend.market.store import MarketStore

    if not role_family or not level:
        return None
    store = store or MarketStore()
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
    rows = store.salary_observations(role_family, level, since=since)
    # One figure per posting: the range's midpoint, or its only bound.
    points = [(row["employer_key"], (row["annual_min"] + (row["annual_max"] or row["annual_min"])) / 2)
              for row in rows if row.get("annual_min")]
    employers = {employer for employer, _ in points}
    if len(points) < MIN_OBSERVATIONS or len(employers) < MIN_EMPLOYERS:
        return None
    values = sorted(value for _, value in points)
    p25, _, p75 = quantiles(values, n=4, method="inclusive")
    mid = median(values)
    return {
        "kind": "estimated", "currency": "EUR", "role_family": role_family, "level": level,
        "p25": round(p25), "median": round(mid), "p75": round(p75),
        "observations": len(points), "employers": len(employers), "window_days": WINDOW_DAYS,
        "label": (f"Market estimate from {len(points)} advertised salaries ({len(employers)} employers). "
                  "Not this vacancy's pay."),
    }
