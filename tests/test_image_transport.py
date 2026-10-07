"""Image configuration, real loopback failure transports and shared SQLite ledger.

No successful provider response, generated image or paid API call is substituted.
"""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research.agents.provider import ModelClient, ProviderError
from research.figures.images import DEFAULT_VARIANTS, build_image_request, generate_image_candidates, image_generation_config


ROOT = Path(__file__).resolve().parents[1]
IMAGE_CONFIG = {'model': 'explicit-image-model', 'max_request_usd': .2,
                'size': '1024x1024', 'quality': 'high', 'output_format': 'png'}


def local_client(config=None):
    return ModelClient({'kind': 'openai', 'base_url': 'http://127.0.0.1:1/v1',
                        'model': 'unchanged-text-model', 'config': config or {'image_generation': IMAGE_CONFIG}})


def test_image_model_is_explicit_and_independent():
    client = local_client()
    image, payload = build_image_request(client, 'An actual described mechanism')
    assert payload['model'] == 'explicit-image-model'
    assert client.provider['model'] == 'unchanged-text-model'
    assert payload['n'] == 1
    assert 'max_request_usd' not in payload and 'timeout' not in payload
    assert 'response_format' not in payload  # No model-specific parameter guessed.
    image['model'] = 'changed-copy'
    assert client.config['image_generation']['model'] == 'explicit-image-model'
    assert len(DEFAULT_VARIANTS) == 3


@pytest.mark.parametrize('change', [
    {'model': ''}, {'model': None}, {'max_request_usd': 0}, {'max_request_usd': -1},
    {'max_request_usd': True}, {'max_request_usd': float('inf')}, {'max_request_usd': float('nan')},
    {'max_request_usd': '.2'}, {'response_format': 'url'}, {'output_format': 'jpeg'},
    {'n': 3}, {'endpoint': 'https://elsewhere.invalid'}, {'timeout': 0},
    {'timeout': True}, {'size': '0x1024'}, {'quality': []},
])
def test_invalid_image_settings_fail_before_network(change):
    with pytest.raises(ProviderError) as caught:
        image_generation_config({'image_generation': {**IMAGE_CONFIG, **change}})
    assert caught.value.code == 'configuration'


def test_missing_image_model_never_falls_back_to_text_model():
    with pytest.raises(ProviderError):
        build_image_request(local_client({'api': 'responses'}), 'Mechanism')
    with pytest.raises(ValueError):
        build_image_request(local_client(), ' ')


@pytest.mark.parametrize('variants', [[], ['one'], [{'id': 'same', 'prompt_suffix': 'x'}, {'id': 'same', 'prompt_suffix': 'y'}],
                                      [{'id': '../escape', 'prompt_suffix': 'x'}, 'y'],
                                      [{'id': 'ok', 'prompt_suffix': 'x', 'model': 'other'}, 'y']])
def test_invalid_variants_fail_before_reservation(tmp_path, variants):
    with pytest.raises(ValueError):
        generate_image_candidates(local_client(), tmp_path, 'Mechanism', variants)
    assert not list(tmp_path.iterdir())


def test_real_failure_transports_and_image_ledger(tmp_path):
    env = {**os.environ, 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'images.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'), 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, __file__, 'check', str(tmp_path)], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import socket
    import threading

    from research.agents.budget import BudgetExceeded, make_request_guard, usage_summary
    from services.api.db import migrate, Session, Provider, Project, TaskRun, ModelRequest, asdict

    migrate()
    directory = Path(sys.argv[2])
    pricing = {'input_per_million': 1, 'output_per_million': 1, 'currency': 'USD'}

    def saved_provider(base='http://127.0.0.1:1/v1', ceiling=.2, limit=2, project_limit=None, run_limit=None):
        with Session.begin() as session:
            provider = Provider(name='Actual image failure test', kind='openai', base_url=base,
                                model='unchanged-text-model', allow_paid=True,
                                config={'budget_usd': limit, 'pricing': pricing, 'max_retries': 4,
                                        'image_generation': {**IMAGE_CONFIG, 'max_request_usd': ceiling}})
            session.add(provider); session.flush()
            value = asdict(provider, True)
            if project_limit is not None or run_limit is not None:
                project = Project(name='Image accounting test', budget={'allow_paid': True, 'cost_usd': project_limit or 2})
                session.add(project); session.flush()
                run = TaskRun(project_id=project.id, request_id=provider.id, status='running',
                              config={'agent_budget': {'cost': run_limit or 2}})
                session.add(run); session.flush()
                value['_usage_context'] = {'project_id': project.id, 'run_id': run.id}
            return value

    def event(provider):
        return {'phase': 'before', 'api': 'images', 'model': IMAGE_CONFIG['model'],
                'image_generation': deepcopy(provider['config']['image_generation']), 'attempt': 1}

    # Image/text requests share the real locked ledger; an image response's token
    # counts or unverified cost fields cannot free its saved image upper bound.
    provider = saved_provider(limit=.5)
    guard = make_request_guard(provider)
    def reserve(_):
        try:
            return guard(event(provider))
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [value for value in pool.map(reserve, range(20)) if value]
    assert len(accepted) == 2
    guard({'phase': 'after', 'api': 'text', 'reservation': accepted[0],
           'usage': {'input_tokens': 1, 'output_tokens': 1, 'cost': .000001}})
    summary = usage_summary(provider['id'])
    assert summary['reserved_usd'] == .4 and summary['estimated_cost_usd'] is None
    assert summary['known_cost_usd'] == 0 and summary['unknown_cost_requests'] == 1
    assert summary['remaining_usd'] is None
    assert summary['uncertain_requests'] == 1 and all(row['api'] == 'images' for row in summary['requests'])
    guard({'phase': 'error', 'reservation': accepted[1], 'ambiguous': False, 'http_status': 400})
    text_request = guard({'phase': 'before', 'api': 'responses', 'model': provider['model'],
                          'pricing': pricing, 'input_bytes': 0, 'max_output_tokens': 1})
    guard({'phase': 'after', 'reservation': text_request, 'usage': {'input_tokens': 10, 'output_tokens': 2}})
    mixed = usage_summary(provider['id'])
    assert mixed['estimated_cost_usd'] is None and mixed['known_cost_usd'] == .000012
    assert mixed['cost_source'] == 'mixed_known_and_unknown'
    assert mixed['unknown_cost_requests'] == 1
    for limits in ({'project_limit': .39}, {'run_limit': .39}):
        bounded = saved_provider(**limits)
        bounded_guard = make_request_guard(bounded)
        bounded_guard(event(bounded))
        try:
            bounded_guard(event(bounded))
        except BudgetExceeded:
            pass
        else:
            raise AssertionError('Image request escaped an outer limit')
    for bad in (None, True, 0, -1, '0.2'):
        invalid = saved_provider(ceiling=bad)
        try:
            make_request_guard(invalid)(event(invalid))
        except BudgetExceeded:
            pass
        else:
            raise AssertionError('Invalid saved image ceiling accepted')
    changed = saved_provider()
    changed_event = event(changed)
    changed_event['image_generation']['model'] = 'replacement-model'
    try:
        make_request_guard(changed)(changed_event)
    except BudgetExceeded:
        pass
    else:
        raise AssertionError('Image snapshot replacement accepted')

    class FailingEndpoint(BaseHTTPRequestHandler):
        # Deliberately only actual failures. No PNG or success-generation response.
        def log_message(self, *_):
            pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            self.server.requests.append((self.path, json.loads(body)))
            mode = self.server.mode
            if mode == 'disconnect':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            status = {'rejection': 400, 'server_error': 503, 'redirect': 302,
                      'unreadable': 200, 'url_only': 200}[mode]
            raw = (json.dumps({'data': [{'url': 'http://127.0.0.1:1/no-download'}]}).encode()
                   if mode == 'url_only' else b'PRIVATE-ERROR-BODY dummy-test-auth-secret')
            self.send_response(status)
            self.send_header('x-request-id', 'failure-request')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Location', 'http://127.0.0.1:1/never-follow')
            self.end_headers(); self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), FailingEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for mode in ('rejection', 'server_error', 'redirect', 'unreadable', 'url_only', 'disconnect'):
            server.mode, server.requests = mode, []
            provider = saved_provider(base=f'http://127.0.0.1:{server.server_port}/v1')
            client = ModelClient(provider, key='dummy-test-auth-secret')
            out = directory / mode
            try:
                generate_image_candidates(client, out, 'The supplied input enters the described module and yields an output.')
            except ProviderError as exc:
                assert 'PRIVATE-ERROR-BODY' not in str(exc) and 'dummy-test-auth-secret' not in str(exc)
                assert 'http://' not in str(exc)
                assert exc.ambiguous == (mode != 'rejection')
            else:
                raise AssertionError('Failure transport manufactured an image')
            assert len(server.requests) == 1  # max_retries=4 cannot silently multiply paid image calls.
            path, payload = server.requests[0]
            assert path == '/v1/images/generations' and payload['model'] == IMAGE_CONFIG['model'] and payload['n'] == 1
            assert 'conceptual_illustration' in payload['prompt']
            assert 'Do not invent measurements' in payload['prompt']
            assert not list(out.rglob('*.png')) and not (out / 'candidate_manifest.json').exists()
            metadata = json.loads((out / 'generated' / 'overview.metadata.json').read_text())
            assert metadata['status'] == 'failed' and metadata['evidence_role'] == 'conceptual_illustration'
            assert 'PRIVATE-ERROR-BODY' not in json.dumps(metadata) and 'dummy-test-auth-secret' not in json.dumps(metadata)
            with Session() as session:
                rows = list(session.query(ModelRequest).filter_by(provider_id=provider['id']))
                assert len(rows) == 1 and rows[0].status == ('rejected' if mode == 'rejection' else 'uncertain')
                assert rows[0].reserved_microusd == 200000 and rows[0].estimated_microusd is None
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)

    # Actual connection refusal has proof that this attempt never reached a server.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); unused_port = sock.getsockname()[1]
    provider = saved_provider(base=f'http://127.0.0.1:{unused_port}/v1')
    try:
        generate_image_candidates(ModelClient(provider), directory / 'connection_refusal', 'Mechanism')
    except ProviderError as exc:
        assert exc.code == 'transport_error' and not exc.ambiguous
    else:
        raise AssertionError('Connection refusal manufactured an image')
    assert usage_summary(provider['id'])['reserved_usd'] == 0
    print('Image field validation, real HTTP/transport failures and shared image ledger passed')
