"""File publication concurrency and deferred commit aborts on disposable PostgreSQL."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import threading
import time
import uuid

import pytest

from test_api import create, ok


ROOT = Path(__file__).resolve().parents[1]
requires_disposable_database = pytest.mark.skipif(
    not os.environ.get("FOREST_FILE_REVISION_POSTGRES"),
    reason="Set FOREST_FILE_REVISION_POSTGRES only for the dedicated disposable PostgreSQL database",
)


@pytest.mark.skipif(not os.environ.get('FOREST_TEST_POSTGRES') or os.environ.get('FOREST_FILE_REVISION_POSTGRES'),
                    reason='Set FOREST_TEST_POSTGRES to authorize a disposable PostgreSQL test database')
def test_isolated_file_revisions_on_postgres(tmp_path):
    from test_postgres import _postgres_parameters

    createdb, dropdb = shutil.which('createdb'), shutil.which('dropdb')
    assert createdb and dropdb, 'PostgreSQL client tools are required'
    env, flags, authority = _postgres_parameters()
    database = 'forest_test_' + uuid.uuid4().hex
    created = False
    try:
        subprocess.run([createdb, *flags, database], env=env, check=True, capture_output=True, timeout=20)
        created = True
        env.update(FOREST_FILE_REVISION_POSTGRES='1',
                   FOREST_DATABASE_URL=f'postgresql+psycopg://{authority}/{database}',
                   FOREST_DATA_DIR=str(tmp_path / 'data'), FOREST_MODEL='',
                   FOREST_OWNER_TOKEN='file-revision-test-owner', PYTHONPATH=str(ROOT))
        result = subprocess.run([sys.executable, '-m', 'pytest', str(Path(__file__).resolve()),
                                 '-q', '-k', 'test_postgres_'], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        if created:
            subprocess.run([dropdb, *flags, '--force', database], env=env, check=True,
                           capture_output=True, timeout=20)


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient
    from services.api.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _project_file(client):
    project = create(client)
    return project, f"/api/projects/{project['id']}/file", f"/api/projects/{project['id']}/upload"


def _upload(client, endpoint, filename, content):
    return client.post(endpoint, files={"file": (filename, content, "text/plain")})


@requires_disposable_database
def test_postgres_launch_refreshes_execution_kind(api_client):
    from test_api import case_launch_refreshes_execution_kind_after_project_lock

    case_launch_refreshes_execution_kind_after_project_lock(api_client, None)


@requires_disposable_database
def test_postgres_upload_revisions_and_stale_put(api_client):
    client = api_client
    project, file_endpoint, upload_endpoint = _project_file(client)
    try:
        new_upload = ok(_upload(client, upload_endpoint, "new.txt", b"new bytes\n"))
        assert new_upload == {"path": "uploads/new.txt", "size": 10, "origin": "user_import"}
        first = ok(client.get(file_endpoint, params={"path": new_upload["path"]}))
        assert (first["revision"], first["origin"], first["content"]) == (1, "user_import", "new bytes\n")

        same = ok(_upload(client, upload_endpoint, "new.txt", b"new bytes\n"))
        second = ok(client.get(file_endpoint, params={"path": new_upload["path"]}))
        assert same == new_upload
        assert (second["revision"], second["origin"], second["content"]) == (2, "user_import", "new bytes\n")

        put = ok(client.put(file_endpoint, json={
            "path": new_upload["path"], "content": "edited bytes\n",
            "expected_revision": second["revision"],
        }))
        assert put == {"path": new_upload["path"], "revision": 3, "origin": "user_edited"}
        stale = client.put(file_endpoint, json={
            "path": new_upload["path"], "content": "stale bytes\n",
            "expected_revision": second["revision"],
        })
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
        saved = ok(client.get(file_endpoint, params={"path": new_upload["path"]}))
        assert (saved["revision"], saved["origin"], saved["content"]) == (3, "user_edited", "edited bytes\n")

        legacy = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"] / "uploads" / "legacy.txt"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(b"old legacy\n")
        legacy_before = ok(client.get(file_endpoint, params={"path": "uploads/legacy.txt"}))
        assert (legacy_before["revision"], legacy_before["origin"]) == (0, "executor_or_import")
        ok(_upload(client, upload_endpoint, "legacy.txt", b"new legacy\n"))
        legacy_after = ok(client.get(file_endpoint, params={"path": "uploads/legacy.txt"}))
        assert (legacy_after["revision"], legacy_after["origin"], legacy_after["content"]) == (
            1, "user_import", "new legacy\n"
        )

        from services.api.db import FileRevision, Session
        with Session() as session:
            row = session.query(FileRevision).filter_by(
                project_id=project["id"], path="uploads/new.txt"
            ).one()
            assert (row.revision, row.origin) == (3, "user_edited")
    finally:
        client.delete(f"/api/projects/{project['id']}")


@requires_disposable_database
def test_postgres_barrier_orders_get_put_and_upload(api_client, monkeypatch):
    client = api_client
    project, file_endpoint, upload_endpoint = _project_file(client)
    path = "uploads/order.txt"
    try:
        initial = ok(client.put(file_endpoint, json={
            "path": path, "content": "initial\n", "expected_revision": 0,
        }))
        assert initial["revision"] == 1

        import services.api.files as file_api
        import services.worker.scheduler as scheduler

        real_guard = file_api.file_publication_lock
        real_lock_project = scheduler._lock_project
        guard_calls = {"count": 0}
        guard_call_lock = threading.Lock()
        waiter_arrived = threading.Event()
        first_arrived = threading.Event()
        release_first = threading.Event()
        blocked_operation = {"value": None}

        @contextmanager
        def observed_guard(project_id, relative):
            with guard_call_lock:
                guard_calls["count"] += 1
                if guard_calls["count"] == 2:
                    waiter_arrived.set()
            with real_guard(project_id, relative):
                yield

        def observed_project_lock(session, project_id):
            result = real_lock_project(session, project_id)
            if blocked_operation["value"] == "get":
                blocked_operation["value"] = None
                first_arrived.set()
                assert release_first.wait(10), "test did not release the blocked GET"
            return result

        monkeypatch.setattr(file_api, "file_publication_lock", observed_guard)
        monkeypatch.setattr(scheduler, "_lock_project", observed_project_lock)

        # GET starts first and holds its project snapshot while the upload waits
        # at the shared publication lock. The read must return both old bytes and
        # the old token, never a mixed snapshot.
        blocked_operation["value"] = "get"
        guard_calls["count"] = 0
        old_revision = 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            get_future = pool.submit(client.get, file_endpoint, params={"path": path})
            assert first_arrived.wait(5)
            upload_future = pool.submit(_upload, client, upload_endpoint, "order.txt", b"upload after get\n")
            assert waiter_arrived.wait(5)
            assert not upload_future.done()
            release_first.set()
            old_read = get_future.result(timeout=10)
            uploaded = upload_future.result(timeout=10)
        assert old_read.status_code == uploaded.status_code == 200
        assert old_read.json() == {
            "path": path, "content": "initial\n", "revision": old_revision, "origin": "user_edited",
        }
        assert uploaded.json() == {
            "path": path, "size": len(b"upload after get\n"), "origin": "user_import",
        }
        after_upload = ok(client.get(file_endpoint, params={"path": path}))
        assert (after_upload["content"], after_upload["revision"], after_upload["origin"]) == (
            "upload after get\n", old_revision + 1, "user_import"
        )

        # PUT wins the publication guard before upload. Upload follows its
        # committed revision and becomes the next revision.
        first_arrived.clear()
        waiter_arrived.clear()
        release_first.clear()
        guard_calls["count"] = 0
        blocked_operation["value"] = "put"
        # The PUT hook pauses after acquiring the project row through a private
        # callback in managed_change, so install that callback for this order.
        real_touch = file_api.touch_dependents
        put_arrived = threading.Event()
        release_put = threading.Event()

        def pause_put(session, project_id, relative):
            real_touch(session, project_id, relative)
            if relative == path and (Path(os.environ["FOREST_DATA_DIR"]) / "projects" /
                                     project["id"] / path).read_bytes() == b"put before upload\n":
                put_arrived.set()
                assert release_put.wait(10), "test did not release the blocked PUT"

        monkeypatch.setattr(file_api, "touch_dependents", pause_put)
        guard_calls["count"] = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            put_future = pool.submit(client.put, file_endpoint, json={
                "path": path, "content": "put before upload\n",
                "expected_revision": after_upload["revision"],
            })
            assert put_arrived.wait(5)
            upload_future = pool.submit(_upload, client, upload_endpoint, "order.txt", b"upload after put\n")
            assert waiter_arrived.wait(5)
            assert not upload_future.done()
            release_put.set()
            put_response = put_future.result(timeout=10)
            upload_response = upload_future.result(timeout=10)
        monkeypatch.setattr(file_api, "touch_dependents", real_touch)
        assert put_response.status_code == upload_response.status_code == 200
        after_put_upload = ok(client.get(file_endpoint, params={"path": path}))
        assert (after_put_upload["content"], after_put_upload["revision"], after_put_upload["origin"]) == (
            "upload after put\n", after_upload["revision"] + 2, "user_import"
        )

        # Two upload handlers entering in a controlled order each increment once.
        first_arrived.clear()
        release_first.clear()
        waiter_arrived.clear()
        guard_calls["count"] = 0
        real_managed_change = file_api.managed_change
        upload_a_arrived = threading.Event()
        release_upload_a = threading.Event()

        def pause_first_upload(session, project_id, relative, attribution, action_id=None):
            real_managed_change(session, project_id, relative, attribution, action_id)
            if relative == path and attribution == "upload" and (
                Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"] / path
            ).read_bytes() == b"upload A\n":
                upload_a_arrived.set()
                assert release_upload_a.wait(10), "test did not release upload A"

        monkeypatch.setattr(file_api, "managed_change", pause_first_upload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_upload = pool.submit(_upload, client, upload_endpoint, "order.txt", b"upload A\n")
            assert upload_a_arrived.wait(5)
            second_upload = pool.submit(_upload, client, upload_endpoint, "order.txt", b"upload B\n")
            assert waiter_arrived.wait(5)
            assert not second_upload.done()
            release_upload_a.set()
            first_result = first_upload.result(timeout=10)
            second_result = second_upload.result(timeout=10)
        assert first_result.status_code == second_result.status_code == 200
        final = ok(client.get(file_endpoint, params={"path": path}))
        assert (final["content"], final["revision"], final["origin"]) == (
            "upload B\n", after_put_upload["revision"] + 2, "user_import"
        )
    finally:
        client.delete(f"/api/projects/{project['id']}")


@requires_disposable_database
def test_postgres_definite_abort_compensation_with_waiting_writer(api_client, monkeypatch):
    from psycopg import sql
    import psycopg
    import services.api.files as file_api
    from services.api.db import FileRevision, Session

    client = api_client
    project, file_endpoint, upload_endpoint = _project_file(client)
    path = "uploads/abort.txt"
    try:
        current = ok(client.put(file_endpoint, json={
            "path": path, "content": "prior bytes\n", "expected_revision": 0,
        }))
        destination = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"] / path
        destination.chmod(0o640)
        prior_permissions = destination.stat().st_mode & 0o7777

        real_touch = file_api.touch_dependents
        fail_kind = {"value": ""}

        def fail_dependency(session, project_id, relative):
            real_touch(session, project_id, relative)
            if fail_kind["value"] == "dependency":
                fail_kind["value"] = ""
                raise RuntimeError("private dependency invalidation abort")

        monkeypatch.setattr(file_api, "touch_dependents", fail_dependency)
        for kind in ("dependency", "flush"):
            fail_kind["value"] = kind
            if kind == "flush":
                from sqlalchemy import event

                def fail_flush(session, flush_context, instances):
                    owns_target_revision = any(
                        isinstance(row, FileRevision)
                        and row.project_id == project["id"]
                        and row.path == path
                        for row in [*session.new, *session.dirty]
                    )
                    if fail_kind["value"] == "flush" and owns_target_revision:
                        fail_kind["value"] = ""
                        raise psycopg.errors.CheckViolation("private flush abort")

                event.listen(Session.class_, "before_flush", fail_flush)
            response = client.post(
                upload_endpoint,
                files={"file": ("abort.txt", b"uncommitted upload\n", "text/plain")},
            )
            if kind == "flush":
                event.remove(Session.class_, "before_flush", fail_flush)
            assert response.status_code == 500, f"{kind} injection was not observed: {response.text}"
            assert destination.read_bytes() == b"prior bytes\n"
            assert destination.stat().st_mode & 0o7777 == prior_permissions
            after = ok(client.get(file_endpoint, params={"path": path}))
            assert (after["revision"], after["origin"], after["content"]) == (
                current["revision"], "user_edited", "prior bytes\n"
            )

        # An actual deferred PostgreSQL constraint trigger rejects COMMIT after
        # file bytes have been published and flushed. The next API writer blocks
        # at the publication guard until the definite failure is compensated.
        function_name = "forest_upload_abort_" + project["id"].replace("-", "")
        trigger_name = "forest_upload_abort_trigger_" + project["id"].replace("-", "")
        gate_key = abs(hash(project["id"])) % (2**62) + 1
        gate = psycopg.connect(os.environ["FOREST_DATABASE_URL"].replace(
            "postgresql+psycopg://", "postgresql://", 1
        ), autocommit=True)
        with gate:
            gate.execute(sql.SQL("""
                CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.project_id = {} AND NEW.path = {} AND NEW.origin = 'user_import' THEN
                        PERFORM pg_advisory_xact_lock({});
                        RAISE EXCEPTION 'private deferred file revision abort'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$
            """).format(
                sql.Identifier(function_name), sql.Literal(project["id"]),
                sql.Literal(path), sql.Literal(gate_key),
            ))
            gate.execute(sql.SQL("""
                CREATE CONSTRAINT TRIGGER {}
                AFTER INSERT OR UPDATE ON file_revisions
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION {}()
            """).format(sql.Identifier(trigger_name), sql.Identifier(function_name)))

            real_guard = file_api.file_publication_lock
            guard_calls = {"count": 0}
            guard_lock = threading.Lock()
            waiter_arrived = threading.Event()

            @contextmanager
            def observed_guard(project_id, relative):
                with guard_lock:
                    guard_calls["count"] += 1
                    if guard_calls["count"] == 2:
                        waiter_arrived.set()
                with real_guard(project_id, relative):
                    yield

            monkeypatch.setattr(file_api, "file_publication_lock", observed_guard)
            gate_pid = gate.execute("SELECT pg_backend_pid()").fetchone()[0]
            gate.execute("SELECT pg_advisory_lock(%s)", (gate_key,))
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    failed_upload = pool.submit(
                        client.post, upload_endpoint,
                        files={"file": ("abort.txt", b"commit abort\n", "text/plain")},
                    )
                    blocked_upload_pid = wait_for_commit_trigger(gate, gate_pid, timeout=10)
                    assert blocked_upload_pid is not None
                    later_put = pool.submit(client.put, file_endpoint, json={
                        "path": path, "content": "later writer\n",
                        "expected_revision": current["revision"],
                    })
                    assert waiter_arrived.wait(5)
                    assert not later_put.done()
                    gate.execute("SELECT pg_advisory_unlock(%s)", (gate_key,))
                    failed_response = failed_upload.result(timeout=10)
                    later_response = later_put.result(timeout=10)
                assert failed_response.status_code == 500
                assert later_response.status_code == 200
                final = ok(client.get(file_endpoint, params={"path": path}))
                assert (final["content"], final["revision"], final["origin"]) == (
                    "later writer\n", current["revision"] + 1, "user_edited"
                )
                assert destination.read_bytes() == b"later writer\n"
                with Session() as session:
                    row = session.query(FileRevision).filter_by(
                        project_id=project["id"], path=path
                    ).one()
                    assert (row.revision, row.origin) == (
                        current["revision"] + 1, "user_edited"
                    )
            finally:
                try:
                    gate.execute("SELECT pg_advisory_unlock(%s)", (gate_key,))
                except Exception:
                    pass
                gate.execute(sql.SQL("DROP TRIGGER {} ON file_revisions").format(sql.Identifier(trigger_name)))
                gate.execute(sql.SQL("DROP FUNCTION {}()").format(sql.Identifier(function_name)))
                gate.close()

        # A separate commit abort for a brand-new destination removes both the
        # published path and its uncommitted FileRevision row.
        new_path = "uploads/new-abort.txt"
        new_function = "forest_upload_new_abort_" + project["id"].replace("-", "")
        new_trigger = "forest_upload_new_abort_trigger_" + project["id"].replace("-", "")
        with psycopg.connect(os.environ["FOREST_DATABASE_URL"].replace(
            "postgresql+psycopg://", "postgresql://", 1
        ), autocommit=True) as connection:
            connection.execute(sql.SQL("""
                CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.project_id = {} AND NEW.path = {} THEN
                        RAISE EXCEPTION 'private new upload abort' USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$
            """).format(
                sql.Identifier(new_function), sql.Literal(project["id"]),
                sql.Literal(new_path),
            ))
            connection.execute(sql.SQL("""
                CREATE CONSTRAINT TRIGGER {}
                AFTER INSERT OR UPDATE ON file_revisions
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION {}()
            """).format(sql.Identifier(new_trigger), sql.Identifier(new_function)))
            new_response = client.post(
                upload_endpoint,
                files={"file": ("new-abort.txt", b"new uncommitted bytes\n", "text/plain")},
            )
            assert new_response.status_code == 500
            assert not (Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"] / new_path).exists()
            with Session() as session:
                assert session.query(FileRevision).filter_by(
                    project_id=project["id"], path=new_path
                ).one_or_none() is None
            connection.execute(sql.SQL("DROP TRIGGER {} ON file_revisions").format(sql.Identifier(new_trigger)))
            connection.execute(sql.SQL("DROP FUNCTION {}()").format(sql.Identifier(new_function)))
    finally:
        client.delete(f"/api/projects/{project['id']}")


def wait_for_commit_trigger(connection, blocker_pid, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = connection.execute(
            "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
            "AND %s=ANY(pg_blocking_pids(pid))", (blocker_pid,)
        ).fetchone()
        if row:
            return row[0]
        time.sleep(0.02)
    return None
