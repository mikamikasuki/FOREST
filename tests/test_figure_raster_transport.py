"""PNG-reference transport contracts; endpoint responses are unit-test doubles.

These checks verify upload bytes, accounting and failures, not live-model quality.
"""
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import base64
import io
import json

import httpx
from PIL import Image
import pytest

from research.agents.provider import ModelClient, ProviderError
from research.figures import images


def png_bytes(color='white'):
    stream = io.BytesIO()
    Image.new('RGB', (16, 12), color).save(stream, format='PNG')
    return stream.getvalue()


def client_and_events():
    events = []
    def guard(event):
        events.append(deepcopy(event))
        return 'unit-reservation' if event['phase'] == 'before' else None
    client = ModelClient({
        'kind': 'openai', 'base_url': 'http://127.0.0.1:1/v1', 'model': 'text-model',
        'config': {'max_retries': 5, 'image_generation': {
            'model': 'configured-image-model', 'max_request_usd': .2,
            'size': '1024x1024', 'output_format': 'png'}},
    }, key='unit-authorization', request_guard=guard)
    return client, events


@pytest.fixture
def endpoint(monkeypatch):
    requests = []
    real_client = httpx.Client
    def install(*, status=200, value=None, error=None):
        response = value if value is not None else {
            'id': 'unit-image-response', 'data': [{'b64_json': base64.b64encode(png_bytes()).decode()}],
            'usage': {'input_tokens': 17, 'output_tokens': 31},
        }
        def handle(request):
            request.read()
            requests.append(request)
            if error is not None:
                raise error('Unit transport failure', request=request)
            return httpx.Response(status, json=response, headers={'x-request-id': 'unit-image-request'})
        monkeypatch.setattr(images.httpx, 'Client', lambda **kwargs: real_client(
            **kwargs, transport=httpx.MockTransport(handle)))
        return requests
    return install


def references(tmp_path, count=2):
    paths, payloads = [], []
    for index in range(count):
        payload = png_bytes(('red', 'blue', 'green', 'white')[index % 4])
        path = tmp_path / f'private-source-{index}.png'
        path.write_bytes(payload)
        paths.append(path); payloads.append(payload)
    return paths, payloads


def multipart(request):
    header = ('Content-Type: ' + request.headers['content-type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode()
    message = BytesParser(policy=policy.default).parsebytes(header + request.content)
    fields, files = {}, []
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        if part.get_filename():
            files.append((name, part.get_filename(), part.get_content_type(), part.get_payload(decode=True)))
        else:
            fields[name] = part.get_payload(decode=True).decode()
    return fields, files


def test_edits_upload_exact_ordered_pngs_and_preserve_accounting(tmp_path, endpoint):
    requests = endpoint()
    paths, payloads = references(tmp_path, 4)
    client, events = client_and_events()
    config = deepcopy(client.config)
    bundle = images.generate_image_candidates(client, tmp_path / 'outputs', 'The supplied mechanism.',
        variants=['Architecture overview.', 'Mechanism close-up.'], reference_paths=paths)
    assert len(requests) == 2 and len(bundle['candidates']) == 2
    for request in requests:
        assert request.url.path == '/v1/images/edits'
        assert request.headers['authorization'] == 'Bearer unit-authorization'
        assert request.headers['content-type'].startswith('multipart/form-data; boundary=')
        fields, files = multipart(request)
        assert fields['model'] == 'configured-image-model' and fields['n'] == '1'
        assert fields['output_format'] == 'png' and 'Do not invent measurements' in fields['prompt']
        assert 'max_request_usd' not in fields and 'timeout' not in fields
        assert files == [('image[]', f'reference-{index + 1}.png', 'image/png', payload)
                         for index, payload in enumerate(payloads)]
        assert b'private-source-' not in request.content
    assert client.config == config and client.provider['model'] == 'text-model'
    assert [event['phase'] for event in events] == ['before', 'after', 'before', 'after']
    for index in (0, 1):
        assert events[index * 2]['input_bytes'] == len(requests[index].content)
        assert events[index * 2]['attempt'] == 1
        after = events[index * 2 + 1]
        assert after['reservation'] == 'unit-reservation'
        assert after['request_id'] == 'unit-image-request'
        assert after['response_id'] == 'unit-image-response'
        assert after['usage']['cost'] is None
    for candidate in bundle['candidates']:
        report = candidate['report']
        assert report['actual_image_call'] is True and report['endpoint'] == 'images/edits'
        assert len(report['reference_images']) == 4
        assert str(tmp_path) not in json.dumps(report)
        assert open(candidate['outputs']['png'], 'rb').read() == png_bytes()


@pytest.mark.parametrize('reference_paths', [None, []])
def test_absent_references_keep_generation_json(tmp_path, endpoint, reference_paths):
    requests = endpoint()
    client, events = client_and_events()
    images.generate_image_candidates(client, tmp_path, 'Mechanism.', ['Overview.', 'Detail.'],
                                     reference_paths=reference_paths)
    assert len(requests) == 2
    for request in requests:
        assert request.url.path == '/v1/images/generations'
        assert request.headers['content-type'] == 'application/json'
        assert json.loads(request.content)['n'] == 1
    assert [event['phase'] for event in events] == ['before', 'after', 'before', 'after']


def test_asset_interface_still_makes_one_generation(tmp_path, endpoint):
    requests = endpoint()
    client, _ = client_and_events()
    candidate = images.generate_image_asset(client, tmp_path, 'An isolated scientific object.')
    assert len(requests) == 1 and requests[0].url.path == '/v1/images/generations'
    assert candidate['report']['actual_image_call'] is True


@pytest.mark.parametrize('bad', ['list_shape', 'too_many', 'missing', 'directory', 'invalid', 'oversize', 'aggregate'])
def test_invalid_references_fail_before_network_or_reservation(tmp_path, endpoint, monkeypatch, bad):
    requests = endpoint()
    paths, payloads = references(tmp_path)
    if bad == 'list_shape':
        paths = str(paths[0])
    elif bad == 'too_many':
        paths = paths * 3
    elif bad == 'missing':
        paths = [tmp_path / 'unavailable.png']
    elif bad == 'directory':
        paths = [tmp_path]
    elif bad == 'invalid':
        paths[0].write_bytes(b'not PNG')
    elif bad == 'oversize':
        monkeypatch.setattr(images, 'MAX_REFERENCE_PNG_BYTES', len(payloads[0]) - 1)
    elif bad == 'aggregate':
        monkeypatch.setattr(images, 'MAX_PNG_BYTES', max(map(len, payloads)) + 1)
    client, events = client_and_events()
    output = tmp_path / 'outputs'
    with pytest.raises(ValueError) as caught:
        images.generate_image_candidates(client, output, 'Mechanism.', reference_paths=paths)
    assert str(tmp_path) not in str(caught.value)
    assert not requests and not events and not output.exists()


def test_edit_http_failure_is_not_retried_or_replaced(tmp_path, endpoint):
    requests = endpoint(status=503, value={'error': {'message': 'PRIVATE response body'}})
    paths, _ = references(tmp_path, 1)
    client, events = client_and_events()
    with pytest.raises(ProviderError) as caught:
        images.generate_image_candidates(client, tmp_path / 'outputs', 'Mechanism.', reference_paths=paths)
    assert len(requests) == 1 and requests[0].url.path == '/v1/images/edits'
    assert caught.value.request_id == 'unit-image-request' and caught.value.ambiguous
    assert 'PRIVATE' not in str(caught.value) and 'unit-authorization' not in str(caught.value)
    assert [event['phase'] for event in events] == ['before', 'error']
    assert events[-1]['reservation'] == 'unit-reservation' and events[-1]['ambiguous']
    assert not list((tmp_path / 'outputs').rglob('*.png'))


@pytest.mark.parametrize('value', [
    {'data': [{'url': 'https://unrequested.invalid/no-download'}]},
    {'data': [{'b64_json': base64.b64encode(b'not PNG').decode()}]},
    {'data': []},
])
def test_edit_invalid_output_never_creates_an_image(tmp_path, endpoint, value):
    requests = endpoint(value=value)
    paths, _ = references(tmp_path, 1)
    client, events = client_and_events()
    with pytest.raises(ProviderError) as caught:
        images.generate_image_candidates(client, tmp_path / 'outputs', 'Mechanism.', reference_paths=paths)
    assert len(requests) == 1 and caught.value.code == 'invalid_image_response'
    assert caught.value.ambiguous and caught.value.request_id == 'unit-image-request'
    assert [event['phase'] for event in events] == ['before', 'after']
    assert not list((tmp_path / 'outputs').rglob('*.png'))


@pytest.mark.parametrize('error,ambiguous', [(httpx.ConnectError, False), (httpx.ReadTimeout, True)])
def test_edit_transport_error_preserves_reservation_semantics(tmp_path, endpoint, error, ambiguous):
    requests = endpoint(error=error)
    paths, _ = references(tmp_path, 1)
    client, events = client_and_events()
    with pytest.raises(ProviderError) as caught:
        images.generate_image_candidates(client, tmp_path / 'outputs', 'Mechanism.', reference_paths=paths)
    assert len(requests) == 1 and caught.value.code == 'transport_error'
    assert caught.value.ambiguous is ambiguous and caught.value.retryable is (not ambiguous)
    assert [event['phase'] for event in events] == ['before', 'error']
    assert events[-1]['ambiguous'] is ambiguous
