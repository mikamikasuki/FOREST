"""Real backup/restore and opt-in Docker isolation qualification."""
import getpass
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import pytest

from scripts.backup import create_backup, restore_backup
from scripts.install import prepare_secrets
from scripts.release import portable_path


def seed_sqlite(directory):
    directory.mkdir()
    database = directory / 'forest.db'
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,config JSON); CREATE TABLE workers(id TEXT PRIMARY KEY); CREATE TABLE task_runs(id TEXT PRIMARY KEY,status TEXT,pid INTEGER,process_created FLOAT,worker_id TEXT,error TEXT);')
        db.execute('INSERT INTO projects VALUES (?,?)', ('project-1', json.dumps({'controller': {'status': 'running'}})))
        db.execute('INSERT INTO workers VALUES (?)', ('worker-1',))
        db.execute('INSERT INTO task_runs VALUES (?,?,?,?,?,?)', ('run-1', 'running', 12345, 42.0, 'worker-1', None))
    (directory / 'projects/project-1').mkdir(parents=True)
    (directory / 'projects/project-1/result.txt').write_text('Editable measured artifact\n')
    (directory / 'secrets.json').write_text('{"private":"should not be exported"}')
    return 'sqlite:///' + str(database)


def test_sqlite_backup_restore_and_verification(tmp_path):
    source = tmp_path / 'source'
    url = seed_sqlite(source)
    archive = tmp_path / 'backup.tar.gz'
    manifest = create_backup(source, url, archive)
    assert manifest['omitted_secrets'] == ['secrets.json']
    verified = restore_backup(archive, tmp_path / 'verify', verify_only=True)
    assert verified['status'] == 'verified'
    destination = tmp_path / 'restored'
    restored = restore_backup(archive, destination)
    assert restored['status'] == 'restored'
    assert (destination / 'projects/project-1/result.txt').read_text() == 'Editable measured artifact\n'
    assert not (destination / 'secrets.json').exists()
    with sqlite3.connect(destination / 'forest.db') as db:
        assert db.execute('SELECT status,pid,worker_id FROM task_runs').fetchone() == ('interrupted', None, None)
        assert db.execute('SELECT COUNT(*) FROM workers').fetchone()[0] == 0
        assert json.loads(db.execute('SELECT config FROM projects').fetchone()[0])['controller']['status'] == 'paused'
    with pytest.raises(ValueError, match='empty destination'):
        restore_backup(archive, destination)


def test_secrets_are_random_idempotent_and_private_parent(tmp_path):
    target = tmp_path / 'secrets'
    first = prepare_secrets(target)
    values = {p.name: p.read_text() for p in target.iterdir()}
    second = prepare_secrets(target)
    assert len(first['created']) == 2 and second['created'] == []
    assert values == {p.name: p.read_text() for p in target.iterdir()}
    assert values['database_password'] != values['owner_token']
    assert target.stat().st_mode & 0o777 == 0o700


def test_container_resume_starts_created_container(monkeypatch):
    from runners.container import ContainerRunner

    runner = ContainerRunner({})
    calls = []
    monkeypatch.setattr(runner, '_verified', lambda job: {'State': {'Status': 'created', 'Paused': False}})
    monkeypatch.setattr(runner, 'command', lambda argv, **kwargs: calls.append(argv))
    monkeypatch.setattr(runner, 'status', lambda job: {'status': 'running'})

    result = runner.resume({'container_id': 'created-job'})

    assert result == {'status': 'running'}
    assert calls == [['start', 'created-job']]


def test_container_reconnect_resumes_only_in_the_admitted_executor(tmp_path, monkeypatch):
    from runners.container import execute_container

    workspace = tmp_path / 'workspace'
    output = tmp_path / 'output'
    workspace.mkdir()
    output.mkdir()
    (output / 'container_task.json').write_text(json.dumps({
        'container_id': 'detached-job', 'workspace': str(workspace.resolve()),
        'task_id': 'run-a',
    }))
    calls = []

    class FakeRunner:
        def __init__(self, config):
            pass

        def resume(self, job):
            calls.append(('resume', job['container_id']))

        def status(self, job):
            return {'status': 'completed', 'exit_code': 0}

        def output(self, job, cursor):
            return {'text': '', 'cursor': cursor}

    monkeypatch.setattr('runners.container.ContainerRunner', FakeRunner)
    result = execute_container({
        'command': ['true'], 'execution_attempt': {'mode': 'container_reconnect'},
    }, workspace, output)

    assert result['command_exit_code'] == 0
    assert calls == [('resume', 'detached-job')]


@pytest.mark.parametrize('name', ['forest/CON.txt', 'forest/file?.txt', 'forest/a.', 'forest/../escape', 'C:/escape', 'forest/a\\b'])
def test_portable_paths_reject_windows_hazards(name):
    with pytest.raises(ValueError):
        portable_path(name)


@pytest.mark.skipif(not os.environ.get('FOREST_TEST_POSTGRES'), reason='Requires explicitly selected PostgreSQL test server')
def test_postgres_backup_restores_into_new_isolated_database(tmp_path):
    import psycopg
    from psycopg import sql
    from scripts.backup import _new_postgres, _drop_postgres, _pg_env
    admin = os.environ['FOREST_TEST_POSTGRES']
    if admin == '1':
        admin = f'postgresql://{getpass.getuser()}@127.0.0.1:5432/postgres'
    url, parameters, name = _new_postgres(admin)
    try:
        with psycopg.connect(**{**parameters, 'dbname': name}) as db:
            db.execute('CREATE TABLE measured_result(id INTEGER PRIMARY KEY,value DOUBLE PRECISION)')
            db.execute('INSERT INTO measured_result VALUES (1,0.875)')
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'observation.txt').write_text('real PostgreSQL backup test')
        archive = tmp_path / 'postgres.tar.gz'
        create_backup(source, url, archive)
        result = restore_backup(archive, tmp_path / 'verified', admin, verify_only=True)
        assert result['status'] == 'verified' and result['tables']['measured_result'] == 1
        restored = restore_backup(archive, tmp_path / 'restored', admin)
        try:
            assert restored['database_name'] != name
            with psycopg.connect(**{**parameters, 'dbname': restored['database_name']}) as db:
                assert db.execute('SELECT value FROM measured_result').fetchone()[0] == .875
        finally:
            _drop_postgres(parameters, restored['database_name'])
    finally:
        _drop_postgres(parameters, name)


def wait_container(manager, ident, wanted, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = manager.inspect(ident)
        if state['status'] in wanted:
            return state
        time.sleep(.2)
    raise AssertionError(manager.inspect(ident))


@pytest.mark.skipif(not os.environ.get('FOREST_TEST_DOCKER'), reason='Requires a real Docker engine and built forest-task:local image')
def test_real_container_isolation_logs_reconnect_pause_cancel(tmp_path, monkeypatch):
    from research.execution.process_manager import process_manager
    monkeypatch.setenv('FOREST_OWNER_TOKEN', 'platform-secret-must-stay-host-side')
    workspace = Path(__file__).resolve().parents[1] / 'output/validation/deployment' / ('workspace-' + uuid.uuid4().hex)
    workspace.mkdir(parents=True)
    config = {'execution_backend': 'container', 'container': {'cpus': .5, 'memory': '256m'}}
    manager = process_manager(workspace, config)
    created = []
    try:
        script = "import json,os,pathlib,sys,time; print('live-log',flush=True); print('error-log',file=sys.stderr,flush=True); pathlib.Path('receipt.json').write_text(json.dumps({'uid':os.getuid(),'token':os.getenv('FOREST_OWNER_TOKEN'),'socket':pathlib.Path('/var/run/docker.sock').exists(),'data':pathlib.Path('/data').exists()})); time.sleep(20)"
        first = manager.start([sys.executable, '-c', script], process_id='isolation-' + uuid.uuid4().hex)
        created.append(first['container_id'])
        ident = first['process_id']
        wait_container(manager, ident, {'running'})
        deadline = time.monotonic() + 15
        while not (workspace / 'receipt.json').exists() and time.monotonic() < deadline:
            time.sleep(.2)
        receipt = json.loads((workspace / 'receipt.json').read_text())
        assert receipt == {'uid': os.getuid(), 'token': None, 'socket': False, 'data': False}
        assert 'live-log' in manager.read_output(ident)['content']
        assert 'error-log' in manager.read_output(ident, stream='stderr')['content']
        info = json.loads(subprocess.check_output(['docker', 'inspect', first['container_id']], text=True))[0]
        assert info['HostConfig']['NanoCpus'] == 500000000
        assert info['HostConfig']['Memory'] == 256 * 1024**2
        assert info['HostConfig']['ReadonlyRootfs'] is True
        assert info['HostConfig']['NetworkMode'] == 'none'
        assert len(info['Mounts']) == 1 and info['Mounts'][0]['Destination'] == '/workspace'
        reconnected = process_manager(workspace, config)
        assert reconnected.inspect(ident)['container_id'] == first['container_id']
        reconnected.signal_all(signal.SIGSTOP)
        assert reconnected.inspect(ident)['status'] == 'paused'
        reconnected.signal_all(signal.SIGCONT)
        assert reconnected.inspect(ident)['status'] == 'running'
        assert reconnected.cancel(ident)['status'] == 'cancelled'
        assert not json.loads(subprocess.check_output(['docker', 'inspect', first['container_id']], text=True))[0]['State']['Running']
        finished = manager.start(['python', '-c', "import pathlib;pathlib.Path('real.txt').write_text(str(sum(range(10))))"], process_id='completion-' + uuid.uuid4().hex)
        created.append(finished['container_id'])
        result = wait_container(manager, finished['process_id'], {'completed', 'failed'})
        assert result['status'] == 'completed' and result['exit_code'] == 0
        assert (workspace / 'real.txt').read_text() == '45'
    finally:
        if created:
            subprocess.run(['docker', 'rm', '-f', *created], check=True, capture_output=True)


@pytest.mark.skipif(not os.environ.get('FOREST_TEST_DOCKER'), reason='Requires a real Docker engine and built forest-task:local image')
def test_real_container_command_receipt_metrics_and_executor_death(tmp_path):
    from runners.container import execute_container, ContainerProcesses
    root = Path(__file__).resolve().parents[1]
    output = root / 'output/validation/deployment' / ('command-' + uuid.uuid4().hex)
    workspace = output / 'workspace'
    workspace.mkdir(parents=True)
    handles = []
    launcher = None
    try:
        result = execute_container({'command': ['python', '-c', "import json,pathlib; print('actual-command');pathlib.Path('metrics.json').write_text(json.dumps({'total':sum(range(100))}))"], 'container': {'memory': '256m'}}, workspace, output, require_metrics=True)
        handles.append(result['container_execution']['container_id'])
        assert result['total'] == 4950 and result['command_exit_code'] == 0
        assert json.loads((output / 'container_result.json').read_text())['status'] == 'completed'
        ident = 'recovery-' + uuid.uuid4().hex
        code = "from runners.container import ContainerProcesses; import sys,time; m=ContainerProcesses(sys.argv[1],{'memory':'256m'}); m.start(['python','-c',\"import time,pathlib;time.sleep(3);pathlib.Path('survived.txt').write_text('finished after host executor died')\"],process_id=sys.argv[2]);time.sleep(60)"
        launcher = subprocess.Popen([sys.executable, '-c', code, str(workspace), ident], cwd=root)
        handle = workspace.parent / '.forest-container-processes' / workspace.name / ident / 'container.json'
        deadline = time.monotonic() + 20
        while not handle.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        job = json.loads(handle.read_text())
        handles.append(job['container_id'])
        manager = ContainerProcesses(workspace, {'memory': '256m'})
        wait_container(manager, ident, {'running'})
        launcher.kill()
        launcher.wait(timeout=5)
        state = wait_container(manager, ident, {'completed', 'failed'})
        assert state['status'] == 'completed' and state['exit_code'] == 0
        assert (workspace / 'survived.txt').read_text() == 'finished after host executor died'
    finally:
        if launcher and launcher.poll() is None:
            launcher.kill()
            launcher.wait(timeout=5)
        if handles:
            subprocess.run(['docker', 'rm', '-f', *handles], check=True, capture_output=True)


@pytest.mark.skipif(not os.environ.get('FOREST_TEST_DOCKER'), reason='Requires a real Docker engine and built forest-task:local image')
def test_real_api_worker_container_pause_cancel_and_recovery():
    from test_worker import Harness, wait_until
    root = Path(__file__).resolve().parents[1]
    directory = root / 'output/validation/deployment' / ('worker-' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    harness = Harness(directory)
    containers = []
    try:
        harness.start_api()
        harness.start_worker()
        _, node = harness.project_node(seconds=1)
        script = "import json,pathlib,time; p=pathlib.Path('starts.txt');p.write_text(str(int(p.read_text())+1) if p.exists() else '1');print('container-worker-live',flush=True);time.sleep(16);pathlib.Path('metrics.json').write_text(json.dumps({'computed':sum(range(100))}))"
        harness.request('PATCH', '/api/nodes/' + node['id'], json={'config': {'kind': 'command', 'command': ['python', '-c', script], 'execution_backend': 'container', 'container': {'memory': '256m', 'cpus': .5}, 'timeout': 60}})
        run = harness.launch(node)
        first = harness.running(run)
        handle_path = harness.output(run) / 'container_task.json'
        job = wait_until(lambda: json.loads(handle_path.read_text()) if handle_path.exists() else None)
        containers.append(job['container_id'])
        wait_until(lambda: 'container-worker-live' in harness.request('GET', '/api/runs/' + run['id'] + '/output')['text'])
        paused = harness.request('POST', '/api/runs/' + run['id'] + '/pause', json={})
        assert paused['status'] == 'paused'
        assert json.loads(subprocess.check_output(['docker', 'inspect', job['container_id']], text=True))[0]['State']['Paused'] is True
        harness.request('POST', '/api/runs/' + run['id'] + '/resume', json={})
        # Neither the worker nor its executor owns the detached container lifetime.
        harness.worker.kill()
        harness.worker.wait(timeout=5)
        os.kill(first['pid'], signal.SIGKILL)
        harness.start_worker()
        completed = harness.terminal(run, timeout=45)
        assert completed['status'] == 'completed', completed
        assert completed['metrics']['computed'] == 4950
        assert (harness.output(run) / 'workspace/starts.txt').read_text() == '1'
        assert json.loads(handle_path.read_text())['container_id'] == job['container_id']
        assert any(item['status'] == 'interrupted' for item in completed['resource']['attempts'])
        cancelled_run = harness.launch(node)
        harness.running(cancelled_run)
        next_handle = harness.output(cancelled_run) / 'container_task.json'
        next_job = wait_until(lambda: json.loads(next_handle.read_text()) if next_handle.exists() else None)
        containers.append(next_job['container_id'])
        cancelled = harness.request('POST', '/api/runs/' + cancelled_run['id'] + '/cancel', json={})
        assert cancelled['status'] == 'cancelled'
        assert json.loads(subprocess.check_output(['docker', 'inspect', next_job['container_id']], text=True))[0]['State']['Running'] is False
        assert json.loads((harness.output(cancelled_run) / 'container_result.json').read_text())['status'] == 'cancelled'
    finally:
        harness.cleanup()
        if containers:
            subprocess.run(['docker', 'rm', '-f', *containers], check=True, capture_output=True)


@pytest.mark.skipif(not os.environ.get('FOREST_TEST_DOCKER'), reason='Requires a real Docker engine and built forest-task:local image')
def test_sibling_container_workspaces_keep_process_receipts_separate():
    from runners.container import ContainerProcesses
    parent = Path(__file__).resolve().parents[1] / 'output/validation/deployment' / ('siblings-' + uuid.uuid4().hex)
    handles = []
    try:
        states = []
        for name in ('workspace-one', 'workspace-two'):
            workspace = parent / name
            workspace.mkdir(parents=True)
            manager = ContainerProcesses(workspace, {'memory': '256m'})
            assert manager.all() == []
            state = manager.start(['python', '-c', "print('separate real computation')"], process_id='same-readable-process-id')
            handles.append(state['container_id'])
            states.append(wait_container(manager, state['process_id'], {'completed', 'failed'}))
            assert len(manager.all()) == 1
        assert all(state['status'] == 'completed' for state in states)
        assert len(set(handles)) == 2
    finally:
        if handles:
            subprocess.run(['docker', 'rm', '-f', *handles], check=True, capture_output=True)


@pytest.mark.skipif(not os.environ.get('FOREST_TEST_DOCKER'), reason='Requires a real Docker engine and built forest-task:local image')
def test_real_container_checkpoint_training_matches_uninterrupted_run():
    from test_worker import Harness, wait_until
    root = Path(__file__).resolve().parents[1]
    directory = root / 'output/validation/deployment' / ('checkpoint-' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    harness = Harness(directory)
    containers = []
    training = '''import json,os,time
from pathlib import Path
import numpy as np
rng=np.random.default_rng(1701)
x=rng.normal(size=(512,32)); truth=rng.normal(size=32); y=x@truth
checkpoint=Path(os.environ.get('FOREST_CHECKPOINT_PATH','checkpoint.json'))
resume=os.environ.get('FOREST_RESUME_PATH')
state=json.loads(Path(resume).read_text()) if resume else {'step':0,'weights':[0.0]*32}
restored_step=state['step']; weights=np.array(state['weights'],dtype=np.float64)
print('restored_step='+str(restored_step),flush=True)
for step in range(restored_step,60):
 gradient=x.T@(x@weights-y)/len(y); weights-=0.05*gradient
 pending=checkpoint.with_suffix('.tmp');pending.write_text(json.dumps({'step':step+1,'weights':weights.tolist()}));pending.replace(checkpoint)
 print('checkpoint-step='+str(step+1),flush=True);time.sleep(.04)
loss=float(np.mean((x@weights-y)**2))
Path('metrics.json').write_text(json.dumps({'step':60,'weights':weights.tolist(),'loss':loss,'restored_step':restored_step}))
'''
    try:
        harness.start_api()
        harness.start_worker()
        _, node = harness.project_node(seconds=1)
        config = {'kind': 'experiment', 'command': ['python', '-c', training], 'execution_backend': 'container',
                  'container': {'memory': '256m', 'cpus': .5}, 'timeout': 60,
                  'env': {'OPENBLAS_NUM_THREADS': '1'},
                  'recovery': {'mode': 'checkpoint', 'checkpoint': 'checkpoint.json', 'retry_on_failure': True, 'max_attempts': 3}}
        harness.request('PATCH', '/api/nodes/' + node['id'], json={'config': config})
        resumed_run = harness.launch(node)
        harness.running(resumed_run)
        output = harness.output(resumed_run)
        checkpoint = output / 'workspace/checkpoint.json'
        wait_until(lambda: checkpoint.exists() and json.loads(checkpoint.read_text())['step'] >= 5)
        first = json.loads((output / 'container_task.json').read_text())
        containers.append(first['container_id'])
        subprocess.run(['docker', 'kill', '--signal', 'KILL', first['container_id']], check=True, capture_output=True)
        resumed = harness.terminal(resumed_run, timeout=40)
        final = json.loads((output / 'container_task.json').read_text())
        containers.append(final['container_id'])
        assert resumed['status'] == 'completed', resumed
        assert final['container_id'] != first['container_id']
        assert resumed['metrics']['restored_step'] >= 5
        assert resumed['metrics']['step'] == 60
        assert any(item.get('mode') == 'checkpoint' for item in resumed['resource']['attempts'])
        prior = list((output / 'attempts').glob('container-*/container_task.json'))
        assert prior and json.loads(prior[0].read_text())['container_id'] == first['container_id']
        baseline_run = harness.launch(node)
        harness.running(baseline_run)
        baseline = harness.terminal(baseline_run, timeout=30)
        baseline_job = json.loads((harness.output(baseline_run) / 'container_task.json').read_text())
        containers.append(baseline_job['container_id'])
        assert baseline['status'] == 'completed', baseline
        assert baseline['metrics']['restored_step'] == 0
        assert resumed['metrics']['weights'] == baseline['metrics']['weights']
        assert resumed['metrics']['loss'] == baseline['metrics']['loss']
        report = {'status': 'passed', 'computation': 'Actual 60-step CPU NumPy gradient descent, 512 examples and 32 weights',
                  'interruption': 'Docker SIGKILL after persisted training step >= 5', 'restored_step': resumed['metrics']['restored_step'],
                  'final_step': 60, 'resumed_loss': resumed['metrics']['loss'], 'uninterrupted_loss': baseline['metrics']['loss'],
                  'exact_final_weights_match': True, 'previous_container': first['container_id'], 'resumed_container': final['container_id'],
                  'attempts': resumed['resource']['attempts'], 'limits': 'Task explicitly saves/restores all state used in this deterministic CPU training example; generic training recovery is not inferred.'}
        (directory / 'checkpoint-report.json').write_text(json.dumps(report, indent=2))
        (root / 'output/validation/deployment/checkpoint-report.json').write_text(json.dumps(report, indent=2))
    finally:
        harness.cleanup()
        if containers:
            subprocess.run(['docker', 'rm', '-f', *set(containers)], check=True, capture_output=True)
