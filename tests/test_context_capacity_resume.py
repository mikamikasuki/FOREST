"""Actual local context and HTTP persistence checks; no model transport."""
import copy
import json
import subprocess
import sys

import pytest

from research.agents.runtime import session_messages
from research.agents.context_store import ContextStore
from tests.test_worker import Harness


def test_oversized_native_exchange_rebases_without_context_capacity_error(tmp_path):
    state = {'messages': [{'role': 'system', 'content': 'Required policy'},
                          {'role': 'user', 'content': 'Required task'},
                          {'role': 'assistant', 'content': '', 'native_output': [
                              {'type': 'reasoning', 'encrypted_content': 'opaque' * 5000},
                              {'type': 'function_call', 'call_id': 'actual-wire-contract'}]},
                          {'role': 'user', 'content': 'Saved file receipt', 'native_call_id': 'actual-wire-contract'}],
             'transcript': []}
    before = copy.deepcopy(state)
    compact = session_messages(state, tmp_path, 2000)
    assert all('native_output' not in message and 'native_call_id' not in message for message in compact)
    assert state['messages'] == before['messages']
    receipt = ContextStore(tmp_path).read(state['context_management']['latest_public_exchange'])
    assert 'Saved file receipt' in receipt['content'] and 'encrypted_content' not in receipt['content']
    expanded = session_messages(state, tmp_path, 40000)
    assert state['messages'][-2] in expanded and state['messages'][-1] in expanded
    assert state['messages'] == before['messages']


def test_context_capacity_resume_preserves_same_run_and_existing_files(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project = harness.request('POST', '/api/projects', json={'name': 'Context continuation'})
        code = r'''
import json
from services.api.db import Session,TaskRun
from services.api.common import project_dir
with Session.begin() as s:
 r=TaskRun(project_id=PROJECT,request_id='retained-context-failure',kind='agent',status='failed',pid=999999999,process_created=0,
   error='The latest complete native tool exchange exceeds context_char_budget; increase that editable budget. Call correlation and required instructions were preserved.',
   config={'context_char_budget':48000,'agent_budget':{'cost':1.1}})
 s.add(r);s.flush();r.output_path='runs/'+r.id
 w=project_dir(PROJECT)/r.output_path/'workspace';w.mkdir(parents=True)
 (w/'actual.py').write_text('print(7)\n');(w/'agent_session.json').write_text('{"run_id":"'+r.id+'","transcript":[]}')
 print(json.dumps({'id':r.id,'workspace':str(w)}))
'''.replace('PROJECT', repr(project['id']))
        result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=harness.env,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        run = json.loads(result.stdout)
        from pathlib import Path
        workspace = Path(run['workspace'])
        originals = {p.name: p.read_bytes() for p in workspace.iterdir()}
        endpoint = f"/api/runs/{run['id']}/resume"
        for body in ({'context_char_budget': True}, {'context_char_budget': 20.5}, {'context_char_budget': 0}):
            assert harness.client.post(endpoint, json=body).status_code == 422
            assert harness.request('GET', f"/api/runs/{run['id']}")['status'] == 'failed'
        resumed = harness.request('POST', endpoint, json={})
        assert resumed['id'] == run['id'] and resumed['status'] == 'queued'
        assert resumed['config']['_next_attempt']['mode'] == 'continue'
        assert resumed['config']['agent_budget'] == {'cost': 1.1}
        assert resumed['config']['context_char_budget'] == 48000
        change = resumed['config']['context_policy_changes'][-1]
        assert (change['previous'], change['updated'], change['previous_status']) == ('legacy', 'automatic', 'failed')
        assert change['previous_error'].startswith('The latest complete native tool exchange')
        assert {name: (workspace / name).read_bytes() for name in originals} == originals
    finally:
        harness.cleanup()
