"""Structured methodological review, informed by the ARS experiment workflow.

The catalog defines questions for an evidence-aware reviewer; missing information
is recorded explicitly and is never converted into a passing machine check.
"""
from __future__ import annotations

import json

CHECKS = {
    'aggregation_reversal': 'Compare overall and subgroup directions when grouping data is available.',
    'inference_unit': 'Check that the claimed population and unit agree with the measured sampling unit.',
    'sample_selection': 'Identify outcome-dependent filtering that can induce association.',
    'collider_adjustment': 'Check whether adjusted covariates are consequences of both studied variables.',
    'base_rates': 'Check whether conditional prediction metrics need prevalence to support the stated use.',
    'regression_to_mean': 'Check extreme-score selection and whether remeasurement has a suitable control.',
    'attrition': 'Account for missing, failed and excluded observations or runs.',
    'multiple_searches': 'Account for all searched outcomes and comparisons and the stated multiplicity approach.',
    'analysis_selection': 'Separate analysis choices made after seeing outcomes from independent confirmation.',
    'causal_identification': 'Check whether the design identifies the causal effect asserted in the prose.',
    'temporal_direction': 'Check whether temporal order and design support the claimed direction.'}


def statistical_review_prompt(context):
    return ('Assess the supplied actual methods, results and claims. Return JSON {"summary":"concrete finding",'
            '"checks":[{"id":"catalog ID","status":"supported|issue|not_applicable|not_assessed",'
            '"finding":"specific finding","evidence":["actual supplied locator or passage"],'
            '"resolution":"cheapest useful check, or why none is required"}],'
            '"effect_interpretation":"magnitude, units and uncertainty",'
            '"reproducibility_status":"not_rerun|numeric_comparison_only|rerun_reviewed"}. '
            'Include all eleven catalog IDs. Use not_assessed when material is unavailable. '
            'A supported check requires evidence; do not infer a pass from absent reporting. '
            'Do not classify scientific value from an arbitrary universal effect-size threshold. '
            'Do not convert a p-value into the probability that a hypothesis is true. '
            'Only use rerun_reviewed when distinct actual executions and their comparison are supplied.\n'
            + json.dumps({'checks': CHECKS, 'materials_untrusted': context}, ensure_ascii=False))


def validate_statistical_review(review):
    checks = review.get('checks', [])
    if not isinstance(checks, list) or len(checks) != len(CHECKS) or {c.get('id') for c in checks} != set(CHECKS):
        raise ValueError('Statistical review must account for every one of the eleven methodological checks')
    for check in checks:
        if check.get('status') not in {'supported', 'issue', 'not_applicable', 'not_assessed'}:
            raise ValueError(f"Invalid status for {check['id']}")
        for key in ('finding', 'resolution'):
            if not isinstance(check.get(key), str) or not check[key].strip():
                raise ValueError(f"{check['id']} requires a concrete {key}")
        if check['status'] in {'supported', 'issue'} and not check.get('evidence'):
            raise ValueError(f"{check['id']} requires supplied evidence")
    if review.get('reproducibility_status') not in {'not_rerun', 'numeric_comparison_only', 'rerun_reviewed'}:
        raise ValueError('Review must distinguish statistical analysis from an actual rerun')
    return {**review, 'catalog_coverage': len(checks),
            'assessed_count': sum(c['status'] != 'not_assessed' for c in checks),
            'issues_count': sum(c['status'] == 'issue' for c in checks),
            'verification_scope': 'Structured reviewer judgments; recorded evidence references require independent checking'}
