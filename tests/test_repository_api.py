"""Authenticated repository queue and provenance contracts with an isolated DB."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_repository_api_queues_without_git_or_model_calls(tmp_path):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'api.db'),
           'FOREST_MODEL': '', 'FOREST_OWNER_TOKEN': 'test-owner-only',
           'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, __file__, 'check'], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    import json
    from fastapi.testclient import TestClient
    from sqlalchemy import select, func
    from services.api.main import app
    from services.api.common import project_dir
    from services.api.db import Session, TaskRun, ModelRequest, Project
    from services.worker.scheduler import enqueue

    url = 'https://github.com/example/research.git'
    source = {'url': url, 'ref': 'v1.0', 'credential': 'github-read'}
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'Repository input', 'mode': 'manual',
                              'budget': {'max_runs': 6, 'allow_paid': False}}).json()
        pid = project['id']
        endpoint = f'/api/projects/{pid}/repositories/clone'
        for invalid in [
            {'repository': {'url': 'https://user:private-token@github.com/example/research.git'}},
            {'repository': {'url': url, 'directory': '../escape'}},
            {'repository': {'url': url, 'identity_file': '/private/key'}},
            {'repository': {'url': url, 'credential': {'token': 'private-token'}}},
            {'repository': {'url': url}, 'token_file': '/private/token'},
            {'repository': {'url': url}, 'request_id': {'unexpected': 'object'}},
        ]:
            response = client.post(endpoint, json=invalid)
            assert response.status_code == 422, response.text
            assert 'private-token' not in response.text and '/private/key' not in response.text
        with Session() as session:
            assert session.scalar(select(func.count()).select_from(TaskRun)) == 0

        response = client.post(endpoint, json={'repository': source, 'request_id': 'source-01'})
        assert response.status_code == 200, response.text
        run = response.json()
        assert run['kind'] == 'repository_clone' and run['status'] == 'queued'
        assert run['config']['repository'] == {**source, 'directory': 'source', 'transport': 'https'}
        assert not (project_dir(pid) / run['output_path']).exists()
        # The queue is durable and returns the same run before any worker starts.
        repeated = client.post(endpoint, json={'repository': source, 'request_id': 'source-01'})
        assert repeated.status_code == 200 and repeated.json()['id'] == run['id']
        conflict = client.post(endpoint, json={'repository': {**source, 'ref': 'v2.0'}, 'request_id': 'source-01'})
        assert conflict.status_code == 409 and conflict.json()['detail']['code'] == 'REQUEST_ID_CONFLICT'
        assert client.post('/api/projects/missing/repositories/clone', json={'repository': source}).status_code == 404
        with Session() as session:
            assert session.scalar(select(func.count()).select_from(TaskRun)) == 1
            assert session.scalar(select(func.count()).select_from(ModelRequest)) == 0

        # Scheduling inherited node/run config applies the same reference-only validation.
        with Session.begin() as session:
            for kind in ('agent', 'command', 'experiment'):
                task = enqueue(session, pid, kind, {'repository': source}, f'node-source-{kind}')
                assert task.config['repository']['transport'] == 'https'
            unrelated = enqueue(session, pid, 'command', {}, 'unrelated-id')
        response = client.post(endpoint, json={'repository': source, 'request_id': 'unrelated-id'})
        assert response.status_code == 409
        try:
            with Session.begin() as session:
                enqueue(session, pid, 'command', {'repository': {'url': url, 'token_file': '/private/token'}}, 'invalid-node')
        except Exception as exc:
            assert getattr(exc, 'status_code', None) == 422
        else:
            raise AssertionError('Raw host authentication paths must not be admitted')
        try:
            with Session.begin() as session:
                enqueue(session, pid, 'command', {'_repository_source': {'commit': 'a' * 40}}, 'forged-source')
        except Exception as exc:
            assert getattr(exc, 'status_code', None) == 422
        else:
            raise AssertionError('Source provenance must be runner-owned')
        assert client.patch(f"/api/runs/{run['id']}/configuration",
                            json={'_repository_source': {'commit': 'a' * 40}}).status_code == 422
        assert client.patch(f"/api/runs/{run['id']}/configuration",
                            json={'repository': {'url': url, 'token_file': '/private/token'}}).status_code == 422
        assert client.get(f"/api/runs/{run['id']}").json()['config']['repository'] == run['config']['repository']

        manifest_endpoint = f"/api/runs/{run['id']}/repository"
        assert client.get(manifest_endpoint).json()['repository'] is None
        folder = project_dir(pid) / run['output_path']
        folder.mkdir(parents=True)
        manifest = folder / 'repository.json'
        recorded = {'url': url, 'requested_ref': 'v1.0', 'commit': 'a' * 40,
                    'directory': 'source', 'transport': 'https'}
        manifest.write_text(json.dumps({**recorded, 'credential': 'private-profile',
            'identity_file': '/private/key', 'token': 'private-token'}))
        response = client.get(manifest_endpoint)
        assert response.status_code == 200, response.text
        assert response.json()['repository'] == recorded
        assert 'private' not in response.text
        manifest.write_text(json.dumps({**recorded, 'commit': 'secret-token'}))
        response = client.get(manifest_endpoint)
        assert response.status_code == 422 and 'secret-token' not in response.text
        manifest.unlink()
        outside = project_dir(pid).parent / 'outside-manifest.json'
        outside.write_text(json.dumps(recorded))
        manifest.symlink_to(outside)
        assert client.get(manifest_endpoint).status_code == 403
        manifest.unlink()
        assert client.get('/api/runs/missing/repository').status_code == 404

    with TestClient(app, base_url='http://remote.example') as remote:
        remote.cookies.clear()
        assert remote.post(endpoint, json={'repository': source}).status_code == 401
        assert remote.get(manifest_endpoint).status_code == 401
        response = remote.post(endpoint, headers={'Authorization': 'Bearer test-owner-only'},
                               json={'repository': source, 'request_id': 'remote-source-01'})
        assert response.status_code == 200, response.text
    print('Repository queue, idempotency, authentication and manifest contracts passed')
