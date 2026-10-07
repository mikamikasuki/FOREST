"""Real API, worker, arithmetic subprocess and goal revision receipts.

A local deterministic protocol fixture captures transport requests.
"""
import hashlib
import json
import re
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.test_worker import Harness, wait_until


def probe(directory, mode):
    edited = mode == "queued_edit"
    resumed = mode == "continuation"
    captures = []

    class Transport(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            messages = payload['messages']
            captures.append(payload)
            stage = len(captures)
            if stage == 1:
                text = json.dumps(messages)
                operation = '*' if 'GOAL_MULTIPLY' in text else '+'
                action = {'tool': 'python', 'arguments': {'code': "import json\nfrom pathlib import Path\nvalue=2" + operation + "3\nPath('metrics.json').write_text(json.dumps({'value':value}))\nprint(value)\n"}}
            elif stage == 2:
                matches = re.findall(r'"process_id"\s*:\s*"([^"]+)"', '\n'.join(m['content'] for m in messages))
                action = {'tool': 'wait_for_process', 'arguments': {'process_id': matches[-1]}}
            elif stage == 3:
                action = {'tool': 'read_file', 'arguments': {'path': 'metrics.json'}}
            else:
                action = {'tool': 'finish', 'arguments': {'summary': 'Local arithmetic inspected.', 'artifacts': ['metrics.json']}}
            response = json.dumps({'model': 'deterministic-contract-fixture', 'message': {'role': 'assistant', 'content': json.dumps(action)}, 'done': True, 'prompt_eval_count': 100, 'eval_count': 40}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Transport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    h = Harness(directory)
    h.env['FOREST_WORKER_CONCURRENCY'] = '1'
    try:
        h.start_api()
        provider = h.request('POST', '/api/providers', json={'name': 'Local contract fixture', 'kind': 'ollama', 'base_url': f'http://127.0.0.1:{server.server_port}', 'model': 'deterministic-contract-fixture', 'allow_paid': False})
        project = h.request('POST', '/api/projects', json={'name': 'Goal dispatch provenance', 'goal': 'GOAL_ADD: compute 2 + 3 using an actual Python process.', 'config': {'provider_id': provider['id']}, 'budget': {'max_runs': 3, 'seconds': 60, 'allow_paid': False}})
        graph = h.request('GET', f"/api/projects/{project['id']}/graph")
        nid = str(uuid.uuid4())
        response = h.request('POST', f"/api/projects/{project['id']}/graph/commands", json={'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'], 'operation': 'add_node', 'targets': [], 'params': {'id': nid, 'branch_id': graph['branches'][0]['id'], 'type': 'experiment', 'title': 'Execute project arithmetic goal', 'instructions': 'Execute the current project goal, inspect metrics.json, then finish.', 'config': {'kind': 'agent', 'required_outputs': ['metrics.json'], 'metrics_file': 'metrics.json', 'agent_budget': {'steps': 1 if resumed else 8}, 'timeout': 45}}})
        node = next(n for n in response['graph']['nodes'] if n['id'] == nid)
        queued = h.launch(node)
        if edited:
            p = h.request('GET', f"/api/projects/{project['id']}")
            h.request('PATCH', f"/api/projects/{project['id']}", json={'expected_revision': p['revision'], 'goal': 'GOAL_MULTIPLY: compute 2 * 3 using an actual Python process.'})
        h.start_worker()
        if resumed:
            wait_until(lambda: h.run(queued)['status'] == 'budget_exhausted', timeout=30)
            p = h.request('GET', f"/api/projects/{project['id']}")
            h.request('PATCH', f"/api/projects/{project['id']}", json={'expected_revision': p['revision'], 'goal': 'GOAL_MULTIPLY: compute 2 * 3 using an actual Python process.'})
            h.stop(h.worker)
            h.request('POST', f"/api/runs/{queued['id']}/resume", json={'agent_budget': {'steps': 8}})
            h.start_worker()
        completed = h.terminal(queued, timeout=40)
        (directory / 'run.json').write_text(json.dumps(completed, indent=2))
        (directory / 'provider_requests.json').write_text(json.dumps(captures, indent=2))
        workspace = h.output(completed) / 'workspace'
        packet = json.loads((workspace / 'context_packet.json').read_text()) if (workspace / 'context_packet.json').exists() else {}
        report = {'edited': edited, 'status': completed['status'], 'enqueue_goal': queued['config']['project_goal'], 'persisted_goal': completed['config']['project_goal'], 'consumed_goal': packet.get('controls', {}).get('goal', packet.get('goal')), 'metrics': completed['metrics'], 'error': completed.get('error'), 'model_requests': len(captures), 'workspace': str(workspace)}
        (directory / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        assert report['status'] == 'completed', report
        h.stop(h.api)
        h.start_api()
        reloaded = h.run(queued)
        assert reloaded['metrics'] == completed['metrics']
        return report, completed, captures
    finally:
        h.cleanup()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('mode', ['normal', 'queued_edit', 'continuation'])
def test_actual_worker_retains_consumed_goal_context_across_edit_resume_and_api_restart(tmp_path, mode):
    report, run, captures = probe(tmp_path, mode)
    contexts = run['metrics']['execution_context']
    assert contexts['history_scope'] == 'authoritative_project_context'
    for request, receipt in zip(captures, contexts['requests'], strict=True):
        digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert receipt['payload_sha256'] == digest
        assert receipt['goal_field_present']
        assert receipt['task_delivery'] == ('original_message' if receipt['original_task_message_present'] else 'managed_context')
    assert contexts['unrecorded_steps'] == []
    history = contexts['history']
    assert history[0]['first_step'] == 1
    assert history[-1]['last_step'] == run['metrics']['steps']
    assert [step for entry in history for step in range(entry['first_step'], entry['last_step'] + 1)] == list(range(1, len(captures) + 1))
    for entry in history:
        assert entry['run_id'] == run['id']
        assert entry['project_id'] == run['project_id']
        assert entry['node_id'] == run['node_id']
        assert entry['attempt_id']
        assert entry['project_revision'] is not None
        for request in captures[entry['first_step']-1:entry['last_step']]:
            assert entry['goal'] in json.dumps(request)
    assert run['config']['project_goal'].startswith('GOAL_ADD')
    if mode == 'continuation':
        assert len(history) >= 2
        assert history[0]['goal'].startswith('GOAL_ADD')
        assert history[0]['first_step'] == history[0]['last_step'] == 1
        assert all(entry['goal'].startswith('GOAL_MULTIPLY') for entry in history[1:])
        assert history[1]['first_step'] == 2
        assert history[0]['project_revision'] < history[1]['project_revision']
        assert history[0]['attempt_id'] != history[1]['attempt_id']
        assert report['metrics']['observed_metrics']['value'] == 5
    else:
        assert all(entry['goal'].startswith('GOAL_MULTIPLY' if mode == 'queued_edit' else 'GOAL_ADD') for entry in history)
        assert report['metrics']['observed_metrics']['value'] == (6 if mode == 'queued_edit' else 5)


def test_legacy_turns_keep_unknown_context_instead_of_inheriting_current_goal():
    from research.agents.runtime import execution_context_receipts
    state = {'context_history': [{'task': 'historical context'}, {'project_context': {'goal': 'current goal'}}],
             'transcript': [{'step': 1}, {'step': 2, 'context_history_index': 0},
                            {'step': 3, 'context_history_index': 1}, {'step': 4, 'context_history_index': True}]}
    receipt = execution_context_receipts(state)
    assert receipt['unrecorded_steps'] == [1, 2, 4]
    assert receipt['history'] == [{'goal': 'current goal', 'context_history_index': 1, 'first_step': 3, 'last_step': 3}]


@pytest.mark.parametrize('mode', ['failed_turn', 'paged_goal'])
def test_actual_transport_failure_and_paging_keep_distinct_delivery_receipts(tmp_path, mode):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'FOREST_MODEL': '', 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'delivery.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'), 'PYTHONPATH': str(root)}
    result = subprocess.run([sys.executable, __file__, str(tmp_path), mode], cwd=root,
                            env=env, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def probe_request_delivery(folder, mode):
    from services.api.db import migrate, Session, Project, TaskRun, uid
    from research.agents.runtime import run_agent, AgentYield, execution_context_receipts
    migrate()
    captures=[]
    class Transport(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captures.append(body)
            stage=len(captures)
            status=200
            if mode=='paged_goal' and stage==1:
                status=400
                response={'error':{'code':'context_length_exceeded'}}
            else:
                if mode=='failed_turn' and stage==1:
                    response={'model':'validator-local','message':{'content':'unfinished'},'done':True,'done_reason':'length','prompt_eval_count':100,'eval_count':40}
                else:
                    action = ({'tool':'write_file_chunk','arguments':{'path':'notes.txt','content':'local recovered fixture','offset':0}} if mode=='failed_turn' and stage==2 else
                              {'tool':'finish','arguments':{'summary':'Local protocol completed.','artifacts':['notes.txt']}} if mode=='failed_turn' else
                              {'tool':'list_files','arguments':{}})
                    response={'model':'validator-local','message':{'content':json.dumps(action)},'done':True,'prompt_eval_count':100,'eval_count':40}
            value=json.dumps(response).encode()
            self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(value))); self.send_header('x-request-id',f'{mode}-{stage}'); self.end_headers(); self.wfile.write(value)
    server=ThreadingHTTPServer(('127.0.0.1',0),Transport)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    goal='VALIDATOR_UNIQUE_GOAL: '+ ('a'*16000 if mode=='paged_goal' else 'retain recovered model goal')
    config={'provider_snapshot':{'kind':'ollama','base_url':f'http://127.0.0.1:{server.server_port}','model':'validator-local','config':{'max_retries':0}},'instructions':'Follow original project controls.','agent_budget':{'steps':2 if mode=='paged_goal' else 8},'allow_paid':False}
    with Session.begin() as s:
        p=Project(name='Disposable validation '+mode,goal=goal,revision=42)
        s.add(p);s.flush()
        r=TaskRun(project_id=p.id,request_id=uid(),status='running',kind='agent',config={**config,'execution_attempt':{'id':uid(),'number':1}})
        s.add(r);s.flush();run_id=r.id
    result=None;outcome='completed'
    try:
        result=run_agent(run_id,folder/'workspace',config)
    except AgentYield as exc:
        outcome=exc.status
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
    state=json.loads((folder/'workspace'/'agent_session.json').read_text())
    receipt=execution_context_receipts(state)
    report={'mode':mode,'outcome':outcome,'requests':len(captures),'goal_present_in_requests':[goal in json.dumps(x) for x in captures], 'goal_marker_present_in_requests':['VALIDATOR_UNIQUE_GOAL' in json.dumps(x) for x in captures], 'context_history_indexes':[turn.get('context_history_index') for turn in state['transcript']], 'turn_statuses':[turn.get('status','response') for turn in state['transcript']], 'execution_context':receipt,'request_context_management':state.get('context_management')}
    (folder/'provider_requests.json').write_text(json.dumps(captures,indent=2))
    (folder/'report.json').write_text(json.dumps(report,indent=2))
    if result:(folder/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('execution_context','request_context_management')},indent=2))
    print('receipt_unknown_steps',receipt['unrecorded_steps'],'receipt_ranges',[(e['first_step'],e['last_step']) for e in receipt['history']])
    if mode=='failed_turn':
        assert outcome=='completed' and len(captures)==3
        assert all(report['goal_present_in_requests'])
        assert receipt['unrecorded_steps']==[]
        assert [x['original_task_message_present'] for x in receipt['requests']] == [True, True, True]
        assert receipt['requests'][0]['status'] == 'incomplete'
    else:
        assert outcome=='budget_exhausted' and len(captures)==2
        assert report['goal_marker_present_in_requests']==[True,False]
        assert receipt['history_scope'] == 'authoritative_project_context'
        assert receipt['history'][0]['first_step']==1 and receipt['history'][0]['goal']==goal
        assert receipt['unrecorded_steps'] == []
        assert [x['original_task_message_present'] for x in receipt['requests']] == [True, False]
        assert receipt['requests'][1]['task_delivery'] == 'managed_context'
        assert receipt['requests'][1]['required_context_page']
        assert [x['goal_field_present'] for x in receipt['requests']] == [True, False]
    for request, delivered in zip(captures, receipt['requests'], strict=True):
        digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert delivered['payload_sha256'] == digest
    return report



if __name__ == '__main__':
    probe_request_delivery(Path(sys.argv[1]), sys.argv[2])


def test_goal_delivery_requires_actual_task_field_and_handles_json_escaping():
    from research.agents.runtime import request_has_goal_field
    goal = 'Quote "雪" and a newline\nremain exact'
    assert request_has_goal_field([{'role': 'user', 'content': json.dumps({'context': {'controls': {'goal': goal}}})}], goal)
    assert not request_has_goal_field([{'role': 'user', 'content': json.dumps({'materials': {'goal': goal}})}], goal)
    assert not request_has_goal_field([{'role': 'user', 'content': json.dumps({'context': {}})}], '')
