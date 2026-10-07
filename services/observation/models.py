"""Mutable reporting-domain records. Source bytes have bounded current retention."""
from sqlalchemy import String, Integer, Float, JSON, ForeignKey, Text, LargeBinary, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from services.api.db import Base, Identity, uid

class ObservationState(Base):
    __tablename__ = 'observation_states'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    epoch: Mapped[str] = mapped_column(String(64), default=uid)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[str|None] = mapped_column(String(40), nullable=True)
    lease_owner: Mapped[str|None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    active_job: Mapped[str|None] = mapped_column(String(64), nullable=True)

class ObservationScope(Identity, Base):
    __tablename__ = 'observation_scopes'
    __table_args__ = (UniqueConstraint('project_id', 'kind', 'object_id'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(64))
    root: Mapped[str] = mapped_column(Text)
    scan_generation: Mapped[int] = mapped_column(Integer, default=0)
    queue: Mapped[list] = mapped_column(JSON, default=lambda: [['', '']])
    coverage: Mapped[str] = mapped_column(String(40), default='pending')
    observed_at: Mapped[str|None] = mapped_column(String(40), nullable=True)
    error: Mapped[str|None] = mapped_column(String(80), nullable=True)
    attempt_id: Mapped[str|None] = mapped_column(String(64), nullable=True)

class ObservedFile(Identity, Base):
    __tablename__ = 'observed_files'
    __table_args__ = (UniqueConstraint('scope_id', 'path'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    scope_id: Mapped[str] = mapped_column(ForeignKey('observation_scopes.id', ondelete='CASCADE'), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    scan_generation: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(40), default='available')
    size: Mapped[int] = mapped_column(Integer, default=0)
    stat: Mapped[dict] = mapped_column(JSON, default=dict)
    content: Mapped[bytes|None] = mapped_column(LargeBinary, nullable=True)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    parse_state: Mapped[str] = mapped_column(String(40), default='metadata_only')
    attribution: Mapped[str] = mapped_column(String(40), default='external_observation')
    action_id: Mapped[str|None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[str|None] = mapped_column(String(40), nullable=True)
    attempt_id: Mapped[str|None] = mapped_column(String(64), nullable=True)

class ReporterPolicy(Base):
    __tablename__ = 'reporter_policies'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    last_dispatch: Mapped[float] = mapped_column(Float, default=0)
    last_cursor: Mapped[int] = mapped_column(Integer, default=0)

class ReportJob(Identity, Base):
    __tablename__ = 'report_jobs'
    __table_args__ = (UniqueConstraint('project_id', 'request_id'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    request_id: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default='queued', index=True)
    epoch: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[int] = mapped_column(Integer)
    dependencies: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str|None] = mapped_column(String(120), nullable=True)
    lease_owner: Mapped[str|None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)

class ReportRequest(Identity, Base):
    __tablename__ = 'report_requests'
    __table_args__ = (UniqueConstraint('project_id','request_id'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),index=True)
    request_id: Mapped[str] = mapped_column(String(160))
    job_id: Mapped[str] = mapped_column(ForeignKey('report_jobs.id',ondelete='CASCADE'),index=True)

class ReportDispatch(Base):
    """Two service leases survive project/job deletion until inference returns."""
    __tablename__ = 'report_dispatch_slots'
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    job_id: Mapped[str|None] = mapped_column(String(64),nullable=True)
    owner: Mapped[str|None] = mapped_column(String(64),nullable=True)
    lease_until: Mapped[float] = mapped_column(Float,default=0)
