from sqlalchemy import ForeignKey, Integer, JSON, String, Text, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from services.api.db import Base, Identity


class Intervention(Identity, Base):
    __tablename__ = 'interventions'
    __table_args__ = (UniqueConstraint('project_id', 'request_id'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    request_id: Mapped[str] = mapped_column(String(160))
    actor: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(40))
    observed_revision: Mapped[int] = mapped_column(Integer)
    applied_revision: Mapped[int] = mapped_column(Integer)
    intent: Mapped[dict] = mapped_column(JSON)
    impact: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default='accepted')
    accepted_at: Mapped[str] = mapped_column(String(40))
    applied_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class InterventionEffect(Identity, Base):
    __tablename__ = 'intervention_effects'
    __table_args__ = (UniqueConstraint('intervention_id', 'run_id', 'action'),)
    intervention_id: Mapped[str] = mapped_column(ForeignKey('interventions.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    # Retain the target identity when history cleanup removes a run.
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default='pending', index=True)
    target: Mapped[dict] = mapped_column(JSON)
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    retry_after: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    applied_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ActionDecision(Identity, Base):
    __tablename__ = 'action_decisions'
    __table_args__ = (UniqueConstraint('run_id', 'action_id'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('task_runs.id', ondelete='CASCADE'), index=True)
    action_id: Mapped[str] = mapped_column(String(64))
    attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_revision: Mapped[int] = mapped_column(Integer)
    proposed: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default='pending')
    answer: Mapped[dict] = mapped_column(JSON, default=dict)
    answered_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consumed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
