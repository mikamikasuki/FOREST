"""Handoffs check actual editable files and genuine arithmetic, not simulations."""
from copy import deepcopy
import json
import math
import os

import pytest

from research.validation.handoff import check_contract, validate_contract


@pytest.fixture
def workspaces(tmp_path):
    producer, verifier = tmp_path / 'producer', tmp_path / 'verifier'
    producer.mkdir()
    verifier.mkdir()
    return producer, verifier


def contract(*checks):
    return {'producer_node_id': 'experiment', 'checks': list(checks)}


def numeric(**options):
    return {'id': 'scores', 'kind': 'numeric_compare', 'source': 'metrics.json', 'repeat': 'metrics.json',
            'absolute_tolerance': 0, 'relative_tolerance': 0, **options}


def write_json(path, value):
    path.write_text(json.dumps(value))


def test_actual_numeric_files_are_compared_with_pointer_receipts(workspaces):
    producer, verifier = workspaces
    write_json(producer / 'metrics.json', {'score': 7, 'results': [{'accuracy': .75}]})
    write_json(verifier / 'metrics.json', {'score': 7, 'results': [{'accuracy': .75}]})
    result = check_contract(contract(numeric()), producer, verifier)
    assert result['verification_status'] == 'accepted'
    receipt = result['checks'][0]
    assert receipt['comparison_count'] == 2
    assert receipt['comparisons'][0]['pointer'] == '/results/0/accuracy'
    assert receipt['comparisons'][0]['original'] == receipt['comparisons'][0]['repeated'] == .75
    assert 'implementation independence' in receipt['verification_scope']


def test_editing_an_actual_repeat_changes_current_verdict(workspaces):
    producer, verifier = workspaces
    write_json(producer / 'metrics.json', {'score': 10})
    write_json(verifier / 'metrics.json', {'score': 10})
    checks = contract(numeric())
    assert check_contract(checks, producer, verifier)['verification_status'] == 'accepted'
    write_json(verifier / 'metrics.json', {'score': 11})
    result = check_contract(checks, producer, verifier)
    assert result['verification_status'] == 'rejected'
    assert result['checks'][0]['comparisons'][0]['absolute_difference'] == 1
    write_json(verifier / 'metrics.json', {'score': 10})
    assert check_contract(checks, producer, verifier)['verification_status'] == 'accepted'


def test_pointer_scope_is_explicit_and_tolerance_is_actual(workspaces):
    producer, verifier = workspaces
    write_json(producer / 'metrics.json', {'accuracy': .8, 'duration': 2})
    write_json(verifier / 'metrics.json', {'accuracy': .8001, 'duration': 8})
    scoped = contract(numeric(pointers=['/accuracy'], absolute_tolerance=.0002))
    assert check_contract(scoped, producer, verifier)['verification_status'] == 'accepted'
    assert check_contract(contract(numeric()), producer, verifier)['verification_status'] == 'rejected'


def test_json_pointer_escapes_and_scalar_roots_are_actual(workspaces):
    producer, verifier = workspaces
    for directory in workspaces:
        write_json(directory / 'metrics.json', {'a/b': {'~key': 4}})
    assert check_contract(contract(numeric(pointers=['/a~1b/~0key'])), producer, verifier)['verification_status'] == 'accepted'
    for directory in workspaces:
        write_json(directory / 'metrics.json', 4)
    assert check_contract(contract(numeric(pointers=[''])), producer, verifier)['verification_status'] == 'accepted'


@pytest.mark.parametrize('measurement', [float('nan'), float('inf'), -float('inf')])
def test_selected_nonfinite_measurements_are_rejected(workspaces, measurement):
    producer, verifier = workspaces
    write_json(producer / 'metrics.json', {'score': measurement})
    write_json(verifier / 'metrics.json', {'score': 4})
    result = check_contract(contract(numeric(pointers=['/score'])), producer, verifier)
    assert result['verification_status'] == 'rejected'
    assert not result['checks'][0]['comparisons'][0]['finite_in_both']
    json.dumps(result, allow_nan=False)


def test_missing_file_or_selected_value_is_inconclusive(workspaces):
    producer, verifier = workspaces
    write_json(producer / 'metrics.json', {'score': 4})
    assert check_contract(contract(numeric()), producer, verifier)['verification_status'] == 'inconclusive'
    write_json(verifier / 'metrics.json', {'other': 4})
    result = check_contract(contract(numeric(pointers=['/score'])), producer, verifier)
    assert result['verification_status'] == 'inconclusive'
    assert not result['checks'][0]['comparisons'][0]['present_in_both']


def test_definite_difference_takes_priority_over_unavailable_evidence(workspaces):
    producer, verifier = workspaces
    write_json(producer / 'metrics.json', {'score': 4, 'missing': 1})
    write_json(verifier / 'metrics.json', {'score': 5})
    result = check_contract(contract(numeric(pointers=['/score', '/missing'])), producer, verifier)
    assert result['verification_status'] == 'rejected'
    assert {receipt['status'] for receipt in result['checks'][0]['comparisons']} == {'rejected', 'inconclusive'}


@pytest.mark.parametrize('content', ['{invalid', '', 'null', '"no result"'])
def test_invalid_or_empty_numeric_artifacts_are_not_accepted(workspaces, content):
    producer, verifier = workspaces
    (producer / 'metrics.json').write_text(content)
    write_json(verifier / 'metrics.json', {'score': 4})
    assert check_contract(contract(numeric(pointers=['/score'])), producer, verifier)['verification_status'] == 'inconclusive'


def test_byte_comparison_reads_actual_full_files_beyond_first_chunk(workspaces):
    producer, verifier = workspaces
    source = b'a' * 140000
    (producer / 'result.bin').write_bytes(source)
    (verifier / 'result.bin').write_bytes(source)
    checks = contract({'id': 'bytes', 'kind': 'byte_compare', 'source': 'result.bin', 'repeat': 'result.bin'})
    assert check_contract(checks, producer, verifier)['verification_status'] == 'accepted'
    (verifier / 'result.bin').write_bytes(source[:-1] + b'b')
    result = check_contract(checks, producer, verifier)
    assert result['verification_status'] == 'rejected'
    assert result['checks'][0]['first_difference_offset'] == 139999
    assert result['checks'][0]['source_bytes'] == result['checks'][0]['repeat_bytes'] == 140000
    assert result['checks'][0]['full_files_read']


def test_byte_length_and_empty_artifacts_have_correct_verdict(workspaces):
    producer, verifier = workspaces
    checks = contract({'id': 'bytes', 'kind': 'byte_compare', 'source': 'result.bin', 'repeat': 'result.bin'})
    (producer / 'result.bin').write_bytes(b'ab')
    (verifier / 'result.bin').write_bytes(b'abc')
    assert check_contract(checks, producer, verifier)['verification_status'] == 'rejected'
    (producer / 'result.bin').write_bytes(b'')
    (verifier / 'result.bin').write_bytes(b'')
    assert check_contract(checks, producer, verifier)['verification_status'] == 'inconclusive'


def csv_check(kind='csv_coverage', **options):
    return {'id': 'matrix', 'kind': kind, 'source': 'observations.csv',
            'required_columns': ['dataset', 'seed', 'subject', 'method', 'score'],
            'unique_by': ['dataset', 'seed', 'subject', 'method'], 'numeric_columns': ['seed', 'score'],
            'expected': [{'dataset': 'D', 'seed': 0, 'subject': 'p1', 'method': 'A'},
                         {'dataset': 'D', 'seed': 0, 'subject': 'p2', 'method': 'A'}], **options}


def test_csv_checks_actual_composite_units_and_full_expected_matrix(workspaces):
    producer, verifier = workspaces
    (producer / 'observations.csv').write_text('dataset,seed,subject,method,score\nD,0,p1,A,.2\nD,0,p2,A,.4\n')
    result = check_contract(contract(csv_check()), producer, verifier)
    assert result['verification_status'] == 'accepted'
    assert result['checks'][0]['unique_units'] == 2
    assert result['checks'][0]['row_count'] == 2
    assert 'not a training-replication' in result['checks'][0]['verification_scope']


@pytest.mark.parametrize('body,problem', [
    ('D,0,p1,A,.2\n', 'missing'),
    ('D,0,p1,A,.2\nD,0,p1,A,.3\n', 'unique_statistical_unit'),
    ('D,0,p1,B,.2\nD,0,p2,A,.4\n', 'unexpected'),
    ('D,0,p1,A,nan\nD,0,p2,A,.4\n', 'finite_numeric'),
])
def test_csv_missing_duplicate_incompatible_and_nonfinite_are_rejected(workspaces, body, problem):
    producer, verifier = workspaces
    (producer / 'observations.csv').write_text('dataset,seed,subject,method,score\n' + body)
    result = check_contract(contract(csv_check()), producer, verifier)
    assert result['verification_status'] == 'rejected'
    assert problem in json.dumps(result)


def test_csv_coverage_checks_labels_beyond_unit_keys(workspaces):
    producer, verifier = workspaces
    (producer / 'observations.csv').write_text('dataset,seed,subject,method,score\nD,0,p1,B,.2\nD,0,p2,A,.4\n')
    check = csv_check(unique_by=['dataset', 'seed', 'subject'])
    result = check_contract(contract(check), producer, verifier)
    assert result['verification_status'] == 'rejected'
    assert result['checks'][0]['incompatible'][0]['column'] == 'method'


def test_csv_integrity_can_target_actual_verifier_output(workspaces):
    producer, verifier = workspaces
    (verifier / 'observations.csv').write_text('dataset,seed,subject,method,score\nD,0,p1,A,.2\nD,0,p2,A,.4\n')
    check = csv_check(kind='csv_integrity', workspace='verifier')
    assert check_contract(contract(check), producer, verifier)['verification_status'] == 'accepted'


def paired_check(**options):
    return {'id': 'paired', 'kind': 'paired_recompute', 'source': 'paired.csv', 'results': 'metrics.json',
            'unit_column': 'patient', 'baseline_column': 'baseline', 'candidate_column': 'candidate',
            'direction': 'lower', 'absolute_tolerance': 1e-12, 'relative_tolerance': 0,
            'fields': {field: '/' + field for field in ['baseline_mean', 'candidate_mean', 'improvement', 'unit_count', 'observation_count']},
            **options}


def paired_files(producer):
    (producer / 'paired.csv').write_text('patient,baseline,candidate\np1,10,7\np1,10,9\np2,6,5\np3,8,4\n')
    actual = {'baseline_mean': 8, 'candidate_mean': 17 / 3, 'improvement': 7 / 3,
              'unit_count': 3, 'observation_count': 4}
    write_json(producer / 'metrics.json', actual)
    return actual


def test_paired_arithmetic_reanalysis_uses_units_not_row_replication(workspaces):
    producer, verifier = workspaces
    paired_files(producer)
    result = check_contract(contract(paired_check()), producer, verifier)
    assert result['verification_status'] == 'accepted'
    actual = result['checks'][0]
    assert actual['measured']['unit_count'] == 3 and actual['measured']['observation_count'] == 4
    assert actual['measured']['baseline_mean'] == 8
    assert actual['measured']['improvement'] == pytest.approx(7 / 3)
    assert 'ci_low' not in actual['measured']
    assert 'no experiment rerun' in actual['verification_scope']


def test_paired_expected_numerical_result_cannot_be_fabricated(workspaces):
    producer, verifier = workspaces
    actual = paired_files(producer)
    actual['unit_count'] = 4
    actual['improvement'] = 99
    write_json(producer / 'metrics.json', actual)
    result = check_contract(contract(paired_check()), producer, verifier)
    assert result['verification_status'] == 'rejected'
    assert next(receipt for receipt in result['checks'][0]['comparisons'] if receipt['field'] == 'unit_count')['recomputed'] == 3


def test_paired_ci_uses_explicit_actual_cluster_bootstrap(workspaces):
    producer, verifier = workspaces
    actual = paired_files(producer)
    # For three unit improvements [2,1,4], the empirical percentile bootstrap
    # support and its 95% endpoints are [1,4] at this fixed draw configuration.
    actual.update(ci_low=1, ci_high=4)
    write_json(producer / 'metrics.json', actual)
    check = paired_check(sampling={'confidence': .95, 'bootstrap_samples': 5000, 'seed': 0, 'independent_units': True})
    check['fields'].update(ci_low='/ci_low', ci_high='/ci_high')
    result = check_contract(contract(check), producer, verifier)
    assert result['verification_status'] == 'accepted', result
    assert result['checks'][0]['measured']['ci_low'] == 1
    assert result['checks'][0]['measured']['ci_high'] == 4


def test_paths_aliases_and_symlinks_cannot_grant_acceptance(workspaces):
    producer, verifier = workspaces
    write_json(producer / 'metrics.json', {'score': 4})
    assert check_contract(contract(numeric()), producer, producer)['verification_status'] == 'inconclusive'
    os.link(producer / 'metrics.json', verifier / 'metrics.json')
    assert check_contract(contract(numeric()), producer, verifier)['verification_status'] == 'inconclusive'
    (verifier / 'metrics.json').unlink()
    (verifier / 'metrics.json').symlink_to(producer / 'metrics.json')
    assert check_contract(contract(numeric()), producer, verifier)['verification_status'] == 'inconclusive'


def test_symlink_parent_escape_is_never_read(workspaces, tmp_path):
    producer, verifier = workspaces
    outside = tmp_path / 'outside'
    outside.mkdir()
    write_json(outside / 'metrics.json', {'score': 4})
    (producer / 'escape').symlink_to(outside, target_is_directory=True)
    write_json(verifier / 'metrics.json', {'score': 4})
    result = check_contract(contract(numeric(source='escape/metrics.json')), producer, verifier)
    assert result['verification_status'] == 'inconclusive'
    assert 'Symlink' in result['checks'][0]['reason']


def test_checks_never_modify_actual_inputs_or_write_artifacts(workspaces):
    producer, verifier = workspaces
    for directory in workspaces:
        write_json(directory / 'metrics.json', {'score': 4})
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for directory in workspaces for path in directory.iterdir()}
    check_contract(contract(numeric()), producer, verifier)
    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for directory in workspaces for path in directory.iterdir()}
    assert before == after


@pytest.mark.parametrize('path', ['../metrics.json', '/tmp/metrics.json', 'x/../metrics.json', 'x\\metrics.json', 'x//metrics.json'])
def test_unsafe_contract_paths_are_rejected(path):
    with pytest.raises(ValueError, match='safe relative'):
        validate_contract(contract(numeric(source=path)))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True, '1e-6'])
def test_invalid_tolerances_cannot_silently_relax_contract(value):
    with pytest.raises(ValueError, match='finite|nonnegative'):
        validate_contract(contract(numeric(absolute_tolerance=value)))


def test_contract_has_no_vacuous_acceptance_or_generic_file_check():
    with pytest.raises(ValueError, match='meaningful'):
        validate_contract(contract())
    with pytest.raises(ValueError, match='Unsupported'):
        validate_contract(contract({'id': 'file', 'kind': 'file_exists', 'source': 'metrics.json'}))
    with pytest.raises(ValueError, match='unique'):
        validate_contract(contract(numeric(), numeric()))


def test_pairing_ci_and_expected_unit_contracts_are_explicit():
    paired = paired_check()
    paired['fields']['ci_low'] = '/ci_low'
    with pytest.raises(ValueError, match='explicit sampling'):
        validate_contract(contract(paired))
    paired = paired_check(sampling={'confidence': .95, 'bootstrap_samples': 5000, 'seed': 0})
    with pytest.raises(ValueError, match='independent_units'):
        validate_contract(contract(paired))
    expected = csv_check()
    expected['expected'].append(deepcopy(expected['expected'][0]))
    with pytest.raises(ValueError, match='duplicate statistical units'):
        validate_contract(contract(expected))


@pytest.mark.parametrize('change', [{'kind': []}, {'kind': {}}, {'ignored_requirement': 42}])
def test_invalid_or_unsupported_check_parameters_never_silently_pass(change):
    with pytest.raises(ValueError, match='Unsupported'):
        validate_contract(contract(numeric(**change)))


def test_comparison_missing_and_difference_checks_aggregate_without_vacuity(workspaces):
    producer, verifier = workspaces
    for directory in workspaces:
        write_json(directory / 'metrics.json', {'score': 4})
    missing = numeric(id='missing', repeat='missing.json')
    assert check_contract(contract(numeric(), missing), producer, verifier)['verification_status'] == 'inconclusive'
    write_json(verifier / 'metrics.json', {'score': 5})
    assert check_contract(contract(numeric(), missing), producer, verifier)['verification_status'] == 'rejected'
