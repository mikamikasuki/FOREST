from __future__ import annotations
import datetime as dt
import uuid
from sqlalchemy import (create_engine, event, String, Text, Integer, Float, Boolean, JSON,
                        ForeignKey, UniqueConstraint, Index, case, func, select, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings

def uid(): return str(uuid.uuid4())
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
class Base(DeclarativeBase): pass
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={'check_same_thread':False} if settings.database_url.startswith('sqlite') else {})
if settings.database_url.startswith('sqlite'):
    @event.listens_for(engine,'connect')
    def sqlite_setup(conn, record):
        conn.execute('PRAGMA foreign_keys=ON'); conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA busy_timeout=10000')
Session = sessionmaker(engine, expire_on_commit=False)
def begin_sqlite_write(session):
    """Serialize a read/modify/write before reading mutable JSON on SQLite."""
    if session.bind.dialect.name=='sqlite':
        connection=session.connection()
        if not connection.connection.driver_connection.in_transaction:
            with session.no_autoflush: session.execute(text('BEGIN IMMEDIATE'))

class Identity:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now, onupdate=now)
class Project(Identity, Base):
    __tablename__='projects'
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default='')
    goal: Mapped[str] = mapped_column(Text, default='')
    current_direction: Mapped[str] = mapped_column(Text, default='')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(20), default='assisted')
    budget: Mapped[dict] = mapped_column(JSON, default=lambda:{'allow_paid':False})
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    graph_meta: Mapped[dict] = mapped_column(JSON, default=dict)
class Branch(Identity,Base):
    __tablename__='branches'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),index=True)
    name: Mapped[str] = mapped_column(String(240))
    root_node_id: Mapped[str|None] = mapped_column(String(64),nullable=True)
    status: Mapped[str] = mapped_column(String(30),default='active')
    workspace: Mapped[str] = mapped_column(Text)
    is_main: Mapped[bool] = mapped_column(Boolean,default=False)
    config: Mapped[dict] = mapped_column(JSON,default=dict)
    extra: Mapped[dict] = mapped_column(JSON,default=dict)
class Node(Identity,Base):
    __tablename__='nodes'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),index=True)
    branch_id: Mapped[str] = mapped_column(String(64),index=True)
    type: Mapped[str] = mapped_column(String(80),default='idea')
    title: Mapped[str] = mapped_column(String(400))
    instructions: Mapped[str] = mapped_column(Text,default='')
    revision: Mapped[int] = mapped_column(Integer,default=0)
    config: Mapped[dict] = mapped_column(JSON,default=dict)
    position: Mapped[dict] = mapped_column(JSON,default=lambda:{'x':0,'y':0})
    execution_status: Mapped[str] = mapped_column(String(40),default='idle')
    research_status: Mapped[str] = mapped_column(String(40),default='unevaluated')
    deliverable_status: Mapped[str] = mapped_column(String(40),default='draft')
    archived: Mapped[bool] = mapped_column(Boolean,default=False)
    inputs: Mapped[list] = mapped_column(JSON,default=list)
    outputs: Mapped[list] = mapped_column(JSON,default=list)
    context_overrides: Mapped[dict] = mapped_column(JSON,default=dict)
    comments: Mapped[list] = mapped_column(JSON,default=list)
    extra: Mapped[dict] = mapped_column(JSON,default=dict)
class Edge(Identity,Base):
    __tablename__='edges'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),index=True)
    source: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(40),default='depends_on')
    input_mapping: Mapped[dict] = mapped_column(JSON,default=dict)
class CommandReceipt(Identity,Base):
    __tablename__='command_receipts'
    __table_args__=(UniqueConstraint('project_id','request_id'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'))
    request_id: Mapped[str] = mapped_column(String(160))
    response: Mapped[dict] = mapped_column(JSON)
class TaskRun(Identity,Base):
    __tablename__='task_runs'
    __table_args__=(UniqueConstraint('project_id','request_id'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),index=True)
    node_id: Mapped[str|None] = mapped_column(String(64),nullable=True)
    branch_id: Mapped[str|None] = mapped_column(String(64),nullable=True)
    request_id: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(80),default='agent')
    status: Mapped[str] = mapped_column(String(40),default='queued',index=True)
    config: Mapped[dict] = mapped_column(JSON,default=dict)
    node_revision: Mapped[int] = mapped_column(Integer,default=0)
    priority: Mapped[int] = mapped_column(Integer,default=0)
    dependencies: Mapped[list] = mapped_column(JSON,default=list)
    worker_id: Mapped[str|None] = mapped_column(String(80),nullable=True)
    pid: Mapped[int|None] = mapped_column(Integer,nullable=True)
    process_created: Mapped[float|None] = mapped_column(Float,nullable=True)
    started_at: Mapped[str|None] = mapped_column(String(40),nullable=True)
    finished_at: Mapped[str|None] = mapped_column(String(40),nullable=True)
    exit_code: Mapped[int|None] = mapped_column(Integer,nullable=True)
    error: Mapped[str|None] = mapped_column(Text,nullable=True)
    output_path: Mapped[str] = mapped_column(Text,default='')
    metrics: Mapped[dict] = mapped_column(JSON,default=dict)
    resource: Mapped[dict] = mapped_column(JSON,default=dict)
class ToolExecution(Identity,Base):
    __tablename__='tool_executions'
    run_id: Mapped[str] = mapped_column(ForeignKey('task_runs.id',ondelete='CASCADE'),index=True)
    tool: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[dict] = mapped_column(JSON,default=dict)
    status: Mapped[str] = mapped_column(String(40),default='running')
    result: Mapped[dict] = mapped_column(JSON,default=dict)
    elapsed: Mapped[float|None] = mapped_column(Float,nullable=True)
class Worker(Identity,Base):
    __tablename__='workers'
    name: Mapped[str] = mapped_column(String(160))
    pid: Mapped[int] = mapped_column(Integer)
    heartbeat: Mapped[str] = mapped_column(String(40),default=now)
    capabilities: Mapped[dict] = mapped_column(JSON,default=dict)
class Resource(Identity):
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),index=True)
    title: Mapped[str] = mapped_column(Text,default='')
    revision: Mapped[int] = mapped_column(Integer,default=0)
    status: Mapped[str] = mapped_column(String(40),default='draft')
    data: Mapped[dict] = mapped_column(JSON,default=dict)
class SourcePaper(Resource,Base): __tablename__='source_papers'
class SourcePassage(Resource,Base): __tablename__='source_passages'
class ExperimentSpec(Resource,Base): __tablename__='experiment_specs'
class DatasetAsset(Resource,Base): __tablename__='dataset_assets'
class Hypothesis(Resource,Base): __tablename__='hypotheses'
class Derivation(Resource,Base): __tablename__='derivations'
class ResearchClaim(Resource,Base): __tablename__='research_claims'
class Figure(Resource,Base): __tablename__='figures'
class PaperDocument(Resource,Base): __tablename__='paper_documents'
class Analysis(Resource,Base): __tablename__='analyses'
class Review(Resource,Base): __tablename__='reviews'
class Provider(Identity,Base):
    __tablename__='providers'
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40),default='openai')
    base_url: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(240))
    credential_ref: Mapped[str|None] = mapped_column(String(80),nullable=True)
    allow_paid: Mapped[bool] = mapped_column(Boolean,default=False)
    status: Mapped[str] = mapped_column(String(40),default='untested')
    config: Mapped[dict] = mapped_column(JSON,default=dict)
class Host(Identity,Base):
    __tablename__='hosts'
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40),default='local')
    status: Mapped[str] = mapped_column(String(40),default='untested')
    config: Mapped[dict] = mapped_column(JSON,default=dict)
class ModelRequest(Identity,Base):
    __tablename__='model_requests'
    provider_id: Mapped[str] = mapped_column(ForeignKey('providers.id',ondelete='CASCADE'),index=True)
    project_id: Mapped[str|None] = mapped_column(String(64),nullable=True,index=True)
    run_id: Mapped[str|None] = mapped_column(String(64),nullable=True,index=True)
    model: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(40),default='reserved')
    reserved_microusd: Mapped[int] = mapped_column(Integer,default=0)
    estimated_microusd: Mapped[int|None] = mapped_column(Integer,nullable=True)
    request_id: Mapped[str|None] = mapped_column(String(240),nullable=True)
    response_id: Mapped[str|None] = mapped_column(String(240),nullable=True)
    details: Mapped[dict] = mapped_column(JSON,default=dict)
class Agent(Identity,Base):
    __tablename__='agents'
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(80))
    instructions: Mapped[str] = mapped_column(Text,default='')
    provider_id: Mapped[str|None] = mapped_column(String(64),nullable=True)
    tools: Mapped[list] = mapped_column(JSON,default=list)
    config: Mapped[dict] = mapped_column(JSON,default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
class Event(Identity,Base):
    __tablename__='events'
    __table_args__=(Index('uq_events_project_sequence','project_id','sequence',unique=True),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),index=True)
    sequence: Mapped[int] = mapped_column(Integer,default=0)
    type: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON,default=dict)
class EventSequence(Base):
    __tablename__='event_sequences'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer,default=0)
class FileRevision(Identity,Base):
    __tablename__='file_revisions'
    __table_args__=(UniqueConstraint('project_id','path'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'))
    path: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer,default=0)
    origin: Mapped[str] = mapped_column(String(40),default='user_edited')
class Share(Identity,Base):
    __tablename__='shares'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'))
    token: Mapped[str] = mapped_column(String(100),unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
    selection: Mapped[dict] = mapped_column(JSON,default=dict)
class Preference(Base):
    __tablename__='preferences'
    key: Mapped[str] = mapped_column(String(100),primary_key=True)
    value: Mapped[dict] = mapped_column(JSON,default=dict)
class Migration(Base):
    __tablename__='schema_versions'
    version: Mapped[int] = mapped_column(Integer,primary_key=True)
    applied_at: Mapped[str] = mapped_column(String(40),default=now)
RESOURCE_MODELS={'library':SourcePaper,'experiments':ExperimentSpec,'datasets':DatasetAsset,'ideas':Hypothesis,'theories':Derivation,'claims':ResearchClaim,'figures':Figure,'analyses':Analysis,'reviews':Review}
def asdict(obj, secrets=False):
    result={c.key:getattr(obj,c.key) for c in obj.__table__.columns}
    if isinstance(obj,Provider) and not secrets:
        result['has_key']=bool(result.pop('credential_ref',None))
    if 'extra' in result: result.update(result.pop('extra') or {})
    return result

def _event_cursor_insert(session):
    dialect=session.bind.dialect.name
    if dialect=='sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif dialect=='postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError(f'Event cursors are not supported by the {dialect} database dialect')
    return insert

def _seed_event_cursor(session, project_id, sequence):
    insert=_event_cursor_insert(session)
    statement=insert(EventSequence).values(project_id=project_id,sequence=sequence)
    excluded=statement.excluded.sequence
    statement=statement.on_conflict_do_update(
        index_elements=[EventSequence.project_id],
        set_={'sequence':case((EventSequence.sequence<excluded,excluded),else_=EventSequence.sequence)})
    session.execute(statement)

def allocate_event_sequence(session, project_id):
    """Atomically allocate the next per-project SSE cursor across DB writers."""
    insert=_event_cursor_insert(session)
    initial=(select(func.coalesce(func.max(Event.sequence),0)+1)
             .where(Event.project_id==project_id).scalar_subquery())
    statement=insert(EventSequence).values(project_id=project_id,sequence=initial)
    statement=statement.on_conflict_do_update(
        index_elements=[EventSequence.project_id],
        set_={'sequence':EventSequence.sequence+1})
    return session.execute(statement.returning(EventSequence.sequence)).scalar_one()

def repair_event_sequences(session):
    """Move legacy duplicate cursors above the old high-water mark."""
    projects=session.execute(
        select(Event.project_id,func.max(Event.sequence))
        .group_by(Event.project_id).order_by(Event.project_id)).all()
    for project_id, maximum in projects:
        _seed_event_cursor(session,project_id,maximum)
        duplicate_sequences=session.execute(
            select(Event.sequence,func.count())
            .where(Event.project_id==project_id)
            .group_by(Event.sequence).having(func.count()>1).order_by(Event.sequence)).all()
        next_sequence=maximum
        for duplicate_sequence,_ in duplicate_sequences:
            rows=list(session.scalars(
                select(Event).where(Event.project_id==project_id,Event.sequence==duplicate_sequence)
                .order_by(Event.created_at,Event.id)))
            for row in rows[1:]:
                next_sequence+=1
                row.sequence=next_sequence
        cursor=session.get(EventSequence,project_id)
        if cursor.sequence<next_sequence:
            cursor.sequence=next_sequence
    session.flush()

def migrate():
    from services.observation import models  # Register additive reporting tables.
    Base.metadata.create_all(engine)
    with Session.begin() as s:
        if engine.dialect.name=='sqlite':
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        elif engine.dialect.name=='postgresql':
            s.execute(text('SELECT pg_advisory_xact_lock(824721)'))
        if not s.get(Migration,1): s.add(Migration(version=1))
        if not s.get(Migration,3): s.add(Migration(version=3))
        if not s.get(Migration,2):
            repair_event_sequences(s)
            s.add(Migration(version=2))
        s.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_events_project_sequence ON events (project_id, sequence)'))
if __name__=='__main__': migrate(); print('FOREST database schema 3 ready')
