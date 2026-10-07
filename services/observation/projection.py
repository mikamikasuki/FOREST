"""Cheap SQL projections; no verifier, process probe, model, or filesystem read."""
from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session as OrmSession
from services.api.db import (engine, Project, Node, TaskRun, Event, EventSequence,
                            PaperDocument, Worker, RESOURCE_MODELS, now)
from services.api.common import get
from .contracts import ProjectProgressSnapshot, ProgressFact, SourceRef, ObserverHealth
from .models import ObservationState, ObservationScope

@contextmanager
def coherent_read():
    # Driver autocommit defaults otherwise defer SQLite BEGIN past SELECT.
    with engine.connect() as connection:
        if connection.dialect.name == 'postgresql':
            connection = connection.execution_options(isolation_level='REPEATABLE READ')
            transaction = connection.begin()
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        else:
            transaction = connection.begin()
            connection.exec_driver_sql('BEGIN')
        with OrmSession(bind=connection, expire_on_commit=False) as session:
            try: yield session
            finally: transaction.rollback()

def age(timestamp):
    if not timestamp: return None
    try: return max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(timestamp)).total_seconds())
    except ValueError: return None

def project_snapshot(project_id, *, epoch, generation, previous_cursor=0):
    with coherent_read() as s:
        p=get(s,Project,project_id); observed=now()
        cursor=s.scalar(select(EventSequence.sequence).where(EventSequence.project_id==project_id)) or 0
        first=s.scalar(select(func.min(Event.sequence)).where(Event.project_id==project_id))
        gap=bool(first and previous_cursor<first-1)
        counts=dict(s.execute(select(TaskRun.status,func.count()).where(TaskRun.project_id==project_id).group_by(TaskRun.status)).all())
        # Bounded rows; counts still cover all persisted runs.
        active=list(s.scalars(select(TaskRun).where(TaskRun.project_id==project_id,TaskRun.status.in_(
            ['queued','running','waiting','waiting_input','paused','pausing','budget_exhausted'])).order_by(TaskRun.created_at.desc(),TaskRun.id).limit(100)))
        recent=list(s.scalars(select(TaskRun).where(TaskRun.project_id==project_id).order_by(TaskRun.created_at.desc(),TaskRun.id).limit(20)))
        runs={r.id:r for r in [*active,*recent]}
        node_ids={r.node_id for r in runs.values() if r.node_id}
        nodes={n.id:n for n in s.scalars(select(Node).where(Node.project_id==project_id,Node.id.in_(node_ids)))} if node_ids else {}
        workers={w.id:w for w in s.scalars(select(Worker).where(Worker.id.in_({r.worker_id for r in active if r.worker_id})))}
        facts=[]; dependencies={'project_revision':p.revision,'epoch':epoch}
        def fact(ident,section,label,value,source,applicability='current',time=observed,classification='OPERATIONAL'):
            facts.append(ProgressFact(id=ident,section=section,label=label,value=value,observed_at=time,
                                      sources=[source],applicability=applicability,classification=classification))
        control=(p.config or {}).get('controller') or {}
        controller=control.get('status','idle')
        if controller not in ('idle','running','paused','stopped','waiting','waiting_input','budget_exhausted','completed','failed'):
            controller='unrecognized recorded state'
        dependencies['controller']=controller
        dependencies['run_counts']=str(sorted(counts.items()))
        fact('project:'+project_id,'now','Controller',controller,SourceRef(epoch=epoch,project_id=project_id,kind='project',object_id=project_id,revision=p.revision))
        for r in runs.values():
            n=nodes.get(r.node_id); current=not n or (n.revision==r.node_revision and (n.extra or {}).get('latest_run_id')==r.id)
            source=SourceRef(epoch=epoch,project_id=project_id,kind='run',object_id=r.id,revision=r.node_revision,
                             attempt_id=(r.config.get('execution_attempt') or {}).get('id'))
            section=('next' if r.status=='queued' else 'attention' if r.status in ('waiting_input','budget_exhausted') else
                     'blocked' if r.status=='waiting' else 'now' if r.status in ('running','pausing','paused') else 'recent')
            fact('run:'+r.id,section,'Run '+r.id[:8],r.status,source,'current' if current else 'historical',r.updated_at)
            reason=(r.resource or {}).get('blocked_reason')
            reasons={'dependency_failed':'An execution dependency has no successful output; inspect its run and retry or rebind inputs.',
                'input_missing':'Required produced files are missing; inspect the input bindings.',
                'research_route_replan':'The recorded route needs replanning before execution.',
                'verification_configuration':'The verification configuration needs correction.',
                'verification_scope':'Required inputs are outside the accepted check scope.',
                'verification_input_changed':'A required input changed after checking; verify the current input.',
                'verification_source_changed':'The producer changed after checking; verify its current execution.',
                'verification_source':'Verification requires a current completed producer.',
                'verification_rejected':'The recorded check rejected a required producer.',
                'verification_inconclusive':'The required check is inconclusive.',
                'verification_unverified':'A required producer has no applicable verification.'}
            if r.status in ('waiting','waiting_input','budget_exhausted','paused'):
                value=reasons.get(reason,'Inspect this run’s recorded wait, budget or pause state; no eligible next action is inferred.')
                fact('reason:'+r.id,'blocked' if r.status=='waiting' else 'attention','Recorded execution block',value,source,
                     'current' if current else 'historical',r.updated_at)
                dependencies['reason:'+r.id]=reason if reason in reasons else 'inspect recorded state'
            # A DB lifecycle state never proves that a process is currently alive.
            if r.status in ('running','pausing'):
                worker=workers.get(r.worker_id); a=age(worker.heartbeat) if worker else None
                fact('heartbeat:'+r.id,'now','Worker observation', 'missing' if a is None else 'stale' if a>30 else 'recent heartbeat',source,time=worker.heartbeat if worker else r.updated_at)
                dependencies['heartbeat:'+r.id]='missing' if a is None else 'stale' if a>30 else 'recent heartbeat'
            dependencies['run:'+r.id]=f'{r.status}:{r.node_revision}:{source.attempt_id}:{r.started_at}:{r.finished_at}'
            if n: dependencies['node:'+n.id]=f'{n.revision}:{(n.extra or {}).get("latest_run_id")}:{(n.extra or {}).get("results_current")}'
            receipt=r.resource.get('verification_receipt') or {}
            recorded=r.resource.get('verification_status')
            if recorded in ('accepted','rejected','inconclusive','unverified') and receipt.get('checked_at'):
                fact('verification:'+r.id,'evidence','Last checked verification',recorded+'; declared checks only; current applicability not rechecked',source,
                     'not_checked',str(receipt['checked_at']),classification='REPORTED')
                dependencies['verification:'+r.id]=f'{recorded}:{receipt["checked_at"]}'
        for paper in s.scalars(select(PaperDocument).where(PaperDocument.project_id==project_id).order_by(PaperDocument.updated_at.desc()).limit(20)):
            compiled=paper.data.get('compiled_revision')
            if not isinstance(compiled,int) or isinstance(compiled,bool): compiled=None
            status=paper.status if paper.status in ('draft','compiled','compiling','failed','ready','stale','validated') else 'recorded'
            if compiled is not None and compiled!=paper.revision: status+='; compiled output is historical'
            value=f'{status}; source revision {paper.revision}; compiled revision {compiled if compiled is not None else "unavailable"}'
            fact('paper:'+paper.id,'evidence','Manuscript '+paper.id[:8],value,SourceRef(epoch=epoch,project_id=project_id,kind='paper',object_id=paper.id,revision=paper.revision), 'not_checked',paper.updated_at)
            dependencies['paper:'+paper.id]=paper.updated_at
        coverage=dict(s.execute(select(ObservationScope.coverage,func.count()).where(ObservationScope.project_id==project_id).group_by(ObservationScope.coverage)).all())
        file_state='pending' if not coverage else 'partial' if any(k!='complete' for k in coverage) else 'complete inventories; content bounds apply'
        node_count=s.scalar(select(func.count()).select_from(Node).where(Node.project_id==project_id))
        partial=len(active)==100 or len(recent)==20
        health=ObserverHealth(state='healthy',observed_at=observed,age_seconds=0,
            database_coverage=f'{node_count} nodes; all run counts; at most 100 active and 20 recent run facts'+('; bounded detail' if partial else ''),
            file_coverage=file_state,history_gap=gap,
            note='Recorded lifecycle and heartbeat only. Evidence applicability is not rechecked by observation; use Run evidence for authoritative checks.')
        return ProjectProgressSnapshot(project_id=project_id,epoch=epoch,generation=generation,
            snapshot_id=f'{epoch}:{generation}',cursor=cursor,project_revision=p.revision,
            observed_at=observed,controller_status=controller,facts=facts,run_counts=counts,health=health,dependencies=dependencies)
