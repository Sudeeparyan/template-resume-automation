"""The Tracker: Irish postings the app has read, with the evidence a Stamp 1G graduate checks first.

It lists open postings from the shared market store (every source the app reads), one per
cluster: the same role at the same employer and place counts once, shown from its most direct
source (the employer's own feed before an official board, an aggregator or a job board).
Each row shows:

* where it was read, and whether it is new (first seen in the last ``NEW_HOURS``), closing soon
  (within ``CLOSING_DAYS``) or a lead (an aggregator's excerpt, shown with its attribution, for
  the person to open on the aggregator's site);
* the posting's own permit sentence (quoted), DETE's permit counts for the employer (with the
  legal names they were matched to) and advertised pay;
* for the person viewing it, whether they saved, applied to or excluded it. These come from
  their own profile and are never written to the shared store; applied jobs are hidden unless
  asked for.

Nothing here is immigration advice: these are published facts with their sources.
"""

from __future__ import annotations

import csv
import io
import json
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from backend.market import normalize

NEW_HOURS = 72
CLOSING_DAYS = 7
PAGE = 50
MAX_ROWS = 5000
TYPES = ("graduate_programme", "internship", "entry", "experienced", "unspecified")
STATEMENTS = ("supports", "silent", "ambiguous", "refuses")
SOURCE_LABELS = {"tracked": "Tracked company", "directory": "Employer directory", "registry": "Permit employer board",
                 "eures": "EURES / JobsIreland", "gradireland": "gradireland", "jobs_ie": "jobs.ie",
                 "careerjet": "Careerjet", "jooble": "Jooble", "chat": "Pasted", "cli": "Pasted"}
FILTERS = {"type": "", "county": "", "statement": "", "hide_refusing": "1", "dete": "", "min_salary": "",
           "posted_within": "", "closing_within": "", "employer": "", "q": "", "family": "", "level": "",
           "leads": "1", "show_applied": "", "status": ""}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(value: str) -> datetime | None:
    from backend.services.job_sources import _parse_date

    return _parse_date(value)


def refresh_permits(store) -> int:
    """Cache DETE's permit history for employers with open postings not yet checked against the current data."""
    from backend.permits.history import index, window_for

    history = index()
    if history.version() is None:
        return 0  # no DETE data in this copy: the Tracker shows no permit counts
    version = f"{history.version()}|{window_for(date.today())[1].isoformat()}"
    with closing(store.connect()) as db:
        stale = [(row["key"], row["name"]) for row in db.execute(
            "SELECT key,name FROM employers WHERE permits_checked!=? AND key IN "
            "(SELECT DISTINCT employer_key FROM postings WHERE state='open')", (version,))]
    updates = []
    for key, name in stale:
        found = history.lookup(name or key)
        if found.get("match_type") == "unavailable":
            break  # the DETE index cannot be built now; try again on a later view
        updates.append((int(found.get("permits_24_months") or 0) if found.get("found") else 0,
                        json.dumps(found.get("matched_legal_names") or []), json.dumps(found.get("by_year") or {}),
                        version, key))
    if updates:
        from backend.market.store import _LOCK

        with _LOCK, closing(store.connect()) as db, db:
            db.executemany("UPDATE employers SET permits_24m=?,permit_names=?,permit_years=?,permits_checked=? WHERE key=?",
                           updates)
    return len(updates)


def _matches(row: dict, filters: dict, today: date) -> bool:
    text = lambda key: str(filters.get(key) or "").strip()
    if text("type") and row["posting_type"] not in text("type").split(","):
        return False
    if text("county") and text("county").casefold() not in [c.casefold() for c in row["counties"]]:
        return False
    if text("statement") and row["statement"] not in text("statement").split(","):
        return False
    if text("hide_refusing") == "1" and not text("statement") and row["statement"] == "refuses":
        return False
    if text("dete") == "yes" and not (row["permits"]["permits_24m"] or 0) > 0:
        return False
    if text("dete") == "no" and (row["permits"]["permits_24m"] or 0) > 0:
        return False
    if text("min_salary"):
        try:
            floor = float(text("min_salary"))
        except ValueError:
            floor = 0
        top = row["salary"].get("max") or row["salary"].get("min")
        if floor and not (row["salary"].get("kind") == "advertised" and top and top >= floor):
            return False
    if text("posted_within").isdigit():
        posted = _when(row["posted_at"]) or _when(row["first_seen"])
        if not posted or posted < _now() - timedelta(days=int(text("posted_within"))):
            return False
    if text("closing_within").isdigit():
        closing_on = row["closing_date"]
        if not closing_on or not (today.isoformat() <= closing_on <= (today + timedelta(days=int(text("closing_within")))).isoformat()):
            return False
    if text("employer") and text("employer").casefold() not in row["company"].casefold():
        return False
    if text("family") and row["role_family"] != text("family"):
        return False
    if text("level") and row["level"] != text("level"):
        return False
    if text("leads") != "1" and row["lead"]:
        return False
    if text("q"):
        haystack = f"{row['title']} {row['company']} {row['location']} {row.get('_description', '')}".casefold()
        if not all(word in haystack for word in text("q").casefold().split()):
            return False
    mine = row.get("mine") or {}
    if mine.get("status") == "applied" and text("show_applied") != "1":
        return False
    if text("status") == "new" and "new" not in row["tags"]:
        return False
    if text("status") == "closing_soon" and "closing_soon" not in row["tags"]:
        return False
    if text("status") == "saved" and not mine:
        return False
    return True


def _row(record, *, today: date, overlays: dict, excluded: set[str]) -> dict:
    data = dict(record)
    salary = json.loads(data.get("salary") or "{}") if isinstance(data.get("salary"), str) else (data.get("salary") or {})
    first_seen = _when(data["first_seen"])
    tags = []
    if first_seen and first_seen >= _now() - timedelta(hours=NEW_HOURS):
        tags.append("new")
    if data["closing_date"] and today.isoformat() <= data["closing_date"] <= (today + timedelta(days=CLOSING_DAYS)).isoformat():
        tags.append("closing_soon")
    if data["lead"]:
        tags.append("lead")
    mine = overlays.get(identity(data["url"]))
    return {
        "key": data["key"], "title": data["title"], "company": data["company"], "url": data["url"],
        "location": data["location"], "counties": [c for c in str(data["counties"] or "").split(";") if c],
        "region": data["region"], "posting_type": data["posting_type"], "level": data["level"],
        "role_family": data["role_family"], "years_required": data["years_required"],
        "closing_date": data["closing_date"], "posted_at": data["posted_at"], "first_seen": data["first_seen"],
        "last_seen": data["last_seen"],
        "salary": {"kind": salary.get("kind") or data["salary_kind"], "min": data["salary_min"], "max": data["salary_max"],
                   "currency": salary.get("currency") or "", "period": salary.get("period") or "",
                   "quote": salary.get("quote") or ""},
        "statement": data["statement"], "statement_quote": data["statement_quote"], "on_eures": bool(data["on_eures"]),
        "source": data["source"], "source_label": SOURCE_LABELS.get(data["source"], data["source"]),
        "source_kind": data["source_kind"], "lead": bool(data["lead"]), "attribution": data["attribution"],
        "sources": int(data.get("copies") or 1),
        "permits": {"permits_24m": data.get("permits_24m"), "legal_names": json.loads(data.get("permit_names") or "[]"),
                    "by_year": json.loads(data.get("permit_years") or "{}")},
        "tags": tags, "mine": mine, "excluded": identity(data["url"]) in excluded,
        "_description": data.get("description") or "",
    }


def load_rows(store, *, overlays: dict | None = None, excluded: set[str] | None = None) -> tuple[list[dict], str]:
    """Every open cluster as a Tracker row (the person's excluded postings left out), and when the store last changed."""
    today = date.today()
    refresh_permits(store)
    with closing(store.connect()) as db:
        records = db.execute(
            "SELECT p.*, e.permits_24m, e.permit_names, e.permit_years, c.copies FROM ("
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY cluster ORDER BY lead, source_rank, last_seen DESC) AS place"
            "  FROM postings WHERE state='open') p "
            "LEFT JOIN employers e ON e.key=p.employer_key "
            "LEFT JOIN (SELECT cluster, COUNT(*) AS copies FROM postings WHERE state='open' GROUP BY cluster) c "
            "  ON c.cluster=p.cluster "
            "WHERE p.place=1 ORDER BY p.first_seen DESC LIMIT ?", (MAX_ROWS,)).fetchall()
        updated = db.execute("SELECT MAX(last_seen) FROM postings").fetchone()[0]
    rows = [_row(record, today=today, overlays=overlays or {}, excluded=excluded or set()) for record in records]
    return [row for row in rows if not row["excluded"]], updated or ""


def _filters(filters: dict | None) -> dict:
    return {**FILTERS, **{k: str(v) for k, v in (filters or {}).items() if k in FILTERS and v is not None}}


def query(store, filters: dict | None = None, *, overlays: dict | None = None, excluded: set[str] | None = None,
          limit: int = PAGE, offset: int = 0) -> dict:
    """{"rows", "total", "facets", "filters", "updated"}: one row per cluster, newest first."""
    filters = _filters(filters)
    today = date.today()
    rows, updated = load_rows(store, overlays=overlays, excluded=excluded)
    facets = {"type": _count(row["posting_type"] for row in rows),
              "statement": _count(row["statement"] for row in rows),
              "county": _count(county for row in rows for county in row["counties"]),
              "dete": {"yes": sum(1 for row in rows if (row["permits"]["permits_24m"] or 0) > 0)},
              "new": sum("new" in row["tags"] for row in rows),
              "closing_soon": sum("closing_soon" in row["tags"] for row in rows),
              "leads": sum(row["lead"] for row in rows)}
    chosen = [row for row in rows if _matches(row, filters, today)]
    for row in rows:
        row.pop("_description", None)
    return {"rows": chosen[offset:offset + limit], "total": len(chosen), "all": len(rows), "facets": facets,
            "filters": filters, "updated": updated,
            "note": ("Published facts with their sources, not immigration advice. DETE counts are permits issued to the "
                     "named legal employers; a posting's own sentence is quoted as written.")}


def _count(values) -> dict:
    out: dict = {}
    for value in values:
        if value:
            out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


EXPORT_COLUMNS = ["Title", "Employer", "Location", "Counties", "Type", "Level", "Advertised pay (EUR, annual)",
                  "Pay note", "Posting's permit sentence", "Statement", "DETE permits (24 months)",
                  "DETE legal names", "On EURES / JobsIreland", "Closing date", "Posted", "First seen by the app",
                  "Source", "Link"]


def export_rows(rows: list[dict]) -> list[list]:
    out = []
    for row in rows:
        pay = row["salary"]
        amount = "–".join(f"{int(v):,}" for v in (pay.get("min"), pay.get("max")) if v) if pay.get("kind") == "advertised" else ""
        source = row["source_label"] + (f" ({row['attribution']})" if row.get("attribution") else "")
        out.append([row["title"], row["company"], row["location"], "; ".join(row["counties"]),
                    row["posting_type"].replace("_", " "), row["level"], amount, pay.get("quote") or "",
                    row["statement_quote"], row["statement"], row["permits"]["permits_24m"] or 0,
                    "; ".join(row["permits"]["legal_names"]), "yes" if row["on_eures"] else "no",
                    row["closing_date"], (row["posted_at"] or "")[:10], (row["first_seen"] or "")[:10], source, row["url"]])
    return out


def _safe_csv(value):
    # A cell beginning with =, +, - or @ would run as a formula in a spreadsheet; keep it text.
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else value


def export_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for values in export_rows(rows):
        writer.writerow([_safe_csv(v) for v in values])
    return buffer.getvalue()


def export_xlsx(rows: list[dict]) -> bytes:
    from backend.market.xlsx import workbook

    return workbook(EXPORT_COLUMNS, export_rows(rows), sheet="Tracker")


def identity(url: str) -> str:
    """A posting's link as one identity (services/postings.posting_key without a requisition), or ''."""
    from backend.services.postings import posting_key

    try:
        return posting_key(url)
    except (ValueError, AttributeError):
        return ""


def overlays(services) -> tuple[dict, set[str]]:
    """The viewing person's own records, by posting identity: {identity: {"job_id", "status"}}, and excluded identities."""
    mine, excluded = {}, set()
    for job in services.w.jobs():
        key = identity(str(job.get("url") or ""))
        if key:
            mine[key] = {"job_id": job["id"], "status": job.get("status") or "saved"}
    for row in services.excluded():
        key = identity(str(row.get("url") or ""))
        if key:
            excluded.add(key)
    return mine, excluded


def market_posting_for_save(store, key: str) -> dict:
    """The stored posting as add_posting takes it; a lead (an aggregator's excerpt) cannot be saved as a posting."""
    record = store.posting(key)
    if record is None:
        raise KeyError("That posting is not in the Tracker any more.")
    if record.get("lead"):
        raise ValueError(f"This is a lead from {SOURCE_LABELS.get(record['source'], record['source'])}: an excerpt, not "
                         "the posting. Open it, then paste the employer's own link in the Assistant.")
    if record.get("state") != "open":
        raise ValueError("This posting is closed or no longer listed.")
    # A key built from a requisition ("req:<employer>:<id>") gives the save the same identity.
    requisition = key.split(":", 2)[2] if key.startswith("req:") and key.count(":") >= 2 else ""
    return {"company": record["company"], "title": record["title"], "location": record["location"],
            "url": record["url"], "description": record["description"], "requisition_id": requisition,
            "last_seen": record["last_seen"]}


def counties_known() -> list[str]:
    return sorted(normalize.COUNTIES)


# ---- saving a Tracker posting to the person's own job list ---------------------------------------

RECENT_DAYS = 7  # a posting seen this recently may be saved when its source cannot be read again now


def save(services, store, key: str) -> dict:
    """Save one posting through the same gates as any other (sponsorship, never-re-apply), re-read first."""
    from backend.services import job_sources

    try:
        posting = market_posting_for_save(store, key)
    except KeyError as error:
        raise ValueError(str(error.args[0])) from error
    fresh = job_sources.read_posting(posting["url"])
    if fresh and fresh.get("description"):
        posting.update(description=fresh["description"], location=fresh.get("location") or posting["location"])
    else:
        seen = _when(posting["last_seen"])
        if not seen or seen < _now() - timedelta(days=RECENT_DAYS):
            raise ValueError("The posting could not be read again from its source, and the app last saw it more than "
                             f"{RECENT_DAYS} days ago. Open the link to check that it is still open.")
    posting.pop("last_seen", None)
    result = services.add_posting(posting, source="tracker")
    if result.get("excluded"):
        return {"saved": False, "excluded": True, "sentence": result["sentence"], "reason": result["reason_label"],
                "summary": "Not saved: the posting says “" + result["sentence"] + "” (" + result["reason_label"] + ")."}
    if result.get("blocked"):
        return {"saved": False, "blocked": True, "rule": result["rule"], "summary": "Not saved: " + result["note"]}
    job = result["job"]
    if not result.get("duplicate"):
        try:
            services.w.track_search_job(job["id"], services.today())
        except ValueError:
            pass
    return {"saved": True, "duplicate": bool(result.get("duplicate")), "job_id": job["id"],
            "summary": ("Already in your jobs: " if result.get("duplicate") else "Saved to your jobs: ")
                       + f"{job['company']} — {job['title']}"}


# ---- alerts: saved filters, with their new matches ------------------------------------------------

ALERTS = "tracker_alerts"
MAX_ALERTS = 10


def _alerts(services) -> list[dict]:
    value = services.pref(ALERTS, []) or []
    return [a for a in value if isinstance(a, dict) and a.get("id")]


def alerts(services, store) -> list[dict]:
    """Each saved filter with how many of its matches the app first saw since the person last looked."""
    saved = _alerts(services)
    if not saved:
        return []
    mine, excluded = overlays(services)
    rows, _ = load_rows(store, overlays=mine, excluded=excluded)
    today = date.today()
    out = []
    for alert in saved:
        filters = _filters(alert.get("filters"))
        matching = [row for row in rows if _matches(dict(row), filters, today)]
        since = str(alert.get("seen_at") or "")
        fresh = [row for row in matching if not since or row["first_seen"] > since]
        out.append({**alert, "matches": len(matching), "new": len(fresh),
                    "examples": [{"title": r["title"], "company": r["company"], "key": r["key"]} for r in fresh[:3]]})
    return out


def save_alert(services, name: str, filters: dict) -> dict:
    import uuid

    saved = _alerts(services)
    if len(saved) >= MAX_ALERTS:
        raise ValueError(f"Keep at most {MAX_ALERTS} alerts; delete one first.")
    alert = {"id": uuid.uuid4().hex[:12], "name": " ".join(name.split())[:80],
             "filters": {k: str(v) for k, v in (filters or {}).items() if k in FILTERS and str(v)},
             "created_at": _now().isoformat(timespec="seconds"), "seen_at": _now().isoformat(timespec="seconds")}
    services.set_pref(ALERTS, saved + [alert])
    return alert


def delete_alert(services, alert_id: str) -> dict:
    saved = _alerts(services)
    kept = [a for a in saved if a["id"] != alert_id]
    if len(kept) == len(saved):
        raise ValueError("That alert was not found.")
    services.set_pref(ALERTS, kept)
    return {"deleted": alert_id}


def mark_seen(services, alert_id: str) -> dict:
    saved = _alerts(services)
    if not any(a["id"] == alert_id for a in saved):
        raise ValueError("That alert was not found.")
    stamp = _now().isoformat(timespec="seconds")
    services.set_pref(ALERTS, [{**a, "seen_at": stamp} if a["id"] == alert_id else a for a in saved])
    return {"id": alert_id, "seen_at": stamp}
