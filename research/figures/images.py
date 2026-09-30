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


def generate_image_candidates(client, output_dir, prompt, variants=None):
    """Generate three actual alternatives by default and register for reviews.

    ``variants`` is a list of {id, prompt_suffix} presentation instructions or
    suffix strings. Only successful actual PNG outputs enter the candidate bundle.
    No synthetic fallback, URL download, model substitution, or automatic retry is
    performed. A partial job retains its actual files and safe failure metadata.
    """
    image, _ = build_image_request(client, prompt)
    variants = deepcopy(DEFAULT_VARIANTS if variants is None else variants)
    if not isinstance(variants, list) or len(variants) < 2:
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
    headers = {'Content-Type': 'application/json'}
    if client.key:
        headers['Authorization'] = f'Bearer {client.key}'
    candidates = []
    for index, variant in enumerate(normalized):
        actual_prompt = ILLUSTRATION_RULE + '\n\nScientific mechanism:\n' + prompt + '\n\nDesign variant:\n' + variant['prompt_suffix']
        _, payload = build_image_request(client, actual_prompt)
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        metadata_path = generated / (variant['id'] + '.metadata.json')
        prompt_path = generated / (variant['id'] + '.prompt.txt')
        prompt_path.write_text(actual_prompt)
        metadata = {'api': 'images', 'model': image['model'], 'reservation': None,
                    'candidate_id': variant['id'], 'candidate_index': index + 1,
                    'evidence_role': 'conceptual_illustration', 'status': 'request_pending',
                    'parameters': {key: value for key, value in payload.items() if key != 'prompt'}}
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
                    response = transport.post(client.base + '/images/generations', headers=headers, content=encoded)
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
    return register_candidates(output, candidates, kind='image')
