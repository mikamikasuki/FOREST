"""Diagnostics inspect actual receipts without treating them as live remote state."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

from research.execution.diagnostics import run_diagnostics
from research.agents.processes import ManagedProcesses


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_command_progress_and_source_keep_real_receipts_and_redact_credentials(tmp_path):
    write(tmp_path / 'execution.json', {'command': ['curl', '--token', 'private-value',
        '-H', 'Authorization: Bearer other-private'], 'status': 'running', 'started_at': 10})
    write(tmp_path / 'repository.json', {'url': 'https://github.com/owner/repo.git',
        'requested_ref': 'main', 'commit': 'a' * 40, 'directory': 'source', 'transport': 'https',
        'token': 'must-not-be-returned', 'identity_file': '/private/key'})
    (tmp_path / 'stdout.txt').write_text('real command output\n')
    result = run_diagnostics(tmp_path, run_id='r1', status='running', config={'repository': {'url': 'example'}, 'env': {'API_TOKEN': 'private-value'}}, resource={'elapsed_seconds': 4})
    assert result['phase'] == 'executing'
    assert result['repository']['commit'] == 'a' * 40
    assert result['logs']['bytes'] == len(b'real command output\n')
    output = json.dumps(result)
    assert all(secret not in output for secret in ('private-value', 'other-private', 'must-not-be-returned', '/private/key'))
    assert result['processes'][0]['command'][2] == '[redacted]'
    assert result['observation'] == 'saved_execution_receipts'


def test_container_receipt_is_not_mistaken_for_live_poll_or_run_completion(tmp_path):
    write(tmp_path / 'container_task.json', {'task_id': 'c1', 'status': 'running'})
    result = run_diagnostics(tmp_path, run_id='r1', status='interrupted', config={'execution_backend': 'container', 'command': ['python', 'train.py']})
    assert result['status'] == 'interrupted' and result['phase'] == 'interrupted'
    assert result['processes'][0]['status'] == 'running'
    assert result['processes'][0]['state_source'] == 'saved_execution_receipt'
    assert result['remote_state_is_live'] is False
    assert not (tmp_path / 'workspace').exists()  # Reading cannot create or start work.


@pytest.mark.parametrize('command', ['curl --token custom-secret --password "two word secret"',
                                     ['curl', '--token', 'custom-secret', '--password', 'two word secret']])
def test_diagnostics_mask_shell_and_argv_secret_options(tmp_path, command):
    write(tmp_path / 'execution.json', {'command': command, 'status': 'running'})
    output = json.dumps(run_diagnostics(tmp_path, run_id='r1', status='running'))
    assert 'custom-secret' not in output and 'two word secret' not in output


def test_diagnostics_collect_secrets_before_displaying_any_process(tmp_path):
    write(tmp_path / 'workspace/.forest-processes/p1/request.json', {'command': ['echo', 'shared-private']})
    write(tmp_path / 'workspace/.forest-processes/p2/request.json', {'command': ['true'], 'env': {'API_TOKEN': 'shared-private'}})
    assert 'shared-private' not in json.dumps(run_diagnostics(tmp_path, run_id='r1', status='running'))


def test_diagnostics_ignore_invalid_process_identity_and_mask_receipt_fields(tmp_path):
    write(tmp_path / 'workspace/.forest-processes/p1/request.json', {'command': ['true']})
    write(tmp_path / 'workspace/.forest-processes/p1/identity.json', {'pid': {'invalid': 1}, 'process_created': []})
    write(tmp_path / 'workspace/.forest-processes/p1/state.json', {'status': 'running', 'started_at': 'private-value'})
    result = run_diagnostics(tmp_path, run_id='r1', status='running', config={'env': {'API_TOKEN': 'private-value'}})
    assert result['processes'][0]['state_source'] == 'saved_execution_receipt'
    assert result['processes'][0]['started_at'] == '[redacted]'


def test_diagnostics_ignore_symlinks_partial_receipts_and_oversized_files(tmp_path):
    private = tmp_path.parent / 'private-receipt.json'
    private.write_text('{"command": ["private command"], "status": "running"}')
    (tmp_path / 'execution.json').symlink_to(private)
    (tmp_path / 'repository.json').write_text('{')
    write(tmp_path / 'workspace/.forest-processes/p1/request.json', {'command': ['true']})
    (tmp_path / 'workspace/.forest-processes/p1/state.json').write_text('x' * 2_000_001)
    result = run_diagnostics(tmp_path, run_id='r1', status='running', config={'repository': {'url': 'example'}})
    assert result['repository'] is None and result['phase'] == 'preparing_source'
    assert len(result['processes']) == 1 and result['processes'][0]['status'] == 'starting'
    assert 'private command' not in json.dumps(result)


def test_detached_task_does_not_inherit_checkout_credentials(tmp_path, monkeypatch):
    blocked = ('FOREST_GIT_CREDENTIALS_FILE', 'FOREST_GIT_TOKEN_FILE', 'SSH_AUTH_SOCK', 'GIT_ASKPASS',
               'GIT_CONFIG_GLOBAL', 'GCM_CREDENTIAL_STORE', 'SSH_AGENT_PID', 'SUDO_ASKPASS')
    for key in blocked:
        monkeypatch.setenv(key, 'operator-only-value')
    monkeypatch.setenv('FOREST_DIAGNOSTIC_NORMAL', 'ordinary-value')
    manager = ManagedProcesses(tmp_path / 'workspace')
    code = "import json,os; print(json.dumps({key:os.environ.get(key) for key in " + repr([*blocked, 'FOREST_DIAGNOSTIC_NORMAL', 'PATH']) + "}))"
    process = manager.start([sys.executable, '-c', code], env={'PATH': '/task-configured-path'})
    deadline = time.monotonic() + 10
    while manager.inspect(process['process_id'])['status'] not in ('completed', 'failed', 'lost') and time.monotonic() < deadline:
        time.sleep(.02)
    state = manager.inspect(process['process_id'])
    assert state['status'] == 'completed'
    values = json.loads(manager.read_output(process['process_id'])['content'])
    assert all(values[key] is None for key in blocked)
    assert values['FOREST_DIAGNOSTIC_NORMAL'] == 'ordinary-value'
    assert values['PATH'] == '/task-configured-path'
    result = run_diagnostics(tmp_path, run_id='r1', status='completed')
    assert result['processes'][0]['command'][0] == sys.executable
    assert result['processes'][0]['status'] == 'completed'
    assert result['processes'][0]['process_alive'] is False


def test_diagnostics_endpoint_is_authenticated_and_read_only(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'api.db'), 'FOREST_MODEL': '', 'PYTHONPATH': str(root)}
    result = subprocess.run([sys.executable, __file__, 'api'], env=env, cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from services.api.main import app
    from services.api.db import Session, TaskRun
    with TestClient(app) as client:
        pid = client.post('/api/projects', json={'name': 'Diagnostics'}).json()['id']
        with Session.begin() as session:
            run = TaskRun(project_id=pid, request_id='diagnostics-check', kind='command', status='queued', output_path='runs/check', config={'command': ['true']})
            session.add(run)
            session.flush()
            rid = run.id
        response = client.get(f'/api/runs/{rid}/diagnostics')
        assert response.status_code == 200 and response.json()['phase'] == 'queued'
        assert client.get('/api/runs/missing/diagnostics').status_code == 404
    with TestClient(app, base_url='https://remote.example', client=('remote', 1234)) as client:
        assert client.get(f'/api/runs/{rid}/diagnostics').status_code == 401
