import os
import signal
from services.api.db import Session,TaskRun
from services.api.common import get,emit

def checkpoint(run_id,stage,payload,enabled):
    if stage not in enabled: return payload
    from services.worker.scheduler import _lock_project
    with Session() as reader: project_id=get(reader,TaskRun,run_id).project_id
    with Session.begin() as s:
        _lock_project(s,project_id);run=get(s,TaskRun,run_id,for_update=True)
        if run.resource.get('pending_intervention'): raise RuntimeError('Lifecycle control is pending at debug checkpoint')
        run.status='waiting_input'; run.resource={**run.resource,'checkpoint':{'stage':stage,'payload':payload,'override':None}}
        emit(s,run.project_id,'run_changed',{'run_id':run.id,'status':'waiting_input','checkpoint':stage})
    # The worker launched this executor in its own process group.
    os.killpg(os.getpgrp(),signal.SIGSTOP)
    with Session.begin() as s:
        _lock_project(s,project_id);run=get(s,TaskRun,run_id,for_update=True)
        point=run.resource.get('checkpoint') or {}; override=point.get('override'); run.resource={**run.resource,'checkpoint':None}
        if run.status=='cancelled': raise RuntimeError('Cancelled at debug checkpoint')
    return override if override is not None else payload
