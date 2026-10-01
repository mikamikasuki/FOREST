"""Reliability statistics retain repetitions, empty bins and paper dimensions."""
import json

import numpy as np
import pytest
from pypdf import PdfReader
from scipy import stats

from research.figures.calibration import _build_calibration, render_calibration


def predictions(datasets=('D1', 'D2'), seeds=(0, 1, 2), methods=('baseline', 'candidate')):
    return [{'dataset': dataset, 'seed': seed, 'method': method,
             'sample_id': str(index), 'y_true': index % 2,
             'probability': min(.98, max(.02, probability + seed * .005 + method_index * .015))}
            for dataset in datasets for seed in seeds for method_index, method in enumerate(methods)
            for index, probability in enumerate([.05, .08, .22, .25, .62, .65, .9, .95])]


def test_full_dataset_seed_method_grid_is_retained():
    source = predictions()
    _, report, _ = _build_calibration(source)
    assert report['datasets'] == ['D1', 'D2']
    assert report['methods'] == ['baseline', 'candidate']
    assert report['input_rows'] == report['selected_rows'] == len(source)
    assert len(report['per_seed_bins']) == 2 * 2 * 3 * 10
    assert report['seeds_by_dataset'] == {'D1': [0, 1, 2], 'D2': [0, 1, 2]}
    assert sum(entry['count'] for entry in report['per_seed_bins']) == len(source)
    assert all(entry['n_seeds_total'] == 3 for entry in report['aggregate_bins'])


def test_single_seed_has_no_fake_zero_sd():
    _, report, _ = _build_calibration(predictions(datasets=('D',), seeds=(0,), methods=('A',)))
    assert all(entry['y']['sd'] is None and entry['y']['uncertainty']['type'] == 'none'
               for entry in report['aggregate_bins'] if not entry['empty'])


def test_estimand_gives_seed_means_equal_weight_not_pooling_rows():
    rows = [{'dataset': 'D', 'method': 'A', 'seed': seed, 'sample_id': f'{seed}-{index}',
             'y_true': label, 'probability': .15}
            for seed, labels in [(0, [0]), (1, [1] * 9)] for index, label in enumerate(labels)]
    _, report, _ = _build_calibration(rows)
    entry = next(entry for entry in report['aggregate_bins'] if not entry['empty'])
    assert entry['y']['estimate'] == .5
    assert entry['y']['sd'] == pytest.approx(np.std([0, 1], ddof=1))
    assert entry['n_seeds'] == 2
    assert entry['counts']['mean'] == 5 and entry['counts']['total'] == 10


def test_empty_bins_are_explicit_and_break_curve_segments():
    rows = [{'dataset': 'D', 'method': 'A', 'seed': seed, 'sample_id': str(index),
             'y_true': index, 'probability': probability}
            for seed in [0, 1] for index, probability in enumerate([.05, .95])]
    _, report, _ = _build_calibration(rows)
    assert len(report['aggregate_bins']) == 10
    assert [entry['bin'] for entry in report['aggregate_bins'] if entry['empty']] == list(range(1, 9))
    assert report['curve_segments'][0]['connected_bin_segments'] == [[0], [9]]
    assert all(entry['counts']['total'] == 0 for entry in report['aggregate_bins'] if entry['empty'])


def test_partially_empty_seed_bin_does_not_supply_uncertainty():
    rows = [{'dataset': 'D', 'method': 'A', 'seed': seed, 'sample_id': str(seed),
             'y_true': seed, 'probability': probability} for seed, probability in [(0, .05), (1, .95)]]
    _, report, _ = _build_calibration(rows)
    entry = report['aggregate_bins'][0]
    assert entry['n_seeds'] == 1 and entry['n_seeds_total'] == 2 and entry['empty_seeds'] == [1]
    assert entry['counts']['mean'] == .5
    assert entry['y']['sd'] is None


@pytest.mark.parametrize('kind', ['se', 't_ci', 'bootstrap'])
def test_ci_requires_explicit_independent_seed_declaration(kind):
    with pytest.raises(ValueError, match='independent:true'):
        _build_calibration(predictions(), {'uncertainty': {'type': kind, 'unit': 'seed'}})


def test_actual_t_interval_is_across_seed_bin_means():
    rows = [{'dataset': 'D', 'method': 'A', 'seed': seed, 'sample_id': f'{seed}-{index}',
             'y_true': label, 'probability': .05}
            for seed, labels in [(0, [0, 0, 0, 1]), (1, [0, 0, 1, 1]), (2, [0, 1, 1, 1])]
            for index, label in enumerate(labels)]
    _, report, _ = _build_calibration(rows, {'uncertainty': {'type': 't_ci', 'unit': 'seed', 'independent': True}})
    entry = report['aggregate_bins'][0]
    expected_half = stats.t.ppf(.975, 2) * .25 / np.sqrt(3)
    assert entry['y']['estimate'] == .5
    assert entry['y']['ci_low'] == pytest.approx(.5 - expected_half)
    assert entry['y']['ci_high'] == pytest.approx(.5 + expected_half)


def test_unbalanced_method_seed_grid_is_rejected():
    rows = [row for row in predictions() if not (row['dataset'] == 'D1' and row['method'] == 'candidate' and row['seed'] == 2)]
    with pytest.raises(ValueError, match='complete selected'):
        _build_calibration(rows)


def test_selection_requires_scope_and_preserves_actual_full_data(tmp_path):
    source = predictions()
    with pytest.raises(ValueError, match='selection_scope'):
        _build_calibration(source, {'datasets': ['D1']})
    outputs = render_calibration(tmp_path, source, {'datasets': ['D1'], 'seeds': [0, 1],
        'selection_scope': 'D1 held-out reliability across the first two prescribed seeds', 'width': 3.25})
    report = json.loads(open(outputs['report']).read())
    assert report['datasets'] == ['D1'] and report['seeds_by_dataset'] == {'D1': [0, 1]}
    assert report['input_rows'] == len(source) and report['selected_rows'] == 32
    assert len(json.loads(open(outputs['data']).read())) == len(source)


def test_curve_rendering_exports_vectors_reports_and_editable_source(tmp_path):
    outputs = render_calibration(tmp_path, predictions(), {'width': 6.5, 'font_size': 9,
        'dataset_labels': {'D1': 'Natural prevalence', 'D2': 'Shifted prevalence'}})
    assert set(('pdf', 'svg', 'png', 'source', 'data', 'style', 'report', 'caption_context')) <= set(outputs)
    for path in outputs.values():
        assert __import__('pathlib').Path(path).is_file()
    report = json.loads(open(outputs['report']).read())
    assert report['minimum_font_pt'] >= 8
    assert all(entry['inside_canvas'] and entry['font_pt'] >= 8 for entry in report['typography'])
    assert report['method_colors'] == {'baseline': '#0077BB', 'candidate': '#EE7733'}
    assert 'Natural prevalence' in open(outputs['svg']).read()
    assert 'Shifted prevalence' in open(outputs['svg']).read()
    page = PdfReader(outputs['pdf']).pages[0]
    assert float(page.mediabox.width) / 72 == pytest.approx(6.5)
    assert float(page.mediabox.height) / 72 == pytest.approx(report['height_in'])
    caption = json.loads(open(outputs['caption_context']).read())
    assert caption['uncertainty']['type'] == 'sd'
    assert 'not statistical significance' in caption['interpretation_contract']


def test_probability_one_enters_final_bin_without_loss():
    rows = [{'dataset': 'D', 'method': 'A', 'seed': 0, 'y_true': 1, 'probability': 1.0}]
    _, report, _ = _build_calibration(rows)
    assert report['per_seed_bins'][-1]['count'] == 1
    assert report['aggregate_bins'][-1]['x']['estimate'] == 1


@pytest.mark.parametrize('probability', [float('nan'), float('inf'), -.01, 1.01, True])
def test_invalid_probabilities_fail_before_render(probability):
    rows = predictions()
    rows[0]['probability'] = probability
    with pytest.raises(ValueError, match='finite numbers'):
        _build_calibration(rows)


def test_duplicate_object_or_changed_label_is_rejected():
    rows = predictions()
    with pytest.raises(ValueError, match='Duplicate test-object'):
        _build_calibration(rows + [rows[0]])
    rows[8]['y_true'] = 1
    with pytest.raises(ValueError, match='inconsistent binary labels'):
        _build_calibration(rows)


@pytest.mark.parametrize('edges', [[.1, .5, 1], [0, .5, .5, 1], [0, .5, .9]])
def test_bin_edges_cannot_discard_probability_regions(edges):
    with pytest.raises(ValueError, match='strictly from zero to one'):
        _build_calibration(predictions(), {'bin_edges': edges})


def test_inference_never_uses_object_rows_as_independent_seed_repetitions():
    with pytest.raises(ValueError, match='uncertainty.unit=seed'):
        _build_calibration(predictions(), {'uncertainty': {'type': 't_ci', 'unit': 'unit_id', 'independent': True}})


def test_method_comparisons_retain_the_same_test_objects():
    source = predictions()
    source = [row for row in source if not (row['dataset'] == 'D1' and row['method'] == 'candidate' and row['seed'] == 0 and row['sample_id'] == '0')]
    with pytest.raises(ValueError, match='same saved test-object set'):
        _build_calibration(source)


def test_saved_data_and_style_reproduce_exact_selected_statistics(tmp_path):
    outputs = render_calibration(tmp_path, predictions(), {'dataset': 'D2', 'seed': 1,
        'selection_scope': 'Prescribed shifted-distribution inspection', 'width': 3.25})
    saved = json.loads(open(outputs['report']).read())
    _, repeated, _ = _build_calibration(json.loads(open(outputs['data']).read()),
                                      json.loads(open(outputs['style']).read()))
    for field in ('per_seed_bins', 'aggregate_bins', 'scope', 'ece_summary', 'curve_segments'):
        assert saved[field] == repeated[field]
