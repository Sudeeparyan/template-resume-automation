"""Studio persistence, profile capture and advisor isolation in disposable workspaces."""
import hashlib
import json
import shutil
import pytest
from concurrent.futures import ThreadPoolExecutor
from test_workspace_v2 import service
from test_career_workspace import workspace, add
from backend.services.resume_studio import ResumeStudio, replace_macro
from backend.services.agents import AgentRunner
from fastapi.testclient import TestClient
from backend.dashboard.app import create_app


def test_open_preserves_existing_and_is_idempotent(service):
    j = add(service.w)
    studio = ResumeStudio(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        drafts = list(pool.map(studio.open, [j['id'], j['id']]))
    assert drafts[0]['source'] == drafts[1]['source']
    assert len(drafts[0]['versions']) == 1
    assert service.w.get_job(j['id'])['application_date'] is None
    folder = service.w.current_folder(j['id'])
    preserved = (folder / 'resume.tex').read_text()
    second = studio.save(j['id'], 1, fields={'SkillsLanguages': 'Python, 50% & SQL'})
    assert '50\\% \\& SQL' in second['source']
    assert (folder / 'resume.tex').read_text() == preserved
    assert studio.open(j['id'])['revision'] == 2


def test_studio_capture_blocks_new_drafts_until_confirmed(service):
    studio = ResumeStudio(service)
    first, second = add(service.w), add(service.w, suffix='2')
    studio.open(first['id'])
    studio.save(first['id'], 1, fields={'SkillsLanguages': 'Python, SQL, Brand new skill'})
    assert service.profile_dirty()
    with pytest.raises(ValueError, match='Open Profile'):
        studio.open(second['id'])
    assert studio.open(first['id'])['revision'] == 2
    service.reconcile_knowledge()
    assert studio.open(second['id'])['revision'] == 1


def test_capture_deduplicates_and_never_changes_registry(service):
    studio = ResumeStudio(service)
    j = add(service.w)
    draft = studio.open(j['id'])
    evidence = (service.w.root / 'data/context/evidence.yml').read_bytes()
    fields = {'SelectedProjectTitle': 'My new project', 'SelectedProjectContext': 'Personal work | Python', 'SelectedProjectBulletOne': 'Built a local prototype.', 'SkillsLanguages': 'Python, SQL, New skill'}
    saved = studio.save(j['id'], 1, fields=fields)
    captured = [k for k in service.knowledge() if k['source'].startswith('User edit in Resume Studio')]
    assert {k['kind'] for k in captured} == {'project', 'skill'}
    assert all(k['review_state'] == 'user_updated' for k in captured)
    assert (service.w.root / 'data/context/evidence.yml').read_bytes() == evidence
    assert service.profile_dirty()
    assert studio.save(j['id'], 2, source=saved['source'])['revision'] == 2
    restored = studio.save(j['id'], 2, restore_revision=1)
    assert restored['source'] == draft['source']
    assert len(studio.get(j['id'])['captures']) == 2
    again = studio.save(j['id'], 3, fields=fields)
    assert len(again['captures']) == 2
    assert len(again['versions']) == 4
    assert service.w.get_job(j['id'])['status'] == 'prepared'


def test_concurrent_save_rejected_and_invalid_restore_atomic(service):
    studio = ResumeStudio(service)
    j = add(service.w)
    studio.open(j['id'])
    studio.save(j['id'], 1, fields={'SkillsLanguages': 'Python, first edit'})
    with pytest.raises(ValueError, match='changed elsewhere'):
        studio.save(j['id'], 1, fields={'SkillsLanguages': 'Python, lost edit'})
    with pytest.raises(ValueError, match='not found'):
        studio.save(j['id'], 2, restore_revision=999)
    assert studio.get(j['id'])['revision'] == 2


def test_deleted_project_not_reintroduced_and_dirty_blocks_new_drafts(service):
    studio = ResumeStudio(service)
    j = add(service.w)
    studio.open(j['id'])
    project = next(i for i in service.knowledge() if i['kind'] == 'project')
    service.delete_knowledge(project['id'])
    with pytest.raises(ValueError, match='active registered'):
        studio.save(j['id'], 1, project_id=project['id'])
    with pytest.raises(ValueError, match='reconciliation'):
        studio.open(add(service.w, '2')['id'])
    assert studio.save(j['id'], 1, fields={'SkillsLanguages': 'Python, existing draft edit'})['revision'] == 2


def test_project_switch_and_missing_project_warning(service):
    studio = ResumeStudio(service)
    j = add(service.w)
    studio.open(j['id'])
    d = studio.save(j['id'], 1, project_id='PROJ-P05-RESUME')
    assert d['fields']['SelectedProjectID'] == 'PROJ-P05-RESUME'
    assert not d['captures']
    d = studio.save(j['id'], 2, fields={'SelectedProjectTitle': ''})
    assert any('missing' in w for w in d['warnings'])


def test_preview_revision_staleness_and_real_compile(service):
    if not shutil.which('tectonic'):
        pytest.skip('PDF runtime unavailable')
    studio = ResumeStudio(service)
    j = add(service.w)
    studio.open(j['id'])
    d = studio.preview(j['id'], 1)
    assert d['preview']['page_count'] == 1
    assert d['preview']['current']
    pdf = service.w.root / 'data/output' / d['preview']['path'] / 'resume.pdf'
    assert pdf.exists()
    assert (pdf.parent / 'page-01.png').exists()
    d = studio.save(j['id'], 1, fields={'SkillsLanguages': 'Python, a changed draft'})
    assert not d['preview']['current']
    with pytest.raises(ValueError, match='current resume'):
        studio.preview(j['id'], 1)
    assert pdf.exists()


def test_studio_api_keeps_origin_guard_and_rejects_stale_save(service):
    j = add(service.w)
    app = create_app(service.w.root)
    app.state.agents.execute = lambda *a, **kw: {'summary': 'Fixture', 'report': 'Fixture', 'sources': [], 'limitations': []}
    with TestClient(app, base_url='http://127.0.0.1') as client:
        url = '/api/v2/studio/' + j['id']
        assert client.post(url + '/open').status_code == 200
        assert client.post(url + '/open').status_code == 200
        assert not [r for r in service.runs() if r['kind'] in ('research', 'study_plan')]  # opening a draft starts no AI
        assert client.put(url, json={'revision': 1, 'fields': {'SkillsLanguages': 'Python, SQL, API test skill'}}, headers={'Origin': 'https://foreign.test'}).status_code == 403
        assert client.put(url, json={'revision': 1, 'fields': {'SkillsLanguages': 'Python, SQL, API test skill'}}).status_code == 200
        assert client.put(url, json={'revision': 1, 'source': 'Stale'}).status_code == 400
        assert client.get(url).json()['revision'] == 2


def test_signature_project_is_never_reused_across_companies(service):
    studio = ResumeStudio(service)
    first = add(service.w)
    studio.open(first['id'])
    d = studio.save(first['id'], 1, project_id='PROJ-P05-RESUME')
    other = service.w.add_job('Another employer', 'Data Engineer', 'Austin, TX', 'https://careers.other.test/jobs/1', 'Build Kafka and Flink streaming pipelines with Airflow, SQL and AWS Glue for an entry-level data engineering team.')
    second = studio.open(other['id'])
    assert second['fields']['SelectedProjectID'] != 'PROJ-P05-RESUME'
    with pytest.raises(ValueError, match='never reused across companies'):
        studio.save(other['id'], second['revision'], project_id='PROJ-P05-RESUME')
    # It may still appear as the supporting project.
    if second['fields'].get('SecondProjectID') != 'PROJ-P05-RESUME':
        assert studio.save(other['id'], second['revision'], second_project_id='PROJ-P05-RESUME')['fields']['SecondProjectID'] == 'PROJ-P05-RESUME'
    with service.w.connect() as db:
        owners = {tuple(r) for r in db.execute('SELECT company_key, project_id FROM signature_assignments')}
    assert ('exampleemployer', 'PROJ-P05-RESUME') in owners


def test_supporting_only_projects_never_lead(service):
    """PROJ-P10-PACMAN is coursework: registered as supporting only, so it can never be the signature project."""
    studio = ResumeStudio(service)
    j = service.w.add_job('Algo Co', 'Software Engineer', 'Austin, TX', 'https://careers.algo.test/jobs/1',
                          'Software Engineer I. Algorithms, search, heuristics, A* search, BFS and DFS, data structures, Python. Pacman search algorithms coursework welcome.')
    d = studio.open(j['id'])
    assert d['fields']['SelectedProjectID'] != 'PROJ-P10-PACMAN'
    with pytest.raises(ValueError, match='supporting project only'):
        studio.save(j['id'], d['revision'], project_id='PROJ-P10-PACMAN')
