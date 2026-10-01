import base64
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import pytest
from PIL import Image
from pypdf import PdfReader

from research.figures.scene import _connection_route, _route, _segment_clear, _wire_penalty, layout_scene, measure_edge_label, measure_scene_layout, render_scene
from research.figures.spec import build_figure_contract


def _input():
    nodes = [{'id': 'image', 'label': 'Encoded image'},
             {'id': 'attention', 'label': 'Query attention'},
             {'id': 'tokens', 'label': 'Output tokens'}]
    graph = {'nodes': nodes, 'edges': [{'source': 'image', 'target': 'attention'},
                                      {'source': 'attention', 'target': 'tokens'}],
             'narrative_mode': 'method_only'}
    contract = build_figure_contract(graph, style={'layout_width_in': 6.5, 'height': 3.6, 'font_size': 9})
    objects = [
        {'id': 'visual', 'panel': 'main', 'operation_id': 'image', 'kind': 'tensor',
         'label': 'Encoded image', 'bbox': [.06, .27, .21, .50], 'params': {'shape': [4, 4, 3]},
         'evidence_refs': ['operation:image']},
        {'id': 'queries', 'panel': 'main', 'operation_id': 'attention', 'kind': 'matrix',
         'label': 'Query attention', 'bbox': [.39, .27, .21, .50],
         'params': {'rows': 4, 'cols': 4, 'mask': 'causal', 'schematic': True},
         'evidence_refs': ['operation:attention']},
        {'id': 'text', 'panel': 'main', 'operation_id': 'tokens', 'kind': 'tokens',
         'label': 'Output tokens', 'bbox': [.72, .27, .21, .50],
         'params': {'items': ['x₁', 'x₂', 'x₃']}, 'evidence_refs': ['operation:tokens']},
    ]
    scene = {'version': 1, 'width_in': 6.5, 'height_in': 3.6,
             'panels': [{'id': 'main', 'role': 'mechanism', 'title': 'Source-bound query mechanism',
                         'bbox': [.02, .02, .96, .96]}],
             'objects': objects, 'connections': [
                 {'id': 'visual_query', 'source': 'visual', 'target': 'queries', 'semantic_edge': 0,
                  'label': '', 'from_port': 'auto', 'to_port': 'auto'},
                 {'id': 'query_text', 'source': 'queries', 'target': 'text', 'semantic_edge': 1, 'label': ''}],
             'annotations': []}
    return {'production_contract': contract, 'production_scene': scene}


def _report(outputs):
    return json.loads(Path(outputs['report']).read_text())


def test_real_scene_exports_editable_svg_and_exact_print_dimensions(tmp_path):
    outputs = render_scene(tmp_path, _input())
    report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    assert report['minimum_font_pt'] >= 9
    assert report['native_shape_elements'] > 45
    assert report['native_text_elements'] >= 8
    svg = ElementTree.parse(outputs['svg'])
    groups = {element.attrib.get('id') for element in svg.iter()}
    assert 'visual-tensor-layer' in groups
    assert 'queries-matrix-cell' in groups
    assert 'text-token-text' in groups
    assert not svg.findall('.//{http://www.w3.org/2000/svg}image')
    assert Image.open(outputs['png']).size == (1950, 1080)
    page = PdfReader(outputs['pdf']).pages[0]
    assert float(page.mediabox.width) / 72 == pytest.approx(6.5)
    assert float(page.mediabox.height) / 72 == pytest.approx(3.6)
    assert 'Encoded image' in page.extract_text()
    assert Path(outputs['svg']).read_bytes().startswith(b'<?xml')
    assert Path(outputs['pdf']).read_bytes().startswith(b'%PDF-')


def test_exported_source_rerenders_from_saved_contract_and_scene(tmp_path):
    outputs = render_scene(tmp_path, _input())
    Path(outputs['png']).unlink()
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, outputs['source']], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert Image.open(outputs['png']).size == (1950, 1080)
    assert _report(outputs)['geometry_passed']
    assert json.loads(Path(outputs['data']).read_text())['production_contract']['nodes'][0]['label'] == 'Encoded image'


def test_layout_caption_is_saved_separately_from_original_source_caption(tmp_path):
    data = _input()
    source_caption = 'Left: image encoding. Right: query-conditioned output tokens.'
    actual_caption = 'Encoded image features pass through query attention to produce output tokens.'
    data['production_contract']['source_context']['caption'] = source_caption
    data['production_scene']['caption_text'] = actual_caption
    outputs = render_scene(tmp_path, data)
    companion = json.loads(Path(outputs['caption_context']).read_text())
    assert companion['caption_text'] == actual_caption
    assert companion['source_caption'] == source_caption
    assert json.loads(Path(outputs['scene']).read_text())['caption_text'] == actual_caption
    assert json.loads(Path(outputs['data']).read_text())['production_scene']['caption_text'] == actual_caption
    assert actual_caption not in PdfReader(outputs['pdf']).pages[0].extract_text()
    assert _report(outputs)['geometry_passed']


def test_missing_layout_caption_falls_back_to_source_caption(tmp_path):
    data = _input()
    source_caption = 'Encoded image features feed query attention and output tokens.'
    data['production_contract']['source_context']['caption'] = source_caption
    outputs = render_scene(tmp_path, data)
    companion = json.loads(Path(outputs['caption_context']).read_text())
    assert companion['caption_text'] == source_caption
    assert companion['source_caption'] == source_caption


@pytest.mark.parametrize('title', [None, 'omitted'])
def test_untitled_panel_packs_and_exports_without_native_header_text(tmp_path, title):
    data = _input()
    panel = data['production_scene']['panels'][0]
    if title == 'omitted':
        panel.pop('title')
    else:
        panel['title'] = title
    panel['header_bbox'] = [.03, .03, .90, .08]
    panel['layout'] = {'flow': 'horizontal', 'object_ids': ['visual', 'queries', 'text']}
    derived = layout_scene(data['production_scene'], data['production_contract'])
    assert 'header_bbox' not in derived['panels'][0]
    outputs = render_scene(tmp_path, data)
    report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    assert all(record['role'] != 'panel-title' for record in report['text_measurements'])
    tree = ElementTree.parse(outputs['svg'])
    assert not any(element.attrib.get('id') == 'main-panel-title' for element in tree.iter())
    native_text = [''.join(element.itertext()) for element in tree.findall('.//{http://www.w3.org/2000/svg}text')]
    assert 'None' not in native_text
    assert 'Encoded image' in PdfReader(outputs['pdf']).pages[0].extract_text()


def test_routing_goes_around_real_obstacle_with_declared_ports():
    obstacle = (2., .8, 1., 2.)
    path = _route((.5, 1.8), (4.5, 1.8), [obstacle], 5., 3.6)
    assert path and len(path) >= 4
    assert all(_segment_clear(a, b, [obstacle]) for a, b in zip(path[:-1], path[1:]))
    assert any(point[1] <= .8 or point[1] >= 2.8 for point in path)


def test_panel_confined_routing_takes_internal_corridor_instead_of_external_shortcut():
    bounds = (.3, 1.8, 4.6, 1.4)
    obstacle = (2., 1.6, 1., 1.2)
    unrestricted = _route((.6, 2.1), (4.5, 2.1), [obstacle], 5.2, 3.6)
    assert unrestricted and any(point[1] < bounds[1] for point in unrestricted)
    confined = _route((.6, 2.1), (4.5, 2.1), [obstacle], 5.2, 3.6, bounds=bounds)
    assert confined
    assert all(bounds[0] <= x <= bounds[0] + bounds[2]
               and bounds[1] <= y <= bounds[1] + bounds[3] for x, y in confined)
    assert all(_segment_clear(a, b, [obstacle]) for a, b in zip(confined[:-1], confined[1:]))
    assert any(point[1] >= 2.8 for point in confined)


def test_panel_barrier_returns_unroutable_instead_of_escaping_into_another_panel():
    bounds = (.3, 1.8, 4.6, 1.4)
    barrier = (2., 1.6, 1., 1.8)
    assert _route((.6, 2.1), (4.5, 2.1), [barrier], 5.2, 3.6)
    assert _route((.6, 2.1), (4.5, 2.1), [barrier], 5.2, 3.6, bounds=bounds) is None


def test_native_routes_report_shared_panel_bounds_and_keep_cross_panel_dependency(tmp_path):
    data = _input()
    scene = data['production_scene']
    scene['panels'][0]['bbox'] = [.02, .02, .63, .96]
    scene['panels'].append({'id': 'readout', 'role': 'detail', 'title': None,
                            'bbox': [.67, .02, .31, .96]})
    scene['objects'][2]['panel'] = 'readout'
    outputs = render_scene(tmp_path, data)
    report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    assert len(report['connections']) == 2
    same_panel, cross_panel = report['connections']
    assert same_panel['routing_panel'] == 'main'
    x, y, w, h = same_panel['routing_bounds_in']
    assert all(x <= px <= x + w and y <= py <= y + h for px, py in same_panel['path_in'])
    assert cross_panel['routing_panel'] is None
    assert cross_panel['routing_bounds_in'] is None
    assert cross_panel['source'] == 'queries' and cross_panel['target'] == 'text'
    assert cross_panel['semantic_edge'] == 1
    groups = {element.attrib.get('id') for element in ElementTree.parse(outputs['svg']).iter()}
    assert 'query_text-arrowhead' in groups


def test_native_edge_label_cannot_use_space_outside_its_shared_panel(tmp_path):
    data = _input()
    scene = data['production_scene']
    scene['panels'][0].update(title=None, bbox=[.02, .40, .96, .12])
    for item in scene['objects']:
        item.update(kind='operator', params={'shape': 'capsule'})
        item['bbox'][1] = .415
        item['bbox'][3] = .08
    scene['connections'][0]['label'] = 'Q, K, V'
    data['production_contract']['edges'][0]['label'] = 'Q, K, V'
    report = _report(render_scene(tmp_path, data))
    assert not report['geometry_passed']
    issue = next(issue for issue in report['quality_issues']
                 if issue['id'] == 'visual_query' and issue['code'] == 'edge_label_unplaceable')
    assert issue['routing_panel'] == 'main'
    assert issue['routing_bounds_in']
    edge = report['connections'][0]
    assert edge['canonical_label'] == 'Q, K, V'
    assert edge['label_bbox_in'] is None
    assert len(report['connections']) == 2
    assert report['coverage']['dependency_coverage'] == 2


def test_host_measures_prose_breaks_without_changing_math_or_punctuation():
    text = 'Image-conditioned query/token transfer'
    measured = measure_edge_label(text, .82, 9)
    assert measured['lines'] >= 3
    assert measured['width_in'] <= .84
    assert measured['font_pt'] == 9
    assert measured['height_in'] > .4
    assert _rejoin_wrapped(measured['wrapped_text']) == text
    math = '$Q/K-V$'
    assert measure_edge_label(math, .20, 9)['wrapped_text'] == math
    assert measure_edge_label('1/32→1/16', .40, 9)['wrapped_text'].replace('\n', '') == '1/32→1/16'


def _rejoin_wrapped(text):
    # A line break at an existing separator adds no inter-word space.
    return '\n'.join(text.splitlines()).replace('-\n', '-').replace('/\n', '/').replace('\n', ' ')


def test_host_waypoints_offsets_and_preallocated_label_are_used_in_actual_artifacts(tmp_path):
    data = _input()
    edge = data['production_scene']['connections'][0]
    edge.update(label='Q, K, V', from_port='right', to_port='left',
                from_port_offset_in=.13, to_port_offset_in=-.13,
                waypoints=[[1.786 / 6.5, 1 - 2.85 / 3.6], [2.504 / 6.5, 1 - 2.85 / 3.6]],
                label_bbox=[1.855 / 6.5, 1 - 2.80 / 3.6, .60 / 6.5, .23 / 3.6])
    data['production_contract']['edges'][0]['label'] = edge['label']
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    rendered = report['connections'][0]
    assert rendered['mandatory_waypoints'] == 2
    assert rendered['from_port_offset_in'] == .13 and rendered['to_port_offset_in'] == -.13
    assert rendered['label_preallocated']
    assert rendered['label_bbox_in'] == pytest.approx([1.855, 2.57, .60, .23])
    assert .025 < rendered['label_route_distance_in'] <= .051
    assert any(x == pytest.approx(1.786) and y == pytest.approx(2.85) for x, y in rendered['path_in'])
    assert any(x == pytest.approx(2.504) and y == pytest.approx(2.85) for x, y in rendered['path_in'])
    measured = next(item for item in report['text_measurements']
                    if item['id'] == edge['id'] and item['role'] == 'connection-label')
    assert measured['text'] == 'Q, K, V'
    assert json.loads(Path(outputs['scene']).read_text())['connections'][0] == edge
    assert 'Q, K, V' in PdfReader(outputs['pdf']).pages[0].extract_text()


@pytest.mark.parametrize('waypoint,code', [([.12, .40], 'connection_waypoint_blocked'),
                                         ([.99, .40], 'connection_waypoint_outside_bounds')])
def test_invalid_mandatory_waypoint_is_reported_and_never_silently_ignored(tmp_path, waypoint, code):
    data = _input()
    data['production_scene']['connections'][0]['waypoints'] = [waypoint]
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert not report['geometry_passed']
    assert any(issue['id'] == 'visual_query' and issue['code'] == code for issue in report['quality_issues'])
    assert all(edge['id'] != 'visual_query' for edge in report['connections'])
    assert json.loads(Path(outputs['scene']).read_text())['connections'][0]['waypoints'] == [waypoint]
    assert report['coverage']['dependency_coverage'] == 2


def test_invalid_explicit_port_offset_reports_available_native_side_capacity(tmp_path):
    data = _input()
    data['production_scene']['connections'][0].update(from_port='right', from_port_offset_in=4.)
    report = _report(render_scene(tmp_path, data))
    assert not report['geometry_passed']
    issue = next(issue for issue in report['quality_issues'] if issue['code'] == 'connection_port_offset_outside_bounds')
    assert issue['requested_offset_in'] == 4.
    assert issue['maximum_offset_in'] == pytest.approx(.86)
    assert all(edge['id'] != 'visual_query' for edge in report['connections'])


def test_native_attachment_stub_cannot_cross_other_scientific_content():
    source, target = (.5, .5, 1., 1.), (3., .5, 1., 1.)
    blocker = (1.502, .97, .015, .06)
    obstacles = [(.475, .475, 1.05, 1.05), (2.975, .475, 1.05, 1.05), blocker]
    edge = {'source': 'input', 'target': 'output', 'from_port': 'right', 'to_port': 'left',
            'from_port_offset_in': 0., 'to_port_offset_in': 0.}
    assert _connection_route(source, target, edge, obstacles, [], 5., 3., .025) is None
    edge.pop('from_port_offset_in')
    routed = _connection_route(source, target, edge, obstacles, [], 5., 3., .025)
    assert routed and abs(routed[4]) >= .065
    assert _segment_clear(routed[0][0], routed[0][1], [blocker])


def test_preallocated_label_too_small_is_not_relocated_or_reduced_below_minimum(tmp_path):
    data = _input()
    edge = data['production_scene']['connections'][0]
    edge.update(label='Query-conditioned input features', label_bbox=[.29, .16, .06, .02])
    data['production_contract']['edges'][0]['label'] = edge['label']
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert not report['geometry_passed']
    issue = next(issue for issue in report['quality_issues'] if issue['code'] == 'edge_label_bbox_overflow')
    assert issue['required_height_in'] > .02 * 3.6
    assert issue['required_width_in'] > .06 * 6.5
    rendered = report['connections'][0]
    assert rendered['label_preallocated']
    assert rendered['label_bbox_in'] == pytest.approx([.29 * 6.5, (1 - .16 - .02) * 3.6, .06 * 6.5, .02 * 3.6])
    assert rendered['canonical_label'] == edge['label']
    assert report['minimum_font_pt'] >= 9


def test_allocated_label_is_checked_against_its_own_wire_instead_of_any_nearby_space(tmp_path):
    data = _input()
    edge = data['production_scene']['connections'][0]
    edge.update(label='Q', label_bbox=[.29, .83, .08, .06])
    data['production_contract']['edges'][0]['label'] = 'Q'
    report = _report(render_scene(tmp_path, data))
    assert not report['geometry_passed']
    issue = next(issue for issue in report['quality_issues'] if issue['code'] == 'edge_label_detached')
    assert issue['id'] == 'visual_query'
    assert issue['label_route_distance_in'] > .8
    assert report['connections'][0]['label_preallocated']
    assert report['connections'][0]['label_bbox_in'] == pytest.approx([1.885, .396, .52, .216])


def test_same_source_branches_keep_distinct_native_attachments_without_implicit_bus(tmp_path):
    data = _input()
    data['production_contract']['edges'][1]['source'] = 'image'
    data['production_scene']['connections'][1]['source'] = 'visual'
    for edge in data['production_scene']['connections']:
        edge.update(from_port='right', to_port='left')
    report = _report(render_scene(tmp_path, data))
    assert report['geometry_passed'], report['quality_issues']
    first, branch = report['connections']
    assert first['source'] == branch['source'] == 'visual'
    assert first['from_port'] == branch['from_port'] == 'right'
    assert first['from_port_offset_in'] != branch['from_port_offset_in']
    assert all(_wire_penalty(a, b, [first['path_in']]) is not None
               for a, b in zip(branch['path_in'][:-1], branch['path_in'][1:]))
    assert report['coverage']['dependency_coverage'] == 2


def test_all_host_label_boxes_are_reserved_before_first_dependency_routing(tmp_path):
    labels = ['Features', 'Output', 'Queries', 'Attention']
    contract = build_figure_contract({'narrative_mode': 'method_only',
        'nodes': [{'id': 'stage' + str(index), 'label': label} for index, label in enumerate(labels)],
        'edges': [{'source': 'stage0', 'target': 'stage1'},
                  {'source': 'stage2', 'target': 'stage3', 'label': 'Q'}]},
        style={'layout_width_in': 6.5, 'height': 3.6, 'font_size': 9})
    positions = [[.06, .28, .18, .20], [.73, .28, .18, .20],
                 [.06, .68, .18, .20], [.45, .08, .18, .20]]
    scene = {'version': 1, 'width_in': 6.5, 'height_in': 3.6,
        'panels': [{'id': 'main', 'role': 'mechanism', 'bbox': [.02, .02, .96, .96]}],
        'objects': [{'id': 'object' + str(index), 'panel': 'main', 'operation_id': 'stage' + str(index),
                     'kind': 'operator', 'label': label, 'params': {'shape': 'capsule'},
                     'bbox': positions[index], 'evidence_refs': ['operation:stage' + str(index)]}
                    for index, label in enumerate(labels)],
        'connections': [{'id': 'first', 'source': 'object0', 'target': 'object1', 'semantic_edge': 0,
                         'label': '', 'from_port': 'right', 'to_port': 'left'},
                        {'id': 'later', 'source': 'object2', 'target': 'object3', 'semantic_edge': 1,
                         'label': 'Q', 'from_port': 'right', 'to_port': 'left',
                         'waypoints': [[2.5 / 6.5, 1 - .792 / 3.6], [2.5 / 6.5, 1 - 2.952 / 3.6]],
                         'label_bbox': [2.56 / 6.5, 1 - 2.37 / 3.6, .30 / 6.5, .27 / 3.6]}],
        'annotations': []}
    report = _report(render_scene(tmp_path, {'production_scene': scene, 'production_contract': contract}))
    assert report['geometry_passed'], report['quality_issues']
    first, later = report['connections']
    assert later['label_preallocated']
    obstacle = (2.535, 2.075, .35, .32)
    assert all(_segment_clear(a, b, [obstacle]) for a, b in zip(first['path_in'][:-1], first['path_in'][1:]))
    assert any(abs(y - 2.232) > .10 for x, y in first['path_in'])
    assert report['coverage']['dependency_coverage'] == 2


def test_independent_wire_uses_a_parallel_lane_instead_of_reusing_existing_span():
    previous = [[(.5, 1.8), (4.5, 1.8)]]
    path = _route((.5, 1.8), (4.5, 1.8), [], 5., 3.6, previous_wires=previous)
    assert path and len(path) >= 4
    assert any(abs(point[1] - 1.8) >= .06 for point in path)
    assert all(_wire_penalty(a, b, previous) is not None for a, b in zip(path[:-1], path[1:]))


def test_dense_six_operation_flow_routes_eight_distinct_dependencies(tmp_path):
    names = ['Image features', 'Query tokens', 'Attention mask', 'Context memory', 'Masked queries', 'Output tokens']
    nodes = [{'id': 'stage' + str(index), 'label': name} for index, name in enumerate(names)]
    links = [(0, 1), (1, 2), (3, 4), (4, 5), (0, 4), (3, 1), (1, 5), (4, 2)]
    graph = {'nodes': nodes, 'edges': [{'source': nodes[a]['id'], 'target': nodes[b]['id']} for a, b in links],
             'narrative_mode': 'method_only'}
    contract = build_figure_contract(graph, style={'layout_width_in': 6.5, 'height': 4.4, 'font_size': 9})
    objects = [{'id': 'object' + str(index), 'panel': 'main', 'operation_id': node['id'],
                'kind': 'tokens', 'label': node['label'],
                'bbox': [.07 + (index % 3) * .32, .19 + (index // 3) * .43, .19, .29],
                'params': {'items': ['x₁', 'x₂', 'x₃']}, 'evidence_refs': ['operation:' + node['id']]}
               for index, node in enumerate(nodes)]
    scene = {'version': 1, 'width_in': 6.5, 'height_in': 4.4,
             'panels': [{'id': 'main', 'role': 'mechanism', 'title': 'Coupled query and context paths',
                         'bbox': [.02, .02, .96, .96]}], 'objects': objects,
             'connections': [{'id': 'edge' + str(index), 'source': objects[a]['id'], 'target': objects[b]['id'],
                              'semantic_edge': index, 'label': ''} for index, (a, b) in enumerate(links)],
             'annotations': []}
    report = _report(render_scene(tmp_path, {'production_scene': scene, 'production_contract': contract}))
    assert report['geometry_passed'], report['quality_issues']
    assert len(report['connections']) == 8
    assert {edge['semantic_edge'] for edge in report['connections']} == set(range(8))
    assert all(edge['obstacle_free'] for edge in report['connections'])
    assert all(edge['from_port'] in {'left', 'right', 'top', 'bottom'} for edge in report['connections'])


def test_incoming_and_outgoing_arrows_share_named_side_using_distinct_anchors(tmp_path):
    data = _input()
    for edge in data['production_scene']['connections']:
        edge['from_port'] = 'top'; edge['to_port'] = 'top'
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    assert len(report['connections']) == 2
    assert report['connections'][0]['to_port'] == 'top'
    assert report['connections'][1]['from_port'] == 'top'
    assert abs(report['connections'][1]['from_port_offset_in']) >= .06
    assert report['coverage']['dependency_coverage'] == 2


def test_long_dependency_label_wraps_in_narrow_flow_without_changing_text(tmp_path):
    data = _input()
    supplied_label = 'Image dependent query attention'
    data['production_contract']['edges'][0]['label'] = supplied_label
    data['production_scene']['connections'][0]['label'] = supplied_label
    outputs = render_scene(tmp_path, data)
    report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    connection = report['connections'][0]
    assert connection['label_text'] == supplied_label
    assert connection['label_lines'] >= 2
    assert connection['label_bbox_in'][2] < 1.0
    label_measurement = next(item for item in report['text_measurements']
                             if item['id'] == 'visual_query' and item['role'] == 'connection-label')
    assert label_measurement['text'] == supplied_label
    assert label_measurement['font_pt'] >= 9
    assert 'Image' in PdfReader(outputs['pdf']).pages[0].extract_text()


def test_two_line_operation_label_reserves_measured_space(tmp_path):
    data = _input()
    supplied_label = 'Image-conditioned query transformer'
    graph = {'nodes': deepcopy(data['production_contract']['nodes']),
             'edges': deepcopy(data['production_contract']['edges']), 'narrative_mode': 'method_only'}
    graph['nodes'][0]['label'] = supplied_label
    data['production_contract'] = build_figure_contract(
        graph, style={'layout_width_in': 6.5, 'height': 3.6, 'font_size': 9})
    data['production_scene']['objects'][0]['label'] = supplied_label
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    label = next(item for item in report['text_measurements'] if item['id'] == 'visual' and item['role'] == 'label')
    assert label['text'] == supplied_label
    assert label['font_pt'] == 9
    assert label['bbox_in'][3] > .20
    svg_texts = ElementTree.parse(outputs['svg']).findall('.//{http://www.w3.org/2000/svg}text')
    assert any('Image-conditioned' in ''.join(element.itertext()) for element in svg_texts)


def test_presentation_alias_is_visible_without_changing_canonical_source(tmp_path):
    data = _input()
    data['production_scene']['objects'][0]['display_label'] = 'Visual encoder'
    outputs = render_scene(tmp_path, data)
    saved = json.loads(Path(outputs['scene']).read_text())
    assert saved['objects'][0]['label'] == 'Encoded image'
    assert saved['objects'][0]['display_label'] == 'Visual encoder'
    assert data['production_contract']['nodes'][0]['label'] == 'Encoded image'
    text = PdfReader(outputs['pdf']).pages[0].extract_text()
    assert 'Visual encoder' in text
    assert _report(outputs)['geometry_passed']


def test_unambiguous_edge_can_hide_typography_while_retaining_arrow_and_source(tmp_path):
    data = _input()
    canonical = 'Image features entering query-conditioned attention'
    graph = {'nodes': deepcopy(data['production_contract']['nodes']),
             'edges': deepcopy(data['production_contract']['edges']), 'narrative_mode': 'method_only'}
    graph['edges'][0]['label'] = canonical
    data['production_contract'] = build_figure_contract(
        graph, style={'layout_width_in': 6.5, 'height': 3.6, 'font_size': 9})
    edge = data['production_scene']['connections'][0]
    edge.update(label=canonical, label_visible=False,
                label_visibility_reason='The direct arrow connects explicitly labelled visual features and query attention.')
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    rendered = report['connections'][0]
    assert rendered['canonical_label'] == canonical
    assert rendered['label_text'] == ''
    assert rendered['label_bbox_in'] is None
    assert rendered['label_visible'] is False
    assert rendered['label_visibility_reason'] == edge['label_visibility_reason']
    assert rendered['semantic_edge'] == 0
    assert report['coverage']['dependency_coverage'] == 2
    assert json.loads(Path(outputs['scene']).read_text())['connections'][0]['label'] == canonical
    groups = {element.attrib.get('id') for element in ElementTree.parse(outputs['svg']).iter()}
    assert 'visual_query-arrowhead' in groups
    assert 'visual_query-connection-label' not in groups


def test_symbolic_schematic_attention_cells_are_native_text(tmp_path):
    data = _input()
    data['production_scene']['objects'][1]['params'] = {
        'rows': 2, 'cols': 2, 'values': [['0', '−∞'], ['0', '0']], 'schematic': True}
    outputs = render_scene(tmp_path, data)
    report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    tree = ElementTree.parse(outputs['svg'])
    cells = [element for element in tree.iter() if element.attrib.get('id', '').startswith('queries-matrix-cell-label')]
    assert len(cells) == 4
    assert any('−∞' in ''.join(element.itertext()) for element in cells)


def test_native_packing_measures_content_preserves_topology_and_reserves_headers(tmp_path):
    data = _input()
    original = deepcopy(data)
    scene = data['production_scene']
    scene['title'] = 'Query-conditioned representation'
    scene['panels'][0]['layout'] = {'flow': 'horizontal', 'object_ids': ['visual', 'queries', 'text'],
                                   'routing_gutter_in': .20}
    for item in scene['objects']:
        item.pop('bbox')
    derived = layout_scene(scene, data['production_contract'])
    assert all('bbox' in item for item in derived['objects'])
    assert derived['connections'] == original['production_scene']['connections']
    assert derived['panels'][0]['header_bbox'][1] > .07
    assert all('bbox' not in item for item in scene['objects'])
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    assert report['layout_requirements']['visual']['minimum_height_in'] > .6
    assert json.loads(Path(outputs['data']).read_text())['production_scene']['panels'][0]['layout']['flow'] == 'horizontal'


@pytest.mark.parametrize('formula', ['Query states attend to visual features', '1/32→1/16→1/8→repeat'])
def test_operator_formula_wraps_instead_of_imposing_full_sentence_width(tmp_path, formula):
    data = _input()
    item = data['production_scene']['objects'][1]
    item['kind'] = 'operator'; item['params'] = {'formula': formula}
    measurements = measure_scene_layout(data['production_scene'], data['production_contract'])
    assert measurements['queries']['minimum_width_in'] < 1.4
    assert measurements['queries']['body_height_in'] > .4
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    text_record = next(record for record in report['text_measurements'] if record['id'] == 'queries' and record['role'] == 'formula')
    assert text_record['text'] == formula
    assert text_record['bbox_in'][3] > .20


def test_packed_objects_avoid_unlisted_manual_objects_without_moving_them(tmp_path):
    data = _input()
    scene = data['production_scene']
    fixed_box = [.60, .15, .33, .20]
    scene['objects'][2]['bbox'] = fixed_box
    scene['panels'][0]['layout'] = {'flow': 'vertical', 'object_ids': ['visual', 'queries']}
    derived = layout_scene(scene, data['production_contract'])
    assert derived['objects'][2]['bbox'] == fixed_box
    assert all(item['bbox'][1] >= .35 for item in derived['objects'][:2])
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    assert report['coverage']['operation_coverage'] == 3
    assert report['coverage']['dependency_coverage'] == 2


def test_impossible_packing_reports_required_inches_without_dropping_content(tmp_path):
    data = _input()
    scene = data['production_scene']
    scene['panels'][0]['bbox'] = [.02, .02, .96, .16]
    scene['panels'][0]['layout'] = {'flow': 'horizontal', 'object_ids': ['visual', 'queries', 'text']}
    with pytest.raises(ValueError, match='required_layouts') as error:
        render_scene(tmp_path, data)
    assert '9.0pt' in str(error.value)
    assert len(scene['objects']) == 3
    assert len(scene['connections']) == 2
    assert not (tmp_path / 'figure.png').exists()


def test_measured_preflight_identifies_impossible_legacy_text_anchor():
    data = _input()
    item = data['production_scene']['objects'][1]
    item['kind'] = 'custom'
    item['params'] = {'primitives': [
        {'kind': 'rect', 'bbox': [.1, .1, .8, .8]},
        {'kind': 'text', 'position': [.5, .02], 'text': 'Masked attention', 'font_pt': 9}]}
    measurements = measure_scene_layout(data['production_scene'], data['production_contract'])
    assert measurements['queries']['body_height_in'] > 4
    assert measurements['queries']['font_pt'] == 9


def test_custom_text_has_dark_default_ink_and_explicit_native_bounds(tmp_path):
    data = _input()
    item = data['production_scene']['objects'][1]
    item['kind'] = 'custom'
    item['params'] = {'primitives': [
        {'kind': 'rect', 'bbox': [.02, .02, .96, .96], 'fill': '#E9E1F0', 'stroke': '#E9E1F0'},
        {'kind': 'text', 'bbox': [.10, .32, .80, .34], 'text': 'Q × K', 'font_pt': 9,
         'stroke': '#E9E1F0', 'align': 'center', 'valign': 'center'}]}
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    tree = ElementTree.parse(outputs['svg'])
    group = next(element for element in tree.iter() if element.attrib.get('id') == 'queries-custom-text')
    text = group.find('.//{http://www.w3.org/2000/svg}text')
    assert text is not None
    assert '#253647' in text.attrib['style']
    assert 'Q × K' in ''.join(text.itertext())


def test_actual_long_text_is_reported_as_overflow_not_approved(tmp_path):
    data = _input()
    data['production_scene']['annotations'] = [{
        'id': 'claim', 'panel': 'main', 'role': 'claim', 'text': 'A' * 150,
        'bbox': [.1, .83, .20, .10], 'font_pt': 9, 'evidence_refs': ['operation:attention']}]
    report = _report(render_scene(tmp_path, data))
    assert not report['geometry_passed']
    assert any(issue['id'] == 'claim' and issue['code'] == 'text_overflow' for issue in report['quality_issues'])
    assert report['minimum_font_pt'] >= 9


def test_overlapping_scientific_objects_are_reported(tmp_path):
    data = _input()
    data['production_scene']['objects'][1]['bbox'] = [.14, .27, .21, .50]
    report = _report(render_scene(tmp_path, data))
    assert not report['geometry_passed']
    assert any(issue['code'] == 'object_overlap' for issue in report['quality_issues'])


def test_attention_block_mask_and_labels_remain_native(tmp_path):
    data = _input()
    matrix = data['production_scene']['objects'][1]
    matrix['params'] = {'rows': 2, 'cols': 2, 'mask_matrix': [[True, False], [True, True]],
                        'row_labels': ['Q', 'T'], 'col_labels': ['Q', 'T'], 'schematic': True}
    outputs = render_scene(tmp_path, data)
    report = _report(outputs)
    assert report['geometry_passed'], report['quality_issues']
    svg = ElementTree.parse(outputs['svg'])
    ids = [element.attrib.get('id', '') for element in svg.iter()]
    assert len([identifier for identifier in ids if identifier.startswith('queries-matrix-cell')]) == 4
    assert len([identifier for identifier in ids if identifier.startswith('queries-matrix-row-label')]) == 2
    assert len([identifier for identifier in ids if identifier.startswith('queries-matrix-column-label')]) == 2
    assert not svg.findall('.//{http://www.w3.org/2000/svg}image')


def test_small_raster_asset_cannot_pass_print_quality(tmp_path):
    data = _input()
    scene = data['production_scene']; scene['objects'][0]['kind'] = 'asset'
    scene['objects'][0]['params'] = {'asset_id': 'detail_asset'}
    scene['assets'] = [{'id': 'detail_asset', 'role': 'conceptual_illustration',
                        'prompt': 'An explicitly source-bound illustrative image encoding with no labels',
                        'evidence_refs': ['operation:image']}]
    image = Image.new('RGBA', (32, 32), '#29669D'); stream = io.BytesIO(); image.save(stream, format='PNG')
    data['asset_data'] = {'detail_asset': {'base64': base64.b64encode(stream.getvalue()).decode(), 'mime_type': 'image/png'}}
    outputs = render_scene(tmp_path, data); report = _report(outputs)
    assert not report['geometry_passed']
    assert any(issue['code'] == 'raster_resolution' for issue in report['quality_issues'])
    assert ElementTree.parse(outputs['svg']).findall('.//{http://www.w3.org/2000/svg}image')
    assert report['native_text_elements'] >= 7


def test_invalid_asset_payload_fails_before_publishing(tmp_path):
    data = _input()
    scene = data['production_scene']; scene['objects'][0]['kind'] = 'asset'
    scene['objects'][0]['params'] = {'asset_id': 'detail_asset'}
    scene['assets'] = [{'id': 'detail_asset', 'role': 'conceptual_illustration',
                        'prompt': 'A source-grounded scientific depiction without labels or arrows',
                        'evidence_refs': ['operation:image']}]
    data['asset_data'] = {'detail_asset': 'this is not a PNG'}
    with pytest.raises(ValueError, match='Invalid PNG asset'):
        render_scene(tmp_path, data)
    assert not (tmp_path / 'figure.png').exists()


def test_source_topology_cannot_be_deleted_to_repair_layout(tmp_path):
    data = deepcopy(_input())
    data['production_scene']['connections'].pop()
    with pytest.raises(ValueError, match='omitted supplied dependency'):
        render_scene(tmp_path, data)
    assert not (tmp_path / 'figure.svg').exists()
