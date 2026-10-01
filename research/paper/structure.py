"""Editable manuscript blocks with explicit evidence and mathematical structure."""
from __future__ import annotations

import math
import re


FULL_PAPER_ROLES = {'introduction', 'related_work', 'method', 'experimental_setup', 'results', 'discussion'}
MATH_COMMANDS = set('frac dfrac tfrac sqrt sum prod int iint lim min max arg log ln exp sin cos tan det dim inf sup '
                    'left right big Big bigg Bigg mathbb mathbf mathcal mathrm mathit mathsf boldsymbol operatorname '
                    'text textrm underbrace overbrace overline underline hat widehat bar tilde widetilde vec dot ddot '
                    'alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi '
                    'pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta '
                    'Lambda Xi Pi Sigma Upsilon Phi Psi Omega infty partial nabla ell hbar '
                    'times cdot div pm mp le leq ge geq neq approx sim simeq equiv propto in notin subset subseteq '
                    'supset supseteq cup cap emptyset varnothing forall exists neg land lor to mapsto gets '
                    'rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow '
                    'longrightarrow longleftarrow longleftrightarrow longmapsto Longrightarrow Longleftarrow Longleftrightarrow '
                    'hookrightarrow hookleftarrow uparrow downarrow updownarrow Uparrow Downarrow Updownarrow '
                    'iff implies ldots cdots vdots ddots '
                    'langle rangle lvert rvert lVert rVert vert Vert mid parallel perp '
                    'quad qquad hspace vspace substack underset overset binom dbinom tbinom '
                    'begin end tag label nonumber notag'.split())
MATH_ENVIRONMENTS = {'aligned', 'alignedat', 'gathered', 'split', 'cases', 'matrix', 'pmatrix', 'bmatrix', 'vmatrix', 'Vmatrix'}


def validate_math(expression):
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError('Equation blocks require a nonempty latex expression')
    if any(character in expression for character in ('$', '%', '#')):
        raise ValueError('Equation latex is a math expression without dollar delimiters, comments or parameter macros')
    commands = set(re.findall(r'\\([A-Za-z]+)', expression))
    unknown = commands - MATH_COMMANDS
    if unknown:
        raise ValueError('Unsupported mathematical commands: ' + ', '.join(sorted(unknown)))
    for environment in re.findall(r'\\(?:begin|end)\{([^}]+)\}', expression):
        if environment not in MATH_ENVIRONMENTS:
            raise ValueError('Only mathematical environments are allowed in equation blocks')
    if re.search(r'\\(?:label|tag)\b', expression):
        raise ValueError('Use the equation block label field instead of inline label or tag commands')


def _authored_blocks(section):
    paragraphs = section.get('paragraphs', [])
    blocks = section.get('blocks', [])
    if not isinstance(paragraphs, list) or any(not isinstance(p, str) or not p.strip() for p in paragraphs):
        raise ValueError('Section paragraphs must be nonempty strings')
    if not isinstance(blocks, list):
        raise ValueError('Section blocks must be an array')
    return [{'type': 'paragraph', 'text': paragraph, 'id': 'paragraph-' + str(index)}
            for index, paragraph in enumerate(paragraphs)] + blocks


def _mentions_visual(text, label):
    if not isinstance(text, str) or not isinstance(label, str):
        return False
    if f'[[ref:{label}]]' in text:
        return True
    pattern = r'(?:tab|fig):[A-Za-z0-9_][A-Za-z0-9:_.-]*'
    sequence = pattern + r'(?:(?:\s*,\s*(?:and\s+)?|\s+(?:and|or)\s+)' + pattern + r')*'
    return any(label in {item.rstrip('.:;-') for item in re.findall(pattern, match.group(1))}
               for match in re.finditer(r'\b(?:Figures?|Tables?)\s+(' + sequence + r')', text, re.I))


def section_blocks(section):
    """Place visual blocks immediately after their real argumentative paragraph.

    Ordered blocks remain authored text. ``anchor:{after:paragraph_id}`` is an
    explicit placement decision; otherwise a labeled visual follows its first
    prose reference within its own section. This also fixes legacy paragraph
    arrays that previously pushed every figure and table to the section end.
    """
    blocks = _authored_blocks(section)
    paragraph_ids, paragraphs = {}, []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValueError('Manuscript blocks must be objects')
        if block.get('type') != 'paragraph':
            continue
        identifier = block.get('id')
        if identifier is not None:
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9:_.-]*', identifier) or identifier in paragraph_ids:
                raise ValueError('Paragraph IDs must be unique simple identifiers within a section')
            paragraph_ids[identifier] = index
        paragraphs.append((index, block))
    after, relocated = {}, set()
    for index, block in enumerate(blocks):
        anchor = block.get('anchor')
        if anchor is not None and block.get('type') not in ('table', 'figure'):
            raise ValueError('Only figure and table blocks can declare a paragraph anchor')
        if block.get('type') not in ('table', 'figure'):
            continue
        target = None
        if anchor is not None:
            if not isinstance(anchor, dict) or set(anchor) - {'after', 'reason', 'section_role'} or not isinstance(anchor.get('after'), str) or anchor['after'] not in paragraph_ids:
                raise ValueError('Visual anchor.after must identify an actual paragraph ID in its section')
            if 'section_role' in anchor and anchor['section_role'] != section.get('role'):
                raise ValueError('Visual anchor section_role must match its actual owning section')
            if 'reason' in anchor and (not isinstance(anchor['reason'], str) or not anchor['reason'].strip()):
                raise ValueError('Visual anchor reason must explain its actual argumentative placement')
            target = paragraph_ids[anchor['after']]
        elif block.get('label'):
            if not isinstance(block['label'], str):
                raise ValueError('Block labels must be simple identifiers')
            target = next((pi for pi, paragraph in paragraphs if _mentions_visual(paragraph.get('text'), block['label'])), None)
        if target is not None:
            after.setdefault(target, []).append(block)
            relocated.add(index)
    placed = []
    for index, block in enumerate(blocks):
        if index not in relocated:
            placed.append(block)
        placed.extend(after.get(index, []))
    return placed


def visual_placements(section):
    """Inspectable positions used by rendering, planning and submission review."""
    blocks = section_blocks(section)
    original = _authored_blocks(section)
    report = []
    for index, block in enumerate(blocks):
        if block.get('type') not in ('table', 'figure'):
            continue
        preceding = next((item for item in reversed(blocks[:index]) if item.get('type') == 'paragraph'), None)
        label = block.get('label')
        local_mentions = [i for i, item in enumerate(blocks) if item.get('type') == 'paragraph' and _mentions_visual(item.get('text'), label)]
        original_index = next(i for i, item in enumerate(original) if item is block)
        report.append({'type': block['type'], 'label': label, 'block_index': index,
                       'argumentative_duty': block.get('argumentative_duty'),
                       'authored_block_index': original_index,
                       'after': preceding.get('id') if preceding else None,
                       'anchor_source': 'explicit' if block.get('anchor') else 'first_reference' if local_mentions else 'authored_order',
                       'first_reference_block': min(local_mentions) if local_mentions else None,
                       'has_local_reference': bool(local_mentions)})
    return report


def block_labels(draft):
    """Labels whose numbers are assigned by the actual LaTeX environments."""
    return {block['label']: block['type']
            for section in draft['sections'] + draft.get('appendices', [])
            for block in section_blocks(section)
            if block.get('label') and block['type'] in ('equation', 'table', 'figure')}


def normalize_crossreferences(text, labels):
    """Support recorded plain label references without inventing any numbering."""
    label_pattern = r'(?:eq|tab|fig):[A-Za-z0-9_][A-Za-z0-9:_.-]*'
    def replace(match):
        name, value = match.groups()
        kind = name.rstrip('s').lower()
        def reference(item):
            value = item.group()
            label = value.rstrip('.:;-')
            punctuation = value[len(label):]
            if labels.get(label) != kind:
                raise ValueError('Manuscript cross-reference has no matching ' + kind + ' label: ' + label)
            return '[[ref:' + label + ']]' + punctuation
        return name + ' ' + re.sub(label_pattern, reference, value)
    sequence = label_pattern + r'(?:(?:\s*,\s*(?:and\s+)?|\s+(?:and|or)\s+)' + label_pattern + r')*'
    return re.sub(r'\b(Equations?|Tables?|Figures?)\s+(' + sequence + r')', replace, text)


def validate_structure(draft, evidence):
    sections = draft.get('sections')
    if not isinstance(sections, list) or not sections:
        raise ValueError('A real draft must contain manuscript sections')
    appendices = draft.get('appendices', [])
    if not isinstance(appendices, list):
        raise ValueError('Appendices must be an array of sections')
    if draft.get('manuscript_type') == 'full_paper':
        roles = {section.get('role') for section in sections if isinstance(section, dict)}
        missing = FULL_PAPER_ROLES - roles
        if missing:
            raise ValueError('Full papers require section roles: ' + ', '.join(sorted(missing)))
        if not evidence.get('sources'):
            raise ValueError('A full paper requires actual literature evidence')
    texts, figures, labels = [], set(), set()
    available_figures = {figure['id'] for figure in evidence.get('figures', [])}
    for section in sections + appendices:
        if not isinstance(section, dict) or not isinstance(section.get('title'), str) or not section['title'].strip():
            raise ValueError('Every section needs a title')
        texts.append(section['title'])
        blocks = section_blocks(section)
        if not blocks:
            raise ValueError('Every section needs paragraph text or structured blocks')
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError('Manuscript blocks must be objects')
            label = block.get('label')
            if label is not None:
                if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9:_.-]*', label) or label in labels:
                    raise ValueError('Block labels must be unique simple identifiers')
                labels.add(label)
            kind = block.get('type')
            if kind in ('table', 'figure'):
                from research.paper.layout import block_layout, normalize_layout
                block_layout(block, normalize_layout(draft.get('layout')))
            if label is not None and kind not in ('equation', 'table', 'figure'):
                raise ValueError('Only equation, table and figure blocks can have numbered labels')
            if kind == 'paragraph':
                if not isinstance(block.get('text'), str) or not block['text'].strip():
                    raise ValueError('Paragraph blocks require text')
                texts.append(block['text'])
            elif kind == 'equation':
                validate_math(block.get('latex'))
                texts.append(block['latex'])
            elif kind == 'table':
                columns, rows = block.get('columns'), block.get('rows')
                if not isinstance(columns, list) or not columns or any(not isinstance(cell, str) or not cell.strip() for cell in columns):
                    raise ValueError('Table columns must be nonempty strings')
                if not isinstance(rows, list) or not rows or any(not isinstance(row, list) or len(row) != len(columns) or any(not isinstance(cell, str) for cell in row) for row in rows):
                    raise ValueError('Table rows must contain string cells matching the columns; bind measured values with metric tokens')
                texts.extend(columns)
                texts.extend(cell for row in rows for cell in row)
                if 'alignment' in block and (not isinstance(block['alignment'], list) or len(block['alignment']) != len(columns) or any(value not in ('left', 'right', 'center') for value in block['alignment'])):
                    raise ValueError('Table alignment must give left, right or center for each column')
                texts.extend(validate_table_presentation(block))
            elif kind == 'figure':
                panels = block.get('panels')
                if panels is not None:
                    if not isinstance(panels, list) or not panels or any(not isinstance(panel, dict) or panel.get('figure_id') not in available_figures or not isinstance(panel.get('caption', ''), str) for panel in panels):
                        raise ValueError('Figure panels require actual supplied figure IDs and text captions')
                    if block.get('figure_id'):
                        raise ValueError('Choose a single figure_id or panels, not both')
                    figures.update(panel['figure_id'] for panel in panels)
                    texts.extend(panel.get('caption', '') for panel in panels)
                elif block.get('figure_id') not in available_figures:
                    raise ValueError('Figure block requires an actual supplied figure_id')
                else:
                    figures.add(block['figure_id'])
                width = block.get('width', .95)
                if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or not 0 < width <= 1:
                    raise ValueError('Figure width must be a finite fraction of the text width in (0, 1]')
            else:
                raise ValueError('Supported block types are paragraph, equation, table and figure')
            if kind in ('table', 'figure'):
                if not isinstance(block.get('caption'), str) or not block['caption'].strip():
                    raise ValueError('Tables and figures require a concrete caption')
                texts.append(block['caption'])
    return texts, figures


def validate_table_presentation(block):
    """Validate grouped headers, source-bound notes and readable table panels."""
    texts = []
    for field, limit in [('column_groups', len(block['columns'])), ('row_groups', len(block['rows']))]:
        groups = block.get(field, [])
        if not isinstance(groups, list):
            raise ValueError('Table ' + field + ' must be an array')
        occupied = set()
        for group in groups:
            if not isinstance(group, dict) or set(group) != {'label', 'start', 'count'} or not isinstance(group['label'], str) or not group['label'].strip():
                raise ValueError('Table groups require label, start and count')
            start, count = group['start'], group['count']
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, count)) or start < 0 or count < 1 or start + count > limit:
                raise ValueError('Table group indices must cover actual columns or rows')
            covered = set(range(start, start + count))
            if covered & occupied:
                raise ValueError('Table groups cannot overlap')
            occupied |= covered
            texts.append(group['label'])
    notes = block.get('notes', [])
    if not isinstance(notes, list) or any(not isinstance(note, str) or not note.strip() for note in notes):
        raise ValueError('Table notes must be nonempty strings')
    texts.extend(notes)
    styles = block.get('cell_styles', {})
    if not isinstance(styles, dict):
        raise ValueError('Table cell_styles must map row:column to bold')
    for key, value in styles.items():
        if not isinstance(key, str) or not re.fullmatch(r'\d+:\d+', key) or value != 'bold':
            raise ValueError('Table cell_styles supports explicit row:column bold entries')
        row, column = map(int, key.split(':'))
        if row >= len(block['rows']) or column >= len(block['columns']):
            raise ValueError('Table cell styles must identify actual cells')
    panels = block.get('column_panels', [])
    if not isinstance(panels, list):
        raise ValueError('Table column_panels must contain column index arrays')
    if panels:
        seen = set()
        for panel in panels:
            if not isinstance(panel, list) or len(panel) < 2 or panel[0] != 0 or any(isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(block['columns']) for index in panel) or panel != sorted(set(panel)):
                raise ValueError('Each table panel must retain its identity column and ordered actual columns')
            if seen & set(panel[1:]):
                raise ValueError('Numeric table columns must appear exactly once across panels')
            seen.update(panel[1:])
        if seen != set(range(1, len(block['columns']))):
            raise ValueError('Table panels cannot drop a numeric column')
    weights = block.get('column_weights')
    if weights is not None and (not isinstance(weights, list) or len(weights) != len(block['columns']) or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in weights)):
        raise ValueError('Table column_weights must assign a finite positive width to every column')
    return texts


def table_column_spec(block):
    """Wrap text and headers at a fixed readable size; right-align numeric cells."""
    kinds = block.get('alignment')
    if kinds is None:
        kinds = ['right' if all(re.fullmatch(r'(?:\[\[metric:[^]]+\]\]|[\d\s.eE+±%()\-−])+', row[index]) for row in block['rows']) else 'left'
                 for index in range(len(block['columns']))]
    weights = block.get('column_weights') or [1.6 if kind == 'left' else 1 for kind in kinds]
    total = sum(weights)
    commands = {'left': 'raggedright', 'right': 'raggedleft', 'center': 'centering'}
    return ''.join('>{\\' + commands[kind] + '\\arraybackslash}p{\\dimexpr ' + f'{weight/total:.6f}' + '\\linewidth-2\\tabcolsep\\relax}'
                   for kind, weight in zip(kinds, weights))


def wrap_equation(expression, columns):
    """Break only explicit top-level relation separators, without changing math."""
    if columns != 'double' or len(expression) < 75 or '\\begin{' in expression:
        return expression
    parts = re.split(r'(,\s*\\qquad\s*|\\quad\s*\\Longrightarrow\s*\\quad)', expression)
    lines = []
    for index in range(0, len(parts), 2):
        prefix = ''
        if index:
            previous = parts[index-1]
            if 'Longrightarrow' in previous:
                prefix = '\\Longrightarrow\\quad '
            elif lines:
                lines[-1] += ','
        text = parts[index]
        equalities, depth = [], 0
        for position, character in enumerate(text):
            if character == '{': depth += 1
            elif character == '}': depth -= 1
            elif character == '=' and depth == 0: equalities.append(position)
        # A chained equality can start a continuation line; its relation remains explicit.
        if len(equalities) > 1 and len(text) > 75:
            position = equalities[1]
            lines.extend([prefix + text[:position].strip(), text[position:].strip()])
        else:
            lines.append(prefix + text.strip())
    if len(lines) == 1:
        return expression
    return '\\begin{aligned}\n' + '\\\\\n'.join('&' + line for line in lines) + '\n\\end{aligned}'


def render_section(section, render, render_math, figure_paths, block_plans=None, columns='single', section_index=0, appendix=False):
    from research.paper.layout import block_identifier, PLACEMENTS
    block_plans = block_plans or {}
    output = ['\\FloatBarrier\n\\section{' + render(section['title']) + '}']
    for index, block in enumerate(section_blocks(section)):
        kind = block['type']
        identifier = block_identifier(section_index, index, appendix)
        plan = block_plans.get(identifier, {'span': 'column', 'placement': 'auto', 'font_pt': 9, 'strategy': 'wrap', 'max_rows': 18, 'panel_columns': 2})
        star = '*' if plan['span'] == 'page' and columns == 'double' else ''
        placement = PLACEMENTS[plan['placement']]
        label = '\\label{' + block['label'] + '}\n' if block.get('label') else ''
        if kind == 'paragraph':
            marker = '% FOREST_PARAGRAPH ' + identifier + (' ' + block['id'] if block.get('id') else '')
            output.append(marker + '\n\\label{forest-anchor:' + identifier + '}\n' + render(block['text']))
        elif kind == 'equation':
            output.append('\\begin{equation}\n' + label + render_math(wrap_equation(block['latex'], columns)) + '\n\\end{equation}')
        elif kind == 'table':
            output.extend(render_table(block, render, plan, identifier, star, placement, label))
        elif kind == 'figure':
            graphics = []
            if block.get('panels'):
                per_row = min(plan['panel_columns'], len(block['panels']))
                width = (1 - .035 * (per_row-1)) / per_row
                for pi, panel in enumerate(block['panels']):
                    graphics.append('\\begin{minipage}[t]{' + f'{width:.4f}' + '\\linewidth}\n\\centering\n\\includegraphics[width=\\linewidth]{' + figure_paths[panel['figure_id']] +
                        '}\n\\par\\small\\textbf{(' + chr(97+pi) + ')} ' + render(panel.get('caption', '')) + '\n\\end{minipage}')
                    graphics.append('\\par\\medskip' if (pi+1) % per_row == 0 else '\\hfill')
            else:
                graphics.append('\\includegraphics[width=' + str(block.get('width', .95)) + '\\linewidth]{' + figure_paths[block['figure_id']] + '}')
            output.append('% FOREST_LAYOUT_BLOCK ' + identifier + '\n\\begin{figure' + star + '}[' + placement + ']\n\\centering\n' + '\n'.join(graphics) +
                          '\n\\caption{' + render(block['caption']) + '}\n' + label + '\\end{figure' + star + '}\n% FOREST_LAYOUT_END')
    return '\n\n'.join(output)


def _table_text(text, render):
    """Direction marks are mathematical symbols even with pdfLaTeX fonts."""
    commands = {'↑': r'\ensuremath{\uparrow}', '↓': r'\ensuremath{\downarrow}', '—': r'\textemdash{}'}
    return ''.join(commands.get(part, render(part)) for part in re.split(r'([↑↓—])', text))


def _table_header(block, indices, render):
    lines = ['\\toprule']
    groups = block.get('column_groups', [])
    if groups:
        grouped, rules, offset = [], [], 0
        while offset < len(indices):
            group = next((group for group in groups if group['start'] <= indices[offset] < group['start'] + group['count']), None)
            if group is None:
                grouped.append('\\multicolumn{1}{l}{}')
                offset += 1
                continue
            stop = offset + 1
            while stop < len(indices) and group['start'] <= indices[stop] < group['start'] + group['count']:
                stop += 1
            grouped.append('\\multicolumn{' + str(stop-offset) + '}{c}{' + _table_text(group['label'], render) + '}')
            rules.append('\\cmidrule(lr){' + str(offset+1) + '-' + str(stop) + '}')
            offset = stop
        lines.extend([' & '.join(grouped) + r' \\', ''.join(rules)])
    lines.extend([' & '.join(_table_text(block['columns'][index], render) for index in indices) + r' \\', '\\midrule'])
    return '\n'.join(lines) + '\n'


def _table_rows(block, indices, start, stop, render):
    rows = []
    groups = block.get('row_groups', [])
    for row_index in range(start, stop):
        group = next((group for group in groups if group['start'] <= row_index < group['start'] + group['count']), None)
        if group and (row_index == group['start'] or row_index == start):
            label = group['label'] + (' (continued)' if row_index > group['start'] else '')
            rows.append('\\addlinespace\n\\multicolumn{' + str(len(indices)) + '}{l}{\\textbf{' + _table_text(label, render) + '}}' + r' \\')
        values = []
        for column in indices:
            value = _table_text(block['rows'][row_index][column], render)
            if block.get('statistics_binding') and column and value:
                value = '\\mbox{' + value + '}'
            if block.get('cell_styles', {}).get(f'{row_index}:{column}') == 'bold':
                value = '\\textbf{\\boldmath ' + value + '}'
            values.append(value)
        rows.append(' & '.join(values) + r' \\')
    return '\n'.join(rows)


def _table_notes(block, render):
    if not block.get('notes'):
        return ''
    return ('\\par\\smallskip\n\\begin{minipage}{\\linewidth}\n\\raggedright\n\\textbf{Notes.} ' +
            '\\par\n'.join(_table_text(note, render) for note in block['notes']) + '\n\\end{minipage}')


def render_table(block, render, plan, identifier, star, placement, label):
    """Repeat real headers and notes across row or column continuations."""
    font = '\\fontsize{' + str(plan['font_pt']) + '}{' + str(round(plan['font_pt'] * 1.2, 2)) + '}\\selectfont\n'
    panels = block.get('column_panels') or [list(range(len(block['columns'])))]
    notes = _table_notes(block, render)
    output = []
    total_chunks = len(panels) * (math.ceil(len(block['rows']) / plan['max_rows']) if plan['strategy'] == 'split' else 1)
    part = 0
    for indices in panels:
        panel_block = {**block, 'columns': [block['columns'][index] for index in indices],
                       'rows': [[row[index] for index in indices] for row in block['rows']]}
        if 'alignment' in block:
            panel_block['alignment'] = [block['alignment'][index] for index in indices]
        if 'column_weights' in block:
            panel_block['column_weights'] = [block['column_weights'][index] for index in indices]
        spec = table_column_spec(panel_block)
        header = _table_header(block, indices, render)
        if plan['strategy'] == 'longtable':
            footnote = ('\\multicolumn{' + str(len(indices)) + '}{p{\\dimexpr\\linewidth-2\\tabcolsep\\relax}}{' +
                        notes.removeprefix('\\par\\smallskip\n') + '}' + r' \\' + '\n') if notes else ''
            continuation = '\\addtocounter{table}{-1}\n' if part else ''
            caption = block['caption'] + (f' (panel {part+1} of {len(panels)})' if len(panels) > 1 else '')
            output.append('{\n' + font + continuation + '\\begin{longtable}{' + spec + '}\n\\caption{' + render(caption) + '}' +
                          (label if part == 0 else '') + '\\\\\n' + header + '\\endfirsthead\n' +
                          '\\multicolumn{' + str(len(indices)) + '}{c}{\\tablename\\ \\thetable{} (continued)}\\\\\n' +
                          header + '\\endhead\n\\bottomrule\n' + footnote + '\\endfoot\n' +
                          _table_rows(block, indices, 0, len(block['rows']), render) + '\n\\end{longtable}\n}')
            part += 1
            continue
        ranges = [(start, min(start + plan['max_rows'], len(block['rows']))) for start in range(0, len(block['rows']), plan['max_rows'])] if plan['strategy'] == 'split' else [(0, len(block['rows']))]
        for start, stop in ranges:
            caption = block['caption'] + (f' (part {part+1} of {total_chunks})' if total_chunks > 1 else '')
            continuation = '\\addtocounter{table}{-1}\n' if part and block.get('statistics_binding') else ''
            output.append('% FOREST_LAYOUT_BLOCK ' + identifier + '\n\\begin{table' + star + '}[' + placement + ']\n\\centering\n' + font +
                          continuation + '\\caption{' + render(caption) + '}\n' + (label if part == 0 else '') +
                          '\\begin{tabular}{' + spec + '}\n' + header + _table_rows(block, indices, start, stop, render) +
                          '\n\\bottomrule\n\\end{tabular}\n' + notes + '\n\\end{table' + star + '}\n% FOREST_LAYOUT_END')
            part += 1
    return output
