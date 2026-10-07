"""Source-bound branch copies with durable receipts and atomic activation."""
from copy import deepcopy
import hashlib
import json
import os
import shutil
import time

from sqlalchemy import select
from research.kernel.artifacts import BranchWorkspace, _files
from research.kernel.errors import GraphError
from services.api.common import project_dir, safe_path, get, emit
from services.api.db import Session, Branch, now, uid
from services.worker.scheduler import _lock_project
from .models import Intervention, InterventionEffect


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''): result.update(block)
    return result.hexdigest()


def prepare(project_id, graph, action):
    """Bind intent to selected bytes before accepting the graph transaction."""
    root = project_dir(project_id)
    workspaces = BranchWorkspace(root, graph)
    for branch_id in ([next(n['branch_id'] for n in graph['nodes'] if n['id'] == action['node_id'])]
                      if action['action'] == 'fork' else [action['left'], action['right']]):
        branch = next(b for b in graph['branches'] if b['id'] == branch_id)
        if branch.get('status') == 'materializing' or branch.get('workspace_intervention'):
            raise GraphError('workspace_pending', 'Wait for the source branch workspace to finish materializing.', status_code=409)
    if action['action'] == 'fork':
        planned = workspaces.fork(action['node_id'], action.get('copy_policy'),
            branch_id=action['branch_id'], dry_run=True, capture_sources=True)
    else:
        planned = workspaces.merge(action['left'], action['right'], action.get('resolution'),
            branch_id=action['branch_id'], dry_run=True, capture_sources=True)
        if planned['conflicts']:
            raise GraphError('merge_conflict', 'Resolve file and configuration conflicts before merging.', status_code=409)
    files = planned.pop('_materialization')
    total = 0
    for item in files.values():
        if item['kind'] == 'copy':
            path = safe_path(root, item['source'], True)
            item.update(sha256=digest(path), size=path.stat().st_size)
        elif item['kind'] == 'content':
            content = item['content'].encode('utf-8')
            item.update(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
        total += item.get('size', 0)
    # Both forks and merges have an explicit bounded default, editable per intent.
    policy = action.get('copy_policy') or action.get('resolution') or {}
    maximum=policy.get('max_copy_bytes',100*1024*1024)
    if type(maximum) is not int or maximum<0:
        raise GraphError('copy_budget','max_copy_bytes must be a nonnegative integer.')
    if total > maximum:
        raise GraphError('copy_budget', 'Selected workspace bytes exceed max_copy_bytes.')
    return {'project_id': project_id, 'branch_id': action['branch_id'], 'files': files,
            'workspace': planned['workspace'], 'base_workspace': planned.get('base_workspace') if action['action'] == 'fork' else None,
            'action': action['action'], 'selected_bytes': total}


def record(session, parent, plans):
    for plan in plans:
        effect = InterventionEffect(intervention_id=parent.id, project_id=parent.project_id,
            run_id=plan['branch_id'], action='materialize_workspace', target=plan)
        session.add(effect); session.flush()
        branch = get(session, Branch, plan['branch_id'])
        branch.status = 'materializing'
        branch.extra = {**branch.extra, 'workspace_intervention': {
            'intervention_id': parent.id, 'effect_id': effect.id, 'status': 'pending'}}


def _verify(directory, files):
    expected = {rel: item for rel, item in files.items() if item['kind'] != 'delete'}
    observed = _files(directory)
    if observed.keys() != expected.keys() or any(
            observed[rel].stat().st_size != item['size'] or digest(observed[rel]) != item['sha256']
            for rel, item in expected.items()):
        raise GraphError('workspace_bytes_changed', 'Workspace bytes do not match the accepted source-bound copy.')


def materialize(effect_id, plan):
    import fcntl
    root = project_dir(plan['project_id'])
    control = safe_path(root, '.forest-interventions')
    control.mkdir(parents=True, exist_ok=True)
    receipt = control / (effect_id + '.json')
    with safe_path(control, 'branch-'+plan['branch_id']+'.lock').open('a') as lock:
        # A renewed DB lease cannot allow two filesystem writers for this effect.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if receipt.is_file():
            observed = json.loads(receipt.read_text())
            if observed.get('plan_sha256') != _plan_digest(plan):
                raise GraphError('workspace_receipt_conflict', 'Workspace receipt belongs to another copy intent.')
            for path in (plan.get('base_workspace'),plan['workspace']):
                if path: _verify(safe_path(root,path),plan['files'])
            return observed
        staging = control / (effect_id + '.staging')
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            adopted = next((safe_path(root, path) for path in (plan.get('base_workspace'), plan['workspace'])
                            if path and safe_path(root, path).exists()), None)
            if adopted is not None:
                _verify(adopted, plan['files'])
                shutil.copytree(adopted, staging, dirs_exist_ok=True)
            else:
                for relative, item in plan['files'].items():
                    if item['kind'] == 'delete': continue
                    target = safe_path(staging, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if item['kind'] == 'copy':
                        source = safe_path(root, item['source'], True)
                        if digest(source) != item['sha256']:
                            raise GraphError('workspace_source_changed', 'Selected source bytes changed after acceptance: '+item['source'])
                        shutil.copy2(source, target)
                        if digest(source) != item['sha256']:
                            raise GraphError('workspace_source_changed', 'Selected source changed during copy: '+item['source'])
                    else: target.write_text(item['content'], encoding='utf-8')
                    with target.open('rb') as handle: os.fsync(handle.fileno())
            _verify(staging, plan['files'])
            # A crash between the two renames is recovered using verified bytes;
            # scheduling remains fenced until the DB receives this receipt.
            for path in (plan.get('base_workspace'), plan['workspace']):
                if not path: continue
                destination = safe_path(root, path)
                if destination.exists():
                    _verify(destination, plan['files'])
                    continue
                pending = control / (effect_id + '.activate')
                shutil.rmtree(pending, ignore_errors=True)
                shutil.copytree(staging, pending)
                for file in _files(pending).values():
                    with file.open('rb') as handle: os.fsync(handle.fileno())
                for directory in sorted((p for p in pending.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True): _sync_directory(directory)
                _sync_directory(pending)
                destination.parent.mkdir(parents=True, exist_ok=True)
                pending.rename(destination)
                _sync_directory(destination.parent)
            observed = {'plan_sha256': _plan_digest(plan), 'workspace': plan['workspace'],
                'files': {rel: {key: item[key] for key in ('sha256', 'size') if key in item}
                          for rel, item in plan['files'].items() if item['kind'] != 'delete'},
                'selected_bytes': plan['selected_bytes'], 'observed_at': now(), 'state': 'materialized'}
            temporary = receipt.with_suffix('.tmp')
            with temporary.open('w') as handle:
                json.dump(observed, handle); handle.flush(); os.fsync(handle.fileno())
            temporary.replace(receipt)
            _sync_directory(control)
            return observed
        finally: shutil.rmtree(staging, ignore_errors=True)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reconcile(*, intervention_id=None, limit=1):
    from .application import summarize
    with Session() as session:
        query = select(InterventionEffect.id, InterventionEffect.project_id).where(
            InterventionEffect.action == 'materialize_workspace',
            InterventionEffect.status.in_(('pending', 'applying', 'uncertain')),
            InterventionEffect.lease_until <= time.time(), InterventionEffect.retry_after <= time.time())
        if intervention_id: query = query.where(InterventionEffect.intervention_id == intervention_id)
        candidates = session.execute(query.order_by(InterventionEffect.created_at).limit(limit)).all()
    for effect_id, project_id in candidates:
        owner = uid()
        with Session.begin() as session:
            _lock_project(session, project_id)
            effect = session.scalar(select(InterventionEffect).where(InterventionEffect.id == effect_id).with_for_update())
            if not effect or effect.status not in ('pending', 'applying', 'uncertain') or effect.lease_until > time.time(): continue
            branch = session.get(Branch, effect.run_id)
            if not branch or branch.extra.get('workspace_intervention', {}).get('effect_id') != effect.id:
                effect.status = 'superseded'; summarize(session, get(session, Intervention, effect.intervention_id)); continue
            effect.status = 'applying'; effect.lease_owner = owner; effect.lease_until = time.time()+120
            effect.attempts += 1; plan = deepcopy(effect.target)
        observed = {}; failure = None; permanent = False
        try: observed = materialize(effect_id, plan)
        except Exception as exc:
            failure = str(exc)[:1500]
            permanent = isinstance(exc, GraphError)
        with Session.begin() as session:
            _lock_project(session, project_id)
            effect = session.scalar(select(InterventionEffect).where(InterventionEffect.id == effect_id).with_for_update())
            if not effect or effect.lease_owner != owner: continue
            branch = session.get(Branch, effect.run_id)
            if not branch or branch.extra.get('workspace_intervention', {}).get('effect_id') != effect.id:
                effect.status = 'superseded'
            elif failure:
                effect.status = 'failed' if permanent else 'uncertain'; effect.error = failure
                effect.retry_after = time.time()+min(60, 2**min(effect.attempts, 6))
                branch.extra = {**branch.extra, 'workspace_intervention': {
                    **branch.extra['workspace_intervention'], 'status': effect.status, 'error': failure}}
            else:
                effect.status = 'applied'; effect.applied_at = now(); effect.observation = observed; effect.error = None
                branch.status = 'active' if branch.status == 'materializing' else branch.status
                branch.extra = {key: value for key, value in branch.extra.items() if key != 'workspace_intervention'}
                branch.extra = {**branch.extra, 'workspace_receipt': {'effect_id': effect.id, **observed}}
            effect.lease_owner = None; effect.lease_until = 0
            parent = get(session, Intervention, effect.intervention_id); summarize(session, parent)
            emit(session, project_id, 'intervention_changed', {'intervention_id': parent.id, 'status': parent.status})
            emit(session, project_id, 'node_changed', {'workspace_branch_id': effect.run_id})
