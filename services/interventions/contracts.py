from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Wire(BaseModel):
    model_config = ConfigDict(extra='forbid')


class InstructionRequest(Wire):
    request_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=100000)
    scope: Literal['project', 'branch', 'node', 'run']
    target_id: str | None = Field(default=None, max_length=64)
    boundary: Literal['next_request', 'next_run'] = 'next_request'


class EffectView(BaseModel):
    id: str
    intervention_id: str
    project_id: str
    run_id: str
    attempt_id: str | None
    action: str
    status: str
    observation: dict[str, Any]
    error: str | None
    attempts: int
    retry_after: float
    created_at: str
    updated_at: str
    applied_at: str | None


class InterventionView(BaseModel):
    id: str
    project_id: str
    request_id: str
    actor: str
    kind: str
    observed_revision: int
    applied_revision: int
    intent: dict[str, Any]
    impact: dict[str, Any]
    status: str
    accepted_at: str
    applied_at: str | None
    created_at: str
    updated_at: str
    effects: list[EffectView]


class DecisionAnswer(Wire):
    expected_revision: int = Field(ge=0)
    choice: Literal['accept', 'edit', 'reject']
    reason: str = Field(default='', max_length=100000)
    action: dict[str, Any] | None = None
    resume: bool = True


class DecisionView(BaseModel):
    id: str
    project_id: str
    run_id: str
    action_id: str
    attempt_id: str | None
    observed_revision: int
    proposed: dict[str, Any]
    status: str
    answer: dict[str, Any]
    answered_at: str | None
    consumed_at: str | None
    created_at: str
    updated_at: str
    resume_error: str | None = None
    run_status: str | None = None
    can_resume: bool | None = None


class RejectionRequest(Wire):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=100000)


class ApplicabilityDecision(Wire):
    request_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)
    choice: Literal['reuse', 'exclude']
    reason: str = Field(min_length=1, max_length=100000)


class ApplicabilityView(BaseModel):
    run_id: str
    status: Literal['current', 'approved', 'excluded', 'needs_review', 'unknown']
    ready: bool
    current_goal: str
    execution_goals: list[str]
    current_goal_scope: dict[str, Any]
    execution_goal_scopes: list[dict[str, Any]]
    decision_id: str | None
    reason: str
    scope: str


class AcceptanceView(BaseModel):
    ready: bool
    purpose: str
    scope: str
    contract: dict[str, Any] | None = None
    failures: list[str] = Field(default_factory=list)
    source_checks: list[dict[str, Any]] = Field(default_factory=list)
    comparison: dict[str, Any] | None = None
    confirmations: list[dict[str, Any]] = Field(default_factory=list)
