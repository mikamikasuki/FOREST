"""Upload preservation checks against isolated API databases and real files."""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from test_api import create, ok

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 1024 * 1024


def project_files(client):
    project = create(client)
    root = Path(os.environ["FOREST_DATA_DIR"]).resolve() / "projects" / project["id"]
    return project, root, f"/api/projects/{project['id']}/upload"


def assert_no_temporary_files(root):
    assert not list(root.rglob(".forest-upload-*.tmp"))


def case_rejected_overwrite_preserves_existing_file(client):
    project, root, endpoint = project_files(client)
    original = ok(client.post(endpoint, files={"file": ("same.txt", b"original")}))
    target = root / original["path"]
    before = target.stat()
    paper = ok(client.get(f"/api/papers/{project['id']}"))
    response = client.post(endpoint, files={"file": ("same.txt", b"x" * (LIMIT + 1))})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"
    assert target.exists(), "A rejected upload deleted the existing file"
    assert target.read_bytes() == b"original"
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_mode) == (before.st_ino, before.st_mtime_ns, before.st_mode)
    downloaded = client.get(f"/api/projects/{project['id']}/download", params={"path": original["path"]})
    assert downloaded.status_code == 200 and downloaded.content == b"original"
    assert ok(client.get(f"/api/papers/{project['id']}")) == paper
    assert_no_temporary_files(root)


def case_rejected_new_upload_leaves_no_partial_file(client):
    _, root, endpoint = project_files(client)
    response = client.post(endpoint, files={"file": ("new.txt", b"x" * (LIMIT + 1))})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"
    assert not (root / "uploads" / "new.txt").exists()
    assert_no_temporary_files(root)


def case_files_list_includes_entries_after_10000(client):
    project, root, _ = project_files(client)
    generated = root / "generated"
    generated.mkdir()
    for index in range(10_001):
        (generated / f"file-{index:05}.txt").touch()

    cursor = None
    paths = []
    page_count = 0
    while True:
        response = ok(client.get(f"/api/projects/{project['id']}/files", params={
            "limit": 1000, **({"cursor": cursor} if cursor else {})
        }))
        assert len(response["files"]) <= 1000
        paths.extend(item["path"] for item in response["files"])
        page_count += 1
        if not response["has_more"]:
            assert response["next_cursor"] is None
            break
        assert response["next_cursor"]
        cursor = response["next_cursor"]

    assert page_count > 10
    assert len(paths) == len(set(paths))
    assert "generated/file-10000.txt" in paths


def case_files_list_streams_a_bounded_page_and_replays_cursors(client):
    project, root, _ = project_files(client)
    generated = root / "streamed"
    generated.mkdir()
    for index in range(200):
        (generated / f"file-{index:03}.txt").touch()

    import importlib
    file_api = importlib.import_module("services.api.files")
    original_scandir = file_api.os.scandir
    visited = 0

    class CountedScandir:
        def __init__(self, directory):
            self.iterator = original_scandir(directory)
        def __enter__(self): return self
        def __exit__(self, *_): self.iterator.close()
        def __iter__(self): return self
        def __next__(self):
            nonlocal visited
            visited += 1
            return next(self.iterator)

    file_api.os.scandir = CountedScandir
    try:
        first = ok(client.get(f"/api/projects/{project['id']}/files", params={"limit": 5}))
    finally:
        file_api.os.scandir = original_scandir

    assert visited < 100, f"first page scanned {visited} entries instead of stopping at its bound"
    assert len(first["files"]) == 5 and first["has_more"]
    cursor = first["next_cursor"]
    second = ok(client.get(f"/api/projects/{project['id']}/files", params={"limit": 5, "cursor": cursor}))
    replay = ok(client.get(f"/api/projects/{project['id']}/files", params={"limit": 5, "cursor": cursor}))
    assert second == replay
    assert set(first["files"][i]["path"] for i in range(5)).isdisjoint(
        item["path"] for item in second["files"]
    )


def case_upload_preflight_reports_conflicts_outside_the_visible_page(client):
    project, root, _ = project_files(client)
    existing = root / "uploads" / "older-page.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep")
    (root / "uploads" / "folder-collision").mkdir()
    response = ok(client.post(
        f"/api/projects/{project['id']}/files/existing",
        json={"paths": [
            "uploads/older-page.txt",
            "uploads/folder-collision",
            "uploads/new.txt",
        ]},
    ))
    assert response == {
        "existing": ["uploads/older-page.txt"],
        "directories": ["uploads/folder-collision"],
    }


def case_file_list_cursor_cannot_cross_project_boundaries(client):
    first_project, root, _ = project_files(client)
    (root / "visible.txt").write_text("one")
    other_project, _, _ = project_files(client)
    first = ok(client.get(f"/api/projects/{first_project['id']}/files", params={"limit": 1}))
    assert first["has_more"]
    response = client.get(
        f"/api/projects/{other_project['id']}/files",
        params={"limit": 1, "cursor": first["next_cursor"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_FILE_CURSOR"


def case_accepted_overwrite_and_limit_boundary(client):
    project, root, endpoint = project_files(client)
    ok(client.post(endpoint, files={"file": ("same.txt", b"original")}))
    target = root / "uploads" / "same.txt"
    target.chmod(0o755)
    content = b"replacement" + b"x" * (LIMIT - len(b"replacement"))
    result = ok(client.post(endpoint, files={"file": ("same.txt", content)}))
    assert result == {"path": "uploads/same.txt", "size": LIMIT, "origin": "user_import"}
    assert target.read_bytes() == content
    assert target.stat().st_mode & 0o7777 == 0o755
    result = ok(client.post(endpoint, files={"file": ("same.txt", b"")}))
    assert result == {"path": "uploads/same.txt", "size": 0, "origin": "user_import"}
    assert target.read_bytes() == b""

    binary = b"\x00\xff\x80binary\x00"
    binary_result = ok(client.post(endpoint, files={"file": ("binary.bin", binary)}))
    assert binary_result == {
        "path": "uploads/binary.bin",
        "size": len(binary),
        "origin": "user_import",
    }
    binary_path = root / binary_result["path"]
    assert binary_path.read_bytes() == binary
    download = client.get(
        f"/api/projects/{project['id']}/download",
        params={"path": binary_result["path"]},
    )
    assert download.status_code == 200 and download.content == binary
    from services.api.db import FileRevision, Session

    with Session() as session:
        row = session.query(FileRevision).filter_by(
            project_id=project["id"],
            path="uploads/binary.bin",
        ).one()
        assert (row.revision, row.origin) == (1, "user_import")
    assert_no_temporary_files(root)


def case_interrupted_stream_preserves_file_and_cleans_temporary_file(client):
    from fastapi import UploadFile
    from services.api.files import upload

    project, root, endpoint = project_files(client)
    ok(client.post(endpoint, files={"file": ("same.txt", b"original")}))
    target = root / "uploads" / "same.txt"

    class InterruptedStream(io.BytesIO):
        def read(self, size=-1):
            assert target.read_bytes() == b"original", "Destination changed before upload completed"
            if self.tell():
                raise OSError("Upload stream interrupted")
            return super().read(size)

    with InterruptedStream(b"new content") as stream:
        incoming = UploadFile(file=stream, filename="same.txt")
        with pytest.raises(OSError, match="Upload stream interrupted"):
            asyncio.run(upload(project["id"], incoming, "uploads"))
    assert target.read_bytes() == b"original"
    assert_no_temporary_files(root)


def case_failed_replace_cleans_temporary_file(client):
    from fastapi import UploadFile
    from services.api.files import upload

    project, root, _ = project_files(client)
    target = root / "uploads" / "same.txt"
    target.mkdir(parents=True)
    marker = target / "keep.txt"
    marker.write_bytes(b"original")
    with io.BytesIO(b"new content") as stream:
        incoming = UploadFile(file=stream, filename="same.txt")
        with pytest.raises(OSError):
            asyncio.run(upload(project["id"], incoming, "uploads"))
    assert marker.read_bytes() == b"original"
    assert_no_temporary_files(root)


def case_upload_replacement_preserves_file_revision_guards(client):
    project, root, endpoint = project_files(client)
    file_endpoint = f"/api/projects/{project['id']}/file"

    initial_upload = client.post(
        endpoint, files={"file": ("observations.txt", b"initial bytes\n")}
    )
    initial = client.get(file_endpoint, params={"path": "uploads/observations.txt"})
    seed_put = client.put(
        file_endpoint,
        json={
            "path": "uploads/observations.txt",
            "content": "editor baseline\n",
            "expected_revision": initial.json()["revision"],
        },
    )
    current_put = client.put(
        file_endpoint,
        json={
            "path": "uploads/observations.txt",
            "content": "replacement bytes\n",
            "expected_revision": seed_put.json()["revision"],
        },
    )
    normal_stale_put = client.put(
        file_endpoint,
        json={
            "path": "uploads/observations.txt",
            "content": "ordinary stale write\n",
            "expected_revision": seed_put.json()["revision"],
        },
    )
    editor_snapshot = client.get(
        file_endpoint, params={"path": "uploads/observations.txt"}
    )
    replacement_upload = client.post(
        endpoint, files={"file": ("observations.txt", b"replacement bytes\n")}
    )
    stale_editor_put = client.put(
        file_endpoint,
        json={
            "path": "uploads/observations.txt",
            "content": "stale editor overwrote upload\n",
            "expected_revision": editor_snapshot.json()["revision"],
        },
    )
    after_stale_put = client.get(
        file_endpoint, params={"path": "uploads/observations.txt"}
    )
    new_upload = client.post(
        endpoint, files={"file": ("new.txt", b"new file bytes\n")}
    )
    new_file = client.get(file_endpoint, params={"path": "uploads/new.txt"})
    fresh_put = client.put(
        file_endpoint,
        json={
            "path": "uploads/observations.txt",
            "content": "fresh editor save\n",
            "expected_revision": after_stale_put.json()["revision"],
        },
    )
    force_put = client.put(
        file_endpoint,
        json={"path": "uploads/observations.txt", "content": "forced save\n"},
    )

    legacy_path = root / "uploads" / "legacy.txt"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"legacy bytes\n")
    legacy_before = client.get(
        file_endpoint, params={"path": "uploads/legacy.txt"}
    )
    legacy_upload = client.post(
        endpoint, files={"file": ("legacy.txt", b"new legacy bytes\n")}
    )
    legacy_after = client.get(
        file_endpoint, params={"path": "uploads/legacy.txt"}
    )

    assert initial_upload.status_code == 200
    assert initial.json()["revision"] == 1
    assert seed_put.status_code == 200 and seed_put.json()["revision"] == 2
    assert current_put.status_code == 200
    assert normal_stale_put.status_code == 409
    assert replacement_upload.status_code == 200
    assert replacement_upload.json() == {
        "path": "uploads/observations.txt",
        "size": len(b"replacement bytes\n"),
        "origin": "user_import",
    }
    assert stale_editor_put.status_code == 409
    assert after_stale_put.status_code == 200
    assert after_stale_put.json() == {
        "path": "uploads/observations.txt",
        "content": "replacement bytes\n",
        "revision": editor_snapshot.json()["revision"] + 1,
        "origin": "user_import",
    }
    assert new_upload.status_code == 200
    assert new_file.json()["revision"] == 1
    assert new_file.json()["origin"] == "user_import"
    assert fresh_put.status_code == 200
    assert fresh_put.json()["origin"] == "user_edited"
    assert force_put.status_code == 200
    assert force_put.json()["origin"] == "user_edited"
    assert legacy_before.json()["revision"] == 0
    assert legacy_upload.status_code == 200
    assert legacy_after.json()["revision"] == 1
    assert legacy_after.json()["origin"] == "user_import"


def case_definite_upload_failures_restore_published_files(client):
    from fastapi import UploadFile
    from sqlalchemy import event
    import services.api.files as file_api
    from services.api.db import FileRevision, Session

    project, root, _ = project_files(client)
    endpoint = f"/api/projects/{project['id']}/upload"
    file_endpoint = f"/api/projects/{project['id']}/file"
    path = "uploads/rollback.txt"
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old bytes\n")
    target.chmod(0o640)
    ok(client.put(file_endpoint, json={"path": path, "content": "old bytes\n", "expected_revision": 0}))
    before = ok(client.get(file_endpoint, params={"path": path}))
    prior_mode = target.stat().st_mode & 0o7777

    def invoke_upload(name, body=b"new bytes\n"):
        incoming = UploadFile(file=io.BytesIO(body), filename=name)
        return asyncio.run(file_api.upload(project["id"], incoming, "uploads"))

    failing_upload = {"enabled": False}
    original_touch = file_api.touch_dependents
    failure_kind = {"value": ""}

    def fail_dependency(session, project_id, relative):
        original_touch(session, project_id, relative)
        if failure_kind["value"] == "dependency" and failing_upload["enabled"]:
            failing_upload["enabled"] = False
            raise RuntimeError("injected dependency invalidation failure")

    def fail_flush(session, flush_context, instances):
        if failing_upload["enabled"] and failure_kind["value"] == "flush":
            failing_upload["enabled"] = False
            raise sqlite3.IntegrityError("injected definite flush abort")

    def fail_commit(session):
        if failing_upload["enabled"] and failure_kind["value"] == "commit":
            failing_upload["enabled"] = False
            raise sqlite3.IntegrityError("injected definite commit abort")

    file_api.touch_dependents = fail_dependency
    event.listen(Session.class_, "before_flush", fail_flush)
    event.listen(Session.class_, "before_commit", fail_commit)
    try:
        for kind in ("dependency", "flush"):
            failure_kind["value"] = kind
            failing_upload["enabled"] = True
            with pytest.raises((RuntimeError, sqlite3.IntegrityError), match="injected"):
                invoke_upload("rollback.txt")
            after = ok(client.get(file_endpoint, params={"path": path}))
            assert after == before
            assert target.read_bytes() == b"old bytes\n"
            assert target.stat().st_mode & 0o7777 == prior_mode
            with Session() as session:
                row = session.query(FileRevision).filter_by(
                    project_id=project["id"], path=path
                ).one()
                assert (row.revision, row.origin) == (
                    before["revision"], before["origin"]
                )

        restore_arrived = threading.Event()
        release_restore = threading.Event()
        waiter_arrived = threading.Event()
        guard_calls = {"count": 0}
        original_guard = file_api.file_publication_lock
        original_restore = file_api._restore_file_publication

        @contextmanager
        def observed_guard(project_id, relative):
            guard_calls["count"] += 1
            if guard_calls["count"] == 2:
                waiter_arrived.set()
            with original_guard(project_id, relative):
                yield

        def paused_restore(path, backup, existed):
            restore_arrived.set()
            assert release_restore.wait(10), "test did not release file compensation"
            original_restore(path, backup, existed)

        def asynchronous_upload(filename):
            incoming = UploadFile(file=io.BytesIO(b"new bytes\n"), filename=filename)
            return asyncio.run(file_api.upload(project["id"], incoming, "uploads"))

        file_api.file_publication_lock = observed_guard
        file_api._restore_file_publication = paused_restore
        failure_kind["value"] = "commit"
        failing_upload["enabled"] = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            failed_upload = pool.submit(asynchronous_upload, "rollback.txt")
            assert restore_arrived.wait(5), "commit abort did not reach compensation"
            later_put = pool.submit(client.put, file_endpoint, json={
                "path": path, "content": "later SQLite writer\n",
                "expected_revision": before["revision"],
            })
            assert waiter_arrived.wait(5), "later writer did not reach publication guard"
            assert not later_put.done(), "later writer ran before compensation completed"
            release_restore.set()
            with pytest.raises(sqlite3.IntegrityError, match="injected definite commit"):
                failed_upload.result(timeout=10)
            later_response = later_put.result(timeout=10)
        file_api.file_publication_lock = original_guard
        file_api._restore_file_publication = original_restore
        assert later_response.status_code == 200
        later_read = ok(client.get(file_endpoint, params={"path": path}))
        assert (later_read["content"], later_read["revision"], later_read["origin"]) == (
            "later SQLite writer\n", before["revision"] + 1, "user_edited"
        )
        assert target.read_bytes() == b"later SQLite writer\n"

        failure_kind["value"] = "flush"
        failing_upload["enabled"] = True
        with pytest.raises(sqlite3.IntegrityError, match="injected definite flush"):
            invoke_upload("new-after-abort.txt")
        new_target = root / "uploads" / "new-after-abort.txt"
        assert not new_target.exists()
        with Session() as session:
            assert session.query(FileRevision).filter_by(
                project_id=project["id"], path="uploads/new-after-abort.txt"
            ).one_or_none() is None
        assert not list(root.rglob(".forest-upload-*.tmp"))
        assert not list(root.rglob(".forest-upload-*.backup"))
    finally:
        event.remove(Session.class_, "before_flush", fail_flush)
        event.remove(Session.class_, "before_commit", fail_commit)
        file_api.touch_dependents = original_touch
        if "original_guard" in locals():
            file_api.file_publication_lock = original_guard
            file_api._restore_file_publication = original_restore


def case_file_publication_orders_reads_and_writes(client):
    from fastapi import UploadFile
    import services.api.files as file_api
    import services.worker.scheduler as scheduler

    project, root, endpoint = project_files(client)
    file_endpoint = f"/api/projects/{project['id']}/file"
    path = "uploads/order.txt"
    initial = ok(client.put(
        file_endpoint, json={"path": path, "content": "initial\n", "expected_revision": 0}
    ))
    assert initial["revision"] == 1

    real_guard = file_api.file_publication_lock
    real_project_lock = scheduler._lock_project
    real_managed_change = file_api.managed_change
    real_touch_dependents = file_api.touch_dependents
    block = {"origin": None, "content": None}
    publication_arrived = threading.Event()
    release_publication = threading.Event()
    waiter_arrived = threading.Event()
    guard_calls = {"count": 0}
    patch_guard = {"enabled": False}
    block_get = {"enabled": False}
    get_arrived = threading.Event()
    release_get = threading.Event()

    @contextmanager
    def observed_guard(project_id, relative):
        if patch_guard["enabled"] and relative == path:
            guard_calls["count"] += 1
            if guard_calls["count"] == 2:
                waiter_arrived.set()
        with real_guard(project_id, relative):
            yield

    def pause_after_managed_change(session, project_id, relative, attribution, action_id=None):
        real_managed_change(session, project_id, relative, attribution, action_id)
        if relative == path and attribution == block["origin"] and (root / path).read_bytes() == block["content"]:
            publication_arrived.set()
            assert release_publication.wait(10), "test did not release the first publication"

    def pause_after_touch(session, project_id, relative):
        real_touch_dependents(session, project_id, relative)
        if relative == path and block["origin"] == "owner_editor" and (root / path).read_bytes() == block["content"]:
            publication_arrived.set()
            assert release_publication.wait(10), "test did not release the first publication"

    def pause_get_after_project_lock(session, project_id):
        result = real_project_lock(session, project_id)
        if block_get["enabled"]:
            block_get["enabled"] = False
            get_arrived.set()
            assert release_get.wait(10), "test did not release the blocked GET"
        return result

    file_api.file_publication_lock = observed_guard
    file_api.managed_change = pause_after_managed_change
    file_api.touch_dependents = pause_after_touch
    scheduler._lock_project = pause_get_after_project_lock

    def upload(content, name="order.txt"):
        return client.post(endpoint, files={"file": (name, content, "text/plain")})

    def start_ordered_pair(first, second, expected_revision):
        publication_arrived.clear()
        release_publication.clear()
        waiter_arrived.clear()
        guard_calls["count"] = 0
        patch_guard["enabled"] = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(first, expected_revision)
            assert publication_arrived.wait(5), "first writer did not reach the publication barrier"
            second_future = pool.submit(second, expected_revision)
            assert waiter_arrived.wait(5), "second writer did not reach the shared file guard"
            assert not second_future.done(), "second writer escaped the first publication lock"
            release_publication.set()
            first_response = first_future.result(timeout=10)
            second_response = second_future.result(timeout=10)
        patch_guard["enabled"] = False
        return first_response, second_response

    try:
        # GET holds the publication guard after taking the project lock. A
        # replacement waits until both old bytes and their old revision have
        # been read, rather than returning a mixed bytes/token pair.
        block_get["enabled"] = True
        patch_guard["enabled"] = True
        guard_calls["count"] = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            get_future = pool.submit(client.get, file_endpoint, params={"path": path})
            assert get_arrived.wait(5), "GET did not reach the project-lock barrier"
            upload_future = pool.submit(upload, b"GET-first upload\n")
            assert waiter_arrived.wait(5), "upload did not reach the shared file guard"
            assert not upload_future.done(), "upload escaped the GET publication lock"
            release_get.set()
            old_read = get_future.result(timeout=10)
            upload_result = upload_future.result(timeout=10)
        patch_guard["enabled"] = False
        assert old_read.status_code == upload_result.status_code == 200
        assert old_read.json() == {
            "path": path,
            "content": "initial\n",
            "revision": initial["revision"],
            "origin": "user_edited",
        }
        read_after_get_upload = ok(client.get(file_endpoint, params={"path": path}))
        assert read_after_get_upload == {
            "path": path,
            "content": "GET-first upload\n",
            "revision": initial["revision"] + 1,
            "origin": "user_import",
        }

        stale_snapshot = ok(client.get(file_endpoint, params={"path": path}))
        block.update(origin="upload", content=b"upload first\n")
        upload_first, stale_put = start_ordered_pair(
            lambda _revision: upload(b"upload first\n"),
            lambda revision: client.put(file_endpoint, json={
                "path": path, "content": "stale put\n", "expected_revision": revision
            }),
            stale_snapshot["revision"],
        )
        assert upload_first.status_code == 200
        assert stale_put.status_code == 409
        read_after_upload = ok(client.get(file_endpoint, params={"path": path}))
        assert read_after_upload == {
            "path": path,
            "content": "upload first\n",
            "revision": stale_snapshot["revision"] + 1,
            "origin": "user_import",
        }

        block.update(origin="owner_editor", content=b"put first\n")
        put_first, upload_second = start_ordered_pair(
            lambda revision: client.put(file_endpoint, json={
                "path": path, "content": "put first\n", "expected_revision": revision
            }),
            lambda _revision: upload(b"upload second\n"),
            read_after_upload["revision"],
        )
        assert put_first.status_code == 200
        assert upload_second.status_code == 200
        read_after_put_upload = ok(client.get(file_endpoint, params={"path": path}))
        assert read_after_put_upload["content"] == "upload second\n"
        assert read_after_put_upload["revision"] == read_after_upload["revision"] + 2
        assert read_after_put_upload["origin"] == "user_import"

        block.update(origin="upload", content=b"upload A\n")
        upload_a, upload_b = start_ordered_pair(
            lambda _revision: upload(b"upload A\n"),
            lambda _revision: upload(b"upload B\n"),
            read_after_put_upload["revision"],
        )
        assert upload_a.status_code == upload_b.status_code == 200
        final = ok(client.get(file_endpoint, params={"path": path}))
        assert final == {
            "path": path,
            "content": "upload B\n",
            "revision": read_after_put_upload["revision"] + 2,
            "origin": "user_import",
        }
    finally:
        patch_guard["enabled"] = False
        release_get.set()
        release_publication.set()
        file_api.file_publication_lock = real_guard
        file_api.managed_change = real_managed_change
        file_api.touch_dependents = real_touch_dependents
        scheduler._lock_project = real_project_lock


def case_second_process_reads_committed_revision_after_publication_guard(client):
    import json
    from services.api.files import file_publication_lock

    project, _, upload_endpoint = project_files(client)
    ok(client.post(upload_endpoint, files={'file': ('persisted.txt', b'persisted content')}))
    endpoint = f"/api/projects/{project['id']}/file"
    before = ok(client.get(endpoint, params={'path': 'uploads/persisted.txt'}))
    script = '''import json, sys
from services.api.files import read_file
print('ready', flush=True)
print(json.dumps(read_file(sys.argv[1], sys.argv[2])), flush=True)
'''
    reader = None
    try:
        with file_publication_lock(project['id'], 'uploads/persisted.txt'):
            reader = subprocess.Popen([sys.executable, '-c', script, project['id'], 'uploads/persisted.txt'],
                                      cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            assert reader.stdout.readline().strip() == 'ready'
            with pytest.raises(subprocess.TimeoutExpired): reader.communicate(timeout=.2)
        output, errors = reader.communicate(timeout=10)
        assert reader.returncode == 0, errors
        assert json.loads(output.strip()) == before
        assert before['revision'] == 1 and before['origin'] == 'user_import'
    finally:
        if reader is not None and reader.poll() is None:
            reader.kill()
            reader.communicate(timeout=5)


def case_pending_workspace_rejects_all_owner_writes(client):
    from services.api.db import Session, Branch, FileRevision

    project, root, _ = project_files(client)
    endpoint = f"/api/projects/{project['id']}/file"
    with Session.begin() as session:
        branch = session.query(Branch).filter_by(project_id=project['id']).one()
        workspace = branch.workspace
        branch.extra = {**branch.extra, 'workspace_intervention': 'pending-copy'}
    target = root / workspace / 'protected.txt'
    target.write_text('original')
    relative = str(target.relative_to(root))
    requests = [
        lambda: client.put(endpoint, json={'path': relative, 'content': 'changed', 'expected_revision': 0}),
        lambda: client.post(endpoint.rsplit('/', 1)[0] + '/upload', params={'directory': workspace},
                            files={'file': ('protected.txt', b'changed')}),
        lambda: client.delete(endpoint, params={'path': relative}),
        lambda: client.post(endpoint + '/rename', json={'path': relative, 'new_path': 'moved.txt'}),
        lambda: client.post(endpoint + '/rename', json={'path': 'outside.txt', 'new_path': workspace + '/moved.txt'}),
        lambda: client.put(endpoint, json={'path': workspace + '/new/nested/file.txt', 'content': 'changed'}),
        lambda: client.post(endpoint.rsplit('/', 1)[0] + '/upload', params={'directory': workspace + '/new/nested'},
                            files={'file': ('file.txt', b'changed')}),
    ]
    (root / 'outside.txt').write_text('outside')
    for request in requests:
        response = request()
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'WORKSPACE_PENDING'
        assert target.read_text() == 'original'
        assert not (root / workspace / 'new').exists()
    with Session() as session:
        assert session.query(FileRevision).filter_by(project_id=project['id']).count() == 0
    assert not list(root.rglob('.forest-edit-*'))
    assert not list(root.rglob('.forest-upload-*'))


def case_rename_and_delete_wait_for_failed_publication_recovery(client):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from services.api import files as file_api

    project, root, upload_endpoint = project_files(client)
    endpoint = f"/api/projects/{project['id']}/file"
    real_restore = file_api._restore_file_publication
    real_guard = file_api.file_publication_lock
    real_touch = file_api.touch_dependents
    for operation in ('rename', 'delete'):
        path = 'uploads/' + operation + '/original.txt'
        saved = ok(client.put(endpoint, json={'path': path, 'content': 'original', 'expected_revision': 0}))
        restoring = threading.Event()
        release = threading.Event()
        waiting = threading.Event()
        calls = 0
        inject_failure = True

        @contextmanager
        def observed_guard(project_id, relative):
            nonlocal calls
            calls += 1
            if calls == 2: waiting.set()
            with real_guard(project_id, relative): yield

        def failed_touch(session, project_id, relative):
            nonlocal inject_failure
            real_touch(session, project_id, relative)
            if relative == path and inject_failure:
                inject_failure = False
                raise RuntimeError('publication failed')

        def paused_restore(target, backup, existed):
            restoring.set()
            assert release.wait(10)
            real_restore(target, backup, existed)

        def publish():
            from fastapi import UploadFile
            asyncio.run(file_api.upload(project['id'], UploadFile(file=io.BytesIO(b'failed replacement'),
                        filename='original.txt'), 'uploads/' + operation))

        file_api.file_publication_lock = observed_guard
        file_api.touch_dependents = failed_touch
        file_api._restore_file_publication = paused_restore
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                failed = pool.submit(publish)
                assert restoring.wait(5)
                if operation == 'rename':
                    later = pool.submit(client.post, endpoint + '/rename', json={
                        'path': 'uploads/rename', 'new_path': 'uploads/moved'})
                else:
                    later = pool.submit(client.delete, endpoint, params={'path': 'uploads/delete'})
                assert waiting.wait(5)
                assert not later.done()
                release.set()
                with pytest.raises(RuntimeError, match='publication failed'): failed.result(timeout=10)
                ok(later.result(timeout=10))
            assert not (root / path).exists()
            if operation == 'rename':
                moved = ok(client.get(endpoint, params={'path': 'uploads/moved/original.txt'}))
                assert moved['content'] == 'original'
                assert moved['revision'] == saved['revision'] + 1
            else:
                assert not (root / 'uploads/delete').exists()
        finally:
            release.set()
            file_api.file_publication_lock = real_guard
            file_api.touch_dependents = real_touch
            file_api._restore_file_publication = real_restore
    assert not list(root.rglob('.forest-upload-*'))


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_file_upload(tmp_path, case):
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"),
           "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "upload.sqlite"),
           "FOREST_OWNER_TOKEN": "upload-test-owner", "FOREST_MODEL": "",
           "FOREST_MAX_UPLOAD_MB": "1", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app

    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()["case_" + selected](client)
