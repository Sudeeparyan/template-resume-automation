"""Compatibility adapter for the v2 grounded document assessment.

No profile, registry, notes, workspace object, email, or research access.
This diagnostic measures vocabulary coverage, never candidate eligibility.
"""
import re
from backend.assessment import assess

VERSION = 'document-coverage-v1'
ALIASES = {
    'power bi': ('power bi', 'powerbi'), 'sql': ('sql',), 'python': ('python',),
    'r': ('r',), 'excel': ('excel', 'spreadsheets'), 'tableau': ('tableau',),
    'power query': ('power query', 'm query'), 'dax': ('dax',),
    'machine learning': ('machine learning', 'ml'), 'statistics': ('statistics', 'statistical'),
    'data quality': ('data quality', 'validation', 'validate'),
    'dashboards': ('dashboard', 'dashboards'), 'reporting': ('reporting', 'reports'),
    'stakeholders': ('stakeholder', 'stakeholders'), 'requirements': ('requirements', 'brd'),
    'communication': ('communication', 'communicate', 'communicating'),
    'teamwork': ('teamwork', 'team', 'collaboration', 'collaborate'),
    'etl': ('etl',), 'snowflake': ('snowflake',), 'redshift': ('redshift',),
    'aws': ('aws', 'amazon web services'), 'azure': ('azure',),
    'data modeling': ('data modeling', 'data modelling'),
    'rag': ('rag', 'retrieval augmented', 'retrieval-augmented'),
    'langgraph': ('langgraph',), 'langchain': ('langchain',),
    'genai': ('genai', 'generative ai'), 'pandas': ('pandas',),
    'scikit-learn': ('scikit-learn', 'sklearn'), 'git': ('git', 'github'),
    'docker': ('docker',), 'kubernetes': ('kubernetes',), 'spark': ('spark',),
    'testing': ('testing', 'test', 'uat'), 'reconciliation': ('reconciliation', 'reconcile'),
    'time series': ('time series', 'time-series', 'arima'),
}


def contains(text, aliases):
    return any(re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', text, re.I) for a in aliases)


def evaluate(resume_text, job_description):
    return assess(resume_text, job_description)


def legacy_evaluate(resume_text, job_description):
    if not resume_text.strip() or not job_description.strip():
        raise ValueError('A readable finished PDF and a saved job description are required')
    requirements = []
    lines = [s.strip() for s in re.split(r'[\n.!?]+', job_description) if s.strip()]
    for term, aliases in ALIASES.items():
        jd_lines = [s for s in lines if contains(s, aliases)]
        if not jd_lines:
            continue
        preferred = all(re.search(r'preferred|desirable|nice.to.have|bonus', s, re.I) for s in jd_lines)
        weight = 1 if preferred else 2
        snippets = [s.strip() for s in resume_text.splitlines() if contains(s, aliases)]
        requirements.append({'term': term, 'weight': weight, 'matched': bool(snippets),
                             'jd_excerpt': jd_lines[0][:500], 'resume_excerpt': (snippets or [''])[0][:500]})
    total = sum(r['weight'] for r in requirements)
    matched = sum(r['weight'] for r in requirements if r['matched'])
    return {'method': VERSION, 'label': 'JD term coverage',
            'score': round(100 * matched / total) if total else None,
            'matched_weight': matched, 'total_weight': total, 'requirements': requirements,
            'gaps': [r['term'] for r in requirements if not r['matched']],
            'profile_access': False, 'ai_used': False,
            'limitations': ['Vocabulary presence is not proof of experience, proficiency or a satisfied requirement.',
                             'Uses a fixed analytics vocabulary. Other requirements, negations, dates, work rights and education need review.',
                             'Preferred-only terms weigh 1; other detected terms weigh 2. This is not an ATS score or hiring probability.',
                             'No score is returned when no supported vocabulary is detected. Artifact QA remains separate.']}
