#!/usr/bin/env python3
"""Idempotent local setup or explicit Compose setup; never installs a VM."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare_secrets(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    created = []
    for name in ('database_password', 'owner_token'):
        target = directory / name
        if not target.exists():
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(secrets.token_urlsafe(48) + '\n')
            created.append(name)
        if len(target.read_text().strip()) < 24:
            raise ValueError(f'{name} is too short; use a random value of at least 24 characters')
        # Compose bind-mounted secrets retain host permissions; the private parent
        # directory protects these read-only files from other host users.
        os.chmod(target, 0o444)
    return {'directory': str(directory), 'created': created, 'values': 'not printed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['local', 'compose'], default='local')
    parser.add_argument('--secrets-dir', type=Path, default=ROOT / 'var/deployment/secrets')
    parser.add_argument('--prepare-only', action='store_true', help='Prepare Compose secrets without building or starting services')
    args = parser.parse_args()
    if args.mode == 'compose':
        report = prepare_secrets(args.secrets_dir)
        print(json.dumps(report, indent=2))
        if args.prepare_only:
            return
        if not shutil.which('docker'):
            raise SystemExit('Install Docker Desktop or Docker with Colima, then start the engine. This installer does not create a VM.')
        env = {**os.environ, 'FOREST_SECRETS_DIR': report['directory']}
        subprocess.run(['docker', 'info'], env=env, stdout=subprocess.DEVNULL, check=True)
        compose = ['docker', 'compose']
        if subprocess.run([*compose, 'version'], capture_output=True).returncode and shutil.which('docker-compose'):
            compose = ['docker-compose']
        subprocess.run([*compose, 'config', '--quiet'], cwd=ROOT, env=env, check=True)
        subprocess.run([*compose, 'up', '--build', '-d', '--wait'], cwd=ROOT, env=env, check=True)
        print('FOREST is running at http://127.0.0.1:' + env.get('FOREST_PORT', '8000'))
        return
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 or newer is required')
    if not shutil.which('npm'):
        raise SystemExit('Node.js 22+ with npm is required')
    version = subprocess.check_output(['node', '--version'], text=True).strip()
    if int(version.lstrip('v').split('.')[0]) < 22:
        raise SystemExit('Node.js 22+ is required')
    if os.name == 'nt':
        raise SystemExit('Run local services inside WSL2, or use Compose with Docker Desktop. Native Windows is supported for source/project archive inspection only.')
    subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    python = ROOT / '.venv/bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.lock.txt')], check=True)
    subprocess.run(['npm', 'ci'], cwd=ROOT / 'apps/web', check=True)
    subprocess.run(['npm', 'run', 'build'], cwd=ROOT / 'apps/web', check=True)
    subprocess.run([str(python), '-m', 'services.api.db'], cwd=ROOT, check=True)
    print('Ready. Start with .venv/bin/python scripts/start.py; install a user service with scripts/service.py install.')


if __name__ == '__main__':
    main()
