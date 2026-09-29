"""Real model proposals, checked against the current editable graph before adoption."""
import json
from pathlib import Path
from sqlalchemy import select
from services.api.db import Session, Project, TaskRun, SourcePaper, ResearchClaim, Hypothesis, uid, now, asdict
from services.api.common import get, graph_from_db, save_graph, project_dir, emit, safe_path
from research.kernel import GraphCommandService

PLANNER_INSTRUCTIONS = '''An example COMMAND SHAPE (replace branch_id and task details using current context): {"operation":"add_node","targets":[],"params":{"id":"baseline-implementation","branch_id":"ACTUAL_BRANCH_ID","type":"implementation","title":"Implement and measure baseline","instructions":"Implement the specified real task, run it, read measured results and write metrics.json and report.md.","config":{"kind":"agent","role":"Engineer","required_outputs":["metrics.json","report.md"]},"position":{"x":0,"y":0}}}. The commands field contains command OBJECTS, never just node titles. No work executes when commands is empty.
Context uses an automatically selected working set: inspect context_coverage before inferring that work or evidence is absent. Counts describe the complete project; omitted records remain in full_context_artifact and can be read now through read_context_segment. Read every required control page before submitting a plan; full project goals and constraints retain their original authority. Trial dispositions and global best summaries refer to the entire measured history, not only displayed runs.
You plan the next real research work in an editable project. Establish a strong baseline before candidate comparisons. Follow measured results, investigate failures, propose ablations and cheap discriminating experiments, and keep all contrary evidence. Never substitute a bundled example for the user's question. Every experiment has a duty: effectiveness, mechanism, scenario_value or alternative_explanation. No simulated providers, numbers, progress or completion. All files and research choices remain editable. Work in English.
Return JSON {"action":"continue|completed|blocked", "rationale":"evidence-grounded decision", "evidence_run_ids":[], "best_estimate":"...", "against":"...", "decisive_unknown":"...", "cheapest_resolution":"...", "commands":[]}. Commands use {"operation":"add_node|edit_node|add_dependency|fork_branch|prune_branch|set_main_branch", "targets":[], "params":{}}. For add_node supply id (unique descriptive string), branch_id from the graph, type, title, instructions, config, inputs and position:{x,y}. config for research steps uses kind:'agent', role:'Researcher|Engineer|Experimenter|Analyst|Writer' and expected_outputs when known. For executable existing scripts use kind:'experiment', command:argv, metrics_file:'metrics.json', experiment_duty, and input references to producer node files. Never invent executable paths that have not been produced. Add depends_on edges via add_dependency params {source:id,target:id}. Use inputs [{node_id:id,path:'file',destination:'file',branch_id:id}] to consume actual producer files. Engineer writes code and checkpoint support, Experimenter runs it with start_process and wait_for_process and saves predictions/metrics, Analyst independently recalculates. Use a few useful nodes at a time; do not create empty tasks to inflate graph size. Completed requires achieved goal and cited completed measured runs with required deliverables present, not simply an empty queue. Blocked must state an exact missing input. A failed attempt is evidence for repair or a different path, never successful research. Do not silently replace existing working files or discard results.'''


def measurement_excerpt(value, limit=3200):
    """Retain actual scalar observations while explicitly marking omitted arrays."""
    omitted=[]
    def visit(item,path,depth=0):
        if isinstance(item,dict):
            result={}
            # Scalar metrics must not disappear behind per-example predictions.
            for key,child in sorted(item.items(),key=lambda pair:isinstance(pair[1],(dict,list))):
                if len(json.dumps(result,ensure_ascii=False,default=str))>limit or depth>=5:
                    omitted.append(path+'/'+str(key)); continue
                result[key]=visit(child,path+'/'+str(key),depth+1)
            return result
        if isinstance(item,list):
            if len(json.dumps(item,ensure_ascii=False,default=str))>400:
                omitted.append(path); return {'omitted_array_length':len(item)}
            return [visit(x,path+'/'+str(i),depth+1) for i,x in enumerate(item)]
        if isinstance(item,str) and len(item)>600:
            omitted.append(path); return item[:600]
        return item
    result={'values':visit(value,'')}
    if omitted: result['omitted_paths']=omitted; result['complete']=False
    else: result['complete']=True
    return result


def run_artifact_observations(run,root):
    if run.status!='completed': return []
    artifacts=[]
    names=list(dict.fromkeys([run.config.get('metrics_file','metrics.json'),*(run.config.get('required_outputs') or run.config.get('expected_outputs') or [])]))
    for name in names[:6]:
        if not isinstance(name,str): continue
        try:
            path=safe_path(root,run.output_path+'/workspace/'+name)
            item={'path':str(path.relative_to(root)),'exists':path.is_file()}
            if path.is_file():
                item['bytes']=path.stat().st_size
                if path.suffix.lower() in ('.md','.txt'):
                    with path.open(encoding='utf-8',errors='replace') as stream: excerpt=stream.read(1601)
                    item.update(text_excerpt=excerpt[:1600],complete=len(excerpt)<=1600)
            artifacts.append(item)
        except (OSError,ValueError): continue
    return artifacts


def planning_context(project_id):
    with Session() as s:
        p=get(s,Project,project_id); graph=graph_from_db(s,p); graph.pop('_history',None)
        runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==p.id).order_by(TaskRun.created_at.desc())))
        from .scoreboard import compare_trials
        trial_rows=compare_trials([{'id':r.id,'kind':r.kind,'status':r.status,'metrics':r.metrics,'config':r.config,'created_at':r.created_at} for r in runs],p.config.get('objective'))
        return {'trials':trial_rows,'project':{'id':p.id,'goal':p.goal,'budget':p.budget,'controller':p.config.get('controller',{}),'objective':p.config.get('objective'),'planner_context_chars':p.config.get('planner_context_chars',24000)},'graph':graph,
                'runs':[{'id':r.id,'node_id':r.node_id,'status':r.status,'kind':r.kind,'metrics':r.metrics,'error':r.error,'output_path':r.output_path,
                         'artifact_observations':run_artifact_observations(r,project_dir(p.id)) if i<12 else []} for i,r in enumerate(runs)],
                'sources':[{'id':x.id,'title':x.title,'data':x.data} for x in s.scalars(select(SourcePaper).where(SourcePaper.project_id==p.id))],
                'claims':[asdict(x) for x in s.scalars(select(ResearchClaim).where(ResearchClaim.project_id==p.id))]}


def compact_planning_context(context, char_budget=24000):
    """Select an optional evidence working set, retaining complete project intent.

    Trial classifications are computed over the complete history before this
    presentation filter. Omitted records are counted, never relabeled. The
    legacy character setting is a soft hint; large mandatory metadata is paged
    by planning.model instead of being shortened or rejected here.
    """
    from collections import Counter
    from copy import deepcopy
    budget=max(4000,int(char_budget or 24000))
    encoded=lambda value: json.dumps(value,ensure_ascii=False,default=str)
    size=lambda value: len(encoded(value))
    graph=context['graph']; nodes=graph.get('nodes',[]); runs=context.get('runs',[]); trials=context.get('trials',[])
    totals={'nodes':len(nodes),'edges':len(graph.get('edges',[])),'branches':len(graph.get('branches',[])),
            'runs':len(runs),'trials':len(trials),'sources':len(context.get('sources',[])),'claims':len(context.get('claims',[]))}
    coverage={'complete':True,'total':totals,'included':dict(totals),'omitted':{k:0 for k in totals},'full_context_artifact':context.get('full_context_artifact'),
              'working_set_hint_chars':budget,'mandatory_project_complete':True}
    full={**context,'context_coverage':coverage}
    if size(full)<=budget: return full
    coverage['complete']=False
    project=deepcopy(context['project']); control=project.get('controller',{})
    result={'project':project,'graph':{'revision':graph['revision'],'nodes':[],'edges':[],'branches':[]},
            'graph_summary':{'execution_status_counts':dict(Counter(n.get('execution_status','idle') for n in nodes))},
            'runs':[],'trials':[],'sources':[],'claims':[],'global_best_by_conditions':[], 'context_coverage':coverage}
    # Build champions from all measured trials, not the display window.
    champions={}; direction=(project.get('objective') or {}).get('direction','min')
    for trial in trials:
        if trial.get('value') is None: continue
        key=encoded(trial.get('conditions',{})); old=champions.get(key)
        if old is None or (trial['value']<old['value'] if direction=='min' else trial['value']>old['value']): champions[key]=trial
    best=list(champions.values()); best_ids={x['run_id'] for x in best}
    recent_ids={r['id'] for r in runs[:6]}
    run_by_id={r['id']:r for r in runs}
    evidence_nodes={run_by_id[rid].get('node_id') for rid in best_ids|recent_ids if rid in run_by_id}
    selected=control.get('branch_id')
    projected_size=size(result)
    def add(target,key,item,section_limit=None):
        nonlocal projected_size
        collection=target[key]
        item_size=size(item)+2
        if section_limit is not None and size(collection)+item_size>section_limit: return False
        if projected_size+item_size>budget-500: return False
        collection.append(item)
        projected_size+=item_size
        return True
    for branch in sorted(graph.get('branches',[]),key=lambda b:(b['id']!=selected,not b.get('is_main',False))):
        add(result['graph'],'branches',{k:branch.get(k) for k in ('id','name','status','is_main')},budget//10)
    for trial in best:
        add(result,'global_best_by_conditions',{'run_id':trial['run_id'],'metric':trial['metric'],'value':trial['value'],'conditions':trial.get('conditions',{}),'scope':'entire_project_history'},budget//8)
    ordered_trials=sorted(trials,key=lambda t:(t['run_id'] not in best_ids,t['run_id'] not in recent_ids,t.get('disposition')!='baseline'))
    for trial in ordered_trials: add(result,'trials',trial,budget//6)
    ordered_runs=sorted(enumerate(runs),key=lambda item:(item[1]['id'] not in best_ids|recent_ids,item[0]))
    for _,run in ordered_runs:
        card={k:run.get(k) for k in ('id','node_id','status','kind','output_path')}
        if run.get('error'): card['error']=str(run['error'])[:400]
        # Numeric objectives live in trials. Large per-example measurements are
        # retrievable from the full context and actual output path.
        if size(run.get('metrics',{}))<=800: card['metrics']=run.get('metrics',{})
        else:
            metrics=run.get('metrics',{})
            measurements=metrics.get('observed_metrics',metrics)
            card['measurement_excerpt']=measurement_excerpt(measurements)
            if metrics.get('metrics_file'): card['metrics_file']=metrics['metrics_file']
            if metrics.get('metrics_evidence'): card['measurement_provenance']=metrics['metrics_evidence']
            card['metrics_omitted']=True
        if run.get('artifact_observations'): card['artifact_observations']=run['artifact_observations']
        add(result,'runs',card,budget//4)
    def priority(node):
        pending=node.get('execution_status','idle')!='completed' or node.get('deliverable_status')=='needs_update'
        return (node['id']!=control.get('current_node'),not pending,selected is not None and node.get('branch_id')!=selected,node['id'] not in evidence_nodes)
    for node in sorted(nodes,key=priority):
        card={k:node.get(k) for k in ('id','branch_id','type','revision','execution_status','deliverable_status')}
        card['title']=str(node.get('title',''))[:160]
        card['instructions_excerpt']=str(node.get('instructions',''))[:500]
        cfg=node.get('config',{});card['execution_kind']=cfg.get('kind');card['inputs']=node.get('inputs',[])
        if size(card['inputs'])>1000: card['inputs']=card['inputs'][:3];card['inputs_omitted']=True
        add(result['graph'],'nodes',card,budget//3)
    included_nodes={n['id'] for n in result['graph']['nodes']}
    for edge in graph.get('edges',[]):
        if edge.get('source') in included_nodes or edge.get('target') in included_nodes:
            add(result['graph'],'edges',{k:edge.get(k) for k in ('source','target','relation')},budget//10)
    for source in context.get('sources',[]):
        add(result,'sources',{'id':source['id'],'title':str(source.get('title',''))[:180]},budget//20)
    for claim in context.get('claims',[]):
        add(result,'claims',{'id':claim['id'],'title':str(claim.get('title',''))[:180],'status':claim.get('status')},budget//20)
    coverage['included']={k:len(result['graph'][k] if k in ('nodes','edges','branches') else result[k]) for k in totals}
    coverage['omitted']={k:totals[k]-coverage['included'][k] for k in totals}
    coverage['global_best_groups_total']=len(best)
    coverage['global_best_groups_included']=len(result['global_best_by_conditions'])
    coverage['exceeds_working_set_hint']=size(result)>budget
    return result

def apply_plan(project_id, run_id, plan, expected_revision):
    if plan.get('action') not in ('continue','completed','blocked') or not str(plan.get('rationale','')).strip():
        raise ValueError('Research plan requires an action and evidence-grounded rationale')
    commands=plan.get('commands',[])
    if not isinstance(commands,list): raise ValueError('commands must be an array')
    if plan['action']=='continue' and not commands: raise ValueError('Continue requires actual graph work')
    if plan['action']!='continue' and commands: raise ValueError('Complete or block after proposed graph work has been adopted and evaluated in a separate cycle')
    allowed={'add_node','edit_node','add_dependency','fork_branch','prune_branch','set_main_branch'}
    with Session.begin() as s:
        p=s.scalar(select(Project).where(Project.id==project_id).with_for_update())
        control=p.config.get('controller',{})
        if control.get('status')!='running':
            return {'status':'proposal_only','reason':'Research was paused or stopped before plan adoption','plan':plan}
        if p.revision!=expected_revision:
            emit(s,p.id,'plan_changed',{'run_id':run_id,'status':'stale','revision':p.revision})
            return {'status':'proposal_only','reason':'Project edited while planning; next cycle will use the current graph','plan':plan}
        graph=graph_from_db(s,p)
        for c in commands:
            if c.get('operation') not in allowed: raise ValueError('Unsupported autonomous graph operation')
            params=c.get('params',{})
            cfg=params.get('config',{})
            if c['operation']=='add_node':
                if not params.get('instructions'): raise ValueError('New research work requires concrete instructions')
                if params.get('type') in ('experiment','baseline') and cfg.get('experiment_duty') not in ('effectiveness','mechanism','scenario_value','alternative_explanation'):
                    raise ValueError('Experiment requires an explicit argumentative duty')
                if cfg.get('kind')=='experiment' and not (cfg.get('command') or cfg.get('entrypoint')):
                    raise ValueError('Implement the experiment first; no example fallback exists')
            graph=GraphCommandService(graph,project_dir(p.id)).apply({**c,'request_id':uid(),'expected_revision':graph['revision']})['graph']
        evidence=[]
        for rid in plan.get('evidence_run_ids',[]):
            r=get(s,TaskRun,rid)
            if r.project_id!=p.id: raise ValueError('Evidence belongs to another project')
            evidence.append(r)
        if plan['action']=='completed':
            if not evidence or any(r.status!='completed' for r in evidence) or not any(r.kind not in ('research_plan','ideas','suggest_paths') for r in evidence):
                raise ValueError('Research completion requires completed evidence runs')
            for evidence_run in evidence:
                receipt=safe_path(project_dir(p.id),evidence_run.output_path+'/result.json')
                if not receipt.is_file(): raise ValueError('Evidence completion receipt is missing: '+evidence_run.id)
                content=json.loads(receipt.read_text())
                expected=evidence_run.config.get('execution_attempt',{}).get('id')
                if content.get('status')!='completed' or (expected and content.get('attempt_id')!=expected):
                    raise ValueError('Evidence receipt does not match the completed run: '+evidence_run.id)
            pending=[n for n in graph['nodes'] if not n.get('archived') and (not control.get('branch_id') or n['branch_id']==control['branch_id']) and n.get('execution_status') in ('queued','running','waiting','paused','pausing','budget_exhausted')]
            if pending: raise ValueError('Research cannot complete while selected work is still pending')
            missing=[x for x in control.get('required_artifacts',[]) if not safe_path(project_dir(p.id),x).is_file()]
            if missing: raise ValueError('Required deliverables missing: '+', '.join(missing))
        if commands: save_graph(s,p,graph)
        record=Hypothesis(project_id=p.id,title='Research decision',status='adopted' if commands else plan['action'],data={**plan,'origin':'research_controller','run_id':run_id,'graph_revision':p.revision,'evidence_label':'INFERRED'})
        s.add(record); s.flush()
        control={**control,'phase':'EXECUTE' if commands else 'CONFIRM','last_decision_id':record.id,'last_rationale':plan['rationale'], 'last_plan_run':run_id, 'cycles':int(control.get('cycles',0))+1}
        if plan['action']!='continue': control.update(status=plan['action'],reason=plan['rationale'])
        p.config={**p.config,'controller':control}
        emit(s,p.id,'controller_changed',control)
        if commands: emit(s,p.id,'node_changed',{'revision':p.revision})
        return {'decision_id':record.id,'action':plan['action'],'applied_commands':len(commands),'graph_revision':p.revision,'rationale':plan['rationale']}
