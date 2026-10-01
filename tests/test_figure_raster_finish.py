"""Raster finishing orchestration with explicit unit-only model/image doubles.

These tests inspect real PNG and PDF artifacts. The synthetic responses and
images do not establish live-model performance or scientific publication quality.
"""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
from pypdf import PdfReader
import pytest

from research.figures.images import ILLUSTRATION_RULE
from research.figures.raster_finish import OPTIMIZATION_TARGET, finish_raster
from research.figures.spec import build_figure_contract
from research.figures.workflow import REVIEW_ROLES, register_candidates


def supplied():
    graph = {
        'nodes': [{'id': 'Encode', 'label': 'Token encoding'},
                  {'id': 'Attend', 'label': 'Causal attention'}],
        'edges': [{'source': 'Encode', 'target': 'Attend', 'label': 'Q, K, V'}],
        'key_operation_id': 'Attend',
        'story_context': {
            'method': 'Encode tokens and apply a causal query-key mask before aggregating values.',
            'caption': 'Encoded tokens enter a causal aggregation mechanism.',
            'evidence': {'metrics': [{'id': 'accuracy', 'value': 83.5, 'unit': '%'}]},
        },
    }
    style = {'layout_width_in': 6.5, 'height': 4.4, 'font_size': 9}
    contract = build_figure_contract(graph, style=style)
    graph.update(production_scene={'old': 'native scene'},
                 production_contract={'old': 'contract'},
                 production_composition={'old': 'plan'}, asset_data={'old': 'asset'})
    return graph, contract, style


def _payload(messages):
    content = messages[1]['content']
    text = content if isinstance(content, str) else next(
        item['text'] for item in content if item.get('type') in ('text', 'input_text'))
    return json.JSONDecoder().raw_decode(text)[0]


class RasterModelDouble:
    """Only deterministic unit-test responses; no provider transport."""
    api = 'chat'

    def __init__(self, *, count=3, scores=None, verdicts=None, plan_change=None):
        self.config = {
            'max_output_tokens': 8192, 'figure_raster_finish_candidates': count,
            'image_generation': {'model': 'unit-image-double', 'max_request_usd': 1},
        }
        self.scores, self.verdicts = scores or {}, verdicts or {}
        self.plan_change = plan_change
        self.calls = []

    def complete(self, messages):
        payload = _payload(messages)
        instruction = messages[0]['content']
        images = [item['image_url']['url'] for item in messages[1]['content']
                  if item.get('type') == 'image_url'] if isinstance(messages[1]['content'], list) else []
        self.calls.append({'payload': payload, 'instruction': instruction, 'images': images})
        role = next((role for role in REVIEW_ROLES
                     if 'Act as the independent ' + role in instruction), None)
        if role:
            value = {'role': role, 'placement': None, 'reviews': [
                {'candidate_id': candidate['id'],
                 'verdict': self.verdicts.get((role, candidate['id']), 'accept'),
                 'scores': {dimension: self.scores.get((role, candidate['id']), 4.5)
                            for dimension in REVIEW_ROLES[role]},
                 'reasons': ['Explicit unit-test response for the attached synthetic candidate.']}
                for candidate in payload['candidates']]}
        else:
            assert 'Act as a scientific illustration designer' in instruction
            value = {
                'brief': ('Draw encoded token vectors entering a triangular causal attention matrix. '
                          'Show Q, K, V pathways and mark future positions blocked before value aggregation. '
                          'Use a compact overview and a larger mechanism detail.'),
                'caption_text': ('Encoded token representations enter causal attention. '
                                 'The triangular mask blocks future positions before aggregation.'),
                'variants': [{'id': f'candidate{index + 1}',
                              'prompt_suffix': f'Use source-faithful spatial arrangement {index + 1}.'}
                             for index in range(payload['requested_candidate_count'])],
            }
            if self.plan_change:
                self.plan_change(value)
        return {'text': json.dumps(value), 'model': 'unit-text-double',
                'request_id': 'unit-request', 'response_id': 'unit-response',
                'usage': {'input_tokens': 1, 'output_tokens': 1}}


class ImageBatchDouble:
    """Synthetic PNG generation using the real candidate registration contract."""
    def __init__(self, dimensions=(1950, 1320), mode='RGB'):
        self.dimensions, self.mode = dimensions, mode
        self.calls, self.originals = [], {}

    def __call__(self, client, output_dir, prompt, variants, *, reference_paths=()):
        self.calls.append({'prompt': prompt, 'variants': deepcopy(variants),
                           'reference_paths': reference_paths})
        folder = Path(output_dir) / 'generated'
        folder.mkdir(parents=True)
        records = []
        for index, variant in enumerate(variants):
            picture = Image.new(self.mode, self.dimensions,
                                (50 + 15 * index, 100, 170) if self.mode != 'P' else 1)
            if self.mode == 'P':
                picture.putpalette([255, 255, 255, 50, 100, 170] + [0] * 762)
            ImageDraw.Draw(picture).rectangle((10, 10, 100, 100),
                                             fill=(120, 50, 80) if self.mode != 'P' else 0)
            png = folder / (variant['id'] + '.png')
            picture.save(png)
            self.originals[variant['id']] = png.read_bytes()
            source = folder / (variant['id'] + '.prompt.txt')
            source.write_text(ILLUSTRATION_RULE + '\n\nScientific mechanism:\n' + prompt
                              + '\n\nDesign variant:\n' + variant['prompt_suffix'])
            metadata = folder / (variant['id'] + '.metadata.json')
            metadata.write_text('{"raw_provider_log":"unit-only-private"}')
            records.append({
                'id': variant['id'], 'outputs': {'png': str(png), 'prompt': str(source),
                                                'generation_metadata': str(metadata)},
                'report': {'api': 'images', 'model': 'unit-image-double',
                           'actual_image_call': True, 'request_id': 'unit-image-request',
                           'response_id': 'unit-image-response', 'usage': {'total_tokens': 1},
                           'width_px': self.dimensions[0], 'height_px': self.dimensions[1],
                           'reservation': {'unit_only_private': True},
                           'revised_prompt': 'private unit response'},
                'style': {'design_variant': variant['prompt_suffix']},
            })
        return register_candidates(output_dir, records, kind='image')


def invoke(tmp_path, client, generator, monkeypatch, *, graph=None, contract=None,
           style=None, reference_paths=()):
    supplied_graph, supplied_contract, supplied_style = supplied()
    graph = supplied_graph if graph is None else graph
    contract = supplied_contract if contract is None else contract
    style = supplied_style if style is None else style
    monkeypatch.setattr('research.figures.raster_finish.generate_image_candidates', generator)
    with TemporaryDirectory(dir=tmp_path) as stage:
        result = finish_raster(client, tmp_path / 'published', stage, graph, contract, style,
                               feedback={'mechanism': {'error': 'Observed overlap'}},
                               prior_plans={'mechanism': {'hero': {'detail': 'Keep the causal mask'}}},
                               reference_paths=reference_paths)
    return result, graph


def test_one_plan_one_batch_three_pixel_reviews_publish_only_selected_artifacts(tmp_path, monkeypatch):
    client, generator = RasterModelDouble(), ImageBatchDouble()
    original_config = deepcopy(client.config)
    graph, contract, style = supplied()
    original_graph, original_contract = deepcopy(graph), deepcopy(contract)
    reference = tmp_path / 'visual-reference.png'
    Image.new('RGB', (80, 60), 'white').save(reference)
    (paths, selection), updated = invoke(tmp_path, client, generator, monkeypatch,
        graph=graph, contract=contract, style=style, reference_paths=[reference])
    assert client.config == original_config
    assert contract == original_contract
    assert len(client.calls) == 4 and len(generator.calls) == 1
    assert len(generator.calls[0]['variants']) == 3
    assert generator.calls[0]['reference_paths'] == [reference]
    planner = client.calls[0]
    assert planner['payload']['source_contract'] == contract
    assert planner['payload']['observed_defects']['mechanism']['error'] == 'Observed overlap'
    assert planner['payload']['prior_native_plans']['mechanism']['hero']['detail'] == 'Keep the causal mask'
    assert len(planner['images']) == 1
    assert OPTIMIZATION_TARGET in planner['instruction']
    assert OPTIMIZATION_TARGET in generator.calls[0]['prompt']
    source_json = generator.calls[0]['prompt'].split('FULL ORIGINAL FROZEN SCIENTIFIC SOURCE CONTRACT:\n')[1]
    assert json.loads(source_json) == contract
    assert all(len(call['images']) == 3 for call in client.calls[1:])
    assert all(image.startswith('data:image/') for call in client.calls for image in call['images'])
    assert all(call['payload']['context']['production_contract'] == contract for call in client.calls[1:])
    assert all('caption_text' in call['payload']['actual_caption_contexts']['candidate1']
               for call in client.calls[1:])
    assert selection['candidate_id'] == 'candidate1'
    assert selection['publication_gate_passed'] is True
    assert selection['quality_status'] == 'passed'
    assert set(paths) == {'png', 'pdf', 'data', 'style', 'source', 'caption_context', 'report', 'selection'}
    assert {file.name for file in (tmp_path / 'published').iterdir()} == {Path(file).name for file in paths.values()}
    assert Path(paths['png']).read_bytes() == generator.originals['candidate1']
    page = PdfReader(paths['pdf']).pages[0]
    assert float(page.mediabox.width) / 72 == pytest.approx(6.5)
    assert float(page.mediabox.height) / 72 == pytest.approx(4.4)
    assert not page.extract_text().strip()
    images = [value.get_object() for value in page['/Resources']['/XObject'].values()]
    assert [(item['/Width'], item['/Height']) for item in images] == [(1950, 1320)]
    report = json.loads(Path(paths['report']).read_text())
    assert report['effective_ppi'] == pytest.approx(300)
    assert report['quality_issues'] == report['quality_warnings'] == []
    assert report['editable'] is False and report['minimum_font_pt'] is None
    assert 'svg' not in paths and 'scene' not in paths
    assert 'reservation' not in report['image_generation_receipt']
    assert 'revised_prompt' not in report['image_generation_receipt']
    assert all('response_path' not in review['provider_receipt'] for review in selection['reviews'])
    assert 'raw_provider_log' not in ''.join(Path(paths[key]).read_text()
                                           for key in ('data', 'style', 'source', 'report', 'selection'))
    data = json.loads(Path(paths['data']).read_text())
    for field in ('nodes', 'edges', 'story_context', 'key_operation_id'):
        assert updated[field] == data[field] == original_graph[field]
    for field in ('production_scene', 'production_composition', 'asset_data'):
        assert field not in updated and field not in data
    assert updated['production_contract'] == data['production_contract'] == contract
    assert updated['production_raster'] == data['production_raster']
    assert json.loads(Path(paths['selection']).read_text()) == selection
    assert not any(path.is_dir() for path in tmp_path.iterdir() if path.name != 'published')


@pytest.mark.parametrize('dimensions,expected_size', [
    ((2600, 1300), (6.5, 3.25)), ((1300, 2600), (2.2, 4.4)),
])
def test_aspect_containment_reports_actual_physical_size_and_original_pixels(
        tmp_path, monkeypatch, dimensions, expected_size):
    generator = ImageBatchDouble(dimensions)
    (paths, _), _ = invoke(tmp_path, RasterModelDouble(count=2), generator, monkeypatch)
    report = json.loads(Path(paths['report']).read_text())
    assert report['contained_image_width_in'] == pytest.approx(expected_size[0])
    assert report['contained_image_height_in'] == pytest.approx(expected_size[1])
    assert report['effective_ppi'] == pytest.approx(dimensions[0] / expected_size[0])
    assert Path(paths['png']).read_bytes() == generator.originals['candidate1']
    image = next(iter(PdfReader(paths['pdf']).pages[0]['/Resources']['/XObject'].values())).get_object()
    assert (image['/Width'], image['/Height']) == dimensions


def test_low_resolution_is_retained_truthfully_as_best_available_without_upsampling(tmp_path, monkeypatch):
    generator = ImageBatchDouble((1536, 1024))
    (paths, selection), graph = invoke(tmp_path, RasterModelDouble(count=2), generator, monkeypatch)
    report = json.loads(Path(paths['report']).read_text())
    assert report['effective_ppi'] == pytest.approx(1536 / 6.5)
    assert report['quality_issues'] == []
    assert report['quality_warnings'][0]['id'] == 'raster_resolution_below_250_ppi'
    assert selection['publication_gate_passed'] is False
    assert selection['quality_status'] == report['quality_status'] == 'best_available'
    assert graph['production_raster']['publication_gate_passed'] is False
    assert Path(paths['png']).read_bytes() == generator.originals['candidate1']


def test_visual_revise_can_choose_best_but_cannot_claim_publication_pass(tmp_path, monkeypatch):
    client = RasterModelDouble(count=2,
        scores={('Evidence Reviewer', 'candidate1'): 4.9,
                ('Figure Critic', 'candidate1'): 3.9,
                ('Visual Editor', 'candidate1'): 4.9},
        verdicts={('Figure Critic', 'candidate1'): 'revise'})
    (paths, selection), _ = invoke(tmp_path, client, ImageBatchDouble(), monkeypatch)
    assert selection['candidate_id'] == 'candidate1'
    assert selection['quality_status'] == 'best_available'
    assert selection['publication_gate_passed'] is False
    assert json.loads(Path(paths['report']).read_text())['publication_gate_passed'] is False
    assert len(client.calls) == 4


@pytest.mark.parametrize('verdict,score', [('reject', 5), ('revise', 5), ('accept', 3.99)])
def test_scientific_rejection_or_subfour_fidelity_never_publishes_or_changes_graph(
        tmp_path, monkeypatch, verdict, score):
    client = RasterModelDouble(count=2,
        scores={('Evidence Reviewer', f'candidate{index}'): score for index in (1, 2)},
        verdicts={('Evidence Reviewer', f'candidate{index}'): verdict for index in (1, 2)})
    generator = ImageBatchDouble()
    graph, contract, style = supplied()
    original = deepcopy(graph)
    with pytest.raises(ValueError, match='No figure candidate satisfies'):
        invoke(tmp_path, client, generator, monkeypatch, graph=graph, contract=contract, style=style)
    assert graph == original
    assert not (tmp_path / 'published').exists()
    assert len(client.calls) == 4 and len(generator.calls) == 1


@pytest.mark.parametrize('count', [True, False, None, '3', 1, 6, 3.0])
def test_bad_candidate_count_fails_before_any_model_or_generation_request(tmp_path, monkeypatch, count):
    client, generator = RasterModelDouble(count=count), ImageBatchDouble()
    with pytest.raises(ValueError, match='figure_raster_finish_candidates'):
        invoke(tmp_path, client, generator, monkeypatch)
    assert not client.calls and not generator.calls


@pytest.mark.parametrize('change,message', [
    (lambda plan: plan.update(unrequested_scene={}), 'brief, caption_text and variants only'),
    (lambda plan: plan.update(brief='x ' * 1201), 'at most 1200 words'),
    (lambda plan: plan.update(caption_text='x ' * 181), 'at most 180 words'),
    (lambda plan: plan.update(caption_text=''), 'nonempty'),
    (lambda plan: plan['variants'].pop(), 'exactly the configured'),
    (lambda plan: plan['variants'][1].update(id='candidate1'), 'unique simple IDs'),
    (lambda plan: plan['variants'][0].update(prompt_suffix='x ' * 181), 'bounded nonempty'),
    (lambda plan: plan.update(caption_text='Accuracy is [[metric:invented]].'), 'unbound measurement'),
])
def test_invalid_plan_does_not_generate_or_retry(tmp_path, monkeypatch, change, message):
    client, generator = RasterModelDouble(plan_change=change), ImageBatchDouble()
    with pytest.raises(ValueError, match=message):
        invoke(tmp_path, client, generator, monkeypatch)
    assert len(client.calls) == 1 and not generator.calls
    assert not (tmp_path / 'published').exists()


def test_bound_caption_and_prompt_values_resolve_only_actual_catalog_measurements(tmp_path, monkeypatch):
    def measurement(plan):
        plan['brief'] += ' Supplied accuracy is [[metric:accuracy]].'
        plan['caption_text'] += ' Supplied accuracy is [[metric:accuracy]].'
    client, generator = RasterModelDouble(count=2, plan_change=measurement), ImageBatchDouble()
    (paths, _), _ = invoke(tmp_path, client, generator, monkeypatch)
    assert 'Supplied accuracy is 83.5 %.' in generator.calls[0]['prompt']
    caption = json.loads(Path(paths['caption_context']).read_text())
    assert caption['caption_text'].endswith('Supplied accuracy is 83.5 %.')
    assert 'accuracy' in caption['source_refs']


def test_changed_source_or_invalid_reference_fails_before_planner(tmp_path, monkeypatch):
    client, generator = RasterModelDouble(), ImageBatchDouble()
    graph, contract, style = supplied()
    graph['edges'][0]['label'] = 'Invented edge meaning'
    with pytest.raises(ValueError, match='frozen original source graph'):
        invoke(tmp_path, client, generator, monkeypatch, graph=graph, contract=contract, style=style)
    assert not client.calls and not generator.calls
    reference = tmp_path / 'not-a-png.txt'
    reference.write_text('No pixels')
    with pytest.raises(ValueError, match='valid bounded PNG'):
        invoke(tmp_path, client, generator, monkeypatch, reference_paths=[reference])
    assert not client.calls and not generator.calls


def test_generation_failure_stops_the_single_batch_without_placeholder_or_repair(tmp_path, monkeypatch):
    client = RasterModelDouble(count=2)
    calls = []
    def failure(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError('Unit-only image transport failure')
    with pytest.raises(RuntimeError, match='image transport failure'):
        invoke(tmp_path, client, failure, monkeypatch)
    assert len(calls) == len(client.calls) == 1
    assert not (tmp_path / 'published').exists()


def test_palette_png_is_preserved_and_pdf_embeds_its_original_colors(tmp_path, monkeypatch):
    generator = ImageBatchDouble((1950, 1320), mode='P')
    (paths, _), _ = invoke(tmp_path, RasterModelDouble(count=2), generator, monkeypatch)
    assert Path(paths['png']).read_bytes() == generator.originals['candidate1']
    image = PdfReader(paths['pdf']).pages[0].images[0].image.convert('RGB')
    assert image.size == (1950, 1320)
    assert image.getpixel((0, 0)) == (50, 100, 170)
    assert image.getpixel((50, 50)) == (255, 255, 255)


def test_default_is_one_five_candidate_batch(tmp_path, monkeypatch):
    client, generator = RasterModelDouble(), ImageBatchDouble()
    client.config.pop('figure_raster_finish_candidates')
    (_, selection), _ = invoke(tmp_path, client, generator, monkeypatch)
    assert len(generator.calls) == 1
    assert len(generator.calls[0]['variants']) == 5
    assert len(selection['ranking']) == 5
    assert client.calls[0]['payload']['requested_candidate_count'] == 5
    assert all(len(call['images']) == 5 for call in client.calls[1:])


def test_successful_native_to_raster_republication_removes_only_obsolete_generated_companions(
        tmp_path, monkeypatch):
    from research.figures.scene import render_scene
    graph, contract, style = supplied()
    scene = {
        'version': 1, 'width_in': 6.5, 'height_in': 4.4,
        'panels': [{'id': 'Mechanism', 'role': 'mechanism',
                    'title': 'Causal token aggregation', 'bbox': [0.03, 0.04, 0.94, 0.92]}],
        'objects': [
            {'id': 'Encoding', 'panel': 'Mechanism', 'operation_id': 'Encode',
             'kind': 'tensor', 'label': 'Token encoding', 'bbox': [0.1, 0.25, 0.25, 0.4],
             'detail': 'Each token carries a learned vector representation.',
             'params': {'shape': [3, 3, 2]}, 'evidence_refs': ['operation:Encode']},
            {'id': 'Attention', 'panel': 'Mechanism', 'operation_id': 'Attend',
             'kind': 'matrix', 'label': 'Causal attention', 'bbox': [0.6, 0.25, 0.28, 0.4],
             'detail': 'The triangular mask excludes information from future token positions.',
             'params': {'rows': 3, 'cols': 3, 'mask': 'causal', 'schematic': True},
             'evidence_refs': ['operation:Attend']},
        ],
        'connections': [{'id': 'Features', 'source': 'Encoding', 'target': 'Attention',
                         'semantic_edge': 0, 'label': 'Q, K, V'}], 'annotations': [],
    }
    graph.update(production_scene=scene, production_contract=deepcopy(contract))
    output = tmp_path / 'published'
    native = render_scene(output, graph, style)
    assert all(Path(native[key]).is_file() for key in ('svg', 'scene', 'source', 'report'))
    notes = output / 'scientific-notes.txt'
    notes.write_text('Author-owned source notes')
    generator = ImageBatchDouble()
    (paths, selection), _ = invoke(tmp_path, RasterModelDouble(count=2), generator, monkeypatch,
                                  graph=graph, contract=contract, style=style)
    assert selection['candidate_id'] == 'candidate1'
    assert all(not Path(native[key]).exists() for key in ('svg', 'scene', 'source', 'report'))
    assert notes.read_text() == 'Author-owned source notes'
    assert Path(paths['png']).read_bytes() == generator.originals['candidate1']
    assert json.loads(Path(paths['data']).read_text())['production_raster']['editable'] is False


def test_failed_scientific_review_keeps_previous_native_artifacts(tmp_path, monkeypatch):
    output = tmp_path / 'published'
    output.mkdir()
    prior = {name: 'Previous generated artifact ' + name
             for name in ('figure.svg', 'scene.json', 'render_scene.py', 'render_report.json')}
    for name, text in prior.items():
        (output / name).write_text(text)
    client = RasterModelDouble(count=2,
        verdicts={('Evidence Reviewer', f'candidate{index}'): 'reject' for index in (1, 2)})
    with pytest.raises(ValueError, match='No figure candidate satisfies'):
        invoke(tmp_path, client, ImageBatchDouble(), monkeypatch)
    assert {path.name: path.read_text() for path in output.iterdir()} == prior
