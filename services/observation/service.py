"""API-owned background observer. Optional failures never escape into science.

One daemon scheduler and a bounded narrator pool per API process; database
leases and the global job limit provide correctness across API processes.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, update
from services.api.db import Session, Project, uid, now
from .models import ObservationState, ObservationScope, ReporterPolicy, ReportJob
from .projection import project_snapshot
from .reporting import write_lock, ensure_state, claim, execute, enqueue, policy
from .files import sync_scopes, reconcile

log=logging.getLogger(__name__)

def refresh_projection(project_id, owner, *, release=False, file_hints=True):
    with Session.begin() as s:
        write_lock(s); state=ensure_state(s,project_id)
        if state.lease_owner!=owner and state.lease_until>time.time(): return False
        state.lease_owner=owner; state.lease_until=time.time()+30
        epoch=state.epoch; generation=state.generation+1; cursor=state.cursor
        previous_gap=(state.snapshot.get('health') or {}).get('history_gap',False)
    snapshot=project_snapshot(project_id,epoch=epoch,generation=generation,previous_cursor=cursor)
    snapshot.health.history_gap=snapshot.health.history_gap or previous_gap
    if not file_hints:
        snapshot.health.file_coverage+='; native hints unavailable; polling reconciliation only'
    with Session.begin() as s:
        changed=s.execute(update(ObservationState).where(ObservationState.project_id==project_id,
            ObservationState.epoch==epoch,ObservationState.lease_owner==owner,
            ObservationState.generation<generation).values(generation=generation,cursor=snapshot.cursor,
            snapshot=snapshot.model_dump(),observed_at=snapshot.observed_at,
            **({"lease_until":0,"lease_owner":None} if release else {})))
        return changed.rowcount==1

class ObservationService:
    def __init__(self):
        self.owner=uid(); self.stop_event=threading.Event(); self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='forest-reporter')
        self.thread=threading.Thread(target=self.run,daemon=True,name='forest-observer'); self.pending=set(); self.last_scopes={}; self.scope_cursors={}; self.dirty={}; self.last_watch_retry=0
    def start(self):
        from .watcher import FileWatcher
        self.watcher=FileWatcher(self.stop_event); self.watcher.start(); self.thread.start()
    def close(self):
        self.stop_event.set(); self.thread.join(timeout=3); self.watcher.thread.join(timeout=1); self.pool.shutdown(wait=False,cancel_futures=True)
        with Session.begin() as s:
            s.execute(update(ObservationState).where(ObservationState.lease_owner==self.owner).values(lease_until=0,lease_owner=None))
    def run(self):
        last_project=''
        while not self.stop_event.is_set():
            try:
                if not self.watcher.thread.is_alive() and time.monotonic()-self.last_watch_retry>5:
                    from .watcher import FileWatcher
                    self.last_watch_retry=time.monotonic(); self.watcher=FileWatcher(self.stop_event); self.watcher.start()
                # Fair project rotation bounds work per tick on large installations.
                with Session() as s:
                    ids=list(s.scalars(select(Project.id).where(Project.id>last_project).order_by(Project.id).limit(10)))
                if not ids: last_project=''; self.stop_event.wait(.25); continue
                last_project=ids[-1]
                for ident in ids:
                    if self.stop_event.is_set(): break
                    try:
                        if not refresh_projection(ident,self.owner,file_hints=self.watcher.available): continue
                        if time.monotonic()-self.last_scopes.get(ident,0)>10:
                            with Session.begin() as s:
                                write_lock(s); ensure_state(s,ident)
                                self.scope_cursors[ident]=sync_scopes(s,ident,cursor=self.scope_cursors.get(ident,''))
                            self.last_scopes[ident]=time.monotonic()
                        # Reserve one bounded pass for live/project inventories so a
                        # large historical import cannot starve an unfinished scan.
                        with Session() as s:
                            state=s.get(ObservationState,ident)
                            live_ids=[f['sources'][0]['object_id'] for f in state.snapshot.get('facts',[]) if f['id'].startswith('run:') and f['value'] in ('queued','running','waiting','waiting_input','pausing','paused')]
                            from sqlalchemy import or_
                            priority=s.scalar(select(ObservationScope.id).where(ObservationScope.project_id==ident,
                                ObservationScope.kind!='remote_workspace',or_(ObservationScope.kind.in_(['project','branch_workspace']),
                                ObservationScope.object_id.in_(live_ids))).order_by(ObservationScope.observed_at.asc().nullsfirst(),ObservationScope.id).limit(1))
                            scope=s.scalar(select(ObservationScope.id).where(ObservationScope.project_id==ident,ObservationScope.kind!='remote_workspace').order_by(ObservationScope.observed_at.asc().nullsfirst(),ObservationScope.id).limit(1))
                        for candidate in dict.fromkeys([priority,scope]):
                            if candidate: reconcile(candidate)
                        self.automatic(ident)
                    except Exception:
                        log.warning('Observation pass failed; current state remains available')
                self.pending={f for f in self.pending if not f.done()}
                while len(self.pending)<2:
                    job=claim(self.owner)
                    if not job: break
                    self.pending.add(self.pool.submit(execute,job,self.owner))
            except Exception:
                log.warning('Observation service degraded; reconciliation will retry')
            self.stop_event.wait(1)
    def automatic(self,project_id):
        with Session() as s:
            settings,_=policy(s,project_id); row=s.get(ReporterPolicy,project_id); state=s.get(ObservationState,project_id)
            if not row or not state or not state.snapshot or not settings.enabled or not settings.automatic:
                self.dirty.pop(project_id,None); return
            cursor=state.snapshot['cursor']; last=row.last_cursor; dispatch=row.last_dispatch
            # Reporting writes do not emit research events or advance this cursor.
            if cursor<=last: self.dirty.pop(project_id,None); return
            previous=s.scalar(select(ReportJob).where(ReportJob.project_id==project_id,ReportJob.epoch==state.epoch,
                ReportJob.policy_version==row.version).order_by(ReportJob.created_at.desc()).limit(1))
            if previous and previous.dependencies==state.snapshot['dependencies']:
                with Session.begin() as writer:
                    write_lock(writer); ensure_state(writer,project_id)
                    saved=writer.get(ReporterPolicy,project_id)
                    if saved and saved.version==row.version: saved.last_cursor=cursor
                self.dirty.pop(project_id,None); return
        first=self.dirty.setdefault(project_id,time.time())
        elapsed=time.time()-dispatch
        if elapsed>=settings.minimum_seconds and (time.time()-first>=5 or time.time()-first>=settings.maximum_wait_seconds):
            enqueue(project_id,f'auto:{state.epoch}:{cursor}',automatic=True); self.dirty.pop(project_id,None)
