"""Persistent research controller: execute ready work, then plan from actual evidence."""
from sqlalchemy import select
from services.api.db import *
from services.api.common import get,project_dir,safe_path,emit,graph_from_db
from services.worker.scheduler import enqueue_nodes,enqueue,ACTIVE,_lock_project
from research.planning.loop import submission_audit
from research.planning.route_review import route_health, route_review_transition, CONTROL_KINDS
from research.kernel import execution_edges

def ready_work(session, project_id, graph, control, active_node_ids=()):
    nodes=list(session.scalars(select(Node).where(Node.project_id==project_id,Node.archived==False)))
    branches={b.id for b in session.scalars(select(Branch).where(Branch.project_id==project_id))
              if b.status=='active' and not b.extra.get('workspace_intervention')}
    selected=[n for n in nodes if n.branch_id in branches and
              (not control.get('branch_id') or n.branch_id==control['branch_id'])]
    completed={n.id for n in nodes if n.execution_status=='completed' and
               n.deliverable_status!='needs_update' and n.extra.get('results_current',True)}
    edges=[(e['source'],e['target']) for e in execution_edges(graph)]
    ready=[n for n in selected if n.id not in completed and n.id not in active_node_ids and
           (n.execution_status in ('idle','not_started','needs_update','completed') or n.deliverable_status=='needs_update') and
           all(source in completed for source,target in edges if target==n.id)]
    return selected,completed,sorted(ready,key=lambda n:(n.created_at,n.id))


def queue_ready(session, ready, slots):
    # One admission transaction preserves project budgets across the whole batch.
    with session.begin_nested():
        return [(node,enqueue_nodes(session,node.id,'single','controller:'+uid())[0]) for node in ready[:slots]]


def advance_projects():
    with Session() as s:
        ids=[p.id for p in s.scalars(select(Project).where(Project.archived==False)) if p.config.get('controller',{}).get('status')=='running']
    for pid in ids:
        with Session.begin() as s:
            p=_lock_project(s,pid); control=p.config.get('controller',{})
            if control.get('status')!='running': continue
            runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==pid)))
            def owns_pending_work(run):
                if run.status not in ACTIVE: return False
                # Missing produced inputs can be repaired by the planner. Owner
                # breakpoints, paused work and budgets still require a decision.
                return not (run.status=='waiting_input' and run.resource.get('blocked_reason') in (
                    'input_missing','dependency_failed','verification_required','verification_rejected',
                    'verification_inconclusive','verification_stale','verification_scope_missing','verification_input_changed',
                    'verification_scope','verification_source','verification_source_changed','verification_configuration',
                    'research_route_replan'))
            pending=[r for r in runs if owns_pending_work(r)]
            graph=graph_from_db(s,p)
            # Pending decisions keep their slot, but independent roots can use
            # explicitly enabled spare slots. Planning still waits for all work.
            parallelism=control.get('ready_parallelism',1)
            if type(parallelism) is not int or not 1<=parallelism<=32: parallelism=1
            if pending:
                if any(r.kind in CONTROL_KINDS for r in pending) or len(pending)>=parallelism or control.get('replan_required'): continue
                _,_,ready=ready_work(s,pid,graph,control,{r.node_id for r in runs if r.status in ACTIVE})
                if not ready: continue
                try:
                    queued=queue_ready(s,ready,parallelism-len(pending))
                    control={**control,'phase':'EXECUTE','current_nodes':[n.id for n,r in queued],
                             'last_run':queued[-1][1].id}
                    control.pop('admission_error',None)
                except Exception as exc:
                    detail=getattr(exc,'detail',{})
                    control={**control,'admission_error':detail.get('message',str(exc)) if isinstance(detail,dict) else str(exc)}
                p.config={**p.config,'controller':control};emit(s,pid,'controller_changed',control);continue
            health=route_health(graph,[asdict(r) for r in runs],p.config)
            route_state=control.get('route_review',{})
            review_run=s.get(TaskRun,route_state.get('run_id')) if route_state.get('run_id') else None
            signal_run_ids=sorted({rid for signal in health['signals'] for rid in signal['run_ids']})
            review_needed=health['replan_required'] and route_state.get('handled_run_ids')!=signal_run_ids
            real_runs=[r.id for r in runs if r.kind not in CONTROL_KINDS and r.status in ('completed','failed','interrupted')]
            last_reviewed=set(route_state.get('covered_run_ids',[]))
            periodic=health['settings']['enabled'] and len(set(real_runs)-last_reviewed)>=health['settings']['review_every_runs']
            if control.get('max_cycles') is not None and int(control.get('cycles',0))>=int(control['max_cycles']):
                review_needed=periodic=False
            if review_run and review_run.status=='completed' and not route_state.get('consumed'):
                report=review_run.metrics
                current=report.get('project_revision')==p.revision and report.get('project_goal')==p.goal
                route_state={**route_state,'consumed':True,'covered_run_ids':report.get('reviewed_run_ids',[]),
                             'handled_run_ids':route_state.get('trigger_run_ids',[]),'current':current}
                control=route_review_transition({**control,'route_review':route_state},report,current)
                review_needed=periodic=False
                if control.get('status')!='running':
                    p.config={**p.config,'controller':control};emit(s,pid,'controller_changed',control);continue
            elif review_run and review_run.status in ('failed','interrupted','cancelled') and not route_state.get('consumed'):
                control={**control,'status':'blocked','phase':'ROUTE_REVIEW',
                         'reason':review_run.error or 'Research direction review did not complete',
                         'route_review':{**route_state,'consumed':True}}
                p.config={**p.config,'controller':control};emit(s,pid,'controller_changed',control);continue
            if review_needed or periodic:
                control={**control,'replan_required':True,'route_health':health,'phase':'ROUTE_REVIEW'}
                if control.get('autonomous',p.mode=='auto'):
                    try:
                        with s.begin_nested():
                            reviewed=enqueue(s,pid,'research_route_review',{'branch_id':control.get('branch_id')},'route-review:'+uid())
                        control['route_review']={'run_id':reviewed.id,'trigger_run_ids':signal_run_ids,
                                                 'covered_run_ids':route_state.get('covered_run_ids',[]),'consumed':False}
                        control['last_run']=reviewed.id
                    except Exception as exc:
                        detail=getattr(exc,'detail',{})
                        control.update(status='budget_exhausted' if isinstance(detail,dict) and 'BUDGET' in detail.get('code','') else 'blocked',
                                       reason=detail.get('message',str(exc)) if isinstance(detail,dict) else str(exc))
                else:
                    control.update(status='waiting_input',reason='Review the research route and choose a new direction before continuing.')
                p.config={**p.config,'controller':control};emit(s,pid,'controller_changed',control);continue
            selected_nodes,completed,ready=ready_work(s,pid,graph,control,{r.node_id for r in runs if r.status in ACTIVE})
            try:
                if ready and not control.get('replan_required'):
                    queued=queue_ready(s,ready,parallelism)
                    control={**control,'phase':'EXECUTE','current_node':queued[0][0].id,
                             'current_nodes':[n.id for n,r in queued],'last_run':queued[-1][1].id}
                elif control.get('autonomous',p.mode=='auto'):
                    last=s.get(TaskRun,control.get('last_run')) if control.get('last_run') else None
                    if last and last.kind=='research_plan' and last.status in ('failed','interrupted'):
                        control={**control,'status':'blocked','reason':last.error,'phase':'PLAN'}
                    elif control.get('max_cycles') is not None and int(control.get('cycles',0))>=int(control['max_cycles']):
                        control={**control,'status':'budget_exhausted','reason':'Research cycle budget reached; the full submission remains unfinished.',
                                 'submission_quality':submission_audit(s,p)}
                    else:
                        with s.begin_nested(): run=enqueue(s,pid,'research_plan',{'branch_id':control.get('branch_id')},'planner:'+uid())
                        control={**control,'phase':'PLAN','last_run':run.id}
                else:
                    missing=[ref for ref in control.get('required_artifacts',[]) if not safe_path(project_dir(pid),ref).is_file()]
                    if selected_nodes and all(n.id in completed for n in selected_nodes) and not missing:
                        audit=submission_audit(s,p)
                        if audit['ready']:
                            control={**control,'status':'completed','phase':'CONFIRM','submission_quality':audit,
                                     'completion':{'executed_nodes':sum(n.id in completed for n in selected_nodes),'checked_files':control.get('required_artifacts',[]),'scientific_scope':'Requested delivery coverage checked; venue acceptance is not established.'}}
                        else:
                            control={**control,'status':'submission_incomplete','phase':'PLAN','submission_quality':audit,
                                     'reason':'Requested graph work finished; full-submission evidence and manuscript delivery still require the listed research work.'}
                    else:
                        control={**control,'status':'blocked','phase':'PLAN','missing_files':missing,'reason':'Provide executable work, repair a failed run, or enable autonomous research.'}
            except Exception as exc:
                detail=getattr(exc,'detail',{})
                budget_code=detail.get('code') if isinstance(detail,dict) else None
                if budget_code in ('RUN_BUDGET_EXHAUSTED','TIME_BUDGET_EXHAUSTED','MODEL_BUDGET_EXHAUSTED'):
                    control={**control,'status':'budget_exhausted','reason':detail.get('message',str(exc)),
                             'submission_quality':submission_audit(s,p)}
                else:
                    control={**control,'status':'blocked','reason':str(exc)}
            p.config={**p.config,'controller':control}; emit(s,pid,'controller_changed',control)
