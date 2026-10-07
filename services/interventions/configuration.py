"""Project control saves and their separately observed consumption boundaries."""
from copy import deepcopy
from sqlalchemy import select
from services.api.common import get, emit
from services.api.db import Session, TaskRun, now, uid
from services.worker.scheduler import ACTIVE, _lock_project
from research.agents.tool_policy import runtime_tools
from .models import Intervention, InterventionEffect
from .application import summarize, readback


def record_change(session, project, observed_revision, intent, request_id=None):
    keys = set(intent) & {'goal', 'mode', 'budget', 'config'}
    if not keys: return None
    parent = Intervention(project_id=project.id, request_id=request_id or uid(), actor='owner', kind='configuration',
        observed_revision=observed_revision, applied_revision=project.revision, intent=deepcopy(intent), accepted_at=now(),
        impact={'changed_fields': sorted(keys), 'boundaries': {'goal': 'next_model_request_and_evidence_use',
            'tools': 'next_tool_acceptance_and_model_request', 'budget': 'current_usage_and_next_admission',
            'mode': 'next_controller_cycle'}, 'configuration_saved': True})
    session.add(parent); session.flush()
    effects = list(session.scalars(select(InterventionEffect).join(Intervention).where(
        Intervention.project_id == project.id, Intervention.kind == 'configuration',
        InterventionEffect.action == 'model_context', InterventionEffect.status == 'pending').with_for_update()))
    for effect in (effects if keys & {'goal', 'config'} else []):
        effect.status = 'superseded'; effect.observation = {'reason': 'newer_project_configuration', 'intervention_id': parent.id}
        summarize(session, get(session, Intervention, effect.intervention_id))
    for run in session.scalars(select(TaskRun).where(TaskRun.project_id == project.id, TaskRun.status.in_(ACTIVE)).with_for_update()):
        if run.kind == 'agent' and keys & {'goal', 'config'}:
            session.add(InterventionEffect(intervention_id=parent.id, project_id=project.id, run_id=run.id,
                attempt_id=(run.config.get('execution_attempt') or {}).get('id'), action='model_context',
                target={'goal': project.goal, 'allowed_tools': runtime_tools(session, run)}))
    summarize(session, parent)
    emit(session, project.id, 'intervention_changed', {'intervention_id': parent.id, 'status': parent.status})
    return readback(session, parent)


def confirm_context(run_id, request_context, provider_request_id=None):
    with Session() as reader: project_id = get(reader, TaskRun, run_id).project_id
    with Session.begin() as session:
        _lock_project(session, project_id)
        run = get(session, TaskRun, run_id, for_update=True)
        if request_context.get('execution_attempt_id') != (run.config.get('execution_attempt') or {}).get('id'): return
        for effect in session.scalars(select(InterventionEffect).where(
            InterventionEffect.run_id == run_id, InterventionEffect.action == 'model_context',
            InterventionEffect.status == 'pending').with_for_update()):
            proof = next((record for record in request_context.get('delivered_controls', []) if
                record['fields'].get('goal') == effect.target['goal'] and
                sorted(record['fields'].get('allowed_tools', [])) == sorted(effect.target['allowed_tools'])), None)
            if proof is None: continue
            effect.status = 'applied'; effect.applied_at = now()
            effect.attempt_id = request_context.get('execution_attempt_id')
            effect.observation = {'delivery': deepcopy(proof), 'payload_sha256': request_context['payload_sha256'],
                'provider_request_id': provider_request_id, 'observed_graph_revision': request_context['observed_graph_revision'],
                'meaning': 'Current authoritative Goal and effective tools reached successful model transport; understanding is not asserted'}
            parent = get(session, Intervention, effect.intervention_id); summarize(session, parent)
            emit(session, project_id, 'intervention_changed', {'intervention_id': parent.id, 'status': parent.status})
