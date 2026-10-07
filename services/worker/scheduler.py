"""Database-backed scheduling with one dependency map per selected research DAG."""
from copy import deepcopy
from datetime import datetime
from sqlalchemy import select, func
from services.api.db import *
from services.api.common import get, error, project_dir, graph_from_db, emit

ACTIVE = ('queued', 'running', 'pausing', 'paused', 'waiting_input', 'waiting', 'budget_exhausted')
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted', 'skipped')
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
        snapshots = history.get(stack) or []
        for snapshot in reversed(snapshots):
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
            # Older rows stored the effective project-clipped timeout in config.
            # Without a node snapshot or separate request cap, it is ambiguous.
            value = None
            source = 'legacy_timeout_not_recoverable'
    else:
        # Non-node legacy rows have no graph history from which to recover the
        # caller's original timeout; use the current project allowance instead.
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
            requested_timeout_override=_TIMEOUT_UNSET):
    p = _lock_project(s, project_id)
    request_id = request_id or uid()
    old = s.scalar(select(TaskRun).where(TaskRun.project_id == project_id, TaskRun.request_id == request_id))
    if old:
        return old
    budget = p.budget or {}
    total = s.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.project_id == project_id)) or 0
    if budget.get('max_runs') is not None and total >= int(budget['max_runs']):
        error('RUN_BUDGET_EXHAUSTED', 'Project run budget exhausted', 409, 'Increase budget or choose a narrower next experiment.')
    runs = list(s.scalars(select(TaskRun).where(TaskRun.project_id == project_id)))
    used = sum(float(r.resource.get('elapsed_seconds', 0)) for r in runs)
    if budget.get('seconds') is not None and used >= float(budget['seconds']):
        error('TIME_BUDGET_EXHAUSTED', 'Project compute budget exhausted', 409)
    remaining = max(0, float(budget['seconds']) - used) if budget.get('seconds') is not None else None
    config = {**(node.config if node else {}), **config}
    if '_repository_source' in config:
        error('INVALID_CONFIGURATION', 'Source provenance is managed by the runner', 422)
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
    requested_timeout = float(requested_timeout) if requested_timeout is not None else None
    if requested_timeout is not None and requested_timeout <= 0:
        error('INVALID_TIMEOUT', 'Task timeout must be positive or omitted', 422)
    timeout = _bounded_timeout(requested_timeout, remaining)
    merged = {**config, 'timeout': timeout, 'project_goal': p.goal, 'allow_paid': bool(budget.get('allow_paid', False))}
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
    if node:
        merged.update(instructions=node.instructions, node_type=node.type, node_title=node.title,
                      context_overrides=deepcopy(node.context_overrides), input_references=deepcopy(node.inputs))
    from services.api.verification import prepare_verification_enqueue
    merged, dependencies = prepare_verification_enqueue(s, p, kind, merged, node, dependencies)
    provider_id = merged.get('provider_id') or p.config.get('provider_id')
    if not provider_id:
        preference=s.get(Preference,'settings')
        provider_id=preference.value.get('default_provider_id') if preference else None
    provider = s.get(Provider, provider_id) if provider_id else s.scalar(select(Provider).order_by(Provider.created_at))
    if provider:
        merged['provider_snapshot'] = asdict(provider, secrets=True)
    if kind == 'paper_generate' or (kind == 'paper_compile' and merged.get('source_scope') == 'workspace'):
        from services.api.paper_state import generation_snapshot
        # Server-owned snapshot: callers cannot opt out of revision protection.
        merged['paper_snapshot'] = generation_snapshot(s, project_id)
    run = TaskRun(project_id=project_id, node_id=node.id if node else None, branch_id=node.branch_id if node else merged.get('branch_id'),
                  request_id=request_id, kind=kind, config=merged, node_revision=node.revision if node else 0,
                  dependencies=dependencies or [], priority=int(config.get('priority', 0)),
                  resource={'time_budget': {
                      'requested_task_timeout_seconds': requested_timeout,
                      'effective_total_timeout_seconds': timeout,
                      'project_budget_seconds_at_enqueue': budget.get('seconds'),
                      'project_revision_at_enqueue': p.revision,
                      'resume_history': [],
                  }, 'rerun_generation_at_enqueue': int(node.extra.get('rerun_generation', 0)) if node else 0})
    s.add(run)
    s.flush()
    run.resource = {**run.resource, 'verification_status': 'unverified'}
    if merged.get('provider_snapshot'):
        run.config = {**merged,'provider_snapshot':{**merged['provider_snapshot'],'_usage_context':{'project_id':project_id,'run_id':run.id}}}
    run.output_path = f'runs/{run.id}'
    if node:
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
                previous = s.scalar(select(TaskRun).where(TaskRun.node_id == parent.id, TaskRun.status == 'completed')
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
    graph = graph_from_db(s, p)
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
        if branches.get(n.branch_id, {}).get('status', 'active') != 'active':
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
                previous = s.scalar(select(TaskRun).where(TaskRun.node_id == parent.id, TaskRun.status == 'completed').order_by(TaskRun.created_at.desc()))
                if not previous or previous.node_revision != parent.revision or parent.extra.get('results_current') is False:
                    error('INPUT_UNAVAILABLE', f'Input node {parent.title} has no current completed run', 409,
                          'Run ancestors first or explicitly rebind this dependency.')
                dependencies.append(previous.id)
        cfg = dict(overrides or {})
        kind = cfg.get('kind') or n.config.get('kind')
        if not kind:
            kind = 'verification' if n.type == 'verification' else 'command' if n.config.get('command') else 'experiment' if n.type in ('experiment', 'baseline') else 'analysis' if n.type == 'analysis' else 'agent'
        created[nid] = enqueue(s, p.id, kind, cfg, f'{request_id}:{nid}', n, list(dict.fromkeys(dependencies)))
    return list(created.values())


def enqueue_nodes(s, node_id, scope='single', request_id=None, overrides=None):
    from research.kernel.graph import execution_edges
    node = get(s, Node, node_id)
    p = _lock_project(s, node.project_id)
    graph = graph_from_db(s, p)
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
