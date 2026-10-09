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

def utf8_page_prefix(data:bytes):
    """Length of the longest leading part of `data` that ends on a complete UTF-8 character.

    A character whose bytes are cut by a page limit is held back (at most three bytes) so the next
    page re-reads it whole. Trailing bytes that cannot start a valid character are not held back;
    they decode with replacement like any other invalid input."""
    size=len(data); index=size-1; skipped=0
    while index>=0 and skipped<3 and 0x80<=data[index]<=0xBF:
        index-=1; skipped+=1
    if index<0: return size
    lead=data[index]
    if lead<0x80: return size
    if 0xC2<=lead<=0xDF: need=2
    elif 0xE0<=lead<=0xEF: need=3
    elif 0xF0<=lead<=0xF4: need=4
    else: return size
    available=size-index
    if available>=need: return size
    tail=data[index:]
    if any(not 0x80<=byte<=0xBF for byte in tail[1:]): return size
    if len(tail)>1 and ((lead==0xE0 and tail[1]<0xA0) or (lead==0xED and tail[1]>0x9F) or (lead==0xF0 and tail[1]<0x90) or (lead==0xF4 and tail[1]>0x8F)): return size
    return index

def graph_from_db(s,p,*,refresh=False):
    def rows(model):
        return s.scalars(select(model).where(model.project_id==p.id).execution_options(populate_existing=refresh))
    graph={'project_id':p.id,'revision':p.revision,'nodes':[asdict(n) for n in rows(Node)], 'edges':[asdict(e) for e in rows(Edge)], 'branches':[asdict(b) for b in rows(Branch)]}
    graph.update(p.graph_meta or {})
    graph['goal']=p.goal
    graph['budget']=p.budget
    return graph

def attach_context_ideas(s,p,graph,overrides=None):
    """Attach only saved idea records explicitly referenced by Agent context."""
    refs=[ref for node in graph.get('nodes',[]) for ref in node.get('inputs',[])]
    for ref in (overrides or {}).get('imports',[]):
        refs.append(ref)
    for ref in (overrides or {}).get('materials',(overrides or {}).get('add',[])):
        refs.append(ref)
    ids={ref.get('id') for ref in refs if isinstance(ref,dict)
         and ref.get('kind')=='idea' and isinstance(ref.get('id'),str)}
    if not ids:return graph
    graph['_context_ideas']={row.id:{'id':row.id,'title':row.title,'revision':row.revision,
        'status':row.status,'data':row.data} for row in s.scalars(
            select(Hypothesis).where(Hypothesis.project_id==p.id,Hypothesis.id.in_(ids)))}
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

# Internal locations whose directory names are copied identifiers, not authored text.
IDENTIFIER_DIRECTORIES=('runs','branches','figures','.forest-bases','.merge-staging')

def remap_identifiers(value,mapping,*,project_id=None,new_project_id=None):
    """Copy references to remapped identifiers while leaving authored text untouched.

    A string is a reference in exactly two cases: it is itself a copied identifier
    (node/run/branch/resource ids, edge endpoints, dependency lists) or it names an
    internal location whose directory is an identifier (``runs/<id>/...``,
    ``branches/<id>/workspace``). Prose that merely mentions an identifier keeps its
    original wording, so a copy cannot silently rewrite a provenance note (#126).
    """
    if isinstance(value,dict):
        return {key:remap_identifiers(item,mapping,project_id=project_id,new_project_id=new_project_id) for key,item in value.items()}
    if isinstance(value,list):
        return [remap_identifiers(item,mapping,project_id=project_id,new_project_id=new_project_id) for item in value]
    if not isinstance(value,str):
        return value
    if project_id is not None and value==project_id: return new_project_id
    if value in mapping: return mapping[value]
    parts=value.split('/')
    for index in range(1,len(parts)):
        if parts[index-1] not in IDENTIFIER_DIRECTORIES: continue
        if project_id is not None and parts[index]==project_id: parts[index]=new_project_id
        elif parts[index] in mapping: parts[index]=mapping[parts[index]]
    return '/'.join(parts)

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

def delete_secret(ref):
    if not ref: return
    p=settings.data_dir/'secrets.json'
    if not p.exists(): return
    data=json.loads(p.read_text())
    if ref not in data: return
    data.pop(ref,None)
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(data)); tmp.chmod(0o600); tmp.replace(p)

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
    if kwargs.get('mode') is None:
        configured_mode=preference.value.get('default_mode') if preference else None
        kwargs['mode']=configured_mode if configured_mode in ('auto','assisted','manual') else 'assisted'
    default_provider=preference.value.get('default_provider_id') if preference else None
    config=dict(kwargs.get('config',{}))
    if default_provider and not config.get('provider_selection_required'):
        provider=s.get(Provider,default_provider)
        if not config.get('provider_id') and provider and provider.status!='retired': config['provider_id']=default_provider
    kwargs['config']=config
    p=Project(name=name,goal=goal,description=description,**kwargs); s.add(p); s.flush()
    bid=uid(); workspace=f'branches/{bid}/workspace'; safe_path(project_dir(p.id),workspace).mkdir(parents=True,exist_ok=True)
    s.add(Branch(id=bid,project_id=p.id,name='Main',workspace=workspace,is_main=True))
    return p

def touch_dependents(s,project_id,origin):
    from services.interventions.dependencies import invalidate_dependents
    impacts=invalidate_dependents(s,project_id,origin)
    emit(s,project_id,'artifact_changed',{'source':origin,'dependencies':impacts})
