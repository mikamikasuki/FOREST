"""Actual API/worker/restart chains using a local deterministic model transport.

These tests establish control semantics and exact request delivery, not model
quality. Live-provider and executor qualification is recorded separately.
"""
import json
import threading
import uuid
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from tests.test_worker import Harness, wait_until
from tests.test_research_verification_workflow import add_node, arithmetic_command, verification_config


@pytest.fixture(params=['sqlite', pytest.param('postgres', marks=pytest.mark.skipif(
    not os.environ.get('FOREST_TEST_POSTGRES'), reason='Set FOREST_TEST_POSTGRES for a dedicated temporary database'))])
def intervention_harness(tmp_path, request):
    h = Harness(tmp_path)
    database = None
    try:
        if request.param == 'postgres':
            from tests.test_postgres import _postgres_parameters
            env, flags, authority = _postgres_parameters()
            database = 'forest_test_' + uuid.uuid4().hex
            subprocess.run([shutil.which('createdb'), *flags, database], env=env, check=True,
                           capture_output=True, text=True, timeout=20)
            h.env['FOREST_DATABASE_URL'] = f'postgresql+psycopg://{authority}/{database}'
        yield h
    finally:
        h.cleanup()
        if database:
            subprocess.run([shutil.which('dropdb'), *flags, '--force', database], env=env, check=True,
                           capture_output=True, text=True, timeout=20)


def test_pause_does_not_suspend_executor_with_database_transaction(intervention_harness):
    """A real descendant holds an actual transaction while its owner pauses."""
    import sys
    import time
    from pathlib import Path
    h = intervention_harness
    h.start_api(); h.start_worker()
    auxiliary = h.request('POST', '/api/projects', json={'name': 'Independent transaction target', 'mode': 'manual'})
    p = h.request('POST', '/api/projects', json={'name': 'Safe transactional pause', 'mode': 'manual',
        'budget': {'max_runs': 4, 'seconds': 120, 'allow_paid': False}})
    code = '''import time
from pathlib import Path
from services.api.db import Session, Project, begin_sqlite_write
with Session.begin() as s:
 begin_sqlite_write(s)
 p=s.get(Project, AUXILIARY, with_for_update=True)
 p.description='Actual committed transaction'
 s.flush()
 Path('transaction-open.txt').write_text('open')
 time.sleep(1.5)
Path('transaction-committed.txt').write_text('committed')
for i in range(40):
 Path('heartbeat.txt').write_text(str(i)); time.sleep(.1)
'''.replace('AUXILIARY', repr(auxiliary['id']))
    n = add_node(h, p, type='implementation', config={'kind': 'command', 'command': [sys.executable, '-c', code], 'timeout': 60})
    run = h.launch(n); h.running(run)
    workspace = h.output(run) / 'workspace'
    wait_until((workspace / 'transaction-open.txt').exists)
    paused = h.control_request('/api/runs/' + run['id'] + '/pause', json={})
    assert paused['status'] == 'paused'
    assert (workspace / 'transaction-committed.txt').read_text() == 'committed'
    # A stopped executor must not retain a DB lock required by another request.
    updated = h.request('PATCH', '/api/projects/' + auxiliary['id'], json={'description': 'Owner can still edit'})
    assert updated['description'] == 'Owner can still edit'
    if (workspace / 'heartbeat.txt').exists():
        before = (workspace / 'heartbeat.txt').read_text(); time.sleep(.3)
        assert (workspace / 'heartbeat.txt').read_text() == before
    resumed = h.control_request('/api/runs/' + run['id'] + '/resume', json={})
    assert resumed['id'] == run['id']
    assert h.terminal(run)['status'] == 'completed'


@pytest.mark.parametrize('case', ['live_instruction', 'revoke_tool', 'review_edit', 'review_reject', 'review_stale',
    'review_cancel_pending', 'review_cancel_accepted', 'review_cancel_rejected'])
def test_intervention_crosses_actual_worker_and_transport_boundaries(tmp_path, case, intervention_harness):
    first = threading.Event()
    release = threading.Event()
    captures = []

    class Transport(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            captures.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            stage = len(captures)
            if stage == 1:
                first.set()
                if case in ('live_instruction', 'revoke_tool'):
                    assert release.wait(25), 'Intervention did not release the in-flight request'
                action = {'tool': 'write_file', 'arguments': {'path': 'old.txt', 'content': 'old response'}}
            elif case == 'live_instruction' and stage == 2:
                action = {'tool': 'write_file', 'arguments': {'path': 'current.txt', 'content': 'corrected response'}}
            else:
                artifacts = ['edited.txt'] if case == 'review_edit' else ['current.txt'] if case == 'live_instruction' else []
                action = {'tool': 'finish', 'arguments': {'summary': 'Actual fixture action inspected', 'artifacts': artifacts}}
            body = json.dumps({'model': 'control-contract-fixture', 'message': {'content': json.dumps(action)},
                               'done': True, 'prompt_eval_count': 100, 'eval_count': 40}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('x-request-id', 'fixture-' + str(stage))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Transport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    h = intervention_harness
    h.env['FOREST_WORKER_CONCURRENCY'] = '1'
    try:
        h.start_api()
        provider = h.request('POST', '/api/providers', json={'name': 'Control contract fixture', 'kind': 'ollama',
            'base_url': f'http://127.0.0.1:{server.server_port}', 'model': 'control-contract-fixture', 'allow_paid': False})
        project = h.request('POST', '/api/projects', json={'name': 'Durable intervention chain', 'goal': 'ORIGINAL_GOAL',
            'config': {'provider_id': provider['id']}, 'budget': {'max_runs': 5, 'seconds': 120, 'allow_paid': False}})
        pid = project['id']
        graph = h.request('GET', f'/api/projects/{pid}/graph')
        nid = str(uuid.uuid4())
        config = {'kind': 'agent', 'tools': ['write_file', 'finish'], 'agent_budget': {'steps': 10}, 'timeout': 60}
        if case.startswith('review_'): config['human_review_tools'] = ['write_file']
        created = h.request('POST', f'/api/projects/{pid}/graph/commands', json={'request_id': str(uuid.uuid4()),
            'expected_revision': graph['revision'], 'operation': 'add_node', 'targets': [],
            'params': {'id': nid, 'type': 'analysis', 'title': 'Controlled actual agent', 'instructions': 'Use current controls', 'config': config}})
        node = next(n for n in created['graph']['nodes'] if n['id'] == nid)
        run = h.launch(node)
        h.start_worker()
        assert first.wait(20)
        instruction = None
        goal_change = None
        answer = None
        decision = None
        if case == 'live_instruction':
            project = h.request('GET', f'/api/projects/{pid}')
            project = h.request('PATCH', f'/api/projects/{pid}', json={'expected_revision': project['revision'], 'goal': 'CORRECTED_GOAL'})
            goal_change=project['intervention']
            assert goal_change['status']=='accepted'
            instruction = h.request('POST', f'/api/projects/{pid}/instructions', json={
                'request_id': str(uuid.uuid4()), 'expected_revision': project['revision'], 'text': 'OWNER_EXPLICIT_INSTRUCTION',
                'scope': 'run', 'target_id': run['id'], 'boundary': 'next_request'})
            assert instruction['status'] == 'accepted'
            assert all(e['status'] == 'pending' for e in instruction['effects'])
            release.set()
        elif case == 'revoke_tool':
            graph = h.request('GET', f'/api/projects/{pid}/graph')
            h.request('PATCH', f'/api/nodes/{nid}', json={'expected_revision': graph['revision'], 'config': {'tools': ['finish']}})
            release.set()
        else:
            waiting = wait_until(lambda: (r if (r := h.run(run))['status'] == 'waiting_input' else None), timeout=25)
            decision = h.request('GET', f'/api/projects/{pid}/decisions?status=pending')[0]
            assert waiting['resource']['wait_for']['decision_id'] == decision['id']
            assert not (h.output(run) / 'workspace' / 'old.txt').exists()
            h.stop(h.worker)
            h.stop(h.api)
            h.start_api()
            assert h.request('GET', f'/api/projects/{pid}/decisions?status=pending')[0]['proposed'] == decision['proposed']
            if case.startswith('review_cancel_'):
                choice = case.removeprefix('review_cancel_')
                if choice != 'pending':
                    h.request('POST', f"/api/decisions/{decision['id']}/answer", json={
                        'expected_revision': decision['observed_revision'],
                        'choice': 'accept' if choice == 'accepted' else 'reject', 'reason': 'Saved before cancellation'})
                stopped = h.control_request(f"/api/runs/{run['id']}/cancel", json={})
                assert stopped['status'] == 'cancelled'
                closed = h.request('GET', f'/api/projects/{pid}/decisions')[0]
                assert closed['id'] == decision['id']
                assert closed['status'] == ('rejected' if choice == 'rejected' else 'stale')
                if choice == 'rejected':
                    assert closed['consumed_at'] and 'executed_attempt_id' not in closed['answer']
                assert not (h.output(run) / 'workspace' / 'old.txt').exists()
                h.start_worker()
                assert h.request('GET', f'/api/projects/{pid}/decisions?status=pending') == []
                assert h.run(run)['status'] == 'cancelled'
                return
            if case == 'review_stale':
                graph = h.request('GET', f'/api/projects/{pid}/graph')
                h.request('PATCH', f'/api/nodes/{nid}', json={'expected_revision': graph['revision'], 'instructions': 'Owner changed task'})
                rejected = h.client.post(f"/api/decisions/{decision['id']}/answer", json={
                    'expected_revision': decision['observed_revision'], 'choice': 'accept', 'resume': True})
                assert rejected.status_code == 200
                assert rejected.json()['status'] == 'stale'
            else:
                answer = {'expected_revision': decision['observed_revision'], 'choice': 'edit' if case == 'review_edit' else 'reject',
                          'reason': 'Saved owner decision', 'resume': True}
                if case == 'review_edit': answer['action'] = {'tool': 'write_file', 'arguments': {'path': 'edited.txt', 'content': 'owner edited action'}}
                answered = h.request('POST', f"/api/decisions/{decision['id']}/answer", json=answer)
                assert not answered.get('resume_error'), answered
            h.start_worker()
        completed = h.terminal(run, timeout=35)
        assert completed['status'] == 'completed', completed
        workspace = h.output(completed) / 'workspace'
        assert not (workspace / 'old.txt').exists()
        if case == 'review_edit': assert (workspace / 'edited.txt').read_text() == 'owner edited action'
        if case == 'review_reject':
            final = h.request('GET', f'/api/projects/{pid}/decisions')[0]
            assert final['status'] == 'rejected' and final['consumed_at']
            assert final['answer']['choice'] == 'reject'
            assert 'executed_attempt_id' not in final['answer']
            repeated = h.request('POST', f"/api/decisions/{decision['id']}/answer", json=answer)
            assert repeated['consumed_at'] == final['consumed_at']

        if case == 'live_instruction':
            assert (workspace / 'current.txt').read_text() == 'corrected response'
            assert 'CORRECTED_GOAL' in json.dumps(captures[1])
            assert 'OWNER_EXPLICIT_INSTRUCTION' in json.dumps(captures[1])
            received = next(i for i in h.request('GET', f'/api/projects/{pid}/interventions') if i['id'] == instruction['id'])
            goal_receipt=h.request('GET','/api/interventions/'+goal_change['id'])
            assert goal_receipt['status']=='applied'
            proof=goal_receipt['effects'][0]['observation']
            assert proof['delivery']['fields']['goal']=='CORRECTED_GOAL' and proof['payload_sha256']
            assert received['status'] == 'applied'
            assert received['effects'][0]['observation']['request_context']['instruction_intervention_ids'] == [instruction['id']]
            assert received['effects'][0]['attempt_id'] == completed['config']['execution_attempt']['id']
        if decision:
            saved = next(d for d in h.request('GET', f'/api/projects/{pid}/decisions') if d['id'] == decision['id'])
            assert saved['status'] == {'review_edit': 'consumed', 'review_reject': 'rejected', 'review_stale': 'stale'}[case]
            if answer:
                repeated = h.request('POST', f"/api/decisions/{decision['id']}/answer", json=answer)
                assert repeated['status'] == saved['status']
                assert h.run(run)['status'] == 'completed'
        (tmp_path / 'captured_requests.json').write_text(json.dumps(captures, indent=2))
    finally:
        release.set()
        h.cleanup()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_prune_restore_holds_queued_runs_until_individually_resumed(intervention_harness):
    import time
    import sys
    h = intervention_harness
    h.env['FOREST_WORKER_CONCURRENCY'] = '1'
    h.start_api(); h.start_worker()
    project, first = h.project_node(seconds=6, timeout=20)
    queued_nodes = [add_node(h, project, type='implementation', title='Owner choice '+str(index),
        config={'kind':'command', 'command':[sys.executable, '-c',
            "import json;from pathlib import Path;Path('metrics.json').write_text(json.dumps({'value':1}))"], 'timeout':15}) for index in range(2)]
    running = h.launch(first); h.running(running)
    queued = [h.launch(node) for node in queued_nodes]
    graph = h.request('GET', '/api/projects/'+project['id']+'/graph')
    request = {'request_id':str(uuid.uuid4()), 'expected_revision':graph['revision'],
               'operation':'prune_branch', 'params':{'branch_id':first['branch_id']}}
    pruned = h.request('POST', '/api/projects/'+project['id']+'/graph/commands', json=request)
    assert pruned['intervention']['status'] == 'applied'
    assert len([effect for effect in pruned['intervention']['effects'] if effect['action']=='hold_scheduling']) == 2
    assert h.run(running)['status'] == 'running'
    assert all(h.run(run)['status']=='waiting_input' for run in queued)
    premature = h.client.post('/api/runs/'+queued[0]['id']+'/resume', json={})
    assert premature.status_code == 409 and premature.json()['detail']['code']=='BRANCH_INACTIVE'
    repeated = h.request('POST', '/api/projects/'+project['id']+'/graph/commands', json=request)
    assert repeated['intervention']['id'] == pruned['intervention']['id']
    restored = h.request('POST', '/api/projects/'+project['id']+'/graph/commands', json={
        'request_id':str(uuid.uuid4()), 'expected_revision':pruned['revision'],
        'operation':'restore_branch', 'params':{'branch_id':first['branch_id']}})
    h.terminal(running)
    time.sleep(1.2)  # Multiple actual worker ticks after the slot becomes free.
    assert all(h.run(run)['status']=='waiting_input' for run in queued)
    assert all(not (h.output(run)/'workspace'/'metrics.json').exists() for run in queued)
    h.request('POST', '/api/runs/'+queued[0]['id']+'/resume', json={})
    resumed = h.terminal(queued[0])
    assert resumed['status']=='completed' and resumed['metrics']['value']==1
    assert 'branch_scheduling_hold' not in resumed['resource']
    assert h.run(queued[1])['status']=='waiting_input'
    assert not (h.output(queued[1])/'workspace'/'metrics.json').exists()
    h.control_request('/api/runs/'+queued[1]['id']+'/cancel', json={})


def test_goal_edit_preserves_actual_verification_but_gates_downstream_use(tmp_path, intervention_harness):
    h = intervention_harness
    try:
        h.start_api(); h.start_worker()
        project = h.request('POST', '/api/projects', json={'name': 'Goal and computation separation', 'mode': 'manual',
            'goal': 'Measure the sum of squares for a numerical check', 'budget': {'seconds': 120, 'max_runs': 20, 'allow_paid': False}})
        producer = add_node(h, project, type='experiment', title='Actual square sum',
            config={'kind': 'experiment', 'command': arithmetic_command(500)})
        verifier = add_node(h, project, type='verification', title='Actual independent formula', config=verification_config(producer))
        consumer = add_node(h, project, type='analysis', title='Consume checked evidence', config={
            'kind': 'command', 'command': arithmetic_command(500), 'required_verification': [verifier['id']]},
            inputs=[{'node_id': producer['id'], 'path': 'metrics.json', 'destination': 'input.json', 'verification_node_id': verifier['id']}])
        source = h.launch(producer); assert h.terminal(source)['status'] == 'completed'
        check = h.launch(verifier); assert h.terminal(check)['status'] == 'completed'
        assert h.request('GET', f"/api/runs/{source['id']}/verification")['verification_status'] == 'accepted'
        current = h.request('GET', f"/api/projects/{project['id']}")
        current = h.request('PATCH', f"/api/projects/{project['id']}", json={'expected_revision': current['revision'], 'goal': 'Interpret the squared sum as a different research endpoint'})
        assert h.request('GET', f"/api/runs/{source['id']}/verification")['verification_status'] == 'accepted'
        applicability = h.request('GET', f"/api/runs/{source['id']}/applicability")
        assert applicability['status'] == 'needs_review' and not applicability['ready']
        queued = h.launch(consumer)
        blocked = wait_until(lambda: (r if (r := h.run(queued))['status'] == 'waiting_input' else None))
        assert blocked['resource']['blocked_reason'] == 'goal_applicability'
        assert not (h.output(queued) / 'workspace' / 'metrics.json').exists()
        h.stop(h.worker); h.stop(h.api); h.start_api()
        current = h.request('GET', f"/api/projects/{project['id']}")
        decision = {'request_id': str(uuid.uuid4()), 'expected_revision': current['revision'], 'choice': 'reuse',
                    'reason': 'The arithmetic observation measures the same input; only interpretation changed'}
        saved = h.request('POST', f"/api/runs/{source['id']}/applicability/decisions", json=decision)
        assert h.request('POST', f"/api/runs/{source['id']}/applicability/decisions", json=decision)['id'] == saved['id']
        assert h.request('GET', f"/api/runs/{source['id']}/applicability")['status'] == 'approved'
        h.request('POST', f"/api/runs/{queued['id']}/resume", json={}); h.start_worker()
        assert h.terminal(queued)['metrics']['score'] == sum(i*i for i in range(1, 501))
        current = h.request('GET', f"/api/projects/{project['id']}")
        h.request('PATCH', f"/api/projects/{project['id']}", json={'expected_revision': current['revision'], 'goal': 'A third distinct goal'})
        assert not h.request('GET', f"/api/runs/{source['id']}/applicability")['ready']
        assert h.request('GET', f"/api/runs/{source['id']}/verification")['verification_status'] == 'accepted'
    finally: h.cleanup()


def test_scoped_goal_edit_preserves_computation_and_requires_new_use_decision(intervention_harness):
    h = intervention_harness
    try:
        h.start_api(); h.start_worker()
        project = h.request('POST', '/api/projects', json={'name': 'Scoped direction and actual calculation',
            'goal': 'Study numerical endpoints', 'budget': {'seconds': 120, 'max_runs': 20, 'allow_paid': False}})
        pid = project['id']
        direction = add_node(h, project, type='goal', title='Scoped question', instructions='Interpret the square sum as endpoint A', config={})
        producer = add_node(h, project, type='experiment', title='Actual arithmetic', config={
            'kind': 'experiment', 'command': arithmetic_command(500)})
        graph = h.request('GET', f'/api/projects/{pid}/graph')
        h.request('POST', f'/api/projects/{pid}/graph/commands', json={'request_id': str(uuid.uuid4()),
            'expected_revision': graph['revision'], 'operation': 'add_dependency', 'targets': [direction['id'], producer['id']],
            'params': {'relation': 'evidence'}})
        verifier = add_node(h, project, type='verification', title='Independent formula', config=verification_config(producer))
        source = h.launch(producer); assert h.terminal(source)['status'] == 'completed'
        check = h.launch(verifier); assert h.terminal(check)['status'] == 'completed'
        original = h.request('GET', '/api/nodes/'+producer['id'])
        assert h.request('GET', f"/api/runs/{source['id']}/applicability")['ready']
        for text in ('Interpret the square sum as endpoint B', 'Interpret the square sum as endpoint C'):
            graph = h.request('GET', f'/api/projects/{pid}/graph')
            h.request('PATCH', '/api/nodes/'+direction['id'], json={'expected_revision': graph['revision'], 'instructions': text})
            current = h.request('GET', '/api/nodes/'+producer['id'])
            assert current.get('results_current') is not False
            assert current.get('rerun_generation', 0) == original.get('rerun_generation', 0)
            assert h.request('GET', f"/api/runs/{source['id']}/verification")['verification_status'] == 'accepted'
            assert h.request('GET', f"/api/runs/{source['id']}/applicability")['status'] == 'needs_review'
            project = h.request('GET', f'/api/projects/{pid}')
            h.request('POST', f"/api/runs/{source['id']}/applicability/decisions", json={'request_id': str(uuid.uuid4()),
                'expected_revision': project['revision'], 'choice': 'reuse',
                'reason': 'The same independently checked arithmetic remains usable for this scoped endpoint'})
            assert h.request('GET', f"/api/runs/{source['id']}/applicability")['ready']
    finally: h.cleanup()


def test_project_instruction_survives_an_unconsumed_cancelled_target(intervention_harness,tmp_path):
    """A terminal target closes its receipt without revoking future scope."""
    captures=[]
    class Transport(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            captures.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body=json.dumps({'message':{'content':json.dumps({'tool':'finish','arguments':{'summary':'Read current owner scope','artifacts':[]}})},
                             'done':True,'prompt_eval_count':80,'eval_count':20}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Transport)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    h=intervention_harness
    try:
        h.start_api()
        provider=h.request('POST','/api/providers',json={'name':'Actual scoped delivery transport','kind':'ollama',
            'base_url':f'http://127.0.0.1:{server.server_port}','model':'fixture','allow_paid':False})
        project=h.request('POST','/api/projects',json={'name':'Unconsumed target scope','goal':'Preserve explicit future scope',
            'config':{'provider_id':provider['id'],'publication_profile':'operational'},'budget':{'seconds':120,'max_runs':5}})
        node=add_node(h,project,type='analysis',config={'kind':'agent','tools':['finish'],'agent_budget':{'steps':3},'timeout':30})
        original=h.launch(node)
        graph=h.request('GET',f"/api/projects/{project['id']}/graph")
        instruction=h.request('POST',f"/api/projects/{project['id']}/instructions",json={'request_id':str(uuid.uuid4()),
            'expected_revision':graph['revision'],'text':'RETAIN_SCOPED_OWNER_INSTRUCTION','scope':'project','boundary':'next_request'})
        assert instruction['status']=='accepted'
        h.control_request('/api/runs/'+original['id']+'/cancel',json={})
        replacement=h.launch(node);h.start_worker()
        completed=h.terminal(replacement,timeout=30)
        assert completed['status']=='completed',completed
        assert 'RETAIN_SCOPED_OWNER_INSTRUCTION' in json.dumps(captures),captures
        receipt=wait_until(lambda:(row if (row:=h.request('GET','/api/interventions/'+instruction['id']))['status']=='partially_applied' else None),timeout=10)
        assert {e['status'] for e in receipt['effects']}=={'superseded','applied'},receipt
        assert {e['run_id'] for e in receipt['effects']}=={original['id'],replacement['id']}
        (tmp_path/'scope_requests.json').write_text(json.dumps(captures,indent=2))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
