#!/usr/bin/env python3
"""Report actual local capabilities without installing software or using models."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def probe(argv, timeout=15):
    if not shutil.which(argv[0]):
        return {'available': False, 'reason': argv[0] + ' is not installed or not on PATH'}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {'available': result.returncode == 0, 'exit_code': result.returncode, 'detail': (result.stdout or result.stderr).strip()[-1500:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'available': False, 'reason': str(exc)}


def report():
    host = platform.system()
    probes = {
        'python': {'available': sys.version_info >= (3, 11), 'detail': sys.version.split()[0]},
        'node': probe(['node', '--version']),
        'npm': probe(['npm', '--version']),
        'latex': probe(['tectonic' if shutil.which('tectonic') else 'pdflatex', '--version']),
        'docker_engine': probe(['docker', 'info', '--format', '{{.ServerVersion}} {{.OSType}} {{.Architecture}}']),
        'compose': probe(['docker', 'compose', 'version']),
        'postgres_backup': probe(['pg_dump', '--version']),
        'postgres_restore': probe(['pg_restore', '--version']),
        'nvidia_gpu': probe(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader']),
        'web_build': {'available': (ROOT / 'apps/web/dist/index.html').is_file()},
        'python_packages': {name: importlib.util.find_spec(name) is not None for name in ('fastapi', 'sqlalchemy', 'psycopg', 'numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'httpx')},
    }
    if not probes['compose']['available'] and shutil.which('docker-compose'):
        probes['compose'] = {**probe(['docker-compose', 'version']), 'invocation': 'docker-compose'}
    try:
        node_major = int(probes['node'].get('detail', 'v0').split('.')[0].lstrip('v'))
    except ValueError:
        node_major = 0
    local_ready = host in ('Darwin', 'Linux') and probes['python']['available'] and node_major >= 22 and all(probes['python_packages'].values()) and probes['web_build']['available']
    return {'host': {'system': host, 'architecture': platform.machine(), 'wsl': 'microsoft' in platform.release().lower()},
            'local_runtime_ready': local_ready,
            'container_runtime_ready': probes['docker_engine']['available'],
            'checks': probes,
            'limits': ['Local execution is trusted code with host-user privileges.',
                       'Container GPU requires a supported Linux Docker host and NVIDIA runtime; macOS Apple GPU is not passed through.',
                       'Native Windows process control is unsupported; use WSL2 or Docker Desktop.',
                       'Compose services do not mount the Docker socket; task containers run through a host worker.',
                       'No provider connectivity or model call is performed by this diagnostic.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require', choices=['local', 'container', 'compose'])
    args = parser.parse_args()
    value = report()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2))
    print(json.dumps(value, indent=2))
    ready = value['local_runtime_ready'] if args.require == 'local' else value['container_runtime_ready']
    if args.require == 'compose':
        ready = ready and value['checks']['compose']['available']
    if args.require and not ready:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
