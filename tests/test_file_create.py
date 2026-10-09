"""New-file intent is distinct from the compatible editor PUT."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_api import create, ok

ROOT = Path(__file__).resolve().parents[1]


def project_files(client):
    project = create(client)
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    return root, f"/api/projects/{project['id']}/file"


def case_occupied_paths(client):
    root, endpoint = project_files(client)
    for tracked in (False, True):
        for content in ("", "existing user content\n"):
            path = f"notes/{tracked}-{bool(content)}.txt"
            if tracked:
                ok(client.put(endpoint, json={"path": path, "content": content}))
            else:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            before = ok(client.get(endpoint, params={"path": path}))
            response = client.put(endpoint, json={
                "path": path, "content": "", "create_only": True,
            })
            after = ok(client.get(endpoint, params={"path": path}))
            assert after == before, "New-file creation altered existing bytes or revision"
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "FILE_EXISTS"
    (root / "occupied-directory").mkdir()
    response = client.put(endpoint, json={
        "path": "occupied-directory", "content": "", "create_only": True,
    })
    assert response.status_code == 409, response.text
    assert (root / "occupied-directory").is_dir()
    assert not list(root.glob(".forest-edit-*"))


def case_create_edit_and_recreate(client):
    root, endpoint = project_files(client)
    path = "notes/new.txt"
    created = ok(client.put(endpoint, json={
        "path": path, "content": "", "create_only": True,
    }))
    assert created["revision"] == 1
    edited = ok(client.put(endpoint, json={
        "path": path, "content": "edited", "expected_revision": created["revision"],
    }))
    assert edited["revision"] == 2
    stale = client.put(endpoint, json={
        "path": path, "content": "stale", "expected_revision": created["revision"],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert ok(client.get(endpoint, params={"path": path}))["content"] == "edited"
    # Legacy intentional writes without a revision retain their API behavior.
    legacy = ok(client.put(endpoint, json={"path": path, "content": "intentional"}))
    assert legacy["revision"] == 3
    ok(client.delete(endpoint, params={"path": path}))
    recreated = ok(client.put(endpoint, json={
        "path": path, "content": "", "create_only": True,
    }))
    assert recreated["revision"] > legacy["revision"]
    assert (root / path).read_text() == ""
    assert client.put(endpoint, json={
        "path": path, "content": "stale", "expected_revision": legacy["revision"],
    }).status_code == 409
    assert not list(root.glob(".forest-edit-*"))


def case_concurrent_creates(client):
    from services.api import files as file_api

    root, endpoint = project_files(client)
    path = "notes/racing.txt"
    real_publish = file_api._publish_file
    staged = threading.Barrier(2)

    def publish_together(*args, **kwargs):
        # Both requests have staged their bytes before either publishes.
        staged.wait(timeout=5)
        return real_publish(*args, **kwargs)

    file_api._publish_file = publish_together
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.put, endpoint, json={
                "path": path, "content": content, "create_only": True,
            }) for content in ("first", "second")]
            responses = [future.result(timeout=10) for future in futures]
    finally:
        file_api._publish_file = real_publish
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(i for i, response in enumerate(responses) if response.status_code == 200)
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["detail"]["code"] == "FILE_EXISTS"
    after = ok(client.get(endpoint, params={"path": path}))
    assert after["content"] == ("first", "second")[winner]
    assert after["revision"] == 1
    assert not list(root.glob(".forest-edit-*"))


CASES = ["occupied_paths", "create_edit_and_recreate", "concurrent_creates"]


@pytest.mark.parametrize("case", CASES)
def test_isolated_file_create(tmp_path, case):
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"),
           "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "create.sqlite"),
           "FOREST_OWNER_TOKEN": "create-test-owner", "FOREST_MODEL": "",
           "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--case", case],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app

    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()["case_" + selected](client)
