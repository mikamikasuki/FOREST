"""Real HTTP, SQLite and subprocess handoffs, with no model or process doubles."""
import hashlib
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


def test_paired_statistics_retain_consumed_upload_across_same_path_replacement(research_worker):
    h = research_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Retained paired input ' + str(uuid.uuid4())[:8],
        'mode': 'manual',
        'goal': 'Keep historical paired metrics bound to the exact uploaded observations.',
        'budget': {'max_runs': 20, 'seconds': 180, 'allow_paid': False}})
    source_path = 'uploads/paired.csv'
    v1_bytes = (
        b'unit,baseline,candidate\n'
        b'u1,10,8\nu2,12,11\nu3,8,6\nu4,14,11\n')
    v2_bytes = (
        b'unit,baseline,candidate\n'
        b'u1,10,13\nu2,12,14\nu3,8,11\nu4,14,17\n')
    options = {
        'unit_column': 'unit', 'baseline_column': 'baseline',
        'candidate_column': 'candidate', 'direction': 'lower',
        'confidence': 0.95, 'bootstrap_samples': 1000, 'seed': 617,
        'meaningful_effect': 0,
    }

    def upload(filename, content):
        response = h.client.post(
            f"/api/projects/{project['id']}/upload",
            params={'directory': 'uploads'},
            files={'file': (filename, content, 'text/csv')})
        assert response.status_code == 200, response.text
        return response.json()

    def run_statistics(path):
        queued = h.request('POST', f"/api/projects/{project['id']}/statistics/paired",
                           json={**options, 'path': path})
        finished = h.terminal(queued)
        assert finished['status'] == 'completed', finished
        return finished

    def analysis_for(run_id, before):
        records = h.request('GET', '/api/analyses',
                            params={'project_id': project['id']})
        created = [item for item in records if item['id'] not in before]
        assert len(created) == 1, created
        return created[0]

    def download(path):
        return h.client.get(f"/api/projects/{project['id']}/download",
                            params={'path': path})

    escaped = h.client.post(
        f"/api/projects/{project['id']}/statistics/paired",
        json={**options, 'path': '../../outside.csv'})
    assert escaped.status_code == 403
    assert escaped.json()['detail']['code'] == 'PATH_ESCAPE'

    original_upload = upload('paired.csv', v1_bytes)
    assert original_upload['path'] == source_path

    before_analyses = {
        item['id'] for item in h.request(
            'GET', '/api/analyses', params={'project_id': project['id']})}
    v1_run = run_statistics(source_path)
    v1_analysis = analysis_for(v1_run['id'], before_analyses)
    v1_artifact_path = f"{v1_run['output_path']}/paired-input.csv"
    v1_metrics_path = f"{v1_run['output_path']}/metrics.json"
    v1_result_path = f"{v1_run['output_path']}/result.json"
    v1_input_before = download(v1_artifact_path)
    v1_metrics_before = download(v1_metrics_path)
    v1_result_before = download(v1_result_path)
    assert v1_metrics_before.status_code == 200, v1_metrics_before.text
    assert v1_result_before.status_code == 200, v1_result_before.text
    source_before = download(source_path)
    assert source_before.status_code == 200 and source_before.content == v1_bytes

    claim_payload = {
        'text': 'The first uploaded candidate improves the paired score.',
        'verification_status': 'unverified',
        'source_path': source_path,
        'source_analysis_id': v1_analysis['id'],
        'metric_run_id': v1_run['id'],
        'metric_artifact_path': v1_metrics_path,
        'metric_snapshot': {'improvement': 2.0},
    }
    claim = h.request('POST', '/api/claims', json={
        'project_id': project['id'], 'title': 'Unverified V1 paired claim',
        'data': claim_payload})
    claim_before = h.request('GET', f"/api/claims/{claim['id']}")
    assert claim_before['data'] == claim_payload
    assert claim_before['status'] == 'draft'

    # Re-run unchanged bytes and then consume identical bytes under another
    # filename. Neither operation publishes over the original source path.
    before_analyses = {
        item['id'] for item in h.request(
            'GET', '/api/analyses', params={'project_id': project['id']})}
    unchanged_run = run_statistics(source_path)
    unchanged_analysis = analysis_for(unchanged_run['id'], before_analyses)
    unchanged_source = download(source_path)
    unchanged_input = download(
        f"{unchanged_run['output_path']}/paired-input.csv")

    other_path = 'uploads/paired-copy.csv'
    other_upload = upload('paired-copy.csv', v1_bytes)
    assert other_upload['path'] == other_path
    before_analyses = {
        item['id'] for item in h.request(
            'GET', '/api/analyses', params={'project_id': project['id']})}
    other_run = run_statistics(other_path)
    other_analysis = analysis_for(other_run['id'], before_analyses)
    other_source = download(other_path)
    other_input = download(f"{other_run['output_path']}/paired-input.csv")
    v1_analysis_after_other = h.request('GET', f"/api/analyses/{v1_analysis['id']}")
    v1_after_controls = download(v1_artifact_path)
    metrics_after_controls = download(v1_metrics_path)

    # A real same-path replacement marks the V1 Analysis and authored claim
    # stale before a distinct V2 run is explicitly submitted.
    replaced = upload('paired.csv', v2_bytes)
    assert replaced['path'] == source_path
    stale_analysis = h.request('GET', f"/api/analyses/{v1_analysis['id']}")
    stale_claim = h.request('GET', f"/api/claims/{claim['id']}")
    v2_source = download(source_path)
    before_analyses = {
        item['id'] for item in h.request(
            'GET', '/api/analyses', params={'project_id': project['id']})}
    v2_run = run_statistics(source_path)
    v2_analysis = analysis_for(v2_run['id'], before_analyses)
    v2_artifact_path = f"{v2_run['output_path']}/paired-input.csv"
    v2_metrics_path = f"{v2_run['output_path']}/metrics.json"
    v2_result_path = f"{v2_run['output_path']}/result.json"
    v2_input = download(v2_artifact_path)
    v2_metrics = download(v2_metrics_path)
    v2_result = download(v2_result_path)

    # A retry reuses the V1 task configuration (including its original
    # source-path key), but must derive provenance again from the bytes it
    # actually consumes rather than inheriting any V1 result metadata.
    retried = h.request('POST', f"/api/runs/{v1_run['id']}/retry", json={})
    retried = h.terminal(retried)
    retry_analysis = analysis_for(
        retried['id'], {v1_analysis['id'], unchanged_analysis['id'],
                        other_analysis['id'], v2_analysis['id']})
    retry_artifact_path = f"{retried['output_path']}/paired-input.csv"
    retry_metrics_path = f"{retried['output_path']}/metrics.json"
    retry_result_path = f"{retried['output_path']}/result.json"
    retry_input = download(retry_artifact_path)
    retry_metrics = download(retry_metrics_path)
    retry_result = download(retry_result_path)

    # Verify persisted run, resource, Analysis and claim references after a
    # real task-owned API/worker restart against the same temporary SQLite DB.
    h.stop(h.worker)
    h.stop(h.api)
    h.client.close()
    h.client = h.client.__class__(base_url=h.base_url, timeout=12)
    h.start_api()
    h.start_worker()
    v1_reopened = h.request('GET', f"/api/runs/{v1_run['id']}")
    v2_reopened = h.request('GET', f"/api/runs/{v2_run['id']}")
    retry_reopened = h.request('GET', f"/api/runs/{retried['id']}")
    v1_analysis_reopened = h.request('GET', f"/api/analyses/{v1_analysis['id']}")
    v2_analysis_reopened = h.request('GET', f"/api/analyses/{v2_analysis['id']}")
    retry_analysis_reopened = h.request('GET', f"/api/analyses/{retry_analysis['id']}")
    claim_reopened = h.request('GET', f"/api/claims/{claim['id']}")
    v1_input_reopened = download(v1_artifact_path)
    v1_metrics_reopened = download(v1_metrics_path)
    v1_result_reopened = download(v1_result_path)
    v2_input_reopened = download(v2_artifact_path)
    v2_metrics_reopened = download(v2_metrics_path)
    v2_result_reopened = download(v2_result_path)
    retry_input_reopened = download(retry_artifact_path)
    retry_metrics_reopened = download(retry_metrics_path)
    retry_result_reopened = download(retry_result_path)
    v1_file_after_replacement = download(source_path)

    expected_v1_provenance = {
        'artifact_path': v1_artifact_path,
        'source_path': source_path,
        'sha256': hashlib.sha256(v1_bytes).hexdigest(),
        'size_bytes': len(v1_bytes),
    }
    expected_v2_provenance = {
        'artifact_path': v2_artifact_path,
        'source_path': source_path,
        'sha256': hashlib.sha256(v2_bytes).hexdigest(),
        'size_bytes': len(v2_bytes),
    }
    expected_retry_provenance = {
        'artifact_path': retry_artifact_path,
        'source_path': source_path,
        'sha256': hashlib.sha256(v2_bytes).hexdigest(),
        'size_bytes': len(v2_bytes),
    }
    assert v1_run['metrics']['improvement'] == 2.0
    assert unchanged_run['metrics']['improvement'] == 2.0
    assert other_run['metrics']['improvement'] == 2.0
    assert v2_run['metrics']['improvement'] == -2.75
    assert v1_run['id'] != v2_run['id']
    assert v1_artifact_path != v2_artifact_path
    assert v1_metrics_path != v2_metrics_path
    assert retried['id'] not in {v1_run['id'], v2_run['id']}
    assert retried['metrics']['improvement'] == -2.75
    assert retried['config']['path'] == source_path
    assert 'input_provenance' not in retried['config']
    assert retried['metrics']['input_provenance'] == expected_retry_provenance
    assert retried['metrics']['source_file'] == str((h.output(retried) / 'paired-input.csv').resolve())
    assert retry_analysis['data']['input_provenance'] == expected_retry_provenance
    assert retry_analysis['data']['analysis_run_id'] == retried['id']
    assert retry_analysis['data']['path'] == retry_artifact_path
    assert retry_analysis['data']['source_path'] == source_path
    assert retry_input.status_code == 200 and retry_input.content == v2_bytes
    assert retry_metrics.status_code == 200
    assert retry_result.status_code == 200
    assert v1_input_before.status_code == 200 and v1_input_before.content == v1_bytes
    assert v1_run['metrics'].get('input_provenance') == expected_v1_provenance
    assert v1_run['metrics']['source_file'] == str((h.output(v1_run) / 'paired-input.csv').resolve())
    assert 'input_provenance' not in v1_run['config']
    assert unchanged_run['metrics'].get('input_provenance') == {
        'artifact_path': f"{unchanged_run['output_path']}/paired-input.csv",
        **{key: value for key, value in expected_v1_provenance.items()
           if key != 'artifact_path'}}
    assert unchanged_analysis['data'].get('input_provenance') == unchanged_run['metrics']['input_provenance']
    assert unchanged_analysis['data']['analysis_run_id'] == unchanged_run['id']
    assert other_run['metrics'].get('input_provenance') == {
        'artifact_path': f"{other_run['output_path']}/paired-input.csv",
        'source_path': other_path,
        'sha256': hashlib.sha256(v1_bytes).hexdigest(),
        'size_bytes': len(v1_bytes),
    }
    assert other_analysis['data'].get('input_provenance') == other_run['metrics']['input_provenance']
    assert other_analysis['data']['analysis_run_id'] == other_run['id']
    assert v2_run['metrics'].get('input_provenance') == expected_v2_provenance
    assert v2_run['metrics']['source_file'] == str((h.output(v2_run) / 'paired-input.csv').resolve())
    assert json.loads(v1_metrics_before.content)['input_provenance'] == expected_v1_provenance
    assert json.loads(v2_metrics.content)['input_provenance'] == expected_v2_provenance
    assert v1_analysis['data'].get('input_provenance') == expected_v1_provenance
    assert v1_analysis['data']['analysis_run_id'] == v1_run['id']
    assert v1_analysis['data']['path'] == v1_artifact_path
    assert v1_analysis['data'].get('source_path') == source_path
    assert v2_analysis['data'].get('input_provenance') == expected_v2_provenance
    assert v2_analysis['data']['analysis_run_id'] == v2_run['id']
    assert v2_analysis['data']['path'] == v2_artifact_path
    assert v2_analysis['data'].get('source_path') == source_path
    assert unchanged_source.status_code == 200 and unchanged_source.content == v1_bytes
    assert unchanged_input.status_code == 200 and unchanged_input.content == v1_bytes
    assert other_source.status_code == 200 and other_source.content == v1_bytes
    assert other_input.status_code == 200 and other_input.content == v1_bytes
    assert v1_analysis_after_other['status'] == 'ready_for_review'
    assert v1_analysis_after_other['data']['input_provenance'] == expected_v1_provenance
    assert v1_after_controls.status_code == 200 and v1_after_controls.content == v1_bytes
    assert metrics_after_controls.status_code == 200
    assert v1_metrics_before.content == metrics_after_controls.content
    assert v1_result_before.content == download(v1_result_path).content
    assert v2_source.status_code == 200 and v2_source.content == v2_bytes
    assert v1_input_reopened.status_code == 200 and v1_input_reopened.content == v1_bytes
    assert v1_metrics_reopened.status_code == 200
    assert v1_metrics_reopened.content == v1_metrics_before.content
    assert v1_result_reopened.status_code == 200
    assert v1_result_reopened.content == v1_result_before.content
    assert v2_input.status_code == 200 and v2_input.content == v2_bytes
    assert v2_metrics.status_code == 200 and v2_metrics_reopened.content == v2_metrics.content
    assert v2_result.status_code == 200 and v2_result_reopened.content == v2_result.content
    assert retry_input_reopened.status_code == 200 and retry_input_reopened.content == v2_bytes
    assert retry_metrics_reopened.status_code == 200
    assert retry_metrics_reopened.content == retry_metrics.content
    assert retry_result_reopened.status_code == 200
    assert retry_result_reopened.content == retry_result.content
    assert json.loads(v1_result_reopened.content)['metrics']['input_provenance'] == expected_v1_provenance
    assert json.loads(v2_result_reopened.content)['metrics']['input_provenance'] == expected_v2_provenance
    assert json.loads(retry_result_reopened.content)['metrics']['input_provenance'] == expected_retry_provenance
    assert v1_reopened['metrics'] == v1_run['metrics']
    assert v2_reopened['metrics'] == v2_run['metrics']
    assert retry_reopened['metrics'] == retried['metrics']
    assert stale_analysis['status'] == 'needs_update'
    assert stale_analysis['data']['analysis_run_id'] == v1_analysis['data']['analysis_run_id']
    assert stale_analysis['data']['run_ids'] == v1_analysis['data']['run_ids']
    assert stale_analysis['data']['metrics_path'] == v1_analysis['data']['metrics_path']
    assert stale_analysis['data']['improvement'] == v1_analysis['data']['improvement'] == 2.0
    assert stale_claim['status'] == 'needs_update'
    assert stale_claim['data']['source_analysis_id'] == v1_analysis['id']
    assert stale_claim['data']['metric_run_id'] == v1_run['id']
    assert stale_claim['data']['metric_artifact_path'] == v1_metrics_path
    assert stale_claim['data']['metric_snapshot'] == {'improvement': 2.0}
    assert stale_claim['data']['verification_status'] == 'unverified'
    assert stale_analysis['data']['input_provenance'] == expected_v1_provenance
    assert claim_reopened == stale_claim
    assert v1_analysis_reopened == stale_analysis
    assert v2_analysis_reopened['data']['input_provenance'] == expected_v2_provenance
    assert retry_analysis_reopened['data']['input_provenance'] == expected_retry_provenance
    assert v1_file_after_replacement.status_code == 200
    assert v1_file_after_replacement.content == v2_bytes


def test_paired_analysis_is_stale_if_source_is_replaced_before_publication(tmp_path):
    directory = tmp_path / 'paired-analysis-publication-race'
    directory.mkdir()
    h = Harness(directory)
    try:
        h.start_api()
        project = h.request('POST', '/api/projects', json={
            'name': 'Paired publication race ' + str(uuid.uuid4())[:8],
            'mode': 'manual',
            'goal': 'Keep paired results stale when their source is replaced in flight.',
            'budget': {'max_runs': 20, 'seconds': 180, 'allow_paid': False},
        })
        source_path = 'uploads/paired.csv'
        v1_bytes = b'unit,baseline,candidate\nu1,10,8\nu2,20,17\n'
        v2_bytes = b'unit,baseline,candidate\nu1,10,13\nu2,20,24\n'

        def upload(content):
            response = h.client.post(
                f"/api/projects/{project['id']}/upload",
                params={'directory': 'uploads'},
                files={'file': ('paired.csv', content, 'text/csv')})
            assert response.status_code == 200, response.text
            return response.json()

        upload(v1_bytes)
        queued = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
            'path': source_path, 'unit_column': 'unit', 'baseline_column': 'baseline',
            'candidate_column': 'candidate', 'direction': 'lower', 'bootstrap_samples': 100,
        })

        gate = directory / 'paired-gate'
        gate.mkdir()
        h.env['FOREST_TEST_PAIRED_GATE_DIR'] = str(gate)
        executor = r'''
import os, sys, time
from pathlib import Path
from research.validation import statistics
gate = Path(os.environ['FOREST_TEST_PAIRED_GATE_DIR'])
original = statistics.paired_csv
def gated(*args, **kwargs):
    (gate / 'entered').write_text('copied')
    deadline = time.monotonic() + 30
    while not (gate / 'release').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('test did not release paired analysis')
        time.sleep(0.01)
    return original(*args, **kwargs)
statistics.paired_csv = gated
from services.worker.execute import main
sys.argv = ['forest-paired-race-test', '--run-id', sys.argv[1]]
main()
'''
        child = h.spawn('paired-executor', [sys.executable, '-c', executor, queued['id']])
        wait_until(lambda: (gate / 'entered').is_file())

        replacement = upload(v2_bytes)
        assert replacement['path'] == source_path
        current_revision = h.request('GET', f"/api/projects/{project['id']}/file",
                                     params={'path': source_path})
        assert current_revision['revision'] > 1
        (gate / 'release').write_text('continue')
        child.wait(timeout=30)
        assert child.returncode == 0, (directory / 'paired-executor.log').read_text()

        analyses = h.request('GET', '/api/analyses', params={'project_id': project['id']})
        assert len(analyses) == 1, analyses
        analysis = analyses[0]
        assert analysis['status'] == 'needs_update', analysis
        assert analysis['data']['input_provenance']['sha256'] == hashlib.sha256(v1_bytes).hexdigest()
        assert analysis['data']['input_provenance']['size_bytes'] == len(v1_bytes)
        assert analysis['data']['stale_reason'] == 'Bound upstream material changed: ' + source_path
        assert any(item['source_id'] == source_path for item in analysis['data']['stale_dependencies'])
        retained = h.client.get(f"/api/projects/{project['id']}/download",
                                params={'path': analysis['data']['path']})
        assert retained.status_code == 200 and retained.content == v1_bytes
        current = h.client.get(f"/api/projects/{project['id']}/download",
                               params={'path': source_path})
        assert current.status_code == 200 and current.content == v2_bytes
    finally:
        h.cleanup()


def test_paired_analysis_is_stale_if_source_is_deleted_and_recreated_before_publication(tmp_path):
    directory = tmp_path / 'paired-analysis-delete-recreate-race'
    directory.mkdir()
    h = Harness(directory)
    try:
        h.start_api()
        project = h.request('POST', '/api/projects', json={
            'name': 'Paired delete recreate race ' + str(uuid.uuid4())[:8],
            'mode': 'manual',
            'goal': 'Keep paired results stale when their source is deleted and recreated in flight.',
            'budget': {'max_runs': 20, 'seconds': 180, 'allow_paid': False},
        })
        source_path = 'uploads/paired.csv'
        v1_bytes = b'unit,baseline,candidate\nu1,10,8\nu2,20,17\n'
        v2_bytes = b'unit,baseline,candidate\nu1,10,13\nu2,20,24\n'

        def upload(content):
            response = h.client.post(
                f"/api/projects/{project['id']}/upload",
                params={'directory': 'uploads'},
                files={'file': ('paired.csv', content, 'text/csv')})
            assert response.status_code == 200, response.text
            return response.json()

        upload(v1_bytes)
        queued = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
            'path': source_path, 'unit_column': 'unit', 'baseline_column': 'baseline',
            'candidate_column': 'candidate', 'direction': 'lower', 'bootstrap_samples': 100,
        })

        gate = directory / 'paired-gate'
        gate.mkdir()
        h.env['FOREST_TEST_PAIRED_GATE_DIR'] = str(gate)
        executor = r'''
import os, sys, time
from pathlib import Path
gate = Path(os.environ['FOREST_TEST_PAIRED_GATE_DIR'])
from research.validation import statistics
original = statistics.paired_csv
def gated(*args, **kwargs):
    (gate / 'entered').write_text('copied')
    deadline = time.monotonic() + 30
    while not (gate / 'release').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('test did not release paired analysis')
        time.sleep(0.01)
    return original(*args, **kwargs)
statistics.paired_csv = gated
from services.worker.execute import main
sys.argv = ['forest-paired-delete-recreate-test', '--run-id', sys.argv[1]]
main()
'''
        child = h.spawn('paired-executor', [sys.executable, '-c', executor, queued['id']])
        wait_until(lambda: (gate / 'entered').is_file())

        deleted = h.client.delete(
            f"/api/projects/{project['id']}/file", params={'path': source_path})
        assert deleted.status_code == 200, deleted.text
        replacement = upload(v2_bytes)
        assert replacement['path'] == source_path
        current_revision = h.request('GET', f"/api/projects/{project['id']}/file",
                                     params={'path': source_path})
        assert current_revision['revision'] == 3
        (gate / 'release').write_text('continue')
        child.wait(timeout=30)
        assert child.returncode == 0, (directory / 'paired-executor.log').read_text()

        analyses = h.request('GET', '/api/analyses', params={'project_id': project['id']})
        assert len(analyses) == 1, analyses
        analysis = analyses[0]
        assert analysis['status'] == 'needs_update', analysis
        assert analysis['data']['input_provenance']['sha256'] == hashlib.sha256(v1_bytes).hexdigest()
        assert analysis['data']['input_provenance']['size_bytes'] == len(v1_bytes)
        assert analysis['data']['stale_reason'] == 'Bound upstream material changed: ' + source_path
        assert any(item['source_id'] == source_path for item in analysis['data']['stale_dependencies'])
        retained = h.client.get(f"/api/projects/{project['id']}/download",
                                params={'path': analysis['data']['path']})
        assert retained.status_code == 200 and retained.content == v1_bytes
        current = h.client.get(f"/api/projects/{project['id']}/download",
                               params={'path': source_path})
        assert current.status_code == 200 and current.content == v2_bytes
    finally:
        h.cleanup()


def test_paired_analysis_remains_current_if_replacement_precedes_source_copy(tmp_path):
    directory = tmp_path / 'paired-analysis-replacement-before-copy'
    directory.mkdir()
    h = Harness(directory)
    try:
        h.start_api()
        project = h.request('POST', '/api/projects', json={
            'name': 'Paired replacement before copy ' + str(uuid.uuid4())[:8],
            'mode': 'manual',
            'goal': 'Keep results current when the worker copies the latest source version.',
            'budget': {'max_runs': 20, 'seconds': 180, 'allow_paid': False},
        })
        source_path = 'uploads/paired.csv'
        v1_bytes = b'unit,baseline,candidate\nu1,10,8\nu2,20,17\n'
        v2_bytes = b'unit,baseline,candidate\nu1,10,13\nu2,20,24\n'

        def upload(content):
            response = h.client.post(
                f"/api/projects/{project['id']}/upload",
                params={'directory': 'uploads'},
                files={'file': ('paired.csv', content, 'text/csv')})
            assert response.status_code == 200, response.text
            return response.json()

        upload(v1_bytes)
        queued = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
            'path': source_path, 'unit_column': 'unit', 'baseline_column': 'baseline',
            'candidate_column': 'candidate', 'direction': 'lower', 'bootstrap_samples': 100,
        })

        gate = directory / 'paired-gate'
        gate.mkdir()
        h.env['FOREST_TEST_PAIRED_GATE_DIR'] = str(gate)
        h.env['FOREST_TEST_PAIRED_SOURCE_PATH'] = str(
            directory / 'data' / 'projects' / project['id'] / source_path)
        executor = r'''
import os, sys, time
from pathlib import Path
gate = Path(os.environ['FOREST_TEST_PAIRED_GATE_DIR'])
source = Path(os.environ['FOREST_TEST_PAIRED_SOURCE_PATH']).resolve()
original_open = Path.open
paused = False
def gated_open(self, *args, **kwargs):
    global paused
    if self.resolve() == source and args and args[0] == 'rb' and not paused:
        paused = True
        (gate / 'entered').write_text('before-source-copy')
        deadline = time.monotonic() + 30
        while not (gate / 'release').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('test did not release paired source copy')
            time.sleep(0.01)
    return original_open(self, *args, **kwargs)
Path.open = gated_open
from services.worker.execute import main
sys.argv = ['forest-paired-before-copy-test', '--run-id', sys.argv[1]]
main()
'''
        child = h.spawn('paired-executor', [sys.executable, '-c', executor, queued['id']])
        wait_until(lambda: (gate / 'entered').is_file())

        replacement = upload(v2_bytes)
        assert replacement['path'] == source_path
        current_revision = h.request('GET', f"/api/projects/{project['id']}/file",
                                     params={'path': source_path})
        assert current_revision['revision'] > 1
        (gate / 'release').write_text('continue')
        child.wait(timeout=30)
        assert child.returncode == 0, (directory / 'paired-executor.log').read_text()

        analyses = h.request('GET', '/api/analyses', params={'project_id': project['id']})
        assert len(analyses) == 1, analyses
        analysis = analyses[0]
        assert analysis['status'] == 'ready_for_review', analysis
        assert 'stale_reason' not in analysis['data'], analysis['data']
        assert analysis['data']['input_provenance']['sha256'] == hashlib.sha256(v2_bytes).hexdigest()
        assert analysis['data']['input_provenance']['size_bytes'] == len(v2_bytes)
        retained = h.client.get(f"/api/projects/{project['id']}/download",
                                params={'path': analysis['data']['path']})
        assert retained.status_code == 200 and retained.content == v2_bytes
        current = h.client.get(f"/api/projects/{project['id']}/download",
                               params={'path': source_path})
        assert current.status_code == 200 and current.content == v2_bytes
    finally:
        h.cleanup()


def test_paired_analysis_waits_for_failed_publication_file_restore(tmp_path):
    directory = tmp_path / 'paired-analysis-file-restore-race'
    directory.mkdir()
    h = Harness(directory)
    original_data_dir = None
    try:
        h.start_api()
        project = h.request('POST', '/api/projects', json={
            'name': 'Paired file restore race ' + str(uuid.uuid4())[:8],
            'mode': 'manual',
            'goal': 'Do not publish an analysis from transient bytes during file rollback.',
            'budget': {'max_runs': 20, 'seconds': 180, 'allow_paid': False},
        })
        source_path = 'uploads/paired.csv'
        v1_bytes = b'unit,baseline,candidate\nu1,10,8\nu2,20,17\n'
        transient_bytes = b'unit,baseline,candidate\nu1,10,13\nu2,20,24\n'

        def upload(content):
            response = h.client.post(
                f"/api/projects/{project['id']}/upload",
                params={'directory': 'uploads'},
                files={'file': ('paired.csv', content, 'text/csv')})
            assert response.status_code == 200, response.text
            return response.json()

        upload(v1_bytes)
        queued = h.request('POST', f"/api/projects/{project['id']}/statistics/paired", json={
            'path': source_path, 'unit_column': 'unit', 'baseline_column': 'baseline',
            'candidate_column': 'candidate', 'direction': 'lower', 'bootstrap_samples': 100,
        })

        gate = directory / 'paired-gate'
        gate.mkdir()
        source_file = directory / 'data' / 'projects' / project['id'] / source_path
        h.env['FOREST_TEST_PAIRED_GATE_DIR'] = str(gate)
        h.env['FOREST_TEST_PAIRED_SOURCE_PATH'] = str(source_file)
        executor = r'''
import os, sys, time
from contextlib import contextmanager
from pathlib import Path
gate = Path(os.environ['FOREST_TEST_PAIRED_GATE_DIR'])
source = Path(os.environ['FOREST_TEST_PAIRED_SOURCE_PATH']).resolve()
original_open = Path.open
def gated_open(self, *args, **kwargs):
    if self.resolve() == source and args and args[0] == 'rb' and not (gate / 'source_opened').exists():
        (gate / 'source_opened').write_text('reading transient publication bytes')
        deadline = time.monotonic() + 30
        while not (gate / 'copy_release').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('test did not release paired source copy')
            time.sleep(0.01)
    return original_open(self, *args, **kwargs)
Path.open = gated_open
from research.validation import statistics
original_statistics = statistics.paired_csv
def gated_statistics(*args, **kwargs):
    (gate / 'compute_entered').write_text('source copied')
    deadline = time.monotonic() + 30
    while not (gate / 'compute_release').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('test did not release paired computation')
        time.sleep(0.01)
    return original_statistics(*args, **kwargs)
statistics.paired_csv = gated_statistics
import services.api.files as file_api
real_guard = file_api.file_publication_lock
@contextmanager
def tracked_guard(project_id, relative):
    (gate / 'publication_lock_attempted').write_text(relative)
    with real_guard(project_id, relative):
        yield
file_api.file_publication_lock = tracked_guard
from services.worker.execute import main
sys.argv = ['forest-paired-file-restore-test', '--run-id', sys.argv[1]]
main()
'''
        child = h.spawn('paired-executor', [sys.executable, '-c', executor, queued['id']])
        wait_until(lambda: (gate / 'source_opened').is_file())

        from services.api.config import settings
        from services.api.files import file_publication_lock
        original_data_dir = settings.data_dir
        settings.data_dir = (directory / 'data').resolve()

        def replace_source(content):
            temporary = source_file.with_name('.paired-rollback-test.tmp')
            temporary.write_bytes(content)
            temporary.replace(source_file)

        try:
            with file_publication_lock(project['id'], source_path):
                # Model _publish_file's transient replace while its file lock
                # is held, followed by rollback compensation restoring V1.
                replace_source(transient_bytes)
                (gate / 'copy_release').write_text('continue')
                wait_until(lambda: (gate / 'compute_entered').is_file())
                (gate / 'compute_release').write_text('continue')
                wait_until(lambda: (gate / 'publication_lock_attempted').is_file())
                assert child.poll() is None
                assert h.request('GET', '/api/analyses',
                                 params={'project_id': project['id']}) == []
                replace_source(v1_bytes)
        finally:
            settings.data_dir = original_data_dir

        child.wait(timeout=30)
        assert child.returncode == 0, (directory / 'paired-executor.log').read_text()
        analyses = h.request('GET', '/api/analyses', params={'project_id': project['id']})
        assert len(analyses) == 1, analyses
        analysis = analyses[0]
        assert analysis['status'] == 'needs_update', analysis
        assert analysis['data']['input_provenance']['sha256'] == hashlib.sha256(transient_bytes).hexdigest()
        assert analysis['data']['input_provenance']['size_bytes'] == len(transient_bytes)
        assert analysis['data']['stale_reason'] == 'Bound upstream material changed: ' + source_path
        retained = h.client.get(f"/api/projects/{project['id']}/download",
                                params={'path': analysis['data']['path']})
        assert retained.status_code == 200 and retained.content == transient_bytes
        current = h.client.get(f"/api/projects/{project['id']}/download",
                               params={'path': source_path})
        assert current.status_code == 200 and current.content == v1_bytes
    finally:
        if original_data_dir is not None:
            from services.api.config import settings
            settings.data_dir = original_data_dir
        h.cleanup()
