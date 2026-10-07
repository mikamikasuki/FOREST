"""Bounded recorded delivery/figure facts, without executing acceptance checks."""
from sqlalchemy import select
from services.api.db import Figure, TaskRun, Node
from .contracts import ProgressFact, SourceRef

def delivery_record(project):
    control=(project.config or {}).get('controller') or {}
    audit=control.get('submission_quality')
    if not isinstance(audit,dict): return None
    status=audit.get('status')
    if status not in ('ready','needs_revision','incomplete','blocked'): status='recorded'
    gaps=audit.get('gaps')
    return {'status':status,'recorded_gap_count':len(gaps) if isinstance(gaps,list) else None,
            'current_applicability':'not_checked'}

def figure_record(figure):
    data=figure.data or {}
    review=data.get('visual_review_status')
    if review not in ('selected','awaiting_independent_reviews','accepted','rejected','needs_revision'):
        review='not recorded'
    ids=data.get('source_run_ids')
    ids=ids if isinstance(ids,list) else []
    selected=[ident for ident in ids[:20] if isinstance(ident,str) and len(ident)<=64]
    return {'visual_review_status':review,'source_run_ids':selected,
            'source_binding_coverage':'partial' if len(ids)>20 or len(selected)!=len(ids) else 'declared bindings',
            'current_source_bytes':'not_checked'}

def project_scientific_facts(session,project,epoch):
    facts=[]; dependencies={}
    audit=delivery_record(project)
    if audit is not None:
        value=f"{audit['status']}; {audit['recorded_gap_count'] if audit['recorded_gap_count'] is not None else 'unknown'} recorded gaps; current applicability not rechecked"
        facts.append(ProgressFact(id='delivery:'+project.id,section='evidence',label='Last recorded delivery audit',
            value=value,classification='REPORTED',applicability='not_checked',observed_at=project.updated_at,
            sources=[SourceRef(project_id=project.id,epoch=epoch,kind='project',object_id=project.id,
                revision=project.revision,record_updated_at=project.updated_at)]))
        dependencies['delivery:'+project.id]=str(audit)
    figures=list(session.scalars(select(Figure).where(Figure.project_id==project.id)
        .order_by(Figure.updated_at.desc(),Figure.id).limit(20)))
    records={figure.id:figure_record(figure) for figure in figures}
    run_ids={ident for record in records.values() for ident in record['source_run_ids']}
    runs={run.id:run for run in session.scalars(select(TaskRun).where(TaskRun.project_id==project.id,TaskRun.id.in_(run_ids)))} if run_ids else {}
    node_ids={run.node_id for run in runs.values() if run.node_id}
    nodes={node.id:node for node in session.scalars(select(Node).where(Node.project_id==project.id,Node.id.in_(node_ids)))} if node_ids else {}
    for figure in figures:
        record=records[figure.id]; ids=record['source_run_ids']; source_state='not_checked'
        for ident in ids:
            run=runs.get(ident); node=nodes.get(run.node_id) if run else None
            dependencies['figure-producer:'+ident]=('missing' if run is None else
                f'{run.status}:{run.node_revision}:{node.revision if node else None}:{(node.extra or {}).get("latest_run_id") if node else None}:{(node.extra or {}).get("results_current") if node else None}')
            if run is None: source_state='unavailable'
            elif run.status!='completed' or node and (node.revision!=run.node_revision
                    or (node.extra or {}).get('latest_run_id')!=run.id
                    or (node.extra or {}).get('results_current') is False):
                if source_state!='unavailable': source_state='historical'
        status=figure.status if figure.status in ('draft','rendering','ready_for_review','failed','stale','validated') else 'recorded'
        value=f"{status}; visual review {record['visual_review_status']}; producer applicability {source_state}; {record['source_binding_coverage']}; current source bytes not rechecked"
        source=SourceRef(project_id=project.id,epoch=epoch,kind='figure',object_id=figure.id,
            revision=figure.revision,record_updated_at=figure.updated_at)
        facts.append(ProgressFact(id='figure:'+figure.id,section='evidence',label='Figure '+figure.id[:8],value=value,
            classification='REPORTED',applicability='historical' if source_state=='historical' else 'not_checked',
            observed_at=figure.updated_at,sources=[source]))
        dependencies['figure:'+figure.id]=figure.updated_at
    return facts,dependencies
