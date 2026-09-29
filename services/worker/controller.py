"""Persistent research controller: execute ready work, then plan from actual evidence."""
from sqlalchemy import select
from services.api.db import *
from services.api.common import get,project_dir,safe_path,emit
from services.worker.scheduler import enqueue_nodes,enqueue,ACTIVE

def advance_projects():
    with Session() as s:
        ids=[p.id for p in s.scalars(select(Project).where(Project.archived==False)) if p.config.get('controller',{}).get('status')=='running']
    for pid in ids:
        with Session.begin() as s:
            p=s.scalar(select(Project).where(Project.id==pid).with_for_update()); control=p.config.get('controller',{})
            if control.get('status')!='running': continue
            runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==pid)))
            def owns_pending_work(run):
                if run.status not in ACTIVE: return False
                # Missing produced inputs can be repaired by the planner. Owner
                # breakpoints, paused work and budgets still require a decision.
                return not (run.status=='waiting_input' and run.resource.get('blocked_reason') in ('input_missing','dependency_failed'))
            if any(owns_pending_work(r) for r in runs): continue
            nodes=list(s.scalars(select(Node).where(Node.project_id==pid,Node.archived==False)))
            eligible_branches={b.id for b in s.scalars(select(Branch).where(Branch.project_id==pid)) if b.status=='active'}
            selected_nodes=[n for n in nodes if n.branch_id in eligible_branches and (not control.get('branch_id') or n.branch_id==control['branch_id'])]
            edges=[(e.source,e.target) for e in s.scalars(select(Edge).where(Edge.project_id==pid,Edge.relation.in_(['depends_on','consumes'])))]
            edges.extend((ref['node_id'],n.id) for n in nodes for ref in n.inputs if isinstance(ref,dict) and ref.get('node_id'))
            completed={n.id for n in nodes if n.execution_status=='completed' and n.deliverable_status!='needs_update' and n.extra.get('results_current',True)}
            ready=[n for n in selected_nodes if n.id not in completed and (n.execution_status in ('idle','not_started','needs_update','completed') or n.deliverable_status=='needs_update') and all(source in completed for source,target in edges if target==n.id)]
            try:
                if ready:
                    target=sorted(ready,key=lambda n:n.created_at)[0]
                    with s.begin_nested(): run=enqueue_nodes(s,target.id,'single','controller:'+uid())[0]
                    control={**control,'phase':'EXECUTE','current_node':target.id,'last_run':run.id}
                elif control.get('autonomous',p.mode=='auto'):
                    last=s.get(TaskRun,control.get('last_run')) if control.get('last_run') else None
                    if last and last.kind=='research_plan' and last.status in ('failed','interrupted'):
                        control={**control,'status':'blocked','reason':last.error,'phase':'PLAN'}
                    elif control.get('max_cycles') is not None and int(control.get('cycles',0))>=int(control['max_cycles']):
                        control={**control,'status':'budget_exhausted','reason':'Research cycle budget reached'}
                    else:
                        with s.begin_nested(): run=enqueue(s,pid,'research_plan',{'branch_id':control.get('branch_id')},'planner:'+uid())
                        control={**control,'phase':'PLAN','last_run':run.id}
                else:
                    missing=[ref for ref in control.get('required_artifacts',[]) if not safe_path(project_dir(pid),ref).is_file()]
                    if selected_nodes and all(n.id in completed for n in selected_nodes) and not missing:
                        control={**control,'status':'completed','phase':'CONFIRM','completion':{'executed_nodes':sum(n.id in completed for n in selected_nodes),'checked_files':control.get('required_artifacts',[]),'scientific_scope':'Requested graph work completed; scientific claims require evidence review.'}}
                    else:
                        control={**control,'status':'blocked','phase':'PLAN','missing_files':missing,'reason':'Provide executable work, repair a failed run, or enable autonomous research.'}
            except Exception as exc:
                control={**control,'status':'blocked','reason':str(exc)}
            p.config={**p.config,'controller':control}; emit(s,pid,'controller_changed',control)
