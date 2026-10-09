"""Apply graph intent once; persist and reconcile external control separately."""
from copy import deepcopy
import time
import threading

from sqlalchemy import select
from services.api.db import Session, Project, Node, TaskRun, CommandReceipt, uid, now, asdict
from services.api.common import get, error, graph_from_db, save_graph, project_dir, emit, touch_dependents, safe_path
from services.worker.scheduler import _lock_project, ACTIVE, TERMINAL, finalize_cancelled_run_elapsed
from research.kernel import GraphCommandService
from research.agents.budget import interrupt_run_reservations
from .models import Intervention, InterventionEffect


_control_slots = threading.BoundedSemaphore(2)


def kick_effects(*, intervention_id=None, limit=1):
    """Bound external control work without occupying a scientific worker slot.

    The database outbox owns retries; a full pool or process exit loses only a
    wakeup. Leases let another worker resume the persisted effect.
    """
    if not _control_slots.acquire(blocking=False): return
    def reconcile():
        try: process_effects(intervention_id=intervention_id, limit=limit)
        except Exception:
            import traceback
            traceback.print_exc()
        finally: _control_slots.release()
    threading.Thread(target=reconcile, name='forest-intervention-control', daemon=True).start()


def readback(session, intervention):
    return {**asdict(intervention), 'effects': [
        {k: v for k, v in asdict(effect).items() if k not in ('target', 'lease_owner', 'lease_until')}
        for effect in session.scalars(select(InterventionEffect).where(
            InterventionEffect.intervention_id == intervention.id).order_by(InterventionEffect.created_at, InterventionEffect.id))]}


def existing(session, project_id, request_id, intent):
    receipt = session.scalar(select(CommandReceipt).where(
        CommandReceipt.project_id == project_id, CommandReceipt.request_id == request_id))
    intervention = session.scalar(select(Intervention).where(
        Intervention.project_id == project_id, Intervention.request_id == request_id))
    if intervention and intervention.intent != intent:
        error('REQUEST_ID_CONFLICT', 'This request identity belongs to different intent', 409)
    if receipt:
        return {**receipt.response, **({'intervention': readback(session, intervention)} if intervention else {})}
    if intervention:
        error('RETIRED_REQUEST','Historical intervention has no executable command receipt; use a new request identity',409)
    return None


def summarize(session, intervention):
    session.flush()
    states = list(session.scalars(select(InterventionEffect.status).where(
        InterventionEffect.intervention_id == intervention.id)))
    if not states or all(x == 'applied' for x in states):
        intervention.status = 'applied'; intervention.applied_at = now()
    elif any(x in ('failed', 'uncertain') for x in states):
        intervention.status = 'partially_applied' if 'applied' in states else 'needs_attention'
    elif all(x == 'superseded' for x in states):
        intervention.status = 'superseded'
    elif all(x in ('applied', 'superseded') for x in states):
        intervention.status = 'partially_applied'
    else:
        intervention.status = 'accepted'


def record_effects(session, intervention, actions):
    for action in actions:
        if action.get('action') == 'stop_scheduling_branch':
            queued = session.scalars(select(TaskRun).where(
                TaskRun.project_id == intervention.project_id, TaskRun.branch_id == action['branch_id'],
                TaskRun.status == 'queued').with_for_update().execution_options(populate_existing=True))
            for run in queued:
                effect = InterventionEffect(intervention_id=intervention.id, project_id=run.project_id,
                    run_id=run.id, action='hold_scheduling', status='applied', applied_at=now(),
                    attempt_id=(run.config.get('execution_attempt') or {}).get('id'), target={},
                    observation={'state':'dispatch_held', 'external_execution_stopped':False, 'observed_at':now()})
                session.add(effect)
                run.status = 'waiting_input'
                run.resource = {**run.resource, 'blocked_reason':'branch_scheduling_hold',
                    'branch_scheduling_hold': {'branch_id':action['branch_id'], 'intervention_id':intervention.id}}
                run.error = 'Branch scheduling is held; restore the branch and explicitly resume this run'
                node = session.get(Node, run.node_id) if run.node_id else None
                if node and node.extra.get('latest_run_id') == run.id: node.execution_status = run.status
                emit(session, run.project_id, 'run_changed', {'run_id':run.id, 'status':run.status})
            continue
        if action.get('action') != 'cancel_current_run':
            continue
        query=select(TaskRun).where(TaskRun.project_id==intervention.project_id,TaskRun.status.in_(ACTIVE))
        query=query.where(TaskRun.id==action['target_run_id']) if action.get('target_run_id') else query.where(TaskRun.node_id==action['node_id'])
        targets = session.scalars(query.with_for_update().execution_options(populate_existing=True))
        for run in targets:
            if session.scalar(select(InterventionEffect.id).where(
                InterventionEffect.intervention_id == intervention.id,
                InterventionEffect.run_id == run.id, InterventionEffect.action == 'cancel')):
                continue
            effect = InterventionEffect(intervention_id=intervention.id, project_id=run.project_id,
                run_id=run.id, attempt_id=(run.config.get('execution_attempt') or {}).get('id'),
                action='cancel', target={k: deepcopy(v) for k, v in asdict(run).items()
                    if k in ('id', 'project_id', 'node_id', 'output_path', 'pid', 'process_created', 'config', 'status')})
            session.add(effect); session.flush()
            prior = run.resource.get('pending_intervention', {}).get('effect_id')
            if prior:
                previous = session.get(InterventionEffect, prior)
                if previous and previous.status not in ('applied', 'superseded'):
                    previous.status = 'superseded'
                    previous.observation = {'reason': 'replaced_by_cancellation', 'intervention_id': intervention.id}
                    summarize(session, get(session, Intervention, previous.intervention_id))
            # A persisted fence prevents dispatch, ordinary resume, recovery and
            # late result promotion before the external effect is confirmed.
            run.resource = {**run.resource, 'pending_intervention': {'id': intervention.id,
                'effect_id': effect.id, 'action': 'cancel', 'accepted_at': intervention.accepted_at}}
            if run.status in ('queued', 'waiting_input', 'budget_exhausted') and not run.pid and not run.started_at:
                finalize_cancelled_run_elapsed(run)
                run.status = 'cancelled'; run.finished_at = now(); run.error = 'Cancelled by owner' if intervention.kind == 'run_control' else 'Stopped to apply edits'
                from .controls import close_unconsumed
                close_unconsumed(session, run)
                interrupt_run_reservations(run.id, session=session)
                effect.status = 'applied'; effect.applied_at = now()
                effect.observation = {'state': 'never_dispatched', 'observed_at': now()}
                run.resource = {k: v for k, v in run.resource.items() if k != 'pending_intervention'}
            else:
                run.status = 'pausing'
            node = session.get(Node, run.node_id)
            if node and node.extra.get('latest_run_id') == run.id:
                node.execution_status = run.status
            emit(session, run.project_id, 'run_changed', {'run_id': run.id, 'status': run.status})
    summarize(session, intervention)


def accept_cancel_in_session(session, project, request_id, run_ids, *, expected_revision=None, actor='owner', parameters=None):
    """Persist a targeted lifecycle cancellation under the caller's project lock."""
    intent={'action':'cancel','run_ids':list(dict.fromkeys(run_ids))}
    if parameters is not None: intent['parameters']=deepcopy(parameters)
    old=session.scalar(select(Intervention).where(Intervention.project_id==project.id,Intervention.request_id==request_id))
    if old:
        if old.intent!=intent: error('REQUEST_ID_CONFLICT','This request identity belongs to different control intent',409)
        return old
    if expected_revision is not None and expected_revision!=project.revision:
        error('REVISION_CONFLICT','The reviewed project changed',409)
    for ident in intent['run_ids']:
        if get(session,TaskRun,ident).project_id!=project.id: error('CROSS_PROJECT','Control target belongs to another project',422)
    row=Intervention(project_id=project.id,request_id=request_id,actor=actor,kind='run_control',
        observed_revision=project.revision,applied_revision=project.revision,intent=intent,
        impact={'run_ids':intent['run_ids']},accepted_at=now())
    session.add(row);session.flush()
    record_effects(session,row,[{'action':'cancel_current_run','target_run_id':ident} for ident in intent['run_ids']])
    emit(session,project.id,'intervention_changed',{'intervention_id':row.id,'status':row.status})
    return row


def cancel_run(run_id, body):
    with Session() as reader: project_id=get(reader,TaskRun,run_id).project_id
    with Session.begin() as session:
        project=_lock_project(session,project_id)
        row=accept_cancel_in_session(session,project,body.get('request_id') or uid(),[run_id],
            expected_revision=body.get('expected_revision'))
        ident=row.id
    kick_effects(intervention_id=ident)
    with Session() as session:
        return {**asdict(get(session,TaskRun,run_id)),'intervention':readback(session,get(session,Intervention,ident))}


def apply_in_session(session, project, request_id, observed_revision, commands, *, actor='owner', batch=False, origin=None, single_revision=False):
    """Caller holds the project writer lock. No network/process control here."""
    intent = {'expected_revision': observed_revision, 'commands': deepcopy(commands), 'batch': batch}
    if origin is not None: intent['origin'] = deepcopy(origin)
    if single_revision: intent['single_revision'] = True
    old = existing(session, project.id, request_id, intent)
    if old:
        return old
    if type(observed_revision) is not int or project.revision != observed_revision:
        error('REVISION_CONFLICT', 'The reviewed graph changed; inspect current state before applying', 409)
    if not isinstance(commands, list) or not commands or len(commands) > 200:
        error('INVALID_COMMANDS', 'Provide between 1 and 200 graph commands', 422)
    graph = graph_from_db(session, project)
    history = deepcopy(graph.get('_history', {'undo': [], 'redo': []})); before = deepcopy(graph)
    before.pop('_history', None)
    results = []; workspace_plans = []
    compound_history = (batch or single_revision) and not any(
        command.get('operation') in ('undo', 'redo') for command in commands if isinstance(command, dict))
    from .workspaces import prepare, record
    compound_service = GraphCommandService(graph, project_dir(project.id)) if compound_history else None
    for index, command in enumerate(commands):
        if not isinstance(command, dict): error('INVALID_COMMAND', 'Commands must be objects', 422)
        # A compound command owns one undo snapshot. Carrying every intermediate
        # snapshot through subsequent kernel copies makes large batches cubic.
        if compound_history: graph.pop('_history', None)
        step = {**command, 'project_id': project.id, 'request_id': request_id+':'+str(index),
                'expected_revision': graph['revision']}
        result = (compound_service.apply_compound_step(step) if compound_service else
                  GraphCommandService(graph, project_dir(project.id)).apply(step, defer_files=True))
        for action in result.pop('workspace_actions', []):
            workspace_plans.append(prepare(project.id, graph, action))
            destination = next(b for b in result['graph']['branches'] if b['id'] == action['branch_id'])
            destination['status'] = 'materializing'
        if compound_history: result['graph'].pop('_history', None)
        if results: results[-1].pop('graph', None)
        graph = result['graph']; results.append(result)
    if batch or single_revision:
        history['undo'].append(before); history['redo'] = []; graph['_history'] = history
    if any(command.get('operation') in ('undo','redo') for command in commands):
        before_branches={branch['id'] for branch in before['branches']}
        for branch in graph['branches']:
            if branch['id'] in before_branches or not branch.get('workspace_intervention'): continue
            original=session.get(InterventionEffect,branch['workspace_intervention']['effect_id'])
            if original and original.action=='materialize_workspace' and original.target:
                workspace_plans.append(deepcopy(original.target))
                branch['status']='materializing'
            else:
                branch['historical_workspace_intervention']=branch.pop('workspace_intervention')
                branch['status']='disabled'
    if single_revision: graph['revision'] = observed_revision + 1
    save_graph(session, project, graph)
    impacts = [r['impact'] for r in results]
    impact = deepcopy(impacts[0]) if len(impacts) == 1 else {'commands': impacts,
        'affected_nodes': list(dict.fromkeys(n for i in impacts for n in i.get('affected_nodes', []))),
        'actions': [a for i in impacts for a in i.get('actions', [])]}
    intervention = Intervention(project_id=project.id, request_id=request_id, actor=actor, kind='graph',
        observed_revision=observed_revision, applied_revision=project.revision,
        intent=intent, impact=impact, accepted_at=now())
    session.add(intervention); session.flush()
    record(session, intervention, workspace_plans)
    record_effects(session, intervention, impact.get('actions', []))
    scientific_nodes=list(dict.fromkeys(n for entry in impacts
        if any(category not in ('layout','display','archive') for category in entry.get('categories',{}).values())
        for key in ('rerun_nodes','refresh_nodes') for n in entry.get(key,[])
        if entry.get('categories',{}).get(n) not in ('layout','display','archive')))
    for nid in scientific_nodes:
        touch_dependents(session, project.id, nid if isinstance(nid, str) else nid.get('id', ''))
    run_ids = []
    from services.worker.scheduler import enqueue_selected
    for index, (command, result) in enumerate(zip(commands, results, strict=True)):
        if command.get('run'):
            run_ids.extend(r.id for r in enqueue_selected(session, result.get('run_nodes', []), request_id+f':{index}:run'))
    public = {**results[-1], 'graph': {k: v for k, v in graph.items() if k != '_history'},
        'revision': project.revision, 'impact': impact, 'run_ids': run_ids,
        'applied': len(commands), 'node_count': len(graph['nodes']), 'edge_count': len(graph['edges']),
        'intervention': readback(session, intervention)}
    session.add(CommandReceipt(project_id=project.id, request_id=request_id, response=public))
    emit(session, project.id, 'node_changed', {'revision': project.revision})
    emit(session, project.id, 'intervention_changed', {'intervention_id': intervention.id, 'status': intervention.status})
    return public


def apply_commands(project_id, request_id, observed_revision, commands, *, actor='owner', batch=False):
    with Session.begin() as session:
        project = _lock_project(session, project_id)
        result = apply_in_session(session, project, request_id, observed_revision, commands, actor=actor, batch=batch)
        ident = result.get('intervention', {}).get('id')
    if ident:
        kick_effects(intervention_id=ident, limit=10)
        with Session() as session:
            result['intervention'] = readback(session, get(session, Intervention, ident))
            # A terminal control confirmation changes presentation, not the
            # graph edit revision. Return the committed current presentation.
            project = get(session, Project, project_id)
            if project.revision == result['revision']:
                result['graph'] = {k: v for k, v in graph_from_db(session, project).items() if k != '_history'}
    return result


def cancel_target(target):
    """Reconcile concrete executor identities outside all database locks."""
    from research.execution.process_manager import process_manager
    from runners.local import stop_group, process_matches
    output = safe_path(project_dir(target['project_id']), target['output_path'])
    # Stop the submitting executor first so it cannot create another detached
    # job after the backend inventory/stop has been observed.
    if target.get('pid'):
        stop_group(target['pid'], target.get('process_created'))
        if process_matches(target['pid'], target.get('process_created')):
            raise RuntimeError('Executor is still live')
    manager = process_manager(output/'workspace', target['config'])
    manager.cancel_all()
    observations = {}
    if target['config'].get('execution_backend') == 'container':
        from runners.container import cancel_container
        observations['container'] = cancel_container(target['config'], output, run_id=target['id'])
        if observations['container'] and observations['container']['status'] not in ('completed','failed','cancelled','lost'):
            raise RuntimeError('Container stop is not yet confirmed')
    if target['config'].get('remote'):
        from runners.remote import cancel_remote
        observations['remote'] = cancel_remote(target['config'], output, run_id=target['id'])
        if observations['remote'] and observations['remote']['status'] not in TERMINAL:
            raise RuntimeError('Remote stop is not yet confirmed')
    deadline = time.monotonic()+3
    while True:
        processes = manager.all()
        active = [p for p in processes if p['status'] not in ('completed', 'failed', 'cancelled', 'lost')]
        if not active: break
        if time.monotonic() >= deadline: raise RuntimeError('Managed process stop is not yet confirmed')
        time.sleep(.05)
    return {**observations, 'executor_live': False,
        'managed_processes': [{'process_id': p['process_id'], 'status': p['status']} for p in processes], 'observed_at': now()}


def process_effects(*, intervention_id=None, limit=10):
    from .controls import reconcile_unconsumed
    reconcile_unconsumed()
    from .workspaces import reconcile
    reconcile(intervention_id=intervention_id, limit=limit)
    owner = uid()
    with Session() as session:
        query = select(InterventionEffect.id, InterventionEffect.project_id).where(
            InterventionEffect.action.in_(('cancel','pause','resume')),
            InterventionEffect.status.in_(('pending', 'applying', 'uncertain')),
            InterventionEffect.retry_after <= time.time(), InterventionEffect.lease_until <= time.time())
        if intervention_id: query = query.where(InterventionEffect.intervention_id == intervention_id)
        candidates = session.execute(query.order_by(InterventionEffect.created_at).limit(limit)).all()
    for effect_id, project_id in candidates:
        with Session.begin() as session:
            _lock_project(session, project_id)
            effect = session.scalar(select(InterventionEffect).where(InterventionEffect.id == effect_id).with_for_update())
            if not effect or effect.status not in ('pending', 'applying', 'uncertain') or effect.lease_until > time.time(): continue
            run = session.scalar(select(TaskRun).where(TaskRun.id == effect.run_id).with_for_update())
            if not run or (run.config.get('execution_attempt') or {}).get('id') != effect.attempt_id:
                effect.status = 'superseded'; effect.observation = {'reason': 'target_run_or_attempt_changed'}
                summarize(session, get(session, Intervention, effect.intervention_id)); continue
            effect.status = 'applying'; effect.lease_owner = owner; effect.lease_until = time.time()+120; effect.attempts += 1
            target = deepcopy(effect.target); intervention = effect.intervention_id; action = effect.action
        observation = {}; failure = None
        try:
            if action == 'cancel': observation = cancel_target(target)
            else:
                from .lifecycle import lifecycle_target
                observation = lifecycle_target(target, action)
        except Exception as exc: failure = str(exc)[:1500]
        with Session.begin() as session:
            _lock_project(session, project_id)
            effect = session.scalar(select(InterventionEffect).where(InterventionEffect.id == effect_id).with_for_update())
            if not effect or effect.lease_owner != owner: continue
            run = session.scalar(select(TaskRun).where(TaskRun.id == effect.run_id).with_for_update())
            if (not run or (run.config.get('execution_attempt') or {}).get('id') != effect.attempt_id
                    or run.resource.get('pending_intervention', {}).get('effect_id') != effect.id):
                effect.status = 'superseded'
            elif failure:
                effect.status = 'uncertain'; effect.error = failure; effect.retry_after = time.time()+min(60, 2**min(effect.attempts, 6))
            elif action != 'cancel':
                from .lifecycle import finish_lifecycle
                finish_lifecycle(run, effect, observation)
                node = session.get(Node, run.node_id) if run.node_id else None
                if node and node.extra.get('latest_run_id') == run.id: node.execution_status = run.status
                emit(session, project_id, 'run_changed', {'run_id': run.id, 'status': run.status})
            else:
                finalize_cancelled_run_elapsed(run)
                run.status = 'cancelled'; run.finished_at = now(); run.error = 'Cancelled by owner' if get(session, Intervention, intervention).kind == 'run_control' else 'Stopped to apply edits'
                from .controls import close_unconsumed
                close_unconsumed(session, run)
                run.resource = {**{k: v for k, v in run.resource.items() if k != 'pending_intervention'},
                                'last_intervention': {'id': intervention, 'effect_id': effect.id, 'applied_at': now()}}
                interrupt_run_reservations(run.id, session=session)
                node = session.get(Node, run.node_id) if run.node_id else None
                if node and node.extra.get('latest_run_id') == run.id: node.execution_status = 'cancelled'
                effect.status = 'applied'; effect.applied_at = now(); effect.error = None; effect.observation = observation
                emit(session, project_id, 'run_changed', {'run_id': run.id, 'status': 'cancelled'})
            effect.lease_until = 0; effect.lease_owner = None
            parent = get(session, Intervention, intervention); summarize(session, parent)
            emit(session, project_id, 'intervention_changed', {'intervention_id': intervention, 'status': parent.status})
