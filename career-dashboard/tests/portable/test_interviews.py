"""Recorded interviews (services/interviews.py): Dublin times, the upcoming list and .ics files."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app_fixture import add_job, profile_app
from backend.services.interviews import escape, fold, parse_start
from career import Workspace

DUBLIN = ZoneInfo("Europe/Dublin")


def local(days: int, hour: int = 14) -> str:
    moment = (datetime.now(DUBLIN) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return moment.strftime("%Y-%m-%dT%H:%M")


def test_times_are_read_in_dublin_time_across_the_clock_change():
    summer = parse_start("2026-07-01T14:00", "Europe/Dublin")
    winter = parse_start("2026-12-01T14:00", "Europe/Dublin")
    assert summer.utcoffset() == timedelta(hours=1)
    assert winter.utcoffset() == timedelta(0)
    # An explicit offset is kept as the same instant.
    assert parse_start("2026-12-01T09:00-05:00", "Europe/Dublin").astimezone(timezone.utc).hour == 14


def test_add_list_update_and_remove_an_interview(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        created = client.post(base + f"/v2/jobs/{job['id']}/interviews", json={
            "stage": "technical", "starts_at": local(3), "duration_min": 60,
            "where_text": "Video call", "notes": "Bring the project demo", "move_to_interview": True,
        })
        assert created.status_code == 201, created.text
        interview = created.json()
        assert interview["stage_label"] == "Technical interview"
        assert interview["company"] == "Example Company"
        # Recording an interview moved the role to the Interview stage, with history.
        assert Workspace(root).get_job(job["id"])["status"] == "interview"

        summary = client.get(base + "/v2/summary").json()
        assert [i["id"] for i in summary["interviews_upcoming"]] == [interview["id"]]
        listed = client.get(base + "/v2/interviews", params={"job_id": job["id"]}).json()["interviews"]
        assert len(listed) == 1

        moved = client.put(base + f"/v2/interviews/{interview['id']}", json={
            "stage": "final", "starts_at": local(5, 10), "duration_min": 30})
        assert moved.status_code == 200 and moved.json()["stage"] == "final"

        removed = client.delete(base + f"/v2/interviews/{interview['id']}")
        assert removed.status_code == 200
        assert client.get(base + "/v2/summary").json()["interviews_upcoming"] == []
        actions = [a["action"] for a in Workspace(root).activity()]
        assert {"interview_added", "interview_updated", "interview_removed"} <= set(actions)


def test_bad_input_is_refused(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        stage = client.post(base + f"/v2/jobs/{job['id']}/interviews", json={"stage": "party", "starts_at": local(2)})
        assert stage.status_code == 400
        when = client.post(base + f"/v2/jobs/{job['id']}/interviews", json={"starts_at": "next tuesday!"})
        assert when.status_code in (400, 422)
        Workspace(root).remove_job(job["id"], "Not suitable")
        removed = client.post(base + f"/v2/jobs/{job['id']}/interviews", json={"starts_at": local(2)})
        assert removed.status_code == 400


def test_calendar_files_are_valid_and_escaped(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root, "Comma, Semi; Slash\\ Ltd", "Analyst", "escape")
        created = client.post(base + f"/v2/jobs/{job['id']}/interviews", json={
            "stage": "screen", "starts_at": local(1, 9), "where_text": "Room 2, Block B",
            "notes": "Line one\nLine two"}).json()
        one = client.get(base + f"/v2/interviews/{created['id']}/calendar.ics")
        assert one.status_code == 200
        assert one.headers["content-type"].startswith("text/calendar")
        assert "attachment" in one.headers["content-disposition"]
        text = one.text
        assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
        assert "Comma\\, Semi\\; Slash\\\\ Ltd" in text.replace("\r\n ", "")
        assert "Line one\\nLine two" in text.replace("\r\n ", "")
        assert "LOCATION:Room 2\\, Block B" in text
        start = parse_start(local(1, 9), "Europe/Dublin").astimezone(timezone.utc)
        assert f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}" in text
        every = client.get(base + "/v2/interviews/calendar.ics").text
        assert every.count("BEGIN:VEVENT") == 1


def test_long_lines_fold_without_splitting_characters():
    line = "DESCRIPTION:" + escape("Café interview " * 12)
    folded = fold(line)
    for part in folded.split("\r\n"):
        assert len(part.encode("utf-8")) <= 75
    assert folded.replace("\r\n ", "") == line
