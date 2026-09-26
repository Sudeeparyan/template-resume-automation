from datetime import date, timedelta
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from test_career_workspace import workspace, add
from career import Workspace
from backend.dashboard.app import create_app


def test_status_update_preserves_date_notes_and_audit(workspace):
    job=add(workspace)
    applied=(date.today()-timedelta(days=1)).isoformat()
    workspace.update_job(job['id'],'applied','Sent via portal',applied)
    workspace.update_job(job['id'],'applied')
    workspace.update_job(job['id'],'interview')
    updated=Workspace(workspace.root).get_job(job['id'])
    assert updated['notes']=='Sent via portal'
    assert updated['application_date']==applied
    event=workspace.activity()[0]
    assert event['details']['before']['status']=='applied'
    assert event['details']['after']['status']=='interview'
    assert json.loads((workspace.root/'data/jobs.json').read_text())[0]==updated


def test_notes_revisions_are_recoverable(workspace):
    before=(workspace.root/'data/context/UPDATES.md').read_text()
    workspace.save_profile_notes('New information')
    workspace.save_profile_notes('Revised information')
    events=workspace.activity()
    assert events[0]['details']['before']=='New information'
    assert events[1]['details']['before']==before
    assert events[0]['details']['review_required'] is True


def test_search_idempotence_and_shared_status(workspace):
    job=add(workspace)
    for _ in range(2): workspace.track_search_job(job['id'],'2026-09-10')
    assert len(workspace.search_runs())==1
    assert len(workspace.search_runs()[0]['jobs'])==1
    assert len([e for e in workspace.activity() if e['action']=='search_job_added'])==1
    workspace.update_job(job['id'],'interview','Invitation received')
    run=workspace.search_runs()[0]
    assert run['jobs'][0]['status']=='interview'
    # Status alone does not establish a submission date.
    assert run['jobs'][0]['application_date'] is None
    path=workspace.root.parent/'daily-job-search/2026-09-10/run.json'
    assert json.loads(path.read_text())==run


@pytest.mark.parametrize('date',['../outside','2026-02-30','2026-9-1','not-a-date'])
def test_search_rejects_invalid_dates(workspace,date):
    with pytest.raises(ValueError): workspace.start_search(date)
    assert workspace.search_runs()==[]


def test_search_and_activity_api(workspace):
    job=add(workspace)
    client=TestClient(create_app(workspace.root),base_url='http://127.0.0.1')
    assert client.post('/api/search-runs/2026-09-10').status_code==200
    assert client.post('/api/search-runs/2026-09-10/jobs/'+job['id']).status_code==200
    assert client.put('/api/search-runs/2026-09-10',json={'text':'Three unsuitable leads; continue research.'}).status_code==200
    assert client.get('/api/search-runs').json()['runs'][0]['notes'].startswith('Three')
    assert client.get('/api/activity').json()[0]['action']=='search_notes_updated'
    assert client.post('/api/search-runs/invalid').status_code==400
    assert client.post('/api/search-runs/2026-09-10/jobs/missing').status_code==400
    assert client.put('/api/search-runs/2026-09-10',json={'text':'foreign'},headers={'Origin':'https://foreign.example'}).status_code==403


def test_tracking_migrates_existing_database_without_losing_jobs(workspace):
    job=add(workspace)
    with workspace.connect() as db:
        db.execute('DROP TABLE search_jobs'); db.execute('DROP TABLE search_runs'); db.execute('DROP TABLE activity')
    migrated=Workspace(workspace.root)
    assert migrated.get_job(job['id'])==job
    assert migrated.activity()==[]
    migrated.update_job(job['id'],'saved','Kept after migration')
    assert migrated.activity()[0]['action']=='progress_updated'
