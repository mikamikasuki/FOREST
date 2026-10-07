"""Real HTTP, SQLite and subprocess handoffs, with no model or process doubles."""
import json
import sqlite3
import subprocess
import sys
import uuid

import pytest

from test_worker import Harness, wait_until


@pytest.fixture(scope='module')
def research_worker(tmp_path_factory):
    harness = Harness(tmp_path_factory.mktemp('research-verification'))
    try:
        harness.start_api()
        harness.start_worker()
        yield harness
    finally:
        harness.cleanup()


def arithmetic_command(n, independent=False):
    calculation = f'{n}*({n}+1)*(2*{n}+1)//6' if independent else f'sum(i*i for i in range(1,{n}+1))'
    return [sys.executable, '-c',
            "import json; from pathlib import Path; "
            f"Path('metrics.json').write_text(json.dumps({{'score':{calculation}}}))"]


def add_node(h, project, **fields):
    graph = h.request('GET', f"/api/projects/{project['id']}/graph")
    node_id = str(uuid.uuid4())
    result = h.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
        'expected_revision': graph['revision'], 'request_id': str(uuid.uuid4()),
        'operation': 'add_node', 'params': {'id': node_id, 'branch_id': graph['branches'][0]['id'], **fields}})
    return next(n for n in result['graph']['nodes'] if n['id'] == node_id)


def verification_config(producer, n=500):
    return {'kind': 'verification', 'command': arithmetic_command(n, independent=True),
            'verification': {'producer_node_id': producer['id'], 'checks': [{
                'id': 'sum-of-squares', 'kind': 'numeric_compare',
                'source': 'metrics.json', 'repeat': 'metrics.json', 'pointers': ['/score'],
                'absolute_tolerance': 0, 'relative_tolerance': 0}]}}


def test_real_independent_handoff_rejects_edits_and_revalidates(research_worker):
    h = research_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Actual numerical handoff', 'mode': 'manual',
        'goal': 'Check a measured sum against an independent closed-form computation.',
        'budget': {'max_runs': 20, 'seconds': 120, 'allow_paid': False},
        'config': {'publication_profile': {'id': 'operational'}}})
    producer = add_node(h, project, type='experiment', title='Direct summation',
                        config={'kind': 'experiment', 'command': arithmetic_command(500)})
    verifier = add_node(h, project, type='verification', title='Independent closed-form check',
                        config=verification_config(producer))
    consumer = add_node(h, project, type='analysis', title='Use accepted arithmetic',
        config={'kind': 'command', 'required_verification': [verifier['id']], 'command': [sys.executable, '-c',
                "import json; from pathlib import Path; value=json.loads(Path('observed.json').read_text())['score']; "
                "Path('metrics.json').write_text(json.dumps({'consumed_score':value}))"]},
        inputs=[{'node_id': producer['id'], 'path': 'metrics.json', 'destination': 'observed.json',
                 'verification_node_id': verifier['id']}])
    produced = h.launch(producer)
    assert h.terminal(produced)['status'] == 'completed'
    before = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert before['verification_status'] == 'unverified'
    blocked = h.client.post(f"/api/nodes/{consumer['id']}/run", json={})
    assert blocked.status_code == 409, blocked.text
    checked = h.launch(verifier)
    finished = h.terminal(checked)
    assert finished['status'] == 'completed', finished
    accepted = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert accepted['verification_status'] == 'accepted', accepted
    consumed = h.launch(consumer)
    assert h.terminal(consumed)['metrics']['consumed_score'] == sum(i*i for i in range(1, 501))
    # A directly edited artifact must not retain an old acceptance receipt.
    subprocess.run(arithmetic_command(501), cwd=h.output(produced)/'workspace', check=True)
    changed = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert changed['verification_status'] != 'accepted', changed
    second = h.launch(consumer)
    denied = wait_until(lambda: (r if (r := h.run(second))['status'] == 'waiting_input' else None))
    assert 'verification' in denied['resource'].get('blocked_reason', '')
    assert not (h.output(second)/'workspace'/'metrics.json').exists()
    h.request('POST', f"/api/runs/{second['id']}/cancel", json={})
    # Ordinary editable node configurations and fresh runs repair the route.
    h.request('PATCH', f"/api/nodes/{producer['id']}", json={
        'config': {'kind': 'experiment', 'command': arithmetic_command(501)}})
    h.request('PATCH', f"/api/nodes/{verifier['id']}", json={'config': verification_config(producer, 501)})
    repaired = h.launch(producer)
    assert h.terminal(repaired)['status'] == 'completed'
    rechecked = h.launch(verifier)
    assert h.terminal(rechecked)['status'] == 'completed'
    current = h.request('GET', f"/api/runs/{repaired['id']}/verification")
    assert current['verification_status'] == 'accepted', current
    final = h.launch(consumer)
    assert h.terminal(final)['metrics']['consumed_score'] == sum(i*i for i in range(1, 502))


def test_wrong_real_rerun_does_not_authorize_consumer(research_worker):
    h = research_worker
    project, producer = h.project_node(seconds=.1)
    h.request('PATCH', f"/api/nodes/{producer['id']}", json={
        'config': {'kind': 'experiment', 'command': arithmetic_command(500)}})
    verifier = add_node(h, project, type='verification', title='Different actual evaluation',
                        config=verification_config(producer, 499))
    produced = h.launch(producer)
    assert h.terminal(produced)['status'] == 'completed'
    checked = h.launch(verifier)
    assert h.terminal(checked)['status'] == 'completed'
    state = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert state['verification_status'] == 'rejected', state
    assert json.loads((h.output(produced)/'workspace'/'metrics.json').read_text()) != json.loads(
        (h.output(checked)/'workspace'/'metrics.json').read_text())


def test_counterexample_guard_uses_real_runs(research_worker):
    h = research_worker
    project, node = h.project_node(seconds=.05)
    h.request('PATCH', f"/api/nodes/{node['id']}", json={'config': {
        'kind': 'command', 'research_activity': 'counterexample', 'command': arithmetic_command(5)}})
    for _ in range(3):
        assert h.terminal(h.launch(node))['status'] == 'completed'
    health = h.request('GET', f"/api/projects/{project['id']}/research/route-health")
    assert health['replan_required']
    signals = [s for s in health['signals'] if s['kind'] == 'excessive_counterexample_checks']
    assert len(signals) == 1 and len(signals[0]['run_ids']) == 3
    # Genuine non-counterexample work resets the consecutive checking streak.
    h.request('PATCH', f"/api/nodes/{node['id']}", json={'config': {
        'kind': 'command', 'research_activity': 'baseline', 'command': arithmetic_command(6)}})
    assert h.terminal(h.launch(node))['status'] == 'completed'
    assert not h.request('GET', f"/api/projects/{project['id']}/research/route-health")['replan_required']


def test_paired_analysis_consumes_only_the_verified_csv(research_worker):
    h = research_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Verified paired input', 'mode': 'manual',
        'goal': 'Bind guarded paired analysis to its admitted CSV.',
        'budget': {'max_runs': 20, 'seconds': 180, 'allow_paid': False},
        'config': {'verification_policy': 'required'}})
    producer_script = (
        "from pathlib import Path; "
        "Path('admitted.csv').write_text('unit,baseline,candidate\\nu1,10,12\\nu2,20,22\\n'); "
        "Path('unchecked.csv').write_text('unit,baseline,candidate\\nu1,10,8\\nu2,20,18\\n')")
    producer = add_node(h, project, type='experiment', title='Produce paired observations',
                        config={'kind': 'command', 'command': [sys.executable, '-c', producer_script]})
    verifier = add_node(h, project, type='verification', title='Check admitted CSV structure',
        config={'kind': 'verification', 'command': arithmetic_command(5), 'verification': {
            'producer_node_id': producer['id'], 'checks': [{
                'id': 'admitted-csv-integrity', 'kind': 'csv_integrity', 'source': 'admitted.csv',
                'required_columns': ['unit', 'baseline', 'candidate'], 'unique_by': ['unit'],
                'numeric_columns': ['baseline', 'candidate']}]}})
    produced = h.launch(producer)
    assert h.terminal(produced)['status'] == 'completed'
    checked = h.launch(verifier)
    assert h.terminal(checked)['status'] == 'completed'
    assert h.request('GET', f"/api/runs/{produced['id']}/verification")['verification_status'] == 'accepted'
    unchecked_path = produced['output_path'] + '/workspace/unchecked.csv'
    admitted_path = produced['output_path'] + '/workspace/admitted.csv'
    paired_fields = {'unit_column': 'unit', 'baseline_column': 'baseline',
                     'candidate_column': 'candidate', 'direction': 'lower', 'bootstrap_samples': 100}

    # The direct statistics endpoint must reject a guarded path outside the
    # accepted check scope before persisting an Analysis.
    standalone = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
        **paired_fields, 'path': unchecked_path, 'run_ids': [produced['id']]})
    blocked = wait_until(lambda: (current if (current := h.run(standalone))['status'] == 'waiting_input' else None))
    assert blocked['resource']['blocked_reason'] == 'verification_scope', blocked
    h.request('POST', f"/api/runs/{standalone['id']}/cancel", json={})

    # The graph consumer declares admitted.csv as its bound input while its
    # paired configuration attempts to select unchecked.csv.
    consumer = add_node(h, project, type='analysis', title='Consume admitted paired observations',
        config={'kind': 'analysis', 'analysis_type': 'paired', 'path': unchecked_path, **paired_fields},
        inputs=[{'node_id': producer['id'], 'path': 'admitted.csv', 'destination': 'admitted.csv',
                 'verification_node_id': verifier['id']}])
    graph_run = h.launch(consumer)
    graph_blocked = wait_until(lambda: (current if (current := h.run(graph_run))['status'] == 'waiting_input' else None))
    assert graph_blocked['resource']['blocked_reason'] == 'verification_scope', graph_blocked
    h.request('POST', f"/api/runs/{graph_run['id']}/cancel", json={})

    # The graph path can consume the admitted copy at its declared destination.
    graph_valid_node = add_node(h, project, type='analysis', title='Consume admitted paired copy',
        config={'kind': 'analysis', 'analysis_type': 'paired', 'path': 'admitted.csv', **paired_fields},
        inputs=[{'node_id': producer['id'], 'path': 'admitted.csv', 'destination': 'admitted.csv',
                 'verification_node_id': verifier['id']}])
    graph_valid = h.terminal(h.launch(graph_valid_node))
    assert graph_valid['status'] == 'completed', graph_valid
    assert graph_valid['metrics']['improvement'] == -2

    # A selected admitted artifact remains usable, including a real negative
    # improvement value; structural verification does not reinterpret it.
    valid = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
        **paired_fields, 'path': admitted_path, 'run_ids': [produced['id']]})
    result = h.terminal(valid)
    assert result['status'] == 'completed', result
    assert result['metrics']['improvement'] == -2

    admitted_csv = h.output(produced) / 'workspace' / 'admitted.csv'
    admitted_csv.write_text(admitted_csv.read_text() + '\n')
    stale = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert stale['verification_status'] != 'accepted', stale
    stale_use = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
        **paired_fields, 'path': admitted_path, 'run_ids': [produced['id']]})
    stale_waiting = wait_until(lambda: (current if (current := h.run(stale_use))['status'] == 'waiting_input' else None))
    assert 'verification' in stale_waiting['resource'].get('blocked_reason', '')
    h.request('POST', f"/api/runs/{stale_use['id']}/cancel", json={})
    rechecked = h.terminal(h.launch(verifier))
    assert rechecked['status'] == 'completed', rechecked
    assert h.request('GET', f"/api/runs/{produced['id']}/verification")['verification_status'] == 'accepted'
    repaired = h.terminal(h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
        **paired_fields, 'path': admitted_path, 'run_ids': [produced['id']]}))
    assert repaired['status'] == 'completed' and repaired['metrics']['improvement'] == -2, repaired

    # The API process can restart and read the durable run and analysis rows
    # from the same disposable SQLite/data paths.
    h.client.close()
    h.stop(h.api)
    h.client = h.client.__class__(base_url=h.base_url, timeout=12)
    h.start_api()
    assert h.request('GET', f"/api/runs/{valid['id']}")['metrics']['improvement'] == -2
    assert h.request('GET', f"/api/runs/{graph_valid['id']}")['metrics']['improvement'] == -2
    assert h.request('GET', f"/api/runs/{repaired['id']}")['metrics']['improvement'] == -2
    with sqlite3.connect(h.directory / 'integration.db') as db:
        rows = db.execute('SELECT data FROM analyses WHERE project_id = ?', (project['id'],)).fetchall()
    analyses = [json.loads(row[0]) for row in rows]
    assert len(analyses) == 3 and all(item['improvement'] == -2 for item in analyses)
    graph_analysis = next(item for item in analyses if 'verification_input' in item)
    assert graph_analysis['verification_input']['source_run_id'] == produced['id']
    assert graph_analysis['run_ids'] == [produced['id']]


def test_explicit_verifier_guard_and_optional_unguarded_paired_use(research_worker):
    h = research_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Explicit paired verifier', 'mode': 'manual',
        'goal': 'Exercise explicit verifier guards separately from required policy.',
        'budget': {'max_runs': 20, 'seconds': 180, 'allow_paid': False},
        'config': {'verification_policy': 'optional'}})
    producer_script = (
        "from pathlib import Path; "
        "Path('admitted.csv').write_text('unit,baseline,candidate\\nu1,10,12\\nu2,20,22\\n'); "
        "Path('unchecked.csv').write_text('unit,baseline,candidate\\nu1,10,8\\nu2,20,18\\n')")
    producer = add_node(h, project, type='experiment', title='Produce paired observations',
                        config={'kind': 'command', 'command': [sys.executable, '-c', producer_script]})
    verifier = add_node(h, project, type='verification', title='Check admitted CSV structure',
        config={'kind': 'verification', 'command': arithmetic_command(5), 'verification': {
            'producer_node_id': producer['id'], 'checks': [{
                'id': 'admitted-csv-integrity', 'kind': 'csv_integrity', 'source': 'admitted.csv',
                'required_columns': ['unit', 'baseline', 'candidate'], 'unique_by': ['unit'],
                'numeric_columns': ['baseline', 'candidate']}]}})
    produced = h.launch(producer)
    assert h.terminal(produced)['status'] == 'completed'
    checked = h.launch(verifier)
    assert h.terminal(checked)['status'] == 'completed'
    unchecked_path = produced['output_path'] + '/workspace/unchecked.csv'
    paired_fields = {'unit_column': 'unit', 'baseline_column': 'baseline',
                     'candidate_column': 'candidate', 'direction': 'lower', 'bootstrap_samples': 100}

    guarded = add_node(h, project, type='analysis', title='Explicitly guarded paired analysis',
        config={'kind': 'analysis', 'analysis_type': 'paired', 'path': unchecked_path, **paired_fields},
        inputs=[{'node_id': producer['id'], 'path': 'admitted.csv', 'destination': 'admitted.csv',
                 'verification_node_id': verifier['id']}])
    attempt = h.launch(guarded)
    blocked = wait_until(lambda: (current if (current := h.run(attempt))['status'] == 'waiting_input' else None))
    assert blocked['resource']['blocked_reason'] == 'verification_scope', blocked
    h.request('POST', f"/api/runs/{attempt['id']}/cancel", json={})

    # Without required policy or any explicit verifier/input binding, the same
    # project-local CSV remains available to ordinary optional analysis.
    optional = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
        **paired_fields, 'path': unchecked_path})
    completed = h.terminal(optional)
    assert completed['status'] == 'completed', completed
    assert completed['metrics']['improvement'] == 2
