"""Editable, actual-file research handoff checks, with no execution or writes.

The worker owns distinct execution receipts and run/revision bindings. This
module reads the current files every time; it does not freeze artifacts, use
digests, run commands, certify independent implementations or endorse scientific
claims. A numerical comparison needs source (producer) and repeat (verifier).
CSV checks establish only their declared integrity/coverage scope. Paired
recomputation is arithmetic reanalysis, never an experiment reproduction.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
import json
import math
import os
from pathlib import Path, PurePosixPath

import numpy as np

from research.validation.statistics import compare_result_files, paired_csv


KINDS = {'numeric_compare', 'byte_compare', 'csv_integrity', 'csv_coverage', 'paired_recompute'}
PAIRED_FIELDS = {'baseline_mean', 'candidate_mean', 'improvement', 'unit_count', 'observation_count'}


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(label + ' must be a nonempty string without control characters')
    return value


def _finite(value, label, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be a finite number')
    if nonnegative and value < 0:
        raise ValueError(label + ' must be nonnegative')
    return float(value)


def _relative(value, label):
    value = _text(value, label)
    parts = value.split('/')
    if '\\' in value or ':' in parts[0] or value.startswith('/') or any(part in {'', '.', '..'} for part in parts):
        raise ValueError(label + ' must be a safe relative file path')
    return str(PurePosixPath(value))


def _pointer(value):
    if not isinstance(value, str) or (value != '' and not value.startswith('/')) or any(ord(c) < 32 for c in value):
        raise ValueError('Selected measurements require explicit JSON pointers')
    for part in value[1:].split('/'):
        index = 0
        while index < len(part):
            if part[index] == '~':
                if index + 1 >= len(part) or part[index + 1] not in {'0', '1'}:
                    raise ValueError('JSON pointers require valid ~0/~1 escapes')
                index += 1
            index += 1
    return value


def _names(value, label, *, empty=False):
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError(label + ' must be a nonempty array')
    result = [_text(item, label) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(label + ' cannot contain duplicate names')
    return result


def _tolerances(check):
    # The normalized contract always contains both explicit effective values.
    check['absolute_tolerance'] = _finite(check.get('absolute_tolerance', 0), 'absolute_tolerance', nonnegative=True)
    check['relative_tolerance'] = _finite(check.get('relative_tolerance', 0), 'relative_tolerance', nonnegative=True)


def validate_contract(contract):
    """Return a normalized actual-file contract; invalid configuration raises.

    All checks have unique id/kind fields. Multiple checks of the same kind may
    target different actual artifacts. Supported forms:

    * numeric_compare: source, repeat, abs/relative tolerances, pointers optional.
    * byte_compare: source, repeat; full bytes, not fingerprints.
    * csv_integrity/coverage: source, workspace producer|verifier (producer by
      default), required_columns, unique_by, numeric_columns, expected optional
      list of cells. Coverage requires expected; expected cells contain every
      unique_by column and can assert additional labels or numeric values.
    * paired_recompute: source CSV, results producer JSON, unit/baseline/candidate
      columns, direction, fields={computed_field: JSON_pointer}, tolerances.
      Five mean/improvement/count fields are required. Optional sampling declares
      confidence, bootstrap_samples, seed, independent_units=true and requires
      ci_low/ci_high bindings. Without sampling, no CI is computed or accepted.
    """
    if not isinstance(contract, dict):
        raise ValueError('Verification contract must be an object')
    result = deepcopy(contract)
    if set(result) - {'producer_node_id', 'checks', 'description', 'version'}:
        raise ValueError('Unsupported verification contract fields')
    if 'description' in result:
        _text(result['description'], 'description')
    if 'version' in result and (isinstance(result['version'], bool) or result['version'] != 1):
        raise ValueError('Verification contract version must be 1')
    result['producer_node_id'] = _text(result.get('producer_node_id'), 'producer_node_id')
    checks = result.get('checks')
    if not isinstance(checks, list) or not checks:
        raise ValueError('Verification requires at least one meaningful check')
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError('Verification checks must be objects')
        identifier = _text(check.get('id'), 'Check id')
        if identifier in seen:
            raise ValueError('Verification check IDs must be unique')
        seen.add(identifier)
        kind = check.get('kind')
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError('Unsupported verification check kind: ' + str(kind))
        common = {'id', 'kind', 'source', 'scope', 'description'}
        supported = {
            'numeric_compare': {'repeat', 'absolute_tolerance', 'relative_tolerance', 'pointers'},
            'byte_compare': {'repeat'},
            'csv_integrity': {'workspace', 'required_columns', 'unique_by', 'numeric_columns', 'expected'},
            'csv_coverage': {'workspace', 'required_columns', 'unique_by', 'numeric_columns', 'expected'},
            'paired_recompute': {'results', 'unit_column', 'baseline_column', 'candidate_column',
                                 'direction', 'fields', 'sampling', 'absolute_tolerance', 'relative_tolerance'},
        }
        if set(check) - common - supported[kind]:
            raise ValueError('Unsupported parameters in ' + kind + ' check')
        if 'description' in check:
            _text(check['description'], 'Check description')
        if 'scope' in check:
            check['scope'] = _text(check['scope'], 'scope')
        check['source'] = _relative(check.get('source'), 'source')
        if kind in {'numeric_compare', 'byte_compare'}:
            check['repeat'] = _relative(check.get('repeat'), 'repeat')
        if kind == 'numeric_compare':
            _tolerances(check)
            if 'pointers' in check:
                pointers = check['pointers']
                if not isinstance(pointers, list) or not pointers:
                    raise ValueError('Numeric comparison pointers must be a nonempty array')
                check['pointers'] = [_pointer(pointer) for pointer in pointers]
                if len(set(check['pointers'])) != len(check['pointers']):
                    raise ValueError('Numeric pointers must be unique')
        if kind in {'csv_integrity', 'csv_coverage'}:
            check['workspace'] = check.get('workspace', 'producer')
            if not isinstance(check['workspace'], str) or check['workspace'] not in {'producer', 'verifier'}:
                raise ValueError('CSV workspace must be producer or verifier')
            for name in ('required_columns', 'unique_by', 'numeric_columns'):
                check[name] = _names(check.get(name), name)
            if not set(check['unique_by'] + check['numeric_columns']) <= set(check['required_columns']):
                raise ValueError('Unit and numeric columns must belong to required_columns')
            expected = check.get('expected')
            if kind == 'csv_coverage' and not expected:
                raise ValueError('CSV coverage requires a nonempty expected matrix')
            if expected is not None:
                if not isinstance(expected, list) or not expected:
                    raise ValueError('Expected matrix must be a nonempty array of actual cells')
                identities = set()
                for cell in expected:
                    if not isinstance(cell, dict) or not set(check['unique_by']) <= set(cell) or not set(cell) <= set(check['required_columns']):
                        raise ValueError('Expected cells require all declared unit columns and only declared columns')
                    for name, value in cell.items():
                        if name in check['numeric_columns']:
                            _finite(value, 'Expected ' + name)
                        elif value is None or isinstance(value, (dict, list, bool)):
                            raise ValueError('Expected labels must be explicit scalar identities')
                        elif isinstance(value, (int, float)) and not math.isfinite(value):
                            raise ValueError('Expected labels must be finite')
                    identity = tuple(_csv_expected(cell[column], column in check['numeric_columns']) for column in check['unique_by'])
                    if identity in identities:
                        raise ValueError('Expected matrix contains duplicate statistical units')
                    identities.add(identity)
        if kind == 'paired_recompute':
            check['results'] = _relative(check.get('results'), 'results')
            for column in ('unit_column', 'baseline_column', 'candidate_column'):
                check[column] = _text(check.get(column), column)
            if len({check['unit_column'], check['baseline_column'], check['candidate_column']}) != 3:
                raise ValueError('Paired recomputation requires distinct identity and score columns')
            if not isinstance(check.get('direction'), str) or check['direction'] not in {'higher', 'lower'}:
                raise ValueError('Paired direction must be higher or lower')
            _tolerances(check)
            fields = check.get('fields')
            if not isinstance(fields, dict) or not PAIRED_FIELDS <= fields.keys():
                raise ValueError('Paired fields must bind both means, improvement and unit/observation counts')
            allowed = PAIRED_FIELDS | {'ci_low', 'ci_high', 'confidence'}
            if not set(fields) <= allowed:
                raise ValueError('Unsupported paired computed field')
            for name, pointer in fields.items():
                fields[name] = _pointer(pointer)
            if len(set(fields.values())) != len(fields):
                raise ValueError('Paired result fields require distinct numeric pointers')
            sampling = check.get('sampling')
            if sampling is None and ({'ci_low', 'ci_high', 'confidence'} & fields.keys()):
                raise ValueError('CI fields require explicit sampling options')
            if sampling is not None:
                if not isinstance(sampling, dict) or sampling.get('independent_units') is not True:
                    raise ValueError('Paired CI sampling requires independent_units:true')
                if set(sampling) - {'confidence', 'bootstrap_samples', 'seed', 'independent_units'}:
                    raise ValueError('Unsupported paired sampling options')
                sampling['confidence'] = _finite(sampling.get('confidence'), 'sampling.confidence')
                if not 0 < sampling['confidence'] < 1:
                    raise ValueError('Sampling confidence must be in (0,1)')
                for name, minimum in (('bootstrap_samples', 100), ('seed', 0)):
                    value = sampling.get(name)
                    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                        raise ValueError('Sampling ' + name + ' requires an explicit integer')
                if not {'ci_low', 'ci_high'} <= fields.keys():
                    raise ValueError('Explicit sampling must bind both CI endpoints')
    return result


class _Unavailable(ValueError):
    pass


def _file(workspace, relative):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise _Unavailable('Workspace is unavailable')
    path = root
    for component in PurePosixPath(relative).parts:
        path = path / component
        if path.is_symlink():
            raise _Unavailable('Symlink artifact references are not admissible')
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise _Unavailable('Artifact escapes its workspace')
    if not path.is_file():
        raise _Unavailable('Required actual file is missing or is not a regular file: ' + relative)
    return path


def _different(source, repeated):
    if source.resolve() == repeated.resolve() or os.path.samefile(source, repeated):
        raise _Unavailable('Repeat artifact aliases the producer file; distinct actual execution output is required')


def _resolve(value, pointer):
    if pointer == '':
        return value
    for part in pointer[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not part.isdigit() or (len(part) > 1 and part[0] == '0'):
                raise KeyError(pointer)
            value = value[int(part)]
        else:
            value = value[part]
    return value


def _safe_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return 'NaN' if math.isnan(value) else ('+Infinity' if value > 0 else '-Infinity')
    return value


def _leaf_numbers(value, pointer=''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaf_numbers(item, pointer + '/' + str(key).replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaf_numbers(item, pointer + '/' + str(index))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield pointer, value


def _status(statuses):
    return 'rejected' if 'rejected' in statuses else 'inconclusive' if 'inconclusive' in statuses else 'accepted'


def _compare_values(check, pointers, original, repeated):
    receipts = []
    for pointer in pointers:
        available = []
        values = []
        for value in (original, repeated):
            try:
                measurement = _resolve(value, pointer)
                present = isinstance(measurement, (int, float)) and not isinstance(measurement, bool)
            except (KeyError, IndexError, TypeError, ValueError):
                measurement, present = None, False
            available.append(present)
            values.append(measurement if present else None)
        finite = all(present and math.isfinite(value) for present, value in zip(available, values))
        nonfinite = any(present and not math.isfinite(value) for present, value in zip(available, values))
        matches = finite and math.isclose(*values, abs_tol=check['absolute_tolerance'], rel_tol=check['relative_tolerance'])
        status = 'rejected' if nonfinite or finite and not matches else 'accepted' if matches else 'inconclusive'
        receipts.append({'pointer': pointer, 'original': _safe_value(values[0]), 'repeated': _safe_value(values[1]),
                         'present_in_both': all(available), 'finite_in_both': finite,
                         'absolute_difference': abs(values[0] - values[1]) if finite else None,
                         'absolute_tolerance': check['absolute_tolerance'], 'relative_tolerance': check['relative_tolerance'],
                         'matches': matches, 'status': status})
    return receipts


def _numeric(check, producer, verifier):
    source, repeated = _file(producer, check['source']), _file(verifier, check['repeat'])
    _different(source, repeated)
    original, repeat = json.loads(source.read_text()), json.loads(repeated.read_text())
    pointers = check.get('pointers')
    if pointers is None:
        pointers = sorted(set(dict(_leaf_numbers(original))) | set(dict(_leaf_numbers(repeat))))
    if not pointers:
        raise _Unavailable('Actual files contain no numeric measurements')
    receipts = _compare_values(check, pointers, original, repeat)
    # Reuse the established engine for finite complete artifacts. Scope-aware
    # receipts above also represent missing or nonfinite selected leaves safely.
    if all(entry['finite_in_both'] for entry in receipts) and all(math.isfinite(value) for _, value in list(_leaf_numbers(original)) + list(_leaf_numbers(repeat))):
        comparison = compare_result_files(source, repeated,
            absolute_tolerance=check['absolute_tolerance'], relative_tolerance=check['relative_tolerance'], pointers=pointers)
        if any(entry['matches'] != actual['matches'] for entry, actual in zip(receipts, comparison['comparisons'])):
            raise RuntimeError('Independent numerical check disagrees with existing comparison engine')
    return {'status': _status([entry['status'] for entry in receipts]), 'comparison_count': len(receipts),
            'comparisons': receipts, 'source': check['source'], 'repeat': check['repeat'],
            'verification_scope': 'Actual numeric artifact comparison; execution provenance and implementation independence are outside this check'}


def _bytes(check, producer, verifier):
    source, repeated = _file(producer, check['source']), _file(verifier, check['repeat'])
    _different(source, repeated)
    first_difference, offset, left_count, right_count = None, 0, 0, 0
    with source.open('rb') as left, repeated.open('rb') as right:
        while True:
            a, b = left.read(65536), right.read(65536)
            if not a and not b:
                break
            left_count += len(a)
            right_count += len(b)
            if first_difference is None and a != b:
                first_difference = offset + next((index for index, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            offset += max(len(a), len(b))
    return {'status': 'inconclusive' if not left_count and not right_count else 'accepted' if first_difference is None else 'rejected',
            'source': check['source'], 'repeat': check['repeat'],
            'source_bytes': left_count, 'repeat_bytes': right_count,
            'first_difference_offset': first_difference, 'full_files_read': True,
            'verification_scope': 'Actual full-file byte equality; no content digest, artifact freeze or independent implementation claim'}


def _csv_expected(value, numeric):
    return format(float(value), '.17g') if numeric else str(value)


def _read_csv(path):
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        columns = reader.fieldnames
        if not columns:
            raise _Unavailable('CSV has no readable header')
        return columns, list(reader)


def _csv(check, producer, verifier):
    path = _file(producer if check['workspace'] == 'producer' else verifier, check['source'])
    columns, rows = _read_csv(path)
    issues = []
    if len(columns) != len(set(columns)):
        issues.append({'check': 'unique_header', 'problem': 'Duplicate CSV column names'})
    missing_columns = sorted(set(check['required_columns']) - set(columns))
    if missing_columns:
        issues.append({'check': 'required_columns', 'missing': missing_columns})
    if not rows:
        issues.append({'check': 'observed_units', 'problem': 'No observed rows'})
    identities, normalized = defaultdict(list), []
    for index, row in enumerate(rows):
        parsed = {}
        if None in row or any(value is None for value in row.values()):
            issues.append({'check': 'row_shape', 'row': index, 'problem': 'CSV row width differs from header'})
        for column in check['required_columns']:
            value = row.get(column)
            if value is None or value == '':
                issues.append({'check': 'nonempty_required_value', 'row': index, 'column': column})
                continue
            if column in check['numeric_columns']:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    issues.append({'check': 'numeric_column', 'row': index, 'column': column, 'actual': str(value)})
                    continue
                if not math.isfinite(value):
                    issues.append({'check': 'finite_numeric', 'row': index, 'column': column, 'actual': _safe_value(value)})
                    continue
                parsed[column] = format(value, '.17g')
            else:
                parsed[column] = value
        identity = tuple(parsed.get(column) for column in check['unique_by'])
        if None not in identity:
            identities[identity].append(index)
        normalized.append(parsed)
    duplicates = [{'identity': dict(zip(check['unique_by'], identity)), 'rows': indices}
                  for identity, indices in identities.items() if len(indices) > 1]
    if duplicates:
        issues.append({'check': 'unique_statistical_unit', 'duplicates': duplicates})
    missing, unexpected, incompatible = [], [], []
    if check.get('expected') is not None:
        expected = {tuple(_csv_expected(cell[column], column in check['numeric_columns']) for column in check['unique_by']): cell
                    for cell in check['expected']}
        missing = [deepcopy(cell) for identity, cell in expected.items() if identity not in identities]
        unexpected = [dict(zip(check['unique_by'], identity)) for identity in identities if identity not in expected]
        for identity, cell in expected.items():
            for index in identities.get(identity, []):
                for column, value in cell.items():
                    wanted = _csv_expected(value, column in check['numeric_columns'])
                    if normalized[index].get(column) != wanted:
                        incompatible.append({'row': index, 'identity': dict(zip(check['unique_by'], identity)),
                                             'column': column, 'expected': value, 'actual': rows[index].get(column)})
        if missing or unexpected or incompatible:
            issues.append({'check': 'expected_matrix', 'missing': missing, 'unexpected': unexpected, 'incompatible': incompatible})
    return {'status': 'rejected' if issues else 'accepted', 'source': check['source'], 'workspace': check['workspace'],
            'columns': columns, 'required_columns': check['required_columns'], 'unique_by': check['unique_by'],
            'numeric_columns': check['numeric_columns'], 'row_count': len(rows), 'unique_units': len(identities),
            'expected_cells': len(check.get('expected', [])), 'missing': missing, 'unexpected': unexpected,
            'incompatible': incompatible, 'issues': issues,
            'verification_scope': 'All actual CSV rows checked for declared columns, finite values, unit identity and configured matrix; row count is not a training-replication count'}


def _paired(check, producer, verifier):
    source, results_file = _file(producer, check['source']), _file(producer, check['results'])
    columns, rows = _read_csv(source)
    required = {check['unit_column'], check['baseline_column'], check['candidate_column']}
    if not required <= set(columns):
        return {'status': 'rejected', 'issues': [{'check': 'paired_columns', 'missing': sorted(required - set(columns))}],
                'verification_scope': 'Arithmetic reanalysis of actual paired observations'}
    if len(columns) != len(set(columns)):
        return {'status': 'rejected', 'issues': [{'check': 'paired_columns', 'problem': 'Duplicate CSV headers'}],
                'verification_scope': 'Arithmetic reanalysis of actual paired observations'}
    grouped, issues = defaultdict(list), []
    for index, row in enumerate(rows):
        if None in row or any(value is None for value in row.values()):
            issues.append({'row': index, 'problem': 'Paired CSV row width differs from its header'})
        identity = row.get(check['unit_column'])
        if not identity:
            issues.append({'row': index, 'problem': 'Missing statistical unit identity'})
            continue
        values = []
        for column in (check['baseline_column'], check['candidate_column']):
            try:
                value = float(row[column])
                if not math.isfinite(value):
                    raise ValueError()
                values.append(value)
            except (KeyError, TypeError, ValueError):
                issues.append({'row': index, 'column': column, 'problem': 'Missing or nonfinite actual paired score'})
        if len(values) == 2:
            grouped[identity].append(values)
    if issues or not grouped:
        return {'status': 'rejected', 'issues': issues or [{'problem': 'No actual paired observations'}],
                'verification_scope': 'Arithmetic reanalysis of actual paired observations'}
    means = np.array([np.mean(grouped[key], axis=0) for key in sorted(grouped)])
    sign = 1 if check['direction'] == 'lower' else -1
    measured = {'baseline_mean': float(means[:, 0].mean()), 'candidate_mean': float(means[:, 1].mean()),
                'improvement': float(((means[:, 0] - means[:, 1]) * sign).mean()),
                'unit_count': len(grouped), 'observation_count': sum(len(values) for values in grouped.values())}
    if check.get('sampling'):
        sampling = check['sampling']
        computed = paired_csv(source, unit_column=check['unit_column'], baseline_column=check['baseline_column'],
            candidate_column=check['candidate_column'], direction=check['direction'],
            confidence=sampling['confidence'], bootstrap_samples=sampling['bootstrap_samples'], seed=sampling['seed'])
        measured.update({key: computed[key] for key in ('ci_low', 'ci_high', 'confidence')})
    expected = json.loads(results_file.read_text())
    receipts = []
    for field, pointer in check['fields'].items():
        try:
            value = _resolve(expected, pointer)
            present = isinstance(value, (int, float)) and not isinstance(value, bool)
        except (KeyError, IndexError, TypeError, ValueError):
            value, present = None, False
        finite = present and math.isfinite(value)
        matches = finite and math.isclose(measured[field], value,
            abs_tol=check['absolute_tolerance'], rel_tol=check['relative_tolerance'])
        status = 'accepted' if matches else 'rejected' if present else 'inconclusive'
        receipts.append({'field': field, 'pointer': pointer, 'original': _safe_value(value) if present else None,
                         'recomputed': measured[field], 'present': present, 'matches': matches, 'status': status,
                         'absolute_tolerance': check['absolute_tolerance'], 'relative_tolerance': check['relative_tolerance']})
    return {'status': _status([entry['status'] for entry in receipts]), 'source': check['source'], 'results': check['results'],
            'measured': measured, 'comparisons': receipts,
            'sampling': deepcopy(check.get('sampling')), 'unit_column': check['unit_column'],
            'verification_scope': 'Arithmetic reanalysis from actual observations with equal unit-cluster weights; no experiment rerun or independent implementation certification',
            'assumptions': ['Statistical units are appropriate for the intended estimand',
                            'CSV baseline and candidate columns refer to the same paired observations'] +
                           (['Declared unit clusters are independently sampled; CI conditions on observed fitted models'] if check.get('sampling') else [])}


def check_contract(contract, producer_workspace: Path, verifier_workspace: Path):
    """Read current artifacts, return machine scope, and never write or execute.

    Contradictory finite values, nonfinite measurements or violated CSV integrity
    reject the handoff. Missing/unreadable files or missing numeric evidence are
    inconclusive. A definite rejection takes priority over unavailable evidence.
    Caller must separately verify run provenance and current node revisions.
    """
    normalized = validate_contract(contract)
    checks = []
    functions = {'numeric_compare': _numeric, 'byte_compare': _bytes,
                 'csv_integrity': _csv, 'csv_coverage': _csv, 'paired_recompute': _paired}
    for configured in normalized['checks']:
        try:
            result = functions[configured['kind']](configured, producer_workspace, verifier_workspace)
        except (_Unavailable, OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
            result = {'status': 'inconclusive', 'reason': str(exc),
                      'verification_scope': 'Requested actual-file check could not be completed'}
        except ValueError as exc:
            # A computation that cannot establish the selected arithmetic scope
            # is unavailable; finite contradictory receipts are handled above.
            result = {'status': 'inconclusive', 'reason': str(exc),
                      'verification_scope': 'Requested arithmetic check could not be completed'}
        checks.append({'id': configured['id'], 'kind': configured['kind'],
                       'scope': configured.get('scope'), **result})
    assumptions = ['Worker binds the actual producer and verifier runs to the current editable contract and node revisions',
                   'Numerical or byte agreement does not establish an independent implementation or scientific truth']
    for result in checks:
        assumptions.extend(result.get('assumptions', []))
    return {'verification_status': _status([result['status'] for result in checks]),
            'producer_node_id': normalized['producer_node_id'], 'checks': checks,
            'verification_scope': 'Only configured checks on current actual files and explicitly selected arithmetic; execution, independence and scientific interpretation require separate evidence',
            'assumptions': list(dict.fromkeys(assumptions))}
