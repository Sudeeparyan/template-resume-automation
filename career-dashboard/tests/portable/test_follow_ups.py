"""Follow-up and thank-you drafts (services/follow_ups.py): due dates, limits, honesty.

The app only drafts: nothing is sent, and a due follow-up never changes a status.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app_fixture import add_job, profile_app
from career import Workspace


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def due_for(client, base):
    return client.get(base + "/v2/summary").json()["follow_ups"]


def test_a_follow_up_is_due_after_ten_quiet_days_and_never_changes_status(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        fresh, quiet, saved = add_job(root), add_job(root, "Quiet Company", "Analyst", "quiet"), add_job(root, "Saved Co", "Analyst", "saved")
        ws = Workspace(root)
        ws.update_job(fresh["id"], "applied", application_date=days_ago(4))
        ws.update_job(quiet["id"], "applied", application_date=days_ago(12))
        due = due_for(client, base)
        assert [item["job_id"] for item in due] == [quiet["id"]]
        assert due[0]["kind"] == "follow_up" and due[0]["days_quiet"] == 12
        assert "Applied 12 days ago" in due[0]["reason"]
        # Being due is a suggestion only: the status and date are untouched.
        assert ws.get_job(quiet["id"])["status"] == "applied"
        assert ws.get_job(saved["id"])["status"] == "saved"


def test_drafts_use_only_recorded_facts_and_two_sent_follow_ups_is_the_limit(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root, "Quiet Company", "Data Analyst", "quiet")
        ws = Workspace(root)
        ws.update_job(job["id"], "applied", application_date=days_ago(15))
        draft = client.post(base + f"/v2/jobs/{job['id']}/follow-up", json={"kind": "follow_up"})
        assert draft.status_code == 201, draft.text
        text = draft.json()["text"]
        assert "Data Analyst" in text and "Quiet Company" in text and "Example Person" in text
        assert date.fromisoformat(days_ago(15)).strftime("%B") in text
        assert draft.json()["marked_sent_at"] is None
        # The due item carries the unsent draft, so the Dashboard can reopen it.
        assert due_for(client, base)[0]["draft"]["id"] == draft.json()["id"]

        sent = client.post(base + f"/v2/follow-ups/{draft.json()['id']}/sent")
        assert sent.status_code == 200 and sent.json()["marked_sent_at"]
        # The next one waits ten days after the one they sent.
        assert due_for(client, base) == []
        with ws.connect() as db:
            db.execute("UPDATE follow_ups SET marked_sent_at=?", ((datetime.now(timezone.utc) - timedelta(days=11)).isoformat(),))
        second = due_for(client, base)
        assert len(second) == 1 and second[0]["sent"] == 1
        again = client.post(base + f"/v2/jobs/{job['id']}/follow-up", json={"kind": "follow_up"}).json()
        assert "once more" in again["text"]
        client.post(base + f"/v2/follow-ups/{again['id']}/sent")
        with ws.connect() as db:
            db.execute("UPDATE follow_ups SET marked_sent_at=?", ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
        assert due_for(client, base) == []
        assert ws.get_job(job["id"])["status"] == "applied"


def test_a_thank_you_note_is_due_after_an_interview_ends(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root, "Interview Company", "Engineer", "interview")
        Workspace(root).update_job(job["id"], "interview", application_date=days_ago(20))
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
        client.post(base + f"/v2/jobs/{job['id']}/interviews", json={
            "stage": "technical", "starts_at": yesterday.isoformat(), "duration_min": 60})
        due = due_for(client, base)
        assert [(item["job_id"], item["kind"]) for item in due] == [(job["id"], "thank_you")]
        note = client.post(base + f"/v2/jobs/{job['id']}/follow-up", json={"kind": "thank_you"}).json()
        assert "Thank you for taking the time" in note["text"] and "Engineer" in note["text"]
        client.post(base + f"/v2/follow-ups/{note['id']}/sent")
        assert due_for(client, base) == []


def test_unknown_kinds_and_removed_roles_are_refused(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root)
        assert client.post(base + f"/v2/jobs/{job['id']}/follow-up", json={"kind": "chaser"}).status_code == 422
        Workspace(root).remove_job(job["id"], "Not suitable")
        assert client.post(base + f"/v2/jobs/{job['id']}/follow-up", json={"kind": "follow_up"}).status_code == 400
        assert client.post(base + "/v2/follow-ups/999/sent").status_code == 400
