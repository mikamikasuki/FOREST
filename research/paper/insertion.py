"""Insert observed figure assets at a unique author-selected prose anchor."""
from __future__ import annotations

import re
from research.paper.manuscript import tex


def insert_figure(source, *, asset_path, caption, label, anchor_text, span='column'):
    if not isinstance(anchor_text, str) or not anchor_text.strip() or source.count(anchor_text) != 1:
        raise ValueError('Select a unique existing paragraph as the figure anchor')
    if not re.fullmatch(r'fig:[A-Za-z0-9:_-]+', label):
        raise ValueError('Figure label must be a simple fig: identifier')
    if not re.fullmatch(r'figures/[A-Za-z0-9_-]+/figure\.(?:pdf|png|jpg|jpeg)', asset_path):
        raise ValueError('Use the copied local figure asset')
    if '\\label{' + label + '}' in source:
        raise ValueError('This figure label is already in the paper')
    body = source.find(r'\begin{document}')
    end = source.find(r'\end{document}')
    start = source.index(anchor_text)
    if body < 0 or end < 0 or not body < start < start + len(anchor_text) <= end:
        raise ValueError('The figure anchor must be in manuscript prose')
    if span not in ('column', 'page'):
        raise ValueError('Figure span must be column or page')
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError('A substantive figure caption is required')
    environment = 'figure*' if span == 'page' and 'twocolumn' in source[:body] else 'figure'
    anchor_label='forestargument:'+label
    insertion = '\n'+r'\label{'+anchor_label+'}\n\n' + '\n'.join([
        '% FOREST visual anchor '+label+' '+anchor_label,
        'Figure~\\ref{' + label + '} ' + tex(caption) + '\n',
        '\\begin{' + environment + '}[!htbp]', r'\centering',
        r'\includegraphics[width=\linewidth]{' + asset_path + '}',
        r'\caption{' + tex(caption) + '}', r'\label{' + label + '}',
        '\\end{' + environment + '}',
    ]) + '\n\n'
    offset = start + len(anchor_text)
    source = source[:offset] + insertion + source[offset:]
    for package in ('graphicx','flafter','placeins'):
        if not re.search(r'\\usepackage(?:\[[^\]]*\])?\{[^}]*\b'+package+r'\b', source[:source.find(r'\begin{document}')]):
            source = source.replace(r'\begin{document}', '\\usepackage{'+package+'}\n' + r'\begin{document}', 1)
    return source
