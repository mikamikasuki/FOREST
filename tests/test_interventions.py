"""Intervention contracts use real handlers and isolated databases.

Fixture run states establish API semantics, not OS cancellation qualification.
Actual executors and transport races are exercised separately.
"""
import os
from pathlib import Path
import subprocess
import sys
import uuid
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


def scenario(case):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from services.api.db import migrate, Session, Node, TaskRun, Branch, Hypothesis, Figure, PaperDocument
    from services.api.main import app
    from services.api.common import project_dir
    from research.agents.runtime import ToolRuntime
    migrate()
    client = TestClient(app)
    project = client.post('/api/projects', json={'name': 'Intervention regression'}).json()
    pid = project['id']
    created = client.post(f'/api/projects/{pid}/graph/commands', json={
        'request_id': str(uuid.uuid4()), 'expected_revision': 0,
        'operation': 'add_node', 'targets': [],
        'params': {'title': 'Editable task', 'instructions': 'Original',
                   'config': {'tools': ['graph_command', 'read_file', 'finish'] if case == 'stale_agent' else ['read_file', 'finish']}}}).json()
    nid = created['graph']['nodes'][0]['id']
    with Session.begin() as s:
        node = s.get(Node, nid)
        run = TaskRun(project_id=pid, node_id=nid, branch_id=node.branch_id,
                      request_id=str(uuid.uuid4()), node_revision=node.revision,
                      output_path='runs/probe', status='running', config={})
        s.add(run); s.flush(); rid = run.id
        node.extra = {**node.extra, 'latest_run_id': rid}
        node.execution_status = 'running'
    workspace = project_dir(pid) / 'runs/probe/workspace'
    workspace.mkdir(parents=True)
    runtime = ToolRuntime(rid, workspace)
    if case == 'stale_agent':
        edited = client.patch(f'/api/nodes/{nid}', json={
            'expected_revision': 1, 'instructions': 'Owner correction'})
        assert edited.status_code == 200
        result = runtime.execute('graph_command', {
            'expected_revision': 1, 'operation': 'edit_node', 'targets': [nid],
            'params': {'instructions': 'Old agent intent'}})
        assert result.get('exit_code') == 1
        assert client.get(f'/api/nodes/{nid}').json()['instructions'] == 'Owner correction'
    elif case == 'tools':
        result = runtime.execute('write_file', {'path': 'forbidden.txt', 'content': 'not authorized'})
        assert result.get('exit_code') == 1 and not (workspace/'forbidden.txt').exists()
    elif case in ('dependencies', 'layout_dependencies'):
        with Session.begin() as s:
            figure=Figure(project_id=pid,title='Bound result figure',data={'run_ids':[rid]})
            s.add(figure);s.flush();fid=figure.id
            paper=PaperDocument(project_id=pid,title='Bound paragraph',data={'dependency_bindings':[
                {'source_kind':'figure','source_id':fid,'target_path':'/sections/results/paragraphs/2'}]},status='compiled')
            unrelated=PaperDocument(project_id=pid,title='Independent manuscript',data={'source':'A prose mention of '+nid},status='compiled')
            s.add_all([paper,unrelated]);s.flush();paper_id=paper.id;unrelated_id=unrelated.id
        patch={'position':{'x':100,'y':100}} if case=='layout_dependencies' else {'instructions':'Changed computational task'}
        response=client.patch(f'/api/nodes/{nid}',json={'expected_revision':1,**patch})
        assert response.status_code==200,response.text
        with Session() as s:
            assert s.get(PaperDocument,unrelated_id).status=='compiled'
            bound=s.get(PaperDocument,paper_id)
            if case=='dependencies':
                assert bound.status=='needs_update'
                assert bound.data['stale_dependencies'][0]['target_path']=='/sections/results/paragraphs/2'
                assert s.get(Figure,fid).status=='needs_update'
            else:assert bound.status=='compiled'
    elif case == 'rejected_consumption':
        from services.interventions.controls import decision_gate, answer_decision, consume_decision
        with Session.begin() as s:
            s.get(Node,nid).config={'tools':['write_file','finish'],'human_review_tools':['write_file']}
        proposed={'id':str(uuid.uuid4()),'tool':'write_file','arguments':{'path':'rejected.txt','content':'must never execute'},'observed_graph_revision':1}
        _,decision=decision_gate(rid,proposed)
        answer_decision(decision['id'],expected_revision=1,choice='reject',reason='Durable refusal')
        consume_decision(decision['id'])
        first=client.get(f'/api/projects/{pid}/decisions').json()[0]
        # A crash can leave the old pending action in the session after this
        # consumed receipt. Replaying it must still refuse execution.
        approved,replayed=decision_gate(rid,proposed)
        assert approved is None and replayed['status']=='rejected'
        consume_decision(decision['id'])
        second=client.get(f'/api/projects/{pid}/decisions').json()[0]
        assert first['consumed_at']==second['consumed_at']
        assert second['status']=='rejected' and second['answer']=={'choice':'reject','reason':'Durable refusal'}
        assert not (workspace/'rejected.txt').exists()
    elif case == 'portable_history':
        import io,zipfile,json
        from services.interventions.controls import decision_gate
        with Session.begin() as s:
            s.get(Node,nid).config={'tools':['write_file','finish'],'human_review_tools':['write_file']}
        proposed={'id':str(uuid.uuid4()),'tool':'write_file','arguments':{'path':'unapproved.txt','content':'pending'},'observed_graph_revision':1}
        _,decision=decision_gate(rid,proposed)
        instruction=client.post(f'/api/projects/{pid}/instructions',json={'request_id':str(uuid.uuid4()),
            'expected_revision':1,'text':'Original scoped instruction','scope':'project'}).json()
        archive=client.post(f'/api/projects/{pid}/export',json={})
        assert archive.status_code==200,archive.text
        manifest=json.loads(zipfile.ZipFile(io.BytesIO(archive.content)).read('forest-project.json'))
        assert manifest['action_decisions'][0]['id']==decision['id']
        from copy import deepcopy
        original_projects = len(client.get('/api/projects').json())
        malformed = []
        bad = deepcopy(manifest); bad['interventions'] = {}; malformed.append(bad)
        bad = deepcopy(manifest); bad['interventions'][0]['project_id'] = str(uuid.uuid4()); malformed.append(bad)
        bad = deepcopy(manifest); bad['interventions'].append(deepcopy(bad['interventions'][0])); malformed.append(bad)
        effect_parent = next(index for index, row in enumerate(manifest['interventions']) if row['effects'])
        bad = deepcopy(manifest); bad['interventions'][effect_parent]['effects'][0]['intervention_id'] = str(uuid.uuid4()); malformed.append(bad)
        bad = deepcopy(manifest); bad['action_decisions'][0]['id'] = bad['interventions'][effect_parent]['effects'][0]['id']; malformed.append(bad)
        for bad in malformed:
            content = io.BytesIO()
            with zipfile.ZipFile(content, 'w') as invalid: invalid.writestr('forest-project.json', json.dumps(bad))
            rejected = client.post('/api/projects/import', files={'file': ('bad.zip', content.getvalue(), 'application/zip')})
            assert rejected.status_code == 422, rejected.text
            assert len(client.get('/api/projects').json()) == original_projects
        copied=client.post(f'/api/projects/{pid}/duplicate',json={})
        assert copied.status_code==200,copied.text
        imported=client.post('/api/projects/import',files={'file':('forest.zip',archive.content,'application/zip')})
        assert imported.status_code==200,imported.text
        for result in (copied,imported):
            new_id=result.json()['id']
            assert not client.get(f'/api/projects/{new_id}/decisions?status=pending').json()
            assert client.get(f'/api/projects/{new_id}/decisions').json()[0]['status']=='stale'
            historical=client.get(f'/api/projects/{new_id}/interventions').json()[0]
            assert historical['status']=='superseded' and historical['id']!=instruction['id']
            assert not (workspace/'unapproved.txt').exists()
    elif case.startswith('stop_'):
        entry, status = case.split('_')[1:]
        with Session.begin() as s:
            s.get(TaskRun, rid).status = status
            s.get(Node, nid).execution_status = status
        command = {'operation': 'edit_node', 'targets': [nid],
                   'params': {'instructions': 'Corrected', 'stop_current_run': True}}
        request_id = str(uuid.uuid4())
        if entry == 'single':
            response = client.post(f'/api/projects/{pid}/graph/commands', json={
                **command, 'request_id': request_id, 'expected_revision': 1})
        elif entry == 'batch':
            response = client.post(f'/api/projects/{pid}/graph/batch', json={
                'request_id': request_id, 'expected_revision': 1, 'commands': [command]})
        else:
            with Session.begin() as s:
                proposal = Hypothesis(project_id=pid, title='Proposed edit', data={
                    'commands': [command], 'graph_revision': 1})
                s.add(proposal); s.flush(); proposal_id = proposal.id
            response = client.post(f'/api/research/proposals/{proposal_id}/apply', json={
                'request_id': request_id, 'expected_revision': 1})
        assert response.status_code == 200, response.text
        deadline = time.monotonic()+5
        while client.get(f'/api/runs/{rid}').json()['status'] != 'cancelled' and time.monotonic()<deadline:
            time.sleep(.02)
        assert client.get(f'/api/runs/{rid}').json()['status'] == 'cancelled'
        receipt = next(i for i in client.get(f'/api/projects/{pid}/interventions').json()
                       if i['id'] == response.json()['intervention']['id'])
        assert receipt['status'] == 'applied'
    print('PASS', case)


CASES = ['rejected_consumption', 'stale_agent', 'tools', 'dependencies', 'layout_dependencies', 'portable_history', *[
    f'stop_{entry}_{status}' for entry in ('single', 'batch', 'proposal')
    for status in ('queued', 'paused', 'running')]]


@pytest.mark.parametrize('case', CASES)
def test_intervention_contract(tmp_path, case):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path/'data'),
           'FOREST_DATABASE_URL': 'sqlite:///'+str(tmp_path/'db.sqlite'),
           'FOREST_MODEL': '', 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), case],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr


if __name__ == '__main__':
    scenario(sys.argv[1])
