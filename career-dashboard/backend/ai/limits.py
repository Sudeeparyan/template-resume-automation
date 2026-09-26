"""Plan limits: recognising "usage limit reached", reading when it resets, and remembering it.

Kimi Code, Codex and Claude Code run on paid-for plans with a rolling usage
window (about five hours, plus weekly caps). When one runs out the router
(``backend/ai/router.py``) rests that plan until it resets and sends the work
to the next one, the way OpenRouter steps past a provider that is rate limited.

The health book is one small JSON file next to the shared ``.env``: the plans
belong to this PC, not to a profile, so every profile sees the same state. It
also counts failures in a row, so a plan that keeps failing for other reasons
(a broken sign-in, an outage) is skipped for a while instead of slowing every
call down.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Nothing readable about when the limit resets: try the plan again after this long.
# A retry that is still limited fails in seconds, so guessing short wastes little.
DEFAULT_REST_MINUTES = 60
# A paid API's 429 is a per-minute token cap, not a five-hour window.
PAID_REST_MINUTES = 2
# Failures in a row (not limits) before a plan is skipped for a while.
FAILURE_STREAK = 3
FAILURE_REST_MINUTES = 15
# Weekly caps exist; nothing is rested for longer than this.
MAX_REST = timedelta(days=7)

# ----- how much of each plan's usage window is used ---------------------------------
# The plans do not publish their limits in tokens, so the app meters every call
# (the real count when the app reports one, else an estimate from the text) and
# compares it with each plan's limit per window. That limit starts as a guess,
# can be set in Settings, and is learned the moment a plan reaches its limit:
# whatever was metered in that window is what the plan allowed.
WINDOW = timedelta(hours=5)
STARTING_CAPACITY = {"kimi_cli": 1_000_000, "codex": 1_000_000, "claude_code": 500_000}
# A window with less than this metered before the limit tells little (other apps
# may have used the plan too), so it is not learned from.
MIN_LEARNED = 20_000
# What an agent app spends beyond the prompt and the answer: its own instructions,
# tool definitions and, with web search, the pages it reads.
CALL_OVERHEAD = {"web": 8_000, "text": 2_000}


def estimate_tokens(chars: int, web: bool = False) -> int:
    """Tokens one call uses, estimated from its text (about 4 characters a token)."""
    return max(0, int(chars)) // 4 + CALL_OVERHEAD["web" if web else "text"]

FILE_NAME = ".ai-plan-health.json"

LIMIT = re.compile(
    r"usage[ _]limit|session limit|weekly limit|\d+-hour limit|rate[ _]limit|quota|limit reached"
    r"|out of (?:extra )?usage|out of credits|\b429\b|hit your (?:\w+ ){0,2}limit"
    r"|resource.?exhausted|too many requests|insufficient_quota",
    re.IGNORECASE,
)

_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_RELATIVE = re.compile(
    r"(?:try again|resets?|available again)\s+in\s+"
    r"((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,]*(?:and\s+)?)+)",
    re.IGNORECASE,
)
_PART = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.IGNORECASE)
_CLOCK = re.compile(
    r"(?:try again|resets?|available again)\s+(?:at\s+|on\s+)?"
    r"(?:(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?:(?P<year>\d{4}),?\s+)?(?:at\s+)?)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]\.?m\.?)"
    r"(?:\s*\((?P<zone>[A-Za-z_]+(?:/[A-Za-z_+-]+){1,2})\))?",
    re.IGNORECASE,
)
_EPOCH = re.compile(r"limit reached\|(\d{10})\b", re.IGNORECASE)
# The phrase to keep: from "resets"/"try again" to the end of that sentence (so a
# time zone or "2 hours 13 minutes" stays whole), or Claude's "limit reached|<epoch>".
_HINT = re.compile(r"(?:try again|resets?|available again)\s[^.;\n]{1,80}|limit reached\|\d{10}", re.IGNORECASE)


def is_limit(text: str) -> bool:
    """True when an error says a plan or API ran out of its usage allowance."""
    return bool(LIMIT.search(text or ""))


def reset_hint(text: str) -> str:
    """The provider's own words about when the limit resets ("resets 5:40pm (America/Chicago)"), or ""."""
    match = _HINT.search(text or "")
    return " ".join(match.group(0).split()) if match else ""


def with_hint(message: str, raw: str) -> str:
    """A limit message that keeps the reset time the CLI printed, so the router can read it."""
    hint = reset_hint(raw)
    return f"{message} It says: {hint}." if hint else message


def _local_zone():
    return datetime.now().astimezone().tzinfo


def parse_reset(text: str, now: datetime | None = None) -> datetime | None:
    """When the limit in this message resets, as an aware datetime, or None when it does not say."""
    text = text or ""
    now = now or datetime.now(timezone.utc)
    epoch = _EPOCH.search(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), timezone.utc)
    relative = _RELATIVE.search(text)
    if relative:
        seconds = sum(int(amount) * _UNIT_SECONDS[unit[0].casefold()]
                      for amount, unit in _PART.findall(relative.group(1)))
        if seconds:
            return now + timedelta(seconds=seconds)
    clock = _CLOCK.search(text)
    if clock:
        zone = _local_zone()
        if clock.group("zone"):
            try:
                zone = ZoneInfo(clock.group("zone"))
            except (ZoneInfoNotFoundError, ValueError):
                pass
        hour = int(clock.group("hour")) % 12 + (12 if clock.group("ampm").casefold().startswith("p") else 0)
        minute = int(clock.group("minute") or 0)
        here = now.astimezone(zone)
        month = _MONTHS.get((clock.group("month") or "")[:3].casefold())
        if month:
            year = int(clock.group("year") or here.year)
            try:
                when = datetime(year, month, int(clock.group("day")), hour, minute, tzinfo=zone)
            except ValueError:
                return None
            if when < here and not clock.group("year"):
                when = when.replace(year=year + 1)
            return when
        when = here.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= here:
            when += timedelta(days=1)
        return when
    return None


def rest_until(message: str, *, paid: bool = False, now: datetime | None = None) -> datetime:
    """How long to rest a plan after this limit message: its own reset time, else a default."""
    now = now or datetime.now(timezone.utc)
    when = parse_reset(message, now)
    if when is None or when <= now:
        when = now + timedelta(minutes=PAID_REST_MINUTES if paid else DEFAULT_REST_MINUTES)
    return min(when, now + MAX_REST)


def _iso(when: datetime | None) -> str | None:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds") if when else None


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


def _path_for(root) -> Path:
    """Next to the shared .env: one file for the whole PC, whichever profile asks."""
    from backend.paths import secrets_root_for

    return secrets_root_for(root) / FILE_NAME


class HealthBook:
    """Which plans are resting, until when and why, how much of each usage window is
    used, failure streaks and today's calls.

    ``clock`` returns the current aware datetime; tests pass a fake one.
    """

    def __init__(self, root, clock=None):
        self.path = _path_for(root)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = _lock_for(self.path)

    # ----- storage -------------------------------------------------------------

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"providers": {}}
        return data if isinstance(data.get("providers"), dict) else {"providers": {}}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".saving")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def _update(self, provider: str, change) -> dict:
        with self._lock:
            data = self._read()
            entry = data["providers"].setdefault(provider, {})
            change(entry)
            try:
                self._write(data)
            except OSError:
                pass  # health is advice; a read-only disk must never fail the call itself
            return dict(entry)

    # ----- what the router asks --------------------------------------------------

    def resting(self, provider: str) -> dict | None:
        """{"until", "reason", "kind"} while the plan rests, else None."""
        entry = self._read()["providers"].get(provider) or {}
        until = _parse(entry.get("resting_until"))
        if until and until > self.clock():
            return {"until": entry["resting_until"], "reason": entry.get("rest_reason") or "",
                    "kind": entry.get("rest_kind") or "limit"}
        return None

    def rest(self, provider: str, until: datetime, reason: str, kind: str = "limit") -> dict:
        def change(entry):
            entry.update({"resting_until": _iso(until), "rest_reason": " ".join((reason or "").split())[:300],
                          "rest_kind": kind, "rested_at": _iso(self.clock()), "streak": 0})
        return self._update(provider, change)

    def rest_for_limit(self, provider: str, message: str, *, paid: bool = False) -> dict:
        """Rest a plan that reached its limit, and learn that limit from this window's usage."""
        now = self.clock()
        until = rest_until(message, paid=paid, now=now)

        def change(entry):
            used = self._window_tokens(entry, now)
            if not paid and used >= MIN_LEARNED:
                entry.update({"capacity_learned": used, "capacity_learned_at": _iso(now)})
            # The plan's next window starts with its first call after the reset.
            entry.update({"window_start": None, "window_tokens": 0, "window_calls": 0,
                          "resting_until": _iso(until), "rest_reason": " ".join((message or "").split())[:300],
                          "rest_kind": "limit", "rested_at": _iso(now), "streak": 0})
        return self._update(provider, change)

    # ----- the usage window ------------------------------------------------------------

    def _window_tokens(self, entry: dict, now: datetime) -> int:
        start = _parse(entry.get("window_start"))
        return int(entry.get("window_tokens") or 0) if start and now < start + WINDOW else 0

    def meter(self, provider: str, tokens: int) -> dict:
        """Add one call's tokens to the plan's current window (a new one starts after 5 hours)."""
        now = self.clock()

        def change(entry):
            start = _parse(entry.get("window_start"))
            if not start or now >= start + WINDOW:
                entry.update({"window_start": _iso(now), "window_tokens": 0, "window_calls": 0})
            entry["window_tokens"] = int(entry.get("window_tokens") or 0) + max(0, int(tokens or 0))
            entry["window_calls"] = int(entry.get("window_calls") or 0) + 1
        return self._update(provider, change)

    def usage(self, provider: str, capacity_set: int | None = None) -> dict:
        """How much of the plan's window is used: tokens, the limit and where it came from, a percent."""
        now = self.clock()
        entry = self._read()["providers"].get(provider) or {}
        used = self._window_tokens(entry, now)
        start = _parse(entry.get("window_start"))
        if capacity_set:
            capacity, source = int(capacity_set), "you"
        elif entry.get("capacity_learned"):
            capacity, source = int(entry["capacity_learned"]), "learned"
        else:
            capacity, source = STARTING_CAPACITY.get(provider), "guess"
        return {
            "tokens": used, "calls": int(entry.get("window_calls") or 0) if used else 0,
            "capacity": capacity, "capacity_source": source,
            "learned_at": entry.get("capacity_learned_at"),
            "percent": round(100 * used / capacity) if capacity else None,
            "window_resets": _iso(start + WINDOW) if used and start else None,
        }

    def wake(self, provider: str) -> dict:
        """Clear a rest by hand ("it reset early, try it now")."""
        def change(entry):
            entry.update({"resting_until": None, "rest_reason": "", "streak": 0})
        return self._update(provider, change)

    def record(self, provider: str, ok: bool, error: str = "") -> dict:
        """One finished attempt. A streak of failures rests the plan for a while."""
        now = self.clock()
        today = now.astimezone(_local_zone()).date().isoformat()

        def change(entry):
            if ok:
                entry["streak"] = 0
                entry["last_ok"] = _iso(now)
                served = entry.get("served") or {}
                entry["served"] = {"day": today, "count": (served.get("count", 0) if served.get("day") == today else 0) + 1}
                return
            entry["streak"] = int(entry.get("streak") or 0) + 1
            entry["last_error"] = " ".join((error or "").split())[:300]
            entry["last_error_at"] = _iso(now)
            if entry["streak"] >= FAILURE_STREAK:
                entry.update({"resting_until": _iso(now + timedelta(minutes=FAILURE_REST_MINUTES)),
                              "rest_reason": f"Failed {entry['streak']} times in a row: {entry['last_error']}"[:300],
                              "rest_kind": "failing", "rested_at": _iso(now), "streak": 0})
        return self._update(provider, change)

    def status(self) -> dict:
        """Every plan's entry, with ``resting`` filled in only while the rest is still running."""
        now = self.clock()
        today = now.astimezone(_local_zone()).date().isoformat()
        out = {}
        for provider, entry in self._read()["providers"].items():
            until = _parse(entry.get("resting_until"))
            served = entry.get("served") or {}
            out[provider] = {
                "resting": ({"until": entry["resting_until"], "reason": entry.get("rest_reason") or "",
                             "kind": entry.get("rest_kind") or "limit"} if until and until > now else None),
                "last_ok": entry.get("last_ok"),
                "last_error": entry.get("last_error"),
                "last_error_at": entry.get("last_error_at"),
                "served_today": served.get("count", 0) if served.get("day") == today else 0,
            }
        return out
