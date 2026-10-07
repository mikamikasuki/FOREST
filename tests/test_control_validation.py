"""Control validation through actual API transactions and durable run snapshots."""
import json
import sys
import uuid

from tests.test_intervention_worker import intervention_harness
from tests.test_research_verification_workflow import add_node
from tests.test_worker import wait_until


def test_invalid_controls_rollback_and_precise_bindings_stay_project_scoped(intervention_harness):
    h=intervention_harness;h.start_api()
    project=h.request('POST','/api/projects',json={'name':'Typed control boundaries'})
    pid=project['id']
    for budget in ({'seconds':-1},{'seconds':'3'},{'max_runs':True},{'cost_usd':-0.1},{'allow_paid':'false'}):
        response=h.client.patch('/api/projects/'+pid,json={'budget':budget})
        assert response.status_code==422,response.text
        assert h.request('GET','/api/projects/'+pid)['revision']==0
        response=h.client.post('/api/projects',json={'name':'Invalid budget','budget':budget})
        assert response.status_code==422,response.text
    for tools in (None,'read_file',['unknown_tool'],['read_file','read_file']):
        response=h.client.patch('/api/projects/'+pid,json={'config':{'tools':tools}})
        assert response.status_code==422,response.text
    node=add_node(h,project,config={'kind':'command','command':[sys.executable,'-c','print(1)']})
    other=h.request('POST','/api/projects',json={'name':'Other source'})
    other_node=add_node(h,other,config={'kind':'command','command':[sys.executable,'-c','print(1)']})
    for binding in ({'source_kind':'node','source_id':other_node['id'],'target_path':'/paragraphs/3'},
                    {'source_kind':'run','source_id':'missing','target_path':'/paragraphs/3'},
                    {'source_kind':'file','source_id':'../outside.csv','target_path':'/abstract'},
                    {'source_kind':'node','source_id':node['id'],'target_path':'no-pointer'}):
        response=h.client.post('/api/claims',json={'project_id':pid,'data':{'dependency_bindings':[binding]}})
        assert response.status_code in (400,403,422),response.text
    claim=h.request('POST','/api/claims',json={'project_id':pid,'data':{'dependency_bindings':[
        {'source_kind':'node','source_id':node['id'],'target_path':'/paragraphs/3','metric_pointer':'/score'}]}})
    graph=h.request('GET','/api/projects/'+pid+'/graph')
    h.request('PATCH','/api/nodes/'+node['id'],json={'expected_revision':graph['revision'],'instructions':'New source condition'})
    stale=h.request('GET','/api/claims/'+claim['id'])
    assert stale['status']=='needs_update'
    locations=stale['data']['stale_dependencies']
    assert any(row['target_path']=='/paragraphs/3' and row['metric_pointer']=='/score' for row in locations)


def test_role_provider_priority_and_historical_retry_use_real_saved_snapshot(intervention_harness):
    h=intervention_harness;h.start_api()
    providers=[h.request('POST','/api/providers',json={'name':name,'kind':'ollama',
        'base_url':'http://127.0.0.1:1','model':name,'allow_paid':False}) for name in ('global','project','role','node','override')]
    h.request('PATCH','/api/settings',json={'default_provider_id':providers[0]['id']})
    project=h.request('POST','/api/projects',json={'name':'Role selection','config':{'provider_id':providers[1]['id']},'budget':{'max_runs':20}})
    agent=next(a for a in h.request('GET','/api/agents') if a['role']=='Engineer')
    h.request('PATCH','/api/agents/'+agent['id'],json={'provider_id':providers[2]['id']})
    node=add_node(h,project,config={'kind':'agent','role':'Engineer','tools':['finish']})
    def launch(source,provider,overrides=None):
        run=h.request('POST','/api/nodes/'+node['id']+'/run',json={'config':overrides or {}})
        assert run['config']['provider_selection']['provider_id']==provider['id'],run
        assert run['config']['provider_selection']['source']==source,run
        return run
    role=launch('agent',providers[2])
    graph=h.request('GET','/api/projects/'+project['id']+'/graph')
    h.request('PATCH','/api/nodes/'+node['id'],json={'expected_revision':graph['revision'],'config':{'provider_id':providers[3]['id']}})
    launch('node',providers[3]);launch('run',providers[4],{'provider_id':providers[4]['id']})
    h.control_request('/api/runs/'+role['id']+'/cancel',json={})
    old=h.request('POST','/api/runs/'+role['id']+'/retry',json={})
    assert old['config']['provider_selection']['provider_id']==providers[2]['id']
    assert old['config']['provider_selection']['source']=='historical_run'
    assert old['node_revision']==role['node_revision']
    invalid=h.client.post('/api/nodes/'+node['id']+'/run',json={'config':{'provider_id':'missing'}})
    assert invalid.status_code==422,invalid.text
    invalid=h.client.patch('/api/agents/'+agent['id'],json={'provider_id':'missing'})
    assert invalid.status_code==422,invalid.text


def test_project_budget_reduction_stops_actual_process_without_erasing_usage(intervention_harness):
    h=intervention_harness;h.start_api();h.start_worker()
    project,node=h.project_node(seconds=20,timeout=40)
    run=h.launch(node);running=h.running(run)
    wait_until(lambda:(h.output(run)/'heartbeat.txt').exists(),timeout=15)
    changed=h.request('PATCH','/api/projects/'+project['id'],json={'budget':{'seconds':0,'max_runs':20,'allow_paid':False}})
    assert changed['intervention']['impact']['configuration_saved']
    stopped=h.terminal(run,timeout=20)
    assert stopped['status']=='failed',stopped
    assert 'exceeded' in str(stopped['error']).lower() and 'budget' in str(stopped['error']).lower(),stopped
    assert stopped['resource']['elapsed_seconds']>0
    import psutil
    assert not psutil.pid_exists(running['pid']) or psutil.Process(running['pid']).status()==psutil.STATUS_ZOMBIE
    denied=h.client.post('/api/nodes/'+node['id']+'/run',json={})
    assert denied.status_code==409,denied.text
