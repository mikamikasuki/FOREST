"""Recovery decisions are explicit; restart and restored computation differ."""
from pathlib import Path
import json
import math


def recovery_decision(config, output, *, kind, failed=False):
    policy = config.get('recovery') or ({'mode': 'restart'} if kind == 'agent' else {})
    mode = policy.get('mode', 'manual')
    attempt = int(config.get('_recovery_failures', 0)) + 1
    maximum = policy.get('max_attempts', 3)
    if mode == 'manual' or (maximum is not None and attempt >= int(maximum)):
        return None
    if failed and not policy.get('retry_on_failure', False):
        return None
    if mode not in ('checkpoint', 'restart'):
        return None
    if mode == 'checkpoint':
        relative = policy.get('checkpoint')
        if not relative:
            return None
        workspace = (Path(output) / 'workspace').resolve()
        path = (workspace / relative).resolve()
        if not path.is_relative_to(workspace) or not path.is_file() or path.stat().st_size == 0:
            return None
        # JSON checkpoints are checked for truncation before being offered to a task.
        if path.suffix == '.json':
            try: json.loads(path.read_text())
            except (ValueError, UnicodeError): return None
        return {'mode': 'checkpoint', 'checkpoint_path': str(path)}
    return {'mode': 'restart'}


def resource_request(config):
    resources = config.get('resources') or {}
    if not isinstance(resources,dict): raise ValueError('resources must be an object')
    cpu=float(resources.get('cpu',1)); memory=float(resources.get('memory_gb',0)); gpu=float(resources.get('gpu',0))
    if not all(math.isfinite(x) for x in (cpu,memory,gpu)) or cpu<=0 or memory<0 or gpu<0 or not gpu.is_integer():
        raise ValueError('CPU must be positive; memory and integer GPU count must be nonnegative finite numbers')
    return {'cpu':cpu,'memory_bytes':int(memory*1024**3),'gpu':int(gpu)}


def admission(request, active_requests, capacity):
    for key in ('cpu', 'memory_bytes', 'gpu'):
        if request[key] > capacity[key]:
            return 'impossible', key
        if request[key] + sum(item[key] for item in active_requests) > capacity[key]:
            return 'wait', key
    return 'ready', None
