"""Separate goal relevance from source-bound computational verification."""
from sqlalchemy import select
from services.api.db import Project, TaskRun, Session, now
from services.api.common import get, error, emit
from services.worker.scheduler import _lock_project
from .models import Intervention
from .application import readback


def goal_scope_snapshot(session, project, node_id):
    """Record the relevant editable direction separately from numerical inputs."""
    if not node_id: return {}
    from services.api.common import graph_from_db
    from research.kernel.graph import _closure, PROPAGATION
    cache = session.info.setdefault('goal_scope_snapshots', {})
    key = (project.id, project.revision)
    if key not in cache: cache[key] = {'graph': graph_from_db(session, project), 'nodes': {}}
    if node_id in cache[key]['nodes']: return cache[key]['nodes'][node_id]
    graph = cache[key]['graph']
    relevant = _closure(graph, [node_id], reverse=True, relations=PROPAGATION)
    scope = {}
    for node in graph['nodes']:
        if node['id'] not in relevant: continue
        if node.get('type') in ('goal', 'research_goal'):
            scope[node['id']] = {key: node.get(key) for key in ('instructions', 'context_overrides')}
        elif node['id'] == node_id and node.get('context_overrides'):
            scope[node['id']] = {'context_overrides': node['context_overrides']}
    cache[key]['nodes'][node_id] = scope
    return scope


def goal_applicability(session, run):
    project = get(session, Project, run.project_id)
    # Agent execution can cross several live goal edits. Preserve that fact
    # instead of stamping all its computations with the final request's goal.
    history = run.metrics.get('execution_context', {}).get('history', [])
    goals = list(dict.fromkeys(entry['goal'] for entry in history if isinstance(entry.get('goal'), str)))
    if not goals and isinstance(run.config.get('project_goal'), str): goals = [run.config['project_goal']]
    current_scope = goal_scope_snapshot(session, project, run.node_id)
    recorded_scopes = [entry['goal_scope'] for entry in history if isinstance(entry.get('goal_scope'), dict)]
    if not recorded_scopes and isinstance(run.config.get('goal_scope'), dict): recorded_scopes = [run.config['goal_scope']]
    scope_matches = all(scope == current_scope for scope in recorded_scopes) if recorded_scopes else not current_scope
    state = 'current' if goals == [project.goal] else 'needs_review' if goals else 'unknown'
    if state == 'current' and not scope_matches: state = 'needs_review'
    reason = 'Execution used the current goal and scoped direction' if state == 'current' else 'Execution goal or scoped direction changed, or spans different goals' if goals else 'Legacy execution has no recorded goal'
    decision = None
    cache=session.info.setdefault('goal_applicability_decisions',{})
    cache_key=(project.id,project.revision,project.goal)
    if cache_key not in cache:
        by_run={}
        for row in session.scalars(select(Intervention).where(Intervention.project_id == project.id,
                Intervention.kind == 'evidence_applicability',Intervention.status=='applied',Intervention.intent['goal'].as_string()==project.goal)
                .order_by(Intervention.created_at.desc(),Intervention.id.desc())):
            by_run.setdefault(row.intent.get('run_id'), []).append(row)
        cache[cache_key]=by_run
    row=next((item for item in cache[cache_key].get(run.id, [])
              if item.intent.get('goal_scope', {}) == current_scope), None)
    if row:
        decision = row.id
        state = 'approved' if row.intent['choice'] == 'reuse' else 'excluded'
        reason = row.intent['reason']
    return {'run_id': run.id, 'status': state, 'ready': state in ('current', 'approved'),
            'current_goal': project.goal, 'execution_goals': goals, 'decision_id': decision,
            'current_goal_scope': current_scope, 'execution_goal_scopes': recorded_scopes,
            'reason': reason, 'scope': 'Goal relevance only; computational verification remains independent'}


def decide_applicability(run_id, request_id, expected_revision, choice, reason):
    if choice not in ('reuse', 'exclude') or not str(reason).strip():
        error('INVALID_DECISION', 'Choose reuse or exclude and record the scientific reason', 422)
    with Session() as reader: project_id = get(reader, TaskRun, run_id).project_id
    with Session.begin() as session:
        project = _lock_project(session, project_id)
        run = get(session, TaskRun, run_id, for_update=True)
        old = session.scalar(select(Intervention).where(Intervention.project_id == project_id, Intervention.request_id == request_id))
        if old:
            if any(old.intent.get(k) != v for k, v in {'run_id': run_id, 'choice': choice, 'reason': reason}.items()):
                error('REQUEST_ID_CONFLICT', 'This request identity belongs to a different decision', 409)
            return readback(session, old)
        if project.revision != expected_revision: error('REVISION_CONFLICT', 'The reviewed project changed', 409)
        if run.status != 'completed': error('EVIDENCE_UNFINISHED', 'Decide applicability after execution completes', 409)
        project.revision += 1
        row = Intervention(project_id=project_id, request_id=request_id, actor='owner', kind='evidence_applicability',
            observed_revision=expected_revision, applied_revision=project.revision, status='applied',
            intent={'run_id': run_id, 'goal': project.goal, 'goal_scope': goal_scope_snapshot(session, project, run.node_id),
                    'choice': choice, 'reason': reason},
            impact={'run_id': run_id, 'computational_verification': 'preserved'}, accepted_at=now(), applied_at=now())
        session.add(row); session.flush()
        emit(session, project_id, 'intervention_changed', {'intervention_id': row.id, 'status': 'applied'})
        emit(session, project_id, 'project_changed', {'revision': project.revision})
        return readback(session, row)
