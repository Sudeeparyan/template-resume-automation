"""Layout fitting uses registered facts, preserves edits, and checks actual one-page usage."""
import json
import shutil
import subprocess
import sys
import yaml
import pytest
from pathlib import Path
from test_career_workspace import workspace, add
from test_workspace_v2 import service
from backend.services.resume_layout import ranked_source, set_density, measure_pages, validate_density, track_for
from backend.services.resume_studio import ResumeStudio, replace_macro
from validate_resume import evidence_ids_from_source


ML_JD = ('Machine Learning Engineer I. Train and evaluate computer vision models in PyTorch, build object detection and '
         'tracking pipelines, work with LSTM or sequence models, and prepare image datasets. Python, model training, '
         'inference and evaluation matter. Nice to have: RAG, LangChain. Boston, MA.')


def role(service, title='Machine Learning Engineer I', jd=ML_JD, location='Boston, MA'):
    return service.w.add_job('Example devices', title, location, 'https://example.test/ml-engineer', jd)


def test_track_selection_follows_the_jd(service):
    profile = service.w.profile()
    assert track_for({'title': 'Machine Learning Engineer I', 'description': ML_JD}, profile) == 'B'
    assert track_for({'title': 'Data Engineer', 'description': 'Kafka, Airflow, SQL, ETL pipelines on AWS'}, profile) == 'A'
    assert track_for({'title': 'Embedded Software Engineer', 'description': 'firmware, RTOS, microcontroller, LabVIEW test automation'}, profile) == 'D'
    assert track_for({'title': 'Software Engineer', 'description': 'C++, OOP, data structures and algorithms, backend APIs'}, profile) == 'C'


def test_ranking_keeps_evidence_and_user_wording(service):
    j = role(service)
    studio = ResumeStudio(service)
    d = studio.open(j['id'])
    d = studio.save(j['id'], d['revision'], project_id='PROJ-P02-MIGA')
    source = replace_macro(d['source'], 'SkillsLanguages', 'Python, SQL, C\\#, C++')
    ranked, info = ranked_source(source, j, service.w.evidence(), service.knowledge(), service.w.profile())
    # ML role: PyTorch leads the ML skills line and Projects come before Experience (track B).
    assert info['track'] == 'B'
    assert ranked.index('\\section{Projects}') < ranked.index('\\section{Professional Experience}')
    assert ranked.count('\\section{Projects}') == 1
    assert '\\newpage' not in ranked
    # Nothing invented: every evidence id in the ranked source is a registered, non-held entry.
    failures = []
    ids = evidence_ids_from_source(ranked, service.w.evidence(), failures)
    assert not failures and 'PROJ-P02-MIGA' in ids
    held = {c['id'] for c in service.w.evidence()['claims'] if c['status'] in {'hold', 'missing'}}
    assert not (ids & held)
    for banned in ('ICCV', 'years of experience', 'Medtronic', 'InsOpsAI'):
        assert banned not in ranked
    again, _ = ranked_source(ranked, j, service.w.evidence(), service.knowledge(), service.w.profile())
    assert again == ranked
    assert set_density(set_density(ranked)) == set_density(ranked)
    assert validate_density(set_density(ranked))[0] == []
    # Never below the contract minimum: 9pt is clamped up and 12pt down, so the block always validates,
    # but a hand-edited block or an extra fontsize override is rejected.
    assert '\\fontsize{10.0pt}' in set_density(ranked, 9)
    assert '\\fontsize{11.0pt}' in set_density(ranked, 12)
    assert validate_density(set_density(ranked).replace('\\clubpenalty=10000', '\\clubpenalty=0'))[0]
    assert '\\fontsize{8}' in validate_density(set_density(ranked) + '\\fontsize{8}{9}')[1]


def test_data_role_keeps_experience_first(service):
    j = role(service, 'Data Engineer', 'Build Kafka and Flink streaming pipelines, Airflow orchestration, SQL and AWS Glue ETL.', 'Austin, TX')
    studio = ResumeStudio(service)
    d = studio.open(j['id'])
    ranked, info = ranked_source(d['source'], j, service.w.evidence(), service.knowledge(), service.w.profile())
    assert info['track'] == 'A'
    assert ranked.index('\\section{Professional Experience}') < ranked.index('\\section{Projects}')
    assert d['fields']['SelectedProjectID'] == 'PROJ-P01-IOT'


def test_deleted_evidence_never_expands(service):
    j = role(service)
    studio = ResumeStudio(service)
    d = studio.open(j['id'])
    d = studio.save(j['id'], d['revision'], project_id='PROJ-P02-MIGA')
    active = [i for i in service.knowledge() if i['id'] not in {'PROJ-P03-DUALFIT', 'SKILL-GENAI-001'}]
    ranked, _ = ranked_source(d['source'], j, service.w.evidence(), active, service.w.profile())
    assert 'STUDIO_PROJECT_SKILLS' not in ranked
    # Ranking only reorders what the source already contains; it never adds a bullet.
    assert ranked.count('\\item') == d['source'].count('\\item')


def test_real_fit_one_full_page_and_no_profile_mutation(service):
    if not shutil.which('tectonic'):
        pytest.skip('PDF runtime unavailable')
    j = role(service)
    studio = ResumeStudio(service)
    d = studio.open(j['id'])
    d = studio.save(j['id'], d['revision'], project_id='PROJ-P02-MIGA')
    profile = service.knowledge()
    original = d['source']
    fitted = studio.fit(j['id'], d['revision'])
    assert fitted['preview']['page_count'] == 1
    assert fitted['preview']['layout']['full_pages']
    assert fitted['preview']['layout']['paper'] == 'letter'
    assert all(p['fill_percent'] >= 80 and p['paper_ok'] for p in fitted['preview']['layout']['pages'])
    assert 10 <= fitted['preview']['body_font_pt'] <= 11
    assert service.knowledge() == profile
    assert not service.profile_dirty()
    assert service.w.get_job(j['id'])['application_date'] is None
    assert len(fitted['versions']) == len(d['versions']) + 1
    target = service.w.root / 'data/output' / fitted['preview']['path']
    evidence_map = yaml.safe_load((target / 'evidence-map.yml').read_text())
    assert 'PROJ-P02-MIGA' in evidence_map['resume_claim_ids']
    assert evidence_map['resume_claim_ids'] == sorted(evidence_ids_from_source(fitted['source'], service.w.evidence(), []))
    assert not evidence_map['source_evidence_errors']
    # Full validator accepts the new layout but retains the unresolved application gates.
    validator = Path(__file__).resolve().parents[1] / 'backend/scripts/validate_resume.py'
    subprocess.run([sys.executable, str(validator), str(target / 'resume.tex'), '--studio-layout', '--compile', '--render-dir', str(target / 'validation-pages'), '--qa-json', str(target / 'qa.json')], capture_output=True, timeout=180)
    qa = json.loads((target / 'qa.json').read_text())
    assert qa['compile_ok'] and qa['layout']['full_pages'] and qa['page_count'] == 1
    assert qa['overflow_count'] == 0
    assert qa['failures'] == ['Evidence map role_eligible must be true', 'Evidence map requirements must be a non-empty list']
    assert not qa['release_ready']
    restored = studio.save(j['id'], fitted['revision'], restore_revision=d['revision'])
    assert restored['source'] == original
    assert not restored['preview']['current']


def test_fitting_fails_before_mutation_for_dirty_stale_or_custom_source(service):
    j = role(service)
    studio = ResumeStudio(service)
    d = studio.open(j['id'])
    with pytest.raises(ValueError, match='changed elsewhere'):
        studio.fit(j['id'], 99)
    changed = studio.save(j['id'], d['revision'], source=d['source'].replace('\\section{Education}', '\\section{Custom education}'))
    with pytest.raises(ValueError, match='standard template sections'):
        studio.fit(j['id'], changed['revision'])
    assert studio.get(j['id'])['source'] == changed['source']
    service.save_knowledge({'kind': 'skill', 'title': 'New test fact', 'summary': 'Needs review'})
    with pytest.raises(ValueError, match='Reconcile'):
        studio.fit(j['id'], changed['revision'])
    assert studio.get(j['id'])['revision'] == changed['revision']


def test_cuts_follow_the_documented_order():
    """A too-long page is trimmed in the order profile.yml documents, never by shrinking fonts below 10pt."""
    source = (Path(__file__).resolve().parents[1] / 'data/templates/resume-base.tex').read_text()
    # Give the supporting project a third bullet so the first cut has something to remove.
    source = source.replace('\\newcommand{\\SecondProjectBulletTwo}', '% EVIDENCE: PROJ-P04-NEWS-RAG\n\\newcommand{\\SecondProjectBulletThree}{Extra}\n\\newcommand{\\SecondProjectBulletTwo}', 1)
    source = source.replace('  \\item \\SecondProjectBulletTwo\n', '  \\item \\SecondProjectBulletTwo\n  % EVIDENCE: PROJ-P04-NEWS-RAG\n  \\item \\SecondProjectBulletThree\n', 1)
    applied = []
    order = []
    current = source
    for _ in range(6):
        nxt, label = ResumeStudio._cut_next(ResumeStudio, current, applied)
        if nxt is None:
            break
        order.append(label)
        applied.append(label)
        current = nxt
    assert order[:4] == ['supporting project third bullet', 'last bullet of the oldest role', 'coursework line', 'second degree']
    assert 'SecondProjectBulletThree' not in current
    assert 'Coursework:' not in current
    assert 'Sri Ramakrishna' not in current
    # The signature project is untouched by every cut.
    assert current.count('\\item \\SelectedProjectBullet') == 3


def test_fit_survives_a_scoring_failure_and_says_so(service, monkeypatch):
    """The fitted page is committed before scoring; a scoring error becomes a warning on the saved draft."""
    if not shutil.which('tectonic'):
        pytest.skip('PDF runtime unavailable')
    j = role(service)
    studio = ResumeStudio(service)
    d = studio.open(j['id'])
    monkeypatch.setattr(ResumeStudio, 'score', lambda self, job_id: (_ for _ in ()).throw(ValueError('Requirement extraction produced an ungrounded excerpt')))
    fitted = studio.fit(j['id'], d['revision'])
    assert fitted['revision'] == d['revision'] + 1 and fitted['preview']['page_count'] == 1
    assert any('Fitted to one page and saved' in w and 'ungrounded excerpt' in w for w in fitted['warnings'])
    assert studio.get(j['id'])['revision'] == fitted['revision']
    assert any(e['action'] == 'studio_score_failed' for e in service.w.activity())
