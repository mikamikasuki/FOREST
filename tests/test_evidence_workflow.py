"""Real numerical processes, files, rendering, and pure protocol validation.

No provider is substituted and no scientific experiment success is simulated.
The test prose is authored input to the renderer, never a purported model reply.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from research.paper.manuscript import collect_evidence, generate_paper, check_paper, compile_paper
from research.paper.evidence import manuscript_prompt, validate_draft
from research.paper.writing import review_defensive_writing, validate_revision_proposal
from research.validation.protocol import validate_idea, validate_experiment
from research.validation.statistics import paired_csv, compare_result_files
from research.validation.review import CHECKS, validate_statistical_review


def computed_evidence(tmp_path):
    folder = tmp_path / 'calculation'
    folder.mkdir()
    # Execute actual quadrature; save observed error and work, not invented scores.
    script = """import json, math
n=10000
integral=sum(math.sin((i+.5)*math.pi/n) for i in range(n))*math.pi/n
json.dump({'integral':integral,'error':abs(integral-2),'intervals':n},open('metrics.json','w'))
"""
    (folder / 'integrate.py').write_text(script)
    result = subprocess.run([sys.executable, 'integrate.py'], cwd=folder, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return collect_evidence([{'id': 'integration-run', 'status': 'completed', 'directory': str(folder)}])


def authored_draft():
    return {'title': 'Midpoint quadrature of a sine integral',
            'abstract': 'Midpoint integration gives [[metric:m0]] for the sine integral on its positive half period.',
            'sections': [{'title': 'Method and measured result', 'paragraphs': [
                'The executable evaluates equally spaced midpoints. The recorded absolute error is [[metric:m1]] using [[metric:m2]] intervals.']}],
            'conclusion': 'The computed result agrees with the analytic integral within the recorded numerical error.',
            'claim_ids': []}


def test_actual_numeric_artifact_to_editable_latex(tmp_path):
    evidence = computed_evidence(tmp_path)
    out = tmp_path / 'paper'
    generated = generate_paper(None, out, evidence=evidence, draft=authored_draft())
    assert len(generated['bindings']) == 3
    assert check_paper(out)['issues'] == []
    assert 'integration-run' in (out / 'evidence.json').read_text()
    result = compile_paper(out)
    if result['status'] != 'unavailable':
        assert result['status'] == 'completed', result['log']
        assert (out / 'paper.pdf').read_bytes().startswith(b'%PDF')
    metrics_file = out / generated['bindings'][0]['file']
    values = json.loads(metrics_file.read_text())
    values['integral'] += 1
    metrics_file.write_text(json.dumps(values))
    assert 'stale_metric' in {x['code'] for x in check_paper(out)['issues']}


def test_no_case_fallback_or_fabricated_reference(tmp_path):
    with pytest.raises(ValueError, match='requires actual run evidence'):
        generate_paper(tmp_path, tmp_path / 'paper')
    with pytest.raises(ValueError, match='not completed'):
        collect_evidence([{'id': 'not-finished', 'status': 'running', 'directory': str(tmp_path)}])
    evidence = computed_evidence(tmp_path)
    draft = authored_draft()
    draft['abstract'] += ' [[source:nonexistent]]'
    with pytest.raises(ValueError, match='unavailable source'):
        validate_draft(draft, evidence)
    assert 'Never invent' in manuscript_prompt(evidence, 'Check numerical integration')


def test_contrary_evidence_must_be_referenced(tmp_path):
    evidence = computed_evidence(tmp_path)
    evidence['claims'] = [{'id': 'accuracy', 'statement': 'Accuracy depends on resolution', 'scope': 'This integral',
                           'evidence_label': 'MEASURED', 'supporting_evidence': ['m0'], 'contrary_evidence': ['m1']}]
    draft = authored_draft()
    draft['claim_ids'] = ['accuracy']
    draft['sections'][0]['paragraphs'] = ['The recorded integral is [[metric:m0]].']
    with pytest.raises(ValueError, match='omits contrary evidence'):
        validate_draft(draft, evidence)


def test_edits_change_artifacts_without_locking_and_stale_macro_is_detected(tmp_path):
    evidence = computed_evidence(tmp_path)
    out = tmp_path / 'paper'
    generate_paper(None, out, evidence=evidence, draft=authored_draft())
    macro = out / 'results_macros.tex'
    macro.write_text(macro.read_text().replace('10000', '10001'))
    assert 'stale_macro' in {x['code'] for x in check_paper(out)['issues']}
    metric_file = Path(evidence['runs'][0]['directory']) / 'metrics.json'
    metric_file.write_text('{"integral":3}')
    with pytest.raises(ValueError, match='changed during drafting'):
        generate_paper(None, out, evidence=evidence, draft=authored_draft())


def test_defensive_review_changes_only_matched_spans():
    text = 'Measured error decreased. It may potentially improve resolution. More research is needed.'
    review = review_defensive_writing(text)
    assert len(review['edits']) == 2
    assert review['edits'][0]['original'] == 'may potentially'
    assert review['edits'][0]['replacement'] == 'may'
    assert review['edits'][1]['requires_evidence_judgment']
    assert review['edits'][1]['replacement'] is None
    validate_revision_proposal(text, {'edits': [review['edits'][0]]})
    with pytest.raises(ValueError, match='exact existing'):
        validate_revision_proposal(text, {'edits': [{'original': 'Invented old text', 'replacement': 'New'}]})


def test_paired_observed_results_and_actual_recomputation(tmp_path):
    # Directly calculate two quadrature rules against the analytic integral.
    import math
    path = tmp_path / 'errors.csv'
    rows = ['unit,baseline,candidate']
    for n in (20, 30, 50, 80, 120, 200):
        baseline = abs(sum(math.sin(i * math.pi / n) for i in range(n)) * math.pi / n - 2)
        candidate = abs(sum(math.sin((i + .5) * math.pi / n) for i in range(n)) * math.pi / n - 2)
        rows.append(f'{n},{baseline},{candidate}')
    path.write_text('\n'.join(rows))
    first = tmp_path / 'first.json'
    second = tmp_path / 'second.json'
    computed = paired_csv(path, unit_column='unit', baseline_column='baseline', candidate_column='candidate', output=first)
    paired_csv(path, unit_column='unit', baseline_column='baseline', candidate_column='candidate', output=second)
    assert computed['unit_count'] == 6 and computed['improvement'] > 0
    assert computed['ci_low'] > 0
    assert compare_result_files(first, second)['status'] == 'NUMERIC_MATCH'
    changed = json.loads(second.read_text())
    changed['improvement'] += .1
    second.write_text(json.dumps(changed))
    assert compare_result_files(first, second)['status'] == 'NUMERIC_DIFFERENCE'


def idea():
    return {'title': 'Test midpoint quadrature', 'claim': 'Midpoints reduce error',
            'baseline': 'Left endpoints', 'experiment': 'Compare observed errors', 'metric': 'absolute error',
            'experiment_duty': 'mechanism', 'best_estimate': 'Symmetric local errors cancel',
            'probability_range': [.7, .9], 'confidence': 'High', 'evidence_label': 'ESTIMATED',
            'why': 'Taylor expansion', 'against': 'Nonsmooth integrands break the derivation',
            'decisive_unknown': 'Smoothness of the target integrand', 'cheapest_resolution': 'Check the derivative',
            'base_case': {'most_likely_outcome': 'Lower error', 'current_best_estimate': 'Second-order convergence',
                          'pre_experiment_bet': 'Midpoints win for smooth functions',
                          'probability_any_signal': [.7, .9], 'probability_meaningful_improvement': [.6, .8],
                          'probability_publishable_finding': [.01, .05], 'biggest_reason_to_work': 'Symmetry',
                          'biggest_reason_to_fail': 'Non-smoothness', 'first_experiment': 'Evaluate the actual integrand'}}


def test_probability_and_nine_conclusion_contract():
    valid = idea()
    assert validate_idea(valid)['evidence_label'] == 'ESTIMATED'
    valid['evidence_label'] = 'MEASURED'
    with pytest.raises(ValueError, match='ESTIMATED'):
        validate_idea(valid)
    valid = idea()
    del valid['base_case']['first_experiment']
    with pytest.raises(ValueError, match='first_experiment'):
        validate_idea(valid)


def test_confirmation_does_not_relabel_selection_data():
    plan = {'research_question': 'Does the candidate lower error?', 'hypothesis': 'Error decreases',
            'baseline': 'A', 'candidate': 'B', 'metric': 'absolute error', 'statistical_unit': 'Independent problem',
            'split_policy': 'Separate problems', 'selection_policy': 'Development only',
            'decision_rule': 'Exceed effect threshold', 'argumentative_duty': 'effectiveness',
            'meaningful_effect': .01, 'phase': 'confirmatory', 'test_used_for_selection': True}
    with pytest.raises(ValueError, match='not used for selection'):
        validate_experiment(plan)
    plan.update(phase='exploratory')
    assert validate_experiment(plan)['editing_policy'].startswith('Editable')


def test_method_context_excludes_credentials_but_retains_actual_parameters(tmp_path):
    evidence = computed_evidence(tmp_path)
    run = evidence['runs'][0]
    run['config'] = {'command': ['python', 'integrate.py'], 'parameters': {'intervals': 10000},
                     'env': {'PASSWORD': 'private'}, 'provider_snapshot': {'credential_ref': 'private'}}
    collected = collect_evidence([run])
    context = collected['runs'][0]['method_context']
    assert context['parameters']['intervals'] == 10000
    assert 'private' not in json.dumps(context)


def test_statistical_catalog_coverage_is_not_a_fabricated_pass():
    review = {'checks': [{'id': key, 'status': 'not_assessed', 'finding': 'No relevant study material supplied',
                          'evidence': [], 'resolution': question} for key, question in CHECKS.items()],
              'reproducibility_status': 'not_rerun'}
    result = validate_statistical_review(review)
    assert result['catalog_coverage'] == 11
    assert result['assessed_count'] == 0
    review['checks'][0]['status'] = 'supported'
    with pytest.raises(ValueError, match='requires supplied evidence'):
        validate_statistical_review(review)
