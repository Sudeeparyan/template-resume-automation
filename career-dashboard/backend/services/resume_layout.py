"""Rank registered resume content against a JD and measure real page fill.

Nothing here invents wording. It reorders skills, bullets and sections that are
already in the source, chooses the track-specific section order from
profile.yml, and reports how full the rendered page is.
"""
from __future__ import annotations
import re
from pathlib import Path
from PIL import Image
from pypdf import PdfReader
from career import tex_escape
from validate_resume import extract_zero_argument_macros, strip_latex_comments
from backend.resume_contract import load_contract, MARGIN_IN

# Related terms aid ordering only; they never create candidate claims. Keyed by track code.
SIGNALS = {
    'A': ('data engineer', 'analytics engineer', 'etl', 'elt', 'pipeline', 'airflow', 'spark', 'databricks', 'kafka', 'flink', 'sql', 'warehouse', 'lakehouse', 'ingestion', 'streaming', 'clickhouse', 'aws', 'glue'),
    'B': ('machine learning', 'deep learning', 'computer vision', 'pytorch', 'llm', 'generative', 'rag', 'nlp', 'model', 'inference', 'mlops', 'scikit', 'detection', 'tracking', 'langchain'),
    'C': ('software engineer', 'software developer', 'backend', 'oop', 'c++', 'c#', '.net', 'data structures', 'algorithms', 'design patterns', 'api', 'sdlc', 'wpf'),
    'D': ('embedded', 'firmware', 'rtos', 'microcontroller', 'fpga', 'verilog', 'labview', 'teststand', 'v&v', 'verification', 'validation', 'sdet', 'test automation', 'hardware', 'medical device'),
}

CONDITIONAL_PROJECT = re.compile(r'\bUse only with (PROJ-[A-Z0-9-]+)', re.I)
STOP = {'and', 'the', 'with', 'for', 'from', 'that', 'this', 'through', 'within', 'across', 'into', 'using', 'data'}


def conditional_skill_map(registry: dict) -> dict:
    """Map each project to the conditional skill entry that may accompany it.

    Read from each skill's `approved_external_use`, which states the project it
    is cleared for, rather than from a hard-coded list.
    """
    pairs = {}
    for entry in registry.values():
        if entry.get('category') != 'skill' or entry.get('status') != 'conditional':
            continue
        for match in CONDITIONAL_PROJECT.finditer(entry.get('approved_external_use') or ''):
            pairs.setdefault(match[1].upper(), entry['id'])
    return pairs


def terms(text):
    text = text.casefold()
    return {word for word in re.findall(r'[a-z][a-z0-9+#.-]*', text) if len(word) > 2} - STOP


def score(text, job):
    target = job['title'] + ' ' + job['description']
    points = 2 * len(terms(text) & terms(target))
    for values in SIGNALS.values():
        if any(re.search(r'\b' + re.escape(t) + r'\b', target, re.I) for t in values):
            points += sum(3 for t in values if re.search(r'\b' + re.escape(t) + r'\b', text, re.I))
    return points


def track_for(job, profile=None) -> str:
    """Pick the track (A-D) whose signals the JD hits most; ties go to the earlier track."""
    target = (job.get('title', '') + ' ' + job.get('description', '')).casefold()
    tracks = (profile or {}).get('role_tracks') or []
    best, best_hits = 'A', -1
    if tracks:
        for track in tracks:
            hits = sum(1 for signal in track.get('signals', []) if str(signal).casefold() in target)
            # Title hits count double: "Machine Learning Engineer" is a B role even if the JD lists SQL.
            hits += sum(1 for signal in track.get('signals', []) if str(signal).casefold() in job.get('title', '').casefold())
            if hits > best_hits:
                best, best_hits = str(track.get('code', 'A')), hits
        return best
    for code, values in SIGNALS.items():
        hits = sum(1 for t in values if t in target)
        if hits > best_hits:
            best, best_hits = code, hits
    return best


def ranked_source(source, job, evidence, active, profile=None, contract=None):
    """Preserve user wording; reorder registered content toward the JD.

    Returns (source, report). Reorders: skill items inside each category macro,
    the skill category lines, bullets inside each role, and the section order
    for the JD's track. Adds nothing that is not already registered wording.
    """
    registered = {i['id'] for i in active if not i.get('deleted') and i['review_state'] == 'registered'}
    registry = {i['id']: i for i in evidence['claims'] + evidence['projects']}
    contract = contract or load_contract()
    changes = []

    def facts(id):
        entry = registry.get(id, {})
        if id not in registered or entry.get('status') in {'hold', 'missing'}:
            return []
        return entry.get('approved_facts', [])

    def bullet(evidence_id, text):
        return '  % EVIDENCE: ' + evidence_id + '\n  \\item ' + tex_escape(text) + '\n'

    # Restore useful registered details that the compact base template omits. These
    # additions are keyed by evidence IDs and therefore become no-ops for profiles
    # that do not carry them; no job-description wording becomes candidate evidence.
    macros = extract_zero_argument_macros(source)
    selected_ids = {macros.get('SelectedProjectID'), macros.get('SecondProjectID')} - {None, ''}
    training = facts('TRAINING-001')
    if len(training) > 1 and training[1] not in source:
        pattern = r'(  % EVIDENCE: TRAINING-002\n  \\item Progressed to Associate Analyst[^\n]+\n)'
        source, count = re.subn(pattern, lambda match: bullet('TRAINING-001', training[1]) + match[0], source, count=1)
        if count:
            changes.append('Added registered BI lifecycle training detail.')
    reconciliation = facts('PROJ-AZ-RECON')
    if reconciliation and 'PROJ-AZ-RECON' not in selected_ids:
        baseline = next((fact for fact in reconciliation if 'six people' in fact), None)
        if baseline and tex_escape(baseline) not in source:
            pattern = r'(  % EVIDENCE: PROJ-AZ-RECON\n  \\item Co-developed a five-source[^\n]+\n)'
            source, count = re.subn(pattern, lambda match: match[0] + bullet('PROJ-AZ-RECON', baseline), source, count=1)
            if count:
                changes.append('Added the registered reconciliation-process baseline.')

    # Conditional project skills may appear only when their supporting registered
    # project is selected, and are labelled as candidate-project evidence.
    source = re.sub(r'% STUDIO_PROJECT_SKILLS\n% EVIDENCE:[^\n]+\n[^\n]+\n', '', source)
    skill_pairs = conditional_skill_map(registry)
    project_skill_lines = []
    for selected_id in sorted(selected_ids):
        skill_id = skill_pairs.get(selected_id)
        skill_facts = facts(skill_id) if skill_id else []
        project = registry.get(selected_id, {})
        if skill_facts and project:
            label = (project.get('resume_content') or {}).get('title') or project.get('external_name') or selected_id
            project_skill_lines.append(
                '% STUDIO_PROJECT_SKILLS\n% EVIDENCE: ' + selected_id + ' ' + skill_id
                + '\n\\textbf{Candidate project (' + tex_escape(label) + '):} '
                + tex_escape('; '.join(skill_facts)) + '\\\\[2pt]\n'
            )
    skill_heading = next((r'\section{' + name + '}' for name in ('Skills', 'Technical Skills')
                          if r'\section{' + name + '}' in source), None)
    if project_skill_lines and skill_heading:
        source = source.replace(skill_heading, skill_heading + '\n' + ''.join(project_skill_lines), 1)
        changes.append('Added skills supported by the selected registered projects.')

    # 1. Skill items inside each category macro, most JD-relevant first.
    macros = extract_zero_argument_macros(source)
    for name, value in macros.items():
        if not (name.startswith('Skills') or name == 'CoreSkills'):
            continue
        separator = '; ' if '; ' in value else ', '
        items = [item.strip() for item in value.split(separator) if item.strip()]
        ordered = sorted(items, key=lambda s: -score(s, job))
        if ordered != items:
            old = r'\newcommand{\%s}{%s}' % (name, value)
            source = source.replace(old, r'\newcommand{\%s}{%s}' % (name, separator.join(ordered)), 1)
            changes.append(f'Ranked {name} items against the JD.')

    # 2. Skill category lines (\textbf{Label:} \SkillsX\\[1pt]) in the skills section.
    skills = re.search(r'\\section\{(?:Technical Skills|Skills)\}\n([\s\S]*?)(?=\n\\section\{|\n\\end\{document\})', source)
    if skills:
        lines = re.findall(r'(?m)^% EVIDENCE: [^\n]+\n\\textbf\{[^{}]+:\}[^\n]*\n', skills[1] + '\n')
        body = ''.join(lines)
        if lines and re.sub(r'\s+', '', body) == re.sub(r'\s+', '', skills[1]):
            macros = extract_zero_argument_macros(source)
            def line_text(line):
                used = re.findall(r'\\(Skills[A-Za-z]+|CoreSkills)\b', line)
                return line + ' ' + ' '.join(macros.get(m, '') for m in used)
            ordered = sorted(lines, key=lambda l: -score(line_text(l), job))
            # Keep the trailing line-break bookkeeping consistent: every line but the last gets \\[1pt].
            ordered = [re.sub(r'\\\\\[\d+pt\]\s*$', '', l.rstrip('\n')) + ('\\\\[1pt]\n' if i < len(ordered) - 1 else '\n') for i, l in enumerate(ordered)]
            if ordered != lines:
                source = source[:skills.start(1)] + ''.join(ordered).rstrip('\n') + source[skills.end(1):]
                changes.append('Ranked skill categories against the JD.')

    # 3. Bullets inside each role, preserving the role order (chronology).
    def rank_items(match):
        body = match[1]
        chunks = list(re.finditer(r'(?m)^\s*% EVIDENCE: [^\n]+\n\s*\\item [^\n]+\n', body))
        if not chunks or 'ProjectBullet' in body:
            return match[0]
        if re.sub(r'\s+', '', ''.join(c[0] for c in chunks)) != re.sub(r'\s+', '', body):
            return match[0]
        return '\\begin{resumeitems}\n' + ''.join(c[0].lstrip('\n') for c in sorted(chunks, key=lambda c: -score(c[0], job))) + '\\end{resumeitems}'
    exp = re.search(r'\\section\{Professional Experience\}[\s\S]*?(?=\\section\{|\\end\{document\})', source)
    if exp:
        ordered_exp = re.sub(r'\\begin\{resumeitems\}([\s\S]*?)\\end\{resumeitems\}', rank_items, exp[0])
        if ordered_exp != exp[0]:
            source = source[:exp.start()] + ordered_exp + source[exp.end():]
            changes.append('Ranked bullets inside each role; employment chronology is preserved.')

    # Client blocks remain inside the same employment role while the most relevant
    # client work moves first.
    client_pattern = r'(?m)^% EVIDENCE: [^\n]+\n\\clientheading\{[^\n]+\}\n\\begin\{resumeitems\}[\s\S]*?\\end\{resumeitems\}\n'
    clients = list(re.finditer(client_pattern, source))
    if len(clients) > 1 and all(not source[a.end():b.start()].strip() for a, b in zip(clients, clients[1:])):
        source = source[:clients[0].start()] + '\n'.join(match[0] for match in sorted(clients, key=lambda match: -score(match[0], job))) + source[clients[-1].end():]
        changes.append('Ranked client blocks against the JD within the same role.')

    # 4. Section order for the JD's track (Projects lead on ML/AI and Embedded resumes).
    source = source.replace(r'\newpage', '').replace(r'\clearpage', '')
    sections = re.split(r'(?=\\section\{)', source)
    names = [re.match(r'\\section\{([^}]+)\}', s) for s in sections]
    mapping = {m[1]: part for m, part in zip(names, sections) if m}
    required = list(contract.required_sections)
    if set(mapping) != set(required) or source.count(r'\end{document}') != 1:
        raise ValueError('Fitting needs the standard template sections (' + ', '.join(required) + '). Your custom source is saved unchanged; restore a template version first.')
    for name in mapping:
        mapping[name] = mapping[name].replace(r'\end{document}', '').strip() + '\n\n'
    track = track_for(job, profile)
    orders = ((profile or {}).get('resume_contract') or {}).get('section_order_by_track') or {}
    order = list(orders.get(track) or contract.allowed_section_orders[0])
    source = sections[0] + ''.join(mapping[name] for name in order) + '\\end{document}\n'
    changes.append(f'Track {track}: section order {" > ".join(order)}.')
    return source, {'track': track, 'section_order': order, 'changes': changes, 'method': 'Deterministic JD relevance ordering of registered evidence; not an ATS score.'}


def set_density(source, body_pt=10.0, item_sep=2.0, contract=None):
    """Fixed margins and a readable font between the contract's bounds. No stretch-to-fill."""
    contract = contract or load_contract()
    body_pt = min(max(float(body_pt), contract.min_body_pt), contract.max_body_pt)
    source = re.sub(r'% STUDIO_DENSITY_START[\s\S]*?% STUDIO_DENSITY_END\n?', '', source)
    source = re.sub(r'\\documentclass\[[^\]]*\]\{article\}', r'\\documentclass[' + contract.documentclass_option + r',10pt]{article}', source, count=1)
    source = re.sub(r'itemsep=[\d.]+pt', 'itemsep=' + str(item_sep) + 'pt', source)
    if contract.pages > 1:
        # Keep role/project constructs together when a multi-page contract is
        # allowed to flow naturally across pages.
        source = source.replace('\\usepackage{needspace}\n', '')
        source = source.replace('\\Needspace', '\\StudioNeedspace')
        if '\\StudioNeedspace{6\\baselineskip}' not in source:
            source = source.replace('\\newcommand{\\roleheading}[4]{%', '\\newcommand{\\roleheading}[4]{%\n  \\StudioNeedspace{6\\baselineskip}')
        if '\\newcommand{\\clientheading}[1]{\\textbf' in source and '\\StudioNeedspace{5\\baselineskip}\\textbf' not in source:
            source = source.replace('\\newcommand{\\clientheading}[1]{\\textbf', '\\newcommand{\\clientheading}[1]{\\StudioNeedspace{5\\baselineskip}\\textbf')
        config = (
            '% STUDIO_DENSITY_START\n'
            '\\DeclareFontShape{T1}{lmr}{m}{n}{<->ec-lmr10}{}\n'
            '\\DeclareFontFamily{TS1}{lmr}{}\n'
            '\\DeclareFontShape{TS1}{lmr}{m}{n}{<->ts1-lmr10}{}\n'
            '\\DeclareFontShape{T1}{lmr}{m}{it}{<->ec-lmri10}{}\n'
            '\\newcommand{\\StudioNeedspace}[1]{\\par\\begingroup\\dimen0=#1\\relax\\dimen2=\\pagegoal\\advance\\dimen2 by -\\pagetotal\\ifdim\\dimen0>\\dimen2\\penalty-10000\\fi\\endgroup}\n'
            '\\clubpenalty=10000\n\\widowpenalty=10000\n\\interlinepenalty=10000\n'
            '\\renewcommand{\\normalsize}{\\fontsize{' + str(body_pt) + 'pt}{' + str(round(body_pt * 1.20, 2)) + 'pt}\\selectfont}\n'
            '% STUDIO_DENSITY_END\n'
        )
    else:
        config = (
            '% STUDIO_DENSITY_START\n'
            '\\clubpenalty=10000\n\\widowpenalty=10000\n'
            '\\renewcommand{\\normalsize}{\\fontsize{' + str(body_pt) + 'pt}{' + str(round(body_pt * 1.18, 2)) + 'pt}\\selectfont}\n'
            '% STUDIO_DENSITY_END\n'
        )
    return source.replace(r'\begin{document}', config + '\\begin{document}\n\\normalsize', 1).replace('\\normalsize\n\\normalsize', '\\normalsize')


def validate_density(source, contract=None):
    """Allow exactly the generated readable font block; reject extra overrides."""
    contract = contract or load_contract()
    blocks = re.findall(r'% STUDIO_DENSITY_START[\s\S]*?% STUDIO_DENSITY_END\n?', source)
    errors = []
    point = re.search(r'\\fontsize\{([\d.]+)pt\}', blocks[0]) if len(blocks) == 1 else None
    if not point or not contract.min_body_pt <= float(point[1]) <= contract.max_body_pt:
        errors.append(f'Studio requires one generated density block with {contract.min_body_pt:g}-{contract.max_body_pt:g}pt body text')
    else:
        expected = re.search(r'% STUDIO_DENSITY_START[\s\S]*?% STUDIO_DENSITY_END\n?', set_density(r'\begin{document}', float(point[1]), contract=contract))[0]
        if blocks[0] != expected:
            errors.append('Studio density block differs from the supported readable layout')
    remaining = source.replace(blocks[0], '') if len(blocks) == 1 else source
    return errors, strip_latex_comments(remaining)


TARGET_FILL_PERCENT = 80


def measure_pages(pdf, pages_dir, contract=None):
    """Measure ink extent and internal whitespace in the actual rendered pages."""
    contract = contract or load_contract()
    reader = PdfReader(str(pdf))
    pages = []
    for index, page in enumerate(reader.pages, 1):
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        with Image.open(Path(pages_dir) / f'page-{index:02d}.png') as image:
            image = image.convert('L')
            dark = image.point(lambda p: 255 if p < 190 else 0)
            box = dark.getbbox()
            scale = height / image.height
            if box:
                top, bottom = box[1] * scale, box[3] * scale
                rows = [y for y in range(box[1], box[3]) if dark.crop((box[0], y, box[2], y + 1)).getbbox()]
                max_gap = max((b - a - 1 for a, b in zip(rows, rows[1:])), default=0) * scale
            else:
                top, bottom, max_gap = height, 0, height
        margin = MARGIN_IN * 72
        fill = max(0, min(1, (bottom - top) / (height - 2 * margin)))
        pages.append({'page': index, 'fill_percent': round(fill * 100, 1), 'bottom_blank_mm': round((height - margin - bottom) * 25.4 / 72, 1), 'largest_internal_gap_mm': round(max_gap * 25.4 / 72, 1), 'paper_ok': contract.is_paper(width, height), 'words': len((page.extract_text() or '').split()), 'within_margins': top >= margin - 5 and bottom <= height - margin + 5})
    target_fill = 85 if contract.pages > 1 else TARGET_FILL_PERCENT
    bottom_limit = 40 if contract.pages > 1 else 45
    full = len(pages) == contract.pages and all(p['fill_percent'] >= target_fill and p['bottom_blank_mm'] <= bottom_limit and p['largest_internal_gap_mm'] <= 14 and p['paper_ok'] and p['within_margins'] for p in pages)
    return {'full_pages': full, 'page_count': len(pages), 'target_pages': contract.pages, 'paper': contract.paper, 'pages': pages, 'target_fill_percent': target_fill, 'normal_margins_mm': round(MARGIN_IN * 25.4, 1)}
