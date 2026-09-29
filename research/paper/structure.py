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


def section_blocks(section):
    """Legacy paragraph arrays remain valid; blocks preserve arbitrary order."""
    paragraphs = section.get('paragraphs', [])
    blocks = section.get('blocks', [])
    if not isinstance(paragraphs, list) or any(not isinstance(p, str) or not p.strip() for p in paragraphs):
        raise ValueError('Section paragraphs must be nonempty strings')
    if not isinstance(blocks, list):
        raise ValueError('Section blocks must be an array')
    return [{'type': 'paragraph', 'text': paragraph} for paragraph in paragraphs] + blocks


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


def table_column_spec(block):
    """Wrap text and headers at a fixed readable size; right-align numeric cells."""
    kinds = block.get('alignment')
    if kinds is None:
        kinds = ['right' if all(re.fullmatch(r'(?:\[\[metric:[^]]+\]\]|[\d\s.eE+±%()\-−])+', row[index]) for row in block['rows']) else 'left'
                 for index in range(len(block['columns']))]
    weights = [1.6 if kind == 'left' else 1 for kind in kinds]
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
    output = [('\\FloatBarrier\n' if section.get('role') == 'discussion' else '') + '\\section{' + render(section['title']) + '}']
    for index, block in enumerate(section_blocks(section)):
        kind = block['type']
        identifier = block_identifier(section_index, index, appendix)
        plan = block_plans.get(identifier, {'span': 'column', 'placement': 'auto', 'font_pt': 9, 'strategy': 'wrap', 'max_rows': 18, 'panel_columns': 2})
        star = '*' if plan['span'] == 'page' and columns == 'double' else ''
        placement = PLACEMENTS[plan['placement']]
        label = '\\label{' + block['label'] + '}\n' if block.get('label') else ''
        if kind == 'paragraph':
            output.append(render(block['text']))
        elif kind == 'equation':
            output.append('\\begin{equation}\n' + label + render_math(wrap_equation(block['latex'], columns)) + '\n\\end{equation}')
        elif kind == 'table':
            font = '\\fontsize{' + str(plan['font_pt']) + '}{' + str(round(plan['font_pt'] * 1.2, 2)) + '}\\selectfont\n'
            header = '\\toprule\n' + ' & '.join(render(cell) for cell in block['columns']) + ' \\\\\n\\midrule\n'
            spec = table_column_spec(block)
            rows = [' & '.join(render(cell) for cell in row) + r' \\' for row in block['rows']]
            if plan['strategy'] == 'longtable':
                output.append('{\n' + font + '\\begin{longtable}{' + spec + '}\n\\caption{' + render(block['caption']) + '}' + label + '\\\\\n' +
                    header + '\\endfirsthead\n\\multicolumn{' + str(len(block['columns'])) + '}{c}{\\tablename\\ \\thetable{} (continued)}\\\\\n' +
                    header + '\\endhead\n\\bottomrule\n\\endfoot\n' + '\n'.join(rows) + '\n\\end{longtable}\n}')
            else:
                chunks = [rows[i:i+plan['max_rows']] for i in range(0, len(rows), plan['max_rows'])] if plan['strategy'] == 'split' else [rows]
                for part, chunk in enumerate(chunks):
                    caption = block['caption'] + (f' (part {part+1} of {len(chunks)})' if len(chunks) > 1 else '')
                    output.append('% FOREST_LAYOUT_BLOCK ' + identifier + '\n\\begin{table' + star + '}[' + placement + ']\n\\centering\n' + font +
                        '\\caption{' + render(caption) + '}\n' + (label if part == 0 else '') + '\\begin{tabular}{' + spec + '}\n' + header +
                        '\n'.join(chunk) + '\n\\bottomrule\n\\end{tabular}\n\\end{table' + star + '}\n% FOREST_LAYOUT_END')
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
