"""Actual HTTP/process verification admission and artifact-scope regressions."""
import json
import subprocess
import sys

import pytest

from test_worker import Harness, wait_until
from test_research_verification_workflow import add_node, arithmetic_command, verification_config


@pytest.fixture(scope='module')
def verification_worker(tmp_path_factory):
    harness=Harness(tmp_path_factory.mktemp('verification-service'))
    try:
        harness.start_api(); harness.start_worker()
        yield harness
    finally:
        harness.cleanup()


def project(h, required=False):
    return h.request('POST','/api/projects',json={'name':'Scoped actual verification','mode':'manual',
        'budget':{'max_runs':30,'seconds':120,'allow_paid':False},
        'config':{'publication_profile':{'id':'operational'},'verification_policy':'required' if required else 'optional'}})


def produced(h,p,n=500):
    node=add_node(h,p,type='experiment',title='Actual sum',config={'kind':'experiment','command':arithmetic_command(n)})
    run=h.launch(node); assert h.terminal(run)['status']=='completed'
    return node,run


def verified(h,p,node,n=500):
    verifier=add_node(h,p,type='verification',title='Actual independent formula',config=verification_config(node,n))
    run=h.launch(verifier); assert h.terminal(run)['status']=='completed'
    assert h.request('GET',f"/api/runs/{run['id']}/verification")['verification_status']=='accepted'
    return verifier,run


def waiting(h,run):
    return wait_until(lambda:(value if (value:=h.run(run))['status']=='waiting_input' else None))


def test_both_artifacts_edited_cannot_reuse_execution(verification_worker):
    h=verification_worker; p=project(h); node,source=produced(h,p); verifier,checked=verified(h,p,node)
    for run in (source,checked):
        subprocess.run(arithmetic_command(501),cwd=h.output(run)/'workspace',check=True)
    value=h.request('GET',f"/api/runs/{source['id']}/verification")
    assert value['verification_status']=='inconclusive',value
    assert value['verifications'][0]['checks'][0]['status']=='accepted'
    assert 'changed' in value['verifications'][0]['reason']


def test_unrelated_accepted_verifier_cannot_authorize_input(verification_worker):
    h=verification_worker; p=project(h); first,source=produced(h,p); other,_=produced(h,p,10)
    verifier,_=verified(h,p,other,10)
    consumer=add_node(h,p,type='analysis',title='Consume actual first output',
        config={'kind':'command','required_verification':[verifier['id']],
                'command':[sys.executable,'-c',"from pathlib import Path; Path('should-not-run.txt').write_text('ran')"]},
        inputs=[{'node_id':first['id'],'path':'metrics.json','destination':'input.json'}])
    run=h.launch(consumer); stopped=waiting(h,run)
    assert stopped['resource']['blocked_reason']=='verification_required',stopped
    assert not (h.output(run)/'workspace'/'should-not-run.txt').exists()
    h.request('POST',f"/api/runs/{run['id']}/cancel",json={})


def test_csv_check_does_not_authorize_unchecked_metrics_or_paper(verification_worker):
    h=verification_worker; p=project(h,required=True)
    producer=add_node(h,p,type='experiment',title='Actual observations and measured result',config={
        'kind':'experiment','command':[sys.executable,'-c',"import json; from pathlib import Path; "
            "Path('observations.csv').write_text('unit,value\\na,1\\nb,2\\n'); "
            "Path('metrics.json').write_text(json.dumps({'score':3}))"]})
    source=h.launch(producer); assert h.terminal(source)['status']=='completed'
    verifier=add_node(h,p,type='verification',title='Observation integrity only',config={
        'kind':'verification','verification':{'producer_node_id':producer['id'],'checks':[{
            'id':'source-rows','kind':'csv_integrity','source':'observations.csv',
            'required_columns':['unit','value'],'unique_by':['unit'],'numeric_columns':['value']}]}})
    checked=h.launch(verifier); assert h.terminal(checked)['status']=='completed'
    result=h.request('GET',f"/api/runs/{source['id']}/verification")
    assert result['verification_status']=='accepted',result
    assert result['check_scope_paths']==['observations.csv'] and result['numerical_scope']==[]
    consumer=add_node(h,p,type='analysis',title='Unchecked numerical input',
        config={'kind':'command','command':['true']},inputs=[{'node_id':producer['id'],'path':'metrics.json'}])
    run=h.launch(consumer); assert waiting(h,run)['resource']['blocked_reason']=='verification_scope'
    h.request('POST',f"/api/runs/{run['id']}/cancel",json={})
    paper=h.request('POST',f"/api/papers/{p['id']}/generate",json={'run_ids':[source['id']]})
    stopped=waiting(h,paper)
    assert stopped['resource']['blocked_reason']=='verification_scope',stopped
    assert not (h.output(paper)/'paper').exists()
    h.request('POST',f"/api/runs/{paper['id']}/cancel",json={})


def test_numeric_check_requires_actual_command_not_prepared_pass(verification_worker):
    h=verification_worker; p=project(h); producer,source=produced(h,p)
    config=verification_config(producer); config.pop('command')
    verifier=add_node(h,p,type='verification',title='No actual rerun',config=config)
    run=h.launch(verifier); assert h.terminal(run)['status']=='completed'
    value=h.request('GET',f"/api/runs/{run['id']}/verification")
    assert value['verification_status']=='inconclusive',value
    assert 'execution receipt' in value['reason']
    spoof=h.client.post('/api/verification/run',json={'project_id':p['id'],**verification_config(producer),
        'verification_status':'accepted'})
    assert spoof.status_code==422,spoof.text


def test_standalone_verification_current_config_and_idempotency(verification_worker):
    h=verification_worker; p=project(h); producer,source=produced(h,p)
    body={'project_id':p['id'],'request_id':'standalone-actual',**verification_config(producer)}
    run=h.request('POST','/api/verification/run',json=body); assert h.terminal(run)['status']=='completed'
    assert h.request('GET',f"/api/runs/{source['id']}/verification")['verification_status']=='accepted'
    assert h.request('POST','/api/verification/run',json=body)['id']==run['id']
    conflict={**body,'verification':{**body['verification'],'checks':[{**body['verification']['checks'][0],'pointers':['/different']}]}}
    assert h.client.post('/api/verification/run',json=conflict).status_code==409
    h.request('PATCH',f"/api/runs/{run['id']}/configuration",json={'verification':conflict['verification']})
    assert h.request('GET',f"/api/runs/{run['id']}/verification")['verification_status']=='inconclusive'


def test_deleted_producer_is_inconclusive(verification_worker):
    h=verification_worker; p=project(h); producer,source=produced(h,p); verifier,checked=verified(h,p,producer)
    graph=h.request('GET',f"/api/projects/{p['id']}/graph")
    h.request('POST',f"/api/projects/{p['id']}/graph/commands",json={
        'expected_revision':graph['revision'],'request_id':'delete-checked-producer','operation':'delete_node',
        'targets':[producer['id']],'params':{'strategy':'reconnect'}})
    state=h.request('GET',f"/api/runs/{checked['id']}/verification")
    assert state['verification_status']=='inconclusive',state


def test_partial_numeric_scope_does_not_authorize_whole_json(verification_worker):
    h=verification_worker; p=project(h,required=True)
    producer=add_node(h,p,type='experiment',title='Multiple actual numerical fields',config={
        'kind':'experiment','command':[sys.executable,'-c',"import json; from pathlib import Path; "
            "Path('metrics.json').write_text(json.dumps({'score':41791750,'unchecked_effect':999}))"]})
    source=h.launch(producer); assert h.terminal(source)['status']=='completed'
    verifier,checked=verified(h,p,producer)
    state=h.request('GET',f"/api/runs/{source['id']}/verification")
    assert state['numerical_scope'][0]['pointers']==['/score']
    paper=h.request('POST',f"/api/papers/{p['id']}/generate",json={'run_ids':[source['id']]})
    stopped=waiting(h,paper)
    assert stopped['resource']['blocked_reason']=='verification_scope'
    assert stopped['resource']['verification_gate']['numerical_coverage']['missing_pointers']==['/unchecked_effect']
    h.request('POST',f"/api/runs/{paper['id']}/cancel",json={})
    consumer=add_node(h,p,type='analysis',title='Whole JSON carries unchecked fields',
        config={'kind':'command','command':['true'],'required_verification':[verifier['id']]},
        inputs=[{'node_id':producer['id'],'path':'metrics.json'}])
    run=h.launch(consumer)
    assert waiting(h,run)['resource']['verification_gate']['numerical_coverage']['missing_pointers']==['/unchecked_effect']
    h.request('POST',f"/api/runs/{run['id']}/cancel",json={})


def test_reverified_edited_file_cannot_promote_old_recorded_numbers(verification_worker):
    h=verification_worker; p=project(h,required=True); producer,source=produced(h,p)
    subprocess.run(arithmetic_command(501),cwd=h.output(source)/'workspace',check=True)
    verifier,checked=verified(h,p,producer,501)
    paper=h.request('POST',f"/api/papers/{p['id']}/generate",json={'run_ids':[source['id']]})
    stopped=waiting(h,paper)
    coverage=stopped['resource']['verification_gate']['numerical_coverage']
    assert coverage['missing_pointers']==[] and coverage['file_metrics_match'] is False
    assert coverage['recorded_metric_mismatches']==['/score']
    h.request('POST',f"/api/runs/{paper['id']}/cancel",json={})


def test_post_execution_source_configuration_change_requires_new_run(verification_worker):
    h=verification_worker; p=project(h); producer,source=produced(h,p); verifier,checked=verified(h,p,producer)
    h.request('PATCH',f"/api/runs/{source['id']}/configuration",json={'seed':9,'dataset':'different evaluation'})
    changed=h.request('GET',f"/api/runs/{source['id']}/verification")
    assert changed['verification_status']=='inconclusive',changed
    current=h.run(source)
    assert current['resource']['configuration_changed_after_execution'] is True
    rechecked=h.launch(verifier)
    stopped=waiting(h,rechecked)
    assert stopped['resource']['blocked_reason']=='verification_source_changed',stopped
    h.request('POST',f"/api/runs/{rechecked['id']}/cancel",json={})
