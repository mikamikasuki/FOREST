"""Real isolated API transactions for adopted executable research plans."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys

import pytest
from test_api import create, ok, graph, command

ROOT = Path(__file__).resolve().parents[1]


def idea(client, project, **data):
    return ok(client.post('/api/ideas', json={'project_id':project['id'],'title':'Test a falsifiable mechanism',
        'data':{'claim':'Changing a mechanism changes the measured outcome','cheapest_resolution':'Matched pilot with one ablation',**data}}))


def adopted(client, record, **body):
    return client.post(f"/api/ideas/{record['id']}/adopt",json=body)


def case_explicit_command_and_case_preserved(client):
    p=create(client)
    config={'kind':'command','command':['python','pilot.py'],'metrics_file':'pilot_metrics.json','timeout':20,'seed':17}
    i=idea(client,p,experiment_config=config)
    g=ok(adopted(client,i,expected_revision=0,request_id='explicit'))
    assert g['revision']==1 and len(g['nodes'])==2 and len(g['edges'])==1
    h=next(n for n in g['nodes'] if n['type']=='hypothesis')
    e=next(n for n in g['nodes'] if n['type']=='experiment')
    assert h['config']=={'kind':'agent','role':'Researcher'}
    assert e['config']==config and e['inputs'][-1]['node_id']==h['id'] and e['inputs'][-1]['path']=='hypothesis.md'
    assert all(n['execution_status']=='idle' for n in g['nodes'])
    assert ok(client.get(f"/api/projects/{p['id']}/runs"))==[]
    assert ok(adopted(client,i,expected_revision=0,request_id='explicit'))==g
    # Adoption is one undoable graph edit, including all dependent nodes.
    assert ok(command(client,p,'undo'))['graph']['nodes']==[]
    assert len(ok(command(client,p,'redo'))['graph']['nodes'])==2
    p2=create(client)
    j=idea(client,p2,experiment_config={'command':['python','old.py']})
    g2=ok(adopted(client,j,config={'case':'class_weight_correction','datasets':['adult'],'seeds':[7]}))
    e2=next(n for n in g2['nodes'] if n['type']=='experiment')
    assert e2['config']['kind']=='experiment' and e2['config']['case']=='class_weight_correction'
    assert e2['config']['datasets']==['adult'] and 'command' not in e2['config']


def case_unspecified_plan_real_dependencies_no_auto_run(client):
    p=create(client)
    i=idea(client,p,experiment_config={'kind':'experiment','command':[],'case':'not_implemented'})
    g=ok(adopted(client,i))
    assert g['revision']==1 and len(g['nodes'])==3 and len(g['edges'])==2
    roles={n['config']['role']:n for n in g['nodes']}
    assert set(roles)=={'Researcher','Engineer','Experimenter'}
    assert all(n['config']['kind']=='agent' and 'max_steps' not in n['config'] for n in g['nodes'])
    assert all(n['inputs'][0]['kind']=='idea' and n['inputs'][0]['id']==i['id'] for n in g['nodes'])
    h,eng,exp=(roles[role] for role in ('Researcher','Engineer','Experimenter'))
    assert {(e['source'],e['target']) for e in g['edges']}=={(h['id'],eng['id']),(eng['id'],exp['id'])}
    assert {ref['path'] for ref in exp['inputs'] if ref.get('path')}=={'hypothesis.md','implementation.py','experiment_plan.json'}
    assert ok(client.get(f"/api/projects/{p['id']}/runs"))==[]
    # Explicit user run creates one real durable task per node; missing produced
    # files are deferred until their producing task completes.
    scheduled=ok(client.post(f"/api/nodes/{exp['id']}/run",json={'scope':'ancestors','request_id':'run-adopted'}))
    runs=ok(client.get(f"/api/projects/{p['id']}/runs"))
    assert len(runs)==3 and all(r['kind']=='agent' for r in runs)
    by_node={r['node_id']:r for r in runs}
    assert by_node[h['id']]['dependencies']==[]
    assert by_node[eng['id']]['dependencies']==[by_node[h['id']]['id']]
    assert set(by_node[exp['id']]['dependencies'])=={by_node[h['id']]['id'],by_node[eng['id']]['id']}
    assert by_node[exp['id']]['config']['input_references']==exp['inputs']
    assert all(r['status']=='queued' for r in runs)


def case_atomic_validation_and_concurrent_receipt(client):
    p=create(client); i=idea(client,p)
    invalid=adopted(client,i,config={'case':'class_weight_correction','datasets':['invented']})
    assert invalid.status_code==422 and invalid.json()['detail']['code']=='INVALID_EXPERIMENT_CONFIG'
    assert graph(client,p)['revision']==0 and graph(client,p)['nodes']==[]
    assert ok(client.get(f"/api/ideas/{i['id']}"))['status']!='adopted'
    assert adopted(client,i,config=[]).status_code==422
    assert adopted(client,i,expected_revision=99).status_code==409
    other=create(client); foreign=idea(client,other)
    assert adopted(client,i,inputs=[{'kind':'idea','id':foreign['id']}]).status_code==422
    assert adopted(client,i,inputs=['../outside']).status_code==403
    assert graph(client,p)['nodes']==[]
    def concurrent(_): return adopted(client,i,request_id='one-adoption',expected_revision=0)
    with ThreadPoolExecutor(max_workers=5) as pool: results=list(pool.map(concurrent,range(5)))
    assert [r.status_code for r in results]==[200]*5,[(r.status_code,r.text) for r in results]
    assert graph(client,p)['revision']==1 and len(graph(client,p)['nodes'])==3
    from sqlalchemy import select,func
    from services.api.db import Session,CommandReceipt
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(CommandReceipt).where(CommandReceipt.project_id==p['id']))==1


def case_completed_producer_file_reused_but_stale_rejected(client):
    p=create(client); i=idea(client,p)
    g=ok(adopted(client,i,config={'kind':'command','command':['python','pilot.py']}))
    h=next(n for n in g['nodes'] if n['type']=='hypothesis')
    e=next(n for n in g['nodes'] if n['type']=='experiment')
    producer=ok(client.post(f"/api/nodes/{h['id']}/run",json={'request_id':'producer'}))
    from services.api.db import Session,TaskRun,Node,now
    from services.api.common import project_dir
    with Session.begin() as s:
        r=s.get(TaskRun,producer['id']); r.status='completed'; r.finished_at=now()
        n=s.get(Node,h['id']); n.execution_status='completed'; n.extra={**n.extra,'results_current':True}
        workspace=project_dir(p['id'])/r.output_path/'workspace'; workspace.mkdir(parents=True)
        (workspace/'hypothesis.md').write_text('Actual completed producer artifact')
    consumed=ok(client.post(f"/api/nodes/{e['id']}/run",json={'request_id':'single-consumer'}))
    assert consumed['dependencies']==[producer['id']]
    # A newer completed revision cannot borrow an older run's file.
    ok(command(client,p,'edit_node',[h['id']],instructions='New protocol revision'))
    rejected=client.post(f"/api/nodes/{e['id']}/run",json={'request_id':'stale-consumer'})
    assert rejected.status_code==409 and rejected.json()['detail']['code']=='INPUT_UNAVAILABLE'
    with Session.begin() as s:
        n=s.get(Node,h['id'])
        newer=TaskRun(project_id=p['id'],node_id=n.id,branch_id=n.branch_id,request_id='new-empty',kind='agent',status='completed',
            node_revision=n.revision,config={},dependencies=[])
        s.add(newer);s.flush();newer.output_path=f'runs/{newer.id}'
        n.extra={**n.extra,'results_current':True}
    missing=client.post(f"/api/nodes/{e['id']}/run",json={'request_id':'no-borrow'})
    assert missing.status_code==409 and missing.json()['detail']['code']=='INPUT_UNAVAILABLE'


CASES=[name.removeprefix('case_') for name in list(globals()) if name.startswith('case_')]


@pytest.mark.parametrize('case',CASES)
def test_isolated_idea_adoption(tmp_path,case):
    env={**os.environ,'FOREST_DATA_DIR':str(tmp_path/'data'),'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'adoption.sqlite'),
         'FOREST_OWNER_TOKEN':'adoption-test-owner','FOREST_MODEL':'','PYTHONPATH':str(ROOT)}
    result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--case',case],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    from fastapi.testclient import TestClient
    from services.api.main import app
    selected=sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client: globals()['case_'+selected](client)
