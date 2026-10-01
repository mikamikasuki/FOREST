"""Production contract tests with real vector/PDF/PNG rendering.

The model double below supplies explicit unit-test responses. These tests check
orchestration, artifact integrity and rejection rules; they are not live-model
validation or evidence of a figure's publication quality.
"""
from copy import deepcopy
import json
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image
import pytest

from research.figures.model_workflow import reviewed_image, reviewed_render
from research.figures.production import _bundle, produce_diagram
from research.figures.spec import build_figure_contract
from research.figures.workflow import REVIEW_ROLES, review_requests


def graph_and_scene(layout='a'):
    graph = {
        'nodes': [{'id': 'Encode', 'label': 'Token encoding'},
                  {'id': 'Attend', 'label': 'Causal attention'}],
        'edges': [{'source': 'Encode', 'target': 'Attend', 'label': 'Q, K, V'}],
        'key_operation_id': 'Attend',
    }
    left, right = [0.10, 0.25, 0.25, 0.40], [0.60, 0.25, 0.28, 0.40]
    if layout == 'b':
        left, right = right, left
    scene = {
        'version': 1, 'width_in': 6.5, 'height_in': 4.4,
        'panels': [{'id': 'Mechanism', 'role': 'mechanism', 'title': 'Causal token aggregation',
                    'bbox': [0.03, 0.04, 0.94, 0.92]}],
        'objects': [
            {'id': 'Encoding', 'panel': 'Mechanism', 'operation_id': 'Encode', 'kind': 'tensor',
             'label': 'Token encoding', 'bbox': left,
             'detail': 'Each token carries a learned vector representation.',
             'params': {'shape': [3, 3, 2]}, 'evidence_refs': ['operation:Encode']},
            {'id': 'Attention', 'panel': 'Mechanism', 'operation_id': 'Attend', 'kind': 'matrix',
             'label': 'Causal attention', 'bbox': right,
             'detail': 'The triangular mask excludes information from future token positions.',
             'params': {'rows': 3, 'cols': 3, 'mask': 'causal', 'schematic': True},
             'evidence_refs': ['operation:Attend']},
        ],
        'connections': [{'id': 'Features', 'source': 'Encoding', 'target': 'Attention',
                         'semantic_edge': 0, 'label': 'Q, K, V'}],
        'annotations': [],
    }
    return graph, scene


def _payload(messages):
    content = messages[1]['content']
    text = content if isinstance(content, str) else next(
        item['text'] for item in content if item.get('type') in ('text', 'input_text'))
    # The pixel reviewer appends a short inspection instruction after its JSON.
    return json.JSONDecoder().raw_decode(text)[0]


class ContractModelDouble:
    """Unit-only scene and review transport with no external model calls."""
    api = 'chat'

    def __init__(self, scene_factory=None, scores=None):
        self.config = {'max_output_tokens': 8192}
        self.scene_factory = scene_factory or (lambda identifier, attempt: graph_and_scene(identifier)[1])
        self.scores = scores or {}
        self.design_calls = {}
        self.repair_payloads = []
        self.repair_pixels = []
        self.review_pixels = []

    def complete(self, messages):
        instruction, payload = messages[0]['content'], _payload(messages)
        role = next((role for role in REVIEW_ROLES if 'Act as the independent ' + role in instruction), None)
        if role is not None:
            self.review_pixels.append([
                item['image_url']['url'] for item in messages[1]['content']
                if item.get('type') == 'image_url'])
            value = {'role': role, 'placement': None, 'reviews': [
                {'candidate_id': candidate['id'], 'verdict': 'accept',
                 'scores': {dimension: self.scores.get(candidate['id'], 4.5)
                            for dimension in REVIEW_ROLES[role]},
                 'reasons': ['Unit-test review response; production geometry must still pass.']}
                for candidate in payload['candidates']
            ]}
        elif 'Act as a scientific figure art director.' in instruction:
            value = {'brief': 'Explain the causal mask applied to encoded tokens.', 'assets': [],
                     'layouts': [{'id': 'a', 'brief': 'Left-to-right mechanism flow.'},
                                 {'id': 'b', 'brief': 'Right-to-left mechanism flow.'}]}
        elif 'Act as the scientific Storyboard Designer.' in instruction:
            graph, _ = graph_and_scene()
            value = {'mode': 'method_only', 'title': 'Causal token aggregation',
                     'composition': {'print_width_in': 6.5, 'print_height_in': 4.4,
                                     'minimum_font_pt': 9},
                     'nodes': graph['nodes'], 'edges': graph['edges']}
        elif 'layout' in payload:
            identifier = payload['layout']['id']
            attempt = self.design_calls.get(identifier, 0) + 1
            self.design_calls[identifier] = attempt
            if 'observed_defects' in payload:
                self.repair_payloads.append(payload)
                content = messages[1]['content']
                self.repair_pixels.append([
                    item['image_url']['url'] for item in content
                    if item.get('type') == 'image_url'] if isinstance(content, list) else [])
            value = self.scene_factory(identifier, attempt)
        else:
            raise AssertionError('Unexpected unit-test model request')
        return {'text': json.dumps(value), 'model': 'unit-contract-double',
                'request_id': 'unit-request', 'usage': {'input_tokens': 1, 'output_tokens': 1}}


def test_success_publishes_real_editable_outputs_preserves_sources_and_cleans_work(tmp_path):
    graph, _ = graph_and_scene()
    original = deepcopy(graph)
    client = ContractModelDouble()
    config = deepcopy(client.config)
    outputs, selection = produce_diagram(client, tmp_path, graph)
    assert graph['nodes'] == original['nodes']
    assert graph['edges'] == original['edges']
    assert client.config == config
    assert selection['candidate_id'] == 'a'
    assert len(client.review_pixels) == 3
    assert all(len(images) == 2 for images in client.review_pixels)
    assert all(url.startswith('data:image/') for images in client.review_pixels for url in images)
    with Image.open(outputs['png']) as picture:
        assert picture.width == 1950
        assert picture.height == 1320
    assert Path(outputs['pdf']).read_bytes().startswith(b'%PDF-')
    tree = ElementTree.parse(outputs['svg'])
    labels = [element.text for element in tree.findall('.//{http://www.w3.org/2000/svg}text')]
    assert 'Token encoding' in labels
    assert 'Causal attention' in labels
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'] is True
    assert report['quality_issues'] == []
    assert report['native_text_elements'] >= 4
    assert report['native_shape_elements'] > 15
    assert set(path.name for path in tmp_path.iterdir()) == {Path(path).name for path in outputs.values()}
    assert all('response_path' not in review['provider_receipt'] for review in report['independent_review']['reviews'])
    saved = json.loads(Path(outputs['data']).read_text())
    assert saved['nodes'] == original['nodes']
    assert saved['edges'] == original['edges']


def test_production_review_projection_retains_complete_source_scene_and_defects(tmp_path):
    graph, scene = graph_and_scene()
    source = {'method': 'Encoded tokens use an inclusive causal mask before value aggregation.',
              'sources': [{'id': 'method', 'text': 'The receiving token cannot attend to later tokens.'}]}
    contract = build_figure_contract(graph, source)
    data = tmp_path / 'data.json'
    data.write_text(json.dumps({'production_scene': scene}))
    candidate = {'id': 'a', 'outputs': {'data': str(data)}, 'vector_content': '<svg>path stream</svg>',
                 'report': {'production_pipeline': 'native', 'quality_issues': [{'code': 'object_overlap'}],
                            'coverage': {'dependency_coverage': 1}, 'text_measurements': [{'text': 'measured'}],
                            'connections': [{'id': 'Features', 'waypoints': [[.1, .2]], 'label': 'Q, K, V'}]}}
    bundle = {'kind': 'method', 'production_review_only': True, 'candidates': [candidate]}
    original = deepcopy(bundle)
    jobs = review_requests(bundle, {'production_contract': contract})
    assert len(jobs) == 3
    for job in jobs:
        payload = json.loads(job['prompt'])
        assert payload['context']['production_contract'] == contract
        assert payload['context']['production_contract']['source_context'] == source
        assert payload['measurement_data']['source_contract_location'] == 'context.production_contract'
        assert 'production_scene' not in payload['measurement_data']
        inspected = payload['candidates'][0]
        assert inspected['native_scene'] == scene
        assert inspected['report']['quality_issues'] == candidate['report']['quality_issues']
        assert inspected['report']['coverage'] == candidate['report']['coverage']
        assert inspected['report']['connections'][0]['label'] == 'Q, K, V'
        assert 'vector_content' not in inspected
        assert 'text_measurements' not in inspected['report']
    assert bundle == original


def test_configured_raster_layer_runs_once_after_native_passes_are_exhausted(tmp_path, monkeypatch):
    from research.figures import raster_finish
    graph, _ = graph_and_scene()
    original = deepcopy(graph)
    client = ContractModelDouble(scores={'a': 3.5, 'b': 3.5})
    client.config['image_generation'] = {'model': 'unit-image-provider', 'max_request_usd': 1}
    calls = []
    def finish(client_arg, output, stage, source_graph, contract, style, **kwargs):
        calls.append(kwargs)
        assert client_arg is client
        assert source_graph['nodes'] == contract['nodes'] == original['nodes']
        assert source_graph['edges'] == contract['edges'] == original['edges']
        assert kwargs['feedback']
        assert len(kwargs['reference_paths']) == 2
        for path in kwargs['reference_paths']:
            with Image.open(path) as image:
                assert image.format == 'PNG'
        path = Path(output) / 'figure.png'
        path.write_bytes(Path(kwargs['reference_paths'][0]).read_bytes())
        return {'png': str(path)}, {'candidate_id': 'final_raster', 'status': 'selected'}
    monkeypatch.setattr(raster_finish, 'finish_raster', finish)
    outputs, selection = produce_diagram(client, tmp_path, graph, attempts=2)
    assert selection['candidate_id'] == 'final_raster'
    assert len(calls) == 1
    assert client.design_calls == {'a': 2, 'b': 2}
    assert len(client.review_pixels) == 6
    assert set(tmp_path.iterdir()) == {Path(outputs['png'])}


def test_successful_native_render_does_not_enter_final_image_layer(tmp_path, monkeypatch):
    from research.figures import raster_finish
    def unexpected(*args, **kwargs):
        raise AssertionError('A passing native diagram must not incur an image batch')
    monkeypatch.setattr(raster_finish, 'finish_raster', unexpected)
    graph, _ = graph_and_scene()
    client = ContractModelDouble()
    client.config['image_generation'] = {'model': 'unit-image-provider', 'max_request_usd': 1}
    outputs, _ = produce_diagram(client, tmp_path, graph)
    assert json.loads(Path(outputs['report']).read_text())['geometry_passed'] is True


def test_compact_composition_uses_host_owned_overview_and_persists_editable_plan(tmp_path):
    plan = {'version': 1, 'overview_position': 'top',
            'display_aliases': {'Encode': 'Token encoding', 'Attend': 'Causal attention'},
            'hero': {'kind': 'matrix', 'params': {'rows': 3, 'cols': 3,
                       'mask': 'causal', 'schematic': True},
                     'detail': 'The causal mask prevents access to future tokens.',
                     'evidence_refs': ['operation:Attend']},
            'caption_text': 'Encoded tokens are aggregated using a causal attention mask.'}
    def compose(identifier, attempt):
        return {**deepcopy(plan), 'overview_position': 'top' if identifier == 'a' else 'left'}
    graph, _ = graph_and_scene()
    client = ContractModelDouble(compose)
    outputs, selection = produce_diagram(client, tmp_path, graph)
    data = json.loads(Path(outputs['data']).read_text())
    assert data['production_composition'] == graph['production_composition']
    assert data['production_composition']['overview_position'] == ('top' if selection['candidate_id'] == 'a' else 'left')
    scene = json.loads(Path(outputs['scene']).read_text())
    assert {obj['operation_id'] for obj in scene['objects']} == {'Encode', 'Attend'}
    assert {edge['semantic_edge'] for edge in scene['connections'] if 'semantic_edge' in edge} == {0}
    report = json.loads(Path(outputs['report']).read_text())
    assert report['geometry_passed'] is True
    assert len(client.review_pixels) == 3
    assert data['nodes'] == graph_and_scene()[0]['nodes']


def test_composition_repair_receives_local_plan_and_rejects_global_geometry(tmp_path):
    def compose(identifier, attempt):
        value = {'version': 1, 'overview_position': 'top',
                 'hero': {'kind': 'matrix', 'params': {'rows': 3, 'cols': 3,
                            'mask': 'causal', 'schematic': True},
                          'detail': 'The causal mask blocks information from future tokens.',
                          'evidence_refs': ['operation:Attend']}}
        if identifier == 'a' and attempt == 1:
            value['width_in'] = 20
        return value
    graph, _ = graph_and_scene()
    client = ContractModelDouble(compose, {'b': 3.5})
    outputs, selection = produce_diagram(client, tmp_path, graph, attempts=2)
    assert selection['candidate_id'] == 'a'
    repair = next(payload for payload in client.repair_payloads if payload['layout']['id'] == 'a')
    assert 'host-owned' in repair['observed_defects']['validation_error']
    assert repair['prior_composition']['width_in'] == 20
    assert 'scene' not in repair
    assert json.loads(Path(outputs['report']).read_text())['width_in'] == 6.5
    assert 'width_in' not in graph['production_composition']
    assert not any(path.is_dir() for path in tmp_path.iterdir())


def test_actual_object_collision_vetoes_unanimous_positive_model_scores(tmp_path):
    def collide(identifier, attempt):
        _, scene = graph_and_scene(identifier)
        scene['objects'][1]['bbox'] = deepcopy(scene['objects'][0]['bbox'])
        return scene
    client = ContractModelDouble(collide, {'a': 5, 'b': 5})
    graph, _ = graph_and_scene()
    with pytest.raises(ValueError, match='No production diagram passed'):
        produce_diagram(client, tmp_path, graph, attempts=1)
    assert client.review_pixels
    assert list(tmp_path.iterdir()) == []
    assert 'production_scene' not in graph


@pytest.mark.parametrize('score, accepted', [(3.9, False), (4.0, True)])
def test_every_production_quality_dimension_must_reach_four(tmp_path, score, accepted):
    graph, _ = graph_and_scene()
    client = ContractModelDouble(scores={'a': score, 'b': score})
    if accepted:
        outputs, _ = produce_diagram(client, tmp_path, graph, attempts=1)
        assert Path(outputs['svg']).is_file()
    else:
        with pytest.raises(ValueError, match='No production diagram passed'):
            produce_diagram(client, tmp_path, graph, attempts=1)
        assert list(tmp_path.iterdir()) == []


def test_partially_invalid_candidate_is_repaired_against_frozen_science(tmp_path):
    def omission(identifier, attempt):
        _, scene = graph_and_scene(identifier)
        if identifier == 'a' and attempt == 1:
            scene['connections'].clear()
        return scene
    graph, _ = graph_and_scene()
    client = ContractModelDouble(omission, {'b': 3.5})
    outputs, _ = produce_diagram(client, tmp_path, graph, attempts=2)
    assert client.design_calls == {'a': 2, 'b': 2}
    assert 'omitted supplied dependency' in client.repair_payloads[0]['observed_defects']['validation_error']
    saved = json.loads(Path(outputs['data']).read_text())
    assert saved['production_scene']['connections'][0]['semantic_edge'] == 0
    assert not any(path.is_dir() for path in tmp_path.iterdir())


def test_single_passing_candidate_can_publish_after_two_distinct_real_proposals(tmp_path):
    def omission(identifier, attempt):
        _, scene = graph_and_scene(identifier)
        if identifier == 'b' and attempt == 1:
            scene['connections'].clear()
        return scene
    graph, _ = graph_and_scene()
    client = ContractModelDouble(omission)
    outputs, selection = produce_diagram(client, tmp_path, graph, attempts=2)
    assert [len(images) for images in client.review_pixels] == [1, 1, 1]
    assert client.design_calls == {'a': 1, 'b': 1}
    assert selection['attempted_layout_count'] == 2
    assert selection['renderable_candidate_count'] == 1
    assert json.loads(Path(outputs['report']).read_text())['geometry_passed'] is True


def test_capacity_failure_replans_hierarchy_instead_of_repairing_a_status_object(tmp_path):
    class CapacityDouble(ContractModelDouble):
        def complete(self, messages):
            payload = _payload(messages)
            if 'capacity_failure' in payload:
                return {'text': json.dumps({'id': payload['layout']['id'],
                        'brief': 'Compact overview and a distinct source-bound mask detail.'}),
                        'model': 'unit-contract-double', 'usage': {}}
            response = super().complete(messages)
            if 'layout' in payload and payload['layout']['id'] == 'a' and self.design_calls['a'] == 1:
                response['text'] = json.dumps({'status': 'needs_split', 'reason': 'Unreserved label corridors.'})
            return response
    graph, _ = graph_and_scene()
    client = CapacityDouble(scores={'b': 3.5})
    outputs, _ = produce_diagram(client, tmp_path, graph, attempts=2)
    assert json.loads(Path(outputs['scene']).read_text())['version'] == 1
    assert all(payload['layout']['id'] != 'a' for payload in client.repair_payloads)
    assert client.design_calls == {'a': 2, 'b': 2}


def test_regressed_revision_next_repairs_the_better_observed_scene(tmp_path):
    def scenes(identifier, attempt):
        _, scene = graph_and_scene(identifier)
        if identifier == 'b':
            scene['connections'].clear()
        elif attempt == 2:
            scene['objects'][0]['bbox'][1] = 0.27
        return scene
    class RegressionDouble(ContractModelDouble):
        def complete(self, messages):
            self.scores['a'] = 2 if self.design_calls.get('a') == 2 else 3.5
            return super().complete(messages)
    graph, initial = graph_and_scene()
    client = RegressionDouble(scenes)
    with pytest.raises(ValueError, match='No production diagram passed'):
        produce_diagram(client, tmp_path, graph, attempts=3)
    third = next(payload for payload in client.repair_payloads
                 if payload['layout']['id'] == 'a' and 'rejected_revision' in payload['observed_defects'])
    assert third['scene'] == initial
    repairs_a = [pixels for payload, pixels in zip(client.repair_payloads, client.repair_pixels)
                 if payload['layout']['id'] == 'a']
    assert repairs_a[0] and repairs_a[0] == repairs_a[1]
    assert len(client.review_pixels) == 9
    assert list(tmp_path.iterdir()) == []


def test_truncated_scene_reply_is_retried_without_publishing_a_partial_figure(tmp_path):
    class TruncatedSceneDouble(ContractModelDouble):
        def complete(self, messages):
            response = super().complete(messages)
            payload = _payload(messages)
            if ('layout' in payload and payload['layout']['id'] == 'a'
                    and self.design_calls['a'] == 1):
                response['text'] = '{"version":1,"objects":['
            return response
    graph, _ = graph_and_scene()
    client = TruncatedSceneDouble(scores={'b': 3.5})
    outputs, _ = produce_diagram(client, tmp_path, graph, attempts=2)
    assert client.design_calls == {'a': 2, 'b': 2}
    assert json.loads(Path(outputs['scene']).read_text())['version'] == 1
    assert not any(path.is_dir() for path in tmp_path.iterdir())


def test_single_independent_evidence_veto_excludes_an_otherwise_high_scoring_candidate(tmp_path):
    class EvidenceVetoDouble(ContractModelDouble):
        def complete(self, messages):
            response = super().complete(messages)
            if 'Act as the independent Evidence Reviewer' in messages[0]['content']:
                review = json.loads(response['text'])
                next(item for item in review['reviews'] if item['candidate_id'] == 'a')['verdict'] = 'reject'
                response['text'] = json.dumps(review)
            return response
    graph, _ = graph_and_scene()
    client = EvidenceVetoDouble(scores={'a': 5, 'b': 4.5})
    outputs, selection = produce_diagram(client, tmp_path, graph, attempts=1)
    assert selection['candidate_id'] == 'b'
    assert Path(outputs['svg']).is_file()
    assert next(item for item in selection['ranking'] if item['candidate_id'] == 'a')['eligible'] is False


def test_invalid_redesign_removes_previous_pixels_instead_of_reusing_them(tmp_path):
    graph, scene = graph_and_scene()
    contract = build_figure_contract(graph)
    bundle, defects = _bundle(tmp_path, graph, contract, {'a': scene}, {}, {}, 'method')
    assert not defects
    preview = Path(bundle['candidates'][0]['outputs']['png'])
    assert preview.is_file()
    invalid = deepcopy(scene)
    invalid['objects'][0]['label'] = 'Invented stronger encoder'
    bundle, defects = _bundle(tmp_path, graph, contract, {'a': invalid}, {}, {}, 'method')
    assert bundle['candidates'] == []
    assert 'exact label' in defects['a']['validation_error']
    assert not preview.exists()


def test_science_altering_repair_is_rejected_and_never_published(tmp_path):
    def change_science(identifier, attempt):
        _, scene = graph_and_scene(identifier)
        if attempt == 1:
            scene['connections'].clear()
        else:
            scene['objects'][0]['label'] = 'An unsupported better encoder'
        return scene
    graph, _ = graph_and_scene()
    original = deepcopy(graph)
    with pytest.raises(ValueError, match='No production diagram passed'):
        produce_diagram(ContractModelDouble(change_science), tmp_path, graph, attempts=2)
    assert graph == original
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('route', ['method', 'image'])
def test_existing_method_and_image_workflow_routes_use_native_production(tmp_path, route):
    graph, _ = graph_and_scene()
    client = ContractModelDouble()
    if route == 'method':
        outputs, _ = reviewed_render(client, tmp_path, graph, kind='method', attempts=1)
    else:
        outputs, _ = reviewed_image(
            client, tmp_path, 'Encode tokens, then apply causal attention to query, key and value.',
            context={'narrative_mode': 'method_only'}, attempts=1)
    report = json.loads(Path(outputs['report']).read_text())
    assert report['kind'] == route
    assert report['render_engine'] == 'production_scene'
    assert report['vector_formats'] == ['svg', 'pdf']
    assert client.design_calls == {'a': 1, 'b': 1}
    assert not any(path.is_dir() for path in tmp_path.iterdir())
