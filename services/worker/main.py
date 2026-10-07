"""Database-backed worker. Browser/API lifetimes do not own scientific processes."""
from __future__ import annotations
import datetime as dt
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
import psutil
from sqlalchemy import select, text
from services.api.db import *
from services.api.common import get,project_dir,safe_path,emit
from services.api.config import ROOT,settings
from runners.local import process_matches,stop_group,process_tree_resources
from services.worker.scheduler import ACTIVE

from research.execution.recovery import recovery_decision, resource_request, admission
from research.execution.process_manager import process_manager
from research.agents.budget import interrupt_run_reservations
from runners.container import cancel_container, control_container, ContainerRunner

PROCESS_STATES=('running','paused','pausing')

def capacity():
    return {'cpu': float(os.environ.get('FOREST_WORKER_CPU_SLOTS', os.cpu_count() or 1)),
            'memory_bytes': int(float(os.environ.get('FOREST_WORKER_MEMORY_GB', psutil.virtual_memory().total / 1024**3)) * 1024**3),
            'gpu': int(os.environ.get('FOREST_WORKER_GPU_SLOTS', 0))}


def task_request(config):
    request=resource_request(config)
    if config.get('execution_backend')=='container':
        import re
        options=config.get('container') or {}
        cpu=float(options.get('cpus',1))
        memory=str(options.get('memory','2g')).lower()
        match=re.fullmatch(r'([1-9][0-9]*)([bkmg]?)',memory)
        if not match: raise ValueError('Invalid container memory limit')
        memory_bytes=int(match[1])*{'':1,'b':1,'k':1024,'m':1024**2,'g':1024**3}[match[2]]
        gpu=options.get('gpus',0)
        gpu=int(os.environ.get('FOREST_WORKER_GPU_SLOTS',0)) or 1 if gpu=='all' else int(gpu or 0)
        maximum=int(options.get('max_processes',1))
        if not 1<=maximum<=32: raise ValueError('container.max_processes must be between 1 and 32')
        request={'cpu':max(request['cpu'],cpu*maximum),'memory_bytes':max(request['memory_bytes'],memory_bytes*maximum),'gpu':max(request['gpu'],gpu*maximum)}
    return request


def reservation_for(run):
    """Hold committed allocations until reconciliation, plus live detached work."""
    try: request=task_request(run.config)
    except (ValueError,TypeError): request=resource_request({})
    workspace=safe_path(project_dir(run.project_id),run.output_path+'/workspace')
    folder=workspace.parent/'.forest-container-processes'/workspace.name if run.config.get('execution_backend')=='container' else workspace/'.forest-processes'
    managed=[]
    if folder.is_dir():
        try: managed=[item for item in process_manager(workspace,run.config).all() if item['status'] not in ('completed','failed','cancelled','lost')]
        except (OSError,ValueError,KeyError,RuntimeError):
            if run.config.get('execution_backend')=='container': managed=[{'status':'unknown'}]
    executor_live=bool(run.pid and process_matches(run.pid,run.process_created))
    container_active=False
    handle=workspace.parent/'container_task.json'
    if run.config.get('execution_backend')=='container' and handle.exists():
        try:
            state=ContainerRunner(run.config.get('container')).status(json.loads(handle.read_text()))
            container_active=state['status'] in ('running','paused','starting')
        except (OSError,ValueError,RuntimeError): container_active=True  # Retain reservation during daemon uncertainty.
    # A different worker must not release this slot between executor exit and
    # the owner's committed terminal/recovery transition. Never-started paused
    # nodes have no allocation; their detached work, if any, is checked above.
    committed=run.status in ('running','pausing') or (run.status=='paused' and run.pid is not None)
    if not committed and not executor_live and not managed and not container_active: return None
    request['memory_bytes']=max(request['memory_bytes'],int(run.resource.get('rss_bytes') or 0),sum(int(item.get('rss_bytes') or 0) for item in managed))
    if run.config.get('execution_backend')!='container': request['cpu']=max(request['cpu'],len(managed))
    return request

class WorkerLoop:
    def __init__(self):
        self.id=uid(); self.stopping=False; self.processes={}; self.log_offsets={}; self.last_heartbeat=0
        migrate()
        with Session.begin() as s: s.add(Worker(id=self.id,name=socket.gethostname(),pid=os.getpid(),capabilities={'concurrency':settings.worker_concurrency,'resources':capacity(),'runner':'local','isolation':'trusted_local_process'}))
    def reconcile_completed(self):
        # Recover node presentation from an existing completed run and its actual dependency revisions.
        with Session.begin() as s:
            for n in s.scalars(select(Node).where(Node.execution_status=='completed')):
                r=s.get(TaskRun,n.extra.get('latest_run_id')) if n.extra.get('latest_run_id') else None
                if not r or r.status!='completed' or r.node_revision!=n.revision: continue
                dependencies=[s.get(TaskRun,ident) for ident in r.dependencies]
                if any(d is None or d.status!='completed' or (d.node_id and (s.get(Node,d.node_id) is None or s.get(Node,d.node_id).revision!=d.node_revision)) for d in dependencies): continue
                folder=safe_path(project_dir(r.project_id),r.output_path)
                if not (folder/'result.json').is_file(): continue
                if not n.extra.get('result_revision'):
                    n.extra={**n.extra,'results_current':True,'last_run_id':r.id,'result_revision':r.node_revision}
                    n.outputs=[{'kind':'run','id':r.id,'path':r.output_path+'/result.json','project_scope':True,'node_revision':r.node_revision}]
                    for filename in ('metrics.json','predictions.csv','sources.json','workspace/agent_result.json','workspace/theory_check.json'):
                        if (folder/filename).is_file(): n.outputs.append({'kind':'file','path':r.output_path+'/'+filename,'project_scope':True,'node_revision':r.node_revision})
    def shutdown(self,*args): self.stopping=True
    def tick(self):
        now_clock=time.time()
        if now_clock-self.last_heartbeat>=2:
            with Session.begin() as s: get(s,Worker,self.id).heartbeat=now()
            self.last_heartbeat=now_clock
            from services.worker.controller import advance_projects
            advance_projects()
        self.wake_waiting()
        with Session() as s: active=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(('running','paused','pausing'))))]
        for r in active:
            if r['worker_id']!=self.id:
                with Session() as s: other=s.get(Worker,r['worker_id']) if r['worker_id'] else None
                fresh=other and (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(other.heartbeat)).total_seconds()<12
                if fresh: continue
                with Session.begin() as s:
                    claim=s.scalar(select(TaskRun).where(TaskRun.id==r['id']).with_for_update())
                    if claim.worker_id!=r['worker_id']: continue
                    claim.worker_id=self.id
                r['worker_id']=self.id
            self.monitor(r)
        self.reap_cancelled()
        with Session() as s:
            active=[r for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(('running','paused','pausing')))) if r.pid or r.status!='paused']
            owned=[r for r in active if r.worker_id==self.id]
        if len(owned)>=settings.worker_concurrency: return
        with Session.begin() as s:
            if engine.dialect.name=='sqlite': s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            elif engine.dialect.name=='postgresql':
                if not s.scalar(text('SELECT pg_try_advisory_xact_lock(824713)')): return
            reservations=[(x.id,request) for x in s.scalars(select(TaskRun).where(TaskRun.status.in_((*PROCESS_STATES,'queued','waiting','waiting_input','budget_exhausted')))) if (request:=reservation_for(x)) is not None]
            candidates=list(s.scalars(select(TaskRun).where(TaskRun.status=='queued').order_by(TaskRun.priority.desc(),TaskRun.created_at).with_for_update(skip_locked=True)))
            age_seconds=max(1,float(os.environ.get('FOREST_PRIORITY_AGING_SECONDS',300)))
            now_epoch=time.time()
            candidates.sort(key=lambda item: -(item.priority+(now_epoch-dt.datetime.fromisoformat(item.created_at).timestamp())/age_seconds))
            for r in candidates:
                try: request=task_request(r.config)
                except (ValueError,TypeError) as exc:
                    r.status='waiting_input'; r.error='Invalid task resource request: '+str(exc)
                    emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status,'error':r.error}); continue
                allowed, dimension=admission(request,[reserved for ident,reserved in reservations if ident!=r.id],capacity())
                if allowed=='wait': continue
                if allowed=='impossible':
                    r.status='waiting_input'; r.error=f'Requested {dimension} exceeds worker capacity. Edit resources or configure an appropriate worker.'
                    emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); continue
                deps=[s.get(TaskRun,d) for d in r.dependencies]
                if any(d is None or d.status in ('failed','cancelled','interrupted','skipped') for d in deps):
                    r.status='waiting_input'; r.resource={**r.resource,'blocked_reason':'dependency_failed'}; r.error='An execution dependency has no successful output. Retry it or rebind the input.'; emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); continue
                if any(d.status!='completed' for d in deps): continue
                # Check the exact queued inputs after prerequisite runs finish.
                from services.api.common import graph_from_db
                from research.kernel import ArtifactResolver
                project=get(s,Project,r.project_id); resolver=ArtifactResolver(project_dir(r.project_id),graph_from_db(s,project)); bound=[]; missing=[]
                for reference in r.config.get('input_references',[]):
                    ref={'path':reference} if isinstance(reference,str) else dict(reference)
                    if not ref.get('path') and not ref.get('node_id'): continue
                    actual=resolver.resolve(ref,r.branch_id)
                    if not actual.get('available') and ref.get('path') and ref.get('node_id'):
                        producer=next((d for d in deps if d.node_id==ref['node_id']),None)
                        if producer:
                            candidate=safe_path(project_dir(r.project_id),producer.output_path+'/workspace/'+ref['path'])
                            if candidate.is_file(): actual={'available':True,'path':str(candidate),'relative_path':str(candidate.relative_to(project_dir(r.project_id)))}
                    if not actual.get('available'): missing.append(ref)
                    elif actual.get('path'): bound.append({'source_path':actual['relative_path'],
                        'destination':ref.get('destination') or Path(ref['path']).name,
                        'source_node_id':ref.get('node_id'), 'reference_path':ref.get('path')})
                if missing:
                    r.status='waiting_input'; r.resource={**r.resource,'blocked_reason':'input_missing'}; r.error='Required produced files are missing: '+json.dumps(missing,ensure_ascii=False); emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); continue
                r.config={**r.config,'resolved_inputs':bound}
                from services.api.verification import dispatch_verification
                gate=dispatch_verification(s,r,resolved_inputs=bound)
                if not gate['ready']:
                    r.status='waiting_input'; r.resource={**r.resource,'blocked_reason':gate['blocked_reason'],
                        'verification_gate':gate}; r.error=gate['message']
                    emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status,'error':r.error,
                        'blocked_reason':gate['blocked_reason']}); continue
                r.resource={**r.resource,'verification_gate':gate}
                b=s.get(Branch,r.branch_id) if r.branch_id else None
                if b and b.status in ('pruned','archived','disabled'): r.status='waiting_input'; r.error='Branch is paused/pruned/archived'; continue
                p=get(s,Project,r.project_id)
                if p.archived: r.status='waiting_input'; r.error='Project archived'; continue
                consumed=sum(float(x.resource.get('elapsed_seconds',0)) for x in s.scalars(select(TaskRun).where(TaskRun.project_id==p.id)))
                if p.budget.get('seconds') is not None and consumed>=float(p.budget['seconds']): r.status='waiting_input'; r.error='Project time budget exhausted'; continue
                if r.config.get('breakpoint') and not r.config.get('breakpoint_passed'):
                    r.status='waiting_input'; r.error='Paused at configured before-node breakpoint'; r.config={**r.config,'breakpoint_passed':True}; continue
                output=safe_path(project_dir(r.project_id),r.output_path); output.mkdir(parents=True,exist_ok=True)
                old_attempt=r.config.get('execution_attempt',{})
                attempt={'id':uid(),'number':int(old_attempt.get('number',0))+1,'mode':r.config.get('_next_attempt',{}).get('mode','initial' if not old_attempt else 'continue'), 'started_at':now()}
                attempt.update(r.config.get('_next_attempt',{}))
                if (output/'result.json').exists():
                    archive=output/'attempts'/str(old_attempt.get('number',0)); archive.mkdir(parents=True,exist_ok=True)
                    (output/'result.json').replace(archive/'result.json')
                r.config={**r.config,'execution_attempt':attempt}; r.config.pop('_next_attempt',None)
                r.resource={**r.resource,'elapsed_before_attempt':r.resource.get('elapsed_seconds',0)}
                log=(output/'stdout.txt').open('ab',buffering=0)
                env={**os.environ,'PYTHONUNBUFFERED':'1','PYTHONPATH':str(ROOT),'PATH':os.environ.get('PATH','')+':/opt/homebrew/bin','FOREST_RUN_ID':r.id,'FOREST_ATTEMPT_ID':attempt['id']}
                recovery=r.config.get('recovery') or {}
                if recovery.get('checkpoint'): env['FOREST_CHECKPOINT_PATH']=str(safe_path(output/'workspace',recovery['checkpoint']))
                if attempt.get('checkpoint_path'): env['FOREST_RESUME_PATH']=attempt['checkpoint_path']
                proc=subprocess.Popen([sys.executable,'-m','services.worker.execute','--run-id',r.id],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env); log.close()
                self.processes[r.id]=proc; r.pid=proc.pid; r.process_created=psutil.Process(proc.pid).create_time(); r.status='running'; r.worker_id=self.id; r.started_at=now(); r.error=None
                n=s.get(Node,r.node_id) if r.node_id else None
                if n and n.revision==r.node_revision and n.extra.get('latest_run_id')==r.id: n.execution_status='running'
                emit(s,r.project_id,'run_started',{'run_id':r.id,'node_id':r.node_id,'pid':proc.pid,'attempt':attempt}); break
    def wake_waiting(self):
        """Waiting yields release executor slots; real process completion wakes work."""
        with Session() as s:
            waiting=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(('waiting','budget_exhausted'))))]
        for value in waiting:
            folder=safe_path(project_dir(value['project_id']),value['output_path'])
            managed=process_manager(folder/'workspace',value['config']).all()
            child_active=any(item['status'] not in ('completed','failed','cancelled','lost') for item in managed)
            if value['status']=='budget_exhausted' and not child_active: continue
            waiting_seconds=max(0,time.time()-float(value['resource'].get('waiting_started_at') or time.time()))
            total=float(value['resource'].get('elapsed_before_wait',value['resource'].get('elapsed_seconds',0)))+waiting_seconds
            timeout=value['config'].get('timeout')
            with Session.begin() as s:
                live=s.get(TaskRun,value['id'])
                if live and live.status==value['status']: live.resource={**live.resource,'elapsed_seconds':total,'rss_bytes':sum(int(item.get('rss_bytes') or 0) for item in managed)}
            if timeout is not None and total>float(timeout):
                folder=safe_path(project_dir(value['project_id']),value['output_path'])
                process_manager(folder/'workspace',value['config']).cancel_all()
                if value['config'].get('execution_backend')=='container': cancel_container(value['config'],folder)
                if value['config'].get('remote'):
                    from runners.remote import cancel_remote
                    cancel_remote(value['config'],folder)
                with Session.begin() as s:
                    live=s.get(TaskRun,value['id'])
                    if live and live.status==value['status']:
                        live.status='budget_exhausted'; live.error='Configured task time budget exhausted while waiting for a process'
                        interrupt_run_reservations(live.id,session=s)
                        node=s.get(Node,live.node_id) if live.node_id else None
                        if node and node.extra.get('latest_run_id')==live.id: node.execution_status=live.status
                        emit(s,live.project_id,'run_changed',{'run_id':live.id,'status':live.status,'error':live.error})
                continue
            if value['status']=='budget_exhausted': continue
            state=value['resource'].get('wait_for') or {}
            due=time.time() >= float(value['resource'].get('resume_after') or 0)
            if state.get('process_id') and state.get('workspace'):
                try:
                    process=process_manager(Path(state['workspace']),value['config']).inspect(state['process_id'])
                    due=process.get('status') in ('completed','failed','cancelled','lost')
                except (OSError,ValueError,KeyError,RuntimeError) as exc:
                    with Session.begin() as s:
                        item=s.get(TaskRun,value['id'])
                        if item and item.status=='waiting':
                            item.status='waiting_input'; item.error='Cannot inspect awaited process: '+str(exc)
                            emit(s,item.project_id,'run_changed',{'run_id':item.id,'status':item.status,'error':item.error})
                    continue
            if not due: continue
            with Session.begin() as s:
                r=s.scalar(select(TaskRun).where(TaskRun.id==value['id']).with_for_update())
                if r and r.status=='waiting':
                    r.status='queued'; r.pid=None; r.process_created=None
                    r.config={**r.config,'_next_attempt':{'mode':'continue'}}
                    emit(s,r.project_id,'run_resumed',{'run_id':r.id,'reason':'Awaited work is ready'})

    def reap_cancelled(self):
        """Reap locally owned executor children after API-side cancellation.

        Cancellation persists a terminal run state in the API process, so the
        normal monitor query no longer selects that run. Keep polling the
        worker-owned Popen until the stopped child exits, then release both the
        process handle and its progress offset.
        """
        if not self.processes:
            return
        identifiers = list(self.processes)
        with Session() as s:
            cancelled = set(s.scalars(select(TaskRun.id).where(
                TaskRun.id.in_(identifiers), TaskRun.status == 'cancelled')))
        for ident in cancelled:
            proc = self.processes.get(ident)
            if proc is not None and proc.poll() is not None:
                self.processes.pop(ident, None)
                self.log_offsets.pop(ident, None)

    def monitor(self,value):
        if value['status']=='paused' and not value.get('pid'):
            return
        ident=value['id']; output=safe_path(project_dir(value['project_id']),value['output_path']); receipt=output/'result.json'; log=output/'stdout.txt'
        elapsed=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(value['started_at'])).total_seconds() if value.get('started_at') else 0
        before=float(value.get('resource',{}).get('elapsed_before_attempt',0))
        alive=bool(value['pid'] and process_matches(value['pid'],value['process_created']))
        proc=self.processes.get(ident)
        if proc: proc.poll()
        resource={'elapsed_seconds':before+elapsed,'cpu_percent':None,'rss_bytes':None,'cpu_measurement_scope':'host_process_tree'}
        if value['config'].get('execution_backend')=='container':
            resource.update(cpu_measurement_scope='host_executor_only',container_cpu_seconds=None)
        if alive:
            try:
                resource.update(process_tree_resources(value['pid'],value['process_created']))
            except (psutil.NoSuchProcess,psutil.AccessDenied): pass
        if value['status']=='paused' and alive: return
        timeout=value['config'].get('timeout')
        outcome=None
        if receipt.exists():
            try: candidate=json.loads(receipt.read_text())
            except ValueError: candidate=None
            expected=value['config'].get('execution_attempt',{}).get('id')
            if candidate and (not expected or candidate.get('attempt_id')==expected):
                outcome=candidate
            elif candidate:
                # Never accept a receipt from a superseded attempt.
                stale=output/'attempts'/'stale'; stale.mkdir(parents=True,exist_ok=True)
                receipt.replace(stale/(str(time.time_ns())+'.json'))
        if outcome is None and timeout is not None and before+elapsed>float(timeout):
            if value['config'].get('remote'):
                from runners.remote import cancel_remote
                cancel_remote(value['config'],output)
            process_manager(output/'workspace',value['config']).cancel_all()
            if value['config'].get('execution_backend')=='container': cancel_container(value['config'],output)
            if alive: stop_group(value['pid'],value['process_created'])
            outcome={'status':'failed','exit_code':124,'error':f'Task exceeded its {float(timeout):g}s budget','budget_exhausted':True}
        elif outcome is None and not alive:
            outcome={'status':'interrupted','exit_code':proc.returncode if proc else None,'error':'Task process ended without a current completion receipt.'}
        with Session.begin() as s:
            s.scalar(select(Project).where(Project.id==value['project_id']).with_for_update())
            r=s.get(TaskRun,ident)
            if not r or r.status in ('cancelled','completed','failed','interrupted','waiting','budget_exhausted'): return
            if r.worker_id!=self.id: return
            if r.config.get('execution_attempt',{}).get('id')!=value['config'].get('execution_attempt',{}).get('id'): return
            r.resource={**r.resource,**resource}
            if log.exists() and self.log_offsets.get(ident)!=log.stat().st_size:
                self.log_offsets[ident]=log.stat().st_size; emit(s,r.project_id,'run_progress',{'run_id':r.id,'output_offset':log.stat().st_size})
            if outcome:
                if outcome['status'] in ('failed','interrupted','cancelled','budget_exhausted'):
                    # Close the failed attempt's accounting before recovery can
                    # queue another executor for the same run ID.
                    interrupt_run_reservations(r.id,session=s)
                attempt={**r.config.get('execution_attempt',{}),'status':outcome['status'],'exit_code':outcome.get('exit_code'),'error':outcome.get('error'),'finished_at':now(),'elapsed_seconds':outcome.get('elapsed_seconds',elapsed)}
                attempts=[*r.resource.get('attempts',[]),attempt]
                r.resource={**r.resource,'attempts':attempts,'elapsed_seconds':before+float(outcome.get('elapsed_seconds',elapsed))}
                decision=None
                if outcome['status'] in ('interrupted','failed') and not outcome.get('budget_exhausted'):
                    decision=recovery_decision(r.config,output,kind=r.kind,failed=outcome['status']=='failed')
                    if r.config.get('execution_backend')=='container' and r.kind in ('command','experiment') and (not decision or decision.get('mode')!='checkpoint'):
                        decision=None  # Recreate work only from an explicitly configured, present task checkpoint.
                    if r.config.get('execution_backend')=='container' and outcome['status']=='interrupted' and (output/'container_task.json').exists():
                        job=json.loads((output/'container_task.json').read_text())
                        state=ContainerRunner(r.config.get('container')).status(job)
                        if state['status'] in ('running','paused','starting','completed') or (state['status']=='failed' and not decision):
                            decision={'mode':'container_reconnect'}
                if decision:
                    r.config={**r.config,'_next_attempt':decision,'_recovery_failures':int(r.config.get('_recovery_failures',0))+1}
                    r.status='paused' if value['status']=='paused' else 'queued'; r.pid=None; r.process_created=None; r.error=outcome.get('error')
                    emit(s,r.project_id,'run_recovery_queued',{'run_id':r.id,'mode':decision['mode'],'attempt':attempt})
                elif outcome['status'] in ('waiting','budget_exhausted'):
                    r.status=outcome['status']; r.pid=None; r.process_created=None; r.error=outcome.get('error')
                    r.resource={**r.resource,'wait_for':outcome.get('wait_for',{}),'resume_after':outcome.get('resume_after'),'waiting_started_at':time.time(),'elapsed_before_wait':r.resource['elapsed_seconds']}
                    emit(s,r.project_id,'run_waiting',{'run_id':r.id,'status':r.status,'wait_for':outcome.get('wait_for',{})})
                else:
                    r.status=outcome['status']; r.exit_code=outcome.get('exit_code'); r.error=outcome.get('error'); r.metrics=outcome.get('metrics',{}); r.finished_at=now()
                    from services.api.verification import verification_for_run
                    verification=verification_for_run(s,r)
                    r.resource={**r.resource,'verification_status':verification['verification_status'],
                        'verification':verification}
                    if r.kind=='verification':
                        producer_id=verification.get('producer_node_id')
                        producer=s.get(Node,producer_id) if producer_id else None
                        source=s.get(TaskRun,verification.get('producer_run_id')) if verification.get('producer_run_id') else None
                        if producer and source:
                            current=verification_for_run(s,source)
                            producer.extra={**producer.extra,'verification_status':current['verification_status'],
                                'latest_verification_run_id':r.id}
                    emit(s,r.project_id,'run_completed' if r.status=='completed' else 'run_failed',{'run_id':r.id,'status':r.status,'error':r.error})
                n=s.get(Node,r.node_id) if r.node_id else None
                if n and n.revision==r.node_revision and n.extra.get('latest_run_id')==r.id:
                    n.execution_status=r.status
                    if r.status in ('completed','failed','interrupted'):
                        n.deliverable_status='ready_for_review' if r.status=='completed' else 'draft'
                        current_result={**n.extra,'results_current':r.status=='completed','last_run_id':r.id,'result_revision':r.node_revision,
                            'verification_status':r.resource.get('verification_status','unverified')}
                        if r.status=='completed': current_result['needs_rerun']=False
                        n.extra=current_result
                        if r.kind=='experiment' and r.status=='completed':
                            recovery=r.metrics.get('mechanism_recovery',[])
                            n.research_status='supported' if recovery and all(x['supported_descriptively'] for x in recovery) else 'not_supported' if recovery else 'unevaluated'
                        n.outputs=[{'kind':'run','id':r.id,'path':r.output_path+'/result.json','project_scope':True,'node_revision':r.node_revision}]
                        for filename in ('metrics.json','predictions.csv','sources.json','verification.json','workspace/agent_result.json','workspace/theory_check.json'):
                            if (output/filename).is_file(): n.outputs.append({'kind':'file','path':r.output_path+'/'+filename,'project_scope':True,'node_revision':r.node_revision})
                self.processes.pop(ident,None)
    def run(self):
        self.reconcile_completed()
        signal.signal(signal.SIGTERM,self.shutdown); signal.signal(signal.SIGINT,self.shutdown)
        print(f'FOREST worker {self.id} ready (independent process; {settings.worker_concurrency} resource-aware slots)',flush=True)
        while not self.stopping:
            try: self.tick()
            except Exception:
                import traceback; traceback.print_exc()
            time.sleep(.4)
        with Session.begin() as s:
            worker=get(s,Worker,self.id); worker.heartbeat='2000-01-01T00:00:00+00:00'
        print('Worker stopped; running child jobs remain recoverable by the next worker.',flush=True)
if __name__=='__main__': WorkerLoop().run()
