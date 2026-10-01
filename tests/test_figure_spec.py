"""Scientific source integrity and typed presentation-contract regressions."""
from copy import deepcopy

import pytest

from research.figures.spec import (
    apply_scene_revision, build_figure_contract, design_instructions,
    resolve_scene_metrics, revision_instructions, scene_schema, validate_scene,
)


def supplied():
    graph = {
        'nodes': [{'id': 'Encode', 'label': 'Input encoding'},
                  {'id': 'Attend', 'label': 'Causal attention'}],
        'edges': [{'source': 'Encode', 'target': 'Attend', 'label': 'query / key / value'}],
        'key_operation_id': 'Attend',
    }
    context = {'method': 'Encode tokens and apply a causal query-key mask before aggregating values.',
               'evidence': {'metrics': [{'id': 'accuracy', 'value': 83.5, 'unit': '%'}]}}
    contract = build_figure_contract(graph, context, {'layout_width_in': 6.5, 'height': 4.4})
    scene = {
        'version': 1, 'width_in': 6.5, 'height_in': 4.4,
        'panels': [{'id': 'Mechanism', 'role': 'mechanism', 'title': 'Causal token aggregation',
                    'bbox': [0.02, 0.02, 0.96, 0.96]}],
        'objects': [
            {'id': 'Encoding', 'panel': 'Mechanism', 'operation_id': 'Encode', 'kind': 'tensor',
             'label': 'Input encoding', 'bbox': [0.07, 0.2, 0.25, 0.35],
             'detail': 'Each token carries a learned vector representation.',
             'params': {'shape': [4, 4, 3]}, 'evidence_refs': ['operation:Encode']},
            {'id': 'Attention', 'panel': 'Mechanism', 'operation_id': 'Attend', 'kind': 'matrix',
             'label': 'Causal attention', 'bbox': [0.58, 0.2, 0.3, 0.35],
             'detail': 'The triangular mask excludes information from future token positions.',
             'params': {'rows': 6, 'cols': 6, 'mask': 'causal', 'schematic': True},
             'evidence_refs': ['operation:Attend']},
        ],
        'connections': [{'id': 'Latents', 'source': 'Encoding', 'target': 'Attention',
                         'semantic_edge': 0, 'label': 'query / key / value'}],
        'annotations': [],
    }
    return graph, contract, scene


def test_contract_retains_sources_without_mutating_input():
    graph, contract, scene = supplied()
    original = deepcopy(graph)
    graph['nodes'][0]['label'] = 'Changed outside the contract'
    assert contract['nodes'] == original['nodes']
    assert contract['edges'] == original['edges']
    report = validate_scene(scene, contract)
    assert report['operation_coverage'] == 2
    assert report['dependency_coverage'] == 1
    assert report['native_objects'] == 2
    assert report['detailed_operations'] == ['Attend', 'Encode']
    assert report['status'] == 'validated_structure'


@pytest.mark.parametrize('stored_context', [False, True])
def test_repeated_render_freezes_science_without_nesting_previous_production(stored_context):
    graph, _, _ = supplied()
    method = deepcopy(graph)
    method['source_note'] = 'A user-supplied causal mask definition.'
    context = {'method': method, 'caption': 'Encoded tokens and causal attention.'}
    if stored_context:
        graph['story_context'] = context
    first = build_figure_contract(graph, context)
    for _ in range(3):
        method.update(production_contract=first, production_scene={'objects': []},
                      production_composition={'version': 1}, asset_data={'preview': 'embedded'},
                      production_raster={'selected_image': 'saved.png'})
        before = deepcopy(method)
        repeated = build_figure_contract(graph, context)
        assert repeated['source_fingerprint'] == first['source_fingerprint']
        assert repeated['source_context']['method']['source_note'] == method['source_note']
        assert not {'production_contract', 'production_scene', 'production_composition',
                    'asset_data', 'production_raster'} & repeated['source_context']['method'].keys()
        assert method == before
        first = repeated


@pytest.mark.parametrize('offset', [True, '0.1', float('nan'), float('inf')])
def test_port_offsets_require_finite_physical_values(offset):
    _, contract, scene = supplied()
    scene['connections'][0].update(from_port='right', from_port_offset_in=offset)
    with pytest.raises(ValueError, match='finite physical offset'):
        validate_scene(scene, contract)


def test_port_offsets_require_named_sides_and_preserve_source_binding():
    _, contract, scene = supplied()
    scene['connections'][0]['from_port_offset_in'] = .1
    with pytest.raises(ValueError, match='named side'):
        validate_scene(scene, contract)
    scene['connections'][0].update(from_port='right', to_port='left', to_port_offset_in=-.1)
    assert validate_scene(scene, contract)['dependency_coverage'] == 1
    assert scene['connections'][0]['semantic_edge'] == 0


@pytest.mark.parametrize('mutation, message', [
    (lambda scene: scene.update(width_in=9.0), 'print width_in'),
    (lambda scene: scene['objects'].pop(), 'omitted supplied operations'),
    (lambda scene: scene['objects'][0].update(label='Better encoder'), 'exact label'),
    (lambda scene: scene['connections'][0].update(label='Improved features'), 'dependency label'),
    (lambda scene: scene['connections'][0].update(source='Attention', target='Encoding'), 'dependency endpoints'),
    (lambda scene: scene['connections'].clear(), 'omitted supplied dependency'),
    (lambda scene: scene['connections'][0].pop('semantic_edge'), 'unbound scientific dependency'),
    (lambda scene: scene['objects'][0].update(evidence_refs=['fabricated_source']), 'actual supplied scientific identities'),
    (lambda scene: scene['objects'][0].update(bbox=[0.97, 0.2, 0.2, 0.2]), 'inside the canvas'),
    (lambda scene: scene['objects'][0].update(bbox=[0, 0, 0.2, 0.2]), 'declared panel'),
    (lambda scene: scene['objects'][1].update(detail='Process the input'), 'key mechanism'),
    (lambda scene: scene['panels'][0].update(role='overview'), 'required explanatory panel'),
])
def test_scientific_source_and_print_violations_fail(mutation, message):
    _, contract, scene = supplied()
    mutation(scene)
    with pytest.raises(ValueError, match=message):
        validate_scene(scene, contract)


def test_scene_revision_can_repair_an_invalid_candidate_without_changing_science():
    _, contract, scene = supplied()
    invalid = deepcopy(scene)
    invalid['connections'].clear()
    repaired = apply_scene_revision(invalid, {'connections': scene['connections']}, contract)
    assert repaired == scene
    assert invalid['connections'] == []
    altered = deepcopy(scene['connections'])
    altered[0]['label'] = 'Imagined improved information'
    with pytest.raises(ValueError, match='dependency label'):
        apply_scene_revision(scene, {'connections': altered}, contract)
    with pytest.raises(ValueError, match='presentation collections only'):
        apply_scene_revision(scene, {'width_in': 12}, contract)


def test_bound_numeric_claim_resolves_exact_measurement_and_preserves_source_scene():
    _, contract, scene = supplied()
    scene['annotations'] = [{
        'id': 'Result', 'panel': 'Mechanism', 'role': 'claim',
        'text': 'Accuracy: [[metric:accuracy]]', 'bbox': [0.06, 0.7, 0.8, 0.1],
        'evidence_refs': ['accuracy'], 'font_pt': 9,
    }]
    resolved = resolve_scene_metrics(scene, contract)
    assert resolved['annotations'][0]['text'] == 'Accuracy: 83.5 %'
    assert scene['annotations'][0]['text'] == 'Accuracy: [[metric:accuracy]]'
    scene['annotations'][0]['text'] = 'Accuracy: 99.9%'
    with pytest.raises(ValueError, match='bind numeric results'):
        validate_scene(scene, contract)


def test_unbound_metric_token_and_small_print_font_are_rejected():
    _, contract, scene = supplied()
    scene['annotations'] = [{
        'id': 'Metric', 'panel': 'Mechanism', 'role': 'note',
        'text': '[[metric:accuracy]]', 'bbox': [0.06, 0.7, 0.8, 0.1],
        'evidence_refs': ['context:method'],
    }]
    with pytest.raises(ValueError, match='unbound measurement token'):
        validate_scene(scene, contract)
    scene['annotations'][0].update(evidence_refs=['accuracy'], font_pt=7.5)
    with pytest.raises(ValueError, match='minimum type size'):
        validate_scene(scene, contract)


def test_observed_matrix_must_match_actual_bound_source_values():
    graph, _, scene = supplied()
    values = [[0.8, 0.2], [0.4, 0.6]]
    contract = build_figure_contract(graph, {'method': 'An observed weight matrix.', 'data': values})
    obj = scene['objects'][1]
    obj['params'] = {'rows': 2, 'cols': 2, 'values': values,
                     'schematic': False, 'source_ref': 'context:data'}
    obj['evidence_refs'].append('context:data')
    validate_scene(scene, contract)
    obj['params']['values'] = [[0.9, 0.1], [0.4, 0.6]]
    with pytest.raises(ValueError, match='exact supplied source matrix'):
        validate_scene(scene, contract)


def test_schematic_matrix_values_cannot_be_mislabeled_as_observations():
    _, contract, scene = supplied()
    scene['objects'][1]['params'] = {'rows': 2, 'cols': 2, 'values': [[1, 0], [1, 1]]}
    with pytest.raises(ValueError, match='explicitly schematic'):
        validate_scene(scene, contract)
    scene['objects'][1]['params']['schematic'] = True
    validate_scene(scene, contract)


def test_generated_component_asset_requires_a_grounded_declared_brief():
    _, contract, scene = supplied()
    scene['objects'][0].update(kind='asset', params={'asset_id': 'Molecule'})
    with pytest.raises(ValueError, match='no declared generation brief'):
        validate_scene(scene, contract)
    contract['assets'] = [{
        'id': 'Molecule', 'prompt': 'An isolated molecular structure with no labels, arrows or text.',
        'role': 'conceptual_illustration', 'evidence_refs': ['operation:Encode'],
    }]
    validate_scene(scene, contract)


def test_simple_explicit_method_request_does_not_require_an_invented_argument():
    graph, _, scene = supplied()
    graph['narrative_mode'] = 'method_only'
    contract = build_figure_contract(graph)
    scene['panels'][0]['role'] = 'overview'
    for obj in scene['objects']:
        obj.update(kind='operator', detail='', params={'formula': obj['label']})
    validate_scene(scene, contract)


def test_worker_context_explicit_method_mode_is_respected():
    graph, _, scene = supplied()
    contract = build_figure_contract(graph, {'narrative_mode': 'method_only'})
    assert contract['narrative_mode'] == 'method_only'
    scene['panels'][0]['role'] = 'overview'
    for obj in scene['objects']:
        obj.update(kind='operator', detail='', params={'formula': obj['label']})
    validate_scene(scene, contract)


def test_block_attention_mask_and_multiple_highlighted_cells_remain_typed():
    _, contract, scene = supplied()
    matrix = scene['objects'][1]
    matrix['params'] = {
        'rows': 2, 'cols': 2, 'mask_matrix': [[True, False], [True, True]],
        'highlight': [[0, 0], [1, 1]], 'schematic': True,
        'row_labels': ['query 1', 'query 2'], 'col_labels': ['key 1', 'key 2'],
    }
    validate_scene(scene, contract)
    matrix['params']['mask_matrix'][0][1] = 0
    with pytest.raises(ValueError, match='boolean rows and columns'):
        validate_scene(scene, contract)


def test_scientific_prompts_lock_claims_and_use_typed_editable_geometry():
    _, contract, _ = supplied()
    design, revision = design_instructions(contract), revision_instructions(contract)
    assert 'single strongest supported contribution' in design
    assert 'Do not hide a declared primary outcome' in design
    assert 'Development chronology' in design
    assert 'Preserve operation IDs and exact canonical labels' in revision
    assert scene_schema()['version'] == 1
    assert 'custom' in scene_schema()['parameters']


def test_label_only_overview_is_allowed_when_the_key_has_a_bound_native_zoom():
    _, contract, scene = supplied()
    scene['objects'][0].update(kind='operator', params={'formula': ''})
    overview = deepcopy(scene['objects'][1])
    overview.update(id='AttentionOverview', kind='operator', params={}, bbox=[0.58, 0.2, 0.3, 0.2])
    zoom = scene['objects'][1]
    zoom.update(id='AttentionZoom', bbox=[0.58, 0.52, 0.3, 0.35])
    scene['objects'].append(overview)
    scene['connections'][0]['target'] = 'AttentionOverview'
    scene['connections'].append({'id': 'ZoomLink', 'source': 'AttentionOverview',
                                 'target': 'AttentionZoom', 'label': ''})
    result = validate_scene(scene, contract)
    assert result['detailed_operations'] == ['Attend']
    scene['objects'].remove(zoom)
    scene['connections'].pop()
    with pytest.raises(ValueError, match='key mechanism'):
        validate_scene(scene, contract)


def test_generic_box_and_explanatory_prose_do_not_substitute_for_native_key_detail():
    _, contract, scene = supplied()
    scene['objects'][1].update(kind='custom', params={'primitives': [
        {'kind': 'rect', 'bbox': [0.1, 0.1, 0.8, 0.8]},
        {'kind': 'text', 'bbox': [0.2, 0.2, 0.6, 0.6], 'text': 'Causal attention'},
    ]})
    with pytest.raises(ValueError, match='key mechanism'):
        validate_scene(scene, contract)


def test_concise_aliases_preserve_canonical_scientific_labels_and_source_bindings():
    _, contract, scene = supplied()
    scene['objects'][0]['display_label'] = 'Encode'
    scene['connections'][0]['display_label'] = 'Q / K / V'
    result = validate_scene(scene, contract)
    assert result['display_aliases']['Encoding']['canonical_label'] == 'Input encoding'
    assert result['display_aliases']['Latents']['canonical_label'] == 'query / key / value'
    assert scene['connections'][0]['semantic_edge'] == 0
    scene['connections'][0]['label'] = 'query / value'
    with pytest.raises(ValueError, match='dependency label'):
        validate_scene(scene, contract)


@pytest.mark.parametrize('alias', [None, '', '   ', 'a' * 65, 'line1\nline2\nline3'])
def test_display_aliases_cannot_be_empty_or_expand_into_prose(alias):
    _, contract, scene = supplied()
    scene['objects'][0]['display_label'] = alias
    with pytest.raises(ValueError, match='concise nonempty text'):
        validate_scene(scene, contract)


def test_symbolic_attention_gates_are_schematic_not_observed_numeric_results():
    _, contract, scene = supplied()
    params = scene['objects'][1]['params']
    params.update(rows=2, cols=2, values=[['−∞', '0'], ['0', '−∞']],
                  mask_matrix=[[False, True], [True, False]])
    validate_scene(scene, contract)
    params['schematic'] = False
    with pytest.raises(ValueError, match='finite numbers'):
        validate_scene(scene, contract)
    params.update(schematic=True, values=[[0, 1], [1, 0]], cell_labels=[['−∞', '0'], ['0', '−∞']])
    validate_scene(scene, contract)
    params['cell_labels'][0].pop()
    with pytest.raises(ValueError, match='matching rows and columns'):
        validate_scene(scene, contract)


def test_native_panel_layout_can_only_pack_its_own_unique_objects():
    _, contract, scene = supplied()
    scene['panels'][0]['layout'] = {'flow': 'horizontal', 'object_ids': ['Encoding', 'Attention'],
                                  'gap_in': 0.25, 'padding_in': 0.1, 'routing_gutter_in': 0.2}
    validate_scene(scene, contract)
    scene['panels'][0]['layout']['object_ids'] = ['Encoding', 'Encoding']
    with pytest.raises(ValueError, match='actual unique objects'):
        validate_scene(scene, contract)


def test_exact_supplied_key_formula_needs_connected_native_scientific_detail():
    graph, _, scene = supplied()
    formula = 'softmax(A + QKᵀ)V + X'
    graph['nodes'][1]['display_transform'] = formula
    contract = build_figure_contract(graph)
    scene['objects'][1].update(kind='operator', params={'formula': formula})
    validate_scene(scene, contract)
    scene['objects'][1]['params']['formula'] = 'softmax(QKᵀ)V'
    with pytest.raises(ValueError, match='key mechanism'):
        validate_scene(scene, contract)
    scene['objects'][1]['params']['formula'] = formula
    scene['objects'][0].update(kind='operator', params={})
    with pytest.raises(ValueError, match='key mechanism'):
        validate_scene(scene, contract)


def test_supplied_canonical_dependency_label_and_legacy_short_label_are_both_preserved():
    graph, _, scene = supplied()
    graph['edges'][0]['display_label'] = 'Q / K / V'
    contract = build_figure_contract(graph)
    validate_scene(scene, contract)
    scene['connections'][0]['label'] = 'Q / K / V'
    validate_scene(scene, contract)
    scene['connections'][0].update(label='query / key / value', display_label='Q,K,V')
    validate_scene(scene, contract)
    assert contract['edges'][0]['label'] == 'query / key / value'


def test_unlabeled_arrow_preserves_scientific_binding_and_requires_semantic_review():
    _, contract, scene = supplied()
    original = deepcopy(scene['connections'][0])
    scene['connections'][0].update(label_visible=False,
        label_visibility_reason='The source glyph is explicitly marked Q, K and V at the outgoing port.')
    result = validate_scene(scene, contract)
    assert result['dependency_coverage'] == 1
    assert scene['connections'][0]['label'] == original['label']
    assert scene['connections'][0]['semantic_edge'] == original['semantic_edge']
    assert result['unlabeled_connections'][0]['canonical_label'] == original['label']
    assert 'Unlabeled arrows with ambiguous meaning' in design_instructions(contract)


@pytest.mark.parametrize('visible, reason', [
    (False, None), (False, ''), (False, '  '), (False, 'a' * 161),
    (0, 'Source and target are explicit.'), (1, None), ('false', 'Source and target are explicit.'),
])
def test_edge_label_visibility_is_a_strict_boolean_with_a_concise_reason(visible, reason):
    _, contract, scene = supplied()
    scene['connections'][0].update(label_visible=visible, label_visibility_reason=reason)
    with pytest.raises(ValueError, match='label_visible must be a boolean|concise nonempty label_visibility_reason'):
        validate_scene(scene, contract)


def test_metric_claims_and_operation_labels_cannot_be_hidden_to_solve_layout():
    graph, _, scene = supplied()
    graph['edges'][0].update(evidence_refs=['score'])
    contract = build_figure_contract(graph, {'evidence': {'metrics': [{'id': 'score', 'value': 0.8}]}})
    scene['connections'][0].update(label_visible=False,
                                  label_visibility_reason='The endpoints identify the transfer.')
    with pytest.raises(ValueError, match='metric claims must remain visible'):
        validate_scene(scene, contract)
    scene['connections'][0]['label_visible'] = True
    scene['objects'][0]['params']['label_visible'] = False
    with pytest.raises(ValueError, match='operation labels must remain visible'):
        validate_scene(scene, contract)


@pytest.mark.parametrize('title', [None, ''])
def test_optional_panel_title_can_be_null_or_empty_without_an_extra_header(title):
    _, contract, scene = supplied()
    scene['panels'][0]['title'] = title
    assert validate_scene(scene, contract)['operation_coverage'] == 2
    scene['panels'][0].pop('title')
    validate_scene(scene, contract)


@pytest.mark.parametrize('title', [True, False, 12])
def test_optional_panel_title_does_not_accept_boolean_or_numeric_values(title):
    _, contract, scene = supplied()
    scene['panels'][0]['title'] = title
    with pytest.raises(ValueError, match='panel title must be text'):
        validate_scene(scene, contract)


def test_generated_caption_retains_source_facts_and_resolves_actual_metric_tokens():
    _, contract, scene = supplied()
    original_source = deepcopy(contract['nodes'])
    scene['caption_text'] = ('Causal token aggregation. Encoded query, key and value representations '
                             'enter the causal attention mask. Accuracy is [[metric:accuracy]].')
    report = validate_scene(scene, contract)
    assert 'independent scientific source review' in report['caption_review']
    assert resolve_scene_metrics(scene, contract)['caption_text'].endswith('Accuracy is 83.5 %.')
    assert contract['nodes'] == original_source
    assert scene['caption_text'].endswith('[[metric:accuracy]].')
    scene['caption_text'] = 'Accuracy is [[metric:invented_result]].'
    with pytest.raises(ValueError, match='unbound measurement token'):
        validate_scene(scene, contract)


@pytest.mark.parametrize('caption', [None, True, '', '   ', 'word ' * 181, 'x' * 1401])
def test_optional_generated_caption_must_be_nonempty_and_bounded(caption):
    _, contract, scene = supplied()
    scene['caption_text'] = caption
    with pytest.raises(ValueError, match='caption_text must be nonempty text'):
        validate_scene(scene, contract)


def test_layout_grounded_caption_patch_preserves_the_frozen_scientific_contract():
    _, contract, scene = supplied()
    scene['caption_text'] = 'Encoded token representations enter the causal attention mechanism.'
    source_fingerprint = contract['source_fingerprint']
    revised = apply_scene_revision(scene, {'caption_text': (
        'Encoded token representations enter the causal attention mechanism; '
        'the triangular cells denote permitted positions.')}, contract)
    assert 'triangular cells' in revised['caption_text']
    assert 'triangular cells' not in scene['caption_text']
    assert contract['source_fingerprint'] == source_fingerprint
    assert revised['connections'] == scene['connections']
    assert 'Caption edits receive new independent source review' in revision_instructions(contract)
