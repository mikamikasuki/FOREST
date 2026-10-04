"""Upload preservation checks against isolated API databases and real files."""
from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import subprocess
import sys

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


def case_accepted_overwrite_and_limit_boundary(client):
    _, root, endpoint = project_files(client)
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
