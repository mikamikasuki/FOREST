"""Database-backed scheduling with one dependency map per selected research DAG."""
from copy import deepcopy
from datetime import datetime, timezone
from sqlalchemy import select, func
from services.api.db import *
from services.api.common import get, error, project_dir, graph_from_db, emit

ACTIVE = ('queued', 'running', 'pausing', 'paused', 'waiting_input', 'waiting', 'budget_exhausted')
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted', 'skipped')
TIME_BUDGET_RESERVED_STATUSES = ('queued', 'running', 'pausing', 'paused', 'waiting', 'budget_exhausted')
_TIMEOUT_UNSET = object()


def _node_created_before_run(node, run):
    created_at = node.get('created_at') if isinstance(node, dict) else getattr(node, 'created_at', None)
    if not created_at or not run.created_at:
        return False
    try:
        return datetime.fromisoformat(created_at) <= datetime.fromisoformat(run.created_at)
    except (TypeError, ValueError):
        return False


def _node_config_at_revision(s, run):
    node = s.get(Node, run.node_id)
    if (node and node.revision == run.node_revision and
            _node_created_before_run(node, run)):
        return node.config or {}, 'unchanged_node_revision'
    project = s.get(Project, run.project_id)
    history = ((project.graph_meta or {}).get('_history') or {}) if project else {}
    for stack in ('undo', 'redo'):
        for snapshot in reversed(history.get(stack) or []):
            old = next((item for item in snapshot.get('nodes', [])
                        if item.get('id') == run.node_id and
                        item.get('revision', 0) == run.node_revision and
                        _node_created_before_run(item, run)), None)
            if old is not None:
                return old.get('config') or {}, 'historical_node_revision'
    return None, None


def requested_task_timeout(s, run):
    """Return the user task cap separately from the effective project cap."""
    resource = run.resource or {}
    policy = resource.get('time_budget') or {}
    config = run.config or {}
    source = 'legacy_effective_timeout'
    if 'requested_task_timeout_seconds' in policy:
        value = policy['requested_task_timeout_seconds']
        source = 'recorded_request'
    elif 'seconds' in (config.get('budget') or {}):
        value = config['budget']['seconds']
        source = 'legacy_saved_task_budget'
    elif run.node_id:
        node_config, node_source = _node_config_at_revision(s, run)
        if node_config is not None:
            value = node_config.get('timeout', (node_config.get('budget') or {}).get('seconds'))
            source = node_source
        else:
            value = None
            source = 'legacy_timeout_not_recoverable'
    else:
        value = None
        source = 'legacy_timeout_not_recoverable'
    if value is None:
        return None, source
    try:
        value = float(value)
    except (TypeError, ValueError):
        error('INVALID_TIMEOUT', 'Task timeout must be positive or omitted', 422)
    if value <= 0:
        error('INVALID_TIMEOUT', 'Task timeout must be positive or omitted', 422)
    return value, source


def _bounded_timeout(requested_timeout, project_limit):
    if project_limit is None:
        return requested_timeout
    if requested_timeout is None:
        return project_limit
    return min(requested_timeout, project_limit)


def finalize_cancelled_run_elapsed(run):
    """Persist all project time consumed before a run releases its reservation."""
    resource = dict(run.resource or {})
    try:
        elapsed = max(0.0, float(resource.get('elapsed_seconds', 0) or 0))
    except (TypeError, ValueError):
        elapsed = 0.0
    current = datetime.now(timezone.utc)
    live_attempt = (run.status in ('running', 'pausing', 'paused') or
                    (run.status == 'waiting_input' and resource.get('branch_scheduling_hold') and run.started_at) or
                    (run.status == 'queued' and (
                        resource.get('container_reconnect_pending_dispatch') or
                        resource.get('live_process_pending_dispatch'))))
    if live_attempt and run.started_at:
        try:
            started = datetime.fromisoformat(run.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            before = max(0.0, float(resource.get('elapsed_before_attempt', elapsed) or 0))
            elapsed = max(elapsed, before + max(0.0, (current - started).total_seconds()))
        except (TypeError, ValueError):
            pass
    elif run.status in ('waiting', 'budget_exhausted') and resource.get('waiting_started_at') is not None:
        try:
            before = max(0.0, float(resource.get('elapsed_before_wait', elapsed) or 0))
            waiting_since = float(resource['waiting_started_at'])
            elapsed = max(elapsed, before + max(0.0, current.timestamp() - waiting_since))
        except (TypeError, ValueError):
            pass
    run.resource = {**resource, 'elapsed_seconds': elapsed}
    return elapsed


def freeze_budget_exhausted_elapsed(run, current=None):
    """Stop charging a budget-exhausted run after its awaited child has stopped."""
    resource = dict(run.resource or {})
    try:
        elapsed = max(0.0, float(resource.get('elapsed_seconds', 0) or 0))
    except (TypeError, ValueError):
        elapsed = 0.0
    waiting_started_at = resource.get('waiting_started_at')
    if waiting_started_at is not None:
        try:
            before = max(0.0, float(resource.get('elapsed_before_wait', elapsed) or 0))
            current = current or datetime.now(timezone.utc)
            elapsed = max(elapsed, before + max(0.0, current.timestamp() - float(waiting_started_at)))
        except (TypeError, ValueError):
            pass
    resource.pop('waiting_started_at', None)
    resource.pop('elapsed_before_wait', None)
    run.resource = {**resource, 'elapsed_seconds': elapsed}
    return elapsed


def _elapsed_seconds_at(run, current=None):
    """Include elapsed time since the last worker checkpoint for active work."""
    resource = run.resource or {}
    try:
        elapsed = max(0.0, float(resource.get('elapsed_seconds', 0) or 0))
    except (TypeError, ValueError):
        elapsed = 0.0
    current = current or datetime.now(timezone.utc)

    if (run.status in ('running', 'pausing') or
            (run.status == 'waiting_input' and resource.get('branch_scheduling_hold') and run.started_at) or
            (run.status == 'paused' and (run.pid is not None or resource.get('paused_live_attempt'))) or
            (run.status == 'queued' and (
                resource.get('container_reconnect_pending_dispatch') or
                resource.get('live_process_pending_dispatch')))):
        if run.started_at:
            try:
                started = datetime.fromisoformat(run.started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                before = max(0.0, float(resource.get('elapsed_before_attempt', elapsed) or 0))
                elapsed = max(elapsed, before + max(0.0, (current - started).total_seconds()))
            except (TypeError, ValueError):
                pass
    elif run.status in ('waiting', 'budget_exhausted') and resource.get('waiting_started_at') is not None:
        try:
            before = max(0.0, float(resource.get('elapsed_before_wait', elapsed) or 0))
            waiting_since = float(resource['waiting_started_at'])
            elapsed = max(elapsed, before + max(0.0, current.timestamp() - waiting_since))
        except (TypeError, ValueError):
            pass
    return elapsed


def _time_budget_reservation_remaining(run, current=None):
    """Return the unconsumed project-time allowance held by a live run.

    TaskRun.config.timeout is the legacy effective per-run cap. New rows also
    persist that cap under resource.time_budget so callers that later preserve
    the original task timeout can distinguish it from the project reservation.
    """
    if run.status not in TIME_BUDGET_RESERVED_STATUSES:
        return 0.0
    resource = run.resource or {}
    policy = resource.get('time_budget') or {}
    if policy.get('reservation_state') == 'deferred':
        return 0.0
    effective = policy.get('effective_total_timeout_seconds')
    if effective is None:
        effective = (run.config or {}).get('timeout')
    if effective is None:
        return 0.0
    try:
        effective = float(effective)
        elapsed = _elapsed_seconds_at(run, current)
    except (TypeError, ValueError):
        # Malformed legacy metadata must not make the scheduler unavailable.
        return 0.0
    return max(0.0, effective - elapsed)


def reserve_project_time(s, project, run):
    """Recalculate a queued/resumed run's total cap against current reservations."""
    budget = project.budget or {}
    cap = budget.get('seconds')
    current = datetime.now(timezone.utc)
    if cap is None:
        resource = {**(run.resource or {}), 'elapsed_seconds': _elapsed_seconds_at(run, current)}
        policy = dict(resource.get('time_budget') or {})
        if 'requested_task_timeout_seconds' in policy:
            requested = policy['requested_task_timeout_seconds']
            run.config = {**(run.config or {}), 'timeout': requested}
            policy.update(effective_total_timeout_seconds=requested,
                          reservation_state='held' if requested is not None else 'unrestricted')
            resource['time_budget'] = policy
        run.resource = resource
        return True, 0.0
    cap = float(cap)
    runs = list(s.scalars(select(TaskRun).where(TaskRun.project_id == project.id)))
    used = sum(_elapsed_seconds_at(item, current) for item in runs)
    reserved = sum(_time_budget_reservation_remaining(item, current) for item in runs if item.id != run.id)
    remaining = max(0.0, cap - used - reserved)
    resource = {**(run.resource or {}), 'elapsed_seconds': _elapsed_seconds_at(run, current)}
    policy = dict(resource.get('time_budget') or {})
    elapsed = resource['elapsed_seconds']
    if 'requested_task_timeout_seconds' in policy:
        requested = policy['requested_task_timeout_seconds']
    else:
        requested = (run.config or {}).get('timeout')
        policy['requested_task_timeout_seconds'] = requested
    requested_remaining = None
    if requested is not None:
        requested_remaining = max(0.0, float(requested) - elapsed)
    allowance = remaining if requested_remaining is None else min(remaining, requested_remaining)
    if allowance <= 0:
        policy.update(effective_total_timeout_seconds=elapsed, reservation_state='deferred',
                      project_budget_seconds_at_enqueue=cap, project_revision_at_enqueue=project.revision)
        run.resource = {**resource, 'time_budget': policy}
        return False, reserved
    effective_total = elapsed + allowance
    run.config = {**(run.config or {}), 'timeout': effective_total}
    policy.update(effective_total_timeout_seconds=effective_total, reservation_state='held',
                  project_budget_seconds_at_enqueue=cap, project_revision_at_enqueue=project.revision)
    run.resource = {**resource, 'time_budget': policy}
    return True, reserved


def _lock_project(s, project_id):
    if s.bind.dialect.name == 'sqlite':
        connection = s.connection()
        # Already-held graph-command transactions are reused. SQLite requires
        # a real writer lock because SELECT FOR UPDATE is ignored there.
        if not connection.connection.driver_connection.in_transaction:
            # Session.execute can autoflush a pending Project/PaperDocument
            # immediately before this statement, opening a SQLite transaction
            # after the check above. Acquire the writer transaction directly
            # on this connection before ORM autoflush is allowed to run.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
    p = s.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if p is None:
        error('NOT_FOUND', 'Project does not exist', 404)
    return p


def enqueue(s, project_id, kind, config, request_id=None, node=None, dependencies=None,
            requested_timeout_override=_TIMEOUT_UNSET, defer_time_budget_reservation=False, node_revision=None):
    retry_snapshot = node_revision is not None
    provider_origin='run' if config.get('provider_id') else 'node' if node and node.config.get('provider_id') else None
    p = _lock_project(s, project_id)
    if node is not None:
        # An edit may have committed while this session waited for the project.
        # Check promotion against the current row, not the pre-lock identity map.
        s.refresh(node)
    request_id = request_id or uid()
    old = s.scalar(select(TaskRun).where(TaskRun.project_id == project_id, TaskRun.request_id == request_id))
    if old:
        return old
    budget = p.budget or {}
    total = s.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.project_id == project_id)) or 0
    if budget.get('max_runs') is not None and total >= int(budget['max_runs']):
        error('RUN_BUDGET_EXHAUSTED', 'Project run budget exhausted', 409, 'Increase budget or choose a narrower next experiment.')
    dependencies = dependencies or []
    config = deepcopy(config) if retry_snapshot else {**(node.config if node else {}), **config}
    from research.agents.tool_policy import validate_permissions
    try: validate_permissions(config)
    except ValueError as exc: error('INVALID_TOOL_POLICY', str(exc), 422)
    if '_repository_source' in config:
        error('INVALID_CONFIGURATION', 'Source provenance is managed by the runner', 422)
    from services.api.verification import prepare_verification_enqueue
    config, dependencies = prepare_verification_enqueue(s, p, kind, config, node, dependencies)
    pending_dependency = any((dependency := s.get(TaskRun, ident)) is None or dependency.status != 'completed'
                             for ident in dependencies)
    deferred_reservation = bool(defer_time_budget_reservation or pending_dependency)
    runs = list(s.scalars(select(TaskRun).where(TaskRun.project_id == project_id)))
    current = datetime.now(timezone.utc)
    used = sum(_elapsed_seconds_at(r, current) for r in runs)
    remaining = None
    if budget.get('seconds') is not None:
        reserved = sum(_time_budget_reservation_remaining(r, current) for r in runs)
        remaining = max(0.0, float(budget['seconds']) - used - reserved)
        if remaining <= 0 and not deferred_reservation:
            error('TIME_BUDGET_EXHAUSTED', 'Project compute budget exhausted', 409)
    if 'repository' in config or kind == 'repository_clone':
        from research.execution.repository import normalize_repository, RepositoryError
        if kind not in ('agent', 'command', 'experiment', 'repository_clone'):
            error('INVALID_REPOSITORY', 'Repository inputs are supported by agent, command and experiment tasks', 422)
        try:
            config['repository'] = normalize_repository(config.get('repository'))
        except RepositoryError as exc:
            error(exc.code, str(exc), 422)
    from research.execution.recovery import resource_request
    try: resource_request(config)
    except (ValueError,TypeError) as exc: error('INVALID_RESOURCES',str(exc),422)
    requested_timeout = (config.get('timeout', (config.get('budget') or {}).get('seconds'))
                         if requested_timeout_override is _TIMEOUT_UNSET else requested_timeout_override)
    import math
    if requested_timeout is not None and (type(requested_timeout) not in (int,float) or not math.isfinite(requested_timeout) or requested_timeout<=0):
        error('INVALID_TIMEOUT','Task timeout must be a finite positive number or null',422)
    requested_timeout = float(requested_timeout) if requested_timeout is not None else None
    if requested_timeout is not None and requested_timeout <= 0:
        error('INVALID_TIMEOUT', 'Task timeout must be positive or omitted', 422)
    timeout = requested_timeout if deferred_reservation else _bounded_timeout(requested_timeout, remaining)
    merged = {**config, 'timeout': timeout, 'project_goal': p.goal, 'allow_paid': bool(budget.get('allow_paid', False))}
    from services.interventions.applicability import goal_scope_snapshot
    merged['goal_scope'] = goal_scope_snapshot(s, p, node.id if node else None)
    from research.publication.profile import publication_profile
    try:
        merged['publication_profile'] = publication_profile({**p.config, **config})
    except (ValueError, TypeError) as exc:
        error('INVALID_PUBLICATION_PROFILE', str(exc), 422)
    if kind == 'paper_generate':
        merged.setdefault('manuscript_type', 'full_paper')
    # Attempts belong to one run. A user retry creates a fresh run.
    for key in ('execution_attempt','_recovery_failures','_next_attempt'):
        merged.pop(key, None)
    if node and not retry_snapshot:
        merged.update(instructions=node.instructions, node_type=node.type, node_title=node.title,
                      context_overrides=deepcopy(node.context_overrides), input_references=deepcopy(node.inputs))
    role = merged.get('role', 'Researcher')
    agent = (s.get(Agent, merged['agent_id']) if merged.get('agent_id') else
             s.scalar(select(Agent).where(Agent.role == role)))
    provider_id = merged.get('provider_id') or (agent.provider_id if agent else None) or p.config.get('provider_id')
    provider_origin=provider_origin or ('agent' if agent and agent.provider_id else 'project' if p.config.get('provider_id') else None)
    if retry_snapshot and merged.get('provider_snapshot'):
        provider_id=merged['provider_snapshot']['id'];provider_origin='historical_run'
    if not provider_id:
        preference=s.get(Preference,'settings')
        provider_id=preference.value.get('default_provider_id') if preference else None
        provider_origin='global' if provider_id else 'first_available'
    provider = s.get(Provider, provider_id) if provider_id else s.scalar(select(Provider).order_by(Provider.created_at))
    if provider_id and not provider: error('PROVIDER_NOT_FOUND','Selected model provider no longer exists',422)
    if provider:
        if not (retry_snapshot and merged.get('provider_snapshot')):
            merged['provider_snapshot'] = asdict(provider, secrets=True)
        merged['provider_selection']={'provider_id':provider.id,'source':provider_origin,'agent_id':agent.id if agent else None}

    if not retry_snapshot and (kind == 'paper_generate' or (kind == 'paper_compile' and merged.get('source_scope') == 'workspace')):
        from services.api.paper_state import generation_snapshot
        # Server-owned snapshot: callers cannot opt out of revision protection.
        merged['paper_snapshot'] = generation_snapshot(s, project_id)
    time_budget = {
        'requested_task_timeout_seconds': requested_timeout,
        'effective_total_timeout_seconds': None if deferred_reservation else timeout,
        'project_budget_seconds_at_enqueue': budget.get('seconds'),
        'project_revision_at_enqueue': p.revision,
        'reservation_state': 'deferred' if deferred_reservation else 'held',
        'resume_history': [],
    }
    run_revision = node_revision if node_revision is not None else node.revision if node else 0
    run = TaskRun(project_id=project_id, node_id=node.id if node else None, branch_id=node.branch_id if node else merged.get('branch_id'),
                  request_id=request_id, kind=kind, config=merged, node_revision=run_revision,
                  dependencies=dependencies or [], priority=int(config.get('priority', 0)),
                  resource={'time_budget': time_budget,
                            'rerun_generation_at_enqueue': int(node.extra.get('rerun_generation', 0)) if node else 0})
    s.add(run)
    s.flush()
    run.resource = {**run.resource, 'verification_status': 'unverified'}
    if merged.get('provider_snapshot'):
        run.config = {**merged,'provider_snapshot':{**merged['provider_snapshot'],'_usage_context':{'project_id':project_id,'run_id':run.id}}}
    run.output_path = f'runs/{run.id}'
    # A retry may replay an older immutable config snapshot. Keep it in run
    # history, but don't make it the current result or execution state for a
    # node that has since moved to another revision.
    if node and node.revision == run_revision:
        node.execution_status = 'queued'
        node.extra = {**node.extra, 'latest_run_id': run.id, 'verification_status': 'unverified'}
    emit(s, project_id, 'run_queued', {'run_id': run.id, 'node_id': run.node_id})
    return run


def _validate_inputs(s, node, graph, selected):
    from research.kernel import ArtifactResolver
    resolver = ArtifactResolver(project_dir(node.project_id), graph)
    for ref in node.inputs:
        if not isinstance(ref, dict):
            # Plain strings are file references, not silently assumed inputs.
            ref = {'path': ref}
        if ref.get('project_id') not in (None, node.project_id):
            error('CROSS_PROJECT', 'An input reference belongs to another project', 422)
        if ref.get('missing') or ref.get('available') is False or ref.get('needs_rebinding'):
            error('INPUT_UNAVAILABLE', f'{node.title} has an unresolved input binding', 409,
                  'Rebind the marked input or remove it before running this node.')
        if ref.get('node_id'):
            parent = s.get(Node, ref['node_id'])
            if not parent or parent.project_id != node.project_id:
                error('INPUT_UNAVAILABLE', 'A referenced input node was deleted or belongs to another project', 409)
            if parent.id in selected and ref.get('path'):
                # This file is produced by a selected prerequisite. Its actual
                # existence is checked when its consumer is dispatched.
                continue
        if ref.get('path') or ref.get('node_id'):
            actual = resolver.resolve(ref, node.branch_id)
            if (not actual.get('available') and actual.get('error') == 'missing_file' and ref.get('node_id')
                    and ref.get('path') and not actual.get('stale') and parent.extra.get('results_current') is not False):
                # Producer outputs remain in their run workspace until an owner
                # explicitly promotes them. A current completed run is a valid
                # dependency for a later single-node consumer run too.
                previous = s.scalar(select(TaskRun).where(TaskRun.node_id == parent.id, TaskRun.status == 'completed', TaskRun.node_revision == parent.revision)
                                    .order_by(TaskRun.created_at.desc()))
                if previous and previous.node_revision == parent.revision:
                    from research.kernel import safe_path
                    root = project_dir(node.project_id)
                    output_workspace = safe_path(root, previous.output_path + '/workspace')
                    candidate = safe_path(output_workspace, ref['path'])
                    if candidate.is_file():
                        actual = {**actual, 'available': True, 'path': str(candidate)}
            if not actual.get('available') or actual.get('stale'):
                error('INPUT_UNAVAILABLE', actual.get('message', 'A referenced input is unavailable or has changed'), 409,
                      'Restore the material, update its reference, or run the producing steps.')
        elif ref.get('kind') in ('idea', 'paper', 'dataset', 'run', 'figure', 'analysis') and ref.get('id'):
            model = {'idea': Hypothesis, 'paper': SourcePaper, 'dataset': DatasetAsset, 'run': TaskRun, 'figure': Figure, 'analysis': Analysis}[ref['kind']]
            record = s.get(model, ref['id'])
            if record is None or record.project_id != node.project_id:
                error('INPUT_UNAVAILABLE', 'A referenced research record is missing or belongs to another project', 409)


def enqueue_selected(s, node_ids, request_id=None, overrides=None):
    """Enqueue exactly this selection in topological order, with real run links.

    Existing current completed runs can satisfy prerequisites outside selection.
    Request IDs identify each selected node consistently across repeated calls.
    """
    from research.kernel import topological_order
    from research.kernel.graph import execution_edges
    selected = set(node_ids)
    if not selected:
        return []
    first = get(s, Node, next(iter(selected)))
    p = _lock_project(s, first.project_id)
    # Pre-lock identities may precede an owner edit. Refresh before choosing
    # the execution kind and checking the graph, not only inside enqueue().
    graph = graph_from_db(s, p, refresh=True)
    nodes = {n['id']: n for n in graph['nodes']}
    if not selected <= nodes.keys():
        error('CROSS_PROJECT', 'All selected nodes must belong to the same project', 422)
    branches = {b['id']: b for b in graph['branches']}
    edges = execution_edges(graph)
    execution_graph = {**graph, 'edges': edges}
    ordered = topological_order(execution_graph, selected)
    created = {}
    request_id = request_id or uid()
    for nid in ordered:
        n = get(s, Node, nid)
        existing = s.scalar(select(TaskRun).where(TaskRun.project_id == p.id, TaskRun.request_id == f'{request_id}:{nid}'))
        if existing:
            created[nid] = existing
            continue
        if n.archived:
            error('NODE_ARCHIVED', f'{n.title} is archived', 409, 'Restore the node before scheduling it.')
        if branches.get(n.branch_id, {}).get('status', 'active') not in ('active', 'materializing'):
            error('BRANCH_INACTIVE', 'The selected branch has stopped exploring', 409, 'Restore this branch before scheduling new runs.')
        _validate_inputs(s, n, graph, selected)
        dependencies = []
        for edge in edges:
            if edge['target'] != nid:
                continue
            if edge['source'] in created:
                dependencies.append(created[edge['source']].id)
            else:
                parent = get(s, Node, edge['source'])
                previous = s.scalar(select(TaskRun).where(TaskRun.node_id == parent.id, TaskRun.status == 'completed', TaskRun.node_revision == parent.revision).order_by(TaskRun.created_at.desc(), TaskRun.id.desc()))
                if not previous or previous.node_revision != parent.revision or parent.extra.get('results_current') is False:
                    error('INPUT_UNAVAILABLE', f'Input node {parent.title} has no current completed run', 409,
                          'Run ancestors first or explicitly rebind this dependency.')
                dependencies.append(previous.id)
        cfg = dict(overrides or {})
        kind = cfg.get('kind') or n.config.get('kind')
        if not kind:
            kind = 'verification' if n.type == 'verification' else 'command' if n.config.get('command') else 'experiment' if n.type in ('experiment', 'baseline') else 'analysis' if n.type == 'analysis' else 'agent'
        created[nid] = enqueue(s, p.id, kind, cfg, f'{request_id}:{nid}', n,
                               list(dict.fromkeys(dependencies)), defer_time_budget_reservation=len(ordered) > 1 and bool(created))
    return list(created.values())


def enqueue_nodes(s, node_id, scope='single', request_id=None, overrides=None):
    from research.kernel.graph import execution_edges
    node = get(s, Node, node_id)
    p = _lock_project(s, node.project_id)
    graph = graph_from_db(s, p, refresh=True)
    nodes = {n['id']: n for n in graph['nodes'] if not n.get('archived')}
    edges = execution_edges(graph)
    selected = {node_id}
    direction = 'up' if scope in ('ancestors', 'to_here') else 'down'
    if scope != 'single':
        pending = [node_id]
        while pending:
            current = pending.pop()
            for edge in edges:
                other = edge['source'] if direction == 'up' and edge['target'] == current else edge['target'] if direction == 'down' and edge['source'] == current else None
                if other in nodes and other not in selected:
                    if scope != 'affected' or nodes[other].get('needs_rerun') or nodes[other].get('deliverable_status') == 'needs_update':
                        selected.add(other)
                        pending.append(other)
    return enqueue_selected(s, selected, request_id, overrides)
