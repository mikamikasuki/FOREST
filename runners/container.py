"""Real Docker task isolation. Docker access belongs to the host worker only.

A task gets its own workspace, explicit environment and resource limits. Detached
containers and identity-checked receipts allow reconnection after executor loss.
This is an opt-in backend, not a promise that trusted local tasks are sandboxed.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

TERMINAL = {'completed', 'failed', 'cancelled', 'lost'}


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def _relative(root, name):
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('Task artifact must remain inside its workspace')
    return target


class ContainerRunner:
    def __init__(self, config=None):
        self.config = config or {}
        self.docker = shutil.which('docker')

    def command(self, argv, timeout=30, check=True):
        if not self.docker:
            raise RuntimeError('Container backend requires Docker CLI and a running engine (Docker Desktop or Colima on macOS). Run scripts/doctor.py.')
        result = subprocess.run([self.docker, *argv], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError('Docker ' + argv[0] + ' failed: ' + (result.stderr or result.stdout)[-3000:])
        return result

    def test(self):
        result = self.command(['info', '--format', '{{json .}}'])
        info = json.loads(result.stdout)
        return {'available': True, 'server_version': info.get('ServerVersion'), 'os': info.get('OSType'), 'architecture': info.get('Architecture')}

    def _inspect(self, reference):
        result = self.command(['inspect', reference], check=False)
        if result.returncode:
            if 'no such object' in result.stderr.lower() or 'no such container' in result.stderr.lower():
                return None
            raise RuntimeError('Cannot inspect task container: ' + result.stderr[-3000:])
        return json.loads(result.stdout)[0]

    def _verified(self, job):
        info = self._inspect(job['container_id'])
        if info is None:
            return None
        labels = info.get('Config', {}).get('Labels') or {}
        if info['Id'] != job['container_id'] or labels.get('org.forest.task') != job['task_id'] or labels.get('org.forest.workspace') != job['workspace']:
            raise RuntimeError('Task container identity does not match its receipt; refusing control')
        return info

    def start(self, argv, workspace, task_id=None, env=None, receipt_path=None, namespace=None):
        workspace = Path(workspace).resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError('Task workspace must be a directory')
        task_id = task_id or uuid.uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', task_id):
            raise ValueError('Invalid task identifier')
        if not argv or not isinstance(argv, list) or any(not isinstance(x, str) for x in argv):
            raise ValueError('Container command must be a nonempty argv list')
        image = self.config.get('image', 'forest-task:local')
        if not isinstance(image, str) or not image or image.startswith('-'):
            raise ValueError('Invalid container image')
        cpus = float(self.config.get('cpus', 1))
        if not math.isfinite(cpus) or cpus <= 0:
            raise ValueError('Container cpus must be positive')
        memory = str(self.config.get('memory', '2g'))
        if not re.fullmatch(r'[1-9][0-9]*(?:[bkmgBKMG])?', memory):
            raise ValueError('Container memory must be bytes or a positive Docker size such as 2g')
        network = self.config.get('network', 'none')
        if network not in ('none', 'bridge'):
            raise ValueError('Container network must be none or bridge; host networking is unavailable')
        pids = int(self.config.get('pids_limit', 256))
        if not 16 <= pids <= 4096:
            raise ValueError('Container pids_limit must be between 16 and 4096')
        if ',' in str(workspace):
            raise ValueError('Docker bind workspace path may not contain commas')
        uid = os.getuid() if hasattr(os, 'getuid') else 10001
        gid = os.getgid() if hasattr(os, 'getgid') else 10001
        if uid == 0:
            uid = gid = 10001
        if namespace is not None and not re.fullmatch(r'[a-f0-9]{16,32}', namespace):
            raise ValueError('Invalid task namespace')
        name = 'forest-task-' + ((namespace + '-') if namespace else '') + task_id.lower()
        args = ['create', '--name', name, '--label', 'org.forest.task=' + task_id,
                '--label', 'org.forest.workspace=' + str(workspace), '--init',
                '--user', f'{uid}:{gid}', '--workdir', '/workspace', '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                '--network', network, '--cpus', str(cpus), '--memory', memory,
                '--pids-limit', str(pids), '--tmpfs', '/tmp:rw,nosuid,size=256m',
                '--mount', f'type=bind,src={workspace},dst=/workspace',
                '--log-driver', 'json-file', '--log-opt', 'max-size=20m', '--log-opt', 'max-file=3']
        gpus = self.config.get('gpus')
        if gpus:
            if not (gpus == 'all' or (isinstance(gpus, int) and gpus > 0)):
                raise ValueError('Container gpus must be all or a positive count')
            args += ['--gpus', str(gpus)]
        explicit_env = {'HOME': '/tmp', 'PYTHONUNBUFFERED': '1', 'FOREST_RUN_DIR': '/workspace', **(env or {})}
        for key, value in explicit_env.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or '\x00' in str(value):
                raise ValueError('Invalid task environment')
            if key in ('FOREST_DATABASE_URL', 'FOREST_OWNER_TOKEN', 'DOCKER_HOST', 'DOCKER_CERT_PATH', 'DOCKER_TLS_VERIFY'):
                raise ValueError('Platform connection settings cannot be passed to task containers')
            args += ['--env', key + '=' + str(value)]
        # A deterministic name closes the create/receipt crash gap. Only containers
        # with exact expected labels can be adopted; arbitrary names are rejected.
        info = self._inspect(name)
        if info is None:
            result = self.command([*args, image, *argv], timeout=120)
            info = self._inspect(result.stdout.strip())
        job = {'backend': 'container', 'task_id': task_id, 'container_id': info['Id'],
               'workspace': str(workspace), 'image': image, 'created_at': info.get('Created'),
               'limits': {'cpus': cpus, 'memory': memory, 'gpus': gpus, 'network': network},
               'status': 'starting', 'attempt_id': explicit_env.get('FOREST_ATTEMPT_ID')}
        self._verified(job)
        if receipt_path:
            _save(receipt_path, job)
        if info['State']['Status'] == 'created':
            self.command(['start', job['container_id']])
        return job

    def status(self, job):
        info = self._verified(job)
        if info is None:
            return {**job, 'status': 'lost', 'error': 'Container was removed; no success is inferred'}
        state = info['State']
        state_name = state['Status']
        if state_name == 'exited':
            status = 'completed' if state['ExitCode'] == 0 else 'failed'
        elif state_name == 'dead':
            status = 'failed'
        elif state_name == 'paused':
            status = 'paused'
        else:
            status = 'running' if state.get('Running') else 'starting'
        return {**job, 'status': status, 'exit_code': state.get('ExitCode') if status in TERMINAL else None,
                'started_at': state.get('StartedAt'), 'finished_at': state.get('FinishedAt'),
                'oom_killed': state.get('OOMKilled', False), 'error': state.get('Error') or None}

    def output(self, job, cursor=None, limit=1_000_000):
        self._verified(job)
        cursor = cursor or {'timestamp': '', 'same_timestamp_lines': 0}
        args = [self.docker, 'logs', '--timestamps']
        if cursor.get('timestamp'):
            args += ['--since', cursor['timestamp']]
        args += [job['container_id']]
        # Docker may emit a very large log between polls. Spool first, then bound
        # the bytes returned; stderr is part of the task log, not silently lost.
        with tempfile.TemporaryFile() as spool:
            result = subprocess.run(args, stdout=spool, stderr=subprocess.STDOUT, timeout=30)
            if result.returncode:
                spool.seek(0)
                raise RuntimeError('Container log read failed: ' + spool.read(3000).decode(errors='replace'))
            spool.seek(0)
            text, size, duplicate = [], 0, 0
            next_cursor = dict(cursor)
            for raw in spool:
                stamp, _, payload = raw.partition(b' ')
                timestamp = stamp.decode(errors='replace')
                if timestamp == cursor.get('timestamp'):
                    duplicate += 1
                    if duplicate <= cursor.get('same_timestamp_lines', 0):
                        continue
                if timestamp == next_cursor.get('timestamp'):
                    next_cursor['same_timestamp_lines'] = next_cursor.get('same_timestamp_lines', 0) + 1
                else:
                    next_cursor = {'timestamp': timestamp, 'same_timestamp_lines': 1}
                text.append(payload.decode(errors='replace'))
                size += len(payload)
                if size >= min(max(int(limit), 1), 1_000_000):
                    break
        return {'text': ''.join(text), 'cursor': next_cursor}

    def stop(self, job, grace=5):
        info = self._verified(job)
        if info and info['State'].get('Running'):
            if info['State'].get('Paused'):
                self.command(['unpause', job['container_id']])
            self.command(['stop', '--time', str(int(grace)), job['container_id']], timeout=grace + 30)
        result = self.status(job)
        return {**result, 'status': 'cancelled', 'cancel_requested_at': time.time()}

    def pause(self, job):
        info=self._verified(job)
        if info and info['State'].get('Paused'): return self.status(job)
        self.command(['pause', job['container_id']])
        return self.status(job)

    def resume(self, job):
        info=self._verified(job)
        if info and not info['State'].get('Paused'): return self.status(job)
        self.command(['unpause', job['container_id']])
        return self.status(job)


def execute_container(config, workspace, output, env=None, require_metrics=False):
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    runner = ContainerRunner(config.get('container'))
    handle_path, cursor_path = output / 'container_task.json', output / 'container_log_cursor.json'
    result_path = output / 'container_result.json'
    if result_path.exists() and json.loads(result_path.read_text()).get('status') == 'cancelled':
        raise RuntimeError('Container task was cancelled; a new run is required')
    attempt = config.get('execution_attempt', {})
    recovery = config.get('recovery') or {}
    checkpoint_mode = attempt.get('mode') == 'checkpoint'
    command = (recovery.get('resume_command') if checkpoint_mode else None) or config.get('command')
    if not command:
        raise ValueError('Container backend requires a command; host Python entrypoints are not run inside it implicitly')
    argv = ['/bin/sh', '-c', command] if isinstance(command, str) else command
    job = json.loads(handle_path.read_text()) if handle_path.exists() else None
    if job and job.get('workspace') != str(workspace):
        raise RuntimeError('Container receipt belongs to a different workspace')
    resume_path = None
    if checkpoint_mode:
        if recovery.get('mode') != 'checkpoint' or not recovery.get('checkpoint'):
            raise ValueError('Container checkpoint continuation requires an explicit recovery.checkpoint policy')
        checkpoint = _relative(workspace, recovery['checkpoint'])
        if not checkpoint.is_file() or checkpoint.stat().st_size == 0:
            raise ValueError('Configured task checkpoint is missing or empty')
        if checkpoint.suffix == '.json':
            json.loads(checkpoint.read_text())
        previous = runner.status(job) if job else None
        if previous and previous['status'] in ('failed', 'lost'):
            # Preserve real previous receipts before creating a new attempt. The
            # checkpoint itself remains editable in the same workspace.
            archive = output / 'attempts' / ('container-' + (job.get('attempt_id') or job['task_id']))
            archive.mkdir(parents=True, exist_ok=True)
            for path in (handle_path, cursor_path, result_path):
                if path.exists():
                    target = archive / path.name
                    if target.exists():
                        target = archive / (uuid.uuid4().hex + '-' + path.name)
                    path.replace(target)
            job = None
        elif previous and previous['status'] not in ('running', 'paused', 'starting', 'completed'):
            raise RuntimeError('Container state does not permit checkpoint continuation')
        resume_path = '/workspace/' + checkpoint.relative_to(workspace).as_posix()
    if job is None:
        run_id = os.environ.get('FOREST_RUN_ID') or uuid.uuid4().hex
        attempt_id = attempt.get('id') or os.environ.get('FOREST_ATTEMPT_ID') or uuid.uuid4().hex
        task_id = run_id + '-a-' + attempt_id if checkpoint_mode else run_id
        forwarded = {'FOREST_RUN_ID': run_id, 'FOREST_ATTEMPT_ID': attempt_id}
        if recovery.get('checkpoint'):
            checkpoint = _relative(workspace, recovery['checkpoint'])
            forwarded['FOREST_CHECKPOINT_PATH'] = '/workspace/' + checkpoint.relative_to(workspace).as_posix()
        if resume_path:
            forwarded['FOREST_RESUME_PATH'] = resume_path
        job = runner.start(argv, workspace, task_id=task_id, env={**config.get('env', {}), **(env or {}), **forwarded}, receipt_path=handle_path)
    cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else None
    timeout = config.get('timeout')
    while True:
        status = runner.status(job)
        chunk = runner.output(job, cursor) if status['status'] != 'lost' else {'text': '', 'cursor': cursor}
        if chunk['text']:
            print(chunk['text'], end='', flush=True)
        cursor = chunk['cursor']
        _save(cursor_path, cursor)
        if status['status'] in TERMINAL:
            # Drain any remaining bounded chunks before publishing completion.
            if chunk['text']:
                continue
            break
        if timeout and status.get('started_at'):
            from datetime import datetime, timezone
            started = datetime.fromisoformat(status['started_at'].replace('Z', '+00:00'))
            if (datetime.now(timezone.utc) - started).total_seconds() >= float(timeout):
                status = {**runner.stop(job), 'status': 'failed', 'error': 'Configured task time budget exceeded'}
                break
        time.sleep(.5)
    if result_path.exists() and json.loads(result_path.read_text()).get('status') == 'cancelled':
        raise RuntimeError('Container task was cancelled; its cancellation receipt is preserved')
    _save(result_path, status)
    if status['status'] != 'completed':
        raise RuntimeError('Container task ' + status['status'] + ': ' + str(status.get('error') or status.get('exit_code')))
    metrics_path = _relative(workspace, config.get('metrics_file', 'metrics.json'))
    if require_metrics and not metrics_path.is_file():
        raise ValueError('Experiment container completed without its required metrics file')
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    if not isinstance(metrics, dict):
        raise ValueError('Metrics file must contain a JSON object')
    if require_metrics:
        shutil.copy2(metrics_path, output / 'metrics.json')
    return {**metrics, 'command_exit_code': status['exit_code'], 'container_execution': status}


def control_container(config, output, action):
    path = Path(output) / 'container_task.json'
    if not path.exists():
        return None
    runner = ContainerRunner(config.get('container'))
    result = getattr(runner, action)(json.loads(path.read_text()))
    if action == 'stop':
        _save(Path(output) / 'container_result.json', result)
    return result


def cancel_container(config, output):
    return control_container(config, output, 'stop')


class ContainerProcesses:
    """ManagedProcesses-compatible detached commands with Docker-owned lifetime.

    stdout and stderr are collected separately using Docker's actual streams.
    Pausing freezes the container; cancellation first unpauses then stops it.
    Receipts remain inspectable after the agent or worker process restarts.
    """
    def __init__(self, workspace, config=None):
        self.workspace = Path(workspace).resolve()
        # Control receipts stay outside the mounted workspace, so child code
        # cannot forge identity, cancellation or completion metadata.
        self.directory = self.workspace.parent / '.forest-container-processes' / self.workspace.name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runner = ContainerRunner(config)
        self.max_processes = int((config or {}).get('max_processes', 1))
        if not 1 <= self.max_processes <= 32: raise ValueError('container.max_processes must be between 1 and 32')

    def folder(self, process_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(process_id or '')):
            raise ValueError('Invalid process ID')
        return self.directory / process_id

    def start(self, command, *, process_id=None, cwd='.', env=None, timeout=None):
        import fcntl
        import sys
        process_id = process_id or str(uuid.uuid4())
        folder = self.folder(process_id)
        folder.mkdir(parents=True, exist_ok=True)
        requested_cwd = _relative(self.workspace, cwd)
        if not requested_cwd.is_dir():
            raise ValueError('Process working directory must exist inside the workspace')
        if timeout is not None and float(timeout) <= 0:
            raise ValueError('timeout must be positive or omitted')
        argv = ['/bin/sh', '-c', command] if isinstance(command, str) else list(command)
        if not argv or any(not isinstance(value, str) for value in argv):
            raise ValueError('command must be a nonempty argv list or shell command string')
        request = {'process_id': process_id, 'command': argv, 'cwd': str(requested_cwd), 'env': env or {}, 'timeout': timeout, 'created_at': time.time(), 'backend': 'container'}
        with (self.directory / 'launch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            request_path = folder / 'request.json'
            if request_path.exists():
                previous = json.loads(request_path.read_text())
                if any(previous.get(key) != request.get(key) for key in ('command', 'cwd', 'env', 'timeout', 'backend')):
                    raise ValueError('This process ID belongs to a different command; choose a new ID')
                if (folder / 'container.json').exists():
                    return self.inspect(process_id)
            else:
                active=[item for item in self.all() if item['status'] not in TERMINAL]
                if len(active)>=self.max_processes:
                    raise ValueError('Container process allowance is full; wait for or cancel an existing process')
                _save(request_path, request)
            mapped = [value.replace(str(self.workspace), '/workspace') for value in argv]
            if mapped[0] == sys.executable:
                mapped[0] = 'python'
            mapped_cwd = '/workspace/' + requested_cwd.relative_to(self.workspace).as_posix()
            # Runtime timeout is enforced in the container, so it remains active
            # if the host agent/worker dies. No platform Python module is mounted.
            supervisor = "import os,signal,subprocess,sys; timeout=float(sys.argv[1]) if sys.argv[1] else None; os.chdir(sys.argv[2]); p=subprocess.Popen(sys.argv[3:],start_new_session=True);\ntry: code=p.wait(timeout=timeout)\nexcept subprocess.TimeoutExpired:\n os.killpg(p.pid,signal.SIGTERM)\n try: p.wait(timeout=3)\n except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait()\n code=124\nsys.exit(code)"
            wrapped = ['python', '-c', supervisor, str(timeout or ''), mapped_cwd, *mapped]
            namespace_path=self.directory/'namespace.json'
            if not namespace_path.exists(): _save(namespace_path,{'id':uuid.uuid4().hex[:16]})
            namespace=json.loads(namespace_path.read_text())['id']
            self.runner.start(wrapped, self.workspace, task_id=process_id, env=env or {}, receipt_path=folder / 'container.json', namespace=namespace)
        return self.inspect(process_id)

    def _job(self, process_id):
        job = json.loads((self.folder(process_id) / 'container.json').read_text())
        if job.get('workspace') != str(self.workspace) or job.get('task_id') != process_id:
            raise RuntimeError('Container receipt belongs to a different workspace or process')
        return job

    def inspect(self, process_id):
        folder = self.folder(process_id)
        request_path = folder / 'request.json'
        if not request_path.exists():
            raise ValueError('Unknown process ID: ' + process_id)
        request = json.loads(request_path.read_text())
        saved = folder / 'state.json'
        if not (folder / 'container.json').exists():
            return {**request, 'status': 'starting' if time.time() - request['created_at'] < 5 else 'lost', 'error': 'No container receipt yet'}
        previous = json.loads(saved.read_text()) if saved.exists() else {}
        if previous.get('status') == 'cancelled':
            return previous
        job = self._job(process_id)
        status = self.runner.status(job)
        value = {**request, **status, 'process_id': process_id, 'command': request['command'], 'rss_bytes': 0}
        if status.get('started_at'):
            from datetime import datetime, timezone
            started = datetime.fromisoformat(status['started_at'].replace('Z', '+00:00'))
            ended = datetime.fromisoformat(status['finished_at'].replace('Z', '+00:00')) if status['status'] in TERMINAL and status.get('finished_at') and not status['finished_at'].startswith('0001') else datetime.now(timezone.utc)
            value['elapsed_seconds'] = max(0, (ended - started).total_seconds())
        if value['status'] in TERMINAL:
            _save(saved, value)
        return value

    def read_output(self, process_id, offset=0, limit=24000, stream='stdout'):
        if stream not in ('stdout', 'stderr') or offset < 0 or not 0 < limit <= 1_000_000:
            raise ValueError('Invalid stream, byte offset, or read limit')
        self.inspect(process_id)
        job_path = self.folder(process_id) / 'container.json'
        if not job_path.exists():
            return {'process_id': process_id, 'stream': stream, 'content': '', 'next_offset': offset}
        job = self._job(process_id)
        self.runner._verified(job)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run([self.runner.docker, 'logs', job['container_id']], stdout=stdout, stderr=stderr, timeout=30)
            if result.returncode:
                stderr.seek(0)
                raise RuntimeError('Container log read failed: ' + stderr.read(3000).decode(errors='replace'))
            selected = stdout if stream == 'stdout' else stderr
            total = selected.seek(0, 2)
            if offset > total:
                # Bounded Docker logs may rotate. Make the discontinuity explicit.
                return {'process_id': process_id, 'stream': stream, 'content': '', 'next_offset': 0, 'log_rotated': True}
            selected.seek(offset)
            content = selected.read(limit)
            return {'process_id': process_id, 'stream': stream, 'content': content.decode(errors='replace'), 'next_offset': selected.tell(), 'retention': 'Docker retains at most 3 files of 20 MB per container'}

    def all(self):
        return [self.inspect(path.name) for path in sorted(self.directory.iterdir()) if path.is_dir() and (path / 'request.json').exists()]

    def cancel(self, process_id):
        folder = self.folder(process_id)
        state = self.inspect(process_id)
        if state['status'] not in TERMINAL and (folder / 'container.json').exists():
            value = self.runner.stop(self._job(process_id))
            state = {**state, **value, 'status': 'cancelled'}
            _save(folder / 'state.json', state)
        return state

    def cancel_all(self):
        return [self.cancel(item['process_id']) for item in self.all() if item['status'] not in TERMINAL]

    def signal_all(self, signum):
        import signal
        for item in self.all():
            if item['status'] in TERMINAL:
                continue
            job = self._job(item['process_id'])
            if signum == signal.SIGSTOP and item['status'] == 'running':
                self.runner.pause(job)
            elif signum == signal.SIGCONT and item['status'] == 'paused':
                self.runner.resume(job)
            elif signum in (signal.SIGTERM, signal.SIGKILL):
                self.cancel(item['process_id'])
