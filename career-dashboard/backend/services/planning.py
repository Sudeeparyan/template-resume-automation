"""Candidate-local goals: missed daily work carries forward without double counting."""

from datetime import date, timedelta
from typing import Iterable


def plan(settings: dict, dates: Iterable[str], on: date):
    start = date.fromisoformat(settings["start_date"])
    days = sorted(set(settings["workdays"]))
    weekly = int(settings["weekly_target"])
    base, remainder = divmod(weekly, len(days))

    def due(d):
        return (
            base + (days.index(d.weekday()) < remainder)
            if d.weekday() in days and d >= start
            else 0
        )

    # One date per unique job; multiple messages never count as extra applications.
    done = [
        date.fromisoformat(d)
        for d in dates
        if d and start <= date.fromisoformat(d) <= on
    ]
    week = on - timedelta(days=on.weekday())
    previous = sum(
        due(start + timedelta(days=i)) for i in range(max(0, (on - start).days))
    )
    completed_before = sum(d < on for d in done)
    carry = max(0, previous - completed_before)
    ahead = max(0, completed_before - previous)
    target = max(0, due(on) + carry - ahead)
    today_done = sum(d == on for d in done)
    schedule = []
    for i in range(7):
        d = week + timedelta(days=i)
        schedule.append(
            {
                "date": d.isoformat(),
                "label": d.strftime("%a"),
                "planned": due(d),
                "completed": sum(x == d for x in done),
                "today": d == on,
            }
        )
    return {
        "date": on.isoformat(),
        "weekly_target": weekly,
        "week_start": week.isoformat(),
        "week_completed": sum(d >= week for d in done),
        "daily_base": due(on),
        "carryover": carry,
        "ahead": ahead,
        "today_target": target,
        "today_completed": today_done,
        "remaining_today": max(0, target - today_done),
        "current_week_target": sum(d["planned"] for d in schedule),
        "week_remaining": max(
            0, sum(d["planned"] for d in schedule) - sum(d >= week for d in done)
        ),
        "schedule": schedule,
        "settings": settings,
    }
