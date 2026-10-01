"""Real HTTP, SQLite and subprocess handoffs, with no model or process doubles."""
import json
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
