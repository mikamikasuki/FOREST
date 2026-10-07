"""Real filesystem operations, transaction rollback and process-exit recovery."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from tests.test_intervention_worker import intervention_harness

ROOT = Path(__file__).resolve().parents[1]


def scenario(case):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from services.api.db import migrate, Session, Branch
    from services.api.main import app
    from services.api.common import project_dir, graph_from_db
    from services.interventions import application, workspaces
    from services.interventions.models import InterventionEffect
    migrate()
    # Hold the wakeup to inspect the durable pre-effect state. Recovery itself
    # executes actual copies and actual process exits, without a filesystem fake.
    application.kick_effects = lambda **kwargs: None
    client = TestClient(app)
    project = client.post('/api/projects', json={'name': 'Workspace recovery'}).json()
    pid = project['id']; root = project_dir(pid)
    result = client.post(f'/api/projects/{pid}/graph/commands', json={
        'request_id': str(uuid.uuid4()), 'expected_revision': 0, 'operation': 'add_node',
        'params': {'title': 'Fork source', 'config': {'command': [sys.executable, '-c', 'print(1)']}}})
    assert result.status_code == 200, result.text
    node = result.json()['graph']['nodes'][0]
    source = root/'branches'/node['branch_id']/'workspace'
    for index in range(100):
        path = source / 'code' / ('module'+str(index)+'.py')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('value = '+str(index)+'\n')
    (source/'data').mkdir(); (source/'data'/'private.csv').write_text('id,value\n1,5\n')
    command = {'operation': 'fork_branch', 'targets': [node['id']], 'params': {'name': 'Independent copy'}}
    request = {'request_id': str(uuid.uuid4()), 'expected_revision': 1, **command}
    if case == 'rollback':
        failed = client.post(f'/api/projects/{pid}/graph/batch', json={
            'request_id': str(uuid.uuid4()), 'expected_revision': 1, 'commands': [command,
                {'operation': 'add_dependency', 'targets': [node['id']],
                 'params': {'source': node['id'], 'target': 'nonexistent'}}]})
        assert failed.status_code in (400, 404, 422), failed.text
        assert {path.name for path in (root/'branches').iterdir()} == {node['branch_id']}
        assert not (root/'.forest-bases').exists()
        assert not client.get(f'/api/projects/{pid}/interventions').json()[0].get('effects')
        assert client.get(f'/api/projects/{pid}/graph').json()['revision'] == 1
        return
    accepted = client.post(f'/api/projects/{pid}/graph/commands', json=request)
    assert accepted.status_code == 200, accepted.text
    parent = accepted.json()['intervention']; effect_id = parent['effects'][0]['id']
    assert parent['status'] == 'accepted'
    branch_id = parent['effects'][0]['run_id']
    destination = root/'branches'/branch_id
    assert not destination.exists()
    with Session() as session:
        branch = session.get(Branch, branch_id)
        assert branch.status == 'materializing' and branch.extra['workspace_intervention']['effect_id'] == effect_id
    if case == 'changed_source':
        (source/'code'/'module1.py').write_text('value = 999\n')
        workspaces.reconcile(intervention_id=parent['id'])
        failed = client.get('/api/interventions/'+parent['id']).json()
        assert failed['status'] == 'needs_attention' and failed['effects'][0]['status'] == 'failed'
        assert 'changed' in failed['effects'][0]['error'] and not destination.exists()
        return
    if case in ('undo_before_copy','undo_after_copy'):
        if case=='undo_after_copy':
            with Session() as session: plan=dict(session.get(InterventionEffect,effect_id).target)
            workspaces.materialize(effect_id,plan)
        undone=client.post(f'/api/projects/{pid}/graph/commands',json={
            'request_id':str(uuid.uuid4()),'expected_revision':2,'operation':'undo','params':{}})
        assert undone.status_code==200,undone.text
        assert branch_id not in {branch['id'] for branch in undone.json()['graph']['branches']}
        request={'request_id':str(uuid.uuid4()),'expected_revision':3,'operation':'redo','params':{}}
        redone=client.post(f'/api/projects/{pid}/graph/commands',json=request)
        assert redone.status_code==200,redone.text
        parent=redone.json()['intervention'];effect_id=parent['effects'][0]['id']
        assert parent['status']=='accepted' and parent['effects'][0]['run_id']==branch_id
    if case in ('receipt_gap', 'rename_gap'):
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'crash-child', effect_id, case],
                               cwd=ROOT, env=os.environ, timeout=30)
        assert child.returncode == (79 if case == 'receipt_gap' else 80)
        time.sleep(.2)
        # The accepted copy survives source edits after its bytes have already
        # reached an atomic branch/base directory. Recovery adopts those bytes.
        (source/'code'/'module1.py').write_text('value = 999\n')
    blocked=client.put(f'/api/projects/{pid}/file',json={'path':str((destination/'code'/'module1.py').relative_to(root)),'content':'unauthorized pending edit'})
    assert blocked.status_code==409,blocked.text
    workspaces.reconcile(intervention_id=parent['id'])
    completed = client.get('/api/interventions/'+parent['id']).json()
    assert completed['status'] == 'applied', completed
    assert (destination/'code'/'module1.py').read_text() == 'value = 1\n'
    assert len(list((destination/'code').glob('*.py'))) == 100
    assert not (destination/'data'/'private.csv').exists()
    with Session() as session: assert session.get(Branch, branch_id).status == 'active'
    repeated = client.post(f'/api/projects/{pid}/graph/commands', json=request)
    assert repeated.status_code == 200 and repeated.json()['intervention']['id'] == parent['id']
    if case == 'merge':
        (destination/'code'/'module1.py').write_text('value = 2\n')
        revision = client.get(f'/api/projects/{pid}/graph').json()['revision']
        merged = client.post(f'/api/projects/{pid}/graph/commands', json={
            'request_id': str(uuid.uuid4()), 'expected_revision': revision, 'operation': 'merge_branches',
            'params': {'left': node['branch_id'], 'right': branch_id}})
        assert merged.status_code == 200, merged.text
        merge_parent = merged.json()['intervention']
        workspaces.reconcile(intervention_id=merge_parent['id'])
        merge_result = client.get('/api/interventions/'+merge_parent['id']).json()
        assert merge_result['status'] == 'applied', merge_result
        merged_path = root/'branches'/merge_result['effects'][0]['run_id']
        assert (merged_path/'code'/'module1.py').read_text() == 'value = 2\n'
    print('PASS', case)


def crash_child(effect_id, case):
    from services.api.db import Session
    from services.interventions.models import InterventionEffect
    from services.interventions import workspaces
    with Session.begin() as session:
        effect = session.get(InterventionEffect, effect_id)
        effect.status = 'applying'; effect.lease_owner = 'exited-process'; effect.lease_until = time.time()+.1
        plan = effect.target
    if case == 'rename_gap':
        original_sync=workspaces._sync_directory
        def terminate_after_published_base(path):
            original_sync(path)
            if path.name=='.forest-bases': os._exit(80)
        workspaces._sync_directory=terminate_after_published_base
    workspaces.materialize(effect_id, plan)
    os._exit(79)


@pytest.mark.parametrize('case', ['rollback', 'fork', 'merge', 'changed_source', 'receipt_gap', 'rename_gap','undo_before_copy','undo_after_copy'])
def test_workspace_recovery(tmp_path, case,intervention_harness):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path/'data'),
           'FOREST_DATABASE_URL': intervention_harness.env['FOREST_DATABASE_URL'], 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), case],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr


if __name__ == '__main__':
    if sys.argv[1] == 'crash-child': crash_child(sys.argv[2], sys.argv[3])
    else: scenario(sys.argv[1])
