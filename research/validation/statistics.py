"""Independent computations from observed result files, with explicit units."""
from __future__ import annotations

import csv
from contextlib import nullcontext
import io
import json
import math
from pathlib import Path

import numpy as np

from research.paper.evidence import _numbers


def paired_csv(path, *, unit_column, baseline_column, candidate_column,
               direction='lower', confidence=0.95, bootstrap_samples=5000, seed=0,
               meaningful_effect=0.0, output=None, input_stream=None):
    """Paired cluster bootstrap of unit-average score differences.

    Repeated observations of a unit remain together. Inference conditions on
    the evaluated fitted models; rows are not claimed to be training replicas.
    Positive improvement always means the candidate is better.
    """
    if direction not in {'lower', 'higher'}:
        raise ValueError('direction must be lower or higher')
    if not 0 < confidence < 1 or bootstrap_samples < 100:
        raise ValueError('Specify confidence in (0, 1) and at least 100 bootstrap draws')
    if not math.isfinite(meaningful_effect) or meaningful_effect < 0:
        raise ValueError('meaningful_effect must be finite and nonnegative')
    groups = {}
    with (Path(path).open(newline='') if input_stream is None else
          nullcontext(input_stream)) as stream:
        rows = csv.DictReader(stream)
        if not {unit_column, baseline_column, candidate_column} <= set(rows.fieldnames or []):
            raise ValueError('Observed CSV is missing requested pairing or score columns')
        for row in rows:
            unit = row[unit_column]
            if not unit:
                raise ValueError('Every observation needs a pairing identity')
            baseline, candidate = float(row[baseline_column]), float(row[candidate_column])
            if not math.isfinite(baseline) or not math.isfinite(candidate):
                raise ValueError('Nonfinite observed scores cannot be silently omitted')
            groups.setdefault(unit, []).append((baseline, candidate))
    if len(groups) < 2:
        raise ValueError('Paired cluster inference requires at least two distinct units')
    scores = np.array([np.mean(groups[key], axis=0) for key in sorted(groups)])
    differences = (scores[:, 0] - scores[:, 1]) * (1 if direction == 'lower' else -1)
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(bootstrap_samples)
    # Bounded batches keep resampling usable for large observed datasets.
    batch_size = max(1, min(256, 1_000_000 // len(scores)))
    for start in range(0, bootstrap_samples, batch_size):
        count = min(batch_size, bootstrap_samples - start)
        indices = rng.integers(0, len(scores), size=(count, len(scores)))
        bootstrap[start:start + count] = differences[indices].mean(axis=1)
    alpha = (1 - confidence) / 2
    low, high = np.quantile(bootstrap, [alpha, 1 - alpha])
    result = {'status': 'ANALYZED', 'evidence_label': 'MEASURED', 'method': 'paired_unit_cluster_percentile_bootstrap',
              'source_file': str(Path(path).resolve()), 'unit_column': unit_column,
              'unit_count': len(groups), 'observation_count': sum(len(v) for v in groups.values()),
              'baseline_column': baseline_column, 'candidate_column': candidate_column,
              'baseline_mean': float(scores[:, 0].mean()), 'candidate_mean': float(scores[:, 1].mean()),
              'improvement': float(differences.mean()), 'ci_low': float(low), 'ci_high': float(high),
              'confidence': confidence, 'bootstrap_samples': bootstrap_samples, 'seed': seed,
              'meaningful_effect': meaningful_effect,
              'ci_excludes_no_improvement': bool(low > 0),
              'ci_exceeds_meaningful_effect': bool(low > meaningful_effect),
              'statistical_scope': 'Equal-weighted independent unit clusters; conditional on observed fitted models',
              'assumptions_to_review': ['Units are independently sampled at the declared unit level',
                                         'Both methods use the same evaluation units',
                                         'Evaluation data was not used for candidate selection'],
              'reproducibility_status': 'No experiment rerun performed by this analysis'}
    if output:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2))
    return result


def compare_result_files(original, repeated, *, absolute_tolerance=0.0, relative_tolerance=0.0,
                         pointers=None, output=None):
    """Compare actual result files without content digests or locked artifacts.

    A numerical match alone does not establish that a second experiment ran.
    Caller links distinct run receipts and the relevant environment separately.
    """
    if any(not math.isfinite(t) or t < 0 for t in (absolute_tolerance, relative_tolerance)):
        raise ValueError('Comparison tolerances must be finite and nonnegative')
    left = dict(_numbers(json.loads(Path(original).read_text())))
    right = dict(_numbers(json.loads(Path(repeated).read_text())))
    selected = sorted(set(pointers) if pointers is not None else set(left) | set(right))
    if not selected:
        raise ValueError('No numeric results selected for comparison')
    comparisons = []
    for pointer in selected:
        a, b = left.get(pointer), right.get(pointer)
        present = pointer in left and pointer in right
        match = present and math.isclose(a, b, abs_tol=absolute_tolerance, rel_tol=relative_tolerance)
        comparisons.append({'pointer': pointer, 'original': a, 'repeated': b,
                            'absolute_difference': abs(a - b) if present else None,
                            'matches': match, 'present_in_both': present})
    result = {'status': 'NUMERIC_MATCH' if all(c['matches'] for c in comparisons) else 'NUMERIC_DIFFERENCE',
              'original_file': str(Path(original).resolve()), 'repeated_file': str(Path(repeated).resolve()),
              'absolute_tolerance': absolute_tolerance, 'relative_tolerance': relative_tolerance,
              'comparison_count': len(comparisons), 'comparisons': comparisons,
              'verification_scope': 'Actual numeric artifact comparison; distinct execution and environment must be reviewed separately'}
    if output:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2))
    return result
