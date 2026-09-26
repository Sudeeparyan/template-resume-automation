"""Two evidence-backed project slots, retaining a ranked full project library."""
import re
from career import tex_escape
from validate_resume import extract_zero_argument_macros


def install_project(source, project, second=False):
    prefix = 'SecondProject' if second else 'SelectedProject'
    marker = 'SECOND_PROJECT' if second else 'SELECTED_PROJECT'
    pid = project['id']
    content = project.get('resume_content', project)
    values = {'ID': pid, 'Title': content['title'], 'Context': content['context'],
              **dict(zip(['BulletOne', 'BulletTwo', 'BulletThree'], (content['bullets'] + ['', '', ''])[:3]))}
    macros = extract_zero_argument_macros(source)
    from backend.services.resume_studio import replace_macro
    for suffix, value in values.items():
        name = prefix + suffix
        if name in macros:
            source = replace_macro(source, name, value)
        else:
            source = source.replace(r'\begin{document}', '% EVIDENCE: ' + pid + '\n\\newcommand{\\' + name + '}{' + tex_escape(value) + '}\n' + r'\begin{document}', 1)
    block = '%' + ' ' + marker + '_BLOCK_START\n\\textbf{\\' + prefix + 'Title} \\hfill \\textit{\\' + prefix + 'Context}\n\\begin{resumeitems}\n'
    for suffix in ['One', 'Two', 'Three'][:len(content['bullets'])]:
        block += '% EVIDENCE: ' + pid + '\n\\item \\' + prefix + 'Bullet' + suffix + '\n'
    block += '\\end{resumeitems}\n% ' + marker + '_BLOCK_END'
    pattern = r'% ' + marker + r'_BLOCK_START[\s\S]*?% ' + marker + '_BLOCK_END'
    if re.search(pattern, source):
        source = re.sub(pattern, lambda _: block, source, count=1)
    elif second:
        anchor = '% SELECTED_PROJECT_BLOCK_END'
        if anchor not in source:
            raise ValueError('The standard selected-project block is missing')
        source = source.replace(anchor, anchor + '\n\n' + block, 1)
    else:
        raise ValueError('The standard selected-project block is missing')
    # Evidence comments attached to replaced definitions must refer to their current slot.
    source = re.sub(r'% EVIDENCE: [^\n]+\n(\\newcommand\{\\' + prefix + r'[^\n]+)', lambda m: '% EVIDENCE: ' + pid + '\n' + m[1], source)
    return source
