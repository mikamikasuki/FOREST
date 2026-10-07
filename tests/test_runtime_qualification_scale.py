"""Actual public CLI/API submission and paging beyond the batch/page limits.

No worker starts in these tests. The terminal-row fixture tests monitor decisions,
not successful computation or model quality; live compute is qualified separately.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_worker import Harness, wait_until

ROOT = Path(__file__).resolve().parents[1]


def submit(tmp_path, h, nodes):
    checkout = tmp_path/'qualification-client'
    scripts = checkout/'scripts'; scripts.mkdir(parents=True)
    for name in ['qualify_runtime.py', 'qualify_task.py']:
        shutil.copyfile(ROOT/'scripts'/name, scripts/name)
    result = subprocess.run([sys.executable, str(scripts/'qualify_runtime.py'),
        '--nodes', str(nodes), '--seconds', '0', '--submit-only', '--url', h.base_url],
        env=h.env, cwd=checkout, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    path = checkout/'var/qa/runtime-qualification.json'
    return scripts/'qualify_runtime.py', path, json.loads(path.read_text())


@pytest.mark.parametrize('nodes', [201, 501])
def test_first_cli_submission_creates_all_nodes_and_queued_runs(tmp_path, nodes):
    server = tmp_path/'server'; server.mkdir()
    h = Harness(server)
    try:
        h.start_api()
        _, _, report = submit(tmp_path, h, nodes)
        pid = report['project_id']
        graph = h.request('GET', f'/api/projects/{pid}/graph')
        runs = []
        for offset in range(0, nodes, 500):
            runs.extend(h.request('GET', f'/api/projects/{pid}/runs?limit=500&offset={offset}'))
        assert len(graph['nodes']) == len(runs) == nodes
        assert len({r['id'] for r in runs}) == nodes
        assert {r['node_id'] for r in runs} == {n['id'] for n in graph['nodes']}
        assert all(r['status'] == 'queued' and r['pid'] is None for r in runs)
        assert report['status'] == 'running'
        with sqlite3.connect(server/'integration.db') as db:
            assert db.execute('SELECT count(*) FROM model_requests').fetchone()[0] == 0
    finally:
        h.cleanup()


def test_monitor_does_not_finish_while_an_older_page_contains_pending_work(tmp_path):
    server = tmp_path/'server'; server.mkdir()
    h = Harness(server)
    try:
        h.start_api()
        script, path, initial = submit(tmp_path, h, 501)
        pid = initial['project_id']; instant = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(server/'integration.db') as db:
            ids = [r[0] for r in db.execute('SELECT id FROM task_runs WHERE project_id=? ORDER BY created_at DESC', (pid,))]
            assert len(ids) == 501
            # Explicit terminal-state fixture: the first API page is completed,
            # while an actual queued run remains on the older page.
            db.executemany('UPDATE task_runs SET status=?, started_at=?, finished_at=?, metrics=?, resource=? WHERE id=?',
                [('completed', instant, instant, json.dumps({'accuracy': .8, 'fixture_only': True}),
                  json.dumps({'elapsed_seconds': .1}), rid) for rid in ids[:500]])
        monitor = h.spawn('runtime-monitor', [sys.executable, str(script), '--resume-project', pid, '--url', h.base_url])
        def observed_pending():
            for line in (server/'runtime-monitor.log').read_text().splitlines():
                try: value = json.loads(line)
                except ValueError: continue
                if value.get('queued') == 1 and value.get('completed') == 500:
                    return True
            return False
        wait_until(observed_pending, 12)
        assert monitor.poll() is None
        assert json.loads(path.read_text())['status'] == 'running'
        h.control_request('/api/runs/'+ids[-1]+'/cancel', json={})
        assert monitor.wait(timeout=15) == 1
        final = json.loads(path.read_text())
        assert final['status'] == 'failed'
        assert final['counts'] == {'completed': 500, 'cancelled': 1}
    finally:
        h.cleanup()


def test_all_cancelled_runs_produce_a_failed_report_without_measurements(tmp_path):
    server = tmp_path/'server'; server.mkdir()
    h = Harness(server)
    try:
        h.start_api()
        script, path, initial = submit(tmp_path, h, 1)
        pid = initial['project_id']
        run = h.request('GET', f'/api/projects/{pid}/runs')[0]
        h.control_request('/api/runs/'+run['id']+'/cancel', json={})
        result = subprocess.run([sys.executable, str(script), '--resume-project', pid, '--url', h.base_url],
            env=h.env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 1, result.stdout + result.stderr
        final = json.loads(path.read_text())
        assert final['status'] == 'failed' and final['counts'] == {'cancelled': 1}
        assert final['measured_accuracy_range'] is None
        assert 'Traceback' not in result.stderr
    finally:
        h.cleanup()


def test_resume_rejects_a_project_different_from_the_saved_report(tmp_path):
    server = tmp_path/'server'; server.mkdir()
    h = Harness(server)
    try:
        h.start_api()
        script, path, initial = submit(tmp_path, h, 1)
        other = h.request('POST', '/api/projects', json={'name': 'Different scope', 'mode': 'manual'})
        result = subprocess.run([sys.executable, str(script), '--resume-project', other['id'], '--url', h.base_url],
            env=h.env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 2, result.stdout + result.stderr
        assert 'must match the recorded project' in result.stderr
        assert json.loads(path.read_text()) == initial
    finally:
        h.cleanup()
