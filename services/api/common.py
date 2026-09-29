import json
import os
import secrets
from pathlib import Path
from sqlalchemy import select, delete, func
from fastapi import HTTPException
from .db import *
from .config import settings

def error(code,message,status=400,suggestion='',retryable=False):
    raise HTTPException(status,{'code':code,'message':str(message),'retryable':retryable,'suggestion':suggestion})
def get(s,model,ident):
    obj=s.get(model,ident)
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

def graph_from_db(s,p):
    graph={'project_id':p.id,'revision':p.revision,'nodes':[asdict(n) for n in s.scalars(select(Node).where(Node.project_id==p.id))], 'edges':[asdict(e) for e in s.scalars(select(Edge).where(Edge.project_id==p.id))], 'branches':[asdict(b) for b in s.scalars(select(Branch).where(Branch.project_id==p.id))]}
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
    sequence=(s.scalar(select(func.max(Event.sequence)).where(Event.project_id==project_id)) or 0)+1
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

def make_project(s,name,goal='',description='',**kwargs):
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
    for cls in (Figure,PaperDocument,ResearchClaim,Analysis):
        for obj in s.scalars(select(cls).where(cls.project_id==project_id)):
            text=json.dumps(obj.data,ensure_ascii=False)
            if origin in text or cls is PaperDocument:
                obj.status='needs_update'; obj.data={**obj.data,'stale_reason':f'Upstream material changed: {origin}'}
    emit(s,project_id,'artifact_changed',{'source':origin})
