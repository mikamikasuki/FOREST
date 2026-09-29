"""Real process lifecycle and spending ledger transitions without model calls.

These tests create real accounting reservations through the production guard
while an ordinary command runs. No HTTP model request or completion is supplied.
The dedicated database and processes belong only to each test harness.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import psutil
import pytest

from test_worker import Harness, ROOT, wait_until


RESERVE = r'''
import json,sys
from services.api.db import Session,Provider
from research.agents.budget import make_request_guard,usage_summary
project_id,run_id=sys.argv[1:3]
pricing={'currency':'USD','input_per_million':1,'cached_input_per_million':1,'output_per_million':2}
with Session.begin() as s:
    provider=Provider(name='Lifecycle accounting only',kind='openai',base_url='https://accounting-only.invalid/v1',model='no-request-made',allow_paid=True,config={'budget_usd':1,'pricing':pricing})
    s.add(provider);s.flush();ident=provider.id
snapshot={'id':ident,'model':'no-request-made','config':{'pricing':pricing},'_usage_context':{'project_id':project_id,'run_id':run_id}}
reservation=make_request_guard(snapshot)({'phase':'before','model':'no-request-made','pricing':pricing,'max_output_tokens':50,'input_bytes':64,'attempt':1})
print(json.dumps({'provider_id':ident,'reservation_id':reservation,'before':usage_summary(ident)}))
'''


def reserve_for_running_command(harness, project, run):
    result = subprocess.run(
        [sys.executable, '-c', RESERVE, project['id'], run['id']],
        cwd=ROOT, env=harness.env,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    ledger = json.loads(result.stdout)
    assert ledger['before']['request_count'] == 1
    assert ledger['before']['requests'][0]['status'] == 'reserved'
    assert ledger['before']['reserved_usd'] > 0
    assert ledger['before']['estimated_cost_usd'] == 0
    return ledger


def uncertain_without_releasing_cap(harness, ledger):
    def updated():
        value = harness.request('GET', '/api/providers/' + ledger['provider_id'] + '/usage')
        return value if value['requests'][0]['status'] == 'uncertain' else None
    summary = wait_until(updated, timeout=15)
    assert summary['request_count'] == 1
    assert summary['requests'][0]['id'] == ledger['reservation_id']
    assert summary['uncertain_requests'] == 1
    assert summary['reserved_usd'] == ledger['before']['reserved_usd']
    assert summary['remaining_usd'] == ledger['before']['remaining_usd']
    assert summary['estimated_cost_usd'] == 0
    return summary


@pytest.fixture
def lifecycle_harness(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.start_api()
        harness.start_worker()
        yield harness
    finally:
        harness.cleanup()


def launch_with_reservation(harness, *, timeout=30, fail=False, child=False, kind='command'):
    project, node = harness.project_node(seconds=20, child=child, timeout=timeout, budget={'allow_paid': True, 'cost_usd': 1, 'max_runs': 20, 'seconds': 120})
    if kind != 'command':
        harness.request('PATCH', '/api/nodes/' + node['id'], json={'config': {**node['config'], 'kind': kind}})
    if fail:
        harness.request('PATCH', '/api/nodes/' + node['id'], json={'config': {'kind': 'command', 'command': [sys.executable, '-c', 'import time,sys;time.sleep(3);sys.exit(7)'], 'timeout': 30}})
    run = harness.launch(node)
    running = harness.running(run)
    ledger = reserve_for_running_command(harness, project, run)
    return project, run, running, ledger


def test_actual_api_cancel_keeps_unknown_charge_reserved(lifecycle_harness):
    harness = lifecycle_harness
    _, run, _, ledger = launch_with_reservation(harness)
    cancelled = harness.request('POST', '/api/runs/' + run['id'] + '/cancel', json={})
    assert cancelled['status'] == 'cancelled'
    uncertain_without_releasing_cap(harness, ledger)
    # Repeated owner cancellation is idempotent and cannot release the cap.
    harness.request('POST', '/api/runs/' + run['id'] + '/cancel', json={})
    uncertain_without_releasing_cap(harness, ledger)


def test_actual_project_delete_preserves_uncertain_ledger(lifecycle_harness):
    harness = lifecycle_harness
    project, run, _, ledger = launch_with_reservation(harness)
    deleted = harness.request('DELETE', '/api/projects/' + project['id'])
    assert deleted['deleted'] == project['id']
    assert harness.client.get('/api/runs/' + run['id']).status_code == 404
    uncertain_without_releasing_cap(harness, ledger)


@pytest.mark.parametrize('kind', ['command', 'experiment'])
def test_actual_worker_timeout_keeps_unknown_charge_reserved(lifecycle_harness, kind):
    harness = lifecycle_harness
    _, run, running, ledger = launch_with_reservation(harness, timeout=3, child=True, kind=kind)
    child_file = harness.output(run) / 'child.pid'
    wait_until(child_file.exists)
    child_pid = int(child_file.read_text())
    wait_until((harness.output(run) / 'child_heartbeat.txt').exists)
    terminal = harness.terminal(run, timeout=15)
    assert terminal['status'] == 'failed'
    assert terminal['exit_code'] == 124
    assert 'budget' in terminal['error'].lower()
    def stopped(pid):
        try:
            return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True
    wait_until(lambda: stopped(running['pid']) and stopped(child_pid))
    uncertain_without_releasing_cap(harness, ledger)


def test_actual_executor_crash_keeps_unknown_charge_reserved(lifecycle_harness):
    harness = lifecycle_harness
    _, run, running, ledger = launch_with_reservation(harness)
    os.killpg(running['pid'], signal.SIGKILL)
    terminal = harness.terminal(run, timeout=15)
    assert terminal['status'] == 'interrupted'
    assert 'without a current completion receipt' in terminal['error']
    uncertain_without_releasing_cap(harness, ledger)


def test_actual_command_failure_keeps_unknown_charge_reserved(lifecycle_harness):
    harness = lifecycle_harness
    _, run, _, ledger = launch_with_reservation(harness, fail=True)
    terminal = harness.terminal(run, timeout=15)
    assert terminal['status'] == 'failed'
    assert 'code 7' in terminal['error']
    uncertain_without_releasing_cap(harness, ledger)
