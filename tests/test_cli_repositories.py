"""Repository client commands remain offline until explicitly queued."""
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from forest_cli import main as cli
from forest_cli.client import CliError, ForestClient


ROOT = Path(__file__).resolve().parents[1]
URL = 'https://github.com/example/research.git'


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for key in ('FOREST_SERVER', 'FOREST_OWNER_TOKEN', 'FOREST_TOKEN_FILE', 'FOREST_PROJECT_ID'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('FOREST_CLI_CONFIG', str(tmp_path / 'cli.json'))
    monkeypatch.chdir(tmp_path)


class Scenario:
    def __init__(self, responder):
        self.calls = []
        self.responder = responder

    def factory(self, endpoint, **kwargs):
        def dispatch(request):
            call = {'method': request.method, 'path': request.url.path,
                    'body': json.loads(request.content) if request.content else None}
            self.calls.append(call)
            return httpx.Response(200, json=self.responder(call))
        return ForestClient(endpoint, **kwargs, transport=httpx.MockTransport(dispatch))

    def execute(self, arguments):
        return cli.execute(cli.build_parser().parse_args(arguments), client_factory=self.factory)


def no_client(*args, **kwargs):
    pytest.fail('Local validation must not connect to a service')


def test_repository_validate_is_offline_even_with_invalid_connection(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'repository.json'
    path.write_text(json.dumps({'url': URL, 'credential': 'github-read'}))
    monkeypatch.setenv('FOREST_SERVER', 'this-is-not-an-api-url')
    args = cli.build_parser().parse_args(['--json', 'repo', 'validate', '--file', str(path)])
    assert cli.execute(args, client_factory=no_client) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == {'valid': True, 'repository': {'url': URL, 'ref': 'HEAD',
                    'directory': 'source', 'transport': 'https', 'credential': 'github-read'}}


def test_repository_validator_does_not_import_backend_runtime():
    result = subprocess.run([sys.executable, '-c',
        "import sys; from research.execution.repository import normalize_repository; "
        "normalize_repository({'url': 'https://github.com/example/research.git'}); "
        "assert not any(k.startswith(('services.', 'sqlalchemy', 'numpy', 'pydantic')) for k in sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('arguments', [
    ['repo', 'clone', 'https://secret:token@github.com/example/research.git'],
    ['repo', 'clone', '/private/research'],
    ['repo', 'clone', URL, '--directory', '../escape'],
    ['repo', 'clone', URL, '--credential', '/private/key'],
    ['repo', 'clone', URL, '--ref=-c core.sshCommand=bad'],
])
def test_invalid_clone_never_sends_http(arguments):
    with pytest.raises(CliError) as raised:
        cli.execute(cli.build_parser().parse_args(arguments), client_factory=no_client)
    assert raised.value.exit_code == 2
    assert 'secret:token' not in raised.value.message


def test_repository_clone_requires_project_before_http():
    scenario = Scenario(lambda call: pytest.fail('Missing project must not submit work'))
    with pytest.raises(CliError) as raised:
        scenario.execute(['repo', 'clone', URL])
    assert raised.value.code == 'PROJECT_REQUIRED'
    assert scenario.calls == []


def test_repository_clone_queues_one_profile_reference_and_can_wait(capsys):
    def respond(call):
        if call['method'] == 'POST':
            return {'id': 'run-1', 'kind': 'repository_clone', 'status': 'queued',
                    'request_id': call['body']['request_id']}
        assert call['path'] == '/api/runs/run-1'
        return {'id': 'run-1', 'status': 'completed'}
    scenario = Scenario(respond)
    assert scenario.execute(['--json', '--project', 'p1', 'repo', 'clone', URL,
        '--ref', 'v1.0', '--directory', 'vendor/research', '--transport', 'https',
        '--credential', 'github-read', '--request-id', 'source-01', '--wait', '--wait-timeout', '3']) == 0
    assert scenario.calls[0] == {'method': 'POST', 'path': '/api/projects/p1/repositories/clone',
        'body': {'repository': {'url': URL, 'ref': 'v1.0', 'directory': 'vendor/research',
                 'transport': 'https', 'credential': 'github-read'}, 'request_id': 'source-01'}}
    assert [call['method'] for call in scenario.calls] == ['POST', 'GET']
    assert json.loads(capsys.readouterr().out)['outcome'] == 'completed'


def test_repository_submission_is_accepted_before_execution(capsys):
    scenario = Scenario(lambda call: {'id': 'queued-source', 'status': 'queued',
                                     'kind': 'repository_clone', 'request_id': 'source-02'})
    assert scenario.execute(['--json', '--project', 'p1', 'repo', 'clone', URL,
                             '--request-id', 'source-02']) == 0
    assert json.loads(capsys.readouterr().out)['submission'] == 'accepted'
    assert len(scenario.calls) == 1


@pytest.mark.parametrize('config', [
    {'repository': {'url': URL, 'token_file': '/private/token'}},
    {'_repository_source': {'commit': 'a' * 40}},
])
def test_node_run_rejects_repository_credentials_or_provenance_before_post(config, tmp_path):
    path = tmp_path / 'run.json'
    path.write_text(json.dumps(config))
    scenario = Scenario(lambda call: pytest.fail('Invalid run config must not submit work'))
    with pytest.raises(CliError) as raised:
        scenario.execute(['node', 'run', 'n1', '--config-file', str(path)])
    assert raised.value.exit_code == 2 and scenario.calls == []


@pytest.mark.parametrize(('arguments', 'path'), [
    (['repo', 'inspect', 'source-1'], '/api/runs/source-1/repository'),
    (['run', 'diagnostics', 'source-1'], '/api/runs/source-1/diagnostics'),
])
def test_source_inspection_and_diagnostics_use_read_only_endpoints(arguments, path):
    scenario = Scenario(lambda call: {'run_id': 'source-1', 'status': 'completed'})
    assert scenario.execute(arguments) == 0
    assert scenario.calls == [{'method': 'GET', 'path': path, 'body': None}]
