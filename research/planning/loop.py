"""Real model proposals, checked against the current editable graph before adoption."""
import json
from copy import deepcopy
from pathlib import Path
from sqlalchemy import select
from services.api.db import Session, Project, TaskRun, SourcePaper, ResearchClaim, Hypothesis, uid, now, asdict
from services.api.common import get, graph_from_db, save_graph, project_dir, emit, safe_path
from research.kernel import GraphCommandService
from research.publication import publication_profile, publication_instructions, assess_submission

PLANNER_INSTRUCTIONS = '''An example COMMAND SHAPE (replace branch_id and task details using current context): {"operation":"add_node","targets":[],"params":{"id":"baseline-implementation","branch_id":"ACTUAL_BRANCH_ID","type":"implementation","title":"Implement and measure baseline","instructions":"Implement the specified real task, run it, read measured results and write metrics.json and report.md.","config":{"kind":"agent","role":"Engineer","required_outputs":["metrics.json","report.md"]},"position":{"x":0,"y":0}}}. The commands field contains command OBJECTS, never just node titles. No work executes when commands is empty.
Context uses an automatically selected working set: inspect context_coverage before inferring that work or evidence is absent. Counts describe the complete project; omitted records remain in full_context_artifact and can be read now through read_context_segment. Read every required control page before submitting a plan; full project goals and constraints retain their original authority. Trial dispositions and global best summaries refer to the entire measured history, not only displayed runs.
You plan the next real research work in an editable project. Establish a strong baseline before candidate comparisons. Follow measured results, investigate failures, propose ablations and discriminating experiments, and keep all contrary evidence. The publication profile defaults to full_submission. Its submission_quality gaps and remaining real experiment matrix drive the next cycle; a pilot or an empty queue cannot satisfy full delivery. Retrieve and read the accepted-paper corpus, infer complete comparison/data/statistical workload, execute it, independently review the actual draft and repair the resulting gaps. Never substitute a bundled example for the user's question. Every experiment has a duty: effectiveness, mechanism, scenario_value or alternative_explanation. No simulated providers, numbers, progress or completion. All files and research choices remain editable. Work in English.
Return JSON {"action":"continue|completed|blocked", "rationale":"evidence-grounded decision", "evidence_run_ids":[], "best_estimate":"...", "against":"...", "decisive_unknown":"...", "cheapest_resolution":"...", "commands":[]}. Commands use {"operation":"add_node|edit_node|add_dependency|fork_branch|prune_branch|set_main_branch", "targets":[], "params":{}}. For add_node supply id (unique descriptive string), branch_id from the graph, type, title, instructions, config, inputs and position:{x,y}. config for research steps uses kind:'agent', role from the supplied specialist roles and expected_outputs when known. Specialist roles include Literature Scout, Benchmark Curator, Experiment Designer, Baseline Reproducer, Engineer, Experimenter, Analyst, Figure Designer, Visual Selector, Layout Reviewer, Writer and Submission Reviewer. For executable existing scripts use kind:'experiment', command:argv, metrics_file:'metrics.json', experiment_duty, and input references to producer node files. Never invent executable paths that have not been produced. Add depends_on edges via add_dependency params {source:id,target:id}. Use inputs [{node_id:id,path:'file',destination:'file',branch_id:id}] to consume actual producer files. Engineer writes code and checkpoint support, Experimenter runs it with start_process and wait_for_process and saves predictions/metrics, Analyst independently recalculates. Use a few useful nodes at a time; do not create empty tasks to inflate graph size. Completed requires achieved goal and cited completed measured runs with required deliverables present AND a ready submission audit for full_submission, not simply an empty queue. Blocked must state an exact missing external input, not merely unfinished experiments or a rejected delivery audit. A failed attempt is evidence for repair or a different path, never successful research. Do not silently replace existing working files or discard results.'''
PLANNER_INSTRUCTIONS += '''
Critical measured handoffs require editable independent verifier DAG nodes, not a producer's self-issued pass or LLM praise. Add config.kind='verification', config.verification={producer_node_id:actual_producer_id,checks:[{id:'recompute-score',kind:'numeric_compare',source:'metrics.json',repeat:'metrics.json',pointers:['/score'],absolute_tolerance:0,relative_tolerance:0}]} and an actual distinct recomputation command that writes repeat files. For source-data checks use explicit csv_coverage identities, numeric columns and the declared expected matrix. Connect producer->verifier->consumer; consumers declare required_verification:[verifier_id] or source input verification_node_id. Read server-bound verification_status and exact check scope. Rejected or inconclusive material requires repair/new evidence; do not announce scientific truth from a limited arithmetic or coverage check. Evidence Verifier specifies checks; Open Source Research Advisor offers implementation advice only.
Declare a complete comparison_signature containing dataset, dataset_version, split, evaluation_protocol, metric, statistical_unit and budget, plus objective comparison_fields. Missing or contradictory declarations remain unverified/incomparable, not baseline champions. Preserve selection history, confirmation access and contrary observations; use comparable reruns after editable protocol changes.
Read route_health and latest_route_review. Research Direction Reviewer reads the complete recorded route; its advice does not verify results. When controller.replan_required is true, return action:'continue' and replanning:{reason:'concrete signal and response',new_direction:'new scientific question/hypothesis/data/method',goal_alignment:'why this serves the unchanged original goal',stop_node_ids:[actual_ids],evidence_run_ids:[actual_ids]}. Cite the actual signal runs and materially edit the relevant work or retire the old nodes/branch, then add an executable scientific step. Title/layout edits, identical retries, duplicate nodes, only review/verification tasks and more counterexample checks cannot resolve a route failure. Declared research_activity:'counterexample' has a default allowance of two consecutive checks; after excess choose a different useful direction immediately. Keep all prior evidence editable and preserved.
'''


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
        from services.api.verification import verification_for_run, numerical_coverage_for_run, required_policy
        from research.planning.route_review import route_health
        from services.interventions.applicability import goal_applicability
        applicability={r.id:goal_applicability(s,r) for r in runs}
        verifications={r.id:verification_for_run(s,r) for r in runs}
        numerical={r.id:numerical_coverage_for_run(s,r) for r in runs if r.kind in ('experiment','command','agent','analysis') and r.status=='completed'}
        from .scoreboard import compare_trials
        trial_rows=compare_trials([{'id':r.id,'kind':r.kind,'status':r.status,'metrics':r.metrics,'config':r.config,'created_at':r.created_at,
                                   'verification_status':verifications[r.id]['verification_status'], 'goal_applicability':applicability[r.id],
                                   'numerical_verification':numerical.get(r.id,{}),
                                   'verification_policy':'required' if required_policy(s,r) else 'optional'} for r in runs],p.config.get('objective'))
        profile=publication_profile(p.config)
        sources=[{'id':x.id,'title':x.title,'data':x.data} for x in s.scalars(select(SourcePaper).where(SourcePaper.project_id==p.id))]
        audit=submission_audit(s,p)
        from research.publication.references import reference_context
        summary={**{k:v for k,v in audit.items() if k not in ('gaps','remaining_cells','profile')},
                 'gaps':[{**g,'finding':g['finding'][:600]} for g in audit['gaps'][:24]],'total_gaps':len(audit['gaps'])}
        health=route_health(graph,[asdict(r) for r in runs],p.config)
        return {'trials':trial_rows,'publication_audit':audit,'framework_reference_corpus':reference_context(),
                'route_health':health,'latest_route_review':_latest_route_review(p,runs),
                'project':{'id':p.id,'goal':p.goal,'budget':p.budget,'controller':p.config.get('controller',{}),'objective':p.config.get('objective'),
                           'verification_policy':p.config.get('verification_policy','optional'),
                           'publication_profile':profile,'submission_quality':summary,'publication_instructions':publication_instructions(profile),'planner_context_chars':p.config.get('planner_context_chars',24000)},'graph':graph,
                'runs':[{'id':r.id,'node_id':r.node_id,'status':r.status,'kind':r.kind,'metrics':r.metrics,'error':r.error,'output_path':r.output_path,
                         'verification':verifications[r.id], 'goal_applicability':applicability[r.id],
                         'numerical_verification':numerical.get(r.id,{}),
                         'artifact_observations':run_artifact_observations(r,project_dir(p.id)) if i<12 else []} for i,r in enumerate(runs)],
                'sources':sources,
                'rejected_proposals':[{'id':x.id,'commands':x.data.get('commands',[]),'rejection':x.data.get('rejection')}
                    for x in s.scalars(select(Hypothesis).where(Hypothesis.project_id==p.id,Hypothesis.status=='rejected')
                        .order_by(Hypothesis.updated_at.desc()).limit(20))],
                'claims':[asdict(x) for x in s.scalars(select(ResearchClaim).where(ResearchClaim.project_id==p.id))]}


def _audit_run(run):
    return {key:getattr(run,key) for key in ('id','status','kind','output_path','config','metrics')}


def _latest_route_review(project,runs):
    """Expose actual completed, attempt-bound advice and its currentness."""
    for run in runs:
        if run.kind!='research_route_review' or run.status!='completed':continue
        directory=safe_path(project_dir(project.id),run.output_path)
        try:
            receipt=json.loads((directory/'result.json').read_text())
            report=json.loads((directory/'route_review.json').read_text())
            expected=run.config.get('execution_attempt',{}).get('id')
            if receipt.get('status')!='completed' or (expected and receipt.get('attempt_id')!=expected):continue
            if report.get('review_run_id')!=run.id or not report.get('model'):continue
            return {**report,'current':report.get('project_revision')==project.revision and report.get('project_goal')==project.goal,
                    'authority':'Scientific direction proposal; not a source-bound verification verdict'}
        except (OSError,ValueError,TypeError):continue
    return None


def submission_audit(session, project):
    """One current delivery check shared by autonomous and assisted completion."""
    runs=list(session.scalars(select(TaskRun).where(TaskRun.project_id==project.id).order_by(TaskRun.created_at.desc())))
    sources=[{'id':x.id,'data':x.data} for x in session.scalars(select(SourcePaper).where(SourcePaper.project_id==project.id))]
    audit=assess_submission(project_dir(project.id),publication_profile(project.config),sources=sources,runs=[_audit_run(r) for r in runs])
    from services.api.verification import verification_for_run, numerical_coverage_for_run
    critical=set(); goal_sources=set(); manifest=None
    if audit.get('manifest_path'):
        try:
            manifest=json.loads(safe_path(project_dir(project.id),audit['manifest_path']).read_text())
            from services.interventions.dependencies import record_bindings
            goal_sources.update(item['source_id'] for item in record_bindings(manifest) if item['source_kind']=='run')
            if publication_profile(project.config)['id']=='full_submission' or project.config.get('verification_policy')=='required':
                critical.update(row['run_id'] for row in manifest.get('measured_cells',[]) if isinstance(row,dict) and row.get('run_id'))
                critical.update(manifest.get('analysis_run_ids',[]))
        except (OSError,ValueError,TypeError):pass
    from services.api.db import PaperDocument
    from services.interventions.dependencies import record_bindings
    for paper in session.scalars(select(PaperDocument).where(PaperDocument.project_id==project.id)):
        goal_sources.update(item['source_id'] for item in record_bindings(paper.data) if item['source_kind']=='run')
    from services.interventions.applicability import goal_applicability
    goal_checks=[]
    for identifier in sorted(goal_sources):
        source=session.get(TaskRun,identifier)
        if source is None or source.project_id!=project.id:
            audit['gaps'].append({'code':'goal_applicability','finding':'Delivery source is unavailable in this project: '+identifier,'next_role':'Evidence Verifier'})
            continue
        checked=goal_applicability(session,source);goal_checks.append(checked)
        if not checked['ready']:
            audit['gaps'].append({'code':'goal_applicability','finding':'Delivery source '+identifier+' needs a current goal-use decision: '+checked['status'],'next_role':'Evidence Verifier'})
    if goal_checks:audit['goal_applicability']=goal_checks
    # Current node policy applies to its latest actual attempt. Superseded
    # evidence remains recorded without becoming a permanent completion gate.
    node_policies={node['id']:node.get('config',{}).get('verification_policy')
                   for node in graph_from_db(session,project)['nodes']}
    seen=set()
    for run in runs:
        if run.kind not in ('experiment','command','agent','analysis'):continue
        key=run.node_id or (run.kind,run.branch_id)
        if key in seen:continue
        seen.add(key)
        required=(node_policies.get(run.node_id)=='required' or run.config.get('verification_policy')=='required'
                  or (project.config.get('verification_policy')=='required' and not audit.get('manifest_path')))
        if required and run.status=='completed':critical.add(run.id)
    checks=[]
    for run in runs:
        if run.id not in critical:continue
        observed=verification_for_run(session,run)
        observed={**observed,'numerical_verification':numerical_coverage_for_run(session,run)}
        checks.append(observed)
        if observed['verification_status']!='accepted' or not observed['numerical_verification']['ready']:
            audit['gaps'].append({'code':'independent_verification','finding':'Critical measured run '+run.id+' requires current source-bound acceptance of its numerical evidence; actual status: '+observed['verification_status']+', numeric coverage: '+str(observed['numerical_verification']['ready']),'next_role':'Evidence Verifier'})
    if checks:audit['independent_verification']=checks
    if publication_profile(project.config)['id']=='full_submission' and manifest is not None:
        from services.interventions.acceptance import acceptance_gate
        from services.api.verification import evidence_sources
        handoffs=manifest.get('acceptance_handoffs',[])
        admitted={}; acceptance_checks=[]; accepted_sources={}; accepted_raw=set()
        if not isinstance(handoffs,list): handoffs=[]
        for identifier in handoffs:
            source=session.get(TaskRun,identifier) if isinstance(identifier,str) else None
            if not source or source.project_id!=project.id or source.status!='completed':
                audit['gaps'].append({'code':'acceptance_handoff','finding':'Select a completed actual acceptance handoff in this project: '+str(identifier),'next_role':'Evidence Verifier'})
                continue
            actual_sources=evidence_sources(session,source)
            try: result=acceptance_gate(session,source,actual_sources)
            except ValueError as exc:
                result={'ready':False,'purpose':'unknown','failures':[str(exc)]}
            current=goal_applicability(session,source)
            acceptance_checks.append({'run_id':source.id,**result,'goal_applicability':current})
            if result['ready'] and current['ready']:
                admitted.setdefault(result['purpose'],[]).append(source.id)
                accepted_sources.setdefault(result['purpose'],set()).update(item.id for item in actual_sources)
                if result['purpose']=='raw_data':
                    accepted_raw.update((item.id,path) for item in actual_sources for path in result['contract']['artifact_paths'])
            else:
                audit['gaps'].append({'code':'acceptance_handoff','finding':'Current purpose-bound admission is missing for '+source.id,'next_role':'Evidence Verifier'})
        for purpose in ('raw_data','comparison','major_claim'):
            if purpose not in admitted:
                audit['gaps'].append({'code':'acceptance_'+purpose,'finding':'Full submission requires a current executed '+purpose+' acceptance handoff; list its run in acceptance_handoffs.','next_role':'Evidence Verifier'})
        measured={cell['run_id'] for cell in manifest.get('measured_cells',[]) if isinstance(cell,dict) and cell.get('run_id')
                  and (session.get(TaskRun,cell['run_id']) is None or session.get(TaskRun,cell['run_id']).config.get('research_phase')!='confirmation')}
        for purpose in ('comparison','major_claim'):
            missing=sorted(measured-accepted_sources.get(purpose,set()))
            if missing:
                audit['gaps'].append({'code':'acceptance_scope_'+purpose,'finding':'Current '+purpose+' handoffs do not cover measured runs: '+', '.join(missing),'next_role':'Evidence Verifier'})
        missing_raw=[cell for cell in manifest.get('measured_cells',[]) if isinstance(cell,dict)
                     and (cell.get('run_id'),cell.get('raw_path')) not in accepted_raw]
        if missing_raw:
            audit['gaps'].append({'code':'acceptance_raw_scope','finding':str(len(missing_raw))+' measured cells lack purpose-bound schema/units/identity/split admission for their actual raw_path.','next_role':'Evidence Verifier'})
        audit['acceptance_handoffs']=acceptance_checks
    audit['ready']=not audit['gaps']
    audit['status']=('not_requested' if audit.get('status')=='not_requested' else 'ready') if audit['ready'] else 'needs_revision'
    return audit


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
    if 'route_health' in context:result['route_health']=deepcopy(context['route_health'])
    result['rejected_proposals']=deepcopy(context.get('rejected_proposals', []))
    if context.get('latest_route_review') is not None:
        review=context['latest_route_review']
        result['latest_route_review']={key:deepcopy(review[key]) for key in ('review_run_id','current','decision','summary','findings','stop_node_ids','recommendation','authority') if key in review}
        result['latest_route_review']['coverage_retrievable_from']='full_context_artifact'
    # Build champions from all measured trials, not the display window.
    champions={}; direction=(project.get('objective') or {}).get('direction','min')
    for trial in trials:
        if trial.get('value') is None or trial.get('comparison_eligible') is not True: continue
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
        add(result,'global_best_by_conditions',{'run_id':trial['run_id'],'metric':trial['metric'],'value':trial['value'],'conditions':trial.get('conditions',{}),
            'verification_status':trial.get('verification_status','unverified'),'scope':'Declared comparable conditions in entire project history; numerical ranking is not scientific confirmation'},budget//8)
    ordered_trials=sorted(trials,key=lambda t:(t['run_id'] not in best_ids,t['run_id'] not in recent_ids,t.get('disposition')!='baseline'))
    for trial in ordered_trials: add(result,'trials',trial,budget//6)
    ordered_runs=sorted(enumerate(runs),key=lambda item:(item[1]['id'] not in best_ids|recent_ids,item[0]))
    for _,run in ordered_runs:
        card={k:run.get(k) for k in ('id','node_id','status','kind','output_path')}
        if run.get('verification'):
            card['verification']={key:run['verification'].get(key) for key in ('verification_status','verification_scope','checks')}
        if run.get('numerical_verification'):card['numerical_verification']=deepcopy(run['numerical_verification'])
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
        for name in ('verification','required_verification','verification_policy','research_activity'):
            if name in cfg:card[name]=deepcopy(cfg[name])
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


def validate_replanning(plan,before,after,runs,control,health,goal):
    """Check an executable route change against real recorded repetition.

    This validates references and changed work, not the scientific merit of a
    model's new direction. Original goals and historical evidence stay intact.
    """
    if not control.get('replan_required'):return None
    if plan.get('action')!='continue':
        raise ValueError('A required route revision needs actual new graph work before completion')
    revision=plan.get('replanning')
    if not isinstance(revision,dict):raise ValueError('Required replanning needs reason, new_direction, goal_alignment, stop_node_ids and evidence_run_ids')
    for name in ('reason','new_direction','goal_alignment'):
        if not isinstance(revision.get(name),str) or not revision[name].strip():
            raise ValueError('Replanning must state '+name)
    if before.get('goal',goal)!=after.get('goal',goal) or after.get('goal',goal)!=goal:
        raise ValueError('Replanning must preserve the current original research goal')
    old={node['id']:node for node in before['nodes']};new={node['id']:node for node in after['nodes']}
    actual_runs={run['id'] for run in runs}
    stopped=revision.get('stop_node_ids');evidence=revision.get('evidence_run_ids')
    for name,refs,available in (('stop_node_ids',stopped,set(old)),('evidence_run_ids',evidence,actual_runs)):
        if not isinstance(refs,list) or any(not isinstance(ref,str) or ref not in available for ref in refs) or len(set(refs))!=len(refs):
            raise ValueError('Replanning '+name+' must reference unique actual records')
    if not evidence:raise ValueError('Replanning requires actual evidence run references')
    signal_runs={rid for signal in health.get('signals',[]) for rid in signal.get('run_ids',[])}
    if signal_runs and not signal_runs.intersection(evidence):
        raise ValueError('Replanning must cite the actual detected route failures')
    active={branch['id'] for branch in after.get('branches',[]) if branch.get('status','active')=='active'}
    scientific_keys={'command','entrypoint','research_question','hypothesis','dataset','dataset_version','datasets',
                     'split','split_policy','evaluation_protocol','metric','statistical_unit','seeds','baseline',
                     'candidate','method','algorithm','parameters','condition','comparison_budget','fair_budget'}
    review_roles={'Reviewer','Evidence Verifier','Research Direction Reviewer','Open Source Research Advisor',
                  'Submission Reviewer','Layout Reviewer','Visual Selector','Figure Designer'}
    def scientific(node):
        cfg=node.get('config',{})
        return {'instructions':str(node.get('instructions','')).strip(),
                'config':{key:deepcopy(value) for key,value in cfg.items() if key in scientific_keys},
                'inputs':deepcopy(node.get('inputs',[]))}
    def productive(node):
        cfg=node.get('config',{})
        return (not node.get('archived') and node.get('branch_id') in active and
                cfg.get('kind') in ('agent','experiment','command','analysis') and
                cfg.get('role') not in review_roles and cfg.get('research_activity') not in ('counterexample','counterexample_check'))
    changed=[]
    for ident,node in new.items():
        if not productive(node):continue
        description=scientific(node)
        if ident in old:
            original=scientific(old[ident])
            meaningful=description['config']!=original['config'] or description['inputs']!=original['inputs']
            if node.get('config',{}).get('kind')=='agent' and description['instructions'] and description['instructions']!=original['instructions']:
                # Instructions define an editable agent task. Structural route
                # admission does not pretend to prove its scientific merit.
                meaningful=True
        else:
            meaningful=bool(description['instructions'] and
                            (description['config'] or node.get('config',{}).get('kind')=='agent') and
                            all(description!=scientific(existing) for existing in old.values()))
        if meaningful:changed.append(ident)
    if not changed:raise ValueError('Replanning needs a materially changed scientific task; title/layout edits, duplicate retries and only review/verification/counterexample work do not qualify')
    affected={nid for signal in health.get('signals',[]) for nid in signal.get('node_ids',[]) if nid in old and not old[nid].get('archived')}
    affected.update(nid for nid in control.get('route_replan_node_ids',[]) if nid in old and not old[nid].get('archived'))
    if affected-set(stopped):
        raise ValueError('Replanning must identify the affected route nodes in stop_node_ids')
    for ident in stopped:
        node=new.get(ident)
        if node and not node.get('archived') and node.get('branch_id') in active and ident not in changed:
            raise ValueError('Retire or materially repair each stopped node before continuing the route')
    return {**deepcopy(revision),'changed_scientific_node_ids':changed,
            'verification_scope':'Actual references and editable task changes; new scientific direction remains an evidence-grounded proposal'}

def apply_plan(project_id, run_id, plan, expected_revision):
    if plan.get('action') not in ('continue','completed','blocked') or not str(plan.get('rationale','')).strip():
        raise ValueError('Research plan requires an action and evidence-grounded rationale')
    commands=plan.get('commands',[])
    if not isinstance(commands,list): raise ValueError('commands must be an array')
    if plan['action']=='continue' and not commands: raise ValueError('Continue requires actual graph work')
    if plan['action']!='continue' and commands: raise ValueError('Complete or block after proposed graph work has been adopted and evaluated in a separate cycle')
    allowed={'add_node','edit_node','add_dependency','fork_branch','prune_branch','set_main_branch'}
    with Session.begin() as s:
        from services.worker.scheduler import _lock_project
        p=_lock_project(s,project_id)
        control=p.config.get('controller',{})
        if control.get('status')!='running':
            return {'status':'proposal_only','reason':'Research was paused or stopped before plan adoption','plan':plan}
        if p.revision!=expected_revision:
            emit(s,p.id,'plan_changed',{'run_id':run_id,'status':'stale','revision':p.revision})
            return {'status':'proposal_only','reason':'Project edited while planning; next cycle will use the current graph','plan':plan}
        graph=graph_from_db(s,p)
        original_graph=deepcopy(graph)
        project_runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==p.id)))
        for index, c in enumerate(commands):
            if c.get('operation') not in allowed: raise ValueError('Unsupported autonomous graph operation')
            params=c.get('params',{})
            cfg=params.get('config',{})
            if c['operation']=='add_node':
                if not params.get('instructions'): raise ValueError('New research work requires concrete instructions')
                if params.get('type') in ('experiment','baseline') and cfg.get('experiment_duty') not in ('effectiveness','mechanism','scenario_value','alternative_explanation'):
                    raise ValueError('Experiment requires an explicit argumentative duty')
                if cfg.get('kind')=='experiment' and not (cfg.get('command') or cfg.get('entrypoint')):
                    raise ValueError('Implement the experiment first; no example fallback exists')
                if cfg.get('kind')=='verification':
                    spec=cfg.get('verification',{})
                    checks=spec.get('checks') if isinstance(spec,dict) else None
                    if not isinstance(spec,dict) or not spec.get('producer_node_id') or not isinstance(checks,list) or not checks or any(not isinstance(check,dict) or not check.get('id') or not check.get('kind') for check in checks):
                        raise ValueError('A verification node requires an actual producer and explicit identified executable checks')
            graph=GraphCommandService(graph,project_dir(p.id)).simulate({**c,'request_id':f'plan:{run_id}:{index}','expected_revision':graph['revision']})['graph']
        from research.planning.route_review import route_health
        health=route_health(original_graph,[asdict(run) for run in project_runs],p.config)
        replanned=validate_replanning(plan,original_graph,graph,[asdict(run) for run in project_runs],control,health,p.goal)
        evidence=[]
        for rid in plan.get('evidence_run_ids',[]):
            r=get(s,TaskRun,rid)
            if r.project_id!=p.id: raise ValueError('Evidence belongs to another project')
            evidence.append(r)
        if plan['action']=='completed':
            if not evidence or any(r.status!='completed' for r in evidence) or not any(r.kind not in ('research_plan','ideas','suggest_paths') for r in evidence):
                raise ValueError('Research completion requires completed evidence runs')
            for evidence_run in evidence:
                from services.interventions.applicability import goal_applicability
                applicable=goal_applicability(s,evidence_run)
                if not applicable['ready']:
                    raise ValueError('Completion evidence requires current goal applicability: '+evidence_run.id+' ('+applicable['status']+')')
                receipt=safe_path(project_dir(p.id),evidence_run.output_path+'/result.json')
                if not receipt.is_file(): raise ValueError('Evidence completion receipt is missing: '+evidence_run.id)
                content=json.loads(receipt.read_text())
                expected=evidence_run.config.get('execution_attempt',{}).get('id')
                if content.get('status')!='completed' or (expected and content.get('attempt_id')!=expected):
                    raise ValueError('Evidence receipt does not match the completed run: '+evidence_run.id)
                node=next((item for item in graph['nodes'] if item['id']==evidence_run.node_id),None)
                required=p.config.get('verification_policy')=='required' or evidence_run.config.get('verification_policy')=='required' or (node and node.get('config',{}).get('verification_policy')=='required')
                if required and evidence_run.kind in ('experiment','command','agent','analysis'):
                    from services.api.verification import verification_for_run, numerical_coverage_for_run
                    accepted=verification_for_run(s,evidence_run)
                    if accepted['verification_status']!='accepted' or not numerical_coverage_for_run(s,evidence_run)['ready']:
                        raise ValueError('Critical completion evidence requires current source-bound verification: '+evidence_run.id+' ('+accepted['verification_status']+')')
            pending=[n for n in graph['nodes'] if not n.get('archived') and (not control.get('branch_id') or n['branch_id']==control['branch_id']) and n.get('execution_status') in ('queued','running','waiting','paused','pausing','budget_exhausted')]
            if pending: raise ValueError('Research cannot complete while selected work is still pending')
            missing=[x for x in control.get('required_artifacts',[]) if not safe_path(project_dir(p.id),x).is_file()]
            if missing: raise ValueError('Required deliverables missing: '+', '.join(missing))
            audit=submission_audit(s,p)
            if not audit['ready']:
                # The model's terminal proposal is retained, but a real failed
                # delivery audit sends the controller into its next plan cycle.
                # This is unfinished work, not a provider-format failure.
                record=Hypothesis(project_id=p.id,title='Submission delivery revision required',status='needs_revision',
                                  data={**plan,'origin':'research_controller','run_id':run_id,'audit':audit,'evidence_label':'INFERRED'})
                s.add(record);s.flush()
                control={**control,'status':'running','phase':'PLAN','last_decision_id':record.id,'last_plan_run':run_id,
                         'last_rationale':'Full-submission delivery audit requires further work.',
                         'submission_quality':audit,'cycles':int(control.get('cycles',0))+1}
                p.config={**p.config,'controller':control};emit(s,p.id,'controller_changed',control)
                return {'decision_id':record.id,'action':'continue','status':'needs_revision','applied_commands':0,
                        'graph_revision':p.revision,'submission_quality':audit,
                        'rationale':'Continue the real research and manuscript revision loop until the delivery audit is ready.'}
        if commands:
            from services.interventions.application import apply_in_session
            applied=apply_in_session(s,p,'plan:'+run_id,expected_revision,commands,actor='planner',batch=True)
            graph=applied['graph']
        record=Hypothesis(project_id=p.id,title='Research decision',status='adopted' if commands else plan['action'],data={**plan,'origin':'research_controller','run_id':run_id,'graph_revision':p.revision,'evidence_label':'INFERRED'})
        s.add(record); s.flush()
        control={**control,'phase':'EXECUTE' if commands else 'CONFIRM','last_decision_id':record.id,'last_rationale':plan['rationale'], 'last_plan_run':run_id, 'cycles':int(control.get('cycles',0))+1}
        if replanned is not None:
            control.update(replan_required=False,last_replanning=replanned,replan_reason=None,route_replan_node_ids=[])
        if plan['action']!='continue': control.update(status=plan['action'],reason=plan['rationale'])
        p.config={**p.config,'controller':control}
        emit(s,p.id,'controller_changed',control)
        if commands: emit(s,p.id,'node_changed',{'revision':p.revision})
        return {'decision_id':record.id,'action':plan['action'],'applied_commands':len(commands),'graph_revision':p.revision,'rationale':plan['rationale']}
