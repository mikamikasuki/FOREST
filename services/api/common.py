import json
import os
import re
import secrets
from pathlib import Path
from sqlalchemy import select, delete, func
from fastapi import HTTPException
from .db import *
from .config import settings

DELETED_FILE_REVISION_ORIGIN='deleted'
RENAMED_FILE_REVISION_ORIGIN='renamed'
FILE_REVISION_TOMBSTONE_ORIGINS=frozenset((
    DELETED_FILE_REVISION_ORIGIN, RENAMED_FILE_REVISION_ORIGIN,
))

def effective_file_origin(origin, fallback):
    """Return provenance for current bytes, not a retained path tombstone."""
    if origin in FILE_REVISION_TOMBSTONE_ORIGINS:
        return fallback
    return origin or fallback

ZERO_PADDED_NUMERIC=re.compile(r'^[+-]?0\d')

def infer_csv_column_types(frame):
    """Restore numeric/boolean columns on a raw-text (dtype=str) CSV frame.

    Default read_csv type inference silently rewrites zero-padded text
    identifiers (02108 -> 2108, 000123 -> 123, 0000 -> 0), corrupting the
    raw-data readback. Reading as raw text and converting only columns
    without zero-padded values preserves the stored representation while
    genuinely numeric columns stay numeric for sorting and display."""
    import pandas as pd
    for column in frame.columns:
        series=frame[column]
        if not pd.api.types.is_string_dtype(series): continue
        values=series.dropna()
        if values.empty or values.str.contains(ZERO_PADDED_NUMERIC).any(): continue
        try: frame[column]=pd.to_numeric(series)
        except (TypeError,ValueError,OverflowError):
            if values.isin(('True','False')).all():
                frame[column]=series.map({'True':True,'False':False})
    return frame

def error(code,message,status=400,suggestion='',retryable=False):
    raise HTTPException(status,{'code':code,'message':str(message),'retryable':retryable,'suggestion':suggestion})
def get(s,model,ident,*,for_update=False):
    obj=s.get(model,ident,with_for_update=True) if for_update else s.get(model,ident)
    if obj is None: error('NOT_FOUND',f'{model.__name__} {ident} does not exist',404)
    return obj
def project_dir(project_id):
    if not project_id or any(c not in '0123456789abcdef-' for c in project_id): error('INVALID_ID','Invalid project identifier')
    return settings.data_dir/'projects'/project_id

def safe_path(root:Path,relative:str,must_exist=False):
    root=root.resolve()
    candidate=(root/relative).resolve()
    if not candidate.is_relative_to(root): error('PATH_ESCAPE','Path escapes project workspace',403)
    if must_exist and not candidate.exists(): error('MISSING_ARTIFACT',f'Missing project file: {relative}',404)
    return candidate

def graph_from_db(s,p,*,refresh=False):
    def rows(model):
        return s.scalars(select(model).where(model.project_id==p.id).execution_options(populate_existing=refresh))
    graph={'project_id':p.id,'revision':p.revision,'nodes':[asdict(n) for n in rows(Node)], 'edges':[asdict(e) for e in rows(Edge)], 'branches':[asdict(b) for b in rows(Branch)]}
    graph.update(p.graph_meta or {})
    graph['goal']=p.goal
    graph['budget']=p.budget
    return graph

def save_graph(s,p,graph):
    p.revision=graph['revision']; p.updated_at=now()
    p.graph_meta={k:v for k,v in graph.items() if k not in ('project_id','revision','nodes','edges','branches')}
    for key,model in [('branches',Branch),('nodes',Node),('edges',Edge)]:
        old={o.id:o for o in s.scalars(select(model).where(model.project_id==p.id))}
        fields=set(c.key for c in model.__table__.columns)
        for values in graph[key]:
            ident=values['id']; obj=old.pop(ident,None)
            if obj is None: obj=model(id=ident,project_id=p.id); s.add(obj)
            for field,val in values.items():
                if field in fields and field not in ('id','project_id','created_at','updated_at'): setattr(obj,field,val)
            if 'extra' in fields: obj.extra={k:v for k,v in values.items() if k not in fields}
        for obj in old.values(): s.delete(obj)
    s.flush()

def emit(s,project_id,event_type,data):
    sequence=allocate_event_sequence(s,project_id)
    s.add(Event(project_id=project_id,sequence=sequence,type=event_type,data=data))
    if sequence%100==0: s.execute(delete(Event).where(Event.project_id==project_id,Event.sequence<sequence-500))

def secret_store():
    p=settings.data_dir/'secrets.json'
    if not p.exists(): p.write_text('{}'); p.chmod(0o600)
    return p

def put_secret(value,ref=None):
    ref=ref or uid(); p=secret_store(); data=json.loads(p.read_text()); data[ref]=value
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(data)); tmp.chmod(0o600); tmp.replace(p); return ref

def read_secret(ref):
    return json.loads(secret_store().read_text()).get(ref,'') if ref else ''

def owner_token():
    if settings.owner_token: return settings.owner_token
    path=settings.data_dir/'owner-token'
    if not path.exists(): path.write_text(secrets.token_urlsafe(40)); path.chmod(0o600)
    return path.read_text().strip()

def validate_project_budget(budget):
    import math
    if not isinstance(budget,dict): error('INVALID_BUDGET','Project budget must be an object',422)
    for field in ('seconds','cost_usd'):
        value=budget.get(field)
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):
            error('INVALID_BUDGET',field+' must be a finite nonnegative number or null',422)
    value=budget.get('max_runs')
    if value is not None and (type(value) is not int or value<0):
        error('INVALID_BUDGET','max_runs must be a nonnegative integer or null',422)
    if 'allow_paid' in budget and type(budget['allow_paid']) is not bool:
        error('INVALID_BUDGET','allow_paid must be a boolean',422)


def make_project(s,name,goal='',description='',**kwargs):
    validate_project_budget(kwargs.get('budget',{}))
    from research.agents.tool_policy import validate_permissions
    try: validate_permissions(kwargs.get('config', {}))
    except ValueError as exc: error('INVALID_TOOL_POLICY', str(exc), 422)
    preference=s.get(Preference,'settings')
    default_provider=preference.value.get('default_provider_id') if preference else None
    if default_provider:
        config=dict(kwargs.get('config',{}))
        if not config.get('provider_id') and s.get(Provider,default_provider): config['provider_id']=default_provider
        kwargs['config']=config
    p=Project(name=name,goal=goal,description=description,**kwargs); s.add(p); s.flush()
    bid=uid(); workspace=f'branches/{bid}/workspace'; safe_path(project_dir(p.id),workspace).mkdir(parents=True,exist_ok=True)
    s.add(Branch(id=bid,project_id=p.id,name='Main',workspace=workspace,is_main=True))
    return p

def touch_dependents(s,project_id,origin):
    from services.interventions.dependencies import invalidate_dependents
    impacts=invalidate_dependents(s,project_id,origin)
    emit(s,project_id,'artifact_changed',{'source':origin,'dependencies':impacts})
