import io
import json
import shutil
import zipfile
import math
import warnings
from datetime import date, datetime
from decimal import Decimal
from numbers import Integral, Real
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Body, Query, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from .common import *
from .schemas import FileWrite
router=APIRouter()

def validate_project(ident):
    with Session() as s: get(s,Project,ident)
    return project_dir(ident)
@router.get('/api/projects/{ident}/files')
def files(ident:str):
    root=validate_project(ident); items=[]
    for p in root.rglob('*'):
        if p.is_symlink() or any(v.startswith('.') and v!='.forest-bases' for v in p.relative_to(root).parts): continue
        if len(items)>=10000: break
        st=p.stat(); items.append({'path':str(p.relative_to(root)),'size':st.st_size,'modified':st.st_mtime,'is_dir':p.is_dir()})
    return {'files':sorted(items,key=lambda x:(not x['is_dir'],x['path']))}
@router.get('/api/projects/{ident}/file')
def read_file(ident:str,path:str):
    root=validate_project(ident); p=safe_path(root,path,True)
    if not p.is_file(): error('NOT_FILE','Select a file')
    if p.stat().st_size>10_000_000: error('FILE_TOO_LARGE','Use download for files larger than 10 MB',413)
    try: content=p.read_text()
    except UnicodeDecodeError: error('BINARY_FILE','This is a binary file. Open its preview or download it.',415)
    with Session() as s: rev=s.scalar(select(FileRevision).where(FileRevision.project_id==ident,FileRevision.path==path))
    return {'path':path,'content':content,'revision':rev.revision if rev else 0,'origin':rev.origin if rev else 'executor_or_import'}

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
            options={'sep':separator,'encoding':'utf-8-sig','on_bad_lines':'error','index_col':False}
            with warnings.catch_warnings():
                warnings.simplefilter('error',pd.errors.ParserWarning)
                columns=[str(c) for c in pd.read_csv(p,nrows=0,**options).columns]
                if len(columns)>1000 or any(len(name)>256 for name in columns):
                    error('TABLE_TOO_WIDE','Preview accepts up to 1,000 source columns and column names up to 256 characters.',413)
                with pd.read_csv(p,chunksize=2048,**options) as reader:
                    for chunk in reader:
                        if returned<limit and total+len(chunk)>offset:
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
@router.put('/api/projects/{ident}/file')
def write_file(ident:str,body:FileWrite):
    from services.worker.scheduler import _lock_project
    from .paper_state import sync_working_file_edit
    root=validate_project(ident); p=safe_path(root,body.path)
    relative=str(p.relative_to(root.resolve()))
    with Session.begin() as s:
        _lock_project(s,ident)
        rev=s.scalar(select(FileRevision).where(FileRevision.project_id==ident,FileRevision.path==relative).with_for_update())
        actual=rev.revision if rev else 0
        if body.expected_revision is not None and body.expected_revision!=actual: error('REVISION_CONFLICT','File changed in another editor',409,'Reload and merge changes.')
        if not rev: rev=FileRevision(project_id=ident,path=relative,revision=0); s.add(rev)
        p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_name(p.name+'.forest-tmp'); tmp.write_text(body.content); tmp.replace(p)
        rev.revision=actual+1; rev.origin='user_edited'
        sync_working_file_edit(s,ident,relative,body.content)
        touch_dependents(s,ident,relative)
        return {'path':relative,'revision':rev.revision,'origin':'user_edited'}
@router.delete('/api/projects/{ident}/file')
def delete_file(ident:str,path:str):
    root=validate_project(ident); p=safe_path(root,path,True)
    if p==root: error('INVALID_PATH','Cannot remove the project root with a file operation')
    if p.is_dir(): shutil.rmtree(p)
    else: p.unlink()
    with Session.begin() as s:
        s.execute(delete(FileRevision).where(FileRevision.project_id==ident,FileRevision.path==path)); touch_dependents(s,ident,path)
    return {'deleted':path}
@router.post('/api/projects/{ident}/file/rename')
def rename_file(ident:str,body:dict=Body(...)):
    root=validate_project(ident); src=safe_path(root,body['path'],True); dst=safe_path(root,body['new_path'])
    if dst.exists(): error('FILE_EXISTS','Destination already exists',409)
    dst.parent.mkdir(parents=True,exist_ok=True); src.rename(dst)
    with Session.begin() as s: touch_dependents(s,ident,body['path'])
    return {'path':body['new_path']}
@router.get('/api/projects/{ident}/download')
def download_file(ident:str,path:str):
    p=safe_path(validate_project(ident),path,True)
    if not p.is_file(): error('NOT_FILE','Choose a file')
    return FileResponse(p,filename=p.name,content_disposition_type='inline')
@router.post('/api/projects/{ident}/upload')
async def upload(ident:str,file:UploadFile=File(...),directory:str='uploads'):
    root=validate_project(ident); p=safe_path(root,directory+'/'+Path(file.filename or 'upload').name); p.parent.mkdir(parents=True,exist_ok=True)
    total=0; temporary=p.with_name('.forest-upload-'+uid()+'.tmp')
    out=temporary.open('xb')
    try:
        with out:
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>settings.max_upload_mb*1024*1024: error('UPLOAD_TOO_LARGE','Upload exceeds configured limit',413)
                out.write(chunk)
        # Publish only complete uploads, keeping existing file permissions.
        if p.is_file(): temporary.chmod(p.stat().st_mode & 0o7777)
        temporary.replace(p)
    finally:
        temporary.unlink(missing_ok=True)
    with Session.begin() as s: touch_dependents(s,ident,str(p.relative_to(root)))
    return {'path':str(p.relative_to(root)),'size':total,'origin':'user_import'}

def project_export(s,p,selection=None):
    root=project_dir(p.id); out=io.BytesIO(); project=asdict(p); project.pop('graph_meta',None)
    project['config']={k:v for k,v in project.get('config',{}).items() if k not in ('provider_id','host_id')}
    graph=graph_from_db(s,p); graph.pop('_history',None)
    resources={k:[asdict(r) for r in s.scalars(select(m).where(m.project_id==p.id))] for k,m in RESOURCE_MODELS.items()}
    papers=[asdict(r) for r in s.scalars(select(PaperDocument).where(PaperDocument.project_id==p.id))]
    runs=[]
    for r in s.scalars(select(TaskRun).where(TaskRun.project_id==p.id)):
        value=asdict(r); value['config']={k:v for k,v in value['config'].items() if k not in ('provider_snapshot','credential_ref')}; value['pid']=None; value['worker_id']=None; runs.append(value)
    payload={'format':'forest-project-v1','project':project,'graph':graph,'resources':resources,'papers':papers,'runs':runs}
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
    validate_graph(manifest['graph'])
    run_timestamps={}
    for r in manifest.get('runs',[]):
        run_timestamps[r['id']]={
            'created_at':imported_run_timestamp(r.get('created_at'),fallback=now()),
            'started_at':imported_run_timestamp(r.get('started_at')),
            'finished_at':imported_run_timestamp(r.get('finished_at'))
        }
    with Session.begin() as s:
        orig=manifest['project']; config=dict(orig.get('config',{})); controller=config.get('controller')
        if isinstance(controller,dict):
            # Runtime references belong to the source project; imported runs are
            # rekeyed and active ones interrupted, so do not carry them forward.
            controller={key:value for key,value in controller.items()
                        if key not in ('last_run','current_node','paused_run_ids','route_review')}
            if controller.get('status')=='running':
                # Import is not an instruction to resume a research controller.
                controller.update(status='paused',phase='PLAN')
            config['controller']=controller
        p=make_project(s,orig['name']+' · Imported',orig.get('goal',''),orig.get('description',''),mode=mode,budget=orig.get('budget',{}),config=config); root=project_dir(p.id)
        graph=manifest['graph']; old_id=graph['project_id']; mapping={item['id']:uid() for key in ('nodes','edges','branches') for item in graph[key]}
        for rows in list(manifest.get('resources',{}).values())+[manifest.get('papers',[]),manifest.get('runs',[])]:
            for item in rows: mapping.setdefault(item['id'],uid())
        def remap(obj):
            if isinstance(obj,dict): return {k:remap(v) for k,v in obj.items()}
            if isinstance(obj,list): return [remap(v) for v in obj]
            if isinstance(obj,str):
                if obj==old_id: return p.id
                if obj in mapping: return mapping[obj]
                for old,new in mapping.items(): obj=obj.replace(old,new)
                return obj
            return obj
        controller=p.config.get('controller')
        if isinstance(controller,dict) and controller.get('branch_id'):
            branch_id=controller['branch_id']; remapped_branch=mapping.get(branch_id)
            if remapped_branch!=branch_id:
                p.config={**p.config,'controller':{**controller,'branch_id':remapped_branch}}
        graph=remap(graph); graph['project_id']=p.id; graph['revision']=0; graph.pop('_history',None)
        for node in graph['nodes']:
            if node.get('execution_status') in ('queued','running','pausing','paused','waiting_input'):
                node['imported_execution_status']=node['execution_status']; node['execution_status']='interrupted'
        validate_graph(graph)
        for branch in graph['branches']: ArtifactResolver(root,graph).branch_path(branch['id'])
        save_graph(s,p,graph)
        for info in z.infolist():
            if not info.filename.startswith('files/') or info.is_dir(): continue
            dest=safe_path(root,remap(info.filename[6:])); dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(z.read(info))
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
                resource=r.get('resource',{}),exit_code=r.get('exit_code')
            ))
        emit(s,p.id,'project_imported',{}); s.flush(); return asdict(p)
