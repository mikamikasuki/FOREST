"""Actual local computation, plot, evidence collection and standalone PDF output.

The authored prose exercises the renderer; it is not a model-output claim or a
scientific publication evaluation. No provider or experiment result is faked.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from research.paper.evidence import collect_evidence, manuscript_prompt, validate_draft, write_manuscript
from research.paper.manuscript import check_paper, compile_paper, tex
from research.paper.structure import FULL_PAPER_ROLES, normalize_crossreferences


def actual_materials(tmp_path):
    runs = []
    for intervals in (40, 160):
        directory = tmp_path / str(intervals)
        workspace = directory / 'workspace'
        workspace.mkdir(parents=True)
        script = '''import json, math, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
n=int(sys.argv[1])
x=[(i+.5)*math.pi/n for i in range(n)]
y=[math.sin(v) for v in x]
result=sum(y)*math.pi/n
json.dump({'integral':result,'error':abs(result-2),'intervals':n},open('metrics.json','w'))
json.dump({'intervals':n,'rule':'midpoint'},open('design.json','w'))
plt.plot(x,y); plt.xlabel('x'); plt.ylabel('sin(x)'); plt.tight_layout(); plt.savefig('samples.pdf'); plt.close()
json.dump([{'id':'samples','path':'samples.pdf','caption':'Actual evaluated midpoint samples.'}],open('figures.json','w'))
'''
        (workspace / 'compute.py').write_text(script)
        process = subprocess.run([sys.executable, 'compute.py', str(intervals)], cwd=workspace, capture_output=True, text=True)
        assert process.returncode == 0, process.stderr
        runs.append({'id': 'run-' + str(intervals), 'status': 'completed', 'directory': str(directory), 'metrics_file': 'workspace/metrics.json'})
    sources = [{'id': 'python-sin', 'title': 'Python math.sin documentation', 'data': {
        'reading_scope': 'fulltext', 'passages': [{'text': math.sin.__doc__, 'section': 'math.sin', 'url': 'https://docs.python.org/3/library/math.html#math.sin'}]}}]
    return runs, sources


def complete_draft(evidence):
    roles = ['introduction', 'related_work', 'method', 'experimental_setup', 'results', 'discussion']
    sections = [{'title': role.replace('_', ' ').title(), 'role': role, 'paragraphs': ['The declared scope is numerical integration of the sine function.']} for role in roles]
    sections[1]['paragraphs'] = ['The Python library defines the evaluated sine function. [[passage:python-sin:p0]]']
    sections[2]['paragraphs'] = ['Equation eq:midpoint defines the rule. Table tab:results reports the outputs; Figure [[ref:fig:samples]] shows the evaluated samples.']
    sections[2]['blocks'] = [{'type': 'equation', 'latex': r'Q_n = \frac{\pi}{n}\sum_{i=0}^{n-1}\sin\left(\frac{(i+1/2)\pi}{n}\right)', 'label': 'eq:midpoint'}]
    sections[4]['blocks'] = [
        {'type': 'table', 'columns': ['Run', 'Integral', 'Absolute error'],
         'rows': [['Coarse', '[[metric:m0]]', '[[metric:m1]]'], ['Fine', '[[metric:m3]]', '[[metric:m4]]']],
         'caption': 'Results independently executed at the declared resolutions.', 'label': 'tab:results'},
        {'type': 'figure', 'figure_id': evidence['figures'][1]['id'], 'width': .65,
         'caption': 'Samples read by the fine-resolution computation.', 'label': 'fig:samples'}]
    return {'manuscript_type': 'full_paper', 'title': 'An integration renderer check',
            'abstract': 'The fine-resolution integral is [[metric:m3]].',
            'sections': sections, 'conclusion': 'The two actual computations provide reproducible renderer inputs.',
            'appendices': [{'title': 'Resolutions', 'paragraphs': ['The runs used [[metric:m2]] and [[metric:m5]] intervals.']}],
            'claim_ids': []}


def test_actual_multirun_equations_tables_figures_and_standalone_pdf(tmp_path):
    runs, sources = actual_materials(tmp_path)
    evidence = collect_evidence(runs, sources, required_run_ids=['run-40', 'run-160'])
    assert evidence['runs'][1]['method_context']['workspace/design.json']['intervals'] == 160
    assert evidence['run_scope']['included_run_ids'] == ['run-40', 'run-160']
    assert evidence['sources'][0]['reading_scope'] == 'supplied_passage_excerpts'
    assert evidence['sources'][0]['passages'][0]['locator']['section'] == 'math.sin'
    output = tmp_path / 'paper'
    generated = write_manuscript(output, evidence, complete_draft(evidence))
    assert len(generated['bindings']) == 6
    assert generated['figures'][0]['run_id'] == 'run-160'
    assert generated['evidence_report']['referenced_passages'] == ['python-sin:p0']
    assert set(generated['evidence_report']['section_roles']) == FULL_PAPER_ROLES
    assert check_paper(output)['issues'] == []
    source = (output / 'paper.tex').read_text()
    assert r'\begin{equation}' in source and r'\begin{table}' in source and r'\includegraphics' in source and r'\appendix' in source
    assert r'Equation \ref{eq:midpoint}' in source and r'Table \ref{tab:results}' in source and r'Figure \ref{fig:samples}' in source
    assert generated['evidence_report']['cross_references'] == [
        {'label': 'eq:midpoint', 'type': 'equation'}, {'label': 'fig:samples', 'type': 'figure'}, {'label': 'tab:results', 'type': 'table'}]
    result = compile_paper(output)
    if result['status'] == 'unavailable':
        pytest.skip('A local TeX compiler is required for actual PDF checks')
    assert result['status'] == 'completed', result['log']
    log = (output / 'paper.log').read_text(errors='replace')
    assert 'There were undefined references' not in log
    assert r'\newlabel{eq:midpoint}{{1}' in (output / 'paper.aux').read_text()
    # Recompile after copying only the output bundle and deleting the source runs.
    standalone = tmp_path / 'standalone'
    shutil.copytree(output, standalone)
    for run in runs:
        shutil.rmtree(run['directory'])
    (standalone / 'paper.pdf').unlink()
    assert compile_paper(standalone)['status'] == 'completed'


def test_declared_fulltext_does_not_invent_passages_or_hide_truncation(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir()
    (directory / 'metrics.json').write_text(json.dumps({'actual_sum': sum(range(500))}))
    run = {'id': 'sum', 'status': 'completed', 'directory': str(directory)}
    sources = [{'id': 'empty', 'title': 'No source text supplied', 'reading_scope': 'fulltext'},
               {'id': 'long', 'title': 'Supplied local text', 'reading_scope': 'fulltext',
                'passages': [{'text': 'a' * 3000, 'page': i} for i in range(10)]}]
    evidence = collect_evidence([run], sources)
    assert evidence['sources'][0]['reading_scope'] == 'retrieved_metadata_and_available_abstract'
    supplied = evidence['sources'][1]
    assert sum(len(p['text']) for p in supplied['passages']) == 6000
    assert supplied['passage_coverage']['omitted_passages'] == 6
    assert not supplied['passage_coverage']['complete']
    assert supplied['passages'][0]['locator']['page'] == 0
    assert all(p['trust'] == 'untrusted_source_evidence' for p in supplied['passages'])


def test_explicit_run_scope_cannot_drop_a_requested_run(tmp_path):
    with pytest.raises(ValueError, match='scope must match'):
        collect_evidence([{'id': 'first'}], required_run_ids=['first', 'second'])
    with pytest.raises(ValueError, match='scope must match'):
        collect_evidence([{'id': 'first'}], required_run_ids=[])


def minimal_evidence():
    return {'metrics': [{'id': 'm0', 'value': sum(range(10))}], 'sources': [{'id': 's', 'passages': []}], 'figures': [], 'claims': []}


def test_full_paper_requires_declared_section_coverage():
    draft = {'title': 'Coverage', 'abstract': '[[metric:m0]]', 'conclusion': 'Complete.',
             'manuscript_type': 'full_paper', 'sections': [{'title': 'Result', 'paragraphs': ['Observed result.']}], 'claim_ids': []}
    with pytest.raises(ValueError, match='section roles'):
        validate_draft(draft, minimal_evidence())
    draft['manuscript_type'] = 'research_note'
    assert validate_draft(draft, minimal_evidence())['manuscript_type'] == 'research_note'


@pytest.mark.parametrize('block', [
    {'type': 'equation', 'latex': r'\input{/private/file}'},
    {'type': 'equation', 'latex': r'\begin{document}x\end{document}'},
    {'type': 'figure', 'figure_id': 'missing', 'caption': 'Unavailable figure'},
    {'type': 'table', 'columns': ['Value'], 'rows': [[45]], 'caption': 'Unbound measured value'}])
def test_structured_blocks_reject_unavailable_or_invalid_content(block):
    draft = {'title': 'Structure', 'abstract': '[[metric:m0]]', 'conclusion': 'Observed.',
             'sections': [{'title': 'Method', 'blocks': [block]}], 'claim_ids': []}
    with pytest.raises(ValueError):
        validate_draft(draft, minimal_evidence())


@pytest.mark.parametrize('text', ['Equation eq:missing.', 'Table eq:present.', '[[ref:missing]]'])
def test_crossreferences_reject_missing_or_wrong_type_labels(text):
    draft = {'title': 'References', 'abstract': '[[metric:m0]]', 'conclusion': 'Observed.',
             'sections': [{'title': 'Method', 'paragraphs': [text], 'blocks': [
                 {'type': 'equation', 'latex': 'x=1', 'label': 'eq:present'}]}], 'claim_ids': []}
    with pytest.raises(ValueError, match='cross-reference|unavailable ref'):
        validate_draft(draft, minimal_evidence())


def test_figure_manifest_cannot_import_outside_the_run(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir()
    (directory / 'metrics.json').write_text(json.dumps({'actual_sum': sum(range(10))}))
    (tmp_path / 'outside.pdf').write_bytes(b'%PDF')
    (directory / 'figures.json').write_text(json.dumps([{'id': 'outside', 'path': '../outside.pdf'}]))
    with pytest.raises(ValueError, match='inside its evidence run'):
        collect_evidence([{'id': 'sum', 'status': 'completed', 'directory': str(directory)}])


def test_writer_prompt_preserves_named_numeric_rows_and_nested_identity(tmp_path):
    values = list(range(1, 11))
    rows = [{'dataset': 'integers-one-to-ten', 'method': 'sum', 'value': sum(values), 'condition': {'scale': 'original', 'enabled': True}},
            {'dataset': 'integers-one-to-ten', 'method': 'sum-of-squares', 'value': sum(value*value for value in values), 'condition': {'scale': 'squared', 'enabled': False}}]
    (tmp_path / 'metrics.json').write_text(json.dumps({'full_panel': rows}))
    evidence = collect_evidence([{'id': 'actual-arithmetic', 'status': 'completed', 'directory': str(tmp_path)}])
    prompt = manuscript_prompt(evidence, 'Write up the actual calculations', expected_type='full_paper')
    payload = json.loads(prompt[prompt.index('\n{"goal":'):])
    assert payload['requested_manuscript_type'] == 'full_paper'
    layout = payload['evidence']['runs'][0]['metric_layout']['full_panel']
    assert [row['method'] for row in layout] == ['sum', 'sum-of-squares']
    assert [row['dataset'] for row in layout] == ['integers-one-to-ten'] * 2
    assert layout[0]['condition'] == rows[0]['condition']
    assert layout[1]['condition'] == rows[1]['condition']
    lookup = {metric['id']: metric for metric in payload['evidence']['metrics']}
    for index, row in enumerate(layout):
        key = row['value'].removeprefix('[[metric:').removesuffix(']]')
        assert lookup[key]['pointer'] == f'/full_panel/{index}/value'
        assert lookup[key]['value'] == rows[index]['value']
    assert layout[0]['value'] != layout[1]['value']


def test_full_paper_request_cannot_silently_become_a_research_note():
    draft = {'title': 'Bounded arithmetic note', 'abstract': 'The recorded value is [[metric:m0]].',
             'conclusion': 'The arithmetic calculation completed.', 'manuscript_type': 'research_note',
             'sections': [{'title': 'Result', 'paragraphs': ['Recorded output.']}], 'claim_ids': []}
    assert validate_draft(draft, minimal_evidence(), expected_type='research_note')['manuscript_type'] == 'research_note'
    with pytest.raises(ValueError, match='Requested manuscript_type=full_paper'):
        validate_draft(draft, minimal_evidence(), expected_type='full_paper')
    del draft['manuscript_type']
    with pytest.raises(ValueError, match='Requested manuscript_type=full_paper'):
        validate_draft(draft, minimal_evidence(), expected_type='full_paper')
    draft['manuscript_type'] = 'full_paper'
    with pytest.raises(ValueError, match='section roles'):
        validate_draft(draft, minimal_evidence(), expected_type='full_paper')


def test_unicode_mathematical_prose_compiles_to_actual_pdf(tmp_path):
    prose = 'For a ≠ 0, the shifted a′ and b″ use c² and c³, λ, σ, and Δ. The author’s “quoted” wording remains.'
    encoded = tex(prose)
    assert r'\ensuremath{\ne}' in encoded
    assert r'\ensuremath{^{\prime}}' in encoded
    assert r'\ensuremath{^{2}}' in encoded
    assert not any(symbol in encoded for symbol in '≠′″²³λσΔ’“”')
    (tmp_path / 'paper.tex').write_text('\\documentclass{article}\n\\usepackage{amsmath,amssymb}\n\\begin{document}\n' + encoded + '\n\\end{document}\n')
    compiled = compile_paper(tmp_path)
    if compiled['status'] == 'unavailable':
        pytest.skip('A local TeX compiler is required for actual PDF checks')
    assert compiled['status'] == 'completed', compiled['log']
    assert (tmp_path / 'paper.pdf').read_bytes().startswith(b'%PDF')


@pytest.mark.parametrize('template', ['article', 'iclr2027'])
def test_citations_crossreferences_and_numeric_display_compile(tmp_path, template):
    output = tmp_path / 'paper'
    output.mkdir()
    if template == 'iclr2027':
        assets = os.environ.get('FOREST_TEST_ICLR_ASSET_DIR')
        if not assets:
            pytest.skip('Set FOREST_TEST_ICLR_ASSET_DIR to locally downloaded official assets')
        for filename in ('iclr2027_conference.sty', 'iclr2027_conference.bst'):
            shutil.copy2(Path(assets) / filename, output / filename)
    run = tmp_path / 'arithmetic'
    run.mkdir()
    values = {'ratio': math.pi / 10, 'small': math.pi / 10**9, 'large': math.pi * 10**9}
    (run / 'metrics.json').write_text(json.dumps(values))
    evidence = collect_evidence([{'id': 'arithmetic', 'status': 'completed', 'directory': str(run)}],
        [{'id': 'python', 'title': 'Python sum documentation', 'year': 2026, 'authors': ['Python'],
          'passages': [{'text': sum.__doc__, 'section': 'sum'}]}])
    draft = {'title': 'Rendering check', 'abstract': 'The computed ratio is [[metric:m0]].', 'conclusion': 'The arithmetic is directly recomputable.',
             'claim_ids': [], 'sections': [{'title': 'Evidence', 'paragraphs': [
                 'The author’s “quoted” definition [[source:python]][[passage:python:p0]] is shown in Tables tab:first and tab:second.'], 'blocks': [
                     {'type': 'table', 'label': 'tab:first', 'caption': 'Small computed ratio.', 'columns': ['Ratio'], 'rows': [['[[metric:m1]]']]},
                     {'type': 'table', 'label': 'tab:second', 'caption': 'Large computed ratio.', 'columns': ['Ratio'], 'rows': [['[[metric:m2]]']]}]}]}
    generated = write_manuscript(output, evidence, draft, template=template)
    source = (output / 'paper.tex').read_text()
    assert source.count(r'\citep{Source0}') == 1
    assert r'Tables \ref{tab:first} and \ref{tab:second}' in source
    assert r'\hypersetup{hidelinks}' in source
    assert not any(symbol in source for symbol in '’“”')
    assert generated['evidence_report']['referenced_sources'] == ['python']
    assert generated['evidence_report']['referenced_passages'] == ['python:p0']
    bindings = generated['bindings']
    assert [item['value'] for item in bindings] == list(values.values())
    assert [item['display'] for item in bindings] == ['0.31416', r'\ensuremath{3.1416\times 10^{-9}}', r'\ensuremath{3.1416\times 10^{9}}']
    assert check_paper(output)['issues'] == []
    compiled = compile_paper(output)
    if compiled['status'] == 'unavailable':
        pytest.skip('A local TeX compiler is required for actual PDF checks')
    assert compiled['status'] == 'completed', compiled['log']
    log = (output / 'paper.log').read_text(errors='replace')
    assert 'Missing character:' not in log
    assert 'There were undefined references' not in log
    assert (output / 'paper.pdf').read_bytes().startswith(b'%PDF')


def test_plural_legacy_crossreferences_preserve_punctuation_and_validate_types():
    labels = {'tab:a': 'table', 'tab:b': 'table', 'tab:c': 'table', 'eq:a': 'equation'}
    assert normalize_crossreferences('Tables tab:a, tab:b, and tab:c.', labels) == 'Tables [[ref:tab:a]], [[ref:tab:b]], and [[ref:tab:c]].'
    with pytest.raises(ValueError, match='matching table label'):
        normalize_crossreferences('Tables tab:a and eq:a.', labels)
