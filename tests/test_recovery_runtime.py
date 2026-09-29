"""Actual model fitting, process death, checkpoint recovery and receipt isolation."""
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

import pytest

from test_worker import Harness, wait_until
from research.execution.checkpoint import CheckpointStore
from research.execution.recovery import admission, resource_request

TRAIN = r'''
import json,math,os,random
from pathlib import Path
from research.execution.checkpoint import CheckpointStore
rng=random.Random(817)
xs=[[rng.uniform(-1,1) for _ in range(6)] for _ in range(200)]
ys=[int(2*x[0]-x[1]+.3*x[2]>.1) for x in xs]
store=CheckpointStore(); state=store.load({'step':0,'weights':[0.0]*6})
with Path('restores.jsonl').open('a') as f: f.write(json.dumps({'attempt':os.environ['FOREST_ATTEMPT_ID'],'step':state['step']})+'\n')
w=state['weights']
for step in range(state['step'],4000):
    grad=[0.0]*6
    for x,y in zip(xs,ys):
        p=1/(1+math.exp(-sum(a*b for a,b in zip(w,x))))
        for j in range(6): grad[j]+=(p-y)*x[j]
    w=[a-.04*b/len(xs) for a,b in zip(w,grad)]
    if (step+1)%40==0: store.save({'step':step+1,'weights':w},progress={'completed':step+1,'total':4000})
loss=sum(math.log1p(math.exp(-sum(a*b for a,b in zip(w,x)))) if y else math.log1p(math.exp(sum(a*b for a,b in zip(w,x)))) for x,y in zip(xs,ys))/len(xs)
Path('metrics.json').write_text(json.dumps({'weights':w,'log_loss':loss,'steps':4000}))
'''


@pytest.fixture(scope='module')
def recovery_worker(tmp_path_factory):
    h=Harness(tmp_path_factory.mktemp('recovery-worker'))
    try:
        h.start_api(); h.start_worker(); yield h
    finally: h.cleanup()


def training_run(h):
    project,node=h.project_node(seconds=1,budget={'allow_paid':False})
    h.request('PATCH',f"/api/nodes/{node['id']}",json={'config':{'kind':'command','command':[sys.executable,'-c',TRAIN], 'recovery':{'mode':'checkpoint','checkpoint':'train-state.json','max_attempts':3}}})
    run=h.launch(node); active=h.running(run)
    return project,node,run,active


def test_actual_training_checkpoint_recovers_after_process_kill(recovery_worker):
    h=recovery_worker
    _,_,reference,_=training_run(h)
    baseline=h.terminal(reference,timeout=35)
    assert baseline['status']=='completed',baseline
    _,_,run,active=training_run(h)
    state=h.output(run)/'workspace/train-state.json'
    wait_until(lambda: state.exists() and json.loads(state.read_text())['state']['step']>=80)
    killed_at=json.loads(state.read_text())['state']['step']
    assert killed_at < 4000
    os.killpg(os.getpgid(active['pid']),signal.SIGKILL)
    complete=h.terminal(run,timeout=35)
    assert complete['status']=='completed',complete
    for key in ('weights','log_loss','steps'):
        assert complete['metrics'][key]==baseline['metrics'][key]
    assert len(complete['resource']['attempts'])==2
    assert complete['resource']['attempts'][0]['status']=='interrupted'
    assert complete['resource']['attempts'][1]['mode']=='checkpoint'
    restores=[json.loads(x) for x in (h.output(run)/'workspace/restores.jsonl').read_text().splitlines()]
    assert restores[0]['step']==0 and 0<restores[1]['step']<4000
    assert restores[0]['attempt']!=restores[1]['attempt']
    assert (h.output(run)/'attempts/1').exists() is False  # No receipt existed for the killed process.
    assert json.loads((h.output(run)/'result.json').read_text())['attempt_id']==complete['config']['execution_attempt']['id']


def test_stale_completion_receipt_cannot_complete_new_attempt(recovery_worker):
    h=recovery_worker
    _,_,run,active=training_run(h)
    receipt=h.output(run)/'result.json'
    receipt.write_text(json.dumps({'attempt_id':'superseded-attempt','status':'completed','metrics':{'fabricated':1},'exit_code':0}))
    wait_until(lambda: any((h.output(run)/'attempts/stale').glob('*.json')) if (h.output(run)/'attempts/stale').exists() else False)
    result=h.terminal(run,timeout=35)
    assert result['status']=='completed'
    assert result['metrics']['steps']==4000 and 'fabricated' not in result['metrics']


def test_checkpoint_keeps_previous_valid_state_on_serialization_error(tmp_path):
    store=CheckpointStore(tmp_path/'state.json')
    store.save({'step':1}); store.save({'step':2})
    assert json.loads((tmp_path/'state.json.previous').read_text())['state']=={'step':1}
    with pytest.raises(ValueError): store.save({'loss':float('nan')})
    assert store.load()=={'step':2}


def test_resource_admission_enforces_explicit_requests():
    cap={'cpu':4,'memory_bytes':4*1024**3,'gpu':1}
    active=[resource_request({'resources':{'cpu':3,'memory_gb':1,'gpu':1}})]
    assert admission(resource_request({'resources':{'cpu':2}}),active,cap)==('wait','cpu')
    assert admission(resource_request({'resources':{'gpu':2}}),[],cap)==('impossible','gpu')
    assert admission(resource_request({}),active,cap)==('ready',None)


def test_remote_supervisor_program_writes_actual_command_receipt(tmp_path):
    # Execute the same standalone program used over SSH. This verifies its
    # process/receipt protocol, not availability of an SSH host.
    import subprocess
    from runners.remote import SUPERVISOR
    folder=tmp_path/'job'
    source="from pathlib import Path; import json; v=sum(1/(i*i) for i in range(1,100001)); Path('metrics.json').write_text(json.dumps({'series_sum':v})); print(v)"
    spec={'task_id':str(uuid.uuid4()),'folder':str(folder),'workdir':str(tmp_path),'argv':[sys.executable,'-c',source]}
    process=subprocess.Popen([sys.executable,'-c',SUPERVISOR,json.dumps(spec)],start_new_session=True)
    assert process.wait(timeout=15)==0
    receipt=json.loads((folder/'state.json').read_text())
    assert receipt['status']=='completed' and receipt['exit_code']==0
    assert receipt['pid']==process.pid and receipt['process_created']
    result=json.loads((tmp_path/'metrics.json').read_text())['series_sum']
    assert 1.6449<result<1.645
    assert str(result) in (folder/'stdout.txt').read_text()


def test_controller_schedules_explicit_input_producer_before_older_consumer(recovery_worker):
    h=recovery_worker
    project=h.request('POST','/api/projects',json={'name':'Explicit input scheduling','goal':'Compute the sum of the actual produced sequence','budget':{'allow_paid':False}})
    graph=h.request('GET',f"/api/projects/{project['id']}/graph")
    branch=graph['branches'][0]['id']; producer_id=str(uuid.uuid4()); consumer_id=str(uuid.uuid4())
    def add(ident,title,code,inputs):
        current=h.request('GET',f"/api/projects/{project['id']}/graph")
        return h.request('POST',f"/api/projects/{project['id']}/graph/commands",json={'request_id':str(uuid.uuid4()),'expected_revision':current['revision'],'operation':'add_node','targets':[],'params':{'id':ident,'branch_id':branch,'type':'experiment','title':title,'instructions':title,'inputs':inputs,'config':{'kind':'command','command':[sys.executable,'-c',code]}}})
    add(consumer_id,'Sum produced sequence',"import json; from pathlib import Path; xs=json.loads(Path('numbers.json').read_text()); Path('metrics.json').write_text(json.dumps({'sum':sum(xs)}))",[{'node_id':producer_id,'path':'numbers.json'}])
    add(producer_id,'Produce sequence',"import json; from pathlib import Path; Path('numbers.json').write_text(json.dumps(list(range(100))))",[])
    h.request('PATCH',f"/api/projects/{project['id']}",json={'config':{'controller':{'status':'running','autonomous':False}}})
    runs=wait_until(lambda: (rows if len(rows:=h.request('GET',f"/api/projects/{project['id']}/runs"))==2 and all(r['status']=='completed' for r in rows) else None),timeout=25)
    producer=next(r for r in runs if r['node_id']==producer_id); consumer=next(r for r in runs if r['node_id']==consumer_id)
    assert consumer['dependencies']==[producer['id']]
    assert consumer['metrics']['sum']==4950
    assert producer['finished_at']<=consumer['started_at']
