"""Observe an actual busy child while its parent waits, without mocked counters."""
import json
import subprocess
import sys
import time

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
