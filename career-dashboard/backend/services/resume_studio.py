"""Job-specific editable drafts, version history and deterministic profile capture."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import yaml
from pathlib import Path
from pypdf import PdfReader
from career import atomic_write, company_key, safe_child, tex_escape
from validate_resume import extract_zero_argument_macros, inspect_pdf, evidence_ids_from_source
from backend.services.resume_layout import ranked_source, set_density, measure_pages
from backend.pdf_compiler import tectonic_executable
from backend.resume_contract import contract_for
from backend.ai_marks import clean_pdf, clean_text
from backend.services.rewrite_guard import evidence_wording


LEGACY_PROJECT_SECTION = re.compile(r'\\section\{(?:Selected|Academic) Projects?\}')


def project_registry(evidence):
    return {p.get('id'): p for p in evidence.get('projects', []) if isinstance(p, dict)}


EMPTY_ITEM_BLOCK = re.compile(
    r'\n?\s*\\begin\{(resumeitems|itemize)\}\s*(?:%[^\n]*\n\s*)*\\end\{\1\}[ \t]*\n?'
)


def drop_empty_item_blocks(source):
    """Remove bullet lists left empty by an earlier content migration.

    LaTeX raises "perhaps a missing \\item" on an empty itemize, which made one
    saved draft uncompilable after its academic bullets were removed.
    """
    return EMPTY_ITEM_BLOCK.sub('\n', source)


ROLE_HEADING_BODY = "  \\textit{#3} \\hfill \\textit{#4}\\vspace{3pt}"
ROLE_HEADING_FIX = (
    "  \\settowidth{\\roleheadingright}{\\textit{#4}}%\n"
    "  \\parbox[t]{\\dimexpr\\linewidth-\\roleheadingright-1em\\relax}{\\raggedright\\textit{#3}}%\n"
    "  \\hfill\\parbox[t]{\\roleheadingright}{\\raggedleft\\textit{#4}}\\vspace{3pt}"
)


def upgrade_role_heading(source):
    """Keep the right-hand location on the heading line when the qualification wraps.

    The old macro put #3 and #4 in one paragraph, so a long degree line pushed the
    location onto its own line and split 'Honours (1:1)'. Giving each its own column
    lets #3 wrap while #4 stays right-aligned at the top.
    """
    if ROLE_HEADING_BODY not in source or "\\roleheadingright" in source:
        return source
    source = source.replace(ROLE_HEADING_BODY, ROLE_HEADING_FIX, 1)
    return source.replace(
        "\\newcommand{\\roleheading}[4]{%",
        "\\newlength{\\roleheadingright}\n\\newcommand{\\roleheading}[4]{%",
        1,
    )


def rename_projects_section(source):
    """Fold legacy Selected/Academic Project headings into the single Projects section."""
    if LEGACY_PROJECT_SECTION.search(source) and '\\section{Projects}' in source:
        # Renaming here would create a second Projects section; leave it for manual review.
        return source
    return LEGACY_PROJECT_SECTION.sub(r'\\section{Projects}', source)


def plain(text):
    for before, after in [(r'\textbar{}', '|'), (r'\textbackslash{}', '\\'),
                          (r'\textasciitilde{}', '~'), (r'\textasciicircum{}', '^')]:
        text = text.replace(before, after)
    return re.sub(r'\\([&%$#_{}])', r'\1', text).strip()


def replace_macro(source, name, value):
    # Older one-page templates called their combined skills line
    # SkillsLanguages. Some two-page templates expose CoreSkills instead;
    # accept the old editor/chat field as a compatibility alias.
    if name == 'SkillsLanguages' and extract_zero_argument_macros(source).get(name) is None:
        name = 'CoreSkills'
    if name == 'CoreSkills':
        from backend.resume_rules import canonicalize_skill_list
        value = canonicalize_skill_list(value)
    old = extract_zero_argument_macros(source).get(name)
    if old is None:
        raise ValueError('Template field is missing: ' + name + '. Use the source editor to restore it.')
    prefix = re.search(r'\\newcommand\{\\' + re.escape(name) + r'\}\s*\{', source)
    start = prefix.end()
    return source[:start] + tex_escape(value.replace('\n', ' ')) + source[start + len(old):]


def macro_span(source, name):
    """(start, end) offsets of a zero-argument macro's value; mirrors studioFields.ts macroSpan."""
    prefix = re.search(r'\\newcommand\{\\' + re.escape(name) + r'\}\s*\{', source)
    if not prefix:
        return None
    start, depth, index = prefix.end(), 1, prefix.end()
    while index < len(source):
        character = source[index]
        if character == '\\':
            index += 2
            continue
        if character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return start, index
        index += 1
    return None


def write_field(source, name, value):
    """Rewrite one macro value in place; mirrors studioFields.ts writeField (same matching, same escaping)."""
    span = macro_span(source, name)
    if not span:
        return source
    replacements = {'\\': r'\textbackslash{}', '|': r'\textbar{}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    escaped = re.sub(r'[\\&%$#_{}|~^]', lambda match: replacements.get(match[0], '\\' + match[0]), value.replace('\n', ' '))
    return source[:span[0]] + escaped + source[span[1]:]


# Tailoring rewrites Projects and Skills fields only; every other macro is out of bounds.
TAILORED_MACRO_NAMES = {'CoreSkills'}
TAILORED_MACRO_PREFIXES = ('SelectedProject', 'SecondProject', 'Skills')
# A tailored skills line keeps at least this many registered entries (or half of them,
# when that is more). Saved wording is not evidence for an unregistered skill.
SKILL_LINE_FLOOR = 3
# At most this many registered skills are added because the role requires them (_top_up_skills).
TOP_UP_SKILLS = 4


def tailored_macro(name):
    return name in TAILORED_MACRO_NAMES or name.startswith(TAILORED_MACRO_PREFIXES)


# A one-page fit may delete a slot's third-bullet macro outright (see the prepare cut
# in career.py); tailoring restores a missing macro next to its siblings when new
# content needs it, and leaves it cut when the new content leaves it empty.
SLOT_SUFFIXES = ('ID', 'Title', 'Context', 'BulletOne', 'BulletTwo', 'BulletThree')


def _restore_macro(source, slot, suffix, value, tag):
    for earlier in reversed(SLOT_SUFFIXES[:SLOT_SUFFIXES.index(suffix)]):
        match = re.search(r'\\newcommand\{\\' + re.escape(slot + earlier) + r'\}[^\n]*\}\n', source)
        if match:
            break
    else:
        raise ValueError('Template field is missing: ' + slot + suffix + '. Use the source editor to restore it.')
    restored = '% EVIDENCE: ' + tag + '\n\\newcommand{\\' + slot + suffix + '}{}\n'
    return write_field(source[:match.end()] + restored + source[match.end():], slot + suffix, value)


def _add_evidence_tags(source, macro, tags):
    """Merge evidence tags into the EVIDENCE comment above a macro definition."""
    pattern = re.compile(r'% EVIDENCE: ([^\n]+)\n(\\newcommand\{\\' + macro + r'\})')
    if pattern.search(source):
        return pattern.sub(lambda match: '% EVIDENCE: ' + match[1] + ' ' + ' '.join(tags) + '\n' + match[2], source, count=1)
    return re.sub(r'(\\newcommand\{\\' + macro + r'\})',
                  lambda match: '% EVIDENCE: ' + ' '.join(tags) + '\n' + match[1], source, count=1)


def _evidence_tags(source, macro):
    """The tags in the EVIDENCE comment above a macro definition."""
    match = re.search(r'% EVIDENCE: ([^\n]+)\n\\newcommand\{\\' + macro + r'\}', source)
    return match[1].split() if match else []


def _set_evidence_tags(source, macro, tags):
    """Replace the EVIDENCE comment above a macro definition (adding one if missing)."""
    line = '% EVIDENCE: ' + ' '.join(tags) + '\n'
    pattern = re.compile(r'% EVIDENCE: [^\n]*\n(\\newcommand\{\\' + macro + r'\})')
    if pattern.search(source):
        return pattern.sub(lambda match: line + match[1], source, count=1)
    return re.sub(r'(\\newcommand\{\\' + macro + r'\})', lambda match: line + match[1], source, count=1)


def read_saved_preview(root, folder, source, revision):
    """Read optional preview metadata, keeping damaged artifacts recoverable."""
    preview_file = folder / 'preview.json'
    if not preview_file.exists():
        return None, None, False
    try:
        saved = json.loads(preview_file.read_text(encoding='utf-8'))
        if (not isinstance(saved, dict)
                or type(saved.get('revision')) is not int
                or type(saved.get('page_count')) is not int
                or saved['page_count'] < 1
                or not isinstance(saved.get('path'), str) or not saved['path']
                or not isinstance(saved.get('source_sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', saved['source_sha256'])
                or (saved.get('layout') is not None and not isinstance(saved['layout'], dict))):
            raise ValueError('Incomplete preview metadata')
        pdf = safe_child(root / 'data/output', saved['path'] + '/resume.pdf')
        saved['current'] = (pdf.is_file() and saved['revision'] == revision
                            and saved['source_sha256'] == hashlib.sha256(source.encode()).hexdigest())
        return saved, pdf, False
    except (OSError, UnicodeError, ValueError):
        return None, None, True


class ResumeStudio:
    def __init__(self, service):
        self.s, self.w = service, service.w
        self.lock = threading.RLock()
        with self.w.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS studio_drafts(job_id TEXT PRIMARY KEY REFERENCES jobs(id), source TEXT NOT NULL, revision INTEGER NOT NULL, folder TEXT NOT NULL, updated_at TEXT NOT NULL, profile_revision TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS studio_versions(job_id TEXT NOT NULL REFERENCES jobs(id), revision INTEGER NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id,revision));
            CREATE TABLE IF NOT EXISTS resume_scores(job_id TEXT NOT NULL REFERENCES jobs(id), revision INTEGER NOT NULL, source_sha256 TEXT NOT NULL, pdf_sha256 TEXT NOT NULL, jd_sha256 TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id,revision,jd_sha256,pdf_sha256));
            CREATE TABLE IF NOT EXISTS studio_captures(job_id TEXT NOT NULL REFERENCES jobs(id), fingerprint TEXT NOT NULL, knowledge_id TEXT NOT NULL REFERENCES knowledge(id), PRIMARY KEY(job_id,fingerprint));
            ''')

    def signature_unique(self):
        return bool((self.w.profile().get('project_selection') or {}).get('signature_unique_per_company', False))

    def contract(self, job_id):
        return contract_for(self.w.root, self.w.get_job(job_id).get('market') or None)

    def resume_evidence_problem(self, job_id, source):
        """Explain an unsupported model suggestion still present in a saved draft.

        Older versions printed predicted items before review, and a keep decision did
        not establish a candidate fact. Preserve those drafts for editing and removal,
        but never compile or export their unsupported claims as a resume.
        """
        if re.search(r'\bresume_items:[A-Za-z0-9_-]+', source):
            return ('This draft contains suggested content without registered candidate evidence. '
                    'Remove it or record the real completed work in Profile, reconcile its evidence, and tailor again.')
        macros = {name: plain(value) for name, value in extract_zero_argument_macros(source).items()}
        skill_names = {part.strip().casefold() for name, value in macros.items()
                       if name == 'CoreSkills' or name.startswith('Skills')
                       for part in re.split(r'[,;]', value) if part.strip()}
        registered_skills = self._verified_skill_pool()
        registry = project_registry(self.w.evidence())
        with self.w.connect() as db:
            rows = db.execute("SELECT * FROM resume_items WHERE job_id=? AND origin='predicted'", (job_id,)).fetchall()
        for row in rows:
            if row['section'] == 'skills':
                key = row['content'].strip().casefold()
                present = key in skill_names and key not in registered_skills
            else:
                try:
                    title = str(json.loads(row['content']).get('title') or '').strip().casefold()
                except (ValueError, TypeError, AttributeError):
                    title = ''
                present = False
                for slot in ('SelectedProject', 'SecondProject'):
                    if not (macros.get(slot + 'ID') == row['id']
                            or bool(title and macros.get(slot + 'Title', '').casefold() == title)):
                        continue
                    registered = registry.get(macros.get(slot + 'ID')) or {}
                    content = registered.get('resume_content') or {}
                    bullets = [str(bullet) for bullet in content.get('bullets', [])][:3]
                    matches_registry = (registered.get('status') not in {'hold', 'missing'}
                                        and content and macros.get(slot + 'Title') == content.get('title')
                                        and macros.get(slot + 'Context', '') == content.get('context', '')
                                        and all(macros.get(slot + 'Bullet' + suffix, '') == bullet
                                                for suffix, bullet in zip(('One', 'Two', 'Three'), bullets + [''] * (3 - len(bullets)))))
                    if not matches_registry:
                        present = True
                        break
            if present:
                return ('This draft still contains a predicted skill or project without registered candidate evidence. '
                        'A keep decision is not evidence. Remove it or record the real completed work in Profile, '
                        'reconcile its evidence, and tailor again.')
        errors = []
        evidence_ids_from_source(source, self.w.evidence(), errors)
        if errors:
            return 'This resume contains claims without usable registered candidate evidence: ' + errors[0]
        return ''

    def _require_resume_evidence(self, job_id, source):
        problem = self.resume_evidence_problem(job_id, source)
        if problem:
            raise ValueError(problem)

    def stale_drafts(self):
        """Drafts whose compiled PDF no longer matches the saved source."""
        stale = []
        with self.w.connect() as db:
            rows = db.execute('SELECT job_id FROM studio_drafts ORDER BY job_id').fetchall()
        for row in rows:
            draft = self.get(row['job_id'])
            preview = draft.get('preview')
            if not preview or not preview.get('current') or preview.get('revision') != draft['revision']:
                stale.append({'job_id': row['job_id'], 'revision': draft['revision'],
                              'reason': 'never compiled' if not preview else 'compiled from an older source'})
        return stale

    def recompile_stale(self, job_ids=None, on_progress=None):
        """Rebuild the PDF for every draft whose source changed after its last compile.

        migrate_current_drafts rewrites saved LaTeX without compiling, which leaves the
        stored PDF showing the superseded wording and hides the PDF download. This
        reconciles the artifacts with the source; it never edits the source itself.
        """
        targets = [d for d in self.stale_drafts() if job_ids is None or d['job_id'] in set(job_ids)]
        rebuilt, failed = [], []
        for target in targets:
            if on_progress:
                on_progress(target)
            try:
                draft = self.preview(target['job_id'], target['revision'])
                rebuilt.append({'job_id': target['job_id'], 'revision': target['revision'],
                                'pages': (draft.get('preview') or {}).get('page_count')})
            except Exception as error:
                failed.append({'job_id': target['job_id'], 'revision': target['revision'], 'error': str(error)[:500]})
        return {'checked': len(targets), 'rebuilt': rebuilt, 'failed': failed}

    def open(self, job_id):
        with self.lock:
            job = self.w.get_job(job_id)
            with self.w.connect() as db:
                row = db.execute('SELECT * FROM studio_drafts WHERE job_id=?', (job_id,)).fetchone()
            if not row:
                if self.s.profile_dirty():
                    raise ValueError('Profile edits need evidence reconciliation before creating a new resume. Open Profile and confirm the pending entries; existing Studio drafts remain editable.')
                if not job['folder']:
                    self.w.prepare(job_id)
                folder = self.w.current_folder(job_id)
                source = (folder / 'resume.tex').read_text(encoding='utf-8')
                # Preserve existing prepared wording. Only a newly prepared draft gets skill ordering.
                if not job['folder']:
                    macros = extract_zero_argument_macros(source)
                    jd = (job['title'] + ' ' + job['description']).casefold()
                    for name, value in macros.items():
                        if name.startswith('Skills') or name == 'CoreSkills':
                            separator = '; ' if '; ' in value else ', '
                            skills = plain(value).split(separator)
                            skills.sort(key=lambda skill: skill.casefold() not in jd)
                            source = replace_macro(source, name, separator.join(skills))
                mapping_file = folder / 'evidence-map.yml'
                draft_profile_revision = (yaml.safe_load(mapping_file.read_text(encoding='utf-8')) or {}).get('candidate_revision', self.w.evidence()['candidate_revision']) if mapping_file.exists() else self.w.evidence()['candidate_revision']
                studio_folder = folder / 'studio'
                studio_folder.mkdir(exist_ok=True)
                stamp = self.s.now()
                with self.w.connect() as db:
                    db.execute('INSERT INTO studio_drafts VALUES(?,?,?,?,?,?)', (job_id, source, 1, str(studio_folder.relative_to(self.w.root)), stamp, draft_profile_revision))
                    db.execute('INSERT INTO studio_versions VALUES(?,?,?,?)', (job_id, 1, source, stamp))
                    self.w.record_event(db, 'studio_opened', job_id, revision=1)
                self.w.export_tracking()
            return self.get(job_id)

    def get(self, job_id, *, write_source=True):
        contract = self.contract(job_id)
        shape = contract.describe_pages()
        with self.w.connect() as db:
            row = db.execute('SELECT * FROM studio_drafts WHERE job_id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('Open Resume Studio for this job first')
            versions = [dict(r) for r in db.execute('SELECT revision,created_at FROM studio_versions WHERE job_id=? ORDER BY revision DESC', (job_id,))]
            captures = [dict(r) for r in db.execute('SELECT DISTINCT k.id,k.kind,k.title,k.review_state,k.deleted FROM studio_captures c JOIN knowledge k ON k.id=c.knowledge_id WHERE c.job_id=?', (job_id,))]
        result = dict(row)
        folder = safe_child(self.w.root / 'data/output', str(Path(row['folder']).relative_to('data/output')))
        if write_source:
            atomic_write(folder / 'resume.tex', row['source'])
        preview, preview_pdf, damaged_preview = read_saved_preview(self.w.root, folder, row['source'], row['revision'])
        fields = {k: plain(v) for k, v in extract_zero_argument_macros(row['source']).items()
                  if k in {'ResumeSummary', 'CoreSkills', 'Coursework'}
                  or k.startswith(('Skills', 'SelectedProject', 'SecondProject'))}
        warnings = []
        if damaged_preview:
            warnings.append('Saved preview metadata is damaged or incomplete. Rebuild the preview in Resume Studio.')
        if self.s.profile_dirty():
            warnings.append('Profile has unreviewed changes. Reconcile evidence before releasing this resume; this saved draft may contain older wording.')
        if row['profile_revision'] != self.w.evidence()['candidate_revision']:
            warnings.append('This draft was started with an older evidence revision. Review it against the current profile.')
        if contract.required_selected_projects >= 1 and (not fields.get('SelectedProjectTitle') or not fields.get('SelectedProjectBulletOne')):
            warnings.append('Your selected project is missing. Add a project before exporting your application resume.')
        if preview and preview.get('layout') and not preview['layout'].get('full_pages'):
            warnings.append(f'The preview does not fill exactly {shape}. Use Fit to {shape} to rank supported content and balance the layout.')
        warnings.append(f'Draft only: wording, evidence, {shape} layout and visual review must pass before release.')
        if contract.required_selected_projects >= 2 and (not fields.get('SecondProjectID') or not fields.get('SecondProjectTitle') or not fields.get('SecondProjectBulletOne')):
            warnings.append('A supporting project is required alongside the signature project. Sync profile & rank projects, or select a second registered project.')
        active_projects = {i['id'] for i in self.s.knowledge() if i['kind'] == 'project' and i['review_state'] == 'registered'}
        with self.w.connect() as db:
            score_row = db.execute('SELECT * FROM resume_scores WHERE job_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (job_id,)).fetchone()
        match = None
        current_pdf_hash = hashlib.sha256(preview_pdf.read_bytes()).hexdigest() if preview_pdf and preview_pdf.is_file() else None
        if score_row:
            match = {**json.loads(score_row['result']), 'revision': score_row['revision'], 'created_at': score_row['created_at'],
                     'current': score_row['source_sha256'] == hashlib.sha256(row['source'].encode()).hexdigest()
                     and score_row['jd_sha256'] == hashlib.sha256(self.w.get_job(job_id)['description'].encode()).hexdigest()
                     and score_row['pdf_sha256'] == current_pdf_hash}
        library = self.s.knowledge()
        ranked = self.w.rank_projects(self.w.get_job(job_id)['description'])
        ranks = {p['id']: (index + 1, p) for index, p in enumerate(ranked)}
        projects = []
        for item in library:
            if item['kind'] != 'project':
                continue
            rank, known = ranks.get(item['id'], (None, {}))
            projects.append({'id': item['id'], 'title': item['title'], 'rank': rank if item['review_state'] == 'registered' else None,
                             'review_state': item['review_state'], 'eligible': item['id'] in active_projects and bool(known),
                             'score': known.get('match_count'), 'reason': known.get('matched_terms', [])})
        projects.sort(key=lambda p: (p['rank'] is None, p['rank'] or 999, p['title']))
        return {**result, 'fields': fields, 'match': match, 'project_library': projects, 'preview': preview, 'versions': versions,
                'captures': captures, 'warnings': warnings, 'file_root': str(folder.relative_to(self.w.root / 'data/output')),
                'projects': [p for p in self.w.rank_projects(self.w.get_job(job_id)['description']) if p['id'] in active_projects]}

    def capture(self, db, job_id, kind, title, summary):
        title, summary = title.strip()[:250], summary.strip()[:30000]
        if not title:
            return
        fingerprint = hashlib.sha256((kind + '\n' + title.casefold() + '\n' + summary).encode()).hexdigest()
        if db.execute('SELECT 1 FROM studio_captures WHERE job_id=? AND fingerprint=?', (job_id, fingerprint)).fetchone():
            return
        # Reuse an exact entry across jobs; never resurrect a deleted profile entry.
        old = db.execute('SELECT id,deleted FROM knowledge WHERE kind=? AND lower(title)=lower(?) AND summary=?', (kind, title, summary)).fetchone()
        if old and old['deleted']:
            return
        related = db.execute("SELECT * FROM knowledge WHERE kind=? AND lower(title)=lower(?) AND source=?", (kind, title, 'User edit in Resume Studio for job ' + job_id)).fetchone()
        if not old and related and not related['deleted']:
            db.execute("UPDATE knowledge SET summary=?,revision=revision+1,review_state='user_updated',updated_at=? WHERE id=?", (summary, self.s.now(), related['id']))
            self.w.record_event(db, 'profile_entry_saved', job_id, entry_id=related['id'], before=dict(related), summary=summary, origin='resume_studio')
            old = related
        key = old['id'] if old else 'studio:' + fingerprint[:24]
        if not old:
            db.execute('INSERT INTO knowledge VALUES(?,?,?,?,?,?,?,?,?,?)', (key, kind, title, summary,
                json.dumps({'origin': 'resume_studio', 'job_id': job_id, 'user_supplied': True}),
                'User edit in Resume Studio for job ' + job_id, 1, 0, 'user_updated', self.s.now()))
            self.w.record_event(db, 'profile_entry_saved', job_id, entry_id=key, origin='resume_studio', kind=kind)
        db.execute('INSERT OR IGNORE INTO studio_captures VALUES(?,?,?)', (job_id, fingerprint, key))

    def track(self, db, job_id, before, after):
        if 'SecondProjectTitle' in after:
            # Reuse the first-slot tracker on a document containing only second-slot definitions.
            def second_only(text):
                return '\n'.join(line for line in text.splitlines() if 'SecondProject' in line).replace('SecondProject', 'SelectedProject')
            self.track(db, job_id, second_only(before), second_only(after))
        old, new = extract_zero_argument_macros(before), extract_zero_argument_macros(after)
        keys = ['SelectedProjectTitle', 'SelectedProjectContext', 'SelectedProjectBulletOne', 'SelectedProjectBulletTwo', 'SelectedProjectBulletThree']
        if any(old.get(k) != new.get(k) for k in keys) and new.get('SelectedProjectTitle'):
            title = plain(new['SelectedProjectTitle'])
            summary = '\n'.join(plain(new[k]) for k in keys[1:] if new.get(k))
            registered = any(p.get('resume_content', {}).get('title') == title and
                             '\n'.join([p['resume_content'].get('context', ''), *p['resume_content'].get('bullets', [])]) == summary
                             for p in self.w.evidence()['projects'])
            if not registered:
                self.capture(db, job_id, 'project', title, summary)
        def skills(source, macros):
            text = ';'.join(plain(v) for k, v in macros.items() if k.startswith('Skills') or k == 'CoreSkills')
            heading = next((r'\section{' + name + '}' for name in ('Skills', 'Technical Skills')
                            if r'\section{' + name + '}' in source), None)
            section = source.split(heading, 1) if heading else [source]
            if len(section) > 1:
                body = section[1].split(r'\section{')[0].split(r'\end{document}')[0]
                for line in body.splitlines():
                    if line.strip().startswith(r'\textbf{') and not line.strip().startswith(r'\textbf{Languages'):
                        text += ';' + re.sub(r'\\\\\[.*?\]', '', line.split('}', 1)[-1])
            from backend.resume_rules import parse_skills
            return {s: s.casefold() for s in parse_skills(plain(text))}
        previous = set(skills(before, old).values())
        known = ' '.join(r['title'] + ' ' + r['summary'] for r in db.execute("SELECT title,summary FROM knowledge WHERE kind='skill' AND deleted=0")).casefold()
        for skill, normalized in skills(after, new).items():
            if normalized not in previous and not re.search(r'(?<!\w)' + re.escape(normalized) + r'(?!\w)', known):
                self.capture(db, job_id, 'skill', skill, skill)

    def save(self, job_id, revision, source=None, fields=None, project_id=None, restore_revision=None, second_project_id=None):
        with self.lock, self.w.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM studio_drafts WHERE job_id=?', (job_id,)).fetchone()
            if not row or row['revision'] != revision:
                raise ValueError('This resume changed elsewhere. Copy your edits, then reload before saving.')
            before = row['source']
            if restore_revision is not None:
                version = db.execute('SELECT source FROM studio_versions WHERE job_id=? AND revision=?', (job_id, restore_revision)).fetchone()
                if not version:
                    raise ValueError('Saved version not found')
                source = version['source']
            source = before if source is None else source
            for name, value in (fields or {}).items():
                if name not in {'ResumeSummary', 'CoreSkills', 'Coursework'} and not name.startswith(('SelectedProject', 'SecondProject', 'Skills')):
                    raise ValueError('Unknown resume field')
                source = replace_macro(source, name, value)
            if project_id or second_project_id:
                from backend.services.resume_projects import install_project
                active = {i['id']: i for i in self.s.knowledge()}
                for chosen, second in [(project_id, False), (second_project_id, True)]:
                    if not chosen:
                        continue
                    project = next((p for p in self.w.rank_projects('') if p['id'] == chosen), None)
                    if not project or chosen not in active or active[chosen]['review_state'] != 'registered':
                        raise ValueError('Choose an active registered project. Edited projects need evidence review.')
                    if not second and project_registry(self.w.evidence()).get(chosen, {}).get('signature_eligible') is False:
                        raise ValueError(f'{chosen} is registered as a supporting project only and cannot lead Projects.')
                    other = extract_zero_argument_macros(source).get('SelectedProjectID' if second else 'SecondProjectID')
                    if other == chosen:
                        raise ValueError('Choose two distinct projects')
                    if not second and self.signature_unique():
                        # One signature project per company: another company's signature may only be a supporting project here.
                        mine = company_key(db.execute('SELECT company FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
                        owner = db.execute('SELECT company_key FROM signature_assignments WHERE project_id=? AND company_key<>?', (chosen, mine)).fetchone()
                        if owner:
                            raise ValueError(f'{chosen} is already the signature project for another company ({owner[0]}). A signature project is never reused across companies; use it as the supporting project instead.')
                    source = install_project(source, project, second)
            if extract_zero_argument_macros(before).get('SelectedProjectID') != extract_zero_argument_macros(source).get('SelectedProjectID'):
                source = re.sub(r'% STUDIO_PROJECT_SKILLS\n% EVIDENCE:[^\n]+\n[^\n]+\n', '', source)
            source = clean_text(source)  # hidden characters pasted into the source editor
            if not source.strip() or len(source) > 150000:
                raise ValueError('Resume source must contain 1–150,000 characters')
            if source != before:
                if restore_revision is None:
                    self.track(db, job_id, before, source)
                stamp = self.s.now()
                db.execute('UPDATE studio_drafts SET source=?,revision=revision+1,updated_at=? WHERE job_id=?', (source, stamp, job_id))
                db.execute('INSERT INTO studio_versions VALUES(?,?,?,?)', (job_id, revision + 1, source, stamp))
                signature = extract_zero_argument_macros(source).get('SelectedProjectID')
                if signature and signature != extract_zero_argument_macros(before).get('SelectedProjectID'):
                    db.execute('INSERT OR REPLACE INTO signature_assignments(company_key, project_id, job_id, assigned_at) VALUES(?,?,?,?)',
                               (company_key(db.execute('SELECT company FROM jobs WHERE id=?', (job_id,)).fetchone()[0]), signature, job_id, stamp))
                self.w.record_event(db, 'studio_saved', job_id, revision=revision + 1, restored_from=restore_revision)
        self.s.export_profile()
        self.w.export_tracking()
        return self.get(job_id)

    def write_review_sources(self, target, source, job_id):
        """Keep each preview's JD and claim references aligned without claiming a review."""
        original = self.w.current_folder(job_id)
        mapping_path = original / 'evidence-map.yml'
        mapping = yaml.safe_load(mapping_path.read_text(encoding='utf-8')) if mapping_path.exists() else {}
        snapshot = original / 'job-description.md'
        if snapshot.exists():
            shutil.copy2(snapshot, target / 'job-description.md')
            mapping['job_snapshot_sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        errors = []
        ids = evidence_ids_from_source(source, self.w.evidence(), errors)
        mapping.update(job_id=job_id, resume_claim_ids=sorted(ids),
                       selected_project_id=extract_zero_argument_macros(source).get('SelectedProjectID'),
                       selected_project_ids=[extract_zero_argument_macros(source).get(k) for k in ('SelectedProjectID', 'SecondProjectID') if extract_zero_argument_macros(source).get(k)],
                       candidate_revision=self.w.evidence()['candidate_revision'],
                       studio_review_required=True, source_evidence_errors=errors)
        atomic_write(target / 'resume.tex', source)
        atomic_write(target / 'evidence-map.yml', yaml.safe_dump(mapping, sort_keys=False))

    def preview(self, job_id, revision):
        with self.lock:
            draft = self.get(job_id)
            if draft['revision'] != revision:
                raise ValueError('Save or reload the current resume before compiling.')
            self._require_resume_evidence(job_id, draft['source'])
            executable = tectonic_executable()
            if not executable:
                raise ValueError('PDF compiler is unavailable. Your draft is saved; install Tectonic and try again.')
            folder = safe_child(self.w.root / 'data/output', draft['file_root'])
            with tempfile.TemporaryDirectory(prefix='studio-preview-') as temp:
                build = Path(temp)
                (build / 'resume.tex').write_text(draft['source'], encoding='utf-8')
                try:
                    run = subprocess.run([executable, '--untrusted', '--outdir', str(build), 'resume.tex'], cwd=build, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
                except subprocess.TimeoutExpired:
                    raise ValueError('Preview timed out. Your source is saved; check it for loops or very large content.') from None
                if run.returncode or not (build / 'resume.pdf').exists():
                    raise ValueError('Preview could not compile. Your edits are saved.\n' + (run.stderr + run.stdout)[-3500:])
                clean_pdf(build / 'resume.pdf')
                if len(PdfReader(str(build / 'resume.pdf')).pages) > 10:
                    raise ValueError('Preview exceeds ten pages. Reduce the content before compiling again.')
                report = inspect_pdf(build / 'resume.pdf', build / 'pages')
                target = folder / ('preview-' + str(revision))
                target.mkdir(exist_ok=True)
                self.write_review_sources(target, draft['source'], job_id)
                shutil.copy2(build / 'resume.pdf', target / 'resume.pdf')
                for page in (build / 'pages').glob('*.png'):
                    shutil.copy2(page, target / page.name)
                metadata = {'revision': revision, 'source_sha256': hashlib.sha256(draft['source'].encode()).hexdigest(), 'page_count': report['page_count'], 'created_at': self.s.now(), 'path': str(target.relative_to(self.w.root / 'data/output')), 'review_required': True, 'layout': measure_pages(build / 'resume.pdf', build / 'pages', contract=self.contract(job_id))}
                atomic_write(folder / 'preview.json', json.dumps(metadata))
            self.score(job_id)
            return self.get(job_id)


    def fit(self, job_id, revision):
        """Commit a ranked version that fills the profile's exact page contract.

        Font goes from the contract maximum down to its minimum (never lower); if the
        page still overflows, content is cut in the order profile.yml documents, one
        registered construct at a time. Nothing is ever added that is not registered.
        """
        from backend.resume_contract import contract_for
        from backend.services.resume_projects import install_project
        contract = self.contract(job_id)
        with self.lock:
            draft = self.get(job_id)
            if draft['revision'] != revision:
                raise ValueError('This resume changed elsewhere. Reload before fitting the page.')
            self._require_resume_evidence(job_id, draft['source'])
            if self.s.profile_dirty() or draft['profile_revision'] != self.w.evidence()['candidate_revision']:
                raise ValueError('Reconcile your Profile edits with the evidence registry before ranking content. You can still edit and preview the saved draft.')
            base_source = draft['source']
            if contract.required_selected_projects >= 2 and not extract_zero_argument_macros(base_source).get('SecondProjectID'):
                choices = [p for p in draft['projects'] if p['id'] != draft['fields'].get('SelectedProjectID')]
                if not choices:
                    raise ValueError('A signature project and one supporting project are required')
                base_source = install_project(base_source, choices[0], second=True)
            source, ranking = ranked_source(base_source, self.w.get_job(job_id), self.w.evidence(), self.s.knowledge(), self.w.profile(), contract=contract)
            executable = tectonic_executable()
            if not executable:
                raise ValueError('PDF compiler is unavailable. Install Tectonic and try again.')
            folder = safe_child(self.w.root / 'data/output', draft['file_root'])
            fixed_font = self.s.pref('resume_font:' + job_id)
            if fixed_font:
                density_options = [(float(fixed_font), 5.0), (float(fixed_font), 6.0)] if contract.pages > 1 else [(float(fixed_font), 2.0)]
            elif contract.pages > 1:
                # Multi-page resumes need enough density to fill every page. Try the
                # full readable range and the slightly roomier registered-item spacing
                # that the previous fitter used successfully.
                points = []
                point = contract.max_body_pt
                while point >= contract.min_body_pt:
                    points.append(round(point, 2))
                    point -= 0.25
                density_options = [(point, spacing) for spacing in (5.0, 6.0) for point in points]
            else:
                points = [contract.max_body_pt, round((contract.max_body_pt + contract.min_body_pt) / 2, 2), contract.min_body_pt]
                density_options = [(point, 2.0) for point in points]
            cuts_applied = []
            best = None
            with tempfile.TemporaryDirectory(prefix='studio-fit-') as temp:
                attempt = 0
                while attempt < 12 and best is None:
                    for point, item_sep in density_options:
                        build = Path(temp) / str(attempt)
                        build.mkdir()
                        attempt += 1
                        candidate = set_density(source, point, item_sep, contract=contract)
                        (build / 'resume.tex').write_text(candidate, encoding='utf-8')
                        try:
                            result = subprocess.run([executable, '--untrusted', '--outdir', str(build), 'resume.tex'], cwd=build, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
                        except subprocess.TimeoutExpired:
                            raise ValueError('Page fitting timed out. Your previous draft remains unchanged.') from None
                        if result.returncode or not (build / 'resume.pdf').exists():
                            raise ValueError('Could not compile the ranked draft. Your previous version is preserved.\n' + (result.stderr + result.stdout)[-2000:])
                        clean_pdf(build / 'resume.pdf')
                        page_count = len(PdfReader(str(build / 'resume.pdf')).pages)
                        if page_count > contract.pages:
                            continue  # try a smaller allowed font, then cut content
                        inspect_pdf(build / 'resume.pdf', build / 'pages')
                        layout = measure_pages(build / 'resume.pdf', build / 'pages', contract=contract)
                        if layout['full_pages'] or (contract.relaxed_min_words and page_count == contract.pages):
                            best = (build, candidate, layout, round(float(point), 2))
                            break
                    if best is None:
                        if contract.pages > 1:
                            break
                        # Every allowed font overflowed: cut the next construct in the documented order.
                        cut_source, label = self._cut_next(source, cuts_applied)
                        if cut_source is None:
                            raise ValueError('The content could not fit ' + contract.describe_pages() + ' at ' + f'{contract.min_body_pt:g}' + 'pt even after the documented cuts (' + '; '.join(cuts_applied) + '). Your draft is unchanged. Shorten custom text, then try again.')
                        source = cut_source
                        cuts_applied.append(label)
                if best is None:
                    raise ValueError('The available registered content could not fill exactly ' + contract.describe_pages()
                                     + f' cleanly at {contract.min_body_pt:g}\u2013{contract.max_body_pt:g}pt. Your draft is unchanged. '
                                       'Add relevant confirmed detail or shorten unusually long custom text, then try again.')
                build, candidate, layout, body_pt = best
                stamp = self.s.now()
                new_revision = revision + 1 if candidate != draft['source'] else revision
                with self.w.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT revision FROM studio_drafts WHERE job_id=?', (job_id,)).fetchone()
                    if current[0] != revision:
                        raise ValueError('This resume changed elsewhere during fitting. Reload before retrying.')
                    if new_revision != revision:
                        # Registry-derived reordering and cuts are not new user claims: do not feed the profile tracker.
                        db.execute('UPDATE studio_drafts SET source=?,revision=?,updated_at=? WHERE job_id=?', (candidate, new_revision, stamp, job_id))
                        db.execute('INSERT INTO studio_versions VALUES(?,?,?,?)', (job_id, new_revision, candidate, stamp))
                    self.w.record_event(db, 'studio_fitted_pages', job_id, revision=new_revision, target_pages=contract.pages, body_font_pt=body_pt, page_fill=[p['fill_percent'] for p in layout['pages']], section_order=ranking['section_order'], cuts=cuts_applied)
                target = folder / ('preview-' + str(new_revision))
                target.mkdir(exist_ok=True)
                self.write_review_sources(target, candidate, job_id)
                shutil.copy2(build / 'resume.pdf', target / 'resume.pdf')
                for page in (build / 'pages').glob('*.png'):
                    shutil.copy2(page, target / page.name)
                metadata = {'revision': new_revision, 'source_sha256': hashlib.sha256(candidate.encode()).hexdigest(), 'page_count': layout['page_count'], 'created_at': stamp, 'path': str(target.relative_to(self.w.root / 'data/output')), 'review_required': True, 'layout': layout, 'ranking': {**ranking, 'cuts': cuts_applied}, 'body_font_pt': body_pt}
                atomic_write(folder / 'preview.json', json.dumps(metadata, indent=2))
                atomic_write(target / 'layout-review.json', json.dumps(metadata, indent=2))
                atomic_write(target / 'resume.tex', candidate)
            self.w.export_tracking()
            try:
                self.score(job_id)
            except ValueError as error:
                # The page is fitted and saved by now; a scoring problem must not read as a failed fit.
                with self.w.connect() as db:
                    self.w.record_event(db, 'studio_score_failed', job_id, revision=new_revision, error=str(error))
                draft = self.get(job_id)
                draft['warnings'] = [*draft.get('warnings', []),
                                     'Fitted to ' + contract.describe_pages() + ' and saved, but the match score could not be computed: ' + str(error)]
                return draft
            return self.get(job_id)

    # Kept for callers that still say "fill"; the contract decides the page count.
    fill = fit

    # The documented cut order (profile.yml resume_contract.cut_order), one step per call.
    CUT_STEPS = (
        ('supporting project third bullet', r'(?m)^\s*% EVIDENCE: [^\n]+\n\s*\\item \\SecondProjectBulletThree\n'),
        ('last bullet of the oldest role', None),
        ('coursework line', r'(?m)^% EVIDENCE: [^\n]+\n\\textbf\{Coursework:\}[^\n]*\n'),
        ('second degree', None),
    )

    def _cut_next(self, source, applied):
        """Apply the first not-yet-applied cut that changes the source. Returns (source, label) or (None, None)."""
        for label, pattern in self.CUT_STEPS:
            if label in applied:
                continue
            if pattern:
                new = re.sub(pattern, '', source, count=1)
                if label == 'supporting project third bullet':
                    new = re.sub(r'(?m)^% EVIDENCE: [^\n]+\n\\newcommand\{\\SecondProjectBulletThree\}\{[^\n]*\}\n', '', new, count=1)
            elif label == 'last bullet of the oldest role':
                new = self._drop_last_bullet_of_oldest_role(source)
            else:
                new = self._drop_second_degree(source)
            if new != source:
                return new, label
            applied.append(label)
        return None, None

    @staticmethod
    def _drop_second_degree(source):
        """Remove the last degree heading in Education when more than one is printed."""
        edu = re.search(r'\\section\{Education\}[\s\S]*?(?=\\section\{|\\end\{document\})', source)
        if not edu:
            return source
        # A degree's result line goes with its heading, never onto the degree above.
        headings = list(re.finditer(r'(?m)^% EVIDENCE: [^\n]+\n\\roleheading\{[^\n]*\n'
                                    r'(?:% EVIDENCE: [^\n]+\n\\textbf\{Result:\}[^\n]*\n)?\n?', edu[0]))
        if len(headings) < 2:
            return source
        last = headings[-1]
        new_edu = edu[0][:last.start()] + edu[0][last.end():]
        return source[:edu.start()] + new_edu + source[edu.end():]

    @staticmethod
    def _drop_last_bullet_of_oldest_role(source):
        """Remove the final bullet of the last role in Professional Experience (roles are chronological, newest first)."""
        exp = re.search(r'\\section\{Professional Experience\}[\s\S]*?(?=\\section\{|\\end\{document\})', source)
        if not exp:
            return source
        blocks = list(re.finditer(r'\\begin\{resumeitems\}([\s\S]*?)\\end\{resumeitems\}', exp[0]))
        for block in reversed(blocks):
            items = list(re.finditer(r'(?m)^\s*% EVIDENCE: [^\n]+\n\s*\\item [^\n]+\n', block[1]))
            if len(items) >= 2:
                last = items[-1]
                body = block[1][:last.start()] + block[1][last.end():]
                new_block = '\\begin{resumeitems}' + body + '\\end{resumeitems}'
                new_exp = exp[0][:block.start()] + new_block + exp[0][block.end():]
                return source[:exp.start()] + new_exp + source[exp.end():]
        return source

    def match_input(self, job_id):
        """Allow-listed snapshot from the actual current PDF, never candidate context."""
        draft = self.get(job_id)
        preview = draft['preview']
        if not preview or not preview['current'] or preview['revision'] != draft['revision']:
            raise ValueError('Build the current saved resume before scoring')
        pdf = safe_child(self.w.root / 'data/output', preview['path'] + '/resume.pdf')
        text = '\n'.join(p.extract_text() or '' for p in PdfReader(str(pdf)).pages)
        jd = self.w.get_job(job_id)['description']
        return {'resume_text': text, 'job_description': jd, 'revision': draft['revision'],
                'source_sha256': hashlib.sha256(draft['source'].encode()).hexdigest(),
                'pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
                'jd_sha256': hashlib.sha256(jd.encode()).hexdigest()}

    def score(self, job_id):
        from backend.assessment import AssessmentService, SCORING_VERSION
        with self.lock:
            payload = self.match_input(job_id)
            with self.w.connect() as db:
                old = db.execute('SELECT details FROM resume_assessments WHERE job_id=? AND draft_revision=? AND jd_hash=? AND pdf_hash=? AND scoring_version=?',
                                 (job_id, payload['revision'], payload['jd_sha256'], payload['pdf_sha256'], SCORING_VERSION)).fetchone()
                if old:
                    return {**json.loads(old[0]), 'cached': True}
            draft = self.get(job_id)
            result = AssessmentService(self.s).run(job_id, payload, draft['source'])
            with self.w.connect() as db:
                # The same revision, PDF and posting scored again under newer scoring rules replaces
                # the older score; a plain INSERT hit the table's key and every re-score after a
                # SCORING_VERSION change failed with a server error (23 Sep).
                db.execute('INSERT OR REPLACE INTO resume_scores VALUES(?,?,?,?,?,?,?)',
                           (job_id, payload['revision'], payload['source_sha256'], payload['pdf_sha256'], payload['jd_sha256'], json.dumps(result), self.s.now()))
                self.w.record_event(db, 'resume_scored', job_id, revision=payload['revision'], ats_readiness=result['ats_readiness']['score'], resume_coverage=result['resume_coverage']['score'])
            self.w.export_tracking()
            return {**result, 'cached': False}

    def assessment(self, job_id):
        draft = self.get(job_id)
        preview = draft.get('preview')
        if not preview or not preview.get('current') or preview.get('revision') != draft['revision']:
            return {'current': False, 'stale': True, 'message': 'Build the current revision to calculate assessments.'}
        result = self.score(job_id)
        return {**result, 'current': True, 'stale': False, 'revision': draft['revision']}

    def download(self, job_id, format):
        if format not in {'pdf', 'tex', 'docx'}:
            raise ValueError('Choose pdf, docx or tex')
        draft = self.get(job_id)
        self._require_resume_evidence(job_id, draft['source'])
        job = self.w.get_job(job_id)
        preview = draft.get('preview')
        if format in ('pdf', 'docx'):
            if not preview or not preview.get('current') or preview.get('revision') != draft['revision']:
                raise ValueError(f'{format.upper()} download is available only for the current successfully compiled revision')
            path = safe_child(self.w.root / 'data/output', preview['path'] + '/resume.pdf')
            if format == 'docx':
                # The Word copy is made from the same checked revision as the PDF beside it.
                from backend import features
                from backend.services.docx_export import cv_document
                if not features.enabled('docx_export', self.s):
                    raise ValueError('Word downloads are switched off on this computer (CAREER_FEATURES).')
                path = path.with_name('resume.docx')
                path.write_bytes(cv_document(draft['source'], title=f"CV for {job['title']} at {job['company']}"))
        else:
            folder = safe_child(self.w.root / 'data/output', draft['file_root'])
            path = folder / 'resume.tex'
            atomic_write(path, draft['source'])
        slug = re.sub(r'[^a-z0-9]+', '-', (job['company'] + '-' + job['title']).casefold()).strip('-')
        return path, f'{slug}-resume-v{draft["revision"]}.{format}'

    def sync_profile(self, job_id, revision):
        """Re-rank the signature and supporting project slots from the registry as a new version."""
        from backend.services.resume_projects import install_project
        with self.lock:
            draft = self.get(job_id)
            if draft['revision'] != revision:
                raise ValueError('This resume changed elsewhere. Reload before syncing')
            if self.s.profile_dirty():
                raise ValueError('Reconcile pending Profile edits before syncing the resume')
            ranked = draft['projects']
            if len(ranked) < 2:
                raise ValueError('Two active registered projects are required')
            # The signature slot follows the one-per-company rule; the supporting slot takes the next best project.
            job = self.w.get_job(job_id)
            signature = self.w.choose_signature_project(job, ranked, [])
            first = next(p for p in ranked if p['id'] == signature)
            supporting = next(p for p in ranked if p['id'] != signature)
            source = draft['source']
            source = install_project(source, first)
            source = install_project(source, supporting, second=True)
            # Registry-derived updates are not new user claims, so bypass capture.
            stamp = self.s.now()
            with self.w.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT revision FROM studio_drafts WHERE job_id=?', (job_id,)).fetchone()
                if current[0] != revision:
                    raise ValueError('This resume changed elsewhere. Reload before syncing')
                db.execute('UPDATE studio_drafts SET source=?,revision=revision+1,profile_revision=?,updated_at=? WHERE job_id=?',
                           (source, self.w.evidence()['candidate_revision'], stamp, job_id))
                db.execute('INSERT INTO studio_versions VALUES(?,?,?,?)', (job_id, revision+1, source, stamp))
                db.execute('INSERT OR REPLACE INTO signature_assignments(company_key, project_id, job_id, assigned_at) VALUES(?,?,?,?)',
                           (company_key(job['company']), signature, job_id, stamp))
                self.w.record_event(db, 'studio_profile_synced', job_id, revision=revision+1,
                                    profile_revision=self.w.evidence()['candidate_revision'], projects=[signature, supporting['id']])
            self.w.export_tracking()
            return self.get(job_id)

    def tailor(self, job_id, team):
        """Tailor this job's Projects and Skills from registered candidate evidence only.

        The job_tailor specialist's plan is validated before anything is stored; only
        Projects/Skills macros are rewritten, every item's origin is kept in resume_items
        for review. Model suggestions never become candidate facts by a keep decision.
        """
        from backend.ai.agents.graph import AgentError
        from backend.countries import require_enabled_markets

        job = self.w.get_job(job_id)
        require_enabled_markets(self.w.root, job.get('market') or None)
        with self.lock:
            draft = self.open(job_id)
            registry = project_registry(self.w.evidence())
            research, role_analysis = '', None
            if job['folder']:
                research_file = self.w.current_folder(job_id) / 'company-research.md'
                if research_file.exists():
                    # Research is saved as UTF-8; the placeholder career.py writes uses the
                    # platform default, so a stray byte must not stop the tailor.
                    research = research_file.read_text(encoding='utf-8', errors='replace')[:8000]
                # The research graph's summaries (graphs/research.py): what a strong application shows
                # for this role, and how the active profile compares. Guidance for ranking only.
                analysis_file = self.w.current_folder(job_id) / 'role-analysis.json'
                try:
                    saved = json.loads(analysis_file.read_text(encoding='utf-8')) if analysis_file.exists() else {}
                except (OSError, ValueError):
                    saved = {}
                if isinstance(saved, dict) and (saved.get('hiring_summary') or saved.get('comparison_summary')):
                    role_analysis = {'hiring_manager_view': str(saved.get('hiring_summary') or '')[:1500],
                                     'profile_comparison': str(saved.get('comparison_summary') or '')[:1500],
                                     'use': 'Ranking guidance only; it is never evidence of what the candidate has done.'}
            never_claim = next((c.get('approved_facts', []) or []
                                for c in self.w.evidence().get('claims', []) if c.get('id') == 'SKILL-NEVER-001'), [])
            # A profile may reserve a lead project for one company; others allow reuse.
            taken = set()
            if self.signature_unique():
                with self.w.connect() as db:
                    taken = {row[0] for row in db.execute('SELECT project_id FROM signature_assignments WHERE company_key<>?',
                                                          (company_key(job['company']),))}
            projects = [{**project, 'can_lead': project['evidence_id'] not in taken
                         and registry[project['evidence_id']].get('signature_eligible') is not False}
                        for project in self._verified_projects(registry)]
            payload = {
                'role': {'title': job['title'], 'company': job['company'],
                         'description': (job['description'] or '')[:12000]},
                'company_research': research,
                'verified_projects': projects,
                'verified_skills': sorted(self._verified_skill_pool().values(), key=lambda skill: skill['name'].casefold()),
                'never_claim': [str(fact) for fact in never_claim],
            }
            if role_analysis:
                payload['role_analysis'] = role_analysis
            from backend import features
            if not features.enabled('evidence_rewrites', self.s):
                payload['rewrites_allowed'] = False  # switched off: registered wording only
            # What the role asks for, checked against the active registry (services/fit.py), so the plan
            # proves the must-haves and never treats a genuine gap as a held skill.
            analysis = None
            try:
                from backend.services import fit
                analysis = fit.for_job(self.s, job_id)
                payload['requirements'] = [{k: item[k] for k in ('text', 'category', 'status', 'evidence_ids')}
                                           for item in analysis['matrix']['requirements'] if item['status'] != 'unknown']
            except Exception:  # noqa: BLE001 - the plan still works from the posting alone
                analysis = None
            try:
                result = team.run('job_tailor', payload)
                try:
                    source, projects, skills, left_out = self._apply_tailoring(draft['source'], result, registry, job, analysis)
                except ValueError as problem:
                    # One corrected plan, told exactly what was wrong with the first.
                    result = team.run('job_tailor', {**payload, 'previous_attempt_problem': str(problem)})
                    source, projects, skills, left_out = self._apply_tailoring(draft['source'], result, registry, job, analysis)
            except (AgentError, ValueError) as error:
                with self.w.connect() as db:
                    self.w.record_event(db, 'studio_tailor_failed', job_id, error=str(error)[:500])
                raise ValueError('The resume could not be tailored for this role, so your saved draft was left unchanged. ' + str(error)) from None
            stamp = self.s.now()
            items = [*projects, *skills]
            with self.w.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revision = db.execute('SELECT revision FROM studio_drafts WHERE job_id=?', (job_id,)).fetchone()[0] + 1
                # Keep legacy suggestions as removed audit records: older untagged
                # previews still need their claim history after this draft is repaired.
                db.execute("UPDATE resume_items SET decision='removed',updated_at=? WHERE job_id=? AND origin='predicted'", (stamp, job_id))
                db.execute("DELETE FROM resume_items WHERE job_id=? AND origin<>'predicted'", (job_id,))
                for item in items:
                    content = json.dumps(item['content'], ensure_ascii=False) if isinstance(item['content'], dict) else item['content']
                    db.execute('INSERT INTO resume_items(id,job_id,section,content,origin,evidence_id,decision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                               (item['row_id'], job_id, item['section'], content, item['origin'], item['evidence_id'], 'pending', stamp, stamp))
                db.execute('UPDATE studio_drafts SET source=?,revision=?,updated_at=? WHERE job_id=?', (source, revision, stamp, job_id))
                db.execute('INSERT INTO studio_versions VALUES(?,?,?,?)', (job_id, revision, source, stamp))
                if projects and projects[0]['origin'] == 'verified':
                    db.execute('INSERT OR REPLACE INTO signature_assignments(company_key, project_id, job_id, assigned_at) VALUES(?,?,?,?)',
                               (company_key(job['company']), projects[0]['evidence_id'], job_id, stamp))
                self.w.record_event(db, 'studio_tailored', job_id, revision=revision,
                                    verified=sum(1 for item in items if item['origin'] == 'verified'),
                                    predicted=sum(1 for item in items if item['origin'] == 'predicted'),
                                    left_out=left_out)
            self.w.export_tracking()
            try:
                fitted = self.fit(job_id, revision)
                try:
                    self.score(job_id)
                except ValueError:
                    pass  # fit() already records and reports a scoring failure
            except ValueError as error:
                # The tailored draft is saved by now; a fitting problem must not read as a failed tailor.
                with self.w.connect() as db:
                    self.w.record_event(db, 'studio_tailor_fit_failed', job_id, revision=revision, error=str(error))
                fitted = self.get(job_id)
                fitted['warnings'] = [*fitted.get('warnings', []),
                                      'Projects and Skills were tailored and saved, but the page could not be fitted automatically: ' + str(error)]
            counts = {origin: sum(1 for item in items if item['origin'] == origin) for origin in ('verified', 'predicted')}
            return {**fitted, 'tailored': True, 'items': counts, 'left_out': left_out,
                    'warnings': [*fitted.get('warnings', []), *left_out]}

    @staticmethod
    def _verified_projects(registry):
        projects = []
        for pid, project in registry.items():
            content = project.get('resume_content')
            if not content or project.get('status') in {'hold', 'missing'}:
                continue
            projects.append({'evidence_id': pid, 'title': content.get('title', ''),
                             'context': content.get('context', ''), 'bullets': content.get('bullets', [])})
        return projects

    def _verified_skill_pool(self):
        """Skill name (case-folded) -> display name and registry claim id."""
        pool = {}
        for claim in self.w.evidence().get('claims', []):
            if claim.get('status') in {'hold', 'missing'} or claim.get('id') == 'SKILL-NEVER-001':
                continue
            if 'skill' not in str(claim.get('category', '')) and not str(claim.get('id', '')).startswith('SKILL'):
                continue
            for fact in claim.get('approved_facts', []) or []:
                pool.setdefault(str(fact).casefold(), {'name': str(fact), 'evidence_id': claim['id']})
        for item in self.s.profile_context():
            if item['kind'] == 'skill' and item['review_state'] == 'registered':
                pool.setdefault(item['title'].casefold(),
                                {'name': item['title'], 'evidence_id': (item.get('details') or {}).get('evidence_id') or item['id']})
        return pool

    def _check_tailored_projects(self, result, registry, job, contract, left_out):
        if contract.required_selected_projects == 0:
            return []
        if not result.projects:
            raise ValueError('The tailoring came back with no projects; the saved draft was left unchanged.')
        projects = []
        for entry in result.projects:
            title, context = entry.title.strip(), entry.context.strip()
            bullets = [bullet.strip() for bullet in entry.bullets if bullet.strip()][:3]
            if not title or not bullets:
                raise ValueError('A tailored project arrived without a title or bullets; the saved draft was left unchanged.')
            if entry.origin == 'verified':
                registered = registry.get(entry.evidence_id) or {}
                content = registered.get('resume_content')
                if not content or registered.get('status') in {'hold', 'missing'}:
                    # Repaired, not fatal: a project the registry does not hold is simply not used.
                    left_out.append("Left out '" + title + "': the plan called it a verified project, but "
                                    + (entry.evidence_id or 'it') + ' is not a registered project.')
                    continue
                registered_text = (content.get('title', ''), content.get('context', ''), [str(b) for b in content.get('bullets', [])])
                if (title, context, bullets) != registered_text:
                    # Verified wording is never the AI's to change: the registered text goes back in.
                    left_out.append("Restored the registered wording of '" + registered_text[0] + "' (the plan had reworded it).")
                    title, context, bullets = registered_text
                projects.append({'row_id': uuid.uuid4().hex, 'section': 'projects',
                                 'content': {'title': title, 'context': context, 'bullets': bullets},
                                 'origin': 'verified', 'evidence_id': entry.evidence_id,
                                 'slot_id': entry.evidence_id, 'evidence_tag': entry.evidence_id})
            else:
                left_out.append("Left out the suggested project '" + title + "': it has no registered candidate evidence.")
        # Repairs rather than failures (23 Sep: one misplaced project failed a whole tailoring run).
        # A verified project listed twice keeps its first place only.
        distinct, seen_ids = [], set()
        for project in projects:
            if project['origin'] == 'verified' and project['evidence_id'] in seen_ids:
                continue
            seen_ids.add(project['evidence_id'])
            distinct.append(project)
        projects = distinct
        if not projects and contract.required_selected_projects:
            raise ValueError('The tailoring came back with no usable projects; the saved draft was left unchanged.')
        if not projects:
            return []
        # A profile may reserve a lead project for one company; others permit reuse.
        taken = set()
        if self.signature_unique():
            with self.w.connect() as db:
                taken = {row[0] for row in db.execute('SELECT project_id FROM signature_assignments WHERE company_key<>?',
                                                      (company_key(job['company']),))}

        def why_not_lead(project):
            if project['origin'] != 'verified':
                return ''
            if registry[project['evidence_id']].get('signature_eligible') is False:
                return 'it is registered as a supporting project only'
            return 'it already leads another company\'s resume' if project['evidence_id'] in taken else ''

        problem = why_not_lead(projects[0])
        if problem:
            lead = next((i for i, p in enumerate(projects) if p['origin'] == 'verified' and not why_not_lead(p)), None)
            if lead is None:
                raise ValueError(projects[0]['evidence_id'] + ' cannot lead Projects because ' + problem
                                 + ', and no other project in the plan can; the saved draft was left unchanged.')
            moved = projects[0]['content']['title']
            projects.insert(0, projects.pop(lead))
            left_out.append("Led with '" + projects[0]['content']['title'] + "' instead of '" + moved + "', because " + problem + '.')
        return projects

    def _check_tailored_skills(self, result, contract, left_out):
        if not result.skills:
            raise ValueError('The tailoring came back with no skills; the saved draft was left unchanged.')
        pool = self._verified_skill_pool()
        skills, seen = [], set()
        # "Python, SQL" in one entry is two skills: split rather than fail the whole plan.
        entries = [(part, entry.origin) for entry in result.skills for part in re.split(r'[,;]', entry.name)]
        for raw_name, origin in entries:
            name = ' '.join(raw_name.split())
            key = name.casefold()
            if not name or key in seen:
                continue
            if origin == 'verified':
                known = pool.get(key)
                if not known:
                    left_out.append("Left out '" + name + "': the plan called it a verified skill, but it is not in the registered profile.")
                    continue
                skills.append({'row_id': uuid.uuid4().hex, 'section': 'skills', 'content': known['name'],
                               'origin': 'verified', 'evidence_id': known['evidence_id']})
                seen.add(key)
            else:
                left_out.append("Left out the suggested skill '" + name + "': it has no registered candidate evidence.")
        if not skills:
            raise ValueError('The tailoring came back with no usable skills; the saved draft was left unchanged.')
        return skills

    def _top_up_skills(self, skills, analysis, left_out):
        """Verified skills that prove a must-have of this role, added when the plan left them out.

        Taken from the job's requirement matrix (services/fit.py): a 'required' item that the
        evidence meets, naming a registered skill the Skills line does not show. At most
        TOP_UP_SKILLS are added, so the page fit does not trim experience for them.
        """
        if not analysis:
            return skills
        from backend.services.fit import named_terms

        pool = self._verified_skill_pool()
        shown = {skill['content'].casefold() for skill in skills}
        added = []
        for item in analysis['matrix']['requirements']:
            if item['category'] != 'required' or item['status'] != 'met':
                continue
            candidates = [entry for entry in pool.values() if entry['evidence_id'] in item['evidence_ids']]
            for name in named_terms([entry['name'] for entry in candidates], item['text'] + ' ' + item['excerpt']):
                if name.casefold() in shown or len(added) >= TOP_UP_SKILLS:
                    continue
                known = pool[name.casefold()]
                shown.add(name.casefold())
                added.append({'row_id': uuid.uuid4().hex, 'section': 'skills', 'content': known['name'],
                              'origin': 'verified', 'evidence_id': known['evidence_id']})
        if added:
            left_out.append('Added ' + ', '.join(s['content'] for s in added)
                            + ' from your registered skills: the role requires ' + ('it' if len(added) == 1 else 'them') + '.')
        return [*skills, *added]

    def _apply_tailoring(self, source, result, registry, job, analysis=None):
        from backend.ai.agents.schemas import TailoringResult
        from backend.resume_contract import contract_for
        result = TailoringResult.model_validate(result)
        contract = contract_for(self.w.root, job.get('market') or None)
        left_out = []
        projects = self._check_tailored_projects(result, registry, job, contract, left_out)
        self._apply_rewrites(projects, result.rewrites, analysis, left_out)
        skills = self._top_up_skills(self._check_tailored_skills(result, contract, left_out), analysis, left_out)
        tailored = self._write_project_slot(source, 'SelectedProject', projects[0]) if projects else source
        if len(projects) > 1 and '% SECOND_PROJECT_BLOCK_START' in tailored:
            tailored = self._write_project_slot(tailored, 'SecondProject', projects[1])
        base_file = self.w.root / 'data/templates/resume-base.tex'
        base = base_file.read_text(encoding='utf-8') if base_file.exists() else None
        tailored = self._write_tailored_skills(tailored, skills, base, self._verified_skill_pool())
        self._require_resume_evidence(job['id'], tailored)
        old, new = extract_zero_argument_macros(source), extract_zero_argument_macros(tailored)
        illegal = {name for name in set(old) | set(new) if old.get(name) != new.get(name) and not tailored_macro(name)}
        if illegal:
            raise ValueError('Tailoring may only change Projects and Skills fields, but it also touched: ' + ', '.join(sorted(illegal)))
        return tailored, projects, skills, left_out

    def _apply_rewrites(self, projects, rewrites, analysis, left_out):
        """Reword a kept project's registered bullets only where services/rewrite_guard.py passes.

        A passing rewording replaces that bullet in this job's draft only (the registry is never
        changed), its macro is tagged with the project and every evidence id it cites, and the
        resume item records the registered line beside it. A failing one keeps the registered
        wording and says why. Experience, education and skills are never reworded.
        """
        from backend import features
        from backend.services import rewrite_guard

        verified = {project['evidence_id']: project for project in projects if project['origin'] == 'verified'}
        if not rewrites or not verified or not features.enabled('evidence_rewrites', self.s):
            return
        evidence = self.w.evidence()
        texts = evidence_wording(evidence)
        skills = [skill['name'] for skill in self._verified_skill_pool().values()]
        never = next((c.get('approved_facts', []) or [] for c in evidence.get('claims', []) if c.get('id') == 'SKILL-NEVER-001'), [])
        filler = [str(f) for f in (self.w.profile().get('resume_contract') or {}).get('prohibited_filler') or []]
        terms = [item['text'] for item in ((analysis or {}).get('matrix') or {}).get('requirements') or []]
        done = {}
        for rewrite in rewrites:
            project = verified.get(rewrite.evidence_id)
            bullets = project['content']['bullets'] if project else []
            if not project or not 1 <= rewrite.bullet <= len(bullets) or done.get(rewrite.evidence_id, 0) >= 2:
                continue
            index, title = rewrite.bullet - 1, project['content']['title']
            if any(r['bullet'] == rewrite.bullet for r in project['content'].get('rewrites', [])):
                continue
            cited = [i for i in dict.fromkeys(map(str, rewrite.cited_ids)) if i in texts and i != rewrite.evidence_id]
            unknown = [i for i in rewrite.cited_ids if i not in texts]
            text = ' '.join(clean_text(rewrite.text).split())
            problems = ([f'it cites {", ".join(map(str, unknown))}, which is not registered evidence'] if unknown else
                        rewrite_guard.check(bullets[index], text, sources=[texts[i] for i in cited], skills=skills,
                                            never=never, posting_terms=terms, filler=filler))
            if problems:
                left_out.append(f"Kept the registered wording of bullet {rewrite.bullet} of '{title}': the rewording failed the "
                                f"evidence check ({problems[0]}).")
                continue
            project['content']['rewrites'] = [*project['content'].get('rewrites', []),
                                              {'bullet': rewrite.bullet, 'registered': bullets[index], 'text': text,
                                               'cited_ids': cited, 'guard': 'passed'}]
            bullets[index] = text
            project.setdefault('bullet_tags', {})[index] = cited
            done[rewrite.evidence_id] = done.get(rewrite.evidence_id, 0) + 1

    @staticmethod
    def _write_project_slot(source, slot, project):
        if project['origin'] != 'verified' or not project['evidence_id']:
            raise ValueError('A project requires registered candidate evidence before it can appear on a resume.')
        marker = 'SECOND_PROJECT' if slot == 'SecondProject' else 'SELECTED_PROJECT'
        content = project['content']
        bullets = (content['bullets'] + ['', '', ''])[:3]
        values = {'ID': project['slot_id'], 'Title': content['title'], 'Context': content['context'],
                  'BulletOne': bullets[0], 'BulletTwo': bullets[1], 'BulletThree': bullets[2]}
        for suffix, value in values.items():
            if macro_span(source, slot + suffix) is None:
                if value:
                    source = _restore_macro(source, slot, suffix, value, project['evidence_tag'])
                continue
            source = write_field(source, slot + suffix, value)
        tag = project['evidence_tag']
        # Evidence comments attached to replaced definitions must refer to what now fills the slot.
        source = re.sub(r'% EVIDENCE: [^\n]+\n(\\newcommand\{\\' + slot + r'[^\n]+)',
                        lambda match: '% EVIDENCE: ' + tag + '\n' + match[1], source)
        # A reworded bullet also names the evidence it cites (the validator checks it again).
        cited = {index: ' '.join([tag, *ids]) for index, ids in (project.get('bullet_tags') or {}).items() if ids}
        for index, tags in cited.items():
            name = slot + ('BulletOne', 'BulletTwo', 'BulletThree')[index]
            source = re.sub(r'% EVIDENCE: [^\n]+\n(\\newcommand\{\\' + name + r'\}[^\n]*)',
                            lambda match, tags=tags: '% EVIDENCE: ' + tags + '\n' + match[1], source)
        block = '% ' + marker + '_BLOCK_START\n\\textbf{\\' + slot + 'Title} \\hfill \\textit{\\' + slot + 'Context}\n\\begin{resumeitems}\n'
        for index, suffix in enumerate(['One', 'Two', 'Three'][:len(content['bullets'])]):
            block += '% EVIDENCE: ' + cited.get(index, tag) + '\n\\item \\' + slot + 'Bullet' + suffix + '\n'
        block += '\\end{resumeitems}\n% ' + marker + '_BLOCK_END'
        return re.sub(r'% ' + marker + r'_BLOCK_START[\s\S]*?% ' + marker + r'_BLOCK_END', lambda _: block, source, count=1)

    @staticmethod
    def _write_tailored_skills(source, items, base=None, verified_pool=None):
        """Replace every skills macro with the tailored list, keeping each macro's category.

        A skill goes to the line the base resume (``base``, resume-base.tex) files it under,
        in tailored order; anything new joins the last skills line so nothing lands in a
        made-up category. The base, not the current draft, decides: on 23 Sep a draft an
        earlier tailoring had thinned (Data Engineering empty) sent every data tool to
        Cloud and Tools on the next run. A macro that gains items has their evidence tags
        rebuilt from the registered claims for the skills it actually displays. Historical
        draft wording never proves that an unregistered skill is held.
        """
        macros = [match[1] for match in re.finditer(r'\\newcommand\{\\([A-Za-z]+)\}', source)
                  if match[1] == 'CoreSkills' or match[1].startswith('Skills')]
        if not macros:
            return source
        reference = extract_zero_argument_macros(base) if base else {}
        wanted, seen = [], set()
        for item in items:
            if item['origin'] != 'verified' or not item['evidence_id']:
                continue
            name = item['content']
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                tag = item['evidence_id']
                wanted.append((key, name, tag))
        approved = {key: (name, tag) for key, name, tag in wanted}
        if verified_pool is not None:
            approved = {key: (entry['name'], entry['evidence_id']) for key, entry in verified_pool.items()}
        per_macro, used = {}, set()
        for macro in macros:
            span = macro_span(source, macro)
            raw = reference.get(macro) or source[span[0]:span[1]]
            separator = '; ' if '; ' in raw else ', '
            original = [item.strip() for item in plain(raw).split(separator)
                        if item.strip() and item.strip().casefold() in approved]
            current = {item.casefold() for item in original}
            kept = [display for key, display, _tag in wanted if key in current]
            used |= {key for key, _display, _tag in wanted if key in current}
            # One model keeps forty skills, another five: a line the tailoring all but emptied
            # keeps its first registered entries (in order), so
            # no heading is ever printed with nothing after it.
            floor = min(len(original), max(SKILL_LINE_FLOOR, len(original) // 2))
            for item in original:
                if len(kept) >= floor:
                    break
                if item.casefold() not in {name.casefold() for name in kept}:
                    kept.append(approved[item.casefold()][0])
            per_macro[macro] = (separator, kept)
        leftover = [(display, tag) for key, display, tag in wanted if key not in used]
        if leftover:
            separator, kept = per_macro[macros[-1]]
            per_macro[macros[-1]] = (separator, kept + [display for display, _tag in leftover])
        for macro, (separator, kept) in per_macro.items():
            source = write_field(source, macro, separator.join(kept))
            tags = list(dict.fromkeys(approved[name.casefold()][1] for name in kept))
            source = _set_evidence_tags(source, macro, tags)
        return source

    @staticmethod
    def _remove_tailored_skill(source, name, tag=None):
        key = name.casefold()
        macros = [match[1] for match in re.finditer(r'\\newcommand\{\\([A-Za-z]+)\}', source)
                  if match[1] == 'CoreSkills' or match[1].startswith('Skills')]
        for macro in macros:
            span = macro_span(source, macro)
            raw = source[span[0]:span[1]]
            separator = '; ' if '; ' in raw else ', '
            items = [item.strip() for item in plain(raw).split(separator) if item.strip()]
            if key not in {item.casefold() for item in items}:
                continue
            source = write_field(source, macro, separator.join([item for item in items if item.casefold() != key]))
        if tag:
            source = re.sub(r'(% EVIDENCE:[^\n]*?) +' + re.escape(tag) + r'\b', r'\1', source)
            source = re.sub(r'^% EVIDENCE:[ \t]*\n', '', source, flags=re.MULTILINE)
        return source

    def _revert_project_slot(self, source, row, job_id):
        """Put a registry project back into the slot the removed item occupied."""
        from backend.services.resume_projects import install_project
        content = json.loads(row['content'])
        macros = {name: plain(value) for name, value in extract_zero_argument_macros(source).items()}
        slot = None
        for candidate in ('SelectedProject', 'SecondProject'):
            if row['evidence_id'] and macros.get(candidate + 'ID') == row['evidence_id']:
                slot = candidate
                break
            if macros.get(candidate + 'Title') == content.get('title'):
                slot = candidate
                break
        if not slot:
            # The slot was already changed by hand; the review stands on its own.
            return source, None
        other = macros.get(('SecondProject' if slot == 'SelectedProject' else 'SelectedProject') + 'ID', '')
        job = self.w.get_job(job_id)
        registry = project_registry(self.w.evidence())
        for project in self.w.rank_projects(job['description']):
            if project['id'] == other:
                continue
            if slot == 'SelectedProject':
                if registry.get(project['id'], {}).get('signature_eligible') is False:
                    continue
                if self.signature_unique():
                    with self.w.connect() as db:
                        owner = db.execute('SELECT company_key FROM signature_assignments WHERE project_id=? AND company_key<>?',
                                           (project['id'], company_key(job['company']))).fetchone()
                    if owner:
                        continue
            return install_project(source, project, slot == 'SecondProject'), (project['id'] if slot == 'SelectedProject' else None)
        raise ValueError('There is no registry project left to restore into this slot; use Sync profile & rank projects to pick one.')

    def items(self, job_id):
        with self.w.connect() as db:
            rows = db.execute('SELECT * FROM resume_items WHERE job_id=? ORDER BY rowid', (job_id,)).fetchall()
        return [{**dict(row), 'content': json.loads(row['content']) if row['section'] == 'projects' else row['content']}
                for row in rows]

    def decide(self, job_id, item_id, decision):
        """Record the review of one tailored item; a removal also repairs the draft and refits the page."""
        if decision not in {'kept', 'removed'}:
            raise ValueError('Choose kept or removed')
        with self.lock:
            draft = self.get(job_id)
            with self.w.connect() as db:
                row = db.execute('SELECT * FROM resume_items WHERE id=? AND job_id=?', (item_id, job_id)).fetchone()
            if not row:
                raise ValueError('That tailored item is not part of this job')
            if decision == 'kept' and row['origin'] != 'verified':
                raise ValueError('Suggested skills and projects need registered candidate evidence before they can be kept. '
                                 'Record the real completed work in Profile, reconcile its evidence, and tailor again.')
            source, signature = draft['source'], None
            if decision == 'removed':
                if row['section'] == 'skills':
                    tag = 'resume_items:' + row['id'] if row['origin'] == 'predicted' else None
                    source = self._remove_tailored_skill(source, row['content'], tag)
                else:
                    source, signature = self._revert_project_slot(source, row, job_id)
            stamp = self.s.now()
            with self.w.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE resume_items SET decision=?,updated_at=? WHERE id=?', (decision, stamp, item_id))
                revision = draft['revision']
                if source != draft['source']:
                    revision += 1
                    db.execute('UPDATE studio_drafts SET source=?,revision=?,updated_at=? WHERE job_id=?', (source, revision, stamp, job_id))
                    db.execute('INSERT INTO studio_versions VALUES(?,?,?,?)', (job_id, revision, source, stamp))
                    if signature:
                        db.execute('INSERT OR REPLACE INTO signature_assignments(company_key, project_id, job_id, assigned_at) VALUES(?,?,?,?)',
                                   (company_key(self.w.get_job(job_id)['company']), signature, job_id, stamp))
                self.w.record_event(db, 'studio_item_reviewed', job_id, item_id=item_id, decision=decision, revision=revision)
            self.w.export_tracking()
            fitted = self.get(job_id)
            if source != draft['source']:
                try:
                    fitted = self.fit(job_id, revision)
                except ValueError as error:
                    # The removal is saved by now; a fitting problem must not read as a failed review.
                    with self.w.connect() as db:
                        self.w.record_event(db, 'studio_tailor_fit_failed', job_id, revision=revision, error=str(error))
                    fitted['warnings'] = [*fitted.get('warnings', []),
                                          'The item was removed and saved, but the page could not be fitted automatically: ' + str(error)]
            return {'items': self.items(job_id), 'draft': fitted}
