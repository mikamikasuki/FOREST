"""Bounded, read-only execution receipts for human and machine diagnostics."""
from __future__ import annotations

import json
from itertools import islice
from pathlib import Path
import re
import time


_SECRET_KEY = re.compile(r'(?:^|_)(?:api_key|token|password|secret|authorization)$', re.I)
_AUTH_TEXT = re.compile(r'(?i)\b((?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+)\S+')
_ASSIGNMENT = re.compile(r'(?i)(\b[\w-]*(?:api[_-]?key|token|password|secret|authorization)\s*[=:]\s*)(?:"[^"]*"|\x27[^\x27]*\x27|[^\s;]+)')
_URL_AUTH = re.compile(r'(https?://)[^\s/@]+(?::[^\s/@]*)?@', re.I)
_TOKEN = re.compile(r'\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,})')
_SECRET_OPTION = re.compile(r'(?i)(--(?:token|password|api[_-]?key|authorization|secret|access-token)(?:\s+|=))(?:"[^"]*"|\x27[^\x27]*\x27|[^\s;&|]+)')
_SECRET_FLAGS = {'--token', '--password', '--api-key', '--api_key', '--authorization', '--secret', '--access-token'}


def _secret_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)) and isinstance(item, str) and item:
                yield item
            else:
                yield from _secret_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _secret_values(item)


def _text(value, secrets):
    value = str(value)
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, '[redacted]')
    value = _URL_AUTH.sub(r'\1[redacted]@', value)
    value = _AUTH_TEXT.sub(r'\1[redacted]', value)
    value = _ASSIGNMENT.sub(r'\1[redacted]', value)
    value = _SECRET_OPTION.sub(r'\1[redacted]', value)
    return _TOKEN.sub('[redacted]', value)[:2000]


def _command(value, secrets):
    if isinstance(value, str):
        return _text(value, secrets)
    if not isinstance(value, list):
        return None
    result = []
    mask_next = False
    for argument in value[:100]:
        argument = str(argument)
        result.append('[redacted]' if mask_next else _text(argument, secrets))
        mask_next = argument.lower() in _SECRET_FLAGS
    return result


def _regular(path, root):
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            return False
        current = path
        while current != root:
            if current.is_symlink():
                return False
            current = current.parent
        return path.is_file()
    except OSError:
        return False


def _read(path, root):
    try:
        if not _regular(path, root) or path.stat().st_size > 2_000_000:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _process(request, state, identity, *, process_id, backend, observed_at, secrets):
    safe = {'process_id': process_id, 'backend': backend,
            'status': _text(state.get('status', 'starting'), secrets),
            'command': _command(request.get('command'), secrets),
            'state_source': 'saved_execution_receipt', 'observed_at': observed_at}
    for key in ('exit_code', 'started_at', 'finished_at', 'elapsed_seconds'):
        if isinstance(state.get(key), (int, float, str)):
            safe[key] = _text(state[key], secrets) if isinstance(state[key], str) else state[key]
    if backend == 'local':
        from research.agents.processes import is_alive
        if (isinstance(identity.get('pid'), int) and not isinstance(identity['pid'], bool) and
                identity['pid'] > 0 and isinstance(identity.get('process_created'), (int, float))):
            safe['process_alive'] = is_alive(identity['pid'], identity['process_created'])
            safe['state_source'] = 'saved_receipt_and_local_process_identity'
            if safe['process_alive'] and safe['status'] == 'starting':
                safe['status'] = 'running'
    return safe


def run_diagnostics(folder, *, run_id, status, config=None, resource=None):
    """Receipts describe observed execution, never certify research success.

    Container/remote state is the last saved observation; this read endpoint
    does not launch external commands or poll those execution hosts.
    """
    root = Path(folder)
    config = config or {}
    resource = resource or {}
    secrets = set(_secret_values(config))
    backend = 'remote' if config.get('remote') else config.get('execution_backend', 'local')
    records = []
    truncated = False
    directories = [(root / 'workspace' / '.forest-processes', 'local'),
                   (root / '.forest-container-processes' / 'workspace', 'container')]
    for directory, kind in directories:
        if directory.is_symlink() or not directory.is_dir():
            continue
        candidates = list(islice(directory.glob('*/request.json'), 101))
        truncated = truncated or len(candidates) > 100
        for request_path in sorted(candidates[:100]):
            request = _read(request_path, root)
            if not request:
                continue
            secrets.update(_secret_values(request.get('env', {})))
            state_path = request_path.parent / 'state.json'
            state = _read(state_path, root)
            identity = _read(request_path.parent / 'identity.json', root)
            updated = state_path if _regular(state_path, root) else request_path
            records.append((request, state, identity, request_path.parent.name, kind, _mtime(updated)))
    processes = [_process(request, state, identity, process_id=process_id, backend=kind,
                          observed_at=observed_at, secrets=secrets)
                 for request, state, identity, process_id, kind, observed_at in records]
    execution_path = root / 'execution.json'
    execution = _read(execution_path, root)
    if execution:
        processes.append(_process(execution, execution, {}, process_id='command', backend=backend,
                                  observed_at=_mtime(execution_path), secrets=secrets))
    # Whole-run container/remote receipts supplement managed agent processes.
    for kind in ('container', 'remote'):
        task_path = root / (kind + '_task.json')
        task = _read(task_path, root)
        if not task:
            continue
        result_path = root / (kind + '_result.json')
        state = _read(result_path, root) or task
        updated = result_path if _regular(result_path, root) else task_path
        processes.append(_process({'command': config.get('command')}, state, {}, process_id=task.get('task_id', 'command'),
                                  backend=kind, observed_at=_mtime(updated), secrets=secrets))
    repository = _read(root / 'repository.json', root)
    source = {key: repository[key] for key in ('url', 'ref', 'requested_ref', 'commit', 'directory', 'transport') if key in repository}
    source = {key: _text(value, secrets) for key, value in source.items() if isinstance(value, str)}
    log = root / 'stdout.txt'
    logs = {'bytes': 0, 'updated_at': None}
    try:
        if _regular(log, root):
            info = log.stat()
            logs = {'bytes': info.st_size, 'updated_at': info.st_mtime}
    except OSError:
        pass
    phase = status
    if status == 'running':
        phase = 'preparing_source' if config.get('repository') and not source else 'executing'
    return {'run_id': run_id, 'status': status, 'phase': phase, 'execution_backend': backend,
            'repository': source or None, 'processes': processes,
            'processes_truncated': truncated,
            'logs': logs, 'elapsed_seconds': resource.get('elapsed_seconds'),
            'observed_at': time.time(), 'observation': 'saved_execution_receipts',
            'remote_state_is_live': False}
