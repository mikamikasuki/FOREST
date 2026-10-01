"""Real numerical outputs and editable comparison/replanning contracts.

No model responses or scientific approval are substituted. Declaration equality
is tested separately from independent acceptance and scientific conclusions.
"""
from copy import deepcopy
import json
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from research.agents.defaults import VERSION_SEVEN_TOOLS, TOOLSET_VERSION, upgrade_default_tools
from research.agents.policy import ROLES, TOOLS, VERSION_SEVEN_ROLE_INSTRUCTIONS
from research.kernel import GraphCommandService
from research.planning.scoreboard import compare_trials
from research.validation.comparability import REQUIRED_FIELDS, assess_comparability, comparison_declaration


def signature():
    return {'dataset': 'quadratic-unit-interval', 'dataset_version': 'definition-1',
            'split': {'evaluation': [0, 1]}, 'evaluation_protocol': 'equal-width-quadrature',
            'metric': {'name': 'error', 'unit': 'absolute-error', 'aggregation': 'one-integration'},
            'statistical_unit': 'integration', 'budget': {'evaluations': 20}}


def calculation(midpoint=False):
    expression='sum(((i+.5)/n)**2 for i in range(n))/n' if midpoint else 'sum((i/n)**2 for i in range(n))/n'
    return [sys.executable, '-c', 'import json; from pathlib import Path; n=20; '
            f"estimate={expression}; Path('metrics.json').write_text(json.dumps({{'error':abs(estimate-1/3)}}))"]


def measured(tmp_path,ident,midpoint=False):
    directory=tmp_path/ident;directory.mkdir()
    subprocess.run(calculation(midpoint),cwd=directory,check=True,capture_output=True,text=True)
    return {'id':ident,'kind':'command','status':'completed','created_at':ident,
            'config':{'comparison_signature':signature()},
            'metrics':json.loads((directory/'metrics.json').read_text())}


def test_complete_real_measurements_rank_only_same_declared_scope(tmp_path):
    first,second=measured(tmp_path,'a'),measured(tmp_path,'b',True)
    assert second['metrics']['error']<first['metrics']['error']
    report=assess_comparability([first,second])
    assert report['directly_comparable'] and report['comparison_status']=='directly_comparable'
    rows=compare_trials([first,second],{'metric':'error','direction':'min'})
    assert [row['disposition'] for row in rows]==['baseline','improved']
    assert rows[1]['comparator_run_id']=='a'
    assert 'Declaration equality only' in report['verification_scope']


@pytest.mark.parametrize('field',REQUIRED_FIELDS)
def test_missing_declarations_never_become_equal_champions(tmp_path,field):
    first,second=measured(tmp_path,'a'),measured(tmp_path,'b',True)
    for run in (first,second):del run['config']['comparison_signature'][field]
    # The objective can explicitly select the metric; all other declarations
    # are still mandatory. Test an absent metric without such a selection.
    assert not assess_comparability([first,second])['directly_comparable']
    if field!='metric':
        rows=compare_trials([first,second],{'metric':'error','exploratory_grouping':True})
        assert all(row['disposition']=='unverified' and not row['comparison_eligible'] for row in rows)
        assert all(row['comparator_run_id'] is None for row in rows)


@pytest.mark.parametrize('field,replacement',[
    ('dataset','another-dataset'),('dataset_version','definition-2'),('split',{'evaluation':[0,2]}),
    ('evaluation_protocol','adaptive-quadrature'),('metric',{'name':'relative_error','unit':'fraction'}),
    ('statistical_unit','seed'),('budget',{'evaluations':40}),
])
def test_actual_changed_scope_is_incomparable_even_with_same_protocol_version(tmp_path,field,replacement):
    first,second=measured(tmp_path,'a'),measured(tmp_path,'b',True)
    first['config']['protocol_version']=second['config']['protocol_version']=1
    second['config']['comparison_signature'][field]=replacement
    report=assess_comparability([first,second])
    assert report['comparison_status']=='incomparable' and not report['directly_comparable']


def test_custom_objective_fields_are_additional_and_missing_is_not_none_group(tmp_path):
    first,second=measured(tmp_path,'a'),measured(tmp_path,'b',True)
    objective={'metric':'error','comparison_fields':['dataset','parameters.regularization']}
    assert not comparison_declaration(first,objective)['complete']
    first['config']['parameters']={'regularization':0}
    second['config']['parameters']={'regularization':1}
    assert assess_comparability([first,second],objective)['comparison_status']=='incomparable'
    second['config']['parameters']={'regularization':0}
    assert assess_comparability([first,second],objective)['directly_comparable']
    first['metrics']['parameters']={'regularization':2}
    assert 'parameters.regularization' in comparison_declaration(first,objective)['conflicting_fields']


def test_fair_signature_budget_and_metric_definition_do_not_conflict_with_execution_limits(tmp_path):
    first,second=measured(tmp_path,'a'),measured(tmp_path,'b',True)
    for run in (first,second):
        run['config'].update(budget={'seconds':90,'cost':.1},metric='error',
                             metric_definition={'unit':'absolute-error','aggregation':'one-integration'})
    assert assess_comparability([first,second],{'metric':'error'})['directly_comparable']
    first['config']['comparison_budget']={'evaluations':21}
    assert 'budget' in comparison_declaration(first)['conflicting_fields']


def test_execution_limits_alone_are_not_a_scientific_budget(tmp_path):
    first=measured(tmp_path,'a')
    del first['config']['comparison_signature']['budget']
    first['config']['budget']={'seconds':90,'cost':.1}
    assert 'budget' in comparison_declaration(first)['missing_fields']


def test_pending_duplicate_empty_and_invalid_comparisons_rejected(tmp_path):
    first,second=measured(tmp_path,'a'),measured(tmp_path,'b',True)
    second['status']='running'
    assert assess_comparability([first,second])['comparison_status']=='unverified'
    assert not assess_comparability([first])['directly_comparable']
    for runs in ([],[first,first]):
        with pytest.raises(ValueError):assess_comparability(runs)
    for objective in ([],{'comparison_fields':['dataset','dataset']},{'comparison_fields':['']}):
        with pytest.raises(ValueError):comparison_declaration(first,objective)


def test_agent_outputs_and_required_handoffs_cannot_win_without_actual_acceptance(tmp_path):
    first=measured(tmp_path,'a');first['kind']='agent'
    first['metrics']['verification_status']='accepted'  # self-authored, ignored
    row=compare_trials([first],{'metric':'error'})[0]
    assert row['disposition']=='unverified' and row['evidence_label']=='REPORTED'
    first['verification_status']='accepted'  # server observation supplied by caller
    assert not compare_trials([first],{'metric':'error'})[0]['comparison_eligible']
    first['numerical_verification']={'ready':True,'checked_pointers':['/error'],'missing_pointers':[]}
    assert compare_trials([first],{'metric':'error'})[0]['comparison_eligible']
    first['kind']='command';first['verification_status']='rejected';first['verification_policy']='required'
    assert not compare_trials([first],{'metric':'error'})[0]['comparison_eligible']


def test_exact_factory_migration_keeps_custom_roles_tools_and_instruction_edits():
    factory=SimpleNamespace(name='Engineer',role='Engineer',instructions=VERSION_SEVEN_ROLE_INSTRUCTIONS['Engineer'],
        tools=list(VERSION_SEVEN_TOOLS),config={'builtin_role':'Engineer','builtin_toolset_version':7},enabled=True,provider_id=None)
    for change in ({'instructions':'Author controlled research role'},{'tools':['read_file','finish']},
                   {'role':'Custom Scientist'},{'config':{**factory.config,'tools_customized':True}}):
        custom=deepcopy(factory)
        for name,value in change.items():setattr(custom,name,value)
        before=deepcopy(vars(custom))
        assert not upgrade_default_tools(custom) and vars(custom)==before
    assert upgrade_default_tools(factory)
    assert factory.instructions==ROLES['Engineer'] and factory.tools==TOOLS
    assert factory.config['builtin_toolset_version']==TOOLSET_VERSION==8
    assert 'verification_run' in factory.tools and not upgrade_default_tools(factory)
    assert {'Evidence Verifier','Open Source Research Advisor','Research Direction Reviewer'}<=set(ROLES)
    explicit=SimpleNamespace(name='Engineer',role='Engineer',instructions=VERSION_SEVEN_ROLE_INSTRUCTIONS['Engineer'],
        tools=list(VERSION_SEVEN_TOOLS),config={'builtin_role':'Engineer','builtin_toolset_version':7,
        'publication_profile':{'id':'operational'},'working_preference':'keep this'},enabled=True,provider_id=None)
    assert upgrade_default_tools(explicit)
    assert explicit.config['publication_profile']=={'id':'operational'} and explicit.config['working_preference']=='keep this'


def research_route(tmp_path):
    command=[sys.executable,'-c',"raise ValueError('actual failed arithmetic')"]
    graph={'project_id':'project','goal':'Compute a real quadratic integral','revision':0,
           'nodes':[{'id':'failed','project_id':'project','branch_id':'main','type':'experiment',
                     'title':'Original computation','instructions':'Compute the integral on the supplied interval.',
                     'revision':0,'config':{'kind':'command','command':command},'inputs':[],
                     'position':{'x':0,'y':0},'archived':False}],
           'edges':[],'branches':[{'id':'main','name':'Main','status':'active','workspace':'.','is_main':True}]}
    runs=[]
    for index in range(3):
        process=subprocess.run(command,cwd=tmp_path,capture_output=True,text=True)
        runs.append({'id':f'actual-failure-{index}','node_id':'failed','node_revision':0,'created_at':str(index),
                     'kind':'command','status':'failed' if process.returncode else 'completed','config':graph['nodes'][0]['config']})
    return graph,runs


def test_required_replanning_uses_actual_failures_and_real_semantic_change(tmp_path):
    from research.planning.loop import validate_replanning
    from research.planning.route_review import route_health
    graph,runs=research_route(tmp_path);health=route_health(graph,runs)
    assert health['replan_required']
    plan={'action':'continue','replanning':{'reason':'The current command raises an actual ValueError.',
          'new_direction':'Use a midpoint quadrature implementation on the same integration problem.',
          'goal_alignment':'The replacement measures the original integral without changing its target.',
          'stop_node_ids':['failed'],'evidence_run_ids':[runs[-1]['id']]}}
    service=GraphCommandService(graph,tmp_path)
    altered=service.apply({'operation':'edit_node','targets':['failed'],'expected_revision':0,
             'params':{'config':{'command':calculation(True)}}})['graph']
    report=validate_replanning(plan,graph,altered,runs,{'replan_required':True},health,graph['goal'])
    assert report['changed_scientific_node_ids']==['failed']
    renamed=service.apply({'operation':'edit_node','targets':['failed'],'expected_revision':0,
             'params':{'title':'Pretend this is a new direction','position':{'x':90,'y':20}}})['graph']
    with pytest.raises(ValueError,match='materially changed'):validate_replanning(plan,graph,renamed,runs,{'replan_required':True},health,graph['goal'])
    invalid=deepcopy(plan);invalid['replanning']['evidence_run_ids']=['not-a-real-run']
    with pytest.raises(ValueError,match='actual records'):validate_replanning(invalid,graph,altered,runs,{'replan_required':True},health,graph['goal'])
    invalid=deepcopy(plan);invalid['replanning']['stop_node_ids']=[]
    with pytest.raises(ValueError,match='affected route'):validate_replanning(invalid,graph,altered,runs,{'replan_required':True},health,graph['goal'])
    # Semantic findings from the direction reviewer must repair their cited
    # route even when deterministic repetition checks have no signal.
    with pytest.raises(ValueError,match='affected route'):
        validate_replanning(invalid,graph,altered,runs,{'replan_required':True,'route_replan_node_ids':['failed']},{'signals':[]},graph['goal'])


def test_excess_counterexample_tasks_cannot_replan_into_more_renamed_checks(tmp_path):
    from research.planning.loop import validate_replanning
    graph,runs=research_route(tmp_path)
    graph['nodes'][0]['config']['research_activity']='counterexample'
    health={'signals':[{'kind':'excessive_counterexample_checks','node_ids':['failed'],'run_ids':[run['id'] for run in runs]}]}
    plan={'action':'continue','replanning':{'reason':'Repeated checking no longer changes the decision.',
          'new_direction':'Run the real baseline comparison.','goal_alignment':'Measure the original target.',
          'stop_node_ids':['failed'],'evidence_run_ids':[runs[-1]['id']]}}
    after=deepcopy(graph);after['nodes'][0]['archived']=True
    extra=deepcopy(graph['nodes'][0]);extra.update(id='renamed-check',archived=False,title='Fresh check')
    extra['config']['command']=calculation(True);after['nodes'].append(extra)
    with pytest.raises(ValueError,match='counterexample'):validate_replanning(plan,graph,after,runs,{'replan_required':True},health,graph['goal'])
    extra['config']['research_activity']='baseline'
    assert validate_replanning(plan,graph,after,runs,{'replan_required':True},health,graph['goal'])['changed_scientific_node_ids']==['renamed-check']


def test_concrete_agent_instruction_repair_remains_editable(tmp_path):
    from research.planning.loop import validate_replanning
    graph,runs=research_route(tmp_path)
    graph['nodes'][0]['config']={'kind':'agent','role':'Engineer'}
    plan={'action':'continue','replanning':{'reason':'The recorded computation failed on the current numerical method.',
          'new_direction':'Implement midpoint integration with an explicit empty-input check.',
          'goal_alignment':'Repair the computation of the original quadratic integral.',
          'stop_node_ids':['failed'],'evidence_run_ids':[runs[-1]['id']]}}
    repaired=GraphCommandService(graph,tmp_path).apply({'operation':'edit_node','targets':['failed'],'expected_revision':0,
        'params':{'instructions':'Implement midpoint integration, validate nonempty input and execute the actual quadrature.'}})['graph']
    health={'signals':[{'node_ids':['failed'],'run_ids':[runs[-1]['id']]}]}
    assert validate_replanning(plan,graph,repaired,runs,{'replan_required':True},health,graph['goal'])['changed_scientific_node_ids']==['failed']


def test_context_compaction_does_not_reintroduce_unverified_global_champions(tmp_path):
    from research.planning.loop import compact_planning_context
    first=measured(tmp_path,'a');first['config']={'dataset':'quadratic-unit-interval','protocol_version':1}
    trials=compare_trials([first],{'metric':'error'})
    context={'project':{'goal':'Original goal '+('scientific context '*200),'objective':{'metric':'error'}},
             'graph':{'revision':0,'nodes':[],'edges':[],'branches':[]},'runs':[first],
             'trials':trials,'sources':[],'claims':[],
             'route_health':{'replan_required':True,'signals':[{'kind':'repeated_failure'}]}}
    view=compact_planning_context(context,4000)
    assert view['global_best_by_conditions']==[] and view['route_health']['replan_required']
    assert view['project']['goal']==context['project']['goal']


def test_actual_http_comparison_rejects_missing_metadata_and_cross_project_ids(tmp_path):
    from test_worker import Harness
    harness=Harness(tmp_path)
    try:
        harness.start_api();harness.start_worker()
        project,node=harness.project_node(seconds=.01)
        harness.request('PATCH',f"/api/nodes/{node['id']}",json={'config':{
            'kind':'command','command':calculation(),'comparison_signature':signature()}})
        first=harness.launch(node);assert harness.terminal(first)['status']=='completed'
        harness.request('PATCH',f"/api/nodes/{node['id']}",json={'config':{
            'kind':'command','command':calculation(True),'comparison_signature':signature()}})
        second=harness.launch(node);assert harness.terminal(second)['status']=='completed'
        path='/api/experiments/compare'
        assert harness.request('POST',path,json={'run_ids':[first['id'],second['id']]})['directly_comparable']
        for identifiers in ([],[first['id'],first['id']],['']):
            assert harness.client.post(path,json={'run_ids':identifiers}).status_code==422
        assert harness.client.post(path,json={'run_ids':[str(uuid.uuid4())]}).status_code==404
        other,other_node=harness.project_node(seconds=.01)
        foreign=harness.launch(other_node);assert harness.terminal(foreign)['status']=='completed'
        assert harness.client.post(path,json={'run_ids':[first['id'],foreign['id']]}).status_code==400
        harness.request('PATCH',f"/api/nodes/{node['id']}",json={'config':{'kind':'command','command':calculation(),'comparison_signature':{field:None for field in REQUIRED_FIELDS}}})
        missing=harness.launch(node);assert harness.terminal(missing)['status']=='completed'
        report=harness.request('POST',path,json={'run_ids':[first['id'],missing['id']]})
        assert report['comparison_status']=='unverified' and not report['directly_comparable']
        # A real accepted numerical check of /score must not rank the unchecked
        # /count objective. Only the current complete source-bound scope may do so.
        from test_research_verification_workflow import add_node
        guarded=harness.request('POST','/api/projects',json={
            'name':'Actual comparison scope','mode':'manual',
            'budget':{'max_runs':20,'seconds':120,'allow_paid':False},
            'config':{'verification_policy':'required','publication_profile':{'id':'operational'},
                      'objective':{'metric':'count','direction':'max'}}})
        declared=signature();declared['metric']={'name':'count','unit':'observations','aggregation':'total'}
        source_command=[sys.executable,'-c',"import json; from pathlib import Path; "
            "values=[i*i for i in range(1,501)]; "
            "Path('metrics.json').write_text(json.dumps({'score':sum(values),'count':len(values)}))"]
        producer=add_node(harness,guarded,type='experiment',title='Actual observations',
            config={'kind':'command','command':source_command,'comparison_signature':declared})
        produced=harness.launch(producer);assert harness.terminal(produced)['status']=='completed'
        verifier_config={'kind':'verification','command':[sys.executable,'-c',
            "import json; from pathlib import Path; n=500; "
            "Path('metrics.json').write_text(json.dumps({'score':n*(n+1)*(2*n+1)//6,'count':n}))"],
            'verification':{'producer_node_id':producer['id'],'checks':[{
                'id':'independent-sum','kind':'numeric_compare','source':'metrics.json',
                'repeat':'metrics.json','pointers':['/score'],'absolute_tolerance':0,'relative_tolerance':0}]}}
        verifier=add_node(harness,guarded,type='verification',title='Independent closed form',config=verifier_config)
        checked=harness.launch(verifier);assert harness.terminal(checked)['status']=='completed'
        state=harness.request('GET',f"/api/projects/{guarded['id']}/research")
        row=next(item for item in state['trials'] if item['run_id']==produced['id'])
        assert row['verification_status']=='accepted' and row['disposition']=='unverified'
        assert row['numerical_verification']['missing_pointers']==['/count']
        assert not row['comparison_eligible']
        verifier_config['verification']['checks'][0]['pointers']=['/score','/count']
        harness.request('PATCH',f"/api/nodes/{verifier['id']}",json={'config':verifier_config})
        rechecked=harness.launch(verifier);assert harness.terminal(rechecked)['status']=='completed'
        state=harness.request('GET',f"/api/projects/{guarded['id']}/research")
        row=next(item for item in state['trials'] if item['run_id']==produced['id'])
        assert row['numerical_verification']['ready'] and row['comparison_eligible']
        assert row['disposition']=='baseline' and row['numerical_verification']['checked_pointers']==['/count','/score']
        # Editable node policy also applies when the project policy is optional.
        # A superseded source cannot remain a permanent delivery requirement.
        harness.request('PATCH',f"/api/projects/{guarded['id']}",json={'config':{
            'verification_policy':'optional','publication_profile':{'id':'operational'},
            'objective':{'metric':'count','direction':'max'}}})
        harness.request('PATCH',f"/api/nodes/{producer['id']}",json={'config':{'verification_policy':'required'}})
        newer=harness.launch(producer);assert harness.terminal(newer)['status']=='completed'
        audit=harness.request('GET',f"/api/projects/{guarded['id']}/publication")
        assert not audit['ready'] and [item['run_id'] for item in audit['independent_verification']]==[newer['id']]
        current=harness.launch(verifier);assert harness.terminal(current)['status']=='completed'
        audit=harness.request('GET',f"/api/projects/{guarded['id']}/publication")
        assert audit['ready'] and [item['run_id'] for item in audit['independent_verification']]==[newer['id']]
    finally:harness.cleanup()
