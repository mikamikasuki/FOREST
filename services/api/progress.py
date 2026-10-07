"""Authenticated owner observation routes; no implicit paid calls on reads."""
from fastapi import APIRouter, Query
from fastapi.responses import Response
import mimetypes
from typing import Literal
from sqlalchemy import select
from .db import Session, Project, uid
from .common import get, error
from services.observation.models import ObservationState, ObservationScope, ObservedFile, ReportJob
from services.observation.contracts import (ProjectProgressSnapshot, ScopePage, FilePage, SourceRef, SourceView,
    ReporterSettingsView, ReporterSettingsWrite, ReporterRefresh, ReporterJob, ReportPage)
from services.observation.files import coverage, file_view, resolve, reference, scope_authorized
from services.observation.projection import age
from services.observation.service import refresh_projection
from services.observation.reporting import configure, settings_view, enqueue, job_view
router=APIRouter()

@router.get('/api/projects/{ident}/progress',response_model=ProjectProgressSnapshot)
def progress(ident:str):
    with Session() as s:
        get(s,Project,ident); state=s.get(ObservationState,ident)
        empty=not state or not state.snapshot
    if empty: refresh_projection(ident,uid(),release=True)
    with Session() as s:
        state=s.get(ObservationState,ident)
        if not state or not state.snapshot: error('PROGRESS_REBUILDING','Progress is rebuilding; retry shortly',503,retryable=True)
        response=ProjectProgressSnapshot.model_validate(state.snapshot)
    seconds=age(response.observed_at); response.health.age_seconds=seconds
    if seconds is None or seconds>10: response.health.state='stale'
    return response

@router.get('/api/projects/{ident}/progress/scopes',response_model=ScopePage)
def scopes(ident:str,cursor:str='',limit:int=Query(50,ge=1,le=200),
           kind:Literal['project','branch_workspace','run_output','run_workspace','remote_workspace']|None=None,
           object_id:str|None=Query(None,max_length=64)):
    with Session() as s:
        get(s,Project,ident)
        query=select(ObservationScope).where(ObservationScope.project_id==ident,ObservationScope.id>cursor)
        if kind: query=query.where(ObservationScope.kind==kind)
        if object_id: query=query.where(ObservationScope.object_id==object_id)
        rows=list(s.scalars(query.order_by(ObservationScope.id).limit(limit+1)))
        return ScopePage(items=[coverage(s,v) for v in rows[:limit]],next_cursor=rows[limit-1].id if len(rows)>limit else None)

@router.get('/api/projects/{ident}/progress/sources',response_model=FilePage)
def sources(ident:str,scope_id:str,cursor:str='',limit:int=Query(50,ge=1,le=200)):
    with Session() as s:
        get(s,Project,ident); scope=get(s,ObservationScope,scope_id)
        if scope.project_id!=ident or not scope_authorized(s,scope): error('SOURCE_SCOPE','Scope is unavailable',404)
        rows=list(s.scalars(select(ObservedFile).where(ObservedFile.project_id==ident,ObservedFile.scope_id==scope_id,ObservedFile.path>cursor).order_by(ObservedFile.path).limit(limit+1)))
        state=s.get(ObservationState,ident)
        return FilePage(items=[file_view(v,scope,state.epoch if state else None) for v in rows[:limit]],next_cursor=rows[limit-1].path if len(rows)>limit else None,coverage=coverage(s,scope))

@router.post('/api/projects/{ident}/progress/source',response_model=SourceView)
def source(ident:str,body:SourceRef):
    if ident!=body.project_id: error('SOURCE_SCOPE','Choose a source reference in this project',422)
    return resolve(body)

@router.get('/api/projects/{ident}/progress/artifacts/{file_id}')
def artifact(ident:str,file_id:str,epoch:str,generation:int=Query(ge=1)):
    with Session() as s:
        get(s,Project,ident); row=get(s,ObservedFile,file_id)
        if row.project_id!=ident: error('SOURCE_SCOPE','Source is unavailable',404)
        scope=get(s,ObservationScope,row.scope_id)
        state=get(s,ObservationState,ident)
        if state.epoch!=epoch or row.generation!=generation or row.content is None:
            error('SOURCE_CHANGED','Observed artifact is changed or unavailable',409)
        ref=reference(row,scope,epoch)
        data=row.content; path=row.path
    view=resolve(ref)
    if view.availability!='available': error('SOURCE_CHANGED','Observed artifact is changed or unavailable',409)
    return Response(content=data,media_type=mimetypes.guess_type(path)[0] or 'application/octet-stream',
                    headers={'Cache-Control':'no-store','Content-Disposition':'inline' if path.lower().endswith(('.pdf','.png','.jpg','.jpeg','.gif','.webp')) else 'attachment',
                             'Content-Security-Policy':"sandbox; default-src 'none'"})

@router.get('/api/projects/{ident}/reporter-settings',response_model=ReporterSettingsView)
def reporter_settings(ident:str): return settings_view(ident)

@router.patch('/api/projects/{ident}/reporter-settings',response_model=ReporterSettingsView)
def update_reporter_settings(ident:str,body:ReporterSettingsWrite): return configure(ident,body)

@router.post('/api/projects/{ident}/reports/refresh',response_model=ReporterJob)
def refresh_report(ident:str,body:ReporterRefresh):
    progress(ident)
    job_id=enqueue(ident,body.request_id)
    with Session() as s: return job_view(s,get(s,ReportJob,job_id))

@router.get('/api/projects/{ident}/reports',response_model=ReportPage)
def reports(ident:str,cursor:str='',limit:int=Query(20,ge=1,le=50)):
    with Session() as s:
        get(s,Project,ident)
        rows=list(s.scalars(select(ReportJob).where(ReportJob.project_id==ident,ReportJob.id>cursor).order_by(ReportJob.id).limit(limit+1)))
        return ReportPage(items=[job_view(s,j) for j in rows[:limit]],next_cursor=rows[limit-1].id if len(rows)>limit else None)

@router.get('/api/projects/{ident}/reports/latest',response_model=ReporterJob|None)
def latest_report(ident:str):
    with Session() as s:
        get(s,Project,ident)
        job=s.scalar(select(ReportJob).where(ReportJob.project_id==ident).order_by(ReportJob.created_at.desc(),ReportJob.id.desc()).limit(1))
        return job_view(s,job) if job else None
