from __future__ import annotations
import asyncio
import datetime as dt
import json
import os
import pty
import select as io_select
import shutil
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
import httpx
import psutil
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, update, func, text as sql_text
from .common import *
from .schemas import *
from .files import router as files_router
from services.worker.scheduler import enqueue,enqueue_nodes,enqueue_selected,ACTIVE,TERMINAL,requested_task_timeout,reserve_project_time,finalize_cancelled_run_elapsed,_elapsed_seconds_at
from research.agents.policy import ROLES,TOOLS
from research.agents.defaults import default_config, upgrade_default_tools, explicit_agent_config
from research.agents.budget import interrupt_run_reservations
from research.kernel import GraphError

@asynccontextmanager
async def lifespan(app):
    migrate()
    with Session.begin() as s:
        if not s.scalar(select(Host)): s.add(Host(name='Local Machine',kind='local',status='connected',config={'platform':os.uname().sysname,'isolation':'trusted_local_process'}))
        if settings.model and not s.scalar(select(Provider)):
            s.add(Provider(name='Local Ollama Model',kind='ollama',base_url=settings.ollama_url,model=settings.model,status='untested'))
        if not s.scalar(select(Agent)):
            for role,instructions in ROLES.items(): s.add(Agent(name=role,role=role,instructions=instructions,tools=list(TOOLS),config=default_config(role)))
        else:
            existing=list(s.scalars(select(Agent)))
            for agent in existing: upgrade_default_tools(agent)
            roles={agent.role for agent in existing}
            for role,instructions in ROLES.items():
                if role not in roles:
                    s.add(Agent(name=role,role=role,instructions=instructions,tools=list(TOOLS),config=default_config(role)))
    from services.observation.service import ObservationService
    observer=ObservationService(); observer.start()
    try:
        yield
    finally:
        observer.close()
    from .terminal import close_all
    close_all()
app=FastAPI(title='FOREST Research API',version='0.1.0',lifespan=lifespan)
TRUSTED_HOSTS={'localhost','127.0.0.1','::1','testserver'}
def authorized(request):
    token=owner_token()
    return request.cookies.get('forest_owner')==token or request.headers.get('authorization')=='Bearer '+token
@app.middleware('http')
async def security(request,call_next):
    route=request.url.path
    public=route in ('/api/health','/api/auth/login','/api/auth/status') or route.startswith('/api/shares/')
    hostname=request.url.hostname
    origin=request.headers.get('origin')
    if route.startswith('/api') and not public:
        if hostname not in TRUSTED_HOSTS and not authorized(request): return JSONResponse({'detail':{'code':'UNAUTHORIZED','message':'Owner authentication required','retryable':False}},401)
        if origin and (urlparse(origin).hostname not in TRUSTED_HOSTS) and urlparse(origin).hostname!=hostname: return JSONResponse({'detail':{'code':'ORIGIN_DENIED','message':'Cross-origin write/read denied','retryable':False}},403)
        forwarded=any(request.headers.get(name) for name in ('forwarded','x-forwarded-for','x-forwarded-host','x-forwarded-proto'))
        local=request.client and request.client.host in ('127.0.0.1','::1','testclient') and hostname in TRUSTED_HOSTS and not forwarded
        if not authorized(request) and not local: return JSONResponse({'detail':{'code':'UNAUTHORIZED','message':'Owner authentication required','retryable':False}},401)
        response=await call_next(request)
        if local and not authorized(request): response.set_cookie('forest_owner',owner_token(),httponly=True,samesite='strict',max_age=86400*14)
    else: response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'; response.headers['Referrer-Policy']='same-origin'
    return response
@app.exception_handler(GraphError)
async def graph_error(request,exc):
    return JSONResponse({'detail':{**exc.as_dict(),'suggestion':'Reload current graph state and reapply the edit.' if exc.code=='revision_conflict' else ''}},exc.status_code)
@app.post('/api/auth/login')
def auth_login(request:Request,body:dict=Body(...)):
    from fastapi.responses import JSONResponse
    if not secrets.compare_digest(str(body.get('token','')),owner_token()): error('INVALID_TOKEN','Owner token is incorrect',401)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).hostname!=request.url.hostname: error('ORIGIN_DENIED','Use the same origin to sign in',403)
    response=JSONResponse({'authenticated':True}); response.set_cookie('forest_owner',owner_token(),httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=86400*14); return response
@app.get('/api/auth/status')
def auth_status(request:Request): return {'authenticated':authorized(request)}
@app.exception_handler(Exception)
async def unexpected(request,exc):
    from research.kernel import GraphError
    if isinstance(exc,GraphError): return JSONResponse({'detail':{'code':exc.code,'message':exc.message,'retryable':False,'details':getattr(exc,'detail',{})}},getattr(exc,'status_code',400))
    return JSONResponse({'detail':{'code':'INTERNAL_ERROR','message':str(exc)[:1500],'retryable':False,'suggestion':'Inspect the server output and retry after correcting the cause.'}},500)
@app.get('/api/health')
def health():
    with Session() as s: s.execute(select(1))
    return {'status':'ok','database':engine.dialect.name,'version':'0.1.0'}
@app.get('/api/projects')
def projects(archived:bool|None=None,limit:int=100,offset:int=0):
    with Session() as s:
        q=select(Project).order_by(Project.updated_at.desc()).limit(min(limit,200)).offset(max(offset,0))
        if archived is not None: q=q.where(Project.archived==archived)
        return [asdict(p) for p in s.scalars(q)]
@app.post('/api/projects')
def create_project(body:ProjectCreate):
    with Session.begin() as s:
        p=make_project(s,**body.model_dump()); return asdict(p)
@app.get('/api/projects/{ident}')
def project(ident:str):
    with Session() as s:
        p=asdict(get(s,Project,ident)); p.pop('graph_meta',None); return p
@app.patch('/api/projects/{ident}')
def edit_project(ident:str,body:dict=Body(...)):
    if 'mode' in body and body['mode'] not in ('auto','assisted','manual'):
        error('INVALID_MODE','Project mode must be auto, assisted, or manual',422)
    with Session.begin() as s:
        p=s.scalar(select(Project).where(Project.id==ident).with_for_update())
        if not p: error('NOT_FOUND','Project missing',404)
        if body.get('expected_revision',p.revision)!=p.revision: error('REVISION_CONFLICT','Project changed',409)
        for k in ('name','description','goal','current_direction','archived','mode','budget','config'):
            if k in body: setattr(p,k,body[k])
        if 'goal' in body:
            for n in s.scalars(select(Node).where(Node.project_id==ident)): n.context_overrides={**n.context_overrides,'needs_refresh':True}
        p.revision+=1; emit(s,ident,'project_changed',{'revision':p.revision}); s.flush(); return asdict(p)
@app.delete('/api/projects/{ident}')
def delete_project(ident:str):
    from runners.local import stop_group
    with Session.begin() as s:
        if s.bind.dialect.name=='sqlite': s.execute(sql_text('BEGIN IMMEDIATE'))
        p=s.scalar(select(Project).where(Project.id==ident).with_for_update())
        if not p: error('NOT_FOUND','Project missing',404)
        for run in s.scalars(select(TaskRun).where(TaskRun.project_id==ident)):
            if run.status not in ACTIVE:
                interrupt_run_reservations(run.id,session=s)
                continue
            from research.execution.process_manager import process_manager
            process_manager(safe_path(project_dir(run.project_id),run.output_path+'/workspace'),run.config).cancel_all()
            if run.config.get('execution_backend')=='container':
                from runners.container import cancel_container
                cancel_container(run.config,safe_path(project_dir(run.project_id),run.output_path))
            if run.config.get('remote'):
                from runners.remote import cancel_remote
                cancel_remote(run.config,safe_path(project_dir(run.project_id),run.output_path))
            if run.pid: stop_group(run.pid,run.process_created)
            interrupt_run_reservations(run.id,session=s)
        s.execute(update(ModelRequest).where(ModelRequest.project_id==ident,ModelRequest.status=='reserved').values(status='uncertain'))
        s.delete(p)
    shutil.rmtree(project_dir(ident),ignore_errors=True); return {'deleted':ident}
@app.post('/api/projects/{ident}/duplicate')
def duplicate_project(ident:str):
    import copy
    with Session.begin() as s:
        p=get(s,Project,ident)
        graph=copy.deepcopy(graph_from_db(s,p)); graph.pop('_history',None)
        resources={key:[asdict(r) for r in s.scalars(select(model).where(model.project_id==p.id))] for key,model in RESOURCE_MODELS.items()}
        papers=[asdict(r) for r in s.scalars(select(PaperDocument).where(PaperDocument.project_id==p.id))]
        runs=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.project_id==p.id))]
        mapping={item['id']:uid() for key in ('nodes','edges','branches') for item in graph[key]}
        for rows in list(resources.values())+[papers,runs]:
            for item in rows: mapping.setdefault(item['id'],uid())
        mode=p.mode if p.mode in ('auto','assisted','manual') else 'assisted'
        new=make_project(s,p.name+' · Copy',p.goal,p.description,mode=mode,budget=copy.deepcopy(p.budget),config=copy.deepcopy(p.config))
        def remap(value):
            if isinstance(value,dict): return {k:remap(v) for k,v in value.items()}
            if isinstance(value,list): return [remap(v) for v in value]
            if isinstance(value,str):
                value=value.replace(p.id,new.id)
                for original,replacement in mapping.items(): value=value.replace(original,replacement)
            return value
        graph=remap(graph); graph['revision']=0
        for node in graph['nodes']:
            if node.get('execution_status') in ACTIVE:
                node['copied_execution_status']=node['execution_status']; node['execution_status']='interrupted'
        source=project_dir(p.id); destination=project_dir(new.id)
        for path in source.rglob('*'):
            if path.is_symlink(): continue
            relative=path.relative_to(source).as_posix()
            target=safe_path(destination,remap(relative))
            if path.is_dir(): target.mkdir(parents=True,exist_ok=True)
            elif path.is_file(): target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        save_graph(s,new,graph)
        for key,rows in resources.items():
            model=RESOURCE_MODELS[key]
            for row in rows: s.add(model(id=mapping[row['id']],project_id=new.id,title=row['title'],status=row['status'],data=remap(row['data'])))
        for row in papers: s.add(PaperDocument(id=mapping[row['id']],project_id=new.id,title=row['title'],status=row['status'],data=remap(row['data'])))
        for row in runs:
            run_id=mapping[row['id']]
            s.add(TaskRun(id=run_id,project_id=new.id,node_id=remap(row.get('node_id')),branch_id=remap(row.get('branch_id')),
                request_id='copy:'+run_id,kind=row['kind'],status=row['status'] if row['status'] in TERMINAL else 'interrupted',
                config={**remap(row['config']),'origin':'copied_run','original_run_id':row['id']},node_revision=row['node_revision'],
                created_at=row['created_at'],started_at=row.get('started_at'),finished_at=row.get('finished_at'),
                output_path=remap(row['output_path']),dependencies=remap(row.get('dependencies',[])),metrics=remap(row['metrics']),resource=row['resource'],exit_code=row['exit_code']))
        s.flush(); return asdict(new)
@app.get('/api/projects/{ident}/graph')
def graph(ident:str):
    with Session() as s: g=graph_from_db(s,get(s,Project,ident)); g.pop('_history',None); return g
@app.post('/api/projects/{ident}/graph/preview')
def preview(ident:str,body:GraphCommand):
    from research.kernel import GraphCommandService
    with Session() as s:
        if body.project_id and body.project_id!=ident: error('CROSS_PROJECT','The command belongs to a different project',422)
        p=get(s,Project,ident); cmd=body.model_dump(); cmd['project_id']=ident
        return GraphCommandService(graph_from_db(s,p),project_dir(ident)).preview(cmd)
@app.post('/api/projects/{ident}/graph/commands')
def command(ident:str,body:GraphCommand):
    from research.kernel import GraphCommandService
    with Session.begin() as s:
        if body.project_id and body.project_id!=ident: error('CROSS_PROJECT','The command belongs to a different project',422)
        p=lock_graph_project(s,ident)
        receipt=s.scalar(select(CommandReceipt).where(CommandReceipt.project_id==ident,CommandReceipt.request_id==body.request_id))
        if receipt: return receipt.response
        cmd=body.model_dump(); cmd['project_id']=ident
        result=GraphCommandService(graph_from_db(s,p),project_dir(ident)).apply(cmd); save_graph(s,p,result['graph'])
        apply_graph_mutation_effects(s,ident,result['impact'])
        result['graph']=graph_from_db(s,p)
        run_ids=[]
        if body.run:
            run_ids=[r.id for r in enqueue_selected(s,result.get('run_nodes',[]),body.request_id+':run')]
        public={**result,'graph':{k:v for k,v in result['graph'].items() if k!='_history'},'run_ids':run_ids}
        s.add(CommandReceipt(project_id=ident,request_id=body.request_id,response=public)); emit(s,ident,'node_changed',{'revision':p.revision}); return public
@app.get('/api/nodes/{ident}')
def node(ident:str):
    with Session() as s: return asdict(get(s,Node,ident))
@app.patch('/api/nodes/{ident}')
def edit_node(ident:str,body:dict=Body(...)):
    with Session() as s: n=get(s,Node,ident); p=get(s,Project,n.project_id); pid=p.id; rev=p.revision
    body=dict(body); expected=body.pop('expected_revision',rev)
    return command(pid,GraphCommand(request_id=uid(),expected_revision=expected,operation='edit_node',targets=[ident],params=body))
@app.get('/api/nodes/{ident}/context')
def context(ident:str):
    from research.kernel import ContextBuilder
    with Session() as s:
        n=get(s,Node,ident); p=get(s,Project,n.project_id); g=graph_from_db(s,p); g['goal']=p.goal; g['budget']=p.budget
        return ContextBuilder(g,project_dir(p.id)).build(ident,n.config.get('role','Researcher'),n.context_overrides)
@app.post('/api/nodes/{ident}/context/rebuild')
def context_rebuild(ident:str,body:dict=Body(default={})):
    with Session.begin() as s:
        n=get(s,Node,ident); n.context_overrides={**n.context_overrides,**body,'needs_refresh':False}; emit(s,n.project_id,'context_changed',{'node_id':ident})
    return context(ident)
@app.post('/api/nodes/{ident}/run')
def run_node(ident:str,body:RunRequest):
    with Session.begin() as s:
        runs=enqueue_nodes(s,ident,body.scope,body.request_id,body.config); return asdict(runs[0]) if len(runs)==1 else {'runs':[asdict(r) for r in runs],'run_ids':[r.id for r in runs]}
@app.post('/api/branches/{ident}/run')
def run_branch(ident:str,body:RunRequest):
    with Session.begin() as s:
        b=get(s,Branch,ident); nodes=list(s.scalars(select(Node).where(Node.branch_id==ident,Node.archived==False)))
        if not nodes: error('EMPTY_BRANCH','Add a node first')
        return {'runs':[asdict(r) for r in enqueue_nodes(s,b.root_node_id or nodes[0].id,'descendants',body.request_id,body.config)]}
@app.get('/api/branches/compare')
def compare_branches(left:str,right:str):
    from research.kernel import BranchWorkspace
    with Session() as s:
        a=get(s,Branch,left); b=get(s,Branch,right)
        if a.project_id!=b.project_id: error('CROSS_PROJECT','Branches must belong to the same project')
        p=get(s,Project,a.project_id)
        comparison=BranchWorkspace(project_dir(a.project_id),graph_from_db(s,p)).compare(left,right)
        runs=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.branch_id.in_([left,right])))]
        return {**comparison,'left':asdict(a),'right':asdict(b),'runs':runs}
@app.get('/api/projects/{ident}/runs')
def runs(ident:str,limit:int=100,offset:int=0):
    with Session() as s: get(s,Project,ident); return [asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.project_id==ident).order_by(TaskRun.created_at.desc()).limit(min(limit,500)).offset(offset))]
@app.get('/api/runs/{ident}')
def run(ident:str):
    with Session() as s:
        r=asdict(get(s,TaskRun,ident)); r['config']={k:v for k,v in r['config'].items() if k!='provider_snapshot'}
        r['tools']=[asdict(t) for t in s.scalars(select(ToolExecution).where(ToolExecution.run_id==ident).order_by(ToolExecution.created_at))]; return r
@app.post('/api/runs/{ident}/{action}')
def run_action(ident:str,action:str,body:dict=Body(default={})):
    from runners.local import signal_group,stop_group
    with Session.begin() as s:
        if action in ('cancel','pause','resume') and s.bind.dialect.name=='sqlite': s.execute(sql_text('BEGIN IMMEDIATE'))
        r=get(s,TaskRun,ident)
        if action=='retry':
            from services.worker.scheduler import _lock_project
            _lock_project(s,r.project_id)
            r=s.scalar(select(TaskRun).where(TaskRun.id==ident).with_for_update().execution_options(populate_existing=True))
            if r.status not in TERMINAL: error('RUN_ACTIVE','Only stopped runs can be restarted',409)
            requested_timeout,_=requested_task_timeout(s,r)
            return asdict(enqueue(s,r.project_id,r.kind,r.config,body.get('request_id'),s.get(Node,r.node_id) if r.node_id else None,r.dependencies,
                                  requested_timeout_override=requested_timeout,
                                  node_revision=r.node_revision if r.node_id else None))
        if action in ('cancel','pause','resume'):
            # Validate control actions against locked current state before
            # signaling anything, using the request guard's project -> run order.
            project=s.scalar(select(Project).where(Project.id==r.project_id).with_for_update())
            if not project: error('NOT_FOUND','Project does not exist',404)
            r=s.scalar(select(TaskRun).where(TaskRun.id==ident).with_for_update().execution_options(populate_existing=True))
            if not r: error('NOT_FOUND','Run missing',404)
        if action=='cancel':
            if r.status in TERMINAL:
                if r.status in ('cancelled','interrupted','failed'): interrupt_run_reservations(r.id,session=s)
                return asdict(r)
            from research.execution.process_manager import process_manager
            process_manager(safe_path(project_dir(r.project_id),r.output_path+'/workspace'),r.config).cancel_all()
            if r.config.get('execution_backend')=='container':
                from runners.container import cancel_container
                cancel_container(r.config,safe_path(project_dir(r.project_id),r.output_path))
            if r.config.get('remote'):
                from runners.remote import cancel_remote
                cancel_remote(r.config,safe_path(project_dir(r.project_id),r.output_path))
            if r.pid: stop_group(r.pid,r.process_created)
            finalize_cancelled_run_elapsed(r)
            r.status='cancelled'; r.finished_at=now(); r.error='Stopped by owner'
            interrupt_run_reservations(r.id,session=s)
        elif action=='pause':
            if r.status not in ('queued','waiting','budget_exhausted','running'): error('INVALID_RUN_STATE','Run is not running or queued',409)
            container_state=None
            from research.execution.process_manager import process_manager
            manager=process_manager(safe_path(project_dir(r.project_id),r.output_path+'/workspace'),r.config)
            managed_before=manager.all()
            manager.signal_all(signal.SIGSTOP)
            if r.config.get('execution_backend')=='container':
                from runners.container import control_container
                container_state=control_container(r.config,safe_path(project_dir(r.project_id),r.output_path),'pause')
            if r.status in ('queued','waiting','budget_exhausted'):
                if r.status in ('waiting','budget_exhausted'):
                    resource={**(r.resource or {}),'elapsed_seconds':_elapsed_seconds_at(r)}
                    resource.pop('waiting_started_at',None); resource.pop('elapsed_before_wait',None)
                    r.resource=resource
                r.status='paused'
                live_managed=any(item['status'] not in ('completed','failed','cancelled','lost') for item in managed_before)
                live_container=container_state and container_state.get('status') in ('running','paused','starting')
                if live_managed or live_container:
                    r.resource={**(r.resource or {}),'paused_live_attempt':True}
            elif r.status=='running':
                r.status='pausing'
                if r.pid and signal_group(r.pid,r.process_created,signal.SIGSTOP): r.status='paused'
                elif container_state and container_state['status']=='paused': r.status='paused'; r.pid=None; r.process_created=None
                else: error('PROCESS_UNAVAILABLE','Cannot pause a process that is no longer running',409)
                r.resource={**(r.resource or {}),'paused_live_attempt':True}
        elif action=='resume':
            legacy_context_failure = (r.kind=='agent' and r.status=='failed' and
                str(r.error or '').startswith(('Task controls exceed the configured context_char_budget;',
                    'The latest complete native tool exchange exceeds context_char_budget;')))
            if r.status not in ('paused','waiting_input','waiting','budget_exhausted') and not legacy_context_failure: error('INVALID_RUN_STATE','Run is not paused or waiting',409)
            requested_timeout,timeout_source=requested_task_timeout(s,r)
            project_budget=project.budget or {}
            other_runs=s.scalars(select(TaskRun).where(TaskRun.project_id==r.project_id,TaskRun.id!=r.id))
            other_elapsed=sum(_elapsed_seconds_at(item) for item in other_runs)
            elapsed=_elapsed_seconds_at(r)
            time_budget=dict((r.resource or {}).get('time_budget') or {})
            time_budget['requested_task_timeout_seconds']=requested_timeout
            r.resource={**(r.resource or {}),'elapsed_seconds':elapsed,'time_budget':time_budget}
            reserved,held_by_other=reserve_project_time(s,project,r)
            if not reserved: error('TIME_BUDGET_EXHAUSTED','No project or task time budget remains for this run',409)
            effective_timeout=(r.resource.get('time_budget') or {}).get('effective_total_timeout_seconds')
            resource=dict(r.resource or {})
            resource.pop('paused_live_attempt',None)
            resource.pop('live_process_pending_dispatch',None)
            if r.status in ('waiting','budget_exhausted'):
                resource.pop('waiting_started_at',None); resource.pop('elapsed_before_wait',None)
            r.resource=resource
            time_budget=dict((r.resource or {}).get('time_budget') or {})
            resume_record={'resumed_at':now(),'requested_task_timeout_seconds':requested_timeout,
                           'requested_timeout_source':timeout_source,'effective_total_timeout_seconds':effective_timeout,
                           'project_budget_seconds':project_budget.get('seconds'),
                           'project_revision':project.revision,'other_run_elapsed_seconds':other_elapsed,
                           'other_run_reserved_seconds':held_by_other,'run_elapsed_seconds':elapsed}
            time_budget.update(project_budget_seconds_at_resume=project_budget.get('seconds'),
                               project_revision_at_resume=project.revision,
                               resume_history=[*time_budget.get('resume_history',[]),resume_record])
            r.resource={**r.resource,'time_budget':time_budget}
            emit(s,r.project_id,'run_time_budget_recalculated',{'run_id':r.id,**resume_record})
            if r.kind=='agent' and (legacy_context_failure or r.config.get('context_policy')!='automatic'):
                change={'changed_at':now(),'previous':r.config.get('context_policy','legacy'),
                        'updated':'automatic','previous_status':r.status,'previous_error':r.error}
                r.config={**r.config,'context_policy':'automatic',
                          'context_policy_changes':[*r.config.get('context_policy_changes',[]),change]}
                emit(s,r.project_id,'agent_context_policy_changed',{'run_id':r.id,**change})
            if 'context_char_budget' in body:
                if r.kind!='agent': error('INVALID_RUN_KIND','Context edits apply only to Agent runs',422)
                value=body['context_char_budget']
                if isinstance(value,bool) or not isinstance(value,int) or value<1000:
                    error('INVALID_CONTEXT_BUDGET','context_char_budget must be an integer of at least1000 characters',422)
                previous=r.config.get('context_char_budget')
                if value!=previous or legacy_context_failure:
                    change={'changed_at':now(),'previous':previous,'updated':value,'action':'resume',
                            'previous_status':r.status,'previous_error':r.error}
                    r.config={**r.config,'context_char_budget':value,'context_budget_changes':[*r.config.get('context_budget_changes',[]),change]}
                    emit(s,r.project_id,'agent_context_budget_changed',{'run_id':r.id,**change})
            if 'agent_budget' in body:
                if r.kind!='agent': error('INVALID_RUN_KIND','agent_budget updates apply only to Agent runs',422)
                from research.agents.budget import agent_budget_update
                previous=dict(r.config.get('agent_budget') or {})
                try: updated=agent_budget_update(previous,body['agent_budget'])
                except ValueError as exc: error('INVALID_AGENT_BUDGET',str(exc),422)
                if updated!=previous:
                    change={'changed_at':now(),'previous':previous,'updated':updated,'action':'resume'}
                    r.config={**r.config,'agent_budget':updated,'agent_budget_changes':[*r.config.get('agent_budget_changes',[]),change]}
                    emit(s,r.project_id,'agent_budget_changed',{'run_id':r.id,**change})
            from research.execution.process_manager import process_manager
            manager=process_manager(safe_path(project_dir(r.project_id),r.output_path+'/workspace'),r.config)
            managed_before=manager.all()
            if r.pid:
                manager.signal_all(signal.SIGCONT)
                if signal_group(r.pid,r.process_created,signal.SIGCONT):
                    if r.config.get('execution_backend')=='container':
                        from runners.container import control_container
                        control_container(r.config,safe_path(project_dir(r.project_id),r.output_path),'resume')
                    r.status='running'
                elif legacy_context_failure:
                    r.pid=None; r.process_created=None; r.status='queued'; r.config={**r.config,'_next_attempt':{'mode':'continue'}}
                elif r.config.get('execution_backend')=='container':
                    r.pid=None; r.process_created=None; r.status='queued'; r.config={**r.config,'_next_attempt':{'mode':'container_reconnect'}}
                    r.resource={**r.resource,'container_reconnect_pending_dispatch':True}
                else: error('PROCESS_UNAVAILABLE','Paused process was lost; restart the run',409)
            else:
                next_mode='container_reconnect' if r.config.get('execution_backend')=='container' else 'continue'
                r.status='queued'; r.config={**r.config,'_next_attempt':{'mode':next_mode}}
                if r.config.get('execution_backend')=='container':
                    r.resource={**r.resource,'container_reconnect_pending_dispatch':True}
                elif any(item['status'] not in ('completed','failed','cancelled','lost') for item in managed_before):
                    # Keep a paused managed child stopped until the worker has
                    # reacquired project budget and records the queue interval.
                    r.resource={**r.resource,'live_process_pending_dispatch':True}
        elif action=='skip':
            if r.status!='queued': error('INVALID_RUN_STATE','Only queued steps may be skipped',409)
            r.status='skipped'; r.finished_at=now()
        elif action=='priority': r.priority=int(body.get('priority',0))
        else: error('UNKNOWN_ACTION','Unknown run action',404)
        n=s.get(Node,r.node_id) if r.node_id else None
        if n and n.extra.get('latest_run_id')==r.id: n.execution_status=r.status
        emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); return asdict(r)
@app.get('/api/runs/{ident}/output')
def output(ident:str,offset:int=0,limit:int=100000,search:str=''):
    with Session() as s: r=get(s,TaskRun,ident); p=safe_path(project_dir(r.project_id),r.output_path+'/stdout.txt'); status=r.status
    text=''; end=max(offset,0)
    if p.exists():
        with p.open('rb') as f: f.seek(max(offset,0)); text=f.read(min(limit,1000000)).decode(errors='replace'); end=f.tell()
    if search: text='\n'.join(l for l in text.splitlines() if search.lower() in l.lower())
    return {'text':text,'offset':end,'status':status}
@app.get('/api/projects/{ident}/events')
async def events(ident:str,request:Request):
    def check_project():
        with Session() as s: get(s,Project,ident)
    await asyncio.to_thread(check_project)
    def read_batch(last,first_poll):
        with Session() as s:
            if first_poll:
                oldest,latest=s.execute(select(func.min(Event.sequence),func.max(Event.sequence)).where(Event.project_id==ident)).one()
                latest=latest or 0
                if last>latest:
                    return [],{'requested_after_sequence':last,'oldest_available_sequence':oldest or 0,
                               'latest_available_sequence':latest,'resume_after_sequence':latest}
            rows=list(s.scalars(select(Event).where(Event.project_id==ident,Event.sequence>last).order_by(Event.sequence).limit(100)))
            gap=None
            if rows and rows[0].sequence>last+1:
                first=rows[0].sequence
                latest=s.scalar(select(func.max(Event.sequence)).where(Event.project_id==ident)) or rows[-1].sequence
                latest=max(latest,rows[-1].sequence)
                gap={'requested_after_sequence':last,'oldest_available_sequence':first,
                     'latest_available_sequence':latest,'resume_after_sequence':latest}
            return [(e.sequence,e.type,e.data) for e in rows],gap
    async def stream():
        cursor=request.headers.get('last-event-id',''); last=0
        if cursor:
            try: last=max(int(cursor),0)
            except ValueError: pass
        yield 'event: connected\ndata: {}\n\n'
        first_poll=True
        while not await request.is_disconnected():
            # Pool waits and database I/O must never block the ASGI event loop.
            rows,gap=await asyncio.to_thread(read_batch,last,first_poll)
            first_poll=False
            if gap:
                last=gap['resume_after_sequence']
                yield f'id: {last}\nevent: cursor_reset\ndata: {json.dumps(gap)}\n\n'
                continue
            for sequence,event_type,data in rows:
                last=sequence; yield f'id: {last}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n'
            yield ': heartbeat\n\n'; await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
_provider_health_cache={}
def provider_health(provider):
    """Short-lived transport/model availability, distinct from a prior chat test."""
    key=(provider.id,provider.updated_at)
    cached=_provider_health_cache.get(key)
    if cached and time.monotonic()-cached[0]<15: return cached[1]
    result={'id':provider.id,'available':False,'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'last_chat_test':provider.status}
    local=urlparse(provider.base_url).hostname in TRUSTED_HOSTS
    if provider.kind=='codex_cli':
        executable=shutil.which(os.environ.get('FOREST_CODEX_EXECUTABLE','codex'))
        try:
            result['available']=bool(executable and subprocess.run([executable,'login','status'],capture_output=True,timeout=3).returncode==0)
            result['reason']='Local CLI login check only; model inference is not probed' if result['available'] else 'Install and sign in to Codex CLI'
        except (OSError,subprocess.TimeoutExpired): result['reason']='Local CLI login check unavailable'
    elif local or provider.allow_paid:
        try:
            endpoint=provider.base_url.rstrip('/')+('/api/tags' if provider.kind=='ollama' else '/models')
            credential=read_secret(provider.credential_ref)
            response=httpx.get(endpoint,headers={'Authorization':'Bearer '+credential} if credential else {},timeout=2)
            response.raise_for_status(); data=response.json()
            names=[m.get('name','') for m in data.get('models',[])] if provider.kind=='ollama' else [m.get('id','') for m in data.get('data',[])]
            result['available']=provider.model in names or (provider.kind=='ollama' and provider.model+':latest' in names)
            if not result['available']: result['reason']='Configured model is absent from the current model list'
        except Exception as exc: result['reason']=type(exc).__name__
    else: result['reason']='External provider is disabled'
    if len(_provider_health_cache)>100: _provider_health_cache.clear()
    _provider_health_cache[key]=(time.monotonic(),result)
    return result
@app.get('/api/system')
def system():
    vm=psutil.virtual_memory(); disk=psutil.disk_usage(str(settings.data_dir)); gpu=[]
    if shutil.which('nvidia-smi'):
        try:
            r=subprocess.run(['nvidia-smi','--query-gpu=name,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
            if r.returncode==0: gpu=[dict(zip(['name','memory_used_mb','memory_total_mb','utilization_percent'],[v.strip() for v in line.split(',')])) for line in r.stdout.splitlines()]
        except (OSError,subprocess.TimeoutExpired): pass
    with Session() as s:
        workers=[asdict(w) for w in s.scalars(select(Worker))]; providers=list(s.scalars(select(Provider)))
    for w in workers: w['online']=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(w['heartbeat'])).total_seconds()<10
    provider_status=[provider_health(p) for p in providers]
    return {'cpu_percent':psutil.cpu_percent(),'memory':{'total':vm.total,'used':vm.used,'percent':vm.percent},'disk':{'total':disk.total,'used':disk.used,'free':disk.free},'gpu':gpu,'workers':workers,'latex':shutil.which('tectonic') or shutil.which('pdflatex'),'model_connected':any(p['available'] for p in provider_status),'provider_status':provider_status,'platform':os.uname().sysname,'isolation':'trusted_local_process'}
@app.get('/api/settings')
def read_settings():
    with Session() as s: p=s.get(Preference,'settings'); return p.value if p else {'language':'en','theme':'light','retention_days':30,'default_mode':'assisted'}
@app.patch('/api/settings')
def update_settings(body:dict=Body(...)):
    with Session.begin() as s:
        p=s.get(Preference,'settings')
        if not p: p=Preference(key='settings',value={}); s.add(p)
        p.value={**p.value,**body}; return p.value
@app.post('/api/settings/cleanup')
def cleanup(body:dict=Body(...)):
    cutoff=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=max(0,int(body.get('days',30))))).isoformat(); removed=0
    with Session.begin() as s:
        for r in s.scalars(select(TaskRun).where(TaskRun.finished_at<cutoff,TaskRun.status.in_(TERMINAL))):
            if body.get('project_id') and r.project_id!=body['project_id']: continue
            shutil.rmtree(safe_path(project_dir(r.project_id),r.output_path),ignore_errors=True); touch_dependents(s,r.project_id,r.id); s.delete(r); removed+=1
        if body.get('clear_edit_history'):
            for p in s.scalars(select(Project)): p.graph_meta={k:v for k,v in p.graph_meta.items() if k!='_history'}
    return {'deleted_runs':removed}
@app.get('/api/providers')
def providers():
    with Session() as s: return [asdict(p) for p in s.scalars(select(Provider))]
@app.post('/api/providers')
def provider_create(body:dict=Body(...)):
    if body.get('kind','openai') not in ('ollama','openai','codex_cli'): error('INVALID_PROVIDER','Use ollama, openai-compatible or codex_cli')
    if urlparse(body.get('base_url','')).scheme not in ('http','https'): error('INVALID_URL','An HTTP(S) base URL is required')
    if body.get('kind')=='codex_cli' and urlparse(body.get('base_url','')).hostname not in TRUSTED_HOSTS:
        error('INVALID_URL','Codex CLI uses the local login; configure a localhost placeholder URL')
    with Session.begin() as s:
        key=body.pop('api_key',None); p=Provider(**{k:body[k] for k in ('name','kind','base_url','model','allow_paid','config') if k in body}); s.add(p)
        if key: p.credential_ref=put_secret(key)
        s.flush(); return asdict(p)
@app.patch('/api/providers/{ident}')
def provider_edit(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        p=get(s,Provider,ident)
        for k in ('name','kind','base_url','model','allow_paid','config'):
            if k in body: setattr(p,k,body[k])
        if body.get('api_key'): p.credential_ref=put_secret(body['api_key'],p.credential_ref)
        p.status='untested'; return asdict(p)
@app.post('/api/providers/{ident}/test')
def provider_test(ident:str):
    from research.agents.provider import ModelClient
    with Session() as s: p=get(s,Provider,ident); d=asdict(p,True)
    try:
        result=ModelClient(d,read_secret(d.get('credential_ref')),d.get('allow_paid',False)).complete([{'role':'user','content':'Return JSON {"status":"connected"}.'}]); status='connected'
    except Exception as exc: result={'error':str(exc)}; status='failed'
    with Session.begin() as s: get(s,Provider,ident).status=status
    if status=='failed': error('PROVIDER_CONNECTION_FAILED',result['error'],502,'Check endpoint, model identifier and credentials.',True)
    return {'status':status,**result}
@app.get('/api/providers/{ident}/usage')
def provider_usage(ident:str):
    from research.agents.budget import usage_summary
    with Session() as s: get(s,Provider,ident)
    return usage_summary(ident)
@app.get('/api/providers/{ident}/models')
def provider_models(ident:str):
    from research.agents.provider import ModelClient
    with Session() as s: p=asdict(get(s,Provider,ident),True)
    return ModelClient(p,read_secret(p.get('credential_ref')),p.get('allow_paid',False)).models()
@app.get('/api/hosts')
def hosts():
    with Session() as s: return [asdict(p) for p in s.scalars(select(Host))]
@app.post('/api/hosts')
def host_create(body:dict=Body(...)):
    with Session.begin() as s: h=Host(**{k:body[k] for k in ('name','kind','config') if k in body}); s.add(h); s.flush(); return asdict(h)
@app.patch('/api/hosts/{ident}')
def host_edit(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        h=get(s,Host,ident)
        for k in ('name','kind','config'):
            if k in body: setattr(h,k,body[k])
        h.status='untested'; return asdict(h)
@app.post('/api/hosts/{ident}/test')
def host_test(ident:str):
    with Session() as s: h=asdict(get(s,Host,ident))
    try:
        if h['kind']=='local': result={'connected':True,'platform':os.uname().sysname}
        else:
            from runners.remote import RemoteRunner
            result=RemoteRunner(h['config']).test()
        with Session.begin() as s: get(s,Host,ident).status='connected'
        return result
    except Exception as exc:
        with Session.begin() as s: get(s,Host,ident).status='failed'
        error('HOST_CONNECTION_FAILED',str(exc),502)
@app.get('/api/agents')
def agents():
    with Session() as s: return [asdict(a) for a in s.scalars(select(Agent))]
@app.post('/api/agents')
def agent_create(body:dict=Body(...)):
    values={k:body[k] for k in ('name','role','instructions','provider_id','tools','config','enabled') if k in body}
    values['config']=explicit_agent_config(values.get('config'))
    with Session.begin() as s:
        a=Agent(**values); s.add(a); s.flush(); return asdict(a)
@app.patch('/api/agents/{ident}')
def agent_edit(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        a=get(s,Agent,ident)
        customized=bool((a.config or {}).get('tools_customized')) or 'tools' in body
        for k in ('name','role','instructions','provider_id','tools','config','enabled'):
            if k in body: setattr(a,k,body[k])
        if customized: a.config=explicit_agent_config(a.config)
        return asdict(a)
@app.post('/api/projects/{ident}/share')
def share(ident:str,body:dict=Body(default={})):
    with Session.begin() as s:
        get(s,Project,ident); item=Share(project_id=ident,token=secrets.token_urlsafe(32),selection=body); s.add(item); s.flush(); return {'id':item.id,'url':'/share/'+item.token,'token':item.token}
@app.delete('/api/shares/{token}')
def revoke_share(token:str,request:Request):
    if not authorized(request): error('UNAUTHORIZED','Owner authentication required',401)
    with Session.begin() as s:
        item=s.scalar(select(Share).where(Share.token==token))
        if not item: error('NOT_FOUND','Share missing',404)
        item.enabled=False
    return {'revoked':True}
@app.get('/api/shares/{token}')
def shared(token:str):
    with Session() as s:
        item=s.scalar(select(Share).where(Share.token==token,Share.enabled==True))
        if not item: error('NOT_FOUND','Share unavailable',404)
        p=get(s,Project,item.project_id); g=graph_from_db(s,p)
        allowed=item.selection.get('node_ids',[])
        nodes=[{k:n[k] for k in ('id','title','type','execution_status','research_status','deliverable_status','position')} for n in g['nodes'] if n['id'] in allowed]
        return {'project':{'name':p.name,'description':p.description if item.selection.get('description') else ''},'nodes':nodes,'read_only':True}
@app.websocket('/ws/projects/{ident}/terminal')
async def terminal(ws:WebSocket,ident:str):
    origin=ws.headers.get('origin'); local=ws.client and ws.client.host in ('127.0.0.1','::1')
    if (origin and urlparse(origin).hostname not in TRUSTED_HOSTS and urlparse(origin).hostname!=ws.url.hostname) or (ws.cookies.get('forest_owner')!=owner_token() and not local): await ws.close(code=1008); return
    with Session() as s:
        p=s.get(Project,ident)
        if not p: await ws.close(code=1008); return
        branch=s.scalar(select(Branch).where(Branch.project_id==ident,Branch.is_main==True)); cwd=safe_path(project_dir(ident),branch.workspace if branch else '.')
    from .terminal import attach
    await attach(ws,ident+':'+str(branch.id if branch else 'main'),cwd)
from .progress import router as progress_router
app.include_router(progress_router)
app.include_router(files_router)
from .research_runtime import router as runtime_router
# Specific research endpoints must precede resources' /research/{action}.
app.include_router(runtime_router)
from .resources import router as research_router
app.include_router(research_router)
from .repositories import router as repositories_router
app.include_router(repositories_router)
from .run_diagnostics import router as diagnostics_router
app.include_router(diagnostics_router)
from .verification import router as verification_router
app.include_router(verification_router)

def api_openapi():
    """Describe the existing API without changing handler validation or output."""
    if app.openapi_schema is None:
        from fastapi.openapi.utils import get_openapi
        from .contracts import enhance_openapi
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        # FastAPI may retain included routers as nested route containers.
        # Generate with its native traversal before excluding the Web fallback.
        schema['paths'] = {path: value for path, value in schema['paths'].items() if path.startswith('/api/')}
        app.openapi_schema = enhance_openapi(schema)
    return app.openapi_schema

app.openapi = api_openapi
web_dist=Path(__file__).resolve().parents[2]/'apps/web/dist'
if web_dist.exists():
    app.mount('/assets',StaticFiles(directory=web_dist/'assets'),name='assets')
    @app.get('/{path:path}')
    def web(path:str):
        if path.startswith('api/') or path.startswith('ws/'): error('NOT_FOUND','API endpoint does not exist',404)
        requested=safe_path(web_dist,path)
        return FileResponse(requested if requested.is_file() else web_dist/'index.html')
