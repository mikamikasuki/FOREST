"""Actual editable route records and scoped numerical handoffs.

HTTP tests execute real arithmetic commands through the durable worker. The
transition tests exercise a proposal contract; they do not simulate a provider
or claim that a scientific direction review has been executed.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from research.planning.route_review import route_review_transition
from test_worker import Harness, wait_until


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def route_worker(tmp_path_factory):
    harness = Harness(tmp_path_factory.mktemp('actual-research-route'))
    try:
        harness.start_api()
        harness.start_worker()
        yield harness
    finally:
        harness.cleanup()


def add_node(harness, project, **fields):
    graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
    identifier = str(uuid.uuid4())
    result = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
        'expected_revision': graph['revision'], 'request_id': str(uuid.uuid4()),
        'operation': 'add_node', 'params': {'id': identifier,
            'branch_id': graph['branches'][0]['id'], **fields}})
    return next(node for node in result['graph']['nodes'] if node['id'] == identifier)


def arithmetic_command(n, *, independent=False, secondary=True):
    # Two actual statistics of the same finite integer sequence. Checking one
    # statistic cannot establish the other statistic's numerical correctness.
    primary = f'{n}*({n}+1)*(2*{n}+1)//6' if independent else f'sum(i*i for i in range(1,{n}+1))'
    cubic = f'({n}*({n}+1)//2)**2' if independent else f'sum(i**3 for i in range(1,{n}+1))'
    values = "{'primary':" + primary + (",'secondary':" + cubic if secondary else '') + '}'
    return [sys.executable, '-c', "import json;from pathlib import Path;"
            f"Path('metrics.json').write_text(json.dumps({values}))"]


def wait_for_block(harness, run):
    observed = wait_until(lambda: (current if (current := harness.run(run))['status']
        in ('waiting_input', 'failed', 'completed') else None))
    assert observed['status'] == 'waiting_input', observed
    assert observed['resource']['blocked_reason'] == 'verification_scope', observed
    return observed


def test_actual_partial_rerun_cannot_authorize_manuscript_or_full_json_input(route_worker):
    h = route_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Two actual integer-sequence statistics', 'mode': 'manual',
        'goal': 'Check independent finite-sequence arithmetic before using numerical evidence.',
        'budget': {'max_runs': 12, 'seconds': 120, 'allow_paid': False},
        'config': {'verification_policy': 'required', 'publication_profile': {'id': 'operational'}}})
    producer = add_node(h, project, type='experiment', title='Compute both statistics',
        config={'kind': 'command', 'command': arithmetic_command(13)})
    verifier = add_node(h, project, type='verification', title='Recompute only squares',
        config={'kind': 'verification', 'command': arithmetic_command(13, independent=True, secondary=False),
            'verification': {'producer_node_id': producer['id'], 'checks': [{
                'id': 'squares', 'kind': 'numeric_compare', 'source': 'metrics.json',
                'repeat': 'metrics.json', 'pointers': ['/primary'],
                'absolute_tolerance': 0, 'relative_tolerance': 0}]}})
    produced = h.launch(producer)
    finished = h.terminal(produced)
    assert finished['status'] == 'completed', finished
    assert {name: finished['metrics'][name] for name in ('primary', 'secondary')} == {
        'primary': sum(i*i for i in range(1, 14)), 'secondary': sum(i**3 for i in range(1, 14))}
    checked = h.launch(verifier)
    assert h.terminal(checked)['status'] == 'completed'
    verdict = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert verdict['verification_status'] == 'accepted', verdict
    assert verdict['numerical_scope'][0]['pointers'] == ['/primary']
    manuscript = h.request('POST', f"/api/papers/{project['id']}/generate", json={
        'run_ids': [produced['id']], 'request_id': str(uuid.uuid4())})
    wait_for_block(h, manuscript)
    assert not (h.output(manuscript)/'paper.tex').exists()
    consumer = add_node(h, project, type='analysis', title='Consume the complete numerical artifact',
        config={'kind': 'command', 'required_verification': [verifier['id']],
            'command': [sys.executable, '-c', "import json;from pathlib import Path;"
                "values=json.loads(Path('observed.json').read_text());"
                "Path('metrics.json').write_text(json.dumps({'consumed_secondary':values['secondary']}))"]},
        inputs=[{'node_id': producer['id'], 'path': 'metrics.json', 'destination': 'observed.json',
            'verification_node_id': verifier['id']}])
    consumed = h.launch(consumer)
    wait_for_block(h, consumed)
    assert not (h.output(consumed)/'workspace'/'metrics.json').exists()
    for run in (manuscript, consumed):
        h.request('POST', f"/api/runs/{run['id']}/cancel", json={})


def test_producer_run_configuration_edit_invalidates_actual_accepted_evidence(route_worker):
    h = route_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Editable configuration provenance', 'mode': 'manual',
        'goal': 'Retain the executed method identity of two integer-sequence statistics.',
        'budget': {'max_runs': 8, 'seconds': 120, 'allow_paid': False},
        'config': {'verification_policy': 'required', 'publication_profile': {'id': 'operational'}}})
    original_command = arithmetic_command(17)
    producer = add_node(h, project, type='experiment', title='Original direct computation',
        config={'kind': 'command', 'command': original_command, 'seed': 0})
    verifier = add_node(h, project, type='verification', title='Complete independent arithmetic',
        config={'kind': 'verification', 'command': arithmetic_command(17, independent=True),
            'verification': {'producer_node_id': producer['id'], 'checks': [{
                'id': 'both-statistics', 'kind': 'numeric_compare', 'source': 'metrics.json',
                'repeat': 'metrics.json', 'absolute_tolerance': 0, 'relative_tolerance': 0}]}})
    produced = h.launch(producer)
    assert h.terminal(produced)['status'] == 'completed'
    checked = h.launch(verifier)
    assert h.terminal(checked)['status'] == 'completed'
    assert h.request('GET', f"/api/runs/{produced['id']}/verification")['verification_status'] == 'accepted'
    actual_metrics = (h.output(produced)/'workspace'/'metrics.json').read_text()
    # Editing a finished run is permitted, but the new command was never the
    # method that produced these otherwise unchanged actual measurements.
    h.request('PATCH', f"/api/runs/{produced['id']}/configuration", json={
        'command': arithmetic_command(19)})
    changed_command = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert changed_command['verification_status'] == 'inconclusive', changed_command
    # Scientific parameters need the same provenance protection as the argv.
    h.request('PATCH', f"/api/runs/{produced['id']}/configuration", json={
        'command': original_command, 'seed': 42})
    changed_seed = h.request('GET', f"/api/runs/{produced['id']}/verification")
    assert changed_seed['verification_status'] == 'inconclusive', changed_seed
    assert (h.output(produced)/'workspace'/'metrics.json').read_text() == actual_metrics
    manuscript = h.request('POST', f"/api/papers/{project['id']}/generate", json={
        'run_ids': [produced['id']], 'request_id': str(uuid.uuid4())})
    stopped = wait_until(lambda: (run if (run := h.run(manuscript))['status']
        in ('waiting_input', 'failed', 'completed') else None))
    assert stopped['status'] == 'waiting_input', stopped
    assert stopped['resource']['blocked_reason'].startswith('verification_'), stopped
    assert not (h.output(manuscript)/'paper.tex').exists()
    h.request('POST', f"/api/runs/{manuscript['id']}/cancel", json={})


@pytest.mark.parametrize('decision,current,status,replan,phase', [
    ('continue', True, 'running', False, 'EXECUTE'),
    ('replan', True, 'running', True, 'PLAN'),
    ('continue', False, 'running', True, 'PLAN'),
    ('external_input', True, 'waiting_input', True, 'ROUTE_REVIEW'),
])
def test_route_advice_transition_has_explicit_dispatch_meaning(decision, current, status, replan, phase):
    control = {'status': 'running', 'phase': 'ROUTE_REVIEW', 'cycles': 3,
        'replan_required': True, 'replan_reason': 'Periodic review pending',
        'route_review': {'consumed': True, 'covered_run_ids': ['actual-record-reference']}}
    original = deepcopy(control)
    proposal = {'decision': decision, 'summary': 'Current route judgment',
        'stop_node_ids': ['current-editable-node'],
        'external_input_request': 'Provide the missing actual held-out labels.'}
    result = route_review_transition(control, proposal, current)
    assert control == original
    assert result['status'] == status
    assert result['replan_required'] is replan
    assert result['phase'] == phase
    assert result['cycles'] == 3
    assert result['route_review'] == original['route_review']
    if decision == 'continue' and current:
        assert 'replan_reason' not in result
    if decision == 'external_input' and current:
        assert result['reason'] == proposal['external_input_request']
    if decision == 'replan' and current:
        assert result['route_replan_node_ids'] == proposal['stop_node_ids']


@pytest.mark.parametrize('kind', ['goal_drift', 'excessive_counterexample_checks'])
def test_declared_semantic_route_defect_requires_replanning(kind):
    from research.planning.route_review import validate_route_review
    from test_evidence_workflow import idea
    # Human-authored contract input, not an executed model review.
    recommendation = {**idea(), 'research_question': 'How does smoothness change midpoint accuracy?',
        'success_event': 'Lower absolute integration error at equal evaluations on the selected smooth integrand.'}
    context = {'coverage': {'node_ids': ['editable-method'], 'run_ids': [], 'resource_ids': []},
        'route_health': {'replan_required': False}}
    report = {'decision': 'continue', 'summary': 'The proposed task abandons the stated integration question.',
        'coverage': deepcopy(context['coverage']), 'findings': [{'kind': kind,
            'finding': 'Stop the unrelated task and evaluate the intended integrand.',
            'node_ids': ['editable-method'], 'run_ids': []}],
        'stop_node_ids': ['editable-method'], 'recommendation': recommendation}
    with pytest.raises(ValueError, match='route defects require a new direction'):
        validate_route_review(report, context)
    report['decision'] = 'replan'
    assert validate_route_review(report, context)['decision'] == 'replan'


def test_route_context_reads_complete_actual_records_and_files_and_budget(route_worker):
    h = route_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Complete editable-route context', 'mode': 'manual',
        'goal': 'Inspect the original integer-sequence implementation and every recorded result.',
        'budget': {'max_runs': 2, 'seconds': 60, 'allow_paid': False},
        'config': {'publication_profile': {'id': 'operational'}}})
    producer = add_node(h, project, type='experiment', title='Actual finite sum',
        config={'kind': 'command', 'command': arithmetic_command(10)})
    unused = add_node(h, project, type='analysis', title='Unevaluated scientific branch',
        config={'kind': 'analysis'})
    first = h.launch(producer)
    assert h.terminal(first)['status'] == 'completed'
    h.request('PATCH', f"/api/nodes/{producer['id']}", json={'config': {
        'kind': 'command', 'command': [sys.executable, '-c',
            "from pathlib import Path;print(Path('required_actual_input.csv').read_text())"]}})
    failed = h.launch(producer)
    assert h.terminal(failed)['status'] == 'failed'
    denied = h.client.post(f"/api/nodes/{producer['id']}/run", json={})
    assert denied.status_code == 409
    assert denied.json()['detail']['code'] == 'RUN_BUDGET_EXHAUSTED'
    # These are actual draft files in the editable branch, not generated agent
    # findings. A credential-shaped canary must never enter provider context.
    graph = h.request('GET', f"/api/projects/{project['id']}/graph")
    workspace = h.directory/'data'/'projects'/project['id']/graph['branches'][0]['workspace']
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace/'method.py').write_text('def score(n):\n    return sum(i*i for i in range(1, n+1))\n')
    (workspace/'secrets.json').write_text(json.dumps({'api_key': 'test-only-context-exclusion-canary'}))
    (workspace/'credentials.json').write_text(json.dumps({'password': 'test-only-context-exclusion-canary'}))
    (workspace/'task_config.json').write_text(json.dumps({'provider_snapshot': {
        'api_key': 'test-only-context-exclusion-canary'}}))
    inspected = subprocess.run([sys.executable, '-c', '''
import json,sys
from services.api.db import Session,Project,Node,TaskRun,Hypothesis,ResearchClaim,SourcePaper,DatasetAsset,ExperimentSpec,Analysis,Figure,PaperDocument,Review,Derivation
from research.planning.route_review import route_context
pid=sys.argv[1]
models=(Hypothesis,ResearchClaim,SourcePaper,DatasetAsset,ExperimentSpec,Analysis,Figure,PaperDocument,Review,Derivation)
with Session.begin() as session:
    project=session.get(Project,pid)
    project.config={**project.config,'provider_snapshot':{'api_key':'test-only-context-exclusion-canary'},'method':{'algorithm':'integer-direct-summation','parameters':{'n':10}}}
    node=session.get(Node,sys.argv[2].split(',')[0])
    node.config={**node.config,'env':{'DATASET_PATH':'test-only-context-exclusion-canary','TRAINING_SEED':'test-only-context-exclusion-canary'},'remote':{'hostname':'test-compute.example','password':'test-only-context-exclusion-canary'},'nested':{'provider':{'api_key':'test-only-context-exclusion-canary'},'method':{'statistical_unit':'integer-sequence'}}}
    for model in models:
        session.add(model(project_id=pid,title='Human-authored editable research record',data={'origin':'manual','scope':'context coverage contract'}))
with Session() as session:
    context=route_context(session,session.get(Project,pid))
    assert set(context['coverage']['node_ids'])==set(sys.argv[2].split(','))
    assert set(context['coverage']['run_ids'])==set(sys.argv[3].split(','))
    assert len(context['coverage']['resource_ids'])==len(models)
    assert {r['status'] for r in context['runs']}=={'completed','failed'}
    assert any(r['metrics'].get('primary')==385 for r in context['runs'])
    assert context['project']['budget']['max_runs']==2
    assert context['project']['budget']['allow_paid'] is False
    assert context['project']['config']['method']=={'algorithm':'integer-direct-summation','parameters':{'n':10}}
    node=next(row for row in context['graph']['nodes'] if row['id']==sys.argv[2].split(',')[0])
    assert node['config']['env']['variable_names']==['DATASET_PATH','TRAINING_SEED']
    assert node['config']['remote']['hostname']=='test-compute.example'
    assert 'password' not in node['config']['remote']
    assert node['config']['nested']['method']['statistical_unit']=='integer-sequence'
    materials={row['path']:row for row in context['materials']}
    assert any(path.endswith('/method.py') and 'sum(i*i' in row['content'] and row['complete'] for path,row in materials.items())
    assert not any(path.endswith(('/secrets.json','/credentials.json','/task_config.json')) for path in materials)
    assert 'test-only-context-exclusion-canary' not in json.dumps(context)
    print(json.dumps({'node_count':len(context['coverage']['node_ids']),'run_count':len(context['coverage']['run_ids']),'resource_count':len(context['coverage']['resource_ids'])}))
''', project['id'], ','.join((producer['id'], unused['id'])), ','.join((first['id'], failed['id']))],
        cwd=ROOT, env=h.env, capture_output=True, text=True, timeout=20)
    assert inspected.returncode == 0, inspected.stdout+inspected.stderr
    assert json.loads(inspected.stdout) == {'node_count': 2, 'run_count': 2, 'resource_count': 10}
