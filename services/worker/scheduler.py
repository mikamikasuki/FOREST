"""Database-backed scheduling with one dependency map per selected research DAG."""
from copy import deepcopy
from sqlalchemy import select, func, text as sql_text
from services.api.db import *
from services.api.common import get, error, project_dir, graph_from_db, emit

ACTIVE = ('queued', 'running', 'pausing', 'paused', 'waiting_input', 'waiting', 'budget_exhausted')
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted', 'skipped')


def _lock_project(s, project_id):
    if s.bind.dialect.name == 'sqlite':
        connection = s.connection()
        # Already-held graph-command transactions are reused. SQLite requires
        # a real writer lock because SELECT FOR UPDATE is ignored there.
        if not connection.connection.driver_connection.in_transaction:
            s.execute(sql_text('BEGIN IMMEDIATE'))
    p = s.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if p is None:
        error('NOT_FOUND', 'Project does not exist', 404)
    return p


def enqueue(s, project_id, kind, config, request_id=None, node=None, dependencies=None):
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
    from research.execution.recovery import resource_request
    try: resource_request(config)
    except (ValueError,TypeError) as exc: error('INVALID_RESOURCES',str(exc),422)
    requested_timeout = config.get('timeout', (config.get('budget') or {}).get('seconds'))
    timeout = float(requested_timeout) if requested_timeout is not None else None
    if timeout is not None and timeout <= 0:
        error('INVALID_TIMEOUT', 'Task timeout must be positive or omitted', 422)
    if remaining is not None:
        timeout = min(timeout, remaining) if timeout is not None else remaining
    merged = {**config, 'timeout': timeout, 'project_goal': p.goal, 'allow_paid': bool(budget.get('allow_paid', False))}
    # Attempts belong to one run. A user retry creates a fresh run.
    for key in ('execution_attempt','_recovery_failures','_next_attempt'):
        merged.pop(key, None)
    if node:
        merged.update(instructions=node.instructions, node_type=node.type, node_title=node.title,
                      context_overrides=deepcopy(node.context_overrides), input_references=deepcopy(node.inputs))
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
                  dependencies=dependencies or [], priority=int(config.get('priority', 0)))
    s.add(run)
    s.flush()
    if merged.get('provider_snapshot'):
        run.config = {**merged,'provider_snapshot':{**merged['provider_snapshot'],'_usage_context':{'project_id':project_id,'run_id':run.id}}}
    run.output_path = f'runs/{run.id}'
    if node:
        node.execution_status = 'queued'
        node.extra = {**node.extra, 'latest_run_id': run.id}
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
    edges = [e for e in graph['edges'] if e['relation'] in ('depends_on', 'consumes')]
    # Explicit node inputs are operational dependencies even when the canvas
    # omits an arrow. This prevents a consumer from racing its producer.
    for n in graph['nodes']:
        for ref in n.get('inputs', []):
            if isinstance(ref, dict) and ref.get('node_id') in nodes:
                pair = (ref['node_id'], n['id'])
                if not any((e['source'], e['target']) == pair for e in edges):
                    edges.append({'source': pair[0], 'target': pair[1], 'relation': 'consumes'})
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
            kind = 'command' if n.config.get('command') else 'experiment' if n.type in ('experiment', 'baseline') else 'analysis' if n.type == 'analysis' else 'agent'
        created[nid] = enqueue(s, p.id, kind, cfg, f'{request_id}:{nid}', n, list(dict.fromkeys(dependencies)))
    return list(created.values())


def enqueue_nodes(s, node_id, scope='single', request_id=None, overrides=None):
    node = get(s, Node, node_id)
    p = _lock_project(s, node.project_id)
    graph = graph_from_db(s, p)
    nodes = {n['id']: n for n in graph['nodes'] if not n.get('archived')}
    edges = [e for e in graph['edges'] if e['relation'] in ('depends_on', 'consumes')]
    for n in graph['nodes']:
        for ref in n.get('inputs', []):
            if isinstance(ref, dict) and ref.get('node_id') in nodes:
                edges.append({'source': ref['node_id'], 'target': n['id']})
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
