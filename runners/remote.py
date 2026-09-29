"""Detached SSH tasks with real receipts, log offsets, and identity checks."""
import json
import re
import shlex
import subprocess
import uuid

SUPERVISOR = r'''
import json,os,pathlib,signal,subprocess,sys,time
spec=json.loads(sys.argv[1]); folder=pathlib.Path(spec['folder']); folder.mkdir(parents=True,exist_ok=True)
state={'task_id':spec['task_id'],'pid':os.getpid(),'process_created':subprocess.check_output(['ps','-p',str(os.getpid()),'-o','lstart='],text=True).strip(),'status':'running','started_at':time.time()}
def save():
    temporary=folder/'state.tmp'; temporary.write_text(json.dumps(state)); temporary.replace(folder/'state.json')
save()
def stop(sig,frame):
    state.update(status='cancelled',finished_at=time.time()); save(); raise SystemExit(128+sig)
signal.signal(signal.SIGTERM,stop)
with (folder/'stdout.txt').open('ab',buffering=0) as stream:
    process=subprocess.Popen(spec['argv'],cwd=spec['workdir'],env={**os.environ,**spec.get('env',{})},stdout=stream,stderr=subprocess.STDOUT)
    state['command_pid']=process.pid; save()
    try:
        code=process.wait(timeout=spec.get('timeout'))
        state.update(status='completed' if code==0 else 'failed',exit_code=code)
    except subprocess.TimeoutExpired:
        state.update(status='failed',exit_code=124,error='Configured task time budget exceeded'); save()
        signal.signal(signal.SIGTERM,signal.SIG_IGN); os.killpg(os.getpgrp(),signal.SIGTERM)
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: os.killpg(os.getpgrp(),signal.SIGKILL)
state['finished_at']=time.time(); save()
'''


class RemoteRunner:
    def __init__(self, config):
        self.config = config
        host = config.get('hostname', ''); user = config.get('username', '')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+', host) or not re.fullmatch(r'[A-Za-z0-9_.-]+', user):
            raise ValueError('A valid SSH hostname and username are required')
        self.target = f'{user}@{host}'
        self.options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-p', str(int(config.get('port', 22)))]
        if config.get('identity_file'): self.options += ['-i', config['identity_file']]

    def command(self, argv, timeout=30):
        result = subprocess.run(['ssh', *self.options, self.target, shlex.join(argv)], capture_output=True, text=True, timeout=timeout)
        if result.returncode: raise RuntimeError(result.stderr[-4000:])
        return result.stdout

    def test(self):
        return {'stdout': self.command(['python3', '-c', 'import platform,os; print(platform.platform()); print(os.getpid())']), 'connected': True}

    def start(self, argv, workdir, task_id=None, env=None, timeout=None):
        task_id = task_id or str(uuid.uuid4())
        if not re.fullmatch(r'[A-Za-z0-9_-]+', task_id): raise ValueError('Invalid task identifier')
        folder = workdir.rstrip('/') + '/.forest-tasks/' + task_id
        spec = {'task_id': task_id, 'folder': folder, 'argv': argv, 'workdir': workdir, 'env': env or {}, 'timeout': timeout}
        script = "import pathlib,subprocess,json,time; folder=pathlib.Path(" + repr(folder) + "); folder.mkdir(parents=True,exist_ok=True); state=folder/'state.json'; "
        script += "\nif state.exists(): print(state.read_text())\nelse:\n"
        script += " p=subprocess.Popen(['python3','-c'," + repr(SUPERVISOR) + "," + repr(json.dumps(spec)) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\n"
        script += " for _ in range(100):\n  if state.exists(): break\n  time.sleep(.02)\n print(state.read_text() if state.exists() else json.dumps({'task_id':" + repr(task_id) + ",'pid':p.pid,'status':'starting'}))"
        return {**json.loads(self.command(['python3', '-c', script])), 'folder': folder}

    def status(self, job):
        if not isinstance(job, dict) or not job.get('folder'): raise ValueError('Status requires the task handle returned by start')
        script = "import pathlib,json,subprocess; p=pathlib.Path(" + repr(job['folder']) + ")/'state.json'; s=json.loads(p.read_text()); "
        script += "\nif s['status']=='running':\n actual=subprocess.run(['ps','-p',str(s['pid']),'-o','lstart='],capture_output=True,text=True).stdout.strip()\n if actual!=s['process_created']: s.update(status='lost',error='Remote process ended without receipt')\nprint(json.dumps(s))"
        return {**json.loads(self.command(['python3', '-c', script])), 'folder': job['folder']}

    def output(self, job, offset=0, limit=100000):
        script = "import pathlib,json; p=pathlib.Path(" + repr(job['folder']) + ")/'stdout.txt'; f=p.open('rb'); f.seek(" + str(max(0, int(offset))) + "); data=f.read(" + str(min(max(1, int(limit)), 1_000_000)) + "); print(json.dumps({'text':data.decode(errors='replace'),'offset':f.tell()}))"
        return json.loads(self.command(['python3', '-c', script]))

    def stop(self, job):
        script = "import pathlib,json,subprocess,os,signal,time; s=json.loads((pathlib.Path(" + repr(job['folder']) + ")/'state.json').read_text()); actual=subprocess.run(['ps','-p',str(s['pid']),'-o','lstart='],capture_output=True,text=True).stdout.strip(); "
        script += "\nif s.get('task_id')==" + repr(job['task_id']) + " and actual==s.get('process_created'):\n os.killpg(s['pid'],signal.SIGTERM)\n os.killpg(s['pid'],signal.SIGCONT)\n time.sleep(3)\n try: os.killpg(s['pid'],signal.SIGKILL)\n except ProcessLookupError: pass"
        return self.command(['python3', '-c', script])

    def fetch(self, remote_path, local_path):
        opts = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-P', str(int(self.config.get('port', 22)))]
        if self.config.get('identity_file'): opts += ['-i', self.config['identity_file']]
        subprocess.run(['scp', *opts, f'{self.target}:{remote_path}', str(local_path)], check=True, timeout=120)

    def upload_workspace(self, local_path, remote_path):
        """Stream a real workspace archive through SSH; no shell interpolation."""
        import tarfile
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryFile() as archive:
            with tarfile.open(fileobj=archive, mode='w') as bundle:
                for path in Path(local_path).iterdir():
                    if path.name not in ('.forest-processes', '.forest-tasks'):
                        bundle.add(path, arcname=path.name, recursive=True)
            archive.seek(0)
            code = "import pathlib,tarfile,sys; root=pathlib.Path(" + repr(remote_path) + "); root.mkdir(parents=True,exist_ok=True); archive=tarfile.open(fileobj=sys.stdin.buffer,mode='r|'); "
            code += "\nfor member in archive:\n target=(root/member.name).resolve()\n if not target.is_relative_to(root.resolve()) or member.issym() or member.islnk(): raise ValueError('Unsafe archive member')\n archive.extract(member,path=root)"
            result = subprocess.run(['ssh', *self.options, self.target, shlex.join(['python3', '-c', code])], stdin=archive, capture_output=True)
            if result.returncode: raise RuntimeError(result.stderr.decode(errors='replace')[-4000:])


def execute_remote(config, workspace, output, env=None, require_metrics=False):
    """Run or reconnect to a real SSH job and fetch declared output artifacts.

    config.remote contains SSH configuration, absolute workdir and outputs.
    Handles survive local executor restarts. Remote SSH interruptions raise a
    real error; recovery.mode=restart reconnects to the same remote process.
    """
    import os
    import time
    from pathlib import Path
    remote = config.get('remote')
    if not remote: raise ValueError('Configure remote SSH settings before remote execution')
    workdir = remote.get('workdir')
    if not workdir or not workdir.startswith('/'): raise ValueError('Remote workdir must be an absolute path')
    runner = RemoteRunner(remote)
    output = Path(output); workspace = Path(workspace)
    handle_path = output / 'remote_task.json'
    if handle_path.exists():
        job = json.loads(handle_path.read_text())
    else:
        # Each run gets its own editable directory; parallel jobs do not overwrite it.
        workdir = workdir.rstrip('/') + '/' + os.environ.get('FOREST_RUN_ID', str(uuid.uuid4()))
        runner.upload_workspace(workspace, workdir)
        command = config.get('command')
        if not command: raise ValueError('Remote execution requires a command')
        argv = ['/bin/sh', '-c', command] if isinstance(command, str) else command
        forwarded = {'FOREST_RUN_ID': os.environ.get('FOREST_RUN_ID', ''), 'FOREST_ATTEMPT_ID': os.environ.get('FOREST_ATTEMPT_ID', ''), 'FOREST_RUN_DIR': workdir}
        job = runner.start(argv, workdir, task_id=os.environ.get('FOREST_RUN_ID'), env={**forwarded, **remote.get('env', {})}, timeout=config.get('timeout'))
        job['workdir'] = workdir
        temporary = handle_path.with_suffix('.tmp'); temporary.write_text(json.dumps(job, indent=2)); temporary.replace(handle_path)
    offset = 0
    offset_path = output / 'remote_output_offset.json'
    if offset_path.exists(): offset = int(json.loads(offset_path.read_text()).get('offset', 0))
    while True:
        status = runner.status(job)
        chunk = runner.output(job, offset)
        if chunk['text']: print(chunk['text'], end='', flush=True)
        offset = chunk['offset']; offset_path.write_text(json.dumps({'offset': offset}))
        if status['status'] in ('completed', 'failed', 'cancelled', 'lost'): break
        time.sleep(1)
    (output / 'remote_result.json').write_text(json.dumps(status, indent=2))
    if status['status'] != 'completed': raise RuntimeError('Remote task '+status['status']+': '+str(status.get('error',status.get('exit_code'))))
    outputs = list(dict.fromkeys(([config.get('metrics_file', 'metrics.json')] if require_metrics else []) + remote.get('outputs', [])))
    for relative in outputs:
        if Path(relative).is_absolute() or '..' in Path(relative).parts: raise ValueError('Output paths must be relative to the remote workspace')
        target = workspace / relative; target.parent.mkdir(parents=True, exist_ok=True)
        runner.fetch(job['workdir'] + '/' + relative, target)
    metrics_path = workspace / config.get('metrics_file', 'metrics.json')
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    return {**metrics, 'command_exit_code': status.get('exit_code'), 'remote_execution': status}


def cancel_remote(config, output):
    from pathlib import Path
    path = Path(output) / 'remote_task.json'
    if config.get('remote') and path.exists():
        return RemoteRunner(config['remote']).stop(json.loads(path.read_text()))
