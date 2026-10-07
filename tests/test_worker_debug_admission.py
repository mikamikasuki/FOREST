"""Real PostgreSQL workers retain capacity at a stopped debug checkpoint."""
import datetime as dt
import os
import sys
import time

import psutil
import psycopg
import pytest

from test_postgres import postgres_workers
from test_worker import WORKLOAD, wait_until


pytestmark = pytest.mark.skipif(
    not os.environ.get('FOREST_TEST_POSTGRES'),
    reason='Requires explicitly authorized temporary PostgreSQL test database',
)


def test_debug_checkpoint_retains_shared_capacity_until_resumed_and_completed(postgres_workers):
    harness = postgres_workers
    for worker in harness.workers:
        harness.stop(worker)
    harness.env['FOREST_WORKER_CPU_SLOTS'] = '2'
    first_project, first_node = harness.project_node(seconds=.6)
    second_project, second_node = harness.project_node(seconds=.6)
    assert first_project['id'] != second_project['id']
    for node in (first_node, second_node):
        config = {**node['config'], 'resources': {'cpu': 2}}
        if node['id'] == first_node['id']:
            config['command'] = [sys.executable, str(WORKLOAD), '--seconds', '.6', '--checkpoint', 'before_tool']
        harness.request('PATCH', f"/api/nodes/{node['id']}", json={'config': config})

    first = harness.launch(first_node)
    harness.start_worker()
    harness.workers = [harness.worker, harness.spawn('debug-worker-2', [sys.executable, '-m', 'services.worker.main'])]
    stopped = wait_until(lambda: (
        run if (run := harness.run(first))['status'] == 'waiting_input'
        and run['resource'].get('checkpoint', {}).get('stage') == 'before_tool'
        else None
    ))
    harness.run_processes.append((stopped['pid'], stopped['process_created']))
    wait_until(lambda: psutil.Process(stopped['pid']).status() == psutil.STATUS_STOPPED)
    second = harness.launch(second_node)
    identifiers = [first['id'], second['id']]

    with psycopg.connect(harness.postgres_dsn, autocommit=True) as connection:
        # Several real worker ticks must leave the cross-project contender
        # queued while the checkpointed executor still owns the full capacity.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            rows = connection.execute(
                'SELECT id,status,pid FROM task_runs WHERE id=ANY(%s)', (identifiers,),
            ).fetchall()
            statuses = {row[0]: row[1] for row in rows}
            assert statuses[first['id']] == 'waiting_input'
            assert statuses[second['id']] == 'queued', rows
            assert psutil.Process(stopped['pid']).status() == psutil.STATUS_STOPPED
            assert all(worker.poll() is None for worker in harness.workers)
            time.sleep(.04)
        assert not (harness.output(second) / 'heartbeat.txt').exists()

        resumed = harness.request('POST', f"/api/runs/{first['id']}/resume", json={})
        assert resumed['status'] in ('pausing', 'running')
        wait_until(lambda: harness.run(first)['status'] in ('running', 'completed'))
        observed = set()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            rows = connection.execute(
                'SELECT id,status,pid,process_created FROM task_runs WHERE id=ANY(%s)', (identifiers,),
            ).fetchall()
            allocated = [row for row in rows if row[1] in ('running', 'pausing', 'paused', 'waiting_input') and row[2]]
            assert len(allocated) <= 1, rows
            for row in allocated:
                observed.add(row[0])
                identity = (row[2], row[3])
                if identity not in harness.run_processes:
                    harness.run_processes.append(identity)
            if all(row[1] == 'completed' for row in rows):
                break
            time.sleep(.03)
        else:
            pytest.fail(f'Both jobs did not complete after resuming the actual checkpoint: {rows}')
        assert observed == set(identifiers)
        records = connection.execute(
            'SELECT id,started_at,finished_at FROM task_runs WHERE id=ANY(%s)', (identifiers,),
        ).fetchall()
    times = {row[0]: (dt.datetime.fromisoformat(row[1]), dt.datetime.fromisoformat(row[2])) for row in records}
    assert times[second['id']][0] >= times[first['id']][1]
    completed_first, completed_second = harness.run(first), harness.run(second)
    assert completed_first['metrics']['checkpoint_value']['tool'] == 'write_file'
    assert (harness.output(first) / 'workspace' / 'checkpoint_output.txt').read_text() == 'original test callback payload'
    assert completed_first['metrics']['ticks'] > 0 and completed_second['metrics']['ticks'] > 0
