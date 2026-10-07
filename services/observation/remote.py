"""Saved remote observations from the owning execution adapter; no SSH on reads."""
from sqlalchemy import select,func
from services.api.db import Session, TaskRun, now
from .models import ObservationScope, ObservedFile
from .files import allowed, sync_scopes, PROJECT_CONTENT_LIMIT, PROJECT_FILE_LIMIT
from .segments import index
from .reporting import write_lock, ensure_state
from services.api.common import get
from services.api.db import Project

def capture(run_id, observation, attempt_id=None):
    if not run_id: return
    with Session.begin() as s:
        write_lock(s); run=s.get(TaskRun,run_id)
        if not run: return
        get(s,Project,run.project_id,for_update=True)
        s.refresh(run)
        if (run.config.get('execution_attempt') or {}).get('id')!=attempt_id: return
        ensure_state(s,run.project_id); sync_scopes(s,run.project_id,recent_only=True)
        scope=s.scalar(select(ObservationScope).where(ObservationScope.project_id==run.project_id,ObservationScope.kind=='remote_workspace',ObservationScope.object_id==run_id))
        if not scope: return
        if not observation:
            scope.coverage='unavailable'; scope.error='Remote host disconnected; last observations retained'; return
        if not observation.get('cursor_found',True):
            scope.coverage='partial'; scope.error='Remote inventory cursor disappeared; next pass restarts'; return
        scope.coverage='partial' if observation.get('has_more') else 'complete'; scope.error=None
        scope.observed_at=now()
        if not observation.get('cursor'): scope.scan_generation+=1
        total=s.scalar(select(func.coalesce(func.sum(func.length(ObservedFile.content)),0)).where(ObservedFile.project_id==run.project_id)) or 0
        count=s.scalar(select(func.count()).select_from(ObservedFile).where(ObservedFile.project_id==run.project_id)) or 0
        for item in observation.get('files',[])[:200]:
            relative=item.get('path','')
            if not allowed(relative): continue
            row=s.scalar(select(ObservedFile).where(ObservedFile.scope_id==scope.id,ObservedFile.path==relative))
            if row is None:
                if count>=PROJECT_FILE_LIMIT:
                    scope.coverage='partial'; scope.error='Project inventory capacity reached'; continue
                count+=1
                row=ObservedFile(project_id=run.project_id,scope_id=scope.id,path=relative,generation=0); s.add(row)
            content=item.get('content'); content=content.encode('utf-8') if isinstance(content,str) else None
            if content is not None and total-len(row.content or b'')+len(content)>PROJECT_CONTENT_LIMIT: content=None
            total=total-len(row.content or b'')+len(content or b'')
            metadata={'size':int(item.get('size',0)),'mtime_ns':int(item.get('mtime_ns',0))}
            if row.content!=content or row.stat!=metadata or row.state=='deleted':
                row.generation=(row.generation or 0)+1; row.content=content
                row.segments,row.parse_state=index(content,relative) if content is not None else ([], 'metadata_only')
            row.stat=metadata; row.size=metadata['size']; row.state='available' if content is not None else 'metadata_only'
            row.scan_generation=scope.scan_generation; row.attempt_id=scope.attempt_id
            row.attribution='run_process_scope'; row.observed_at=scope.observed_at
        if not observation.get('has_more') and not scope.error:
            for row in s.scalars(select(ObservedFile).where(ObservedFile.scope_id==scope.id,ObservedFile.scan_generation!=scope.scan_generation)):
                if row.state!='deleted': row.generation+=1
                row.state='deleted'; row.content=None; row.segments=[]; row.parse_state='unavailable'
