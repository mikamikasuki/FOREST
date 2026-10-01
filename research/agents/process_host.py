"""Small independent supervisor for actual commands; survives agent exits."""
from __future__ import annotations
import fcntl
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

# Executed by absolute path so it also works in isolated branch workspaces.
from processes import atomic_json, read_json
import psutil

# This supervisor is launched by absolute filename from a task workspace.
# Resolve the shared checkout policy from its installation, not task files.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.execution.repository import task_environment


def execute_child(folder):
    request = read_json(folder / 'request.json')
    os.chdir(request['cwd'])
    # Repository credentials belong to the worker's checkout step. Detached
    # tasks do not inherit its Git helpers, SSH agent or registry location.
    env = task_environment({
        'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''),
        'PYTHONUNBUFFERED': '1', 'MPLBACKEND': 'Agg',
        **{str(k): str(v) for k, v in request['env'].items()}})
    atomic_json(folder / 'identity.json', {'pid': os.getpid(), 'process_created': psutil.Process().create_time(), 'resolved_executable': shutil.which(request['command'][0], path=env['PATH']), 'python_environment': sys.executable})
    os.execvpe(request['command'][0], request['command'], env)


def supervise(folder):
    with (folder / 'supervisor.lease').open('a') as lease:
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if (folder / 'identity.json').exists() or read_json(folder / 'state.json').get('status') in ('completed', 'failed', 'cancelled'):
            return
        request = read_json(folder / 'request.json')
        state = {'status': 'starting', 'host_pid': os.getpid(), 'host_created': psutil.Process().create_time(), 'started_at': time.time()}
        atomic_json(folder / 'state.json', state)
        with (folder / 'stdout.log').open('ab') as stdout, (folder / 'stderr.log').open('ab') as stderr:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(folder), '--child'],
                                       cwd=request['cwd'], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, close_fds=True)
            state.update(status='running')
            atomic_json(folder / 'state.json', state)
            cancelled_at = None
            timed_out = False
            while process.poll() is None:
                cancel = (folder / 'cancel.json').exists()
                timed_out = bool(request.get('timeout') and time.time() - state['started_at'] >= float(request['timeout']))
                if cancel or timed_out:
                    if cancelled_at is None:
                        cancelled_at = time.monotonic()
                        try:
                            os.killpg(process.pid, signal.SIGCONT)
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    elif time.monotonic() - cancelled_at >= 3:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                time.sleep(.1)
            state.update(status='cancelled' if (folder / 'cancel.json').exists() else ('completed' if process.returncode == 0 else 'failed'),
                         exit_code=process.returncode, finished_at=time.time(), elapsed_seconds=time.time() - state['started_at'])
            if timed_out:
                state.update(status='failed', error='Configured process timeout exceeded')
            atomic_json(folder / 'state.json', state)


if __name__ == '__main__':
    target = Path(sys.argv[1]).resolve()
    if '--child' in sys.argv:
        execute_child(target)
    else:
        supervise(target)
