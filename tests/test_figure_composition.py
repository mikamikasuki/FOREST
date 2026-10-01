from copy import deepcopy
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image
from pypdf import PdfReader

from research.figures.composition import (FigureCompositionCapacityError, compile_composition,
                                         composition_schema, layout_geometry_guidance, validate_composition)
from research.figures.scene import render_scene
from research.figures.spec import build_figure_contract, validate_scene


def _source(names=None, links=None):
    names = names or ['Input features', 'Gated update', 'Output states']
    nodes = [{'id': 'stage' + str(index), 'label': name} for index, name in enumerate(names)]
    links = links or [(0, 1), (1, 2)]
    return build_figure_contract({
        'narrative_mode': 'method_only', 'nodes': nodes,
        'edges': [{'source': nodes[a]['id'], 'target': nodes[b]['id']} for a, b in links],
        'key_operation_id': 'stage1'},
        style={'layout_width_in': 6.5, 'height': 4.4, 'font_size': 9})


def _composition(position='top'):
    return {'version': 1, 'overview_position': position,
            'hero': {'kind': 'custom', 'detail': 'The supplied gate selects inputs before updating the output states.',
                     'evidence_refs': ['operation:stage1'], 'params': {'primitives': [
                         {'kind': 'rect', 'bbox': [.05, .22, .24, .50]},
                         {'kind': 'rect', 'bbox': [.39, .22, .22, .50]},
                         {'kind': 'rect', 'bbox': [.71, .22, .24, .50]},
                         {'kind': 'arrow', 'points': [[.29, .47], [.39, .47]]},
                         {'kind': 'arrow', 'points': [[.61, .47], [.71, .47]]},
                         {'kind': 'text', 'bbox': [.06, .31, .22, .25], 'text': 'Inputs'},
                         {'kind': 'text', 'bbox': [.40, .31, .20, .25], 'text': 'Gate'},
                         {'kind': 'text', 'bbox': [.72, .31, .22, .25], 'text': 'Outputs'},
                     ]}}}


@pytest.mark.parametrize('position', ['top', 'left'])
def test_host_compiles_complete_source_graph_and_dominant_local_hero(position):
    contract, composition = _source(), _composition(position)
    original_contract, original_design = deepcopy(contract), deepcopy(composition)
    compiled = compile_composition(composition, contract)
    assert contract == original_contract and composition == original_design
    assert (compiled['width_in'], compiled['height_in']) == (6.5, 4.4)
    assert validate_scene(compiled, contract)['dependency_coverage'] == 2
    overview, mechanism = compiled['panels']
    area = lambda panel: panel['bbox'][2] * panel['bbox'][3]
    assert area(mechanism) / (area(overview) + area(mechanism)) >= .50 - 1e-9
    actual = {item['operation_id']: item for item in compiled['objects'] if item['panel'] == 'overview'}
    assert set(actual) == {node['id'] for node in contract['nodes']}
    for node in contract['nodes']:
        assert actual[node['id']]['id'] == 'overview_' + node['id']
        assert actual[node['id']]['label'] == node['label']
        assert actual[node['id']]['kind'] == 'operator'
        assert actual[node['id']]['evidence_refs'] == ['operation:' + node['id']]
    for index, edge in enumerate(contract['edges']):
        connection = compiled['connections'][index]
        assert connection['semantic_edge'] == index
        assert connection['source'] == 'overview_' + edge['source']
        assert connection['target'] == 'overview_' + edge['target']
        assert connection['label'] == edge.get('label', '')
    assert compiled['objects'][-1]['operation_id'] == contract['key_operation_id']
    assert compile_composition(composition, contract) == compiled


def test_compiler_exports_native_source_grounded_svg_pdf_and_png(tmp_path):
    contract, composition = _source(), _composition()
    composition.update(title='Gated representation update', caption_text='The gate selects the supplied inputs.')
    scene = compile_composition(composition, contract)
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert report['minimum_font_pt'] >= 9
    assert report['coverage']['dependency_coverage'] == 2
    root = ElementTree.parse(outputs['svg'])
    assert not root.findall('.//{http://www.w3.org/2000/svg}image')
    groups = {element.get('id') for element in root.iter()}
    assert 'overview_stage0-operator' in groups
    assert 'hero_stage1-custom-rect' in groups
    assert 'dependency_0-arrowhead' in groups
    assert Image.open(outputs['png']).size == (1950, 1320)
    page = PdfReader(outputs['pdf']).pages[0]
    assert float(page.mediabox.width) / 72 == pytest.approx(6.5)
    assert float(page.mediabox.height) / 72 == pytest.approx(4.4)
    assert 'Gated update' in page.extract_text()
    saved = json.loads(Path(outputs['scene']).read_text())
    assert saved['caption_text'] == composition['caption_text']
    assert saved['objects'][-1]['params'] == composition['hero']['params']


def test_dense_cyclic_multi_input_overview_preserves_every_real_dependency(tmp_path):
    contract = _source(['Inputs', 'Gate', 'Memory', 'Context', 'Update', 'Outputs'],
                       [(0, 1), (2, 1), (1, 4), (3, 4), (4, 1), (4, 5), (0, 3), (2, 3)])
    scene = compile_composition(_composition(), contract)
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert {edge['semantic_edge'] for edge in report['connections']} == set(range(8))
    assert all(edge['obstacle_free'] for edge in report['connections'])


@pytest.mark.parametrize('position', ['top', 'left'])
def test_short_necessary_visible_transfer_gets_a_measured_label_corridor(tmp_path, position):
    contract = _source(['Encode', 'Attend'], [(0, 1)])
    contract['edges'][0]['label'] = 'Q, K, V'
    composition = _composition(position)
    scene = compile_composition(composition, contract)
    assert scene['connections'][0].get('label_visible', True)
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    edge = report['connections'][0]
    assert edge['label_visible'] and edge['label_bbox_in']
    assert edge['label_text'] == 'Q, K, V'
    assert 'Q, K, V' in ' '.join(PdfReader(outputs['pdf']).pages[0].extract_text().split())


def test_aliases_and_explicit_redundant_label_reason_keep_canonical_source():
    contract = _source()
    contract['edges'][0]['label'] = 'Input features enter the gate before the state update'
    composition = _composition()
    composition['display_aliases'] = {'stage1': 'Gate'}
    composition['edge_aliases'] = {'0': {'display_label': 'Inputs', 'label_visible': False,
        'label_visibility_reason': 'The direct arrow enters the visibly labeled gate from the input features.'}}
    scene = compile_composition(composition, contract)
    assert scene['connections'][0]['label'] == contract['edges'][0]['label']
    assert scene['connections'][0]['label_visible'] is False
    assert scene['objects'][1]['label'] == 'Gated update'
    assert scene['objects'][1]['display_label'] == 'Gate'
    assert scene['objects'][-1]['display_label'] == 'Gate'
    composition['edge_aliases']['0'].pop('label_visibility_reason')
    with pytest.raises(ValueError, match='reason'):
        validate_composition(composition, contract)


@pytest.mark.parametrize('mutation,match', [
    (lambda value: value.update(objects=[]), 'host-owned'),
    (lambda value: value.update(width_in=12), 'host-owned'),
    (lambda value: value['hero'].update(operation_id='stage0'), 'host-owned'),
    (lambda value: value['hero'].update(bbox=[0, 0, 1, 1]), 'host-owned'),
    (lambda value: value['hero'].update(evidence_refs=['invented-source']), 'actual source'),
    (lambda value: value['hero']['params'].update(connections=[]), 'host-owned'),
    (lambda value: value.update(display_aliases={'invented': 'Input'}), 'actual supplied'),
    (lambda value: value.update(edge_aliases={'03': {}}), 'zero-based'),
    (lambda value: value.update(edge_aliases={'-1': {}}), 'zero-based'),
    (lambda value: value.update(edge_aliases={'99': {}}), 'actual supplied'),
])
def test_design_cannot_replace_global_geometry_graph_or_source_identity(mutation, match):
    composition = _composition()
    mutation(composition)
    with pytest.raises(ValueError, match=match):
        compile_composition(composition, _source())


def test_hero_rejects_low_font_flat_boxes_and_observed_matrix_invention():
    composition = _composition()
    composition['hero']['params']['primitives'][-1]['font_pt'] = 7
    with pytest.raises(ValueError, match='font|smaller'):
        validate_composition(composition, _source())
    composition['hero'].update(kind='operator', params={'formula': 'Inputs → outputs'})
    with pytest.raises(ValueError, match='substantively'):
        validate_composition(composition, _source())
    composition['hero'].update(kind='matrix', params={'rows': 2, 'cols': 2,
        'values': [[1, 0], [0, 1]], 'schematic': False, 'source_ref': 'operation:stage1'})
    with pytest.raises(ValueError, match='exact supplied source matrix'):
        validate_composition(composition, _source())


def test_impossible_local_text_produces_fit_error_without_shrinking_the_font():
    composition = _composition()
    composition['hero']['params']['primitives'][-1]['bbox'] = [.72, .31, .02, .02]
    with pytest.raises(ValueError, match='fit'):
        compile_composition(composition, _source())


def test_schema_requests_local_design_and_assigns_global_ownership_to_host():
    schema = composition_schema()
    assert 'hero' in schema and 'ownership' in schema
    assert not {'objects', 'connections', 'width_in', 'height_in'} & set(schema)


def test_optional_local_primitive_ids_locate_repairs_without_global_source_bindings(tmp_path):
    composition = _composition()
    for index, primitive in enumerate(composition['hero']['params']['primitives']):
        primitive['id'] = 'local_' + str(index)
    original = deepcopy(composition)
    contract = _source()
    scene = compile_composition(composition, contract)
    assert composition == original
    assert scene['objects'][-1]['params'] == composition['hero']['params']
    assert {edge['source'] for edge in scene['connections']} == {'overview_stage0', 'overview_stage1'}
    assert not any(edge['source'].startswith('local_') for edge in scene['connections'])
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    saved = json.loads(Path(outputs['scene']).read_text())
    assert saved['objects'][-1]['params']['primitives'][0]['id'] == 'local_0'


@pytest.mark.parametrize('identifier', [None, 0, '', '0bad', 'operation:stage1', 'local/path', 'x' * 81])
def test_local_primitive_id_requires_a_bounded_simple_identifier(identifier):
    composition = _composition()
    composition['hero']['params']['primitives'][0]['id'] = identifier
    with pytest.raises(ValueError, match='Local primitive IDs'):
        validate_composition(composition, _source())


def test_local_primitive_ids_are_unique_and_cannot_add_global_dependency_fields():
    composition = _composition()
    composition['hero']['params']['primitives'][0]['id'] = 'local_mask'
    composition['hero']['params']['primitives'][1]['id'] = 'local_mask'
    with pytest.raises(ValueError, match='unique simple'):
        validate_composition(composition, _source())
    composition['hero']['params']['primitives'][1]['id'] = 'local_update'
    composition['hero']['params']['primitives'][1]['operation_id'] = 'stage0'
    with pytest.raises(ValueError, match='host-owned'):
        validate_composition(composition, _source())


def test_capacity_feedback_identifies_the_local_text_box_and_native_wrapped_height(tmp_path):
    contract, composition = _source(), _composition()
    primitive = composition['hero']['params']['primitives'][-1]
    primitive.update(id='output_readout', text='Input\nGated\nOutput\nStates', font_pt=9,
                     bbox=[.72, .31, .22, .04])
    original_contract, original_design = deepcopy(contract), deepcopy(composition)
    with pytest.raises(FigureCompositionCapacityError) as captured:
        compile_composition(composition, contract)
    feedback = captured.value.layout_feedback
    tight = feedback['tightest_primitive']
    assert tight['primitive_id'] == 'output_readout'
    assert tight['bbox'] == primitive['bbox']
    assert tight['wrapped_text'] == primitive['text']
    assert tight['wrapped_lines'] == 4 and tight['font_pt'] == 9
    assert tight['required_text_height_in'] > tight['available_text_height_in']
    assert tight['minimum_bbox_height_fraction'] > primitive['bbox'][3]
    assert tight['required_text_height_in'] == pytest.approx(
        tight['minimum_bbox_height_fraction'] * feedback['available_body_height_in'], abs=2e-6)
    assert tight['minimum_text_width_in'] == pytest.approx(
        tight['minimum_bbox_width_fraction'] * feedback['available_body_width_in'], abs=4e-6)
    assert feedback['available_body_width_in'] > 0 and feedback['available_body_height_in'] > 0
    assert 'output_readout' in str(captured.value) and 'local_fit_feedback=' in str(captured.value)
    assert composition == original_design and contract == original_contract
    # A local box repair preserves the text, point size and all source identities.
    primitive['bbox'] = [.72, .17, .22, .60]
    scene = compile_composition(composition, contract)
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert scene['objects'][-1]['params']['primitives'][-1]['text'] == primitive['text']
    assert scene['objects'][-1]['params']['primitives'][-1]['font_pt'] == 9


def test_capacity_feedback_measures_wrapping_at_the_actual_local_body_width():
    composition = _composition()
    primitive = composition['hero']['params']['primitives'][-1]
    primitive.update(id='narrow_readout', text='Input gated output states',
                     bbox=[.72, .31, .04, .04])
    with pytest.raises(FigureCompositionCapacityError) as captured:
        compile_composition(composition, _source())
    feedback = captured.value.layout_feedback
    tight = feedback['tightest_primitive']
    assert tight['primitive_id'] == 'narrow_readout'
    assert tight['wrapped_lines'] >= 3 and '\n' in tight['wrapped_text']
    assert tight['available_text_width_in'] == pytest.approx(
        feedback['available_body_width_in'] * primitive['bbox'][2], abs=1e-6)
    assert tight['minimum_bbox_width_fraction'] > primitive['bbox'][2]


def test_authoring_guidance_reserves_title_header_and_source_operation_label_space():
    contract = _source()
    original = deepcopy(contract)
    guidance = layout_geometry_guidance(contract)
    top = guidance['hero_body']['top']; left = guidance['hero_body']['left']
    for orientation in (top, left):
        plain, titled = orientation['without_figure_title'], orientation['with_figure_title']
        assert plain['available_body_height_in'] > titled['available_body_height_in']
        assert plain['operation_label_height_in'] > 0
        assert plain['available_body_height_in'] < plain['object_height_in']
    assert top['without_figure_title']['available_body_width_in'] > left['without_figure_title']['available_body_width_in']
    assert top['without_figure_title']['available_body_height_in'] < left['without_figure_title']['available_body_height_in']
    assert guidance['required_text_height_in']['9.0']['1'] >= .19
    assert guidance['required_text_height_in']['10.0']['1'] >= .21
    assert contract == original


@pytest.mark.parametrize('position', ['top', 'left'])
def test_small_complete_overview_allocates_its_spare_area_to_the_hero(position):
    contract, composition = _source(), _composition(position)
    original = deepcopy(composition)
    scene = compile_composition(composition, contract)
    overview, mechanism = scene['panels']
    area = lambda panel: panel['bbox'][2] * panel['bbox'][3]
    assert .25 - 1e-9 <= area(overview) / (area(overview) + area(mechanism)) <= .50 + 1e-9
    assert scene['objects'][-1]['params'] == composition['hero']['params']
    assert composition == original
    assert len(scene['connections']) == len(contract['edges'])


def test_compact_overview_frees_width_for_a_hero_that_exceeds_conservative_authoring_width(tmp_path):
    contract, composition = _source(), _composition('left')
    primitive = composition['hero']['params']['primitives'][-1]
    primitive.update(text='Gated', bbox=[.72, .31, .10, .25], font_pt=9, id='gated_readout')
    original_contract, original_design = deepcopy(contract), deepcopy(composition)
    conservative = layout_geometry_guidance(contract)['hero_body']['left']['without_figure_title']
    scene = compile_composition(composition, contract)
    hero = scene['objects'][-1]
    assert hero['bbox'][2] * contract['width_in'] - .05 > conservative['available_body_width_in']
    assert hero['params'] == composition['hero']['params']
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert composition == original_design and contract == original_contract
    assert report['minimum_font_pt'] >= 9


@pytest.mark.parametrize('mutation,match', [
    (lambda plan: plan.update(overview_chain=['stage0', 'stage2']), 'actual directed'),
    (lambda plan: plan.update(overview_chain=['stage0', 'stage1', 'stage0']), 'unique actual'),
    (lambda plan: plan.update(overview_chain=['invented']), 'actual supplied'),
    (lambda plan: plan.update(overview_groups=[{'id': 'stages', 'operation_ids': ['stage0', 'stage1'], 'flow': 'TB'}]), 'every supplied'),
    (lambda plan: plan.update(overview_groups=[{'id': 'stages', 'operation_ids': ['stage0', 'stage1', 'stage2', 'stage1'], 'flow': 'TB'}]), 'exactly once'),
    (lambda plan: plan.update(overview_groups=[{'id': 'stages', 'operation_ids': ['stage0', 'stage1', 'invented'], 'flow': 'TB'}]), 'actual supplied'),
    (lambda plan: plan.update(overview_groups=[{'id': 'stages', 'operation_ids': ['stage0', 'stage1', 'stage2'], 'flow': 'snake'}]), 'LR or TB'),
    (lambda plan: plan.update(overview_glyphs={'invented': {'kind': 'tensor', 'params': {'shape': ['N', 'D']}}}), 'actual supplied'),
    (lambda plan: plan.update(overview_glyphs={'stage0': {'kind': 'tensor', 'params': {'shape': ['N', 'D']}}}), 'source-grounded'),
    (lambda plan: plan.update(overview_glyphs={'stage0': {'kind': 'operator', 'params': {}, 'bbox': [0, 0, 1, 1]}}), 'host-owned'),
])
def test_semantic_overview_hints_are_bounded_and_source_bound(mutation, match):
    plan = _composition()
    mutation(plan)
    with pytest.raises(ValueError, match=match):
        compile_composition(plan, _source())


def test_noncontiguous_grouping_cannot_reclassify_real_forward_edges_as_feedback():
    plan = _composition()
    plan['overview_groups'] = [{'id': 'outside', 'operation_ids': ['stage0', 'stage2'], 'flow': 'TB'},
                               {'id': 'middle', 'operation_ids': ['stage1'], 'flow': 'TB'}]
    with pytest.raises(ValueError, match='forward dependency cycle'):
        compile_composition(plan, _source())


def test_directed_ranks_never_swap_stages_to_shorten_the_wires(tmp_path):
    contract = _source(['Inputs', 'Gate', 'Memory', 'Context', 'Outputs'], [(0, 1), (1, 2), (2, 3), (3, 4), (0, 4)])
    plan = _composition()
    plan['overview_chain'] = ['stage0', 'stage1', 'stage2', 'stage3', 'stage4']
    plan['overview_groups'] = [{'id': 'group' + str(index), 'operation_ids': ['stage' + str(index)], 'flow': 'LR'}
                               for index in range(5)]
    scene = compile_composition(plan, contract)
    actual = {item['operation_id']: item for item in scene['objects'] if item['panel'] == 'overview'}
    assert all(actual['stage' + str(index)]['bbox'][0] < actual['stage' + str(index + 1)]['bbox'][0]
               for index in range(4))
    assert all(edge['from_port'] == 'right' and edge['to_port'] == 'left' for edge in scene['connections'])
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert report['coverage']['dependency_coverage'] == 5


def test_chain_protects_true_forward_edges_inside_a_legitimate_cycle(tmp_path):
    contract = _source(['Inputs', 'Gate', 'Memory'], [(0, 2), (2, 1), (1, 0)])
    plan = _composition()
    plan['overview_chain'] = ['stage0', 'stage2', 'stage1']
    plan['overview_groups'] = [{'id': 'cycle', 'operation_ids': ['stage0', 'stage2', 'stage1'], 'flow': 'TB'}]
    scene = compile_composition(plan, contract)
    actual = {item['operation_id']: item for item in scene['objects'] if item['panel'] == 'overview'}
    assert actual['stage0']['bbox'][1] < actual['stage2']['bbox'][1] < actual['stage1']['bbox'][1]
    edges = {edge['semantic_edge']: edge for edge in scene['connections']}
    assert edges[0]['from_port'] == edges[1]['from_port'] == 'bottom'
    assert edges[2]['source'] == 'overview_stage1' and edges[2]['target'] == 'overview_stage0'
    assert edges[2]['waypoints']
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert len(report['connections']) == 3


def test_overview_native_glyph_preserves_canonical_identity_and_catalog_refs(tmp_path):
    contract, plan = _source(), _composition()
    plan['overview_glyphs'] = {'stage0': {'kind': 'tensor', 'params': {'shape': [3, 2], 'axis_labels': ['N', 'D']},
        'detail': 'The supplied input feature states feed the gated representation update.',
        'evidence_refs': ['operation:stage0']}}
    original_contract, original_plan = deepcopy(contract), deepcopy(plan)
    scene = compile_composition(plan, contract)
    item = next(item for item in scene['objects'] if item['id'] == 'overview_stage0')
    assert item['kind'] == 'tensor' and item['operation_id'] == 'stage0'
    assert item['label'] == contract['nodes'][0]['label']
    assert item['evidence_refs'] == ['operation:stage0']
    assert item['params'] == plan['overview_glyphs']['stage0']['params']
    assert contract == original_contract and plan == original_plan
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert 'overview_stage0-tensor-layer' in Path(outputs['svg']).read_text()


def test_host_reserves_exact_native_edge_labels_before_routing(tmp_path):
    contract = _source(['Encode', 'Attend'], [(0, 1)])
    contract['edges'][0]['label'] = 'Q, K, V'
    scene = compile_composition(_composition(), contract)
    assert scene['connections'][0]['label_bbox']
    assert scene['connections'][0]['waypoints']
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    actual = report['connections'][0]
    assert actual['label_preallocated'] is True
    assert actual['mandatory_waypoints'] > 0
    assert actual['label_text'] == contract['edges'][0]['label']


def test_guidance_reports_all_actual_overview_fraction_capacities():
    guidance = layout_geometry_guidance(_source())
    assert guidance['overview_fraction_candidates'] == [.25, .30, .35, .40, .45, .50]
    sizes = guidance['hero_body_by_overview_fraction']
    assert set(sizes) == {'0.25', '0.3', '0.35', '0.4', '0.45', '0.5'}
    assert sizes['0.35']['left']['without_figure_title']['available_body_width_in'] > sizes['0.5']['left']['without_figure_title']['available_body_width_in']
    assert sizes['0.35']['top']['without_figure_title']['available_body_height_in'] > sizes['0.5']['top']['without_figure_title']['available_body_height_in']
    assert guidance['hero_body'] == sizes['0.5']


def test_five_real_inputs_get_distinct_measured_native_arrival_ports(tmp_path):
    contract = _source(['A', 'Gate', 'B', 'C', 'D', 'E', 'Out'],
                       [(0, 1), (2, 1), (3, 1), (4, 1), (5, 1), (1, 6)])
    plan = _composition()
    plan['overview_groups'] = [
        {'id': 'inputs', 'operation_ids': ['stage0', 'stage2', 'stage3', 'stage4', 'stage5'], 'flow': 'LR'},
        {'id': 'gate', 'operation_ids': ['stage1'], 'flow': 'LR'},
        {'id': 'output', 'operation_ids': ['stage6'], 'flow': 'LR'}]
    plan['overview_chain'] = ['stage0', 'stage1', 'stage6']
    scene = compile_composition(plan, contract)
    gate = next(item for item in scene['objects'] if item['id'] == 'overview_stage1')
    assert gate['bbox'][3] * contract['height_in'] >= .34 - 1e-9
    inputs = [edge for edge in scene['connections'] if edge['target'] == gate['id']]
    assert len({edge['to_port_offset_in'] for edge in inputs}) == 5
    assert {edge['to_port'] for edge in inputs} == {'left'}
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert len(report['connections']) == 6
    assert len({edge['to_port_offset_in'] for edge in report['connections'] if edge['target'] == gate['id']}) == 5


@pytest.mark.parametrize('position', ['top', 'left'])
def test_true_self_dependency_has_a_visible_native_return_arrow(tmp_path, position):
    contract = _source(['Input', 'Gate'], [(0, 1), (1, 1)])
    scene = compile_composition(_composition(position), contract)
    outputs = render_scene(tmp_path, {'production_contract': contract, 'production_scene': scene})
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'], report['quality_issues']
    assert {edge['semantic_edge'] for edge in report['connections']} == {0, 1}
    assert 'dependency_1-arrowhead' in Path(outputs['svg']).read_text()
