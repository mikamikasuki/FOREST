"""Scientific invariants for the statistical evidence contract."""
from copy import deepcopy
import math

import numpy as np
import pytest
from scipy import stats

from research.analysis.presentation import attach_statistical_evidence, build_statistical_results
from research.paper.evidence import _numbers, resolve_pointer


def explicit(rows, *, run_id='r', **options):
    return {'id': run_id, 'metrics': {'statistics': {
        'observations': rows, 'metrics': {'score': {'direction': 'higher', 'unit': '%', 'precision': 2}},
        **options}}}


def observations(values, *, dataset='D', method='A', **fields):
    return [{'dataset': dataset, 'method': method, 'metric': 'score', 'value': value,
             'seed': seed, **fields} for seed, value in enumerate(values)]


def bundle(runs):
    result = {'runs': deepcopy(runs), 'metrics': []}
    for run in runs:
        for pointer, value in _numbers(run['metrics']):
            result['metrics'].append({'id': 'm' + str(len(result['metrics'])), 'run_id': run['id'],
                                      'pointer': pointer, 'value': value})
    return result


def test_merge_all_runs_retains_actual_repetitions():
    runs = [explicit(observations([10, 14]), run_id='first'),
            explicit([{**observations([18])[0], 'seed': 2}], run_id='second')]
    result = build_statistical_results(runs)
    row = result['records'][0]
    assert row['estimate'] == 14
    assert row['sd'] == 4
    assert row['n_units'] == row['n_seeds'] == 3
    assert {location['run_id'] for location in row['source_locations']} == {'first', 'second'}
    assert row['se'] is None and row['ci_low'] is None


def test_single_unit_never_has_fake_zero_uncertainty():
    row = build_statistical_results([explicit(observations([12]))])['records'][0]
    assert row['sd'] is row['se'] is row['ci_low'] is row['ci_high'] is None
    assert row['uncertainty']['type'] == 'none'


def test_conflicting_identity_is_rejected_even_with_dedup_policy():
    runs = [explicit(observations([1]), duplicate_policy='merge_identical'),
            explicit(observations([2]), run_id='other', duplicate_policy='merge_identical')]
    with pytest.raises(ValueError, match='Conflicting observation identity'):
        build_statistical_results(runs)


def test_explicit_equal_duplicate_requires_policy_then_preserves_sources():
    runs = [explicit(observations([1])), explicit(observations([1]), run_id='other')]
    with pytest.raises(ValueError, match='Duplicate observation identity'):
        build_statistical_results(runs)
    for run in runs:
        run['metrics']['statistics']['duplicate_policy'] = 'merge_identical'
    result = build_statistical_results(runs)
    assert result['records'][0]['n_units'] == 1
    assert len(result['records'][0]['source_locations']) == 2
    assert result['deduplications'][0]['policy'].startswith('Exact identity')


def test_legacy_exact_reanalysis_is_traceably_deduplicated():
    metric = {'per_seed': [{'dataset': 'D', 'method': 'A', 'seed': 0, 'accuracy': .8},
                           {'dataset': 'D', 'method': 'A', 'seed': 1, 'accuracy': .9}],
              'summary': [{'dataset': 'D', 'method': 'A', 'seeds': 2,
                           'accuracy': .85, 'accuracy_std': math.sqrt(.005)}]}
    result = build_statistical_results([{'id': 'experiment', 'metrics': metric},
                                        {'id': 'reanalysis', 'metrics': deepcopy(metric)}])
    assert len(result['records']) == 1
    assert result['records'][0]['n_units'] == 2
    assert len(result['deduplications']) == 2
    assert len(result['records'][0]['source_locations']) == 4


@pytest.mark.parametrize('kind', ['se', 't_ci', 'bootstrap'])
def test_inference_requires_independence_declaration(kind):
    with pytest.raises(ValueError, match='independent:true'):
        build_statistical_results([explicit(observations([1, 2, 3]), uncertainty={'type': kind})])


def test_t_interval_matches_actual_units_and_t_quantile():
    values = [3, 5, 9, 11]
    result = build_statistical_results([explicit(observations(values),
        uncertainty={'type': 't_ci', 'unit': 'seed', 'independent': True, 'confidence': .95})])
    row = result['records'][0]
    expected_se = np.std(values, ddof=1) / 2
    half = stats.t.ppf(.975, 3) * expected_se
    assert row['estimate'] == 7
    assert row['se'] == pytest.approx(expected_se)
    assert row['ci_low'] == pytest.approx(7 - half)
    assert row['ci_high'] == pytest.approx(7 + half)


def test_bootstrap_is_deterministic_and_bounded_by_actual_values():
    run = explicit(observations([1, 4, 7, 10]), uncertainty={
        'type': 'bootstrap', 'unit': 'seed', 'independent': True, 'seed': 42, 'samples': 500})
    first = build_statistical_results([run])['records'][0]
    second = build_statistical_results([run])['records'][0]
    assert first == second
    assert 1 <= first['ci_low'] <= first['estimate'] <= first['ci_high'] <= 10


def test_repeated_objects_are_reduced_before_uncertainty():
    rows = [{'dataset': 'D', 'method': 'A', 'metric': 'score', 'unit_id': person,
             'seed': seed, 'value': value} for seed in range(5)
            for person, value in [('p1', 1), ('p2', 9)]]
    row = build_statistical_results([explicit(rows, uncertainty={'type': 't_ci', 'unit': 'unit_id',
        'independent': True})])['records'][0]
    assert row['n_units'] == 2
    assert row['n_observations'] == 10 and row['n_seeds'] == 5
    assert row['sampling_unit'] == 'unit_id'
    assert row['se'] == pytest.approx(4)
    assert 'conditional on supplied seeds' in row['uncertainty']['scope']


def test_object_rows_require_an_explicit_sampling_unit():
    rows = [{'dataset': 'D', 'method': 'A', 'metric': 'score', 'unit_id': 'p1', 'value': 4}]
    with pytest.raises(ValueError, match='explicit uncertainty.unit'):
        build_statistical_results([explicit(rows)])


def test_unit_weighting_is_equal_not_accidental_row_weighting():
    rows = [{'dataset': 'D', 'method': 'A', 'metric': 'score', 'unit_id': 'p1', 'seed': 0, 'value': 0},
            {'dataset': 'D', 'method': 'A', 'metric': 'score', 'unit_id': 'p1', 'seed': 1, 'value': 0},
            {'dataset': 'D', 'method': 'A', 'metric': 'score', 'unit_id': 'p2', 'seed': 0, 'value': 9}]
    row = build_statistical_results([explicit(rows, uncertainty={'type': 'sd', 'unit': 'unit_id'})])['records'][0]
    assert row['estimate'] == 4.5
    assert row['n_units'] == 2


def test_declared_grid_reports_missing_seed_and_missing_method():
    run = explicit(observations([1, 2]), design={'datasets': ['D'], 'methods': ['A', 'B'],
        'metrics': ['score'], 'seeds': [0, 1, 2]})
    coverage = build_statistical_results([run])['coverage']
    assert not coverage['complete']
    assert coverage['expected_count'] == 2 and coverage['observed_count'] == 1
    assert any(row['method'] == 'B' and row['level'] == 'record' for row in coverage['missing'])
    assert any(row['method'] == 'A' and row.get('seed') == 2 for row in coverage['missing'])
    assert coverage['unit_grid_verified']


def test_unexpected_units_are_not_hidden():
    run = explicit(observations([1, 2]), design={'seeds': [0]})
    coverage = build_statistical_results([run])['coverage']
    assert not coverage['complete']
    assert coverage['unexpected'][0]['seed'] == 1


def test_different_conditions_do_not_get_pooled():
    rows = observations([1, 3], condition={'noise': .1}) + observations([10, 14], condition={'noise': .5})
    result = build_statistical_results([explicit(rows)])
    assert sorted(row['estimate'] for row in result['records']) == [2, 12]


def test_scientific_configuration_separates_runs():
    one, two = explicit(observations([1]), run_id='one'), explicit(observations([4]), run_id='two')
    one['method_context'] = {'model_config': {'width': 8}}
    two['method_context'] = {'model_config': {'width': 16}}
    result = build_statistical_results([one, two])
    assert len(result['records']) == 2
    assert {row['condition']['model_config']['width'] for row in result['records']} == {8, 16}


def test_curve_points_preserve_axes_and_seed_uncertainty():
    rows = observations([2, 4], x=10) + observations([6, 8], x=20)
    result = build_statistical_results([explicit(rows, axes={'x': {'label': 'Training samples', 'unit': 'samples'}})])
    assert {row['x'] for row in result['records']} == {10, 20}
    assert all(row['x_label'] == 'Training samples' and row['x_unit'] == 'samples' for row in result['records'])


def test_summary_only_keeps_reported_sd_without_inventing_interval():
    result = build_statistical_results([{'id': 'r', 'metrics': {'summary': [
        {'dataset': 'D', 'method': 'A', 'accuracy': .8, 'accuracy_std': .04, 'seeds': 3}]}}])
    row = result['records'][0]
    assert row['estimate'] == .8 and row['sd'] == .04
    assert row['se'] is row['ci_low'] is row['ci_high'] is None
    assert row['uncertainty']['type'] == 'reported_sd'
    assert not result['coverage']['unit_grid_verified']


def test_summary_single_seed_zero_sd_is_not_uncertainty():
    row = build_statistical_results([{'id': 'r', 'metrics': {'summary': [
        {'dataset': 'D', 'method': 'A', 'accuracy': .8, 'accuracy_std': 0, 'seeds': 1}]}}])['records'][0]
    assert row['sd'] is None and row['uncertainty']['type'] == 'none'


def test_different_summary_aggregates_cannot_become_two_seeds():
    runs = [{'id': str(index), 'metrics': {'summary': [
        {'dataset': 'D', 'method': 'A', 'accuracy': value, 'seeds': 3}]}}
        for index, value in enumerate([.8, .9])]
    with pytest.raises(ValueError, match='Conflicting summary identity'):
        build_statistical_results(runs)


def test_same_run_summary_must_agree_with_raw_data():
    metrics = {'per_seed': [{'dataset': 'D', 'method': 'A', 'seed': 0, 'accuracy': .8}],
               'summary': [{'dataset': 'D', 'method': 'A', 'accuracy': .9, 'seeds': 1}]}
    with pytest.raises(ValueError, match='summary disagrees'):
        build_statistical_results([{'id': 'r', 'metrics': metrics}])


def test_raw_data_from_all_runs_validates_each_subset_summary():
    runs = [{'id': str(index), 'metrics': {
        'per_seed': [{'dataset': 'D', 'method': 'A', 'seed': index, 'accuracy': value}],
        'summary': [{'dataset': 'D', 'method': 'A', 'accuracy': value, 'seeds': 1}]}}
        for index, value in enumerate([.8, .9])]
    row = build_statistical_results(runs)['records'][0]
    assert row['estimate'] == pytest.approx(.85) and row['n_units'] == 2
    runs[0]['metrics']['summary'][0]['accuracy'] = .4
    with pytest.raises(ValueError, match='summary disagrees'):
        build_statistical_results(runs)


def test_paired_improvement_uses_metric_direction_and_matching_units():
    rows = observations([3, 7, 8], method='candidate') + observations([1, 4, 6], method='baseline')
    result = build_statistical_results([explicit(rows, uncertainty={'type': 't_ci', 'independent': True},
        comparisons=[{'candidate': 'candidate', 'baseline': 'baseline', 'test': 'paired_t'}])])
    contrast = result['comparisons'][0]
    delta = np.array([2, 3, 2])
    assert contrast['improvement'] == pytest.approx(delta.mean())
    assert contrast['n_pairs'] == 3
    assert contrast['p_value'] == pytest.approx(stats.ttest_1samp(delta, 0).pvalue)
    assert contrast['adjusted_p'] == contrast['p_value']


def test_paired_comparison_refuses_unmatched_units():
    rows = observations([3, 7], method='candidate') + observations([1], method='baseline')
    with pytest.raises(ValueError, match='matching statistical unit'):
        build_statistical_results([explicit(rows, comparisons=[{'candidate': 'candidate', 'baseline': 'baseline'}])])


def test_permutation_p_value_is_null_based_not_percentile_tail():
    rows = observations([2, 5, 7], method='candidate') + observations([1, 3, 4], method='baseline')
    contrast = build_statistical_results([explicit(rows, uncertainty={'type': 'sd', 'independent': True},
        comparisons=[{'candidate': 'candidate', 'baseline': 'baseline', 'test': 'permutation',
                      'exchangeable_signs': True}])])['comparisons'][0]
    assert contrast['p_value'] == .25


def test_holm_family_accounts_for_all_declared_tests():
    rows = observations([6, 8, 10, 12], method='candidate')
    rows += observations([1, 4, 6, 7], method='B')
    rows += observations([5, 7, 9, 10], method='C')
    result = build_statistical_results([explicit(rows, uncertainty={'type': 'sd', 'independent': True},
        comparisons=[{'candidate': 'candidate', 'baseline': baseline, 'test': 'paired_t', 'family': 'main'}
                     for baseline in ['B', 'C']])])
    ordered = sorted(result['comparisons'], key=lambda row: row['p_value'])
    assert ordered[0]['adjusted_p'] == pytest.approx(min(1, 2 * ordered[0]['p_value']))
    assert ordered[1]['adjusted_p'] == pytest.approx(min(1, max(2 * ordered[0]['p_value'], ordered[1]['p_value'])))
    assert all(row['multiplicity']['size'] == 2 for row in ordered)


def test_legacy_comparison_reorients_ci_but_does_not_promote_bootstrap_tail():
    metrics = {'summary': [{'dataset': 'D', 'method': method, 'brier': value, 'seeds': 3}
                           for method, value in [('new', .1), ('old', .2)]],
               'comparisons': [{'dataset': 'D', 'metric': 'brier', 'candidate': 'new', 'baseline': 'old',
                                'difference': -.1, 'ci_low': -.15, 'ci_high': -.05, 'confidence': .95,
                                'unique_test_objects': 100, 'p_bootstrap': .01, 'p_holm': .02}]}
    contrast = build_statistical_results([{'id': 'r', 'metrics': metrics}])['comparisons'][0]
    assert contrast['improvement'] == .1
    assert contrast['ci_low'] == .05 and contrast['ci_high'] == .15
    assert contrast['p_value'] is contrast['adjusted_p'] is None


def test_attach_binds_every_derived_value_to_real_pointer_and_original_source():
    runs = [explicit(observations([2, 4, 8]), uncertainty={'type': 't_ci', 'independent': True})]
    supplied = bundle(runs)
    original_ids = {metric['id'] for metric in supplied['metrics']}
    assert attach_statistical_evidence(supplied) is supplied
    derived = [metric for metric in supplied['metrics'] if metric.get('artifact') == 'statistical_results']
    assert derived
    for metric in derived:
        assert resolve_pointer(supplied['statistics'], metric['pointer']) == metric['value']
        assert metric['source_refs'] and set(metric['source_refs']) <= original_ids
        assert metric['run_id'] in {'r'}
    record = supplied['statistics']['records'][0]
    assert set(('estimate', 'sd', 'se', 'ci_low', 'ci_high', 'n_units', 'n_seeds')) <= set(record['refs'])
    for field, identifier in record['refs'].items():
        metric = next(metric for metric in derived if metric['id'] == identifier)
        assert metric['value'] == record[field]


def test_attach_is_idempotent_and_missing_bindings_fail_atomically():
    supplied = bundle([explicit(observations([1, 2]))])
    attach_statistical_evidence(supplied)
    once = deepcopy(supplied)
    attach_statistical_evidence(supplied)
    assert supplied == once
    broken = bundle([explicit(observations([1, 2]))])
    broken['metrics'] = []
    before = deepcopy(broken)
    with pytest.raises(ValueError, match='unbound original'):
        attach_statistical_evidence(broken)
    assert broken == before


def test_unknown_metrics_keep_existing_workflow():
    supplied = {'runs': [{'id': 'r', 'metrics': {'custom': 4}}], 'metrics': []}
    original = deepcopy(supplied)
    attach_statistical_evidence(supplied)
    assert supplied == original


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True])
def test_invalid_observations_cannot_enter_tables(value):
    with pytest.raises(ValueError, match='finite numeric'):
        build_statistical_results([explicit(observations([value]))])


def test_metric_definition_conflicts_are_rejected():
    one, two = explicit(observations([1]), run_id='one'), explicit(observations([2]), run_id='two')
    two['metrics']['statistics']['metrics']['score']['direction'] = 'lower'
    with pytest.raises(ValueError, match='Conflicting definition'):
        build_statistical_results([one, two])


def test_unknown_direction_is_never_guessed():
    run = explicit(observations([1]))
    run['metrics']['statistics']['metrics'] = {}
    with pytest.raises(ValueError, match='requires direction'):
        build_statistical_results([run])


def publication_analysis(runs):
    return {'id': 'analysis', 'metrics': {'analysis_type': 'publication',
        'statistical_results': build_statistical_results(runs),
        'source_run_ids': [run['id'] for run in runs]}}


def test_computed_analysis_can_be_the_only_paper_evidence():
    raw = explicit(observations([2, 5, 11]), uncertainty={'type': 't_ci', 'independent': True})
    analysis = publication_analysis([raw])
    original = analysis['metrics']['statistical_results']['records'][0]
    computed = build_statistical_results([analysis])['records'][0]
    for field in ['estimate', 'sd', 'se', 'ci_low', 'ci_high', 'confidence', 'n_units', 'n_seeds']:
        assert computed[field] == original[field]
    assert computed['uncertainty'] == original['uncertainty']
    assert all(location['run_id'] == 'analysis' and
               location['pointer'].startswith('/statistical_results/records/0/')
               for location in computed['source_locations'])


def test_computed_and_original_evidence_merge_without_double_counting():
    raw = explicit(observations([2, 5, 11]), uncertainty={'type': 't_ci', 'independent': True})
    analysis = publication_analysis([raw])
    result = build_statistical_results([raw, analysis])
    assert len(result['records']) == 1
    assert result['records'][0]['n_units'] == 3
    assert result['records'][0]['n_observations'] == 3
    assert {location['run_id'] for location in result['records'][0]['source_locations']} == {'r', 'analysis'}
    assert 'counted once' in result['records'][0]['overlap_policy']


def test_computed_conflicting_interval_is_rejected():
    raw = explicit(observations([2, 5, 11]), uncertainty={'type': 't_ci', 'independent': True})
    analysis = publication_analysis([raw])
    analysis['metrics']['statistical_results']['records'][0]['ci_high'] += 1
    with pytest.raises(ValueError, match='disagree on ci_high'):
        build_statistical_results([raw, analysis])


def test_computed_original_sources_are_rebound_to_actual_analysis_artifact():
    raw = explicit(observations([2, 5, 11]), uncertainty={'type': 't_ci', 'independent': True})
    analysis = publication_analysis([raw])
    supplied = bundle([analysis])
    attach_statistical_evidence(supplied)
    original_lookup = {metric['id']: metric for metric in supplied['metrics'] if metric.get('artifact') != 'statistical_results'}
    for ref in supplied['statistics']['records'][0]['source_refs']:
        assert original_lookup[ref]['run_id'] == 'analysis'
        assert original_lookup[ref]['pointer'].startswith('/statistical_results/records/0/')
    for metric in supplied['metrics']:
        if metric.get('artifact') == 'statistical_results':
            assert resolve_pointer(supplied['statistics'], metric['pointer']) == metric['value']


def test_computed_analysis_preserves_missing_grid_status():
    raw = explicit(observations([2, 5]), design={'datasets': ['D'], 'methods': ['A', 'B'], 'seeds': [0, 1, 2]})
    analysis = publication_analysis([raw])
    result = build_statistical_results([analysis])
    assert not result['coverage']['complete']
    assert any(row['method'] == 'B' and row['level'] == 'record' for row in result['coverage']['missing'])
    assert any(row['method'] == 'A' and row.get('seed') == 2 for row in result['coverage']['missing'])
    assert result['coverage']['expected_count'] == 2


def test_computed_paired_contrasts_preserve_actual_ci_and_holm_adjustment():
    rows = observations([3, 7, 8], method='candidate') + observations([1, 4, 6], method='baseline')
    raw = explicit(rows, uncertainty={'type': 't_ci', 'independent': True},
                   comparisons=[{'candidate': 'candidate', 'baseline': 'baseline', 'test': 'paired_t'}])
    analysis = publication_analysis([raw])
    before = analysis['metrics']['statistical_results']['comparisons'][0]
    result = build_statistical_results([analysis])
    contrast = result['comparisons'][0]
    for field in ('improvement', 'ci_low', 'ci_high', 'n_pairs', 'p_value', 'adjusted_p'):
        assert contrast[field] == before[field]
    assert contrast['multiplicity'] == before['multiplicity']
    merged = build_statistical_results([raw, analysis])
    assert len(merged['comparisons']) == 1
    assert merged['comparisons'][0]['n_pairs'] == 3


def test_computed_result_does_not_replace_known_sd_with_new_estimate():
    raw = explicit(observations([2, 5, 11]))
    analysis = publication_analysis([raw])
    analysis['metrics']['statistical_results']['records'][0]['estimate'] = 99
    with pytest.raises(ValueError, match='disagree on estimate'):
        build_statistical_results([raw, analysis])


def test_display_precision_is_explicit_and_counts_are_integers():
    run = explicit(observations([1.234, 2.345]))
    run['metrics']['statistics']['metrics']['score']['precision'] = 0
    supplied = attach_statistical_evidence(bundle([run]))
    record = supplied['statistics']['records'][0]
    metric_index = {metric['id']: metric for metric in supplied['metrics']}
    assert metric_index[record['refs']['estimate']]['precision'] == 0
    assert metric_index[record['refs']['estimate']]['format'] == '.0g'
    assert metric_index[record['refs']['n_units']]['format'] == '.0f'


def test_redundant_summaries_from_multiple_runs_never_create_observations():
    raw = {'id': 'raw', 'metrics': {'per_seed': [
        {'dataset': 'D', 'method': 'A', 'seed': 0, 'accuracy': .8},
        {'dataset': 'D', 'method': 'A', 'seed': 1, 'accuracy': .9}]}}
    summaries = [{'id': f's{index}', 'metrics': {'summary': [
        {'dataset': 'D', 'method': 'A', 'accuracy': .85, 'accuracy_std': math.sqrt(.005), 'seeds': 2}]}}
        for index in range(2)]
    result = build_statistical_results([raw, *summaries])
    assert len(result['records']) == 1
    assert result['records'][0]['n_units'] == 2
    assert {location['run_id'] for location in result['records'][0]['source_locations']} == {'raw', 's0', 's1'}


def test_original_metric_tokens_must_match_actual_run_values():
    supplied = bundle([explicit(observations([1, 2]))])
    source = next(metric for metric in supplied['metrics'] if metric['pointer'].endswith('/value'))
    source['value'] = 100
    before = deepcopy(supplied)
    with pytest.raises(ValueError, match='binding disagrees'):
        attach_statistical_evidence(supplied)
    assert supplied == before
