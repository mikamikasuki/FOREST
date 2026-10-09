"""Durable lifecycle intents; external control is reconciled outside transactions."""
from copy import deepcopy
import signal
import time

from sqlalchemy import select
from services.api.db import Session, Project, TaskRun, Node, uid, now, asdict
from services.api.common import get, error, emit, project_dir, safe_path
from services.worker.scheduler import (_lock_project, ACTIVE, TERMINAL, requested_task_timeout,
    reserve_project_time, _elapsed_seconds_at)
from .models import Intervention, InterventionEffect
from .application import readback, summarize, kick_effects


def resume_available(s, r):
    if r.resource.get('branch_scheduling_hold'):
        from services.api.db import Branch
        branch = s.get(Branch, r.branch_id)
        if not branch or branch.status != 'active': return False
    legacy_context_failure = (r.kind == 'agent' and r.status == 'failed' and
        str(r.error or '').startswith(('Task controls exceed the configured context_char_budget;',
            'The latest complete native tool exchange exceeds context_char_budget;')))
    return r.status in ('paused', 'waiting_input', 'waiting', 'budget_exhausted') or legacy_context_failure


def prepare_resume(s, project, r, body):
    if r.resource.get('branch_scheduling_hold'):
        from services.api.db import Branch
        branch = s.get(Branch, r.branch_id)
        if not branch or branch.status != 'active':
            error('BRANCH_INACTIVE', 'Restore the branch before explicitly resuming its held run', 409)
    legacy_context_failure = (r.kind=='agent' and r.status=='failed' and
        str(r.error or '').startswith(('Task controls exceed the configured context_char_budget;',
            'The latest complete native tool exchange exceeds context_char_budget;')))
    if not resume_available(s, r): error('INVALID_RUN_STATE','Run is not paused or waiting',409)
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
    if resource.pop('branch_scheduling_hold', None): resource.pop('blocked_reason', None)
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
    return legacy_context_failure


def accept_lifecycle(session, project, request_id, run_ids, action, *, body=None, actor='owner'):
    body = body or {}
    intent = {'action': action, 'run_ids': list(dict.fromkeys(run_ids)),
              'parameters': {key: value for key, value in body.items() if key not in ('request_id', 'expected_revision')}}
    old = session.scalar(select(Intervention).where(Intervention.project_id == project.id,
        Intervention.request_id == request_id))
    if old:
        if old.intent != intent: error('REQUEST_ID_CONFLICT', 'Request identity belongs to different lifecycle intent', 409)
        return old
    if body.get('expected_revision', project.revision) != project.revision:
        error('REVISION_CONFLICT', 'The reviewed project changed', 409)
    row = Intervention(project_id=project.id, request_id=request_id, actor=actor, kind='run_control',
        observed_revision=project.revision, applied_revision=project.revision,
        intent=intent, impact={'run_ids': intent['run_ids']}, accepted_at=now())
    session.add(row); session.flush()
    for identifier in intent['run_ids']:
        run = get(session, TaskRun, identifier, for_update=True)
        if run.project_id != project.id: error('CROSS_PROJECT', 'Control target belongs to another project', 422)
        if run.resource.get('pending_intervention'):
            error('CONTROL_PENDING', 'An existing lifecycle request is still being reconciled', 409)
        previous = run.status
        if action == 'pause':
            if previous not in ('queued', 'waiting', 'waiting_input', 'budget_exhausted', 'running', 'paused'):
                error('INVALID_RUN_STATE', 'Run cannot be paused in its current state', 409)
            run.resource = {**run.resource, 'elapsed_seconds': _elapsed_seconds_at(run)}
        elif action == 'resume': legacy_context_failure = prepare_resume(session, project, run, body)
        else: error('UNKNOWN_ACTION', 'Use pause or resume', 422)
        target = {key: deepcopy(value) for key, value in asdict(run).items()
                  if key in ('id', 'project_id', 'node_id', 'output_path', 'pid', 'process_created', 'config')}
        target['status'] = previous
        if action == 'resume': target['legacy_context_failure'] = legacy_context_failure
        effect = InterventionEffect(intervention_id=row.id, project_id=project.id, run_id=run.id,
            action=action, attempt_id=(run.config.get('execution_attempt') or {}).get('id'), target=target)
        session.add(effect); session.flush()
        if not run.pid and not run.started_at and not run.config.get('remote'):
            # No executor has ever been admitted, so no external stop/resume
            # can be pending. This includes durable review/budget yields.
            outcome = {'status': 'paused' if action == 'pause' else 'queued',
                       'state': 'never_dispatched', 'observed_at': now()}
            if action == 'resume': run.config = {**run.config, '_next_attempt': {'mode': 'continue'}}
            finish_lifecycle(run, effect, outcome)
        else:
            run.status = 'pausing'
            run.resource = {**run.resource, 'pending_intervention': {'id': row.id,
                'effect_id': effect.id, 'action': action, 'accepted_at': row.accepted_at}}
        node = session.get(Node, run.node_id) if run.node_id else None
        if node and node.extra.get('latest_run_id') == run.id: node.execution_status = run.status
        emit(session, project.id, 'run_changed', {'run_id': run.id, 'status': run.status})
    summarize(session, row)
    emit(session, project.id, 'intervention_changed', {'intervention_id': row.id, 'status': row.status})
    return row


def control_run(run_id, action, body):
    with Session() as reader: project_id = get(reader, TaskRun, run_id).project_id
    with Session.begin() as session:
        project = _lock_project(session, project_id)
        row = accept_lifecycle(session, project, body.get('request_id') or uid(), [run_id], action, body=body)
        ident = row.id
    kick_effects(intervention_id=ident)
    with Session() as session:
        return {**asdict(get(session, TaskRun, run_id)), 'intervention': readback(session, get(session, Intervention, ident))}


def lifecycle_target(target, action):
    from runners.local import signal_group, process_matches
    from research.execution.process_manager import process_manager
    import psutil
    output = safe_path(project_dir(target['project_id']), target['output_path'])
    manager = process_manager(output/'workspace', target['config'])
    observations = {}
    if action == 'pause':
        from research.execution.transaction_fence import pause_boundary
        with pause_boundary(output):
            signaled = target.get('pid') and signal_group(target['pid'], target.get('process_created'), signal.SIGSTOP)
            if signaled:
                deadline = time.monotonic() + 3
                while process_matches(target['pid'], target.get('process_created')):
                    if psutil.Process(target['pid']).status() == psutil.STATUS_STOPPED: break
                    if time.monotonic() >= deadline: raise RuntimeError('Executor pause remains unconfirmed')
                    time.sleep(.02)
            manager.signal_all(signal.SIGSTOP)
        if target['config'].get('execution_backend') == 'container':
            from runners.container import control_container
            observations['container'] = control_container(target['config'], output, 'pause')
        if target['config'].get('remote'):
            from runners.remote import control_remote
            observations['remote'] = control_remote(target['config'], output, 'pause', run_id=target['id'])
        processes = manager.all()
        if any(item['status'] not in (*TERMINAL, 'lost', 'paused') for item in processes):
            raise RuntimeError('Managed process pause remains unconfirmed')
        for backend in ('container', 'remote'):
            if observations.get(backend) and observations[backend]['status'] not in (*TERMINAL, 'lost', 'paused'):
                raise RuntimeError(backend + ' pause remains unconfirmed')
        if target['status'] == 'running' and not signaled and not observations:
            raise RuntimeError('Executor was lost before pause could be confirmed')
        live = bool(signaled or any(item['status'] == 'paused' for item in processes)
                    or any(value and value['status'] == 'paused' for value in observations.values()))
        return {**observations, 'status': 'paused', 'paused_live_attempt': live,
                'executor_live': bool(signaled),
                'managed_processes': [{'process_id': item['process_id'], 'status': item['status']} for item in processes],
                'observed_at': now()}
    host_live = bool(target.get('pid') and process_matches(target['pid'], target.get('process_created')))
    if not host_live and target['config'].get('execution_backend') == 'container':
        # Reconnecting requires a newly admitted host executor. Its container
        # remains paused until the worker reserves capacity and dispatches it.
        return {'status': 'queued', 'next_mode': 'container_reconnect', 'observed_at': now()}
    if target['config'].get('execution_backend') == 'container':
        from runners.container import control_container
        observations['container'] = control_container(target['config'], output, 'resume')
    if target['config'].get('remote'):
        from runners.remote import control_remote
        observations['remote'] = control_remote(target['config'], output, 'resume', run_id=target['id'])
    if target.get('pid'):
        manager.signal_all(signal.SIGCONT)
        if signal_group(target['pid'], target.get('process_created'), signal.SIGCONT):
            return {**observations, 'status': 'running', 'observed_at': now()}
        if (target['config'].get('execution_backend') != 'container' and not target['config'].get('remote')
                and not target.get('legacy_context_failure')):
            raise RuntimeError('Paused executor was lost; restart the run')
    mode = 'container_reconnect' if target['config'].get('execution_backend') == 'container' else 'continue'
    return {**observations, 'status': 'queued', 'next_mode': mode,
            'live_process_pending_dispatch': any(item['status'] not in (*TERMINAL, 'lost') for item in manager.all()),
            'observed_at': now()}


def finish_lifecycle(run, effect, observation):
    run.status = observation['status']
    resource = {key: value for key, value in run.resource.items() if key != 'pending_intervention'}
    if effect.action == 'pause':
        resource.pop('waiting_started_at', None); resource.pop('elapsed_before_wait', None)
        if observation.get('executor_live') is False:
            run.pid = None; run.process_created = None
        if observation.get('paused_live_attempt'): resource['paused_live_attempt'] = True
    else:
        resource.pop('paused_live_attempt', None)
        if observation.get('next_mode'):
            run.pid = None; run.process_created = None
            run.config = {**run.config, '_next_attempt': {'mode': observation['next_mode']}}
            if observation['next_mode'] == 'container_reconnect': resource['container_reconnect_pending_dispatch'] = True
            elif observation.get('live_process_pending_dispatch'): resource['live_process_pending_dispatch'] = True
    resource['last_intervention'] = {'id': effect.intervention_id, 'effect_id': effect.id, 'applied_at': now()}
    run.resource = resource
    effect.status = 'applied'; effect.applied_at = now(); effect.error = None; effect.observation = observation
