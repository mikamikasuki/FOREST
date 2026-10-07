"""Observe an actual busy child while its parent waits, without mocked counters."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'research' / 'agents'))
from research.agents.process_host import process_group_exists
from runners.local import process_tree_resources


def test_host_cpu_includes_a_busy_child(tmp_path):
    marker = tmp_path / 'ready.json'
    child = "import time,json,pathlib,os;start=time.process_time();\nwhile time.process_time()-start<0.7: sum(i*i for i in range(10000))\npathlib.Path(" + repr(str(marker)) + ").write_text(json.dumps({'pid':os.getpid(),'cpu_seconds':time.process_time()}));time.sleep(20)"
    parent = "import subprocess,sys;subprocess.run([sys.executable,'-c'," + repr(child) + "])"
    process = subprocess.Popen([sys.executable, '-c', parent], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        receipt = json.loads(marker.read_text())
        resources = process_tree_resources(process.pid)
        assert resources['observed_host_processes'] >= 2
        assert resources['cpu_seconds'] >= .6
        assert resources['host_process_cpu_seconds'] == resources['cpu_seconds']
        assert resources['rss_bytes'] > 0
    finally:
        import os, signal
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires POSIX process groups')
def test_zombie_only_process_group_is_treated_as_terminated():
    ready_read, ready_write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(ready_read)
        os.setpgid(0, 0)
        os.write(ready_write, b'group-ready')
        os.close(ready_write)
        os._exit(0)

    os.close(ready_write)
    try:
        assert os.read(ready_read, len(b'group-ready')) == b'group-ready'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if psutil.Process(child).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(.01)
        assert psutil.Process(child).status() == psutil.STATUS_ZOMBIE
        assert not process_group_exists(child)
    finally:
        os.close(ready_read)
        os.waitpid(child, 0)
