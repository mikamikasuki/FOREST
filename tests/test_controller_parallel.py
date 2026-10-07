"""Actual independent subprocess overlap and DAG barriers on both databases."""
import json
from pathlib import Path
import sys
import uuid

import pytest
from tests.test_worker import wait_until
from tests.test_intervention_worker import intervention_harness
from tests.test_research_verification_workflow import add_node

@pytest.mark.parametrize('slots',[1,2])
def test_controller_bounded_independent_tasks_and_writer_verifier_barrier(tmp_path,intervention_harness,slots):
    h=intervention_harness
    h.env['FOREST_WORKER_CONCURRENCY']='2'
    h.start_api()
    project=h.request('POST','/api/projects',json={'name':'Independent ready work','mode':'manual',
        'goal':'Execute independent writes then verify a completed producer',
        'config':{'route_review':{'enabled':False},'publication_profile':'operational'},
        'budget':{'seconds':180,'max_runs':10,'allow_paid':False}})
    nodes=[]
    command=[sys.executable,'-c',"import time,json;from pathlib import Path;start=time.time();time.sleep(2.5);Path('metrics.json').write_text(json.dumps({'start':start,'end':time.time(),'score':1}))"]
    for index in range(3):
        nodes.append(add_node(h,project,type='experiment',title='Actual independent '+str(index),config={'kind':'command','command':command,'timeout':20}))
    verifier=add_node(h,project,type='verification',title='Writer completion barrier',config={'kind':'verification',
        'command':[sys.executable,'-c',"import json,time;from pathlib import Path;data=json.loads(Path('source.json').read_text());assert data['score']==1;Path('metrics.json').write_text(json.dumps({'start':time.time(),'score':data['score']}))"],
        'verification':{'producer_node_id':nodes[0]['id'],'checks':[{'id':'actual-score','kind':'numeric_compare','source':'metrics.json','repeat':'metrics.json','pointers':['/score'],'absolute_tolerance':0,'relative_tolerance':0}]}},
        inputs=[{'node_id':nodes[0]['id'],'path':'metrics.json','destination':'source.json'}])
    for invalid in (0,33,True,'2'):
        response=h.client.post('/api/projects/'+project['id']+'/research/start',json={'ready_parallelism':invalid})
        assert response.status_code==422,response.text
    started=h.request('POST','/api/projects/'+project['id']+'/research/start',json={'autonomous':False,'ready_parallelism':slots})
    assert started['ready_parallelism']==slots
    h.start_worker()
    def finished():
        runs=h.request('GET','/api/projects/'+project['id']+'/runs')
        return runs if len(runs)==4 and all(r['status']=='completed' for r in runs) else None
    runs=wait_until(finished,timeout=65)
    assert len({r['node_id'] for r in runs})==4
    samples={r['node_id']:json.loads((h.output(r)/'workspace'/'metrics.json').read_text()) for r in runs}
    events=sorted([(samples[n['id']]['start'],1) for n in nodes]+[(samples[n['id']]['end'],-1) for n in nodes])
    active=peak=0
    for timestamp,delta in events: active+=delta;peak=max(peak,active)
    assert peak==slots,{'peak':peak,'slots':slots,'samples':samples}
    assert samples[verifier['id']]['start']>=samples[nodes[0]['id']]['end']
    checked=next(r for r in runs if r['node_id']==verifier['id'])
    assert checked['resource']['verification_status']=='accepted',checked
    wait_until(lambda: h.request('GET','/api/projects/'+project['id']+'/research?overview=true')['controller']['status']=='completed',timeout=15)
    # Repeating a controller stop with nonempty UI parameters must retain its
    # original identity even though no active targets remain on the repeat.
    body={'request_id':str(uuid.uuid4()),'branch_id':None,'autonomous':False,'ready_parallelism':slots}
    stop=h.request('POST','/api/projects/'+project['id']+'/research/stop',json=body)
    repeated=h.request('POST','/api/projects/'+project['id']+'/research/stop',json=body)
    assert stop['intervention']['id']==repeated['intervention']['id']
    (tmp_path/'observed_intervals.json').write_text(json.dumps(samples,indent=2))
