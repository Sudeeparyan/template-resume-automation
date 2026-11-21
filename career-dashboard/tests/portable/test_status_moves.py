"""Application board moves (POST /v2/jobs/{id}/status) and their Undo.

The board follows the same rules as every other way of changing a status: "applied" needs the
real submission date, a recorded application stays in the history, a stale board never
overwrites a newer change, and an undone move never counts in the funnel.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from app_fixture import add_job, profile_app
from career import Workspace

YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def test_applied_needs_the_real_date_and_future_dates_are_refused(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        missing = client.post(base + f"/v2/jobs/{job['id']}/status", json={"status": "applied", "expected_status": "saved"})
        assert missing.status_code == 400
        assert "actual application date" in missing.json()["detail"]
        future = (date.today() + timedelta(days=2)).isoformat()
        ahead = client.post(base + f"/v2/jobs/{job['id']}/status",
                            json={"status": "applied", "application_date": future, "expected_status": "saved"})
        assert ahead.status_code == 400
        moved = client.post(base + f"/v2/jobs/{job['id']}/status",
                            json={"status": "applied", "application_date": YESTERDAY, "expected_status": "saved"})
        assert moved.status_code == 200, moved.text
        body = moved.json()
        assert body["job"]["status"] == "applied" and body["job"]["application_date"] == YESTERDAY
        assert isinstance(body["event_id"], int)


def test_a_stale_board_gets_a_conflict_and_writes_nothing(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        # The Assistant (or another tab) records the application first.
        Workspace(root).update_job(job["id"], "applied", application_date=YESTERDAY)
        stale = client.post(base + f"/v2/jobs/{job['id']}/status",
                            json={"status": "withdrawn", "expected_status": "saved"})
        assert stale.status_code == 409
        assert Workspace(root).get_job(job["id"])["status"] == "applied"


def test_a_recorded_application_never_moves_back_and_tailored_needs_a_package(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        tailored = client.post(base + f"/v2/jobs/{job['id']}/status", json={"status": "prepared", "expected_status": "saved"})
        assert tailored.status_code == 400 and "Prepare the application first" in tailored.json()["detail"]
        Workspace(root).update_job(job["id"], "applied", application_date=YESTERDAY)
        back = client.post(base + f"/v2/jobs/{job['id']}/status", json={"status": "saved", "expected_status": "applied"})
        assert back.status_code == 400 and "stays in your history" in back.json()["detail"]
        closed = client.post(base + f"/v2/jobs/{job['id']}/status", json={"status": "rejected", "expected_status": "applied"})
        assert closed.status_code == 200
        reopened = client.post(base + f"/v2/jobs/{job['id']}/status", json={"status": "interview", "expected_status": "rejected"})
        assert reopened.status_code == 200 and reopened.json()["job"]["status"] == "interview"


def test_undo_restores_status_and_date_and_the_funnel_forgets_the_move(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        moved = client.post(base + f"/v2/jobs/{job['id']}/status",
                            json={"status": "interview", "application_date": YESTERDAY, "expected_status": "saved"}).json()
        summary = client.get(base + "/v2/summary").json()
        assert summary["funnel"]["periods"]["30"]["interviews"] == 1
        undone = client.post(base + f"/v2/jobs/{job['id']}/status/undo", json={"event_id": moved["event_id"]})
        assert undone.status_code == 200, undone.text
        restored = undone.json()["job"]
        # Exactly what was there before: no status change and no application date left behind.
        assert restored["status"] == "saved" and restored["application_date"] is None
        after = client.get(base + "/v2/summary").json()
        assert after["funnel"]["periods"]["30"]["interviews"] == 0
        assert after["funnel"]["periods"]["30"]["applied"] == 0
        actions = [a["action"] for a in Workspace(root).activity()]
        assert "progress_reverted" in actions
        # The same move cannot be undone twice.
        again = client.post(base + f"/v2/jobs/{job['id']}/status/undo", json={"event_id": moved["event_id"]})
        assert again.status_code == 400


def test_undo_is_refused_after_a_later_change_or_the_window(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        first = client.post(base + f"/v2/jobs/{job['id']}/status",
                            json={"status": "applied", "application_date": YESTERDAY, "expected_status": "saved"}).json()
        client.post(base + f"/v2/jobs/{job['id']}/status", json={"status": "interview", "expected_status": "applied"})
        late = client.post(base + f"/v2/jobs/{job['id']}/status/undo", json={"event_id": first["event_id"]})
        assert late.status_code == 400 and "can no longer be undone" in late.json()["detail"]

        other = add_job(root, "Other Company", "Analyst", "other")
        moved = client.post(base + f"/v2/jobs/{other['id']}/status",
                            json={"status": "withdrawn", "expected_status": "saved"}).json()
        ws = Workspace(root)
        with ws.connect() as db:
            db.execute("UPDATE activity SET occurred_at='2020-01-01T00:00:00+00:00' WHERE id=?", (moved["event_id"],))
        expired = client.post(base + f"/v2/jobs/{other['id']}/status/undo", json={"event_id": moved["event_id"]})
        assert expired.status_code == 400
        assert ws.get_job(other["id"])["status"] == "withdrawn"


def test_undo_names_only_this_jobs_moves(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        one, two = add_job(root), add_job(root, "Second Company", "Analyst", "second")
        moved = client.post(base + f"/v2/jobs/{one['id']}/status",
                            json={"status": "withdrawn", "expected_status": "saved"}).json()
        wrong = client.post(base + f"/v2/jobs/{two['id']}/status/undo", json={"event_id": moved["event_id"]})
        assert wrong.status_code == 400
        events = Workspace(root).activity()
        move = next(e for e in events if e["id"] == moved["event_id"])
        assert json.dumps(move["details"]["after"]["status"]) == '"withdrawn"'
