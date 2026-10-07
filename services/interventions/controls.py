"""Explicit instructions and durable human decisions at agent boundaries."""
from copy import deepcopy
from sqlalchemy import select, or_
from services.api.db import Session, Project, Node, TaskRun, ToolExecution, uid, now, asdict
from services.api.common import get, error, emit
from services.worker.scheduler import _lock_project, ACTIVE
from .models import Intervention, InterventionEffect, ActionDecision
from .application import summarize, readback


def send_instruction(project_id, request_id, expected_revision, *, text, scope, target_id=None, boundary='next_request'):
    if not str(text).strip() or len(text) > 100000: error('INVALID_INSTRUCTION', 'Provide a nonempty instruction up to 100000 characters', 422)
    if scope not in ('project', 'branch', 'node', 'run') or boundary not in ('next_request', 'next_run'):
        error('INVALID_SCOPE', 'Choose project, branch, node or run, and next_request or next_run', 422)
    intent = {'text': text, 'scope': scope, 'target_id': target_id, 'boundary': boundary}
    with Session.begin() as session:
        project = _lock_project(session, project_id)
        old = session.scalar(select(Intervention).where(Intervention.project_id == project_id, Intervention.request_id == request_id))
        if old:
            if old.intent != intent: error('REQUEST_ID_CONFLICT', 'Request identity belongs to another instruction', 409)
            return readback(session, old)
        if project.revision != expected_revision: error('REVISION_CONFLICT', 'The reviewed project changed', 409)
        if scope != 'project':
            from services.api.db import Branch
            target = get(session, {'node': Node, 'run': TaskRun, 'branch': Branch}[scope], target_id)
            if target.project_id != project_id: error('CROSS_PROJECT', 'Instruction target belongs to another project', 422)
            if scope == 'run' and (boundary == 'next_run' or target.kind != 'agent'):
                error('INVALID_SCOPE', 'A run instruction requires an agent run and the next_request boundary', 422)
            if scope == 'run' and target.status not in ACTIVE:
                error('RUN_STOPPED', 'This run cannot send another model request; choose a node or project scope', 409)
        project.revision += 1
        row = Intervention(project_id=project_id, request_id=request_id, actor='owner', kind='instruction',
            observed_revision=expected_revision, applied_revision=project.revision, intent=intent,
            impact={'boundary': boundary, 'scope': scope, 'target_id': target_id}, accepted_at=now())
        session.add(row); session.flush()
        if boundary == 'next_request':
            runs = select(TaskRun).where(TaskRun.project_id == project_id, TaskRun.kind == 'agent', TaskRun.status.in_(ACTIVE))
            if scope != 'project': runs = runs.where(getattr(TaskRun, {'run': 'id', 'node': 'node_id', 'branch': 'branch_id'}[scope]) == target_id)
            for run in session.scalars(runs.with_for_update()):
                session.add(InterventionEffect(intervention_id=row.id, project_id=project_id, run_id=run.id,
                    attempt_id=(run.config.get('execution_attempt') or {}).get('id'), action='instruction',
                    target={'run_id': run.id, 'boundary': boundary}))
        # Scope-bound instructions remain available to later authorized runs.
        # Acceptance does not claim any model request has consumed them.
        emit(session, project_id, 'intervention_changed', {'intervention_id': row.id, 'status': 'accepted'})
        emit(session, project_id, 'project_changed', {'revision': project.revision})
        return readback(session, row)


def instruction_context(session, run):
    rows = list(session.scalars(select(Intervention).where(Intervention.project_id == run.project_id,
        Intervention.kind == 'instruction', Intervention.actor != 'imported_history').order_by(Intervention.created_at, Intervention.id)))
    applicable = []
    for row in rows:
        scope = row.intent['scope']; target = row.intent.get('target_id')
        match = scope == 'project' or target == {'node': run.node_id, 'branch': run.branch_id, 'run': run.id}.get(scope)
        if not match: continue
        if row.intent['boundary'] == 'next_run' and run.created_at < row.accepted_at: continue
        applicable.append({'id': row.id, 'text': row.intent['text'], 'scope': scope,
                           'accepted_at': row.accepted_at, 'boundary': row.intent['boundary']})
    return applicable


def confirm_instruction_delivery(run_id, identifiers, request_context, provider_request_id=None):
    if not identifiers: return
    with Session() as reader: project_id=get(reader, TaskRun, run_id).project_id
    with Session.begin() as session:
        _lock_project(session, project_id)
        run = get(session, TaskRun, run_id, for_update=True)
        if request_context.get('execution_attempt_id') != (run.config.get('execution_attempt') or {}).get('id'):
            return  # A late response cannot confirm delivery for a newer attempt.
        for ident in identifiers:
            row = get(session, Intervention, ident)
            if row.project_id != project_id: raise ValueError('Instruction project mismatch')
            effect = session.scalar(select(InterventionEffect).where(InterventionEffect.intervention_id == ident,
                InterventionEffect.run_id == run_id, InterventionEffect.action == 'instruction').with_for_update())
            if not effect:
                effect = InterventionEffect(intervention_id=ident, project_id=project_id, run_id=run_id,
                    action='instruction', attempt_id=(run.config.get('execution_attempt') or {}).get('id'), target={'run_id': run_id})
                session.add(effect)
            if effect.status != 'applied':
                effect.attempt_id = (run.config.get('execution_attempt') or {}).get('id')
                effect.status = 'applied'; effect.applied_at = now()
                effect.observation = {'request_context': deepcopy(request_context), 'provider_request_id': provider_request_id,
                    'meaning': 'Complete authoritative fields delivered directly or through successful requests covering all current control pages; model understanding is not asserted'}
            summarize(session, row)
            emit(session, project_id, 'intervention_changed', {'intervention_id': ident, 'status': row.status})


def close_unconsumed(session, run):
    """A stopped run cannot consume pending controls; retain observed delivery."""
    from services.worker.scheduler import TERMINAL
    if run.status not in TERMINAL: return
    effects = list(session.scalars(select(InterventionEffect).where(
        InterventionEffect.run_id == run.id, InterventionEffect.action.in_(('instruction', 'model_context')),
        InterventionEffect.status.in_(('pending', 'applying', 'uncertain'))).with_for_update()))
    for effect in effects:
        effect.status = 'superseded'
        effect.observation = {'reason': 'run_stopped_before_confirmed_delivery', 'run_status': run.status, 'observed_at': now()}
        row = get(session, Intervention, effect.intervention_id); summarize(session, row)
        emit(session, run.project_id, 'intervention_changed', {'intervention_id': row.id, 'status': row.status})
    decisions = session.scalars(select(ActionDecision).where(ActionDecision.run_id == run.id,
        ActionDecision.status.in_(('pending', 'accepted', 'rejected')),
        ActionDecision.consumed_at.is_(None)).with_for_update())
    for decision in decisions:
        if decision.status == 'rejected': decision.consumed_at = now()
        else: decision.status = 'stale'
        emit(session, run.project_id, 'decision_changed', {'decision_id': decision.id,
            'status': decision.status, 'reason': 'run_stopped', 'run_status': run.status})


def reconcile_unconsumed(limit=50):
    from services.worker.scheduler import TERMINAL
    with Session() as reader:
        instructions = select(InterventionEffect.run_id).where(
            InterventionEffect.action.in_(('instruction', 'model_context')),
            InterventionEffect.status.in_(('pending', 'applying', 'uncertain')))
        decisions = select(ActionDecision.run_id).where(
            ActionDecision.status.in_(('pending', 'accepted', 'rejected')), ActionDecision.consumed_at.is_(None))
        targets = reader.execute(select(TaskRun.id, TaskRun.project_id).where(
            TaskRun.status.in_(TERMINAL), or_(TaskRun.id.in_(instructions), TaskRun.id.in_(decisions))).limit(limit)).all()
    for run_id, project_id in targets:
        with Session.begin() as session:
            _lock_project(session, project_id)
            run = session.get(TaskRun, run_id)
            if run: close_unconsumed(session, run)


def decision_gate(run_id, action):
    with Session() as reader: project_id=get(reader, TaskRun, run_id).project_id
    with Session.begin() as session:
        project = _lock_project(session, project_id); run = get(session, TaskRun, run_id, for_update=True)
        node = session.get(Node, run.node_id) if run.node_id else None
        policy = (node.config if node else {}).get('human_review_tools',
            run.config.get('human_review_tools', project.config.get('human_review_tools', [])))
        if not isinstance(policy, list): raise ValueError('human_review_tools must be a list')
        row = session.scalar(select(ActionDecision).where(ActionDecision.run_id == run_id, ActionDecision.action_id == action['id']))
        if action.get('observed_graph_revision', project.revision) != project.revision:
            if row and row.status in ('pending', 'accepted'):
                row.status = 'stale'
                emit(session, project_id, 'decision_changed', {'decision_id': row.id, 'status': 'stale'})
            return None, {'id': row.id if row else None, 'status': 'stale'}
        if not row and '*' not in policy and action['tool'] not in policy: return action, None
        recorded = session.get(ToolExecution, action['id'])
        if recorded and recorded.status in ('completed', 'failed', 'waiting'): return action, None
        if not row:
            row = ActionDecision(project_id=project_id, run_id=run_id, action_id=action['id'],
                attempt_id=(run.config.get('execution_attempt') or {}).get('id'),
                observed_revision=project.revision, proposed=deepcopy(action))
            session.add(row); session.flush()
            emit(session, project_id, 'decision_requested', {'decision_id': row.id, 'run_id': run_id})
        if row.status == 'pending': return None, {'id': row.id, 'status': 'pending'}
        if row.status in ('rejected', 'stale'): return None, {'id': row.id, 'status': row.status, 'reason': row.answer.get('reason', '')}
        if project.revision != row.observed_revision:
            row.status = 'stale'; emit(session, project_id, 'decision_changed', {'decision_id': row.id, 'status': 'stale'})
            return None, {'id': row.id, 'status': 'stale'}
        return {**deepcopy(row.answer.get('action', row.proposed)),
                **({'call_id': row.proposed['call_id']} if row.proposed.get('call_id') else {}), 'id': action['id'],
                'observed_graph_revision': row.observed_revision}, {'id': row.id, 'status': row.status}


def discard_decision(run_id, action_id):
    """Resolve a saved review when its unexecuted action loses authority."""
    with Session() as reader: project_id = get(reader, TaskRun, run_id).project_id
    with Session.begin() as session:
        _lock_project(session, project_id)
        row = session.scalar(select(ActionDecision).where(ActionDecision.run_id == run_id,
            ActionDecision.action_id == action_id).with_for_update())
        if row and row.status in ('pending', 'accepted'):
            row.status = 'stale'
            emit(session, project_id, 'decision_changed', {'decision_id': row.id, 'status': 'stale'})


def consume_decision(ident):
    if not ident: return
    with Session() as reader: project_id=get(reader, ActionDecision, ident).project_id
    with Session.begin() as session:
        _lock_project(session, project_id); row = get(session, ActionDecision, ident, for_update=True)
        if row.consumed_at: return
        rejected = row.status == 'rejected'
        # Rejection stays permanently non-executable, including a crash before
        # the session's pending action is cleared. Consumption hides stale UI
        # continuation controls without turning refusal into authorization.
        row.status = 'rejected' if rejected else 'consumed'; row.consumed_at = now()
        if not rejected:
            run = get(session, TaskRun, row.run_id)
            row.answer = {**row.answer, 'executed_attempt_id': (run.config.get('execution_attempt') or {}).get('id')}
        emit(session, project_id, 'decision_changed', {'decision_id': ident, 'status': row.status})


def answer_decision(ident, *, expected_revision, choice, reason='', action=None):
    if choice not in ('accept', 'edit', 'reject'): error('INVALID_DECISION', 'Choose accept, edit or reject', 422)
    with Session() as reader: project_id=get(reader, ActionDecision, ident).project_id
    with Session.begin() as session:
        project = _lock_project(session, project_id); row = get(session, ActionDecision, ident, for_update=True)
        run = get(session, TaskRun, row.run_id, for_update=True)
        answer = {'choice': choice, 'reason': reason, **({'action': action} if choice == 'edit' else {})}
        if row.status != 'pending':
            if {k: v for k, v in row.answer.items() if k != 'executed_attempt_id'} == answer: return asdict(row)
            error('DECISION_CONFLICT', 'This decision has already been answered', 409)
        if expected_revision != project.revision or row.observed_revision != project.revision:
            error('REVISION_CONFLICT', 'Reviewed controls changed; the old action cannot be approved', 409)
        if run.status not in ACTIVE or run.resource.get('pending_intervention'):
            error('RUN_STOPPED', 'This run no longer accepts action decisions', 409)
        if choice == 'edit':
            from research.agents.tool_policy import runtime_tools
            if not isinstance(action, dict) or not isinstance(action.get('arguments'), dict) or action.get('tool') not in runtime_tools(session, run):
                error('INVALID_ACTION', 'Edited action must use a currently enabled tool and object arguments', 422)
        row.answer = answer; row.status = 'rejected' if choice == 'reject' else 'accepted'; row.answered_at = now()
        emit(session, project_id, 'decision_changed', {'decision_id': ident, 'status': row.status})
        return asdict(row)
