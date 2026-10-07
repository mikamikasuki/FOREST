"""Source-bound verification of actual files, separate from task completion.

Bindings and execution observations belong to the service. Editable artifacts
are reread on every admission; a saved or model-authored pass is never evidence.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import select

from services.api.common import error, get, project_dir, safe_path
from services.api.db import Node, Project, Session, TaskRun, asdict, now

router = APIRouter()
RESERVED_CONFIG = {'_verification_binding', '_verification_contract', '_verification_sources',
                   'verification_result', 'verification_status'}


def _contract(config):
    from research.validation.handoff import validate_contract
    return validate_contract(config.get('verification'))


def latest_node_run(session, node):
    """A newer queued or failed attempt supersedes older successful evidence."""
    return session.scalar(select(TaskRun).where(TaskRun.node_id == node.id)
                          .order_by(TaskRun.created_at.desc(), TaskRun.id.desc()).limit(1))


def _required_ids(config, inputs=()):
    identifiers = config.get('required_verification', [])
    if not isinstance(identifiers, list) or any(not isinstance(v, str) or not v for v in identifiers):
        raise ValueError('required_verification must contain verifier node IDs')
    identifiers = list(identifiers)
    for reference in inputs:
        if isinstance(reference, dict) and reference.get('verification_node_id'):
            identifiers.append(reference['verification_node_id'])
    return list(dict.fromkeys(identifiers))


def required_policy(session, run):
    project = get(session, Project, run.project_id)
    node = session.get(Node, run.node_id) if run.node_id else None
    policy = (node.config.get('verification_policy') if node else None)
    if policy is None:
        policy = run.config.get('verification_policy', project.config.get('verification_policy', 'optional'))
    return policy == 'required'


def prepare_verification_enqueue(session, project, kind, config, node, dependencies):
    """Validate editable intent and link actual source/verifier runs in the queue."""
    if RESERVED_CONFIG & config.keys():
        error('INVALID_CONFIGURATION', 'Verification identities and verdicts are managed by the service', 422)
    policy = config.get('verification_policy', project.config.get('verification_policy', 'optional'))
    if policy not in ('optional', 'required'):
        error('INVALID_VERIFICATION_POLICY', 'verification_policy must be optional or required', 422)
    config = deepcopy(config)
    dependencies = list(dependencies or [])
    try:
        identifiers = _required_ids(config, config.get('input_references', node.inputs if node else []))
        if kind == 'verification':
            contract = _contract(config)
            producer = get(session, Node, contract['producer_node_id'])
            if producer.project_id != project.id or producer.archived:
                raise ValueError('Verification source must be an active node in this project')
            if producer.id == (node.id if node else None) or producer.config.get('kind') == 'verification':
                raise ValueError('A verifier must check a distinct producer node')
            config['verification'] = contract
            source = latest_node_run(session, producer)
            if source is None:
                error('INPUT_UNAVAILABLE', 'Run the verification source or select its ancestors first', 409)
            dependencies.append(source.id)
        for identifier in identifiers:
            verifier = get(session, Node, identifier)
            if verifier.project_id != project.id or verifier.archived or verifier.config.get('kind') != 'verification':
                raise ValueError('Required verifier must be an active verification node in this project')
            _contract(verifier.config)
            latest = latest_node_run(session, verifier)
            if latest is not None:
                dependencies.append(latest.id)
        # Manuscript/API tasks also bind evidence when no graph node is involved.
        for identifier in config.get('run_ids', []) or []:
            source = get(session, TaskRun, identifier)
            if source.project_id != project.id:
                raise ValueError('Evidence runs must belong to this project')
            if policy == 'required' or identifiers:
                dependencies.append(source.id)
    except (ValueError, TypeError, KeyError) as exc:
        error('INVALID_VERIFICATION', str(exc), 422)
    for identifier in dependencies:
        if get(session, TaskRun, identifier).project_id != project.id:
            error('CROSS_PROJECT', 'Verification dependencies must belong to this project', 422)
    return config, list(dict.fromkeys(dependencies))


def _artifacts(contract):
    """Exact checked file scopes; an integrity check does not verify other files."""
    paths = []
    for check in contract['checks']:
        workspace = check.get('workspace', 'producer')
        if check.get('source'):
            paths.append((workspace, check['source']))
        if check.get('results'):
            paths.append(('producer', check['results']))
        if check.get('repeat'):
            paths.append(('verifier', check['repeat']))
    return list(dict.fromkeys(paths))


def _workspace(run):
    return safe_path(project_dir(run.project_id), run.output_path + '/workspace')


def _producer_workspace(run, binding):
    return (safe_path(project_dir(run.project_id),run.output_path)
            if binding.get('producer_artifact_scope')=='run' else _workspace(run))


def _producer_scope(run, contract):
    """Bind the actual existing artifact root once, including built-in run outputs."""
    paths=[path for scope,path in _artifacts(contract) if scope=='producer']
    workspace=_workspace(run)
    folder=safe_path(project_dir(run.project_id),run.output_path)
    if paths and not all(safe_path(workspace,path).is_file() for path in paths) and all(safe_path(folder,path).is_file() for path in paths):
        return 'run'
    return 'workspace'


def _observations(contract, producer, verifier):
    result = []
    for scope, relative in _artifacts(contract):
        root = producer if scope == 'producer' else verifier
        try:
            path = safe_path(root, relative, True)
            if path.is_symlink() or not path.is_file():
                raise OSError('Not a regular artifact')
            stat = path.stat()
            value = {'workspace': scope, 'path': relative, 'size': stat.st_size,
                     'mtime_ns': stat.st_mtime_ns, 'inode': stat.st_ino}
        except Exception:
            # No contents, host paths, or exception text enter public provenance.
            value = {'workspace': scope, 'path': relative, 'missing': True}
        result.append(value)
    return result


def _execution_spec(config):
    # Binding metadata is visible in existing owner APIs; never duplicate
    # provider keys, environment values or SSH credential configuration here.
    remote = config.get('remote') or {}
    container = config.get('container') or {}
    return {'command': deepcopy(config.get('command')), 'execution_backend': config.get('execution_backend', 'local'),
            'remote': {key: remote.get(key) for key in ('hostname', 'username', 'port', 'workdir')} if remote else None,
            'container': {key: container.get(key) for key in ('image', 'cpus', 'memory', 'network', 'gpus', 'pids_limit')} if container else None}


def _base_verdict(run, status='inconclusive', reason='Verification has not completed'):
    return {'run_id': run.id, 'verification_run_id': run.id, 'verifier_node_id': run.node_id,
            'verification_status': status, 'reason': reason, 'checks': [], 'check_scope_paths': [],
            'verification_scope': 'Only the declared checks of the bound source artifacts'}


def _binding_valid(session, run):
    binding = run.resource.get('verification_binding')
    if not isinstance(binding, dict):
        return None, None, 'The verifier has no service-owned source binding'
    try:
        contract = _contract(run.config)
        if run.resource.get('configuration_changed_after_execution'):
            return binding,None,'Verification configuration was edited after execution; run a fresh verifier'
        if contract != binding.get('contract') or _execution_spec(run.config) != binding.get('execution_spec'):
            return binding, None, 'Verification configuration changed; run the verifier again'
        verifier = session.get(Node, run.node_id) if run.node_id else None
        if run.node_id and (verifier is None or verifier.archived or verifier.revision != run.node_revision or
                            verifier.config.get('kind') != 'verification' or _contract(verifier.config) != contract or
                            latest_node_run(session, verifier).id != run.id):
            return binding, None, 'Verifier node or its latest run changed; run the current verifier again'
        producer = get(session, Node, binding['producer_node_id'])
        source = get(session, TaskRun, binding['producer_run_id'])
        if producer.project_id != run.project_id or source.project_id != run.project_id:
            return binding, None, 'Verification source belongs to another project'
        latest = latest_node_run(session, producer)
        if (producer.archived or source.node_id != producer.id or source.status != 'completed' or
                producer.revision != binding['producer_revision'] or source.node_revision != producer.revision or
                latest is None or latest.id != source.id or producer.extra.get('results_current') is False or
                source.resource.get('configuration_changed_after_execution')):
            return binding, None, 'Source node, revision, or latest execution changed; verify the current source again'
        return binding, source, None
    except (ValueError, TypeError, KeyError, AttributeError, HTTPException):
        return binding, None, 'Verification contract or source binding is unavailable'


def _command_completed(run):
    """Check actual backend execution receipts, never a producer-written verdict."""
    if not run.config.get('command'):
        return False
    folder = safe_path(project_dir(run.project_id), run.output_path)
    backend = run.config.get('execution_backend', 'local')
    name = 'container_result.json' if backend == 'container' else 'remote_result.json' if run.config.get('remote') else 'execution.json'
    try:
        receipt = json.loads(safe_path(folder, name, True).read_text())
        if receipt.get('status') != 'completed' or receipt.get('exit_code') != 0:
            return False
        expected = ['/bin/sh', '-c', run.config['command']] if isinstance(run.config['command'], str) else run.config['command']
        if name == 'execution.json' and receipt.get('command') != expected:
            return False
        if name == 'remote_result.json' and receipt.get('task_id') != run.id:
            return False
        if name == 'container_result.json' and not str(receipt.get('task_id', '')).startswith(run.id):
            return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def _evaluate(session, run, *, execution=False):
    verdict = _base_verdict(run)
    binding, source, problem = _binding_valid(session, run)
    if binding:
        verdict.update({key: binding.get(key) for key in ('producer_node_id', 'producer_run_id', 'producer_revision','producer_artifact_scope')})
        verdict['check_scope_paths'] = sorted({path for scope, path in _artifacts(binding['contract']) if scope == 'producer'})
        verdict['contract'] = deepcopy(binding['contract'])
        verdict['numerical_scope'] = [
            {'file': check['source'], 'pointers': check.get('pointers'), 'kind': check['kind']}
            if check['kind']=='numeric_compare' else
            {'file': check['results'], 'pointers': list(check['fields'].values()), 'kind': check['kind']}
            for check in binding['contract']['checks'] if check['kind'] in ('numeric_compare','paired_recompute')]
    if problem:
        verdict['reason'] = problem
        return verdict
    if not execution and run.status != 'completed':
        verdict['reason'] = 'Verifier execution is ' + run.status + '; no accepted result is available'
        return verdict
    from research.validation.handoff import check_contract
    producer_workspace=_producer_workspace(source,binding)
    result = check_contract(binding['contract'], producer_workspace, _workspace(run))
    verdict.update(result)
    verdict['numerical_scope']=[]
    checks={check['id']:check for check in verdict.get('checks',[])}
    for configured in binding['contract']['checks']:
        actual=checks.get(configured['id'],{})
        if configured['kind'] in ('numeric_compare','paired_recompute') and actual.get('status')=='accepted':
            verdict['numerical_scope'].append({'file':configured.get('results',configured['source']),
                'pointers':[entry['pointer'] for entry in actual.get('comparisons',[]) if entry.get('status')=='accepted'],
                'kind':configured['kind'],'artifact_scope':binding.get('producer_artifact_scope','workspace')})
    repeated = any(check['kind'] in ('numeric_compare', 'byte_compare') for check in binding['contract']['checks'])
    if repeated and not _command_completed(run):
        verdict.update(verification_status='inconclusive', reason='An independent command and its successful execution receipt are required for repeated-result checks')
        return verdict
    observations = _observations(binding['contract'], producer_workspace, _workspace(run))
    previous = binding.get('source_observations', []) if execution else run.resource.get('verification_receipt', {}).get('artifact_observations')
    compared = [item for item in observations if item['workspace'] == 'producer'] if execution else observations
    if previous is None:
        verdict.update(verification_status='inconclusive', reason='No service-recorded verification execution exists; run the verifier')
    elif compared != previous and verdict.get('verification_status') == 'accepted':
        verdict.update(verification_status='inconclusive', reason='Checked artifacts changed after the bound execution; run verification again')
    if verdict.get('verification_status') == 'accepted':
        verdict['reason'] = 'Declared checks passed against the current bound artifacts'
    verdict['artifact_observations'] = observations
    return verdict


def verification_for_run(session, run_or_id):
    """Public current verdict for a verifier or for a producer's checked outputs."""
    run = get(session, TaskRun, run_or_id) if isinstance(run_or_id, str) else run_or_id
    if run.kind == 'verification':
        verdict = _evaluate(session, run)
        return {key: value for key, value in verdict.items() if key != 'artifact_observations'}
    verifications = []
    for candidate in session.scalars(select(TaskRun).where(TaskRun.project_id == run.project_id, TaskRun.kind == 'verification').order_by(TaskRun.created_at.desc())):
        binding = candidate.resource.get('verification_binding', {})
        if binding.get('producer_run_id') == run.id:
            if candidate.node_id:
                node=session.get(Node,candidate.node_id)
                latest=latest_node_run(session,node) if node else None
                if latest is not None and latest.id!=candidate.id:
                    continue
            verifications.append(_evaluate(session, candidate))
    status = next((value for value in ('rejected', 'accepted', 'inconclusive')
                   if any(item['verification_status'] == value for item in verifications)), 'unverified')
    return {'run_id': run.id, 'verification_status': status,
            'checks': [check for item in verifications for check in item.get('checks', [])],
            'check_scope_paths': sorted({path for item in verifications if item['verification_status'] == 'accepted' for path in item['check_scope_paths']}),
            'numerical_scope': [scope for item in verifications if item['verification_status']=='accepted' for scope in item.get('numerical_scope',[])],
            'verification_scope': 'Declared checks of named source artifacts; execution completion does not verify other outputs or scientific claims',
            'verifications': [{key: value for key, value in item.items() if key != 'artifact_observations'} for item in verifications]}


def numerical_coverage_for_run(session, run_or_id, relative=None):
    """Complete JSON handoffs require checks of every transmitted numeric leaf."""
    from research.paper.evidence import _numbers, resolve_pointer
    source=get(session,TaskRun,run_or_id) if isinstance(run_or_id,str) else run_or_id
    metrics_file=source.config.get('metrics_file') or source.metrics.get('metrics_file') or 'metrics.json'
    relative=relative or metrics_file
    observed=verification_for_run(session,source)
    scopes=[scope for scope in observed.get('numerical_scope',[]) if scope['file']==relative]
    checked={pointer for scope in scopes for pointer in scope.get('pointers',[])}
    roots={scope.get('artifact_scope','workspace') for scope in scopes}
    numbers={}
    try:
        if len(roots)!=1:
            raise ValueError('The selected numerical artifact has no unambiguous accepted source scope')
        root=_producer_workspace(source,{'producer_artifact_scope':next(iter(roots))})
        numbers=dict(_numbers(json.loads(safe_path(root,relative,True).read_text())))
        if not numbers:
            raise ValueError('The selected artifact has no finite numerical measurements')
    except (OSError,ValueError,TypeError,HTTPException):
        return {'ready':False,'verification_status':'inconclusive','metric_file':relative,
            'checked_pointers':sorted(checked),'missing_pointers':[],
            'verification_scope':'Numerical handoff has no complete readable accepted artifact scope'}
    missing=sorted(set(numbers)-checked)
    mismatches=[]
    if relative==metrics_file:
        stored_metrics=source.metrics.get('observed_metrics',source.metrics)
        for pointer,value in numbers.items():
            try:
                stored=resolve_pointer(stored_metrics,pointer)
            except (KeyError,IndexError,ValueError,TypeError):
                stored=None
            if isinstance(stored,bool) or not isinstance(stored,(int,float)) or stored!=value:
                mismatches.append(pointer)
    accepted=observed['verification_status']=='accepted' and not missing and not mismatches
    return {'ready':accepted,'verification_status':'accepted' if accepted else 'inconclusive',
        'metric_file':relative,'checked_pointers':sorted(checked),'missing_pointers':missing,
        'file_metrics_match':not mismatches,'recorded_metric_mismatches':mismatches,
        'verification_scope':'All numerical leaves of the named artifact; check assumptions and scientific interpretation remain separate'}


def _same_file(left, right):
    if not left.is_file() or not right.is_file() or left.stat().st_size != right.stat().st_size:
        return False
    with left.open('rb') as first, right.open('rb') as second:
        while True:
            a, b = first.read(65536), second.read(65536)
            if a != b:
                return False
            if not a:
                return True


def verification_gate(session, run, *, resolved_inputs=None, copied_workspace=None):
    """Gate configured consumers using current source-bound, path-scoped checks."""
    if run.kind == 'verification':
        return {'ready': True, 'requirements': []}
    node = session.get(Node, run.node_id) if run.node_id else None
    inputs = node.inputs if node else run.config.get('input_references', [])
    try:
        identifiers = _required_ids(node.config if node else run.config, inputs)
    except ValueError as exc:
        return {'ready': False, 'blocked_reason': 'verification_configuration', 'message': str(exc), 'requirements': []}
    sources = [get(session, TaskRun, identifier) for identifier in list(dict.fromkeys([*run.dependencies, *(run.config.get('run_ids') or [])]))]
    sources = [source for source in sources if source.kind != 'verification']
    requirements = []
    explicit = []
    for identifier in identifiers:
        verifier = session.get(Node, identifier)
        if verifier is None or verifier.project_id != run.project_id or verifier.archived or verifier.config.get('kind') != 'verification':
            explicit.append({'verification_status': 'inconclusive', 'verifier_node_id': identifier,
                             'reason': 'Required verifier is unavailable in this project'})
            continue
        latest = latest_node_run(session, verifier)
        explicit.append(verification_for_run(session, latest) if latest else {'verification_status': 'unverified',
            'verifier_node_id': identifier, 'reason': 'Run the required verification node'})
    requirements.extend(explicit)
    if identifiers and sources:
        for source in sources:
            relevant = [item for item in explicit if item.get('producer_run_id') == source.id]
            if not relevant:
                requirements.append({'verification_status':'inconclusive', 'run_id':source.id,
                    'reason':'Required verifier does not bind the actual consumed producer run'})
    if required_policy(session, run):
        requirements.extend(verification_for_run(session, source) for source in sources)
    elif identifiers:
        # Explicit verifier declarations must bind the actual consumed source.
        for reference in inputs:
            if isinstance(reference, dict) and reference.get('node_id') and reference.get('verification_node_id'):
                current = next((source for source in sources if source.node_id == reference['node_id']), None)
                relevant = [item for item in explicit if item.get('verifier_node_id') == reference['verification_node_id']]
                if current is None or not any(item.get('producer_run_id') == current.id for item in relevant):
                    requirements.append({'verification_status': 'inconclusive', 'reason': 'Input verifier does not bind the current consumed producer run'})
    failures = [item for item in requirements if item['verification_status'] != 'accepted']
    if failures:
        status = 'rejected' if any(item['verification_status'] == 'rejected' for item in failures) else 'required'
        return {'ready': False, 'blocked_reason': 'verification_' + status,
                'message': next((item.get('reason') for item in failures if item.get('reason')), 'Complete an accepted source-bound verification before this task'),
                'requirements': requirements}
    if required_policy(session,run) and run.kind in ('paper_generate','paper_compile'):
        for source in sources:
            metric_file=source.config.get('metrics_file') or source.metrics.get('metrics_file') or 'metrics.json'
            coverage=numerical_coverage_for_run(session,source,metric_file)
            if not coverage['ready']:
                return {'ready':False,'blocked_reason':'verification_scope',
                    'message':'Manuscript numerical evidence needs checks of every transmitted metrics value: '+metric_file,
                    'requirements':requirements,'numerical_coverage':coverage}
    guarded = required_policy(session, run) or bool(identifiers)
    if guarded:
        for binding in resolved_inputs if resolved_inputs is not None else run.config.get('resolved_inputs', []):
            source = next((item for item in sources if item.node_id == binding.get('source_node_id')), None)
            if source is None:
                return {'ready': False, 'blocked_reason': 'verification_scope', 'message': 'Required file inputs must identify their verified producer node', 'requirements': requirements}
            checked = verification_for_run(session, source)
            path = binding.get('reference_path')
            accepted = [item for item in checked['verifications'] if item['verification_status'] == 'accepted']
            covered = {p for item in accepted for p in item['check_scope_paths']}
            if path not in covered:
                for item in accepted:
                    root=_producer_workspace(source,{'producer_artifact_scope':item.get('producer_artifact_scope')})
                    candidate=safe_path(project_dir(run.project_id),binding['source_path'])
                    if candidate.is_relative_to(root):
                        path=candidate.relative_to(root).as_posix()
                        if path in covered:break
            if path not in covered:
                return {'ready': False, 'blocked_reason': 'verification_scope', 'message': 'Input artifact is outside the accepted check scope: ' + str(path), 'requirements': requirements}
            matched=next(item for item in accepted if path in item['check_scope_paths'])
            verified = safe_path(_producer_workspace(source,matched), path)
            if Path(path).suffix.lower()=='.json':
                from research.paper.evidence import _numbers
                try:
                    has_numbers=bool(list(_numbers(json.loads(verified.read_text()))))
                except (OSError,ValueError,TypeError):
                    has_numbers=True
                coverage=numerical_coverage_for_run(session,source,path)
                if has_numbers and not coverage['ready']:
                    return {'ready':False,'blocked_reason':'verification_scope',
                        'message':'JSON input contains numerical values outside the accepted check scope: '+path,
                        'requirements':requirements,'numerical_coverage':coverage}
            actual = safe_path(copied_workspace, binding['destination']) if copied_workspace is not None else safe_path(project_dir(run.project_id), binding['source_path'])
            if not _same_file(actual, verified):
                return {'ready': False, 'blocked_reason': 'verification_input_changed', 'message': 'Resolved input differs from the checked producer artifact: ' + str(path), 'requirements': requirements}
    return {'ready': True, 'requirements': requirements}


def dispatch_verification(session, run, *, resolved_inputs=None):
    if run.kind != 'verification':
        return verification_gate(session, run, resolved_inputs=resolved_inputs)
    try:
        contract = _contract(run.config)
        producer = get(session, Node, contract['producer_node_id'])
        source = latest_node_run(session, producer)
        if source and source.resource.get('configuration_changed_after_execution'):
            return {'ready':False,'blocked_reason':'verification_source_changed',
                'message':'Producer execution configuration changed after computation; create a fresh source run'}
        if source is None or source.status != 'completed' or source.node_revision != producer.revision or producer.extra.get('results_current') is False:
            return {'ready': False, 'blocked_reason': 'verification_source', 'message': 'Verification requires the current completed producer execution'}
        if source.id not in run.dependencies:
            return {'ready': False, 'blocked_reason': 'verification_source_changed', 'message': 'Source execution changed after enqueue; rebind by running verification again'}
        if run.resource.get('verification_binding'):
            _,_,problem=_binding_valid(session,run)
            if problem:
                return {'ready':False,'blocked_reason':'verification_source_changed','message':problem}
            # Recovery reconnects to the same execution and retains its original
            # source observation, rather than rebinding edited files mid-run.
            return {'ready':True,'requirements':[]}
        artifact_scope=_producer_scope(source,contract)
        binding = {'producer_node_id': producer.id, 'producer_run_id': source.id,
                   'producer_revision': producer.revision, 'contract': contract,
                   'producer_artifact_scope':artifact_scope,
                   'execution_spec': _execution_spec(run.config), 'bound_at': now(),
                   'source_observations': [item for item in _observations(contract, _producer_workspace(source,{'producer_artifact_scope':artifact_scope}), _workspace(run)) if item['workspace'] == 'producer']}
        run.resource = {**run.resource, 'verification_binding': binding, 'verification_status': 'unverified'}
        return {'ready': True, 'requirements': []}
    except (ValueError, TypeError, KeyError) as exc:
        return {'ready': False, 'blocked_reason': 'verification_configuration', 'message': str(exc)}


def finish_verification(run_id):
    """Called by the service executor after actual independent computation."""
    with Session.begin() as session:
        run = get(session, TaskRun, run_id)
        verdict = _evaluate(session, run, execution=True)
        observations = verdict.pop('artifact_observations', [])
        run.resource = {**run.resource, 'verification_receipt': {'checked_at': now(), 'artifact_observations': observations},
                        'verification_status': verdict['verification_status'], 'verification': verdict}
        folder = safe_path(project_dir(run.project_id), run.output_path)
        temporary = folder / 'verification.tmp'
        temporary.write_text(json.dumps(verdict, ensure_ascii=False, indent=2, allow_nan=False))
        temporary.replace(folder / 'verification.json')
        return verdict


@router.get('/api/runs/{ident}/verification')
def current_verification(ident: str):
    with Session() as session:
        return verification_for_run(session, get(session, TaskRun, ident))


@router.post('/api/verification/run')
def submit_verification(body: dict = Body(...)):
    from services.worker.scheduler import enqueue
    if not isinstance(body.get('project_id'), str):
        error('INVALID_VERIFICATION', 'project_id is required', 422)
    with Session.begin() as session:
        node = get(session, Node, body['node_id']) if body.get('node_id') else None
        if node and (node.project_id != body['project_id'] or node.config.get('kind') != 'verification'):
            error('INVALID_VERIFICATION', 'Select a verification node in this project', 422)
        config={key:value for key,value in body.items() if key not in ('node_id','project_id','request_id')}
        if RESERVED_CONFIG & config.keys():
            error('INVALID_CONFIGURATION','Verification identities and verdicts are managed by the service',422)
        if body.get('request_id'):
            previous=session.scalar(select(TaskRun).where(TaskRun.project_id==body['project_id'],TaskRun.request_id==body['request_id']))
            if previous and (previous.kind!='verification' or previous.node_id!=(node.id if node else None) or
                    _contract({**(node.config if node else {}),**config})!=_contract(previous.config)):
                error('REQUEST_ID_CONFLICT','Request ID already belongs to another verification submission',409)
        run = enqueue(session, body['project_id'], 'verification', config,
                      body.get('request_id'), node)
        result = asdict(run)
        result['config'] = {key: value for key, value in result['config'].items() if key not in ('provider_snapshot', 'env', 'remote')}
        return result
