"""Actual local process control with process identity and bounded log reads."""
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
import psutil


def process_matches(pid, created):
    try:
        p = psutil.Process(pid)
        return abs(p.create_time() - float(created or 0)) < 0.2 and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
        return False


def signal_group(pid, created, sig):
    if not process_matches(pid, created):
        return False
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except ProcessLookupError:
        return False


def stop_group(pid, created, grace=3):
    if not process_matches(pid, created):
        return
    try:
        group = os.getpgid(pid)
        os.killpg(group, signal.SIGTERM)
        # A stopped process cannot handle TERM until continued.
        os.killpg(group, signal.SIGCONT)
    except ProcessLookupError:
        return
    except PermissionError:
        if not process_matches(pid, created): return
        raise
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try: os.killpg(group, 0)
        except ProcessLookupError: return
        except PermissionError:
            if not process_matches(pid, created): return
            raise
        time.sleep(.05)
    try: os.killpg(group, signal.SIGKILL)
    except ProcessLookupError: pass
    except PermissionError:
        if process_matches(pid, created): raise


def run_command(command, cwd, timeout=None, env=None, output_path=None, tail_bytes=1_000_000):
    """No implicit time limit; full output is spooled rather than held in memory."""
    start = time.monotonic()
    if output_path:
        target = Path(output_path)
    else:
        descriptor, generated = tempfile.mkstemp(prefix='forest-command-', suffix='.log')
        os.close(descriptor)
        target = Path(generated)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('ab', buffering=0) as stream:
        p = subprocess.Popen(command, cwd=str(cwd), stdout=stream, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        created = psutil.Process(p.pid).create_time()
        try: p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_group(p.pid, created)
            p.wait()
            raise TimeoutError(f'Command exceeded {timeout}s; output is available at {target}')
    with target.open('rb') as stream:
        stream.seek(max(0, target.stat().st_size - tail_bytes))
        output = stream.read().decode(errors='replace')
    return {'exit_code': p.returncode, 'stdout': output, 'output_path': str(target),
            'output_truncated': target.stat().st_size > tail_bytes, 'elapsed': time.monotonic() - start}


def process_tree_resources(pid, created=None):
    """Observed host CPU/RSS, including live descendants and reaped children.

    CPU counters describe OS processes, not containers managed by a remote
    Docker daemon. A process disappearing during inspection is simply omitted.
    """
    parent = psutil.Process(pid)
    if created is not None and abs(parent.create_time() - float(created)) >= .2:
        raise psutil.NoSuchProcess(pid)
    processes = [parent, *parent.children(recursive=True)]
    cpu_seconds, rss_bytes, measured = 0.0, 0, 0
    for process in processes:
        try:
            counters = process.cpu_times()
            # children_* account for work already reaped by this process;
            # live descendants are represented separately in the traversal.
            cpu_seconds += sum(float(getattr(counters, field, 0)) for field in ('user', 'system', 'children_user', 'children_system'))
            rss_bytes += process.memory_info().rss
            measured += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {'cpu_seconds': cpu_seconds, 'host_process_cpu_seconds': cpu_seconds,
            'rss_bytes': rss_bytes, 'observed_host_processes': measured}
