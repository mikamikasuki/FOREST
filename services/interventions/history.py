"""Portable control history without transferring executable authorization."""
from sqlalchemy import select
from services.api.db import asdict
from .models import Intervention, InterventionEffect, ActionDecision
from .application import readback


def validate_history(history):
    from .contracts import InterventionView, DecisionView
    if not isinstance(history, dict): raise ValueError('Intervention history must be an object')
    identifiers = set()
    project_id = history.get('project', {}).get('id')
    def identity(row):
        if row['id'] in identifiers: raise ValueError('Historical identities must be globally unique')
        identifiers.add(row['id'])
        if project_id and row['project_id'] != project_id:
            raise ValueError('Control history belongs to a different project')
    for key, model in (('interventions', InterventionView), ('action_decisions', DecisionView)):
        rows = history.get(key, [])
        if not isinstance(rows, list): raise ValueError(key+' must be an array')
        for row in rows:
            model.model_validate(row)
            identity(row)
            if key == 'interventions':
                for effect in row.get('effects', []):
                    identity(effect)
                    if effect['intervention_id'] != row['id']:
                        raise ValueError('Historical effect belongs to a different intervention')


def historical_branches(graph):
    for branch in graph.get('branches', []):
        pending = branch.pop('workspace_intervention', None)
        if pending:
            branch['historical_workspace_intervention'] = pending
            branch['historical_workspace_status'] = branch.get('status')
            branch['status'] = 'disabled'
    return graph


def export_history(session, project_id):
    return {'interventions':[readback(session,row) for row in session.scalars(
                select(Intervention).where(Intervention.project_id==project_id))],
            'action_decisions':[asdict(row) for row in session.scalars(
                select(ActionDecision).where(ActionDecision.project_id==project_id))]}


def history_ids(history):
    return [row['id'] for row in history.get('action_decisions',[])] + [ident
            for row in history.get('interventions',[]) for ident in [row['id'],*[e['id'] for e in row.get('effects',[])]]]


def historical_resource(resource):
    value=dict(resource or {})
    if value.get('pending_intervention'):
        value['historical_pending_intervention']=value.pop('pending_intervention')
    value.pop('wait_for',None)
    return value


def import_history(session, project_id, history, remap):
    # Imported actions/instructions are inspectable historical records. They
    # cannot approve a fresh process, deliver instructions or start an effect.
    session.flush()
    for original in history.get('interventions',[]):
        fields={key:remap(value) for key,value in original.items() if key in Intervention.__table__.columns.keys()}
        fields.update(project_id=project_id,status='superseded',actor='imported_history',
                      impact={**fields.get('impact',{}),'original_status':original['status'],'original_id':original['id']})
        session.add(Intervention(**fields));session.flush()
        for original_effect in original.get('effects',[]):
            fields={key:remap(value) for key,value in original_effect.items() if key in InterventionEffect.__table__.columns.keys()}
            fields.update(project_id=project_id,status='superseded',target={},lease_owner=None,lease_until=0,
                          observation={**fields.get('observation',{}),'original_status':original_effect['status']})
            session.add(InterventionEffect(**fields))
    for original in history.get('action_decisions',[]):
        fields={key:remap(value) for key,value in original.items() if key in ActionDecision.__table__.columns.keys()}
        fields.update(project_id=project_id,status='stale',answer={**fields.get('answer',{}),
            'original_status':original['status'],'original_id':original['id'],'authority':'historical imported decision'})
        session.add(ActionDecision(**fields))
