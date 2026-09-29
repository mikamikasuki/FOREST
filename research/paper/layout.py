"""Editable layout decisions and checks against an actually compiled manuscript."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re


DEFAULT_LAYOUT = {
    'columns': 'single', 'significant_digits': 5, 'scientific_notation': 'auto',
    'table_font_pt': 9, 'min_font_pt': 8, 'max_table_rows': 18,
    'float_placement': 'auto', 'figure_span': 'auto', 'table_span': 'auto',
}
PLACEMENTS = {'auto': 'tbp', 'top': 't', 'bottom': 'b', 'page': 'p', 'here': 'htbp'}
SPANS = {'auto', 'column', 'page'}


def normalize_layout(layout=None, template='article'):
    if layout is not None and not isinstance(layout, dict):
        raise ValueError('Manuscript layout must be an object')
    config = {**DEFAULT_LAYOUT, **(layout or {})}
    unknown = set(config) - set(DEFAULT_LAYOUT)
    if unknown:
        raise ValueError('Unknown manuscript layout fields: ' + ', '.join(sorted(unknown)))
    if config['columns'] not in ('single', 'double'):
        raise ValueError('Layout columns must be single or double')
    if template not in ('article', 'iclr2027'):
        raise ValueError('Layout supports article and iclr2027 templates')
    if template == 'iclr2027' and config['columns'] != 'single':
        raise ValueError('The official ICLR 2027 template is single-column; choose article for a double-column draft')
    if config['scientific_notation'] not in ('auto', 'always', 'never'):
        raise ValueError('scientific_notation must be auto, always or never')
    for key, lower, upper in [('significant_digits', 2, 10), ('max_table_rows', 4, 60)]:
        if isinstance(config[key], bool) or not isinstance(config[key], int) or not lower <= config[key] <= upper:
            raise ValueError(f'{key} must be an integer between {lower} and {upper}')
    for key in ('table_font_pt', 'min_font_pt'):
        if isinstance(config[key], bool) or not isinstance(config[key], (int, float)) or not math.isfinite(config[key]) or not 7 <= config[key] <= 12:
            raise ValueError(key + ' must be a finite point size between 7 and 12')
    if config['table_font_pt'] < config['min_font_pt']:
        raise ValueError('Table font size must meet the declared minimum; tables are never shrunk to fit')
    if config['float_placement'] not in PLACEMENTS or any(config[key] not in SPANS for key in ('figure_span', 'table_span')):
        raise ValueError('Invalid float placement or figure/table span')
    return config


def block_layout(block, config):
    local = block.get('layout', {})
    if not isinstance(local, dict):
        raise ValueError('Block layout must be an object')
    if set(local) - {'span', 'placement', 'strategy', 'max_rows', 'font_pt', 'panel_columns'}:
        raise ValueError('Unknown block layout field')
    result = {'span': config.get(block['type'] + '_span', 'auto'),
              'placement': config['float_placement'], 'strategy': 'auto',
              'font_pt': config['table_font_pt'], 'max_rows': config['max_table_rows'],
              'panel_columns': 2, **local}
    if result['span'] not in SPANS or result['placement'] not in PLACEMENTS:
        raise ValueError('Invalid block span or placement')
    if result['strategy'] not in ('auto', 'wrap', 'split', 'longtable'):
        raise ValueError('Table strategy must be auto, wrap, split or longtable')
    if isinstance(result['font_pt'], bool) or not isinstance(result['font_pt'], (int, float)) or not config['min_font_pt'] <= result['font_pt'] <= 12:
        raise ValueError('Block font size must meet the manuscript minimum and be at most 12 pt')
    for key, lower, upper in [('max_rows', 4, 60), ('panel_columns', 1, 3)]:
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not lower <= result[key] <= upper:
            raise ValueError('Invalid block ' + key)
    return result


def numeric_display(value, config=None):
    """Display precision is separate from the original numeric binding."""
    config = config or DEFAULT_LAYOUT
    if isinstance(value, int):
        return str(value)
    digits = config['significant_digits']
    mode = config['scientific_notation']
    if mode == 'always' and value:
        display = f'{value:.{digits-1}e}'
    else:
        display = f'{value:.{digits}g}'
    if 'e' in display and mode == 'never':
        from decimal import Decimal
        return format(Decimal(display), 'f')
    if 'e' in display:
        mantissa, exponent = display.split('e')
        mantissa = mantissa.rstrip('0').rstrip('.') if '.' in mantissa else mantissa
        return '\\ensuremath{' + mantissa + '\\times 10^{' + str(int(exponent)) + '}}'
    return display


def block_identifier(section_index, block_index, appendix=False):
    return ('appendix' if appendix else 'section') + f'-{section_index}-block-{block_index}'


def plan_layout(draft, evidence, layout=None, template='article'):
    from research.paper.structure import section_blocks, wrap_equation
    config = normalize_layout(layout if layout is not None else draft.get('layout'), template)
    text_width = 5.5 if template == 'iclr2027' else 6.5
    column_width = text_width if config['columns'] == 'single' else (text_width - .25) / 2
    plan = {'version': 1, 'mode': 'structured_render', 'template': template, 'config': config,
            'text_width_in': text_width, 'column_width_in': column_width, 'blocks': [], 'warnings': []}
    figures = {figure['id']: figure for figure in evidence.get('figures', [])}
    for appendix, sections in [(False, draft['sections']), (True, draft.get('appendices', []))]:
        for si, section in enumerate(sections):
            for bi, block in enumerate(section_blocks(section)):
                if block['type'] == 'equation':
                    wrapped = wrap_equation(block['latex'], config['columns']) != block['latex']
                    plan['blocks'].append({'id': block_identifier(si, bi, appendix), 'label': block.get('label'),
                        'type': 'equation', 'span': 'column', 'placement': 'here', 'strategy': 'aligned_relations' if wrapped else 'original_math',
                        'font_pt': 11, 'width_in': column_width, 'reasons': ['Line breaks at existing relation separators; mathematical content preserved'] if wrapped else []})
                    continue
                if block['type'] not in ('table', 'figure'):
                    continue
                settings = block_layout(block, config)
                kind = block['type']
                reasons = []
                span = settings['span']
                rows, columns = len(block.get('rows', [])), len(block.get('columns', []))
                panels = len(block.get('panels', [])) or 1
                if span == 'auto':
                    # Long labels and several numeric comparisons need page width,
                    # not a smaller font at the final physical size.
                    wide = columns > 3 if kind == 'table' else panels > 1 or block.get('width', .95) >= .9
                    span = 'page' if config['columns'] == 'double' and wide else 'column'
                    reasons.append('Automatic page span for a wide comparison' if span == 'page' else 'Fits the document column')
                strategy = settings['strategy']
                if kind == 'table' and strategy == 'auto':
                    strategy = ('longtable' if config['columns'] == 'single' else 'split') if rows > settings['max_rows'] else 'wrap'
                if kind == 'table' and strategy == 'longtable' and config['columns'] == 'double':
                    strategy = 'split'
                    reasons.append('Double-column pages use repeated table panels; longtable cannot span two columns')
                if kind == 'table' and strategy == 'split':
                    panels = math.ceil(rows / settings['max_rows'])
                    reasons.append('All rows retained in repeated panels with headers')
                if kind == 'figure':
                    ids = [panel['figure_id'] for panel in block.get('panels', [])] or [block['figure_id']]
                    panel_count = min(settings['panel_columns'], len(ids))
                    target_width = (text_width if span == 'page' else column_width) * block.get('width', .95) / panel_count
                    for figure_id in ids:
                        meta = figures.get(figure_id, {}).get('render_metadata', {})
                        if meta.get('width_in') and meta.get('minimum_font_pt'):
                            estimated_font = meta['minimum_font_pt'] * target_width / meta['width_in']
                            if estimated_font < config['min_font_pt']:
                                plan['warnings'].append({'code': 'figure_font_below_target', 'message': f'Figure {figure_id} is estimated at {estimated_font:.1f} pt after placement; regenerate at {target_width:.2f} inches or use a wider span.'})
                plan['blocks'].append({'id': block_identifier(si, bi, appendix), 'label': block.get('label'),
                    'type': kind, 'span': span, 'placement': settings['placement'], 'strategy': strategy,
                    'font_pt': settings['font_pt'], 'width_in': text_width if span == 'page' else column_width,
                    'rows': rows, 'columns': columns, 'panels': panels, 'max_rows': settings['max_rows'],
                    'panel_columns': settings['panel_columns'], 'reasons': reasons})
    return plan


def _column_source(source, config, template):
    if template == 'article':
        source = source.replace('% FOREST_COLUMN_SPACING\n\\raggedbottom\n', '')
        source = re.sub(r'% FOREST_FULL_WIDTH_ABSTRACT_BEGIN\n\\twocolumn\[\n\\begin\{@twocolumnfalse\}\n(.*?)\n\\end\{@twocolumnfalse\}\n\]\n% FOREST_FULL_WIDTH_ABSTRACT_END', lambda match: match.group(1), source, flags=re.S)
        options = '11pt,twocolumn' if config['columns'] == 'double' else '11pt'
        source = re.sub(r'\\documentclass(?:\[[^]]*\])?\{article\}', lambda _: '\\documentclass[' + options + ']{article}', source, count=1)
        source = re.sub(r'\\setlength\{\\columnsep\}\{[^}]+\}\s*', '', source)
        if config['columns'] == 'double':
            source = source.replace('\\begin{document}', '\\setlength{\\columnsep}{0.25in}\n% FOREST_COLUMN_SPACING\n\\raggedbottom\n\\begin{document}', 1)
            source = re.sub(r'\\maketitle(.*?\\end\{abstract\})', lambda match:
                '% FOREST_FULL_WIDTH_ABSTRACT_BEGIN\n\\twocolumn[\n\\begin{@twocolumnfalse}\n\\maketitle' + match.group(1) +
                '\n\\end{@twocolumnfalse}\n]\n% FOREST_FULL_WIDTH_ABSTRACT_END', source, count=1, flags=re.S)
    return source


def float_barriers(source):
    """Keep result floats before discussion, references and appendices."""
    source = source.replace('% FOREST_RESULTS_BOUNDARY\n\\clearpage\n\\twocolumn\n', '')
    source = source.replace('% FOREST_RESULTS_BOUNDARY\n\\clearpage\n', '')
    if 'placeins' not in source:
        source = source.replace('\\begin{document}', '\\usepackage{placeins}\n\\begin{document}', 1)
    source = re.sub(r'(\\section\{Discussion\}|\\bibliographystyle\{|\\appendix\b)', lambda match: '\\FloatBarrier\n' + match.group(), source)
    source = re.sub(r'(?:\\FloatBarrier\s*){2,}', lambda _: '\\FloatBarrier\n', source)
    if re.search(r'\\documentclass\[[^]]*twocolumn[^]]*\]\{article\}', source):
        # Drain page-wide result floats and reset both columns together.
        source = source.replace('\\FloatBarrier\n\\section{Discussion}', '% FOREST_RESULTS_BOUNDARY\n\\clearpage\n\\twocolumn\n\\section{Discussion}')
    return source


def _table_parts(body, environment='tabular'):
    """Parse only the renderer's booktabs structure, preserving edited cell text."""
    start = body.find('\\begin{' + environment + '}{')
    if start < 0:
        return None
    spec_start = start + len('\\begin{' + environment + '}')
    depth, stop = 0, None
    for index in range(spec_start, len(body)):
        if body[index] == '{': depth += 1
        elif body[index] == '}':
            depth -= 1
            if depth == 0:
                stop = index + 1; break
    if stop is None:
        return None
    end = body.rfind('\\end{' + environment + '}')
    top, mid = body.find('\\toprule', stop), body.find('\\midrule', stop)
    bottom = body.rfind('\\bottomrule', stop, end)
    if min(end, top, mid, bottom) < 0:
        return None
    data_start = body.find('\\endfoot', stop, end)
    data_start = data_start + len('\\endfoot') if data_start >= 0 else mid + len('\\midrule')
    data_end = end if environment == 'longtable' else bottom
    rows = [row.strip() for row in body[data_start:data_end].split('\\\\') if row.strip()]
    if not rows:
        return None
    return {'spec': body[spec_start:stop], 'header': body[top:mid+len('\\midrule')], 'rows': rows,
            'start': start, 'stop': end+len('\\end{' + environment + '}'), 'caption': body[stop:top].strip()}


def _tabular_text(parts, rows):
    return '\\begin{tabular}' + parts['spec'] + '\n' + parts['header'] + '\n' + '\\\\\n'.join(rows) + '\\\\\n\\bottomrule\n\\end{tabular}'


def apply_layout(directory, layout, template=None):
    """Reflow the current source without regenerating its prose from an old draft."""
    directory = Path(directory)
    source_path = directory / 'paper.tex'
    source = source_path.read_text()
    old = json.loads((directory / 'layout_plan.json').read_text()) if (directory / 'layout_plan.json').exists() else {}
    template_record = json.loads((directory / 'template.json').read_text()) if (directory / 'template.json').exists() else {}
    detected_template = 'iclr2027' if 'iclr2027_conference' in source else 'article'
    template = template or old.get('template') or template_record.get('template') or detected_template
    config = normalize_layout({**old.get('config', {}), **(layout or {})}, template)
    if template_record.get('template', detected_template) != template:
        from research.paper.manuscript import apply_template
        if template == 'iclr2027':
            source_path.write_text(_column_source(source, {**config, 'columns': 'single'}, 'article'))
        apply_template(directory, template)
        source = source_path.read_text()
    source = float_barriers(_column_source(source, config, template))
    changed, warnings = [], []
    # longtable cannot run in a two-column page; convert the recognized current
    # table, including edits, to repeated full-width floats before float reflow.
    def convert_longtable(match):
        body = match.group()
        parts = _table_parts(body, 'longtable')
        if config['columns'] != 'double':
            return body
        if parts is None:
            warnings.append({'code': 'custom_longtable_requires_review', 'message': 'An unrecognized longtable cannot be safely converted to two columns; its current source was retained.'})
            return body
        caption = parts['caption'].rstrip().removesuffix('\\\\').rstrip()
        result = []
        chunks = [parts['rows'][i:i+config['max_table_rows']] for i in range(0,len(parts['rows']),config['max_table_rows'])]
        for index, rows in enumerate(chunks):
            title = caption if index == 0 else re.sub(r'\\label\{[^}]+\}', '', caption)
            result.append('\\begin{table*}[tbp]\n\\centering\n\\small\n' + title + '\n' + _tabular_text(parts, rows) + '\n\\end{table*}')
        return '\n\n'.join(result)
    source = re.sub(r'\\begin\{longtable\}.*?\\end\{longtable\}', convert_longtable, source, flags=re.S)
    def float_block(match):
        kind, starred, placement, body = match.groups()
        if '\\includegraphics' not in body and not ('\\toprule' in body and '\\begin{tabular' in body):
            warnings.append({'code': 'custom_float_preserved', 'message': 'An unrecognized ' + kind + ' was retained without rewriting its content.'})
            return match.group()
        requested = config[kind + '_span']
        span = ('page' if config['columns'] == 'double' else 'column') if requested == 'auto' else requested
        star = '*' if span == 'page' and config['columns'] == 'double' else ''
        place = PLACEMENTS[config['float_placement']]
        if kind == 'table':
            font = config['table_font_pt']
            command = '\\fontsize{' + str(font) + '}{' + str(round(font * 1.2, 2)) + '}\\selectfont'
            if re.search(r'\\fontsize\{[^}]+\}\{[^}]+\}\\selectfont', body):
                body = re.sub(r'\\fontsize\{[^}]+\}\{[^}]+\}\\selectfont', lambda _: command, body)
            else:
                body = re.sub(r'\\(?:small|footnotesize|scriptsize)\b', lambda _: command, body)
        parts = _table_parts(body) if kind == 'table' else None
        chunks = [parts['rows'][i:i+config['max_table_rows']] for i in range(0,len(parts['rows']),config['max_table_rows'])] if parts else []
        if kind == 'table' and parts is None:
            warnings.append({'code': 'custom_table_row_limit_unapplied', 'message': 'This table has no recognized editable row structure; max_table_rows was not applied.'})
        changed.append({'id': 'existing-' + str(len(changed)), 'type': kind, 'span': span,
                        'placement': config['float_placement'], 'strategy': 'preserve_current_source',
                        'font_pt': config['table_font_pt'] if kind == 'table' else None,
                        'rows': len(parts['rows']) if parts else None, 'panels': len(chunks) or 1,
                        'reasons': ['Reflowed current source; prose and cells preserved']})
        bodies = [body]
        if len(chunks) > 1:
            changed[-1]['strategy'] = 'split_current_rows'
            bodies = []
            for index, rows in enumerate(chunks):
                item = body[:parts['start']] + _tabular_text(parts, rows) + body[parts['stop']:]
                if index:
                    item = re.sub(r'\\label\{[^}]+\}', '', item)
                bodies.append(item)
        return '\n\n'.join('\\begin{' + kind + star + '}[' + place + ']' + item + '\\end{' + kind + star + '}' for item in bodies)
    source = re.sub(r'\\begin\{(table|figure)(\*?)\}(?:\[([^]]*)\])?(.*?)\\end\{\1\2\}', float_block, source, flags=re.S)
    if config['columns'] == 'double':
        from research.paper.structure import wrap_equation
        def wrap_existing_equation(match):
            body = match.group(1)
            labels = re.findall(r'\\label\{[^}]+\}', body)
            expression = re.sub(r'\\label\{[^}]+\}', '', body).strip()
            wrapped = wrap_equation(expression, 'double')
            return '\\begin{equation}\n' + '\n'.join(labels) + '\n' + wrapped + '\n\\end{equation}' if wrapped != expression else match.group()
        source = re.sub(r'\\begin\{equation\}(.*?)\\end\{equation\}', wrap_existing_equation, source, flags=re.S)
    source_path.write_text(source)
    binding_path = directory / 'bindings.json'
    if binding_path.exists():
        bindings = json.loads(binding_path.read_text())
        macro_path = directory / 'results_macros.tex'
        macros = macro_path.read_text() if macro_path.exists() else ''
        for binding in bindings:
            before = '\\newcommand{\\' + binding['macro'] + '}{' + binding.get('display', str(binding['value'])) + '}'
            if before not in macros:
                warnings.append({'code': 'edited_numeric_macro_preserved', 'message': 'Numeric macro ' + binding['macro'] + ' differs from its binding and was preserved for review.'})
                continue
            binding['display'] = numeric_display(binding['value'], config)
            macros = macros.replace(before, '\\newcommand{\\' + binding['macro'] + '}{' + binding['display'] + '}')
        macro_path.write_text(macros)
        binding_path.write_text(json.dumps(bindings, indent=2))
    plan = {**old, 'version': 1, 'mode': 'existing_source_reflow', 'template': template, 'config': config,
            'text_width_in': 5.5 if template == 'iclr2027' else 6.5,
            'column_width_in': 3.125 if config['columns'] == 'double' else 5.5 if template == 'iclr2027' else 6.5,
            'blocks': changed, 'warnings': warnings}
    (directory / 'layout_plan.json').write_text(json.dumps(plan, indent=2))
    return plan


def compile_preflight(directory, source_name, completed, log, actual_compilation=True):
    """Compiler and artifact observations, never a scientific endorsement."""
    from research.paper.manuscript import check_paper
    directory = Path(directory)
    source = (directory / source_name).read_text()
    final_log = directory / (Path(source_name).stem + '.log')
    if final_log.exists():
        log = final_log.read_text(errors='replace')
    issues, assets = [], []
    checks = {key: 'not_checked' for key in ('glyphs', 'overflow', 'references', 'assets', 'pdf', 'bindings')}
    for code, pattern, severity in [('missing_glyph', r'Missing character:[^\n]*', 'error'),
                                    ('overflow', r'Overfull \\[hv]box[^\n]*', 'warning'),
                                    ('undefined_reference', r'[^\n]*(?:undefined references|Reference[^\n]*undefined|Citation[^\n]*undefined)[^\n]*', 'error')]:
        for message in dict.fromkeys(re.findall(pattern, log, re.I)):
            issues.append({'code': code, 'severity': severity, 'message': message.strip()})
    if actual_compilation:
        for key, code in [('glyphs', 'missing_glyph'), ('overflow', 'overflow'), ('references', 'undefined_reference')]:
            checks[key] = 'failed' if any(item['code'] == code for item in issues) else 'passed'
    for kind, pattern in [('figure', r'\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}'),
                           ('input', r'\\input\{([^}]+)\}'), ('bibliography', r'\\bibliography\{([^}]+)\}')]:
        for relative in re.findall(pattern, source):
            relative += '.bib' if kind == 'bibliography' and not relative.endswith('.bib') else ''
            path = directory / relative
            exists = path.is_file() and path.resolve().is_relative_to(directory.resolve())
            assets.append({'path': relative, 'exists': exists, 'kind': kind})
            if not exists:
                issues.append({'code': 'missing_asset', 'severity': 'error', 'message': 'Missing local ' + kind + ': ' + relative})
    checks['assets'] = 'passed' if all(item['exists'] for item in assets) else 'failed'
    for item in check_paper(directory, source_name)['issues']:
        issues.append({**item, 'severity': 'error'})
    checks['bindings'] = ('failed' if any(item['code'] in ('stale_metric', 'stale_macro', 'missing_metric') for item in issues) else 'passed') if (directory/'bindings.json').exists() else 'not_checked'
    page_count = None
    pdf = directory / (Path(source_name).stem + '.pdf')
    if completed and pdf.exists():
        try:
            from pypdf import PdfReader
            page_count = len(PdfReader(pdf).pages)
            checks['pdf'] = 'passed'
        except Exception as exc:
            issues.append({'code': 'unreadable_pdf', 'severity': 'error', 'message': str(exc)})
            checks['pdf'] = 'failed'
    elif actual_compilation:
        checks['pdf'] = 'failed'
        issues.append({'code': 'compilation_failed', 'severity': 'error', 'message': 'Compilation did not produce a current PDF.'})
    plan_path = directory / 'layout_plan.json'
    if plan_path.exists():
        for item in json.loads(plan_path.read_text()).get('warnings', []):
            issues.append({**item, 'severity': 'warning'})
    status = 'unavailable' if not actual_compilation else 'failed' if any(item['severity'] == 'error' for item in issues) else 'needs_review' if issues else 'passed'
    report = {'status': status, 'actual_compilation': actual_compilation, 'page_count': page_count,
              'issues': issues, 'assets': assets, 'checks': checks,
              'scope': 'Actual compiler output, readable PDF and local assets/bindings; visual reading and scientific validation remain separate.'}
    (directory / 'layout_preflight.json').write_text(json.dumps(report, indent=2))
    return report
