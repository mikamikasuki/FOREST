"""Public resume API budget edits, actual HTTP and SQLite, no model or worker."""
import json
import subprocess
import sys
import uuid

from tests.test_worker import Harness


def _agent_node(harness, *, timeout='omitted'):
    project = harness.request('POST', '/api/projects', json={
        'name': 'Time budget resume validation',
        'budget': {'allow_paid': False, 'max_runs': 20, 'seconds': 20},
    })
    graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
    node_id = str(uuid.uuid4())
    config = {'kind': 'agent', 'role': 'Researcher'}
    if timeout != 'omitted':
        config['timeout'] = timeout
    changed = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
        'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
        'operation': 'add_node', 'targets': [],
        'params': {'id': node_id, 'branch_id': graph['branches'][0]['id'],
                   'type': 'experiment', 'title': 'Budget resume task', 'config': config},
    })
    node = next(item for item in changed['graph']['nodes'] if item['id'] == node_id)
    run = harness.launch(node)
    return project, node, run


def _set_run_state(harness, run, *, status='budget_exhausted', elapsed, remove_time_budget=False):
    code = '''
from pathlib import Path
from services.api.db import Session,TaskRun
from services.api.common import project_dir
with Session.begin() as s:
 r=s.get(TaskRun,RUN)
 r.status=STATUS
 r.pid=None; r.process_created=None
 r.resource={**(r.resource or {}),'elapsed_seconds':ELAPSED}
 if REMOVE:
  resource=dict(r.resource);resource.pop('time_budget',None);r.resource=resource
 (project_dir(r.project_id)/r.output_path/'workspace').mkdir(parents=True,exist_ok=True)
'''.replace('RUN', repr(run['id'])).replace('STATUS', repr(status)).replace('ELAPSED', repr(float(elapsed))).replace('REMOVE', repr(remove_time_budget))
    result = subprocess.run([sys.executable, '-c', code], env=harness.env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def _increase_project_budget(harness, project, seconds):
    current = harness.request('GET', f"/api/projects/{project['id']}")
    budget = {**current['budget'], 'seconds': seconds}
    return harness.request('PATCH', f"/api/projects/{project['id']}",
                          json={'expected_revision': current['revision'], 'budget': budget})


def _edit_node(harness, node, **patch):
    graph = harness.request('GET', f"/api/projects/{node['project_id']}/graph")
    return harness.request('POST', f"/api/projects/{node['project_id']}/graph/commands", json={
        'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
        'operation': 'edit_node', 'targets': [node['id']], 'params': patch,
    })


def _add_other_run_usage(harness, project_id, elapsed):
    code = '''
from services.api.db import Session,TaskRun,uid
with Session.begin() as s:
 s.add(TaskRun(project_id=PROJECT,request_id=uid(),kind='command',status='completed',
               resource={'elapsed_seconds':ELAPSED}))
'''.replace('PROJECT', repr(project_id)).replace('ELAPSED', repr(float(elapsed)))
    result = subprocess.run([sys.executable, '-c', code], env=harness.env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def _add_legacy_non_node_run(harness, project_id, timeout, elapsed):
    run_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    code = '''
from services.api.db import Session,TaskRun
with Session.begin() as s:
 s.add(TaskRun(id=RUN_ID,project_id=PROJECT,request_id=REQUEST_ID,kind='command',status='failed',
               config={'timeout':TIMEOUT,'command':[PYTHON,'-c','pass']},
               resource={'elapsed_seconds':ELAPSED}))
'''.replace('RUN_ID', repr(run_id)).replace('PROJECT', repr(project_id)).replace('REQUEST_ID', repr(request_id)).replace('TIMEOUT', repr(float(timeout))).replace('PYTHON', repr(sys.executable)).replace('ELAPSED', repr(float(elapsed)))
    result = subprocess.run([sys.executable, '-c', code], env=harness.env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    return {'id': run_id}


def test_public_budget_resume_preserves_run_evidence_and_outer_limits(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project = harness.request('POST', '/api/projects', json={'name': 'Resume accounting validation', 'budget': {'allow_paid': True, 'cost_usd': 2}})
        code = '''
import json
from pathlib import Path
from services.api.db import Session,TaskRun,ModelRequest,Provider
from services.api.common import project_dir
with Session.begin() as s:
 p=Provider(name='No model calls',kind='openai',base_url='https://api.openai.com/v1',model='ledger-only',allow_paid=True,config={'budget_usd':10,'pricing':{'input_per_million':1,'output_per_million':1}})
 s.add(p);s.flush()
 r=TaskRun(project_id=PROJECT,request_id='budget-resume-test',kind='agent',status='budget_exhausted',config={'agent_budget':{'cost':.8,'steps':90},'instructions':'Retain prior evidence.'})
 s.add(r);s.flush();r.output_path='runs/'+r.id
 s.add(ModelRequest(provider_id=p.id,project_id=PROJECT,run_id=r.id,model='ledger-only',status='settled',estimated_microusd=540000,reserved_microusd=600000,details={'usage':{'cost':.54}}))
 workspace=project_dir(PROJECT)/r.output_path/'workspace';workspace.mkdir(parents=True)
 (workspace/'agent_session.json').write_text(json.dumps({'run_id':r.id,'transcript':[{'step':1,'observation':'actual previous accounting input'}],'totals':{'cost':.54}}))
 print(json.dumps({'id':r.id,'provider_id':p.id,'workspace':str(workspace)}))
'''.replace('PROJECT', repr(project['id']))
        seeded = subprocess.run([sys.executable, '-c', code], env=harness.env, cwd=tmp_path,
                                capture_output=True, text=True, timeout=15)
        assert seeded.returncode == 0, seeded.stdout + seeded.stderr
        run = json.loads(seeded.stdout)
        from pathlib import Path
        session = Path(run['workspace']) / 'agent_session.json'
        previous = session.read_bytes()
        endpoint = f"/api/runs/{run['id']}/resume"
        for update in ({'cost': -1}, {'cost': True}, {'steps': 2.5}, {'unknown': 2}, {}):
            response = harness.client.post(endpoint, json={'agent_budget': update})
            assert response.status_code == 422
            unchanged = harness.request('GET', f"/api/runs/{run['id']}")
            assert unchanged['status'] == 'budget_exhausted'
            assert unchanged['config']['agent_budget'] == {'cost': .8, 'steps': 90}
        resumed = harness.request('POST', endpoint, json={'agent_budget': {'cost': 1.1}})
        assert resumed['id'] == run['id'] and resumed['status'] == 'queued'
        assert resumed['config']['agent_budget'] == {'cost': 1.1, 'steps': 90}
        assert resumed['config']['_next_attempt']['mode'] == 'continue'
        change = resumed['config']['agent_budget_changes'][-1]
        assert change['previous']['cost'] == .8 and change['updated']['cost'] == 1.1
        assert session.read_bytes() == previous
        verify = '''
import json
from sqlalchemy import select
from services.api.db import Session,TaskRun,ModelRequest,Provider,Project,Event
from research.agents.budget import make_request_guard,BudgetExceeded
with Session() as s:
 r=s.get(TaskRun,RUN);p=s.get(Provider,PROVIDER);project=s.get(Project,PROJECT)
 rows=list(s.scalars(select(ModelRequest).where(ModelRequest.run_id==RUN)))
 assert len(rows)==1 and rows[0].estimated_microusd==540000
 assert p.config['budget_usd']==10 and project.budget['cost_usd']==2
 assert len(list(s.scalars(select(Event).where(Event.project_id==PROJECT,Event.type=='agent_budget_changed'))))==1
 print('Existing usage, provider/project caps and explicit budget edit event preserved')
'''.replace('RUN', repr(run['id'])).replace('PROVIDER', repr(run['provider_id'])).replace('PROJECT', repr(project['id']))
        checked = subprocess.run([sys.executable, '-c', verify], env=harness.env, cwd=tmp_path,
                                 capture_output=True, text=True, timeout=15)
        assert checked.returncode == 0, checked.stdout + checked.stderr
    finally:
        harness.cleanup()


def test_resume_recalculates_legacy_project_timeout_after_budget_increase(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness)
        assert run['config']['timeout'] == 20
        assert run['resource']['time_budget']['requested_task_timeout_seconds'] is None
        _set_run_state(harness, run, elapsed=20.38, remove_time_budget=True)
        _increase_project_budget(harness, project, 40)

        resumed = harness.request('POST', f"/api/runs/{run['id']}/resume", json={})
        assert resumed['status'] == 'queued'
        assert resumed['config']['timeout'] == 40
        assert resumed['resource']['elapsed_seconds'] == 20.38
        policy = resumed['resource']['time_budget']
        assert policy['requested_task_timeout_seconds'] is None
        assert policy['effective_total_timeout_seconds'] == 40
        assert policy['resume_history'][-1]['requested_timeout_source'] == 'unchanged_node_revision'
        assert policy['resume_history'][-1]['run_elapsed_seconds'] == 20.38
        assert resumed['config']['_next_attempt']['mode'] == 'continue'
    finally:
        harness.cleanup()


def test_resume_recovers_legacy_timeout_from_historical_node_revision(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness)
        _set_run_state(harness, run, elapsed=20.1, remove_time_budget=True)
        edited = _edit_node(harness, node, instructions='Updated after the run was queued')
        current_node = next(item for item in edited['graph']['nodes'] if item['id'] == node['id'])
        assert current_node['revision'] != run['node_revision']
        _increase_project_budget(harness, project, 40)

        resumed = harness.request('POST', f"/api/runs/{run['id']}/resume", json={})
        assert resumed['config']['timeout'] == 40
        assert resumed['resource']['time_budget']['requested_task_timeout_seconds'] is None
        assert resumed['resource']['time_budget']['resume_history'][-1]['requested_timeout_source'] == 'historical_node_revision'
    finally:
        harness.cleanup()


def test_retry_recovers_legacy_explicit_cap_from_historical_node_revision(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness, timeout=30)
        _set_run_state(harness, run, status='failed', elapsed=10, remove_time_budget=True)
        edited = _edit_node(harness, node, instructions='Updated after the run was queued')
        current_node = next(item for item in edited['graph']['nodes'] if item['id'] == node['id'])
        assert current_node['revision'] != run['node_revision']
        _increase_project_budget(harness, project, 40)

        retried = harness.request('POST', f"/api/runs/{run['id']}/retry", json={
            'request_id': str(uuid.uuid4()),
        })
        assert retried['config']['timeout'] == 30
        assert retried['resource']['time_budget']['requested_task_timeout_seconds'] == 30
    finally:
        harness.cleanup()


def test_retry_ignores_recreated_node_with_same_id_and_revision(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness, timeout=30)
        _set_run_state(harness, run, status='failed', elapsed=10, remove_time_budget=True)
        graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        original_node = next(item for item in graph['nodes'] if item['id'] == node['id'])
        deleted = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
            'operation': 'delete_node', 'targets': [node['id']], 'params': {},
        })
        recreated = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': str(uuid.uuid4()), 'expected_revision': deleted['graph']['revision'],
            'operation': 'add_node', 'targets': [],
            'params': {'id': node['id'], 'branch_id': node['branch_id'],
                       'type': 'experiment', 'title': 'Recreated budget task',
                       'config': {'kind': 'agent', 'role': 'Researcher', 'timeout': 60}},
        })
        current_graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        replacement = next(item for item in current_graph['nodes'] if item['id'] == node['id'])
        assert replacement['revision'] == run['node_revision']
        assert replacement['created_at'] > original_node['created_at']
        _increase_project_budget(harness, project, 100)

        retried = harness.request('POST', f"/api/runs/{run['id']}/retry", json={
            'request_id': str(uuid.uuid4()),
        })
        assert retried['config']['timeout'] == 30
        assert retried['resource']['time_budget']['requested_task_timeout_seconds'] == 30
    finally:
        harness.cleanup()


def test_resume_preserves_explicit_task_timeout_when_project_budget_grows(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness, timeout=30)
        policy = run['resource']['time_budget']
        assert policy['requested_task_timeout_seconds'] == 30
        assert policy['effective_total_timeout_seconds'] == 20
        _set_run_state(harness, run, elapsed=10.5)
        _increase_project_budget(harness, project, 40)

        resumed = harness.request('POST', f"/api/runs/{run['id']}/resume", json={})
        assert resumed['config']['timeout'] == 30
        assert resumed['resource']['elapsed_seconds'] == 10.5
        assert resumed['resource']['time_budget']['effective_total_timeout_seconds'] == 30
        assert resumed['resource']['time_budget']['requested_task_timeout_seconds'] == 30
    finally:
        harness.cleanup()


def test_resume_reserves_time_already_used_by_other_project_runs(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness)
        _set_run_state(harness, run, elapsed=10)
        _add_other_run_usage(harness, project['id'], 12)
        _increase_project_budget(harness, project, 40)

        resumed = harness.request('POST', f"/api/runs/{run['id']}/resume", json={})
        assert resumed['config']['timeout'] == 28
        assert resumed['resource']['elapsed_seconds'] == 10
        record = resumed['resource']['time_budget']['resume_history'][-1]
        assert record['other_run_elapsed_seconds'] == 12
        assert record['run_elapsed_seconds'] == 10
        assert record['effective_total_timeout_seconds'] == 28
    finally:
        harness.cleanup()


def test_resume_rejects_run_with_no_remaining_project_time(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness)
        _set_run_state(harness, run, elapsed=20.1)
        before = harness.run(run)

        response = harness.client.post(f"/api/runs/{run['id']}/resume", json={})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'TIME_BUDGET_EXHAUSTED'
        after = harness.run(run)
        assert after['status'] == 'budget_exhausted'
        assert after['resource'] == before['resource']
        assert after['config']['timeout'] == before['config']['timeout']
    finally:
        harness.cleanup()


def test_retry_uses_original_task_cap_not_project_derived_timeout(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project, node, run = _agent_node(harness, timeout=30)
        _set_run_state(harness, run, status='failed', elapsed=10)
        _increase_project_budget(harness, project, 40)

        retried = harness.request('POST', f"/api/runs/{run['id']}/retry", json={
            'request_id': str(uuid.uuid4()),
        })
        assert retried['id'] != run['id']
        assert retried['config']['timeout'] == 30
        assert retried['resource']['time_budget']['requested_task_timeout_seconds'] == 30
        assert retried['resource']['time_budget']['effective_total_timeout_seconds'] == 30
    finally:
        harness.cleanup()


def test_retry_does_not_promote_legacy_non_node_project_timeout(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project = harness.request('POST', '/api/projects', json={
            'name': 'Legacy non-node retry budget',
            'budget': {'allow_paid': False, 'max_runs': 20, 'seconds': 20},
        })
        run = _add_legacy_non_node_run(harness, project['id'], timeout=20, elapsed=10)
        _increase_project_budget(harness, project, 40)

        retried = harness.request('POST', f"/api/runs/{run['id']}/retry", json={
            'request_id': str(uuid.uuid4()),
        })
        assert retried['id'] != run['id']
        assert retried['config']['timeout'] == 30
        policy = retried['resource']['time_budget']
        assert policy['requested_task_timeout_seconds'] is None
        assert policy['effective_total_timeout_seconds'] == 30
    finally:
        harness.cleanup()
