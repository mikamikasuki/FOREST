"""Actual compatible Image API requests for independently reviewed illustrations.

The configured image model is independent of the text model. Every POST reserves
the saved per-request USD ceiling in the existing provider/project/run ledger.
Generated illustrations never stand in for experimental plots or measurements.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import re
import time
import warnings

import httpx
from PIL import Image, UnidentifiedImageError

from research.agents.provider import ModelClient, ProviderError, http_error_policy
from research.figures.workflow import register_candidates


ILLUSTRATION_RULE = (
    'Create a publication-quality conceptual illustration of the supplied scientific mechanism. '
    'Depict only components explicitly described in the prompt. Do not invent measurements, '
    'performance curves, statistical plots, data tables, experimental photographs, microscopy, '
    'or scientific observations. This image is conceptual_illustration, not measured evidence. '
    'Use legible concise labels, a clear reading order, restrained colors and a coherent hierarchy.'
)
DEFAULT_VARIANTS = [
    {'id': 'overview', 'prompt_suffix': 'Emphasize the full mechanism and its input-to-output relationships.'},
    {'id': 'mechanism', 'prompt_suffix': 'Emphasize the key mechanism with separated modules and explicit directional connections.'},
    {'id': 'print', 'prompt_suffix': 'Use a restrained print-oriented composition with high contrast and minimal visual clutter.'},
]
MAX_PNG_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_PNG_BYTES = 50 * 1024 * 1024
MAX_REFERENCE_IMAGES = 4


def image_generation_config(config):
    """Validate explicit image options without selecting or replacing a model."""
    image = config.get('image_generation') if isinstance(config, dict) else None
    if not isinstance(image, dict):
        raise ProviderError('Configure image_generation.model and max_request_usd before generating images', code='configuration')
    allowed = {'model', 'max_request_usd', 'size', 'quality', 'background', 'style',
               'moderation', 'output_format', 'response_format', 'timeout'}
    if set(image) - allowed:
        raise ProviderError('Unsupported image_generation fields; image count and endpoint are controlled by the workflow', code='configuration')
    model = image.get('model')
    if not isinstance(model, str) or not model.strip() or len(model) > 240:
        raise ProviderError('image_generation.model must explicitly name the image model', code='configuration')
    maximum = image.get('max_request_usd')
    if (isinstance(maximum, bool) or not isinstance(maximum, (int, float))
            or not math.isfinite(maximum) or maximum <= 0):
        raise ProviderError('image_generation.max_request_usd must be a positive finite per-request upper bound', code='configuration')
    for key in allowed - {'model', 'max_request_usd', 'timeout'}:
        if key in image and (not isinstance(image[key], str) or not image[key].strip() or len(image[key]) > 64):
            raise ProviderError(f'image_generation.{key} must be a nonempty option string', code='configuration')
    if image.get('output_format', 'png') != 'png' or image.get('response_format', 'b64_json') != 'b64_json':
        raise ProviderError('Image candidates require PNG base64 data; URL responses and other encodings are unsupported', code='configuration')
    if 'size' in image and not re.fullmatch(r'auto|[1-9][0-9]*x[1-9][0-9]*', image['size']):
        raise ProviderError('image_generation.size must be auto or WIDTHxHEIGHT', code='configuration')
    if 'timeout' in image and (isinstance(image['timeout'], bool) or not isinstance(image['timeout'], (int, float))
                               or not math.isfinite(image['timeout']) or image['timeout'] <= 0):
        raise ProviderError('image_generation.timeout must be a positive finite number', code='configuration')
    return deepcopy(image)


def build_image_request(client, prompt):
    if not isinstance(client, ModelClient):
        raise TypeError('Image generation requires the existing validated ModelClient')
    if client.api == 'ollama':
        raise ProviderError('Image generation requires a configured OpenAI-compatible image endpoint', code='configuration')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('An image generation prompt must describe the actual mechanism')
    image = image_generation_config(client.config)
    payload = {key: value for key, value in image.items() if key not in ('max_request_usd', 'timeout')}
    # Some models default to PNG/base64, others need the explicitly configured
    # response_format option. Do not guess model-specific parameters or aliases.
    payload.update(prompt=prompt, n=1)
    return image, payload


def _public_id(value):
    return value if isinstance(value, str) and 0 < len(value) <= 240 else None


def _image_usage(value):
    raw = value.get('usage') if isinstance(value, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    usage = {'cost': None, 'cost_source': 'unknown'}
    for key in ('input_tokens', 'output_tokens', 'total_tokens'):
        count = raw.get(key)
        usage[key] = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    return usage


def _png(value, request_id):
    data = value.get('data') if isinstance(value, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ProviderError('Image endpoint returned no single image candidate', code='invalid_image_response', request_id=request_id, ambiguous=True)
    encoded = data[0].get('b64_json')
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_PNG_BYTES + 2) // 3):
        raise ProviderError('Image endpoint must return bounded base64 PNG data; returned URLs are never downloaded', code='invalid_image_response', request_id=request_id, ambiguous=True)
    try:
        content = base64.b64decode(encoded, validate=True)
        if len(content) > MAX_PNG_BYTES or not content.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Expected PNG')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != 'PNG':
                    raise ValueError('Expected PNG')
                width, height = image.size
                image.verify()
    except (binascii.Error, ValueError, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ProviderError('Image endpoint returned invalid PNG data', code='invalid_image_response', request_id=request_id, ambiguous=True) from None
    return content, width, height, data[0].get('revised_prompt') if isinstance(data[0].get('revised_prompt'), str) else None


def generate_image_candidates(client, output_dir, prompt, variants=None, *, reference_paths=None):
    """Generate three actual alternatives by default and register for reviews.

    ``variants`` is a list of {id, prompt_suffix} presentation instructions or
    suffix strings. Only successful actual PNG outputs enter the candidate bundle.
    No synthetic fallback, URL download, model substitution, or automatic retry is
    performed. A partial job retains its actual files and safe failure metadata.
    Explicit PNG references use the compatible image-edit endpoint. Only the
    passed files are uploaded, in order; absent references retain generation.
    """
    return _generate_images(client, output_dir, prompt, variants, reference_paths=reference_paths)


def generate_image_asset(client, output_dir, prompt, identifier='illustration'):
    """Make one real illustration component; composition owns text and arrows.

    This uses the same request authorization, transport and atomic spending
    reservations as complete-image candidates. It does not synthesize a
    placeholder when the configured image provider is unavailable.
    """
    return _generate_images(client, output_dir, prompt, [{
        'id': identifier,
        'prompt_suffix': ('Draw only this isolated scientific object on a plain white or '
                          'explicitly configured transparent background. No text, letters, '
                          'numbers, labels, arrows, panels, legend or surrounding workflow. '
                          'Use precise scientific illustration, coherent lighting and '
                          'clean contours. The editable composition supplies all typography '
                          'and connections separately.'),
    }], asset=True)


def _reference_images(reference_paths):
    """Read only explicit, bounded PNG inputs before reserving a request."""
    if reference_paths is None:
        return []
    if not isinstance(reference_paths, (list, tuple)) or len(reference_paths) > MAX_REFERENCE_IMAGES:
        raise ValueError('Image references must be a list of at most four explicit PNG files')
    references, total = [], 0
    for index, filename in enumerate(reference_paths):
        if not isinstance(filename, (str, Path)) or not str(filename).strip():
            raise ValueError('Image references must identify explicit local PNG files')
        try:
            path = Path(filename)
            if not path.is_file():
                raise ValueError('Expected a file')
            with path.open('rb') as source:
                content = source.read(MAX_REFERENCE_PNG_BYTES + 1)
            total += len(content)
            if (not content.startswith(b'\x89PNG\r\n\x1a\n')
                    or len(content) > MAX_REFERENCE_PNG_BYTES or total > MAX_PNG_BYTES):
                raise ValueError('PNG reference exceeds its bounded input')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.format != 'PNG':
                        raise ValueError('Expected PNG')
                    width, height = image.size
                    image.verify()
        except (OSError, ValueError, UnidentifiedImageError,
                Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError('Image reference must be a valid bounded PNG file; local paths are not exposed') from None
        references.append({'filename': f'reference-{index + 1}.png', 'content': content,
                           'width_px': width, 'height_px': height})
    return references


def _generate_images(client, output_dir, prompt, variants=None, *, asset=False, reference_paths=None):
    image, _ = build_image_request(client, prompt)
    if asset and reference_paths:
        raise ValueError('Isolated image assets do not accept complete-figure references')
    references = _reference_images(reference_paths)
    variants = deepcopy(DEFAULT_VARIANTS if variants is None else variants)
    if not isinstance(variants, list) or len(variants) < (1 if asset else 2):
        raise ValueError('Image review needs at least two independently generated candidates')
    normalized, seen = [], set()
    for index, item in enumerate(variants):
        if isinstance(item, str):
            item = {'id': f'candidate{index + 1}', 'prompt_suffix': item}
        if not isinstance(item, dict) or set(item) - {'id', 'prompt_suffix'}:
            raise ValueError('Image variants support only id and prompt_suffix')
        identifier, suffix = item.get('id'), item.get('prompt_suffix')
        if (not isinstance(identifier, str) or len(identifier) > 80 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', identifier)
                or identifier in seen or not isinstance(suffix, str) or not suffix.strip()):
            raise ValueError('Image variants need unique simple IDs and nonempty presentation instructions')
        seen.add(identifier)
        normalized.append({'id': identifier, 'prompt_suffix': suffix})
    guard = client.request_guard
    if guard is None:
        from research.agents.budget import make_request_guard
        guard = make_request_guard(client.provider)
    output = Path(output_dir).resolve()
    generated = (output / 'generated').resolve()
    if not generated.is_relative_to(output):
        raise ValueError('Image output must remain within the job directory')
    generated.mkdir(parents=True, exist_ok=True)
    for variant in normalized:
        for suffix in ('.prompt.txt', '.metadata.json', '.png'):
            if not (generated / (variant['id'] + suffix)).resolve().is_relative_to(output):
                raise ValueError('Image output files must remain within the job directory')
    headers = {} if references else {'Content-Type': 'application/json'}
    if client.key:
        headers['Authorization'] = f'Bearer {client.key}'
    candidates = []
    for index, variant in enumerate(normalized):
        actual_prompt = ILLUSTRATION_RULE + '\n\nScientific mechanism:\n' + prompt + '\n\nDesign variant:\n' + variant['prompt_suffix']
        _, payload = build_image_request(client, actual_prompt)
        endpoint = '/images/edits' if references else '/images/generations'
        if references:
            request = httpx.Request('POST', client.base + endpoint, headers=headers,
                                    data={key: str(value) for key, value in payload.items()},
                                    files=[('image[]', (item['filename'], item['content'], 'image/png'))
                                           for item in references])
            encoded = request.read()
            request_headers = dict(request.headers)
        else:
            encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            request_headers = headers
        metadata_path = generated / (variant['id'] + '.metadata.json')
        prompt_path = generated / (variant['id'] + '.prompt.txt')
        prompt_path.write_text(actual_prompt)
        metadata = {'api': 'images', 'model': image['model'], 'reservation': None,
                    'candidate_id': variant['id'], 'candidate_index': index + 1,
                    'evidence_role': 'conceptual_illustration', 'status': 'request_pending',
                    'parameters': {key: value for key, value in payload.items() if key != 'prompt'}}
        if references:
            metadata.update(endpoint='images/edits', reference_images=[
                {'index': index + 1, 'width_px': item['width_px'], 'height_px': item['height_px'],
                 'bytes': len(item['content'])} for index, item in enumerate(references)])
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        start = time.monotonic()
        reservation = guard({'phase': 'before', 'api': 'images', 'model': image['model'],
                             'image_generation': image, 'attempt': 1, 'input_bytes': len(encoded)})
        metadata['reservation'] = reservation
        try:
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        except OSError:
            # This failure precedes network transport, so no API charge occurred.
            guard({'phase': 'error', 'api': 'images', 'reservation': reservation,
                   'ambiguous': False, 'retryable': False})
            raise
        try:
            with httpx.Client(timeout=image.get('timeout', client.timeout), follow_redirects=False) as transport:
                try:
                    response = transport.post(client.base + endpoint, headers=request_headers, content=encoded)
                except httpx.RequestError as exc:
                    rejected = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                    guard({'phase': 'error', 'api': 'images', 'reservation': reservation,
                           'ambiguous': not rejected, 'retryable': rejected})
                    raise ProviderError('Image request failed before a response; no replacement image was generated',
                                        code='transport_error', retryable=rejected, ambiguous=not rejected) from None
            request_id = _public_id(response.headers.get('x-request-id'))
            if not 200 <= response.status_code < 300:
                retryable, ambiguous = http_error_policy(response.status_code)
                guard({'phase': 'error', 'api': 'images', 'reservation': reservation, 'request_id': request_id,
                       'http_status': response.status_code, 'retryable': retryable, 'ambiguous': ambiguous})
                raise ProviderError(f'Image endpoint returned HTTP {response.status_code}', code='http_error', request_id=request_id,
                                    http_status=response.status_code, retryable=retryable, ambiguous=ambiguous)
            try:
                value = response.json()
            except (ValueError, TypeError):
                guard({'phase': 'error', 'api': 'images', 'reservation': reservation, 'request_id': request_id,
                       'http_status': response.status_code, 'ambiguous': True, 'retryable': False})
                raise ProviderError('Image endpoint returned an unreadable response', code='invalid_image_response', request_id=request_id, ambiguous=True) from None
            usage = _image_usage(value)
            response_id = _public_id(value.get('id')) if isinstance(value, dict) else None
            guard({'phase': 'after', 'api': 'images', 'reservation': reservation, 'request_id': request_id,
                   'response_id': response_id, 'usage': usage, 'outcome': 'image_response_received'})
            content, width, height, revised_prompt = _png(value, request_id)
            path = generated / (variant['id'] + '.png')
            path.write_bytes(content)
            metadata.update(status='generated', width_px=width, height_px=height,
                            actual_image_call=True, request_id=request_id, response_id=response_id,
                            usage=usage, elapsed_seconds=time.monotonic() - start)
            if revised_prompt is not None:
                metadata['revised_prompt'] = revised_prompt
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
            candidates.append({'id': variant['id'], 'outputs': {'png': str(path), 'prompt': str(prompt_path),
                                                               'generation_metadata': str(metadata_path)},
                               'report': metadata, 'style': {'design_variant': variant['prompt_suffix']}})
        except ProviderError as exc:
            metadata.update(status='failed', code=exc.code, http_status=exc.http_status,
                            ambiguous=exc.ambiguous, request_id=exc.request_id)
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
            raise
    return candidates[0] if asset else register_candidates(output, candidates, kind='image')
