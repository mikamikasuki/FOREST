#!/usr/bin/env python3
"""Offline, consistent data backup and restore into an empty destination.

SQLite uses its backup API. PostgreSQL uses pg_dump/pg_restore and creates only a
new forest_restore_* database. Verification never overwrites a live database.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SECRET_NAMES = {'secrets.json', 'owner-token', 'owner_token', 'database_password', '.env'}


def _url(value):
    from sqlalchemy.engine import make_url
    return make_url(value)


def _pg_env(url):
    url = _url(url)
    env = dict(os.environ)
    for key, value in {'PGHOST': url.host, 'PGPORT': url.port, 'PGUSER': url.username, 'PGPASSWORD': url.password, 'PGDATABASE': url.database}.items():
        if value is not None:
            env[key] = str(value)
    for key, value in url.query.items():
        if key in ('sslmode', 'sslrootcert', 'sslcert', 'sslkey'):
            env['PG' + key.upper()] = value
    return env


def _engine(url):
    from sqlalchemy import create_engine
    return create_engine(url, pool_pre_ping=True)


def database_summary(url):
    from sqlalchemy import inspect, text
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            return {table: connection.execute(text('SELECT count(*) FROM ' + engine.dialect.identifier_preparer.quote(table))).scalar_one() for table in sorted(tables)}
    finally:
        engine.dispose()


def check_sqlite(path):
    with sqlite3.connect(path) as connection:
        result = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise ValueError('SQLite integrity check failed: ' + result)
        violations = connection.execute('PRAGMA foreign_key_check').fetchmany(10)
        if violations:
            raise ValueError('SQLite foreign key check failed: ' + repr(violations))


def assert_offline(data_dir):
    import psutil
    for process in psutil.process_iter(['pid', 'cmdline', 'cwd']):
        if process.pid == os.getpid():
            continue
        try:
            command = ' '.join(process.info['cmdline'] or [])
            if not any(token in command for token in ('services.api.main:app', 'services.worker.main', 'services.worker.execute', 'research.agents.process_host', 'scripts/start.py')):
                continue
            env = process.environ()
            working = Path(process.info.get('cwd') or ROOT)
            configured = Path(env.get('FOREST_DATA_DIR', str(working / 'var')))
            if not configured.is_absolute():
                configured = working / configured
            if configured.resolve() == data_dir.resolve():
                raise RuntimeError(f'Stop FOREST processes using this data directory before backup/restore (process {process.pid})')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _copy_data(source, destination, database_path=None, include_secrets=False):
    copied, omitted = [], []
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise ValueError('Backup refuses symlinks; move linked data into the data directory: ' + str(relative))
        if not path.is_file():
            continue
        if database_path and (path == database_path or str(path) in (str(database_path) + '-wal', str(database_path) + '-shm')):
            continue
        if not include_secrets and (path.name in SECRET_NAMES or 'secrets' in relative.parts or path.name.startswith('.env.')):
            omitted.append(relative.as_posix())
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = path.stat()
        shutil.copy2(path, target)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('Data changed during offline backup: ' + str(relative))
        copied.append(relative.as_posix())
    return copied, omitted


def create_backup(data_dir, database_url, destination, include_secrets=False):
    data_dir, destination = Path(data_dir).resolve(), Path(destination).resolve()
    if not data_dir.is_dir():
        raise ValueError('Data directory does not exist')
    if destination.exists():
        raise ValueError('Backup destination already exists')
    if destination.is_relative_to(data_dir):
        raise ValueError('Save backups outside the data directory')
    assert_offline(data_dir)
    url = _url(database_url)
    database_path = Path(url.database).resolve() if url.get_backend_name() == 'sqlite' else None
    if database_path and not database_path.is_file():
        raise ValueError('SQLite database does not exist')
    if url.get_backend_name() not in ('sqlite', 'postgresql'):
        raise ValueError('Only SQLite and PostgreSQL backups are supported')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='forest-backup-') as temporary:
        stage = Path(temporary).resolve()
        (stage / 'data').mkdir()
        if database_path:
            dump = stage / 'database.sqlite3'
            source = sqlite3.connect('file:' + str(database_path) + '?mode=ro', uri=True)
            try:
                with sqlite3.connect(dump) as target:
                    source.backup(target)
            finally:
                source.close()
            check_sqlite(dump)
        else:
            dump = stage / 'database.dump'
            if not shutil.which('pg_dump'):
                raise RuntimeError('PostgreSQL client pg_dump is required')
            subprocess.run(['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--file', str(dump)], env=_pg_env(database_url), check=True, timeout=3600)
        files, omitted = _copy_data(data_dir, stage / 'data', database_path, include_secrets)
        manifest = {'format': 'forest-backup-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                    'database_backend': url.get_backend_name(), 'database_file': dump.name,
                    'tables': database_summary(database_url), 'files': files, 'omitted_secrets': omitted,
                    'secrets_included': include_secrets, 'consistency': 'offline_services_required'}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        pending = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with tarfile.open(pending, 'w:gz') as archive:
                for item in sorted(stage.iterdir()):
                    archive.add(item, arcname=item.name)
            os.chmod(pending, 0o600)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
    return {**manifest, 'archive': str(destination), 'bytes': destination.stat().st_size}


@contextmanager
def unpack_backup(archive):
    with tempfile.TemporaryDirectory(prefix='forest-restore-stage-') as temporary:
        stage = Path(temporary).resolve()
        with tarfile.open(archive, 'r:gz') as bundle:
            seen = set()
            for member in bundle.getmembers():
                name = member.name
                target = (stage / name).resolve()
                if name in seen or '\\' in name or not target.is_relative_to(stage) or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe or duplicate backup archive member: ' + name)
                seen.add(name)
            bundle.extractall(stage, filter='data')
        manifest = json.loads((stage / 'manifest.json').read_text())
        if manifest.get('format') != 'forest-backup-v1':
            raise ValueError('Unsupported backup format')
        filename = manifest.get('database_file')
        if filename not in ('database.sqlite3', 'database.dump'):
            raise ValueError('Invalid backup database path')
        actual = sorted(p.relative_to(stage / 'data').as_posix() for p in (stage / 'data').rglob('*') if p.is_file())
        if actual != sorted(manifest['files']):
            raise ValueError('Backup file inventory does not match manifest')
        yield stage, manifest


def _new_postgres(admin_url):
    import psycopg
    from psycopg import sql
    url = _url(admin_url)
    name = 'forest_restore_' + uuid.uuid4().hex
    parameters = {'host': url.host, 'port': url.port or 5432, 'user': url.username, 'password': url.password, 'dbname': url.database or 'postgres'}
    parameters = {k: v for k, v in parameters.items() if v is not None}
    parameters.update(dict(url.query))
    with psycopg.connect(**parameters, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    return url.set(drivername='postgresql+psycopg', database=name).render_as_string(hide_password=False), parameters, name


def _drop_postgres(parameters, name):
    import psycopg
    from psycopg import sql
    if not name.startswith('forest_restore_'):
        raise ValueError('Refusing to remove a database outside the isolated restore namespace')
    with psycopg.connect(**parameters, autocommit=True) as connection:
        connection.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def quarantine_execution(database_url):
    """Restored process IDs/remote handles must never control old live work."""
    from sqlalchemy import inspect, text
    engine = _engine(database_url)
    try:
        with engine.begin() as connection:
            tables = inspect(connection).get_table_names()
            if 'task_runs' in tables:
                connection.execute(text("UPDATE task_runs SET status='interrupted', pid=NULL, process_created=NULL, worker_id=NULL, error='Restored from backup; review artifacts and explicitly start a new run before execution' WHERE status IN ('queued','running','paused','pausing','waiting','waiting_input','budget_exhausted','cancelling')"))
            def read_object(value):
                return json.loads(value) if isinstance(value,str) else dict(value or {})
            def save_object(table,column,ident,value):
                encoded=json.dumps(value)
                assignment='CAST(:value AS JSON)' if engine.dialect.name=='postgresql' else ':value'
                connection.execute(text(f'UPDATE {table} SET {column}={assignment} WHERE id=:id'),{'value':encoded,'id':ident})
            if 'task_runs' in tables and 'resource' in {c['name'] for c in inspect(connection).get_columns('task_runs')}:
                for ident,resource in connection.execute(text('SELECT id, resource FROM task_runs')).all():
                    resource=read_object(resource)
                    if resource.get('pending_intervention'):
                        resource['historical_pending_intervention']=resource.pop('pending_intervention')
                    resource.pop('wait_for',None)
                    save_object('task_runs','resource',ident,resource)
            if 'intervention_effects' in tables:
                for ident,status,observation in connection.execute(text('SELECT id,status,observation FROM intervention_effects')).all():
                    if status not in ('applied','superseded'):
                        save_object('intervention_effects','observation',ident,{**read_object(observation),
                            'reason':'backup_restore_requires_new_execution','original_status':status})
                        connection.execute(text("UPDATE intervention_effects SET status='superseded',target='{}',lease_owner=NULL,lease_until=0,retry_after=0 WHERE id=:id"),{'id':ident})
                    else:
                        connection.execute(text("UPDATE intervention_effects SET target='{}',lease_owner=NULL,lease_until=0 WHERE id=:id"),{'id':ident})
            if 'interventions' in tables:
                connection.execute(text("UPDATE interventions SET status='superseded' WHERE status IN ('accepted','needs_attention','partially_applied')"))
            if 'action_decisions' in tables:
                connection.execute(text("UPDATE action_decisions SET status='stale' WHERE status IN ('pending','accepted')"))
            if 'branches' in tables:
                for ident,extra in connection.execute(text('SELECT id,extra FROM branches')).all():
                    extra=read_object(extra)
                    if extra.get('workspace_intervention'):
                        extra['historical_workspace_intervention']=extra.pop('workspace_intervention')
                        save_object('branches','extra',ident,extra)
                        connection.execute(text("UPDATE branches SET status='disabled' WHERE id=:id"),{'id':ident})
            # Derived snapshots/leases must not become current after a restore.
            for table in ('observed_files','observation_scopes','report_requests','report_jobs','report_dispatch_slots','observation_states'):
                if table in tables:
                    connection.execute(text('DELETE FROM '+table))
            if 'reporter_policies' in tables:
                for ident, settings in connection.execute(text('SELECT project_id, settings FROM reporter_policies')):
                    settings = json.loads(settings) if isinstance(settings,str) else dict(settings or {})
                    settings.update(enabled=False,automatic=False)
                    encoded=json.dumps(settings)
                    assignment='CAST(:settings AS JSON)' if engine.dialect.name=='postgresql' else ':settings'
                    connection.execute(text('UPDATE reporter_policies SET settings='+assignment+', version=version+1 WHERE project_id=:id'), {'settings':encoded,'id':ident})
            if 'model_requests' in tables:
                connection.execute(text("UPDATE model_requests SET status='uncertain' WHERE status='reserved'"))
            if 'workers' in tables:
                connection.execute(text('DELETE FROM workers'))
            if 'projects' in tables:
                for ident, config in connection.execute(text('SELECT id, config FROM projects')):
                    config = json.loads(config) if isinstance(config, str) else dict(config or {})
                    if config.get('controller'):
                        config['controller'] = {**config['controller'], 'status': 'paused', 'reason': 'Restored backup requires explicit resume'}
                        encoded = json.dumps(config)
                        if engine.dialect.name == 'postgresql':
                            connection.execute(text('UPDATE projects SET config=CAST(:config AS JSON) WHERE id=:id'), {'config': encoded, 'id': ident})
                        else:
                            connection.execute(text('UPDATE projects SET config=:config WHERE id=:id'), {'config': encoded, 'id': ident})
    finally:
        engine.dispose()


def restore_backup(archive, destination, postgres_admin_url=None, verify_only=False):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Restore requires an empty destination; existing data is never overwritten')
    assert_offline(destination)
    created_pg = None
    with unpack_backup(archive) as (stage, manifest):
        try:
            if manifest['database_backend'] == 'sqlite':
                check_sqlite(stage / 'database.sqlite3')
                database_url = 'sqlite:///' + str(stage / 'database.sqlite3')
            elif manifest['database_backend'] == 'postgresql':
                if not postgres_admin_url:
                    raise ValueError('PostgreSQL restore requires --postgres-admin-url (or FOREST_BACKUP_POSTGRES_ADMIN_URL) to create a new isolated database')
                if not shutil.which('pg_restore'):
                    raise RuntimeError('PostgreSQL client pg_restore is required')
                database_url, parameters, name = _new_postgres(postgres_admin_url)
                created_pg = (parameters, name)
                subprocess.run(['pg_restore', '--no-owner', '--no-acl', '--exit-on-error', '--dbname', name, str(stage / 'database.dump')], env=_pg_env(database_url), check=True, timeout=3600)
            else:
                raise ValueError('Unsupported database backend')
            restored_tables = database_summary(database_url)
            if restored_tables != manifest['tables']:
                raise ValueError('Restored database row counts differ from backup inventory')
            report = {'status': 'verified' if verify_only else 'restored', 'backend': manifest['database_backend'],
                      'tables': restored_tables, 'files': len(manifest['files']), 'secrets_included': manifest['secrets_included'],
                      'verification': 'actual isolated restore and table counts; SQLite additionally integrity and foreign keys'}
            if verify_only:
                return report
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copytree(stage / 'data', destination, dirs_exist_ok=True)
            if manifest['database_backend'] == 'sqlite':
                target = destination / 'forest.db'
                if target.exists():
                    raise ValueError('Backup data collides with restored database filename')
                shutil.copy2(stage / 'database.sqlite3', target)
                database_url = 'sqlite:///' + str(target)
                report['database_url'] = database_url
            else:
                report['database_name'] = created_pg[1]
                report['database_url'] = _url(database_url).render_as_string(hide_password=True)
            quarantine_execution(database_url)
            report['data_dir'] = str(destination)
            report['execution'] = 'active runs interrupted; controllers paused; explicitly start new work after review'
            (destination / 'restore-report.json').write_text(json.dumps(report, indent=2))
            created_pg = None  # Keep only a successfully restored database.
            return report
        finally:
            if created_pg:
                _drop_postgres(*created_pg)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    create = commands.add_parser('create')
    create.add_argument('archive', type=Path)
    create.add_argument('--data-dir', type=Path, required=True)
    create.add_argument('--database-url', default=os.environ.get('FOREST_DATABASE_URL'))
    create.add_argument('--include-secrets', action='store_true')
    create.add_argument('--offline', action='store_true', required=True, help='Confirm all services and other writers to the selected data have stopped')
    for name in ('restore', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('archive', type=Path)
        command.add_argument('--postgres-admin-url', default=os.environ.get('FOREST_BACKUP_POSTGRES_ADMIN_URL'))
        if name == 'restore':
            command.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'create':
        database_url = args.database_url or 'sqlite:///' + str(args.data_dir.resolve() / 'forest.db')
        result = create_backup(args.data_dir, database_url, args.archive, args.include_secrets)
    elif args.action == 'restore':
        result = restore_backup(args.archive, args.data_dir, args.postgres_admin_url)
    else:
        with tempfile.TemporaryDirectory(prefix='forest-verify-') as temporary:
            result = restore_backup(args.archive, Path(temporary) / 'data', args.postgres_admin_url, verify_only=True)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
