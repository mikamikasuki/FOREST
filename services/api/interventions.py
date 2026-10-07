from fastapi import APIRouter, Query
from sqlalchemy import select
from services.api.db import Session, Project, Hypothesis, TaskRun, asdict, now
from services.api.common import get, error, emit
from services.worker.scheduler import _lock_project
from services.interventions.models import Intervention, ActionDecision
from services.interventions.application import readback
from services.interventions.controls import send_instruction, answer_decision
from services.interventions.contracts import (InstructionRequest, InterventionView, DecisionAnswer,
                                            DecisionView, RejectionRequest, ApplicabilityDecision, ApplicabilityView, AcceptanceView)

router = APIRouter()


@router.get('/api/interventions/{ident}', response_model=InterventionView)
def intervention(ident: str):
    with Session() as session: return readback(session, get(session, Intervention, ident))


@router.get('/api/runs/{ident}/applicability', response_model=ApplicabilityView)
def applicability(ident: str):
    from services.interventions.applicability import goal_applicability
    with Session() as session: return goal_applicability(session, get(session, TaskRun, ident))


@router.get('/api/runs/{ident}/acceptance', response_model=AcceptanceView)
def acceptance(ident: str):
    from services.interventions.acceptance import acceptance_gate
    from services.api.verification import evidence_sources
    with Session() as session:
        run = get(session, TaskRun, ident)
        try: return acceptance_gate(session, run, evidence_sources(session, run))
        except ValueError as exc: error('INVALID_ACCEPTANCE_CONTRACT', str(exc), 422)


@router.post('/api/runs/{ident}/applicability/decisions', response_model=InterventionView)
def applicability_decision(ident: str, body: ApplicabilityDecision):
    from services.interventions.applicability import decide_applicability
    return decide_applicability(ident, **body.model_dump())


@router.get('/api/projects/{ident}/interventions', response_model=list[InterventionView])
def interventions(ident: str, limit: int = Query(50, ge=1, le=200), before: str | None = None):
    with Session() as session:
        get(session, Project, ident)
        query = select(Intervention).where(Intervention.project_id == ident)
        if before: query = query.where(Intervention.created_at < before)
        return [readback(session, row) for row in session.scalars(query.order_by(
            Intervention.created_at.desc(), Intervention.id.desc()).limit(limit))]


@router.post('/api/projects/{ident}/instructions', response_model=InterventionView)
def instruction(ident: str, body: InstructionRequest):
    return send_instruction(ident, **body.model_dump())


@router.get('/api/projects/{ident}/decisions', response_model=list[DecisionView])
def decisions(ident: str, limit: int = Query(50, ge=1, le=200), status: str | None = None):
    with Session() as session:
        get(session, Project, ident)
        query = select(ActionDecision).where(ActionDecision.project_id == ident)
        if status: query = query.where(ActionDecision.status == status)
        return [asdict(row) for row in session.scalars(query.order_by(ActionDecision.created_at.desc()).limit(limit))]


@router.post('/api/decisions/{ident}/answer', response_model=DecisionView)
def decision_answer(ident: str, body: DecisionAnswer):
    result = answer_decision(ident, **body.model_dump(exclude={'resume'}))
    if body.resume:
        with Session() as session:
            run = get(session, TaskRun, result['run_id'])
            waiting = (run.status == 'waiting_input' and result['status'] in ('accepted', 'rejected')
                       and run.resource.get('wait_for', {}).get('decision_id') == ident)
        if waiting:
            from .main import run_action
            try: run_action(run.id, 'resume', {})
            except Exception as exc: result['resume_error'] = str(exc)[:1500]
    return result


@router.post('/api/research/proposals/{ident}/reject')
def reject_proposal(ident: str, body: RejectionRequest):
    with Session() as reader: project_id = get(reader, Hypothesis, ident).project_id
    with Session.begin() as session:
        project = _lock_project(session, project_id); proposal = get(session, Hypothesis, ident)
        if project.revision != body.expected_revision: error('REVISION_CONFLICT', 'The reviewed project changed', 409)
        if proposal.status in ('adopted', 'partly_adopted'): error('PROPOSAL_APPLIED', 'Applied work is preserved; submit a new intervention', 409)
        proposal.status = 'rejected'
        proposal.data = {**proposal.data, 'rejection': {'reason': body.reason,
            'observed_revision': body.expected_revision, 'rejected_at': now()}}
        emit(session, project_id, 'plan_changed', {'proposal_id': ident, 'status': 'rejected'})
        return asdict(proposal)
