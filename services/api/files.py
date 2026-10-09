import io
import json
import os
import secrets
import threading
import time
import lzma
import shutil
import sqlite3
import zlib
from contextlib import contextmanager
from collections import OrderedDict
import zipfile
import math
import warnings
from datetime import date, datetime
from decimal import Decimal
from numbers import Integral, Real
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Body, Query, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from .common import *
from .schemas import FileWrite
from services.observation.files import managed_change, observation_write_lock
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by native Windows runtimes.
    fcntl=None
    import msvcrt
router=APIRouter()

# File pages keep a depth-first scandir iterator alive so later pages continue
# from the prior position instead of walking the project again. Cursors are
# short-lived and process-local; the API currently runs as one Uvicorn worker.
_FILE_CURSOR_TTL=600
_FILE_CURSOR_LIMIT=256
_FILE_CURSOR_LOCK=threading.RLock()
_FILE_CURSORS=OrderedDict()

class _FileCursorSession:
    def __init__(self,project_id,root):
        self.project_id=project_id
        self.root=root
        self.iterator=iter(_walk_workspace(root))
        self.pending=None

class _FileCursor:
    def __init__(self,session):
        self.session=session
        self.expires=time.monotonic()+_FILE_CURSOR_TTL
        self.response=None
        self.lock=threading.Lock()

def _walk_workspace(root):
    def visit(directory):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    relative=Path(entry.path).relative_to(root).as_posix()
                    if any(part.startswith('.') and part!='.forest-bases' for part in relative.split('/')):
                        continue
                    try:
                        if entry.is_symlink(): continue
                        is_dir=entry.is_dir(follow_symlinks=False)
                        stat=entry.stat(follow_symlinks=False)
                    except OSError:
                        # A worker may remove or replace an entry while the
                        # listing is in progress. Skip it instead of failing
                        # the entire page.
                        continue
                    yield {'path':relative,'size':stat.st_size,
                           'modified':stat.st_mtime,'is_dir':is_dir}
                    if is_dir:
                        yield from visit(entry.path)
        except OSError:
            return
    yield from visit(root)

def _prune_file_cursors(now=None):
    now=time.monotonic() if now is None else now
    with _FILE_CURSOR_LOCK:
        for token,entry in list(_FILE_CURSORS.items()):
            if entry.expires<=now: _FILE_CURSORS.pop(token,None)
        while len(_FILE_CURSORS)>_FILE_CURSOR_LIMIT:
            _FILE_CURSORS.popitem(last=False)

def _store_file_cursor(session):
    token=secrets.token_urlsafe(24)
    with _FILE_CURSOR_LOCK:
        _FILE_CURSORS[token]=_FileCursor(session)
        _prune_file_cursors()
    return token

def validate_project(ident):
    with Session() as s: get(s,Project,ident)
    return project_dir(ident)
@contextmanager
def file_publication_lock(project_id, relative):
    """Serialize owner file reads, publications, moves and deletes across processes.

    The guard covers the whole project so directory moves/deletes also wait for
    child-file compensation. Acquire it before database locks and hold it until
    a failed publication has restored the previous bytes.
    """
    lock_dir=settings.data_dir/'.forest-file-publication-locks'
    lock_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock_path=lock_dir/f'{project_id}.lock'
    with lock_path.open('a+b') as lock_file:
        if fcntl:
            fcntl.flock(lock_file.fileno(),fcntl.LOCK_EX)
        else:  # msvcrt locks a byte range, so keep one byte in the lock file.
            lock_file.seek(0,2)
            if lock_file.tell()==0:
                lock_file.write(b'\0'); lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(),msvcrt.LK_LOCK,1)
        try: yield
        finally:
            if fcntl: fcntl.flock(lock_file.fileno(),fcntl.LOCK_UN)
            else:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(),msvcrt.LK_UNLCK,1)

def _is_definite_commit_abort(exc):
    original=getattr(exc,'orig',exc)
    sqlstate=getattr(original,'sqlstate',None) or getattr(original,'pgcode',None)
    if sqlstate:
        return sqlstate[:2] in ('22','23') or sqlstate in ('40001','40002','40P01')
    return isinstance(original,sqlite3.IntegrityError)

def _restore_file_publication(path, backup, existed):
    if existed: backup.replace(path)
    else: path.unlink(missing_ok=True)

def _publish_file(ident,relative,path,temporary,backup,origin,expected_revision=None,content=None,overwrite=True,create_only=False):
    from services.worker.scheduler import _lock_project
    existed=False; published=False; flush_completed=False
    with file_publication_lock(ident,relative):
        if not overwrite and path.exists():
            error('FILE_EXISTS','Destination already exists',409,
                  'Review the destination and explicitly choose whether to replace it.')
        if create_only and path.exists():
            error('FILE_EXISTS','Destination already exists',409,
                  'Choose a new path or open the existing file to edit it.')
        existed=path.is_file()
        if existed: shutil.copy2(path,backup)
        if origin=='user_import' and existed:
            temporary.chmod(path.stat().st_mode & 0o7777)
        try:
            with Session() as s:
                with s.begin():
                    observation_write_lock(s,ident)
                    _lock_project(s,ident)
                    editable_workspace(s,ident,path)
                    revision=s.scalar(select(FileRevision).where(
                        FileRevision.project_id==ident,FileRevision.path==relative
                    ).with_for_update().execution_options(populate_existing=True))
                    actual=revision.revision if revision else 0
                    if expected_revision is not None and expected_revision!=actual:
                        error('REVISION_CONFLICT','File changed in another editor',409,'Reload and merge changes.')
                    if revision is None:
                        revision=FileRevision(project_id=ident,path=relative,revision=0)
                        s.add(revision)
                    path.parent.mkdir(parents=True,exist_ok=True)
                    temporary.replace(path); published=True
                    revision.revision=actual+1
                    revision.origin=origin
                    if origin=='user_edited':
                        from .paper_state import sync_working_file_edit
                        sync_working_file_edit(s,ident,relative,content)
                    touch_dependents(s,ident,relative)
                    managed_change(s,ident,relative,'owner_editor' if origin=='user_edited' else 'upload')
                    s.flush()
                    flush_completed=True
        except Exception as exc:
            if published and (not flush_completed or _is_definite_commit_abort(exc)):
                try: _restore_file_publication(path,backup,existed)
                except OSError as restore_error:
                    raise RuntimeError(
                        'File transaction aborted and file compensation failed'
                    ) from restore_error
            raise
        return {'path':relative,'revision':revision.revision,'origin':origin}
@router.get('/api/projects/{ident}/files')
def files(ident:str,limit:int=Query(default=500,ge=1,le=1000),cursor:str|None=None):
    root=validate_project(ident)
    entry=None
    if cursor is not None:
        if not isinstance(cursor,str) or len(cursor)<30:
            error('INVALID_FILE_CURSOR','Invalid file-list cursor',422)
        _prune_file_cursors()
        with _FILE_CURSOR_LOCK:
            entry=_FILE_CURSORS.get(cursor)
            if entry is None:
                error('FILE_CURSOR_EXPIRED','File-list cursor expired; reload the first page.',409,
                      'Restart pagination from the first page.')
            entry.expires=time.monotonic()+_FILE_CURSOR_TTL
            _FILE_CURSORS.move_to_end(cursor)
        if entry.session.project_id!=ident:
            error('INVALID_FILE_CURSOR','Cursor belongs to a different project.',422)
    else:
        entry=_FileCursor(_FileCursorSession(ident,root))
    session=entry.session
    with entry.lock:
        if entry.response is not None: return entry.response
        page=[]
        if session.pending is not None:
            page.append(session.pending)
            session.pending=None
        while len(page)<limit+1:
            try: page.append(next(session.iterator))
            except StopIteration: break
        has_more=len(page)>limit
        if has_more: session.pending=page.pop()
        items=page
        next_cursor=_store_file_cursor(session) if has_more else None
        response={'files':items,'has_more':has_more,'next_cursor':next_cursor}
        if cursor is not None:
            entry.expires=time.monotonic()+_FILE_CURSOR_TTL
            with _FILE_CURSOR_LOCK: _prune_file_cursors()
            entry.response=response
        return response

@router.post('/api/projects/{ident}/files/existing')
def existing_files(ident:str,body:dict=Body(...)):
    root=validate_project(ident)
    paths=body.get('paths')
    if not isinstance(paths,list) or len(paths)>1000 or any(
            not isinstance(path,str) or not path or len(path)>4096 for path in paths):
        error('INVALID_FILE_PATHS','Provide up to 1,000 nonempty relative file paths.',422)
    existing=[]; directories=[]
    for relative in dict.fromkeys(paths):
        path=safe_path(root,relative)
        if path.is_dir(): directories.append(relative)
        elif path.exists(): existing.append(relative)
    return {'existing':existing,'directories':directories}

@router.get('/api/projects/{ident}/file')
def read_file(ident:str,path:str):
    from services.worker.scheduler import _lock_project
    root=validate_project(ident); p=safe_path(root,path,True)
    relative=str(p.relative_to(root.resolve()))
    with file_publication_lock(ident,relative):
        with Session.begin() as s:
            _lock_project(s,ident)
            if not p.is_file(): error('NOT_FILE','Select a file')
            if p.stat().st_size>10_000_000: error('FILE_TOO_LARGE','Use download for files larger than 10 MB',413)
            try: content=p.read_text()
            except UnicodeDecodeError: error('BINARY_FILE','This is a binary file. Open its preview or download it.',415)
            rev=s.scalar(select(FileRevision).where(
                FileRevision.project_id==ident,FileRevision.path==relative
            ).with_for_update().execution_options(populate_existing=True))
            origin=effective_file_origin(
                rev.origin if rev else None, 'executor_or_import'
            )
            return {'path':relative,'content':content,'revision':rev.revision if rev else 0,
                    'origin':origin}

@router.get('/api/projects/{ident}/file/preview')
def preview_file(ident:str,path:str,offset:int=Query(default=0,ge=0,le=10_000_000),limit:int=Query(default=100,ge=1,le=500)):
    """Bounded table excerpts; totals describe the complete parsed table.

    CSV/TSV rows are counted in chunks. Parquet metadata supplies its total and
    only intersecting row groups are visited, with bounded Arrow batches passed
    through pandas. No uploaded expression or file content is executed.
    """
    import pandas as pd
    root=validate_project(ident); p=safe_path(root,path,True)
    if not p.is_file(): error('NOT_FILE','Select a table file',400)
    suffix=p.suffix.lower()
    if suffix not in ('.csv','.tsv','.parquet'):
        error('UNSUPPORTED_TABLE_FORMAT','Table preview supports CSV, TSV and Parquet files',415)
    before=p.stat()
    if before.st_size>128*1024*1024:
        error('TABLE_TOO_LARGE','Preview accepts files up to 128 MiB. Create a smaller table slice or download the file.',413)
    selected=[]; total=0; returned=0
    try:
        if suffix in ('.csv','.tsv'):
            separator='\t' if suffix=='.tsv' else ','
            options={'sep':separator,'encoding':'utf-8-sig','on_bad_lines':'error','index_col':False,'dtype':str}
            with warnings.catch_warnings():
                warnings.simplefilter('error',pd.errors.ParserWarning)
                columns=[str(c) for c in pd.read_csv(p,nrows=0,**options).columns]
                if len(columns)>1000 or any(len(name)>256 for name in columns):
                    error('TABLE_TOO_WIDE','Preview accepts up to 1,000 source columns and column names up to 256 characters.',413)
                with pd.read_csv(p,chunksize=2048,**options) as reader:
                    for chunk in reader:
                        if returned<limit and total+len(chunk)>offset:
                            chunk=infer_csv_column_types(chunk)
                            start=max(0,offset-total)
                            page=chunk.iloc[start:start+limit-returned,:100]
                            selected.append(page); returned+=len(page)
                        total+=len(chunk)
        else:
            import pyarrow.parquet as parquet
            with parquet.ParquetFile(p) as reader:
                columns=[str(c) for c in reader.schema_arrow.names]
                if len(columns)>1000 or any(len(name)>256 for name in columns):
                    error('TABLE_TOO_WIDE','Preview accepts up to 1,000 source columns and column names up to 256 characters.',413)
                total=reader.metadata.num_rows
                group_start=0
                for group_index in range(reader.metadata.num_row_groups):
                    group=reader.metadata.row_group(group_index)
                    if offset>=group_start+group.num_rows:
                        group_start+=group.num_rows; continue
                    if returned>=limit: break
                    if group.total_byte_size>128*1024*1024:
                        error('TABLE_ROW_GROUP_TOO_LARGE','This Parquet row group exceeds the 128 MiB decoded preview bound. Repartition it into smaller row groups.',413)
                    within_group=0
                    for batch in reader.iter_batches(batch_size=1024,row_groups=[group_index],columns=columns[:100]):
                        batch_start=group_start+within_group
                        if returned<limit and batch_start+batch.num_rows>offset:
                            start=max(0,offset-batch_start)
                            count=min(batch.num_rows-start,limit-returned)
                            selected.append(batch.slice(start,count).to_pandas()); returned+=count
                        within_group+=batch.num_rows
                        if returned>=limit: break
                    group_start+=group.num_rows
    except HTTPException:
        raise
    except ImportError:
        error('PARQUET_ENGINE_UNAVAILABLE','Parquet preview requires the pyarrow dependency on the API server.',503)
    except Exception as exc:
        error('TABLE_PARSE_FAILED',f'Cannot parse this {suffix[1:].upper()} table: {str(exc)[:500]}',422,
              'Check the file format, UTF-8 encoding, column delimiters, and balanced quotes.')
    after=p.stat()
    if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
        error('TABLE_CHANGED','The table changed while its preview was being read. Reload the preview.',409,retryable=True)
    frame=pd.concat(selected,ignore_index=True) if selected else pd.DataFrame(columns=columns[:100])
    frame.columns=[str(c) for c in frame.columns]
    clipped=0
    cell_chars=max(32,min(512,1_000_000//max(1,limit*len(frame.columns))))
    def json_value(value,depth=0):
        nonlocal clipped
        if value is None or value is pd.NA or value is pd.NaT: return None
        if isinstance(value,bool): return value
        if isinstance(value,Integral): return int(value)
        if isinstance(value,Real): return float(value) if math.isfinite(float(value)) else None
        if isinstance(value,Decimal): return str(value) if value.is_finite() else None
        if isinstance(value,(datetime,date)): return value.isoformat()
        if isinstance(value,bytes):
            clipped+=1; return f'<binary: {len(value)} bytes>'
        if isinstance(value,str):
            if len(value)>cell_chars: clipped+=1; return value[:cell_chars]+'…'
            return value
        if depth>=4:
            clipped+=1; return '<nested value>'
        if isinstance(value,dict):
            if len(value)>20: clipped+=1
            return {str(k)[:256]:json_value(v,depth+1) for k,v in list(value.items())[:20]}
        if isinstance(value,(list,tuple)) or hasattr(value,'tolist'):
            values=value.tolist() if hasattr(value,'tolist') else value
            if not isinstance(values,(list,tuple)): return json_value(values,depth+1)
            if len(values)>20: clipped+=1
            return [json_value(v,depth+1) for v in values[:20]]
        return json_value(str(value),depth+1)
    rows=[{column:json_value(value) for column,value in row.items()} for row in frame.to_dict(orient='records')]
    return {'path':str(p.relative_to(root.resolve())),'format':suffix[1:],'columns':list(frame.columns),'rows':rows,
            'total':int(total),'offset':offset,'limit':limit,'returned':len(rows),'has_more':offset+len(rows)<total,
            'truncated_columns':len(columns)>100,'column_count':len(columns),'truncated_cells':clipped}
def editable_workspace(session,project_id,path):
    # A copy can be physically present before its durable DB confirmation. Owner
    # edits at that point must wait rather than mutate an unactivated snapshot.
    for branch in session.scalars(select(Branch).where(Branch.project_id==project_id)):
        if not branch.extra.get('workspace_intervention'): continue
        workspace=safe_path(project_dir(project_id),branch.workspace)
        if path==workspace or path.is_relative_to(workspace) or workspace.is_relative_to(path):
            error('WORKSPACE_PENDING','Workspace materialization is awaiting confirmation',409)


@router.put('/api/projects/{ident}/file')
def write_file(ident:str,body:FileWrite):
    root=validate_project(ident); p=safe_path(root,body.path)
    relative=str(p.relative_to(root.resolve()))
    # Stage outside branch workspaces: rejected writes must not materialize a
    # pending copy's destination or alter the bytes its receipt will validate.
    temporary=root/('.forest-edit-'+uid()+'.tmp')
    backup=root/('.forest-edit-'+uid()+'.backup')
    try:
        temporary.write_text(body.content)
        return _publish_file(
            ident,relative,p,temporary,backup,'user_edited',
            expected_revision=body.expected_revision,content=body.content,create_only=body.create_only
        )
    finally:
        temporary.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
@router.delete('/api/projects/{ident}/file')
def delete_file(ident:str,path:str):
    root=validate_project(ident); p=safe_path(root,path,True)
    if p==root: error('INVALID_PATH','Cannot remove the project root with a file operation')
    with file_publication_lock(ident,path), Session.begin() as s:
        from services.worker.scheduler import _lock_project
        _lock_project(s,ident);editable_workspace(s,ident,p)
        # FileRevision paths use the same native relative spelling as read/write.
        # On POSIX a backslash is a valid filename character, so translating it
        # to '/' aliases a filename with a distinct nested path.
        relative=str(p.relative_to(root.resolve()))
        if p.is_dir():
            removed_paths={str(item.relative_to(root.resolve())) for item in p.rglob('*') if item.is_file()}
        else:
            removed_paths={relative}
        if p.is_dir(): shutil.rmtree(p)
        else: p.unlink()
        prefix=relative.rstrip(os.sep)+os.sep
        revisions={rev.path:rev for rev in s.scalars(
            select(FileRevision).where(FileRevision.project_id==ident).with_for_update()
        )}
        removed_paths.update(
            revision_path for revision_path in revisions
            if revision_path==relative or revision_path.startswith(prefix)
        )
        for removed_path in removed_paths:
            revision=revisions.get(removed_path)
            if revision is None:
                s.add(FileRevision(
                    project_id=ident,path=removed_path,revision=1,
                    origin=DELETED_FILE_REVISION_ORIGIN
                ))
            else:
                revision.revision+=1
                revision.origin=DELETED_FILE_REVISION_ORIGIN
        touch_dependents(s,ident,relative)
        managed_change(s,ident,relative,'owner_editor')
    return {'deleted':path}
@router.post('/api/projects/{ident}/file/rename')
def rename_file(ident:str,body:dict=Body(...)):
    root=validate_project(ident); src=safe_path(root,body['path'],True); dst=safe_path(root,body['new_path'])
    with file_publication_lock(ident,body["path"]), Session.begin() as s:
        from services.worker.scheduler import _lock_project
        _lock_project(s,ident);editable_workspace(s,ident,src);editable_workspace(s,ident,dst)
        if dst.exists(): error('FILE_EXISTS','Destination already exists',409)
        relative_native=str(src.relative_to(root.resolve()))
        new_relative_native=str(dst.relative_to(root.resolve()))
        relative=relative_native
        new_relative=new_relative_native
        prefix=relative.rstrip(os.sep)+os.sep
        moved_native_paths=(
            {str(item.relative_to(root.resolve()))
             for item in src.rglob('*') if item.is_file()}
            if src.is_dir() else {relative_native}
        )
        moved_paths=moved_native_paths
        revisions=list(s.scalars(
            select(FileRevision).where(FileRevision.project_id==ident).with_for_update()
        ))
        revisions_by_path={rev.path:rev for rev in revisions}
        moved_revision_paths={path for path in revisions_by_path
                              if path==relative or path.startswith(prefix)}
        for previous in moved_paths:
            suffix=previous[len(relative):].lstrip(os.sep)
            new_path=(str(Path(new_relative_native)/Path(suffix))
                      if suffix else new_relative_native)
            source_revision=revisions_by_path.get(previous)
            destination_revision=revisions_by_path.get(new_path)
            source_generation=source_revision.revision if source_revision else 0
            destination_generation=destination_revision.revision if destination_revision else 0
            if source_revision is not None or destination_revision is not None:
                live_origin=(source_revision.origin if source_revision else 'executor_or_import')
                if live_origin in (DELETED_FILE_REVISION_ORIGIN,RENAMED_FILE_REVISION_ORIGIN):
                    live_origin='executor_or_import'
                next_generation=max(source_generation,destination_generation)+1
                if destination_revision is None:
                    s.add(FileRevision(
                        project_id=ident,path=new_path,revision=next_generation,
                        origin=live_origin
                    ))
                else:
                    destination_revision.revision=next_generation
                    destination_revision.origin=live_origin
            if source_revision is None:
                s.add(FileRevision(
                    project_id=ident,path=previous,revision=1,
                    origin=RENAMED_FILE_REVISION_ORIGIN
                ))
            else:
                source_revision.revision=source_generation+1
                source_revision.origin=RENAMED_FILE_REVISION_ORIGIN
            touch_dependents(s,ident,previous)
        for previous in moved_revision_paths-moved_paths:
            revision=revisions_by_path[previous]
            revision.revision+=1
            revision.origin=RENAMED_FILE_REVISION_ORIGIN
            touch_dependents(s,ident,previous)
        # Keep the old path's generation after the move. Untracked executor/import
        # files have no FileRevision row yet, so they need a revision-1 tombstone
        # to invalidate an editor that opened them at revision 0.
        s.flush()
        destination_revision=s.scalar(select(FileRevision.revision).where(
            FileRevision.project_id==ident,FileRevision.path==new_relative
        )) or 0
        dst.parent.mkdir(parents=True,exist_ok=True);src.rename(dst)
        touch_dependents(s,ident,relative)
        managed_change(s,ident,relative,'owner_editor');managed_change(s,ident,new_relative,'owner_editor')
    return {'path':body['new_path'],'revision':destination_revision}
@router.get('/api/projects/{ident}/download')
def download_file(ident:str,path:str):
    p=safe_path(validate_project(ident),path,True)
    if not p.is_file(): error('NOT_FILE','Choose a file')
    return FileResponse(p,filename=p.name,content_disposition_type='inline')
@router.post('/api/projects/{ident}/upload')
async def upload(ident:str,file:UploadFile=File(...),directory:str='uploads',overwrite:bool=True):
    root=validate_project(ident); p=safe_path(root,directory+'/'+Path(file.filename or 'upload').name)
    relative=str(p.relative_to(root.resolve()))
    total=0; temporary=root/('.forest-upload-'+uid()+'.tmp')
    backup=root/('.forest-upload-'+uid()+'.backup')
    out=temporary.open('xb')
    try:
        with out:
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>settings.max_upload_mb*1024*1024: error('UPLOAD_TOO_LARGE','Upload exceeds configured limit',413)
                out.write(chunk)
        await run_in_threadpool(
            _publish_file,ident,relative,p,temporary,backup,'user_import',overwrite=overwrite
        )
    finally:
        temporary.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
    return {'path':relative,'size':total,'origin':'user_import'}

def project_export(s,p,selection=None):
    root=project_dir(p.id); out=io.BytesIO(); project=asdict(p); project.pop('graph_meta',None)
    project['config']={k:v for k,v in project.get('config',{}).items() if k!='host_id'}
    graph=graph_from_db(s,p); graph.pop('_history',None)
    resources={k:[asdict(r) for r in s.scalars(select(m).where(m.project_id==p.id))] for k,m in RESOURCE_MODELS.items()}
    papers=[asdict(r) for r in s.scalars(select(PaperDocument).where(PaperDocument.project_id==p.id))]
    runs=[]
    for r in s.scalars(select(TaskRun).where(TaskRun.project_id==p.id)):
        value=asdict(r); value['config']={k:v for k,v in value['config'].items() if k not in ('provider_snapshot','credential_ref')}; value['pid']=None; value['worker_id']=None; runs.append(value)
    from services.interventions.history import export_history
    payload={'format':'forest-project-v1','project':project,'graph':graph,'resources':resources,'papers':papers,'runs':runs,
             **export_history(s,p.id)}
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('forest-project.json',json.dumps(payload,ensure_ascii=False,indent=2))
        for f in root.rglob('*'):
            rel=str(f.relative_to(root))
            if f.is_file() and not f.is_symlink() and f.name not in ('config.json','secrets.json','.env','owner-token') and not any(part.startswith('.') for part in f.relative_to(root).parts):
                if selection and not any(rel.startswith(x) for x in selection): continue
                if f.stat().st_size<=100*1024*1024: z.write(f,'files/'+rel)
            elif f.is_file() and not f.is_symlink() and f.name=='config.json' and not any(part.startswith('.') for part in f.relative_to(root).parts):
                if selection and not any(rel.startswith(x) for x in selection): continue
                try:
                    value=json.loads(f.read_text()); value.pop('provider_snapshot',None); value.pop('credential_ref',None); z.writestr('files/'+rel,json.dumps(value))
                except (ValueError,UnicodeDecodeError): pass
    return out.getvalue()
@router.post('/api/projects/{ident}/export')
def export_project(ident:str,body:dict=Body(default={})):
    with Session() as s: p=get(s,Project,ident); data=project_export(s,p,body.get('paths'))
    return Response(data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="forest-project.zip"'})

def imported_run_timestamp(value, fallback=None):
    if value is None or value == '': return fallback
    if not isinstance(value,str) or len(value)>40:
        error('INVALID_ARCHIVE','Run timestamps must be ISO 8601 strings')
    try: datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: error('INVALID_ARCHIVE','Run timestamps must be ISO 8601 strings')
    return value

@contextmanager
def cleanup_project_import_on_failure():
    """Remove a new workspace when the import body fails before commit.

    This context must exit before the transaction manager attempts to commit:
    a commit exception can have an ambiguous outcome, so deleting the workspace
    then could leave a committed project row without its files.
    """
    root = None

    def remember_root(path):
        nonlocal root
        root = path

    try:
        yield remember_root
    except BaseException:
        if root is not None:
            try:
                shutil.rmtree(root)
            except FileNotFoundError:
                pass
        raise

@router.post('/api/projects/import')
async def import_project(file:UploadFile=File(...)):
    from research.kernel import validate_graph,ArtifactResolver
    raw=await file.read(settings.max_upload_mb*1024*1024+1)
    if len(raw)>settings.max_upload_mb*1024*1024: error('UPLOAD_TOO_LARGE','Archive exceeds upload limit',413)
    try: z=zipfile.ZipFile(io.BytesIO(raw)); manifest=json.loads(z.read('forest-project.json'))
    except Exception: error('INVALID_ARCHIVE','Expected a FOREST project ZIP')
    if manifest.get('format')!='forest-project-v1': error('INVALID_ARCHIVE','Unknown archive format')
    orig=manifest.get('project')
    if not isinstance(orig,dict): error('INVALID_ARCHIVE','Project metadata must be an object')
    mode=orig.get('mode','assisted')
    if mode not in ('auto','assisted','manual'):
        error('INVALID_ARCHIVE','Project mode must be auto, assisted, or manual')
    if sum(x.file_size for x in z.infolist())>500*1024*1024: error('ARCHIVE_TOO_LARGE','Uncompressed archive exceeds 500 MB',413)
    for info in z.infolist():
        if info.filename.startswith('/') or '..' in Path(info.filename).parts or (info.external_attr>>16)&0o170000==0o120000: error('UNSAFE_ARCHIVE','Archive contains unsafe paths or symbolic links')
    from services.interventions.history import validate_history
    try: validate_history(manifest)
    except ValueError as exc: error('INVALID_ARCHIVE',str(exc),422)
    validate_graph(manifest['graph'])
    run_timestamps={}
    for r in manifest.get('runs',[]):
        run_timestamps[r['id']]={
            'created_at':imported_run_timestamp(r.get('created_at'),fallback=now()),
            'started_at':imported_run_timestamp(r.get('started_at')),
            'finished_at':imported_run_timestamp(r.get('finished_at'))
        }
    with Session.begin() as s, cleanup_project_import_on_failure() as remember_import_root:
        orig=manifest['project']; config=dict(orig.get('config',{})); controller=config.get('controller')
        selected_provider=config.get('provider_id')
        if selected_provider and not s.get(Provider,selected_provider):
            config.pop('provider_id',None)
            config['provider_selection_required']=True
        if isinstance(controller,dict):
            # Runtime references belong to the source project; imported runs are
            # rekeyed and active ones interrupted, so do not carry them forward.
            controller={key:value for key,value in controller.items()
                        if key not in ('last_run','current_node','paused_run_ids','route_review')}
            if controller.get('status')=='running':
                # Import is not an instruction to resume a research controller.
                controller.update(status='paused',phase='PLAN')
            config['controller']=controller
        p=make_project(s,orig['name']+' · Imported',orig.get('goal',''),orig.get('description',''),mode=mode,budget=orig.get('budget',{}),config=config)
        root=project_dir(p.id); remember_import_root(root)
        graph=manifest['graph']; old_id=graph['project_id']; mapping={item['id']:uid() for key in ('nodes','edges','branches') for item in graph[key]}
        for rows in list(manifest.get('resources',{}).values())+[manifest.get('papers',[]),manifest.get('runs',[])]:
            for item in rows: mapping.setdefault(item['id'],uid())
        from services.interventions.history import history_ids,import_history,historical_resource
        for ident in history_ids(manifest):mapping.setdefault(ident,uid())
        def remap(obj):
            return remap_identifiers(obj,mapping,project_id=old_id,new_project_id=p.id)
        controller=p.config.get('controller')
        if isinstance(controller,dict) and controller.get('branch_id'):
            branch_id=controller['branch_id']; remapped_branch=mapping.get(branch_id)
            if remapped_branch!=branch_id:
                p.config={**p.config,'controller':{**controller,'branch_id':remapped_branch}}
        from services.interventions.history import historical_branches
        graph=historical_branches(remap(graph)); graph['project_id']=p.id; graph['revision']=0; graph.pop('_history',None)
        for node in graph['nodes']:
            if node.get('execution_status') in ('queued','running','pausing','paused','waiting_input'):
                node['imported_execution_status']=node['execution_status']; node['execution_status']='interrupted'
        validate_graph(graph)
        for branch in graph['branches']: ArtifactResolver(root,graph).branch_path(branch['id'])
        save_graph(s,p,graph)
        for info in z.infolist():
            if not info.filename.startswith('files/') or info.is_dir(): continue
            try:
                contents=z.read(info)
            except (zipfile.BadZipFile, EOFError, lzma.LZMAError, NotImplementedError, OSError,
                    RuntimeError, ValueError, zlib.error):
                error('INVALID_ARCHIVE','Archive contains a corrupt or unreadable file member')
            dest=safe_path(root,remap(info.filename[6:])); dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(contents)
        for key,rows in manifest.get('resources',{}).items():
            model=RESOURCE_MODELS.get(key)
            if model:
                for r in rows: s.add(model(id=mapping[r['id']],project_id=p.id,title=r['title'],data=remap(r['data']),status=r['status']))
        for r in manifest.get('papers',[]): s.add(PaperDocument(id=mapping[r['id']],project_id=p.id,title=r['title'],data=remap(r['data']),status=r['status']))
        for r in manifest.get('runs',[]):
            # Imported measurements retain their original run identifiers inside source data; execution is not replayed.
            ident=mapping[r['id']]
            timestamps=run_timestamps[r['id']]
            s.add(TaskRun(
                id=ident,project_id=p.id,node_id=remap(r.get('node_id')),
                branch_id=remap(r.get('branch_id')),request_id='import:'+ident,
                kind=r['kind'],
                status=r['status'] if r['status'] in ('completed','failed','cancelled') else 'interrupted',
                config={**remap(r.get('config',{})),'origin':'imported_run','original_run_id':r['id']},
                node_revision=r.get('node_revision',0),
                created_at=timestamps['created_at'],
                started_at=timestamps['started_at'],
                finished_at=timestamps['finished_at'],
                output_path=remap(r.get('output_path','')),
                dependencies=remap(r.get('dependencies',[])),metrics=remap(r.get('metrics',{})),
                resource=historical_resource(r.get('resource',{})),exit_code=r.get('exit_code')
            ))
        import_history(s,p.id,manifest,remap)
        emit(s,p.id,'project_imported',{}); s.flush(); return asdict(p)
