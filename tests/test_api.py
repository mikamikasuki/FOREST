"""Real FastAPI/SQLAlchemy integration in isolated interpreter/database processes.

No production module is imported in the pytest parent: each scenario gets its
own temporary FOREST_DATA_DIR and database before Settings or SQLAlchemy load.
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text[:4000]}"
    return response.json()


def await_intervention(client, identifier):
    import time
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        receipt = ok(client.get('/api/interventions/' + identifier))
        if receipt['status'] == 'applied': return receipt
        time.sleep(.02)
    raise AssertionError(receipt)


def create(client):
    return ok(client.post("/api/projects", json={"name": "Integration research", "goal": "Compare measured results", "budget": {"max_runs": 50, "seconds": 300, "allow_paid": False}}))


def graph(client, project):
    return ok(client.get(f"/api/projects/{project['id']}/graph"))


def command(client, project, operation, targets=None, **params):
    g = graph(client, project)
    return client.post(f"/api/projects/{project['id']}/graph/commands", json={"request_id": str(uuid4()), "expected_revision": g["revision"], "operation": operation, "targets": targets or [], "params": params})


def add_node(client, project, **fields):
    result = ok(command(client, project, "add_node", title="Baseline", type="experiment", **fields))
    return result["graph"]["nodes"][-1]


def case_bootstrap_and_persistence(client, app):
    health = ok(client.get("/api/health"))
    assert health["database"] == "sqlite"
    p = create(client)
    n = add_node(client, p, instructions="Reproduce the baseline")
    assert "forest_owner" in client.cookies
    renamed = ok(client.patch(f"/api/projects/{p['id']}", json={"name": "Revised title", "goal": "New controlled target", "expected_revision": 1}))
    assert renamed["revision"] == 2
    g = graph(client, p)
    assert g["goal"] == "New controlled target"
    assert g["nodes"][0]["instructions"] == "Reproduce the baseline"
    packet = ok(client.get(f"/api/nodes/{n['id']}/context"))
    assert packet["controls"]["goal"] == "New controlled target"
    from services.api.db import Session, Node, Project
    with Session() as session:
        assert session.get(Project, p["id"]).name == "Revised title"
        assert session.get(Node, n["id"]).instructions == "Reproduce the baseline"
    assert ok(client.patch(f"/api/projects/{p['id']}", json={"archived": True}))["archived"]
    assert ok(client.get("/api/projects", params={"archived": True}))[0]["id"] == p["id"]
    ok(client.delete(f"/api/projects/{p['id']}"))
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def case_graph_receipts_cycles_and_schema(client, app):
    p = create(client)
    path = f"/api/projects/{p['id']}/graph/commands"
    body = {"request_id": "one-logical-add", "expected_revision": 0, "operation": "add_node", "params": {"title": "A", "type": "experiment"}, "run": False}
    first = ok(client.post(path, json=body))
    assert ok(client.post(path, json=body)) == first
    assert len(graph(client, p)["nodes"]) == 1
    bad = {**body, "request_id": "new-stale-request"}
    conflict = client.post(path, json=bad)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "REVISION_CONFLICT"
    n1 = first["graph"]["nodes"][0]
    n2 = add_node(client, p)
    ok(command(client, p, "add_dependency", source=n1["id"], target=n2["id"]))
    cycle = command(client, p, "add_dependency", source=n2["id"], target=n1["id"])
    assert cycle.status_code == 422 and cycle.json()["detail"]["code"] == "dependency_cycle"
    assert len(graph(client, p)["edges"]) == 1
    assert client.post(path, json={"request_id": "no-revision", "operation": "add_node"}).status_code == 422
    assert client.post(path, json={**body, "unknown": "field"}).status_code == 422
    assert client.post(path, json={**body, "project_id": str(uuid4())}).status_code == 422
    preview = ok(client.post(path.replace("commands", "preview"), json={**body, "request_id": "preview", "expected_revision": graph(client, p)["revision"]}))
    assert preview["changed_nodes"]
    assert len(graph(client, p)["nodes"]) == 2


def case_concurrent_edit_collision(client, app):
    p = create(client)
    n = add_node(client, p)
    def edit(index):
        return client.post(f"/api/projects/{p['id']}/graph/commands", json={"request_id": f"window-{index}", "expected_revision": 1,
                           "operation": "edit_node", "targets": [n["id"]], "params": {"instructions": f"change-{index}"}})
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(edit, range(6)))
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409, 409, 409], [(r.status_code, r.text) for r in responses]
    winner = next(i for i, r in enumerate(responses) if r.status_code == 200)
    assert graph(client, p)["nodes"][0]["instructions"] == f"change-{winner}"
    assert graph(client, p)["revision"] == 2


def case_fork_merge_undo_and_files(client, app):
    p = create(client)
    n = add_node(client, p)
    main = graph(client, p)["branches"][0]
    path = main["workspace"] + "/model.py"
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": path, "content": "base\n", "expected_revision": 0}))
    fork_result = ok(command(client, p, "fork_branch", [n["id"]], name="Candidate"))
    await_intervention(client, fork_result['intervention']['id'])
    fork = fork_result["graph"]["branches"][-1]
    fork_path = fork["workspace"] + "/model.py"
    assert ok(client.get(f"/api/projects/{p['id']}/file", params={"path": fork_path}))["content"] == "base\n"
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": path, "content": "left\n", "expected_revision": 1}))
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": fork_path, "content": "right\n", "expected_revision": 0}))
    comparison = ok(client.get("/api/branches/compare", params={"left": main["id"], "right": fork["id"]}))
    assert comparison["mode"] == "three_way"
    assert comparison["files"][0]["status"] == "conflict"
    collision = command(client, p, "merge_branches", left=main["id"], right=fork["id"])
    assert collision.status_code == 409
    result = ok(command(client, p, "merge_branches", left=main["id"], right=fork["id"], resolution={"files": {"model.py": {"content": "combined\n"}}}))
    await_intervention(client, result['intervention']['id'])
    merged = result["graph"]["branches"][-1]
    assert ok(client.get(f"/api/projects/{p['id']}/file", params={"path": merged["workspace"] + "/model.py"}))["content"] == "combined\n"
    undone = ok(command(client, p, "undo"))
    assert len(undone["graph"]["branches"]) == 2
    assert "_history" not in undone["graph"]
    redone = ok(command(client, p, "redo"))
    assert len(redone["graph"]["branches"]) == 3
    assert redone["graph"]["branches"][-1]["requires_revalidation"]


def case_file_revision_and_escape(client, app):
    p = create(client)
    endpoint = f"/api/projects/{p['id']}/file"
    assert ok(client.put(endpoint, json={"path": "code/train.py", "content": "print(1)", "expected_revision": 0}))["revision"] == 1
    assert client.put(endpoint, json={"path": "code/train.py", "content": "lost", "expected_revision": 0}).status_code == 409
    assert ok(client.get(endpoint, params={"path": "code/train.py"}))["content"] == "print(1)"
    assert client.put(endpoint, json={"path": "../outside", "content": "escape"}).status_code == 403
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / p["id"]
    secret = root.parent / "outside.txt"
    secret.write_text("do not read")
    (root / "symlink.txt").symlink_to(secret)
    assert client.get(endpoint, params={"path": "symlink.txt"}).status_code == 403
    assert client.get(endpoint, params={"path": str(secret)}).status_code == 403
    ok(client.delete(endpoint, params={"path": "code/train.py"}))
    assert client.get(endpoint, params={"path": "code/train.py"}).status_code == 404


def case_delete_recreate_invalidates_stale_file_revisions(client, app):
    project = create(client)
    endpoint = f"/api/projects/{project['id']}/file"

    path = "notes/revision-aba.txt"
    original = ok(client.put(endpoint, json={
        "path": path, "content": "original bytes\n", "expected_revision": 0,
    }))
    assert original["revision"] == 1
    ok(client.delete(endpoint, params={"path": path}))
    stale_after_delete = client.put(endpoint, json={
        "path": path, "content": "stale editor\n",
        "expected_revision": original["revision"],
    })
    assert stale_after_delete.status_code == 409
    assert client.get(endpoint, params={"path": path}).status_code == 404
    workspace = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    (workspace / path).write_text("executor regenerated bytes\n")
    regenerated = ok(client.get(endpoint, params={"path": path}))
    assert regenerated == {
        "path": path,
        "content": "executor regenerated bytes\n",
        "revision": 2,
        "origin": "executor_or_import",
    }
    ok(client.delete(endpoint, params={"path": path}))
    recreated = ok(client.put(endpoint, json={
        "path": path, "content": "new bytes\n",
    }))
    assert recreated["revision"] == 4
    stale = client.put(endpoint, json={
        "path": path, "content": "stale editor\n", "expected_revision": original["revision"],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert ok(client.get(endpoint, params={"path": path})) == {
        "path": path, "content": "new bytes\n", "revision": 4, "origin": "user_edited",
    }

    directory = "results"
    tracked_path = f"{directory}/tracked.txt"
    tracked = ok(client.put(endpoint, json={
        "path": tracked_path, "content": "tracked original\n", "expected_revision": 0,
    }))
    untracked_path = f"{directory}/executor-output.txt"
    workspace = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    (workspace / directory / "executor-output.txt").write_text("executor original\n")
    untracked = ok(client.get(endpoint, params={"path": untracked_path}))
    assert untracked["revision"] == 0
    assert untracked["origin"] == "executor_or_import"

    ok(client.delete(endpoint, params={"path": directory}))
    stale_untracked_after_delete = client.put(endpoint, json={
        "path": untracked_path,
        "content": "stale executor editor\n",
        "expected_revision": untracked["revision"],
    })
    assert stale_untracked_after_delete.status_code == 409
    assert client.get(endpoint, params={"path": untracked_path}).status_code == 404
    tracked_new = ok(client.put(endpoint, json={
        "path": tracked_path, "content": "tracked replacement\n",
    }))
    untracked_new = ok(client.put(endpoint, json={
        "path": untracked_path, "content": "executor replacement\n",
    }))
    assert tracked_new["revision"] == 3
    assert untracked_new["revision"] == 2

    stale_tracked = client.put(endpoint, json={
        "path": tracked_path, "content": "stale tracked editor\n",
        "expected_revision": tracked["revision"],
    })
    stale_untracked = client.put(endpoint, json={
        "path": untracked_path, "content": "stale executor editor\n",
        "expected_revision": untracked["revision"],
    })
    assert stale_tracked.status_code == stale_untracked.status_code == 409
    assert ok(client.get(endpoint, params={"path": tracked_path}))["content"] == "tracked replacement\n"
    assert ok(client.get(endpoint, params={"path": untracked_path}))["content"] == "executor replacement\n"


def case_rename_invalidates_stale_untracked_file_path(client, app):
    project = create(client)
    endpoint = f"/api/projects/{project['id']}/file"
    old_path = "outputs/untracked-result.txt"
    new_path = "outputs/renamed-result.txt"
    project_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    source = project_root / old_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("executor result\n")

    opened = ok(client.get(endpoint, params={"path": old_path}))
    assert opened == {
        "path": old_path,
        "content": "executor result\n",
        "revision": 0,
        "origin": "executor_or_import",
    }
    ok(client.post(
        f"/api/projects/{project['id']}/file/rename",
        json={"path": old_path, "new_path": new_path},
    ))

    stale_save = client.put(endpoint, json={
        "path": old_path,
        "content": "stale editor write\n",
        "expected_revision": opened["revision"],
    })
    assert stale_save.status_code == 409
    assert stale_save.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert client.get(endpoint, params={"path": old_path}).status_code == 404
    assert ok(client.get(endpoint, params={"path": new_path})) == {
        "path": new_path,
        "content": "executor result\n",
        "revision": 0,
        "origin": "executor_or_import",
    }

    source_directory = "sources"
    destination_directory = "archive"
    tracked_source = f"{source_directory}/tracked.txt"
    untracked_source = f"{source_directory}/untracked.txt"
    tracked_destination = f"{destination_directory}/tracked.txt"
    previous_destination = ok(client.put(endpoint, json={
        "path": tracked_destination,
        "content": "old destination bytes\n",
        "expected_revision": 0,
    }))
    ok(client.delete(endpoint, params={"path": tracked_destination}))
    (project_root / destination_directory).rmdir()
    tracked = ok(client.put(endpoint, json={
        "path": tracked_source,
        "content": "source tracked bytes\n",
        "expected_revision": 0,
    }))
    untracked_file = project_root / untracked_source
    untracked_file.parent.mkdir(parents=True, exist_ok=True)
    untracked_file.write_text("source executor bytes\n")
    untracked = ok(client.get(endpoint, params={"path": untracked_source}))
    assert untracked["revision"] == 0

    ok(client.post(
        f"/api/projects/{project['id']}/file/rename",
        json={"path": source_directory, "new_path": destination_directory},
    ))
    assert client.put(endpoint, json={
        "path": tracked_source,
        "content": "stale tracked bytes\n",
        "expected_revision": tracked["revision"],
    }).status_code == 409
    assert client.put(endpoint, json={
        "path": untracked_source,
        "content": "stale executor bytes\n",
        "expected_revision": untracked["revision"],
    }).status_code == 409
    assert client.put(endpoint, json={
        "path": tracked_destination,
        "content": "stale destination bytes\n",
        "expected_revision": previous_destination["revision"],
    }).status_code == 409
    assert client.get(endpoint, params={"path": tracked_source}).status_code == 404
    assert client.get(endpoint, params={"path": untracked_source}).status_code == 404
    assert ok(client.get(endpoint, params={"path": tracked_destination})) == {
        "path": tracked_destination,
        "content": "source tracked bytes\n",
        "revision": 3,
        "origin": "user_edited",
    }
    assert ok(client.get(endpoint, params={"path": f"{destination_directory}/untracked.txt"})) == {
        "path": f"{destination_directory}/untracked.txt",
        "content": "source executor bytes\n",
        "revision": 0,
        "origin": "executor_or_import",
    }


def case_posix_backslash_file_revision_keys(client, app):
    assert os.name == "posix"
    project = create(client)
    endpoint = f"/api/projects/{project['id']}/file"
    project_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]

    # On POSIX this is one filename containing a backslash, distinct from the
    # nested path with the same spelling after an incorrect backslash rewrite.
    backslash_path = r"records\entry.txt"
    slash_path = "records/entry.txt"
    untracked = project_root / backslash_path
    untracked.parent.mkdir(parents=True, exist_ok=True)
    untracked.write_text("original backslash-name bytes\n")
    opened = ok(client.get(endpoint, params={"path": backslash_path}))
    assert opened["revision"] == 0

    ok(client.delete(endpoint, params={"path": backslash_path}))
    nested_create = ok(client.put(endpoint, json={
        "path": slash_path,
        "content": "independent nested file\n",
        "expected_revision": 0,
    }))
    assert nested_create["revision"] == 1

    stale = client.put(endpoint, json={
        "path": backslash_path,
        "content": "stale backslash editor\n",
        "expected_revision": opened["revision"],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert client.get(endpoint, params={"path": backslash_path}).status_code == 404
    assert ok(client.get(endpoint, params={"path": slash_path}))["content"] == "independent nested file\n"

    rename_source = r"rename\untracked.txt"
    rename_target = r"renamed\untracked.txt"
    source = project_root / rename_source
    source.write_text("rename source bytes\n")
    rename_opened = ok(client.get(endpoint, params={"path": rename_source}))
    assert rename_opened["revision"] == 0
    ok(client.post(
        f"/api/projects/{project['id']}/file/rename",
        json={"path": rename_source, "new_path": rename_target},
    ))
    stale_rename = client.put(endpoint, json={
        "path": rename_source,
        "content": "stale renamed editor\n",
        "expected_revision": rename_opened["revision"],
    })
    assert stale_rename.status_code == 409
    assert stale_rename.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert client.get(endpoint, params={"path": rename_source}).status_code == 404
    assert ok(client.get(endpoint, params={"path": rename_target}))["content"] == "rename source bytes\n"


def case_export_import_reference_roundtrip(client, app):
    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    run = ok(client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "original-run"}))
    source_times = {
        "created_at": "2024-05-30T09:00:00+00:00",
        "started_at": "2024-05-30T09:01:12.125000+00:00",
        "finished_at": "2024-05-30T09:01:17.750000+00:00",
    }
    from services.api.db import Session, TaskRun
    with Session.begin() as session:
        row = session.get(TaskRun, run["id"])
        row.created_at = source_times["created_at"]
        row.started_at = source_times["started_at"]
        row.finished_at = source_times["finished_at"]
    # An imported queued run must be visibly interrupted, never silently restarted.
    fig = ok(client.post("/api/figures", json={"project_id": p["id"], "title": "Metrics", "data": {"run_ids": [run["id"]], "metric": "accuracy"}}))
    paper = ok(client.get(f"/api/papers/{p['id']}"))
    ok(client.patch(f"/api/papers/{p['id']}", json={"expected_revision": paper["revision"], "data": {"source": paper["data"]["source"], "bindings": [{"figure_id": fig["id"], "run_id": run["id"]}]}}))
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": run["output_path"] + "/stdout.txt", "content": "actual saved output\n"}))
    archive = client.post(f"/api/projects/{p['id']}/export", json={})
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        archive_files = {item.filename: zipped.read(item.filename) for item in zipped.infolist()}
    manifest = json.loads(archive_files["forest-project.json"])
    manifest["runs"][0]["started_at"] = "not-an-ISO-timestamp"
    archive_files["forest-project.json"] = json.dumps(manifest).encode()
    invalid_archive = io.BytesIO()
    with zipfile.ZipFile(invalid_archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in archive_files.items():
            zipped.writestr(name, content)
    invalid = client.post("/api/projects/import", files={"file": ("invalid-time.zip", invalid_archive.getvalue(), "application/zip")})
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "INVALID_ARCHIVE"
    assert len(ok(client.get("/api/projects"))) == 1
    restored = ok(client.post("/api/projects/import", files={"file": ("project.zip", archive.content, "application/zip")}))
    new_graph = graph(client, restored)
    assert restored["id"] != p["id"] and new_graph["nodes"][0]["id"] != n["id"]
    new_runs = ok(client.get(f"/api/projects/{restored['id']}/runs"))
    assert new_runs[0]["status"] == "interrupted"
    assert {key: new_runs[0][key] for key in source_times} == source_times
    assert new_graph["nodes"][0]["execution_status"] == "interrupted"
    new_fig = ok(client.get("/api/figures", params={"project_id": restored["id"]}))[0]
    assert new_fig["data"]["run_ids"] == [new_runs[0]["id"]]
    new_paper = ok(client.get(f"/api/papers/{restored['id']}"))
    assert new_paper["data"]["bindings"] == [{"figure_id": new_fig["id"], "run_id": new_runs[0]["id"]}]
    assert ok(client.get(f"/api/runs/{new_runs[0]['id']}/output"))["text"] == "actual saved output\n"


def case_import_running_controller_requires_explicit_start(client, app, mode="assisted"):
    p = create(client)
    ok(client.patch(f"/api/projects/{p['id']}", json={"mode": mode}))
    source_branch_id = graph(client, p)["branches"][0]["id"]
    started = ok(client.post(f"/api/projects/{p['id']}/research/start", json={"autonomous": True, "branch_id": source_branch_id}))
    assert started["status"] == "running" and started["autonomous"] is True
    from services.worker.controller import advance_projects
    advance_projects()
    source_state = ok(client.get(f"/api/projects/{p['id']}/research"))
    assert source_state["controller"]["status"] == "running"
    assert source_state["controller"]["last_run"]
    assert source_state["active_runs"][0]["kind"] == "research_plan"

    archive = client.post(f"/api/projects/{p['id']}/export", json={})
    assert archive.status_code == 200
    imported = ok(client.post("/api/projects/import", files={"file": ("running-project.zip", archive.content, "application/zip")}))
    assert imported["mode"] == mode
    imported_id = imported["id"]
    imported_graph = graph(client, imported)
    imported_branch_id = imported_graph["branches"][0]["id"]
    before_tick = ok(client.get(f"/api/projects/{imported_id}/research"))
    imported_runs = ok(client.get(f"/api/projects/{imported_id}/runs"))
    assert before_tick["controller"]["status"] == "paused"
    assert before_tick["controller"]["autonomous"] is True
    assert imported_branch_id != source_branch_id
    assert before_tick["controller"]["branch_id"] == imported_branch_id
    assert "last_run" not in before_tick["controller"]
    assert before_tick["active_runs"] == []
    assert len(imported_runs) == 1 and imported_runs[0]["status"] == "interrupted"

    advance_projects()

    after_tick = ok(client.get(f"/api/projects/{imported_id}/research"))
    assert after_tick["controller"]["status"] == "paused"
    assert after_tick["active_runs"] == []
    assert ok(client.get(f"/api/projects/{imported_id}/runs")) == imported_runs
    source_after_tick = ok(client.get(f"/api/projects/{p['id']}/research"))
    assert source_after_tick["controller"]["status"] == "running"
    assert source_after_tick["active_runs"] == source_state["active_runs"]


def case_import_auto_mode_preserves_controller_pause_and_cleanup(client, app):
    case_import_running_controller_requires_explicit_start(client, app, mode="auto")


def case_export_does_not_follow_config_symlink(client, app):
    p = create(client)
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / p["id"]
    private = root.parent / "private.json"
    private.write_text(json.dumps({"private_marker": "OUTSIDE_PROJECT_SECRET"}))
    (root / "config.json").symlink_to(private)
    response = client.post(f"/api/projects/{p['id']}/export", json={})
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert not any(b"OUTSIDE_PROJECT_SECRET" in archive.read(name) for name in archive.namelist())


def case_unsafe_and_invalid_archive_rejected(client, app):
    p = create(client)
    content = client.post(f"/api/projects/{p['id']}/export", json={}).content
    source = zipfile.ZipFile(io.BytesIO(content))
    manifest = json.loads(source.read("forest-project.json"))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("forest-project.json", json.dumps(manifest))
        archive.writestr("files/../../outside", "escape")
    assert client.post("/api/projects/import", files={"file": ("unsafe.zip", out.getvalue())}).status_code == 400
    manifest["graph"]["edges"] = [{"id": str(uuid4()), "source": "deleted", "target": "also-deleted", "relation": "depends_on"}]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("forest-project.json", json.dumps(manifest))
    assert client.post("/api/projects/import", files={"file": ("invalid.zip", out.getvalue())}).status_code == 422
    assert len(ok(client.get("/api/projects"))) == 1


def case_visitors_shares_and_origin_denial(client, app):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    p = create(client)
    n = add_node(client, p, instructions="private instructions")
    share = ok(client.post(f"/api/projects/{p['id']}/share", json={"node_ids": [n["id"]]}))
    with TestClient(app, base_url="http://visitor.example", client=("198.51.100.12", 5000)) as visitor:
        assert visitor.get("/api/projects").status_code == 401
        assert visitor.post(f"/api/nodes/{n['id']}/run", json={}).status_code == 401
        assert visitor.get("/api/providers").status_code == 401
        shared = ok(visitor.get("/api/shares/" + share["token"]))
        assert shared["read_only"] and len(shared["nodes"]) == 1
        assert "private instructions" not in json.dumps(shared)
        with pytest.raises(WebSocketDisconnect) as blocked:
            with visitor.websocket_connect(f"/ws/projects/{p['id']}/terminal"):
                pytest.fail("Visitor terminal must not connect")
        assert blocked.value.code == 1008
    assert client.post("/api/projects", json={"name": "CSRF"}, headers={"origin": "https://evil.example"}).status_code == 403
    ok(client.delete("/api/shares/" + share["token"]))
    assert client.get("/api/shares/" + share["token"]).status_code == 404


def case_real_provider_failure_and_local_host(client, app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    provider = ok(client.post("/api/providers", json={"name": "Intentionally unavailable endpoint", "kind": "openai", "base_url": f"http://127.0.0.1:{port}/v1", "model": "test-no-model", "api_key": "TEST_ONLY_SECRET", "config": {"timeout": 1}}))
    configured=ok(client.post('/api/projects',json={'name':'Explicit provider selection','config':{'provider_id':provider['id']}}))
    assert configured['config']['provider_id']==provider['id']
    assert "api_key" not in provider and provider["has_key"]
    response = client.post(f"/api/providers/{provider['id']}/test")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "PROVIDER_CONNECTION_FAILED"
    listed = ok(client.get("/api/providers"))
    assert listed[0]["status"] == "failed" and "TEST_ONLY_SECRET" not in json.dumps(listed)
    hosts = ok(client.get("/api/hosts"))
    assert ok(client.post(f"/api/hosts/{hosts[0]['id']}/test"))["connected"] is True
    system = ok(client.get("/api/system"))
    assert system["model_connected"] is False and isinstance(system["gpu"], list)
    secrets_path = Path(os.environ["FOREST_DATA_DIR"]) / "secrets.json"
    assert secrets_path.stat().st_mode & 0o077 == 0


def case_run_receipt_and_immutable_start_config(client, app):
    p = create(client)
    n = add_node(client, p, instructions="Original instructions", config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    body = {"request_id": "same-launch", "scope": "single"}
    first = ok(client.post(f"/api/nodes/{n['id']}/run", json=body))
    second = ok(client.post(f"/api/nodes/{n['id']}/run", json=body))
    assert first["id"] == second["id"]
    ok(command(client, p, "edit_node", [n["id"]], instructions="Revised instructions"))
    actual = ok(client.get(f"/api/runs/{first['id']}"))
    assert actual["config"]["instructions"] == "Original instructions"
    assert actual["node_revision"] == 0
    assert ok(client.get(f"/api/nodes/{n['id']}"))["instructions"] == "Revised instructions"
    assert len(ok(client.get(f"/api/projects/{p['id']}/runs"))) == 1


def case_graph_apply_and_run_preserves_dag_dependencies(client, app):
    p = create(client)
    config = {"kind": "command", "command": ["python", "-c", "print(1)"]}
    first = add_node(client, p, config=config)
    second = add_node(client, p, config=config)
    ok(command(client, p, "add_dependency", source=first["id"], target=second["id"]))
    response = ok(client.post(f"/api/projects/{p['id']}/graph/commands", json={
        "request_id": "edit-and-run-dag", "expected_revision": graph(client, p)["revision"], "operation": "edit_node",
        "targets": [first["id"]], "params": {"instructions": "New controlled configuration"}, "run": True}))
    assert len(response["run_ids"]) == 2
    runs = {r["node_id"]: r for r in ok(client.get(f"/api/projects/{p['id']}/runs"))}
    assert runs[second["id"]]["dependencies"] == [runs[first["id"]]["id"]]
    assert runs[first["id"]]["node_revision"] == 1


def case_pruned_branch_and_missing_inputs_block_scheduling(client, app):
    p = create(client)
    n = add_node(client, p, inputs=[{"path": "unavailable.csv"}])
    denied = client.post(f"/api/nodes/{n['id']}/run", json={})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "INPUT_UNAVAILABLE"
    ok(command(client, p, "edit_node", [n["id"]], inputs=[]))
    ok(command(client, p, "prune_branch", branch_id=n["branch_id"]))
    denied = client.post(f"/api/nodes/{n['id']}/run", json={})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "BRANCH_INACTIVE"
    assert ok(client.get(f"/api/projects/{p['id']}/runs")) == []


def case_concurrent_launch_is_exactly_once(client, app):
    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    def launch(_):
        return client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "same-concurrent-launch"})
    with ThreadPoolExecutor(max_workers=5) as pool:
        runs = [ok(response) for response in pool.map(launch, range(5))]
    assert len({run["id"] for run in runs}) == 1
    assert len(ok(client.get(f"/api/projects/{p['id']}/runs"))) == 1


def case_duplicate_retains_bindings_without_following_links(client, app):
    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    run = ok(client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "to-copy"}))
    source_times = {
        "created_at": "2024-05-30T09:00:00+00:00",
        "started_at": "2024-05-30T09:01:12.125000+00:00",
        "finished_at": "2024-05-30T09:01:17.750000+00:00",
    }
    from services.api.db import Session, TaskRun
    with Session.begin() as session:
        source_run = session.get(TaskRun, run["id"])
        for field, value in source_times.items():
            setattr(source_run, field, value)
    fig = ok(client.post("/api/figures", json={"project_id": p["id"], "title": "Measured", "data": {"run_ids": [run["id"]]}}))
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / p["id"]
    private = root.parent / "outside.txt"
    private.write_text("outside project")
    (root / "secret.txt").symlink_to(private)
    copied = ok(client.post(f"/api/projects/{p['id']}/duplicate"))
    copied_run = ok(client.get(f"/api/projects/{copied['id']}/runs"))[0]
    copied_fig = ok(client.get("/api/figures", params={"project_id": copied["id"]}))[0]
    assert copied_fig["id"] != fig["id"] and copied_fig["data"]["run_ids"] == [copied_run["id"]]
    assert copied_run["status"] == "interrupted"
    assert {field: copied_run[field] for field in source_times} == source_times
    assert client.get(f"/api/projects/{copied['id']}/file", params={"path": "secret.txt"}).status_code == 404


def case_project_copy_preserves_run_mode(client, app):
    for mode in ("auto", "assisted", "manual"):
        source = ok(client.post("/api/projects", json={
            "name": f"{mode} mode copy",
            "goal": "Preserve the selected run mode",
            "mode": mode,
            "budget": {"allow_paid": False},
        }))

        duplicate = ok(client.post(f"/api/projects/{source['id']}/duplicate", json={}))
        assert duplicate["mode"] == mode
        assert ok(client.get(f"/api/projects/{duplicate['id']}"))["mode"] == mode

        archive = client.post(f"/api/projects/{source['id']}/export", json={})
        assert archive.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            manifest = json.loads(zipped.read("forest-project.json"))
        assert manifest["project"]["mode"] == mode

        imported = client.post(
            "/api/projects/import",
            files={"file": ("forest-project.zip", archive.content, "application/zip")},
        )
        imported_project = ok(imported)
        assert imported_project["mode"] == mode
        assert ok(client.get(f"/api/projects/{imported_project['id']}"))["mode"] == mode

    legacy_source = ok(client.post("/api/projects", json={
        "name": "legacy archive mode",
        "goal": "Keep old archives compatible",
        "mode": "manual",
        "budget": {"allow_paid": False},
    }))
    archive = client.post(f"/api/projects/{legacy_source['id']}/export", json={})
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        contents = {item.filename: zipped.read(item.filename) for item in zipped.infolist()}
    manifest = json.loads(contents["forest-project.json"])
    manifest["project"].pop("mode")
    contents["forest-project.json"] = json.dumps(manifest).encode()
    legacy_archive = io.BytesIO()
    with zipfile.ZipFile(legacy_archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in contents.items():
            zipped.writestr(name, content)
    imported_legacy = ok(client.post(
        "/api/projects/import",
        files={"file": ("legacy-project.zip", legacy_archive.getvalue(), "application/zip")},
    ))
    assert imported_legacy["mode"] == "assisted"


def case_duplicate_copy_failure_removes_partial_workspace(client, app):
    from unittest.mock import patch
    import services.api.main as api_main

    source = create(client)
    source_path = "code/duplicate-probe.txt"
    source_bytes = "copied payload from source"
    uploaded = client.put(
        f"/api/projects/{source['id']}/file",
        json={"path": source_path, "content": source_bytes, "expected_revision": 0},
    )
    assert uploaded.status_code == 200, uploaded.text

    projects_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects"
    before_directories = {path.name for path in projects_root.iterdir() if path.is_dir()}
    real_copy2 = api_main.shutil.copy2
    copied_destinations = []

    def copy_then_fail(src, dst, *args, **kwargs):
        result = real_copy2(src, dst, *args, **kwargs)
        copied_destinations.append(str(dst))
        assert Path(dst).read_text() == source_bytes
        raise OSError("injected copy failure after a real file copy")

    with patch.object(api_main.shutil, "copy2", side_effect=copy_then_fail):
        with pytest.raises(OSError, match="injected copy failure after a real file copy"):
            client.post(f"/api/projects/{source['id']}/duplicate")

    assert len(copied_destinations) == 1
    assert {path.name for path in projects_root.iterdir() if path.is_dir()} == before_directories
    assert len(ok(client.get("/api/projects"))) == 1
    assert ok(client.get(f"/api/projects/{source['id']}/file", params={"path": source_path}))["content"] == source_bytes

    duplicate = ok(client.post(f"/api/projects/{source['id']}/duplicate"))
    copied = ok(client.get(f"/api/projects/{duplicate['id']}/file", params={"path": source_path}))
    assert copied["content"] == source_bytes
    assert len(ok(client.get("/api/projects"))) == 2


def case_invalid_import_modes_rejected_before_project_creation(client, app):
    source = create(client)
    exported = client.post(f"/api/projects/{source['id']}/export", json={})
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        entries = {item.filename: archive.read(item.filename) for item in archive.infolist()}

    projects_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects"
    existing_projects = {path.name for path in projects_root.iterdir()}
    for mode in ("operator", None, "x" * 21):
        manifest = json.loads(entries["forest-project.json"])
        manifest["project"]["mode"] = mode
        malformed = dict(entries)
        malformed["forest-project.json"] = json.dumps(manifest).encode()
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, contents in malformed.items():
                archive.writestr(name, contents)

        response = client.post(
            "/api/projects/import",
            files={"file": ("invalid-mode.zip", payload.getvalue(), "application/zip")},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_ARCHIVE"
        assert {path.name for path in projects_root.iterdir()} == existing_projects
        assert len(ok(client.get("/api/projects"))) == 1


def case_project_updates_validate_run_modes(client, app):
    project = create(client)
    for mode in ("auto", "assisted", "manual"):
        updated = ok(client.patch(f"/api/projects/{project['id']}", json={"mode": mode}))
        assert updated["mode"] == mode
        project = ok(client.get(f"/api/projects/{project['id']}"))
        assert project["mode"] == mode

    for mode in ("operator", None, 123, "x" * 21):
        before = ok(client.get(f"/api/projects/{project['id']}"))
        response = client.patch(
            f"/api/projects/{project['id']}",
            json={"mode": mode, "expected_revision": before["revision"]},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_MODE"
        after = ok(client.get(f"/api/projects/{project['id']}"))
        assert after["mode"] == before["mode"]
        assert after["revision"] == before["revision"]


def case_actual_terminal(client, app):
    p = create(client)
    with client.websocket_connect(f"/ws/projects/{p['id']}/terminal") as terminal:
        terminal.send_text("printf '__FOREST_%s__\\n' EXECUTED\n")
        text = ""
        for _ in range(30):
            text += terminal.receive_text()
            if "__FOREST_EXECUTED__" in text:
                break
        assert "__FOREST_EXECUTED__" in text
        terminal.send_text("FOREST_RECONNECT_VALUE=7391\n")
        terminal.send_text("printf '__STORED_%s__\\n' $FOREST_RECONNECT_VALUE\n")
        for _ in range(30):
            if "__STORED_7391__" in terminal.receive_text(): break
        else: raise AssertionError('Shell variable was not set')
    with client.websocket_connect(f"/ws/projects/{p['id']}/terminal") as terminal:
        terminal.send_text("printf '__RECONNECTED_%s__\\n' $FOREST_RECONNECT_VALUE\n")
        text=''
        for _ in range(30):
            text+=terminal.receive_text()
            if '__RECONNECTED_7391__' in text: break
        assert '__RECONNECTED_7391__' in text
        terminal.send_text('{"action":"close"}')


def case_terminal_run_controls_do_not_signal_retained_processes(client, app):
    import signal
    import time
    import psutil
    from research.agents.processes import ManagedProcesses
    from services.api.db import Session, TaskRun

    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": [sys.executable, "-c", "pass"]})
    run = ok(client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "retained-process-controls"}))
    output = Path(os.environ['FOREST_DATA_DIR']) / 'projects' / p['id'] / run['output_path']
    manager = ManagedProcesses(output / 'workspace')
    process = manager.start([sys.executable, '-c', 'import time; time.sleep(30)'])
    pid = process['pid']
    try:
        # A terminal database row can still have retained process receipts.
        with Session.begin() as session:
            row = session.get(TaskRun, run['id'])
            row.status = 'completed'
            row.pid = pid
            row.process_created = process['process_created']
        for action in ('pause', 'resume'):
            if action == 'resume':
                manager.signal_all(signal.SIGSTOP)
                deadline = time.monotonic() + 3
                while psutil.Process(pid).status() != psutil.STATUS_STOPPED and time.monotonic() < deadline:
                    time.sleep(.01)
                assert psutil.Process(pid).status() == psutil.STATUS_STOPPED
            response = client.post(f"/api/runs/{run['id']}/{action}", json={})
            assert response.status_code == 409
            assert response.json()['detail']['code'] == 'INVALID_RUN_STATE'
            time.sleep(.05)
            assert (psutil.Process(pid).status() == psutil.STATUS_STOPPED) == (action == 'resume')
            assert ok(client.get(f"/api/runs/{run['id']}"))['status'] == 'completed'

        with Session.begin() as session:
            row = session.get(TaskRun, run['id'])
            row.config = {**row.config, 'execution_backend': 'container'}
        for action in ('pause', 'resume'):
            response = client.post(f"/api/runs/{run['id']}/{action}", json={})
            assert response.status_code == 409
            assert response.json()['detail']['code'] == 'INVALID_RUN_STATE'
        assert not (output / '.forest-container-processes').exists()
        retried = ok(client.post(f"/api/runs/{run['id']}/retry", json={'request_id': 'explicit-retry'}))
        assert retried['id'] != run['id'] and retried['status'] == 'queued'
    finally:
        manager.cancel_all()
        deadline = time.monotonic() + 3
        while manager.inspect(process['process_id'])['status'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
            time.sleep(.02)


def case_provider_health_does_not_reuse_old_chat_success(client, app):
    from services.api.db import Session, Provider
    # No server listens on this reserved, freshly closed loopback socket.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
    with Session.begin() as session:
        session.add(Provider(name='Offline after chat',kind='ollama',base_url=f'http://127.0.0.1:{port}',model='missing-model',status='connected'))
    report=ok(client.get('/api/system'))
    assert report['model_connected'] is False
    assert report['provider_status'][0]['last_chat_test']=='connected'
    assert report['provider_status'][0]['available'] is False


def case_project_time_budget_reserves_concurrent_runs(client, app):
    from services.api.db import Session, TaskRun

    project = ok(client.post('/api/projects', json={
        'name': 'Concurrent project time reservation',
        'goal': 'Ensure queued Workspace runs share the remaining project time.',
        'budget': {'max_runs': 20, 'seconds': 10, 'allow_paid': False},
    }))
    nodes = [
        add_node(client, project,
                 instructions=f'Concurrent budget node {index}',
                 config={'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 8})
        for index in range(2)
    ]
    request_ids = [f'budget-reservation-{index}' for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda pair: client.post(f"/api/nodes/{pair[0]['id']}/run", json={'request_id': pair[1]}),
            zip(nodes, request_ids),
        ))
    runs = [ok(response) for response in responses]
    assert sorted(run['config']['timeout'] for run in runs) == [2.0, 8.0]
    assert sorted(run['resource']['time_budget']['effective_total_timeout_seconds'] for run in runs) == [2.0, 8.0]
    assert all(run['resource']['time_budget']['project_budget_seconds_at_enqueue'] == 10 for run in runs)

    duplicate = ok(client.post(f"/api/nodes/{nodes[0]['id']}/run", json={'request_id': request_ids[0]}))
    assert duplicate['id'] == runs[0]['id']
    exhausted = client.post(f"/api/nodes/{nodes[0]['id']}/run", json={'request_id': 'budget-reservation-no-capacity'})
    assert exhausted.status_code == 409
    assert exhausted.json()['detail']['code'] == 'TIME_BUDGET_EXHAUSTED'

    # Once a reserved run ends early, its unused allocation is available again.
    longest = next(run for run in runs if run['config']['timeout'] == 8.0)
    with Session.begin() as session:
        row = session.get(TaskRun, longest['id'])
        row.status = 'completed'
        row.resource = {**row.resource, 'elapsed_seconds': 1.0}
    released = ok(client.post(f"/api/nodes/{nodes[0]['id']}/run", json={'request_id': 'budget-reservation-released-cap'}))
    assert released['config']['timeout'] == 7.0
    assert released['resource']['time_budget']['effective_total_timeout_seconds'] == 7.0


def case_selected_dependency_runs_defer_time_reservation(client, app):
    project = ok(client.post('/api/projects', json={
        'name': 'Deferred project time reservation',
        'goal': 'Reserve compute time when a selected dependency becomes runnable.',
        'budget': {'max_runs': 20, 'seconds': 10, 'allow_paid': False},
    }))
    config = {'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 8}
    producer = add_node(client, project, instructions='Producer', config=config)
    consumer = add_node(client, project, instructions='Consumer', config=config)
    ok(command(client, project, 'add_dependency', source=producer['id'], target=consumer['id']))

    queued = ok(client.post(f"/api/nodes/{consumer['id']}/run", json={
        'request_id': 'deferred-budget-chain', 'scope': 'ancestors',
    }))
    runs = {run['node_id']: run for run in queued['runs']}
    first = runs[producer['id']]
    second = runs[consumer['id']]
    assert first['config']['timeout'] == 8.0
    assert first['resource']['time_budget']['reservation_state'] == 'held'
    assert second['config']['timeout'] == 8.0
    assert second['resource']['time_budget']['reservation_state'] == 'deferred'
    assert second['resource']['time_budget']['effective_total_timeout_seconds'] is None


def case_verifier_linked_to_active_producer_defers_when_budget_is_reserved(client, app):
    project = ok(client.post('/api/projects', json={
        'name': 'Verification dependency waits for reserved project time',
        'goal': 'Keep an automatically linked verifier queued until its producer completes.',
        'budget': {'max_runs': 20, 'seconds': 8, 'allow_paid': False},
    }))
    producer = ok(command(client, project, 'add_node', title='Producer', type='experiment', config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 8,
    }))['graph']['nodes'][-1]
    verifier = ok(command(client, project, 'add_node', title='Check producer', type='verification', config={
        'kind': 'verification',
        'verification': {
            'producer_node_id': producer['id'],
            'checks': [{
                'id': 'rows', 'kind': 'csv_integrity', 'source': 'observations.csv',
                'required_columns': ['unit', 'value'], 'unique_by': ['unit'],
                'numeric_columns': ['value'],
            }],
        },
    }))['graph']['nodes'][-1]

    source = ok(client.post(f"/api/nodes/{producer['id']}/run", json={
        'request_id': 'verification-budget-producer',
    }))
    assert source['resource']['time_budget']['reservation_state'] == 'held'
    assert source['resource']['time_budget']['effective_total_timeout_seconds'] == 8

    dependent = ok(client.post('/api/verification/run', json={
        'project_id': project['id'], 'node_id': verifier['id'],
        'request_id': 'verification-budget-dependent',
    }))
    assert source['id'] in dependent['dependencies']
    assert dependent['status'] == 'queued'
    assert dependent['resource']['time_budget']['reservation_state'] == 'deferred'
    assert dependent['resource']['time_budget']['effective_total_timeout_seconds'] is None


def case_paused_container_resume_persists_elapsed_before_reconnect(client, app):
    import time
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from services.api.db import Session, TaskRun

    project = create(client)
    ok(client.patch(f"/api/projects/{project['id']}", json={
        'budget': {'max_runs': 20, 'seconds': 3, 'allow_paid': False},
    }))
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 10,
        'execution_backend': 'container',
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'container-pause-resume-accounting',
    }))
    with Session.begin() as session:
        row = session.get(TaskRun, run['id'])
        row.status = 'running'
        row.pid = 2**30  # The host executor is absent; the container adapter owns pause/resume.
        row.process_created = 1.0
        row.started_at = (datetime.now(timezone.utc) - timedelta(seconds=0.3)).isoformat()
        row.resource = {**row.resource, 'elapsed_seconds': 0.2, 'elapsed_before_attempt': 0.0}

    actions = []

    def container_control(config, output, action):
        actions.append(action)
        return {'status': 'paused' if action == 'pause' else 'running'}

    with patch('runners.container.control_container', side_effect=container_control):
        paused = ok(client.post(f"/api/runs/{run['id']}/pause", json={}))
        await_intervention(client, paused['intervention']['id'])
        paused = ok(client.get(f"/api/runs/{run['id']}"))
        assert paused['status'] == 'paused' and paused['pid'] is None
        assert paused['resource']['paused_live_attempt'] is True
        time.sleep(0.15)
        resumed = ok(client.post(f"/api/runs/{run['id']}/resume", json={}))
        await_intervention(client, resumed['intervention']['id'])
        resumed = ok(client.get(f"/api/runs/{run['id']}"))

    assert resumed['status'] == 'queued'
    assert resumed['resource']['elapsed_seconds'] >= 0.4
    assert 'paused_live_attempt' not in resumed['resource']
    assert resumed['resource']['container_reconnect_pending_dispatch'] is True
    # The external container stays paused until worker budget admission.
    assert actions == ['pause']
    time.sleep(0.15)
    from services.worker.scheduler import _elapsed_seconds_at
    with Session() as session:
        waiting_for_worker = session.get(TaskRun, run['id'])
        effective_elapsed = _elapsed_seconds_at(waiting_for_worker)
    assert effective_elapsed >= resumed['resource']['elapsed_seconds'] + 0.1


def case_paused_waiting_child_resume_defers_continue_until_worker_dispatch(client, app):
    import signal
    import time
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from services.api.db import Session, TaskRun
    from services.worker.scheduler import _elapsed_seconds_at

    project = create(client)
    ok(client.patch(f"/api/projects/{project['id']}", json={
        'budget': {'max_runs': 20, 'seconds': 8, 'allow_paid': False},
    }))
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 10,
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'waiting-live-child-pause-resume',
    }))
    with Session.begin() as session:
        row = session.get(TaskRun, run['id'])
        row.status = 'waiting'
        row.pid = None
        row.started_at = (datetime.now(timezone.utc) - timedelta(seconds=0.3)).isoformat()
        row.resource = {
            **row.resource,
            'elapsed_seconds': 0.1,
            'elapsed_before_attempt': 0.0,
            'elapsed_before_wait': 0.1,
            'waiting_started_at': time.time() - 0.2,
            'time_budget': {
                'requested_task_timeout_seconds': 10,
                'effective_total_timeout_seconds': 8,
                'reservation_state': 'held',
            },
        }

    class FakeManager:
        def __init__(self):
            self.status = 'running'
            self.signals = []

        def all(self):
            return [{'process_id': 'fixture-child', 'status': self.status}]

        def signal_all(self, signum):
            self.signals.append(signum)
            self.status = 'paused' if signum == signal.SIGSTOP else 'running'

    manager = FakeManager()
    with patch('research.execution.process_manager.process_manager', return_value=manager):
        paused = ok(client.post(f"/api/runs/{run['id']}/pause", json={}))
        await_intervention(client, paused['intervention']['id'])
        paused = ok(client.get(f"/api/runs/{run['id']}"))
        assert paused['status'] == 'paused'
        assert paused['resource']['paused_live_attempt'] is True
        time.sleep(0.1)
        resumed = ok(client.post(f"/api/runs/{run['id']}/resume", json={}))
        await_intervention(client, resumed['intervention']['id'])
        resumed = ok(client.get(f"/api/runs/{run['id']}"))

    assert resumed['status'] == 'queued'
    assert resumed['resource']['live_process_pending_dispatch'] is True
    assert 'paused_live_attempt' not in resumed['resource']
    assert manager.signals == [signal.SIGSTOP]
    time.sleep(0.1)
    with Session() as session:
        waiting_for_worker = session.get(TaskRun, run['id'])
        effective_elapsed = _elapsed_seconds_at(waiting_for_worker)
    assert effective_elapsed >= resumed['resource']['elapsed_seconds'] + 0.08


def case_retry_restores_requested_timeout_when_project_budget_is_removed(client, app):
    from services.api.db import Session, TaskRun

    project = ok(client.post('/api/projects', json={
        'name': 'Retry timeout after budget removal',
        'goal': 'Keep the requested task timeout when a project cap is removed.',
        'budget': {'max_runs': 20, 'seconds': 4, 'allow_paid': False},
    }))
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 30,
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'budgeted-timeout-before-retry',
    }))
    assert run['config']['timeout'] == 4
    assert run['resource']['time_budget']['requested_task_timeout_seconds'] == 30
    with Session.begin() as session:
        session.get(TaskRun, run['id']).status = 'completed'

    ok(client.patch(f"/api/projects/{project['id']}", json={
        'budget': {'max_runs': 20, 'allow_paid': False},
    }))
    retried = ok(client.post(f"/api/runs/{run['id']}/retry", json={
        'request_id': 'budget-removed-retry',
    }))
    assert retried['id'] != run['id']
    assert retried['status'] == 'queued'
    assert retried['config']['timeout'] == 30


def case_duplicate_legacy_unsupported_mode_defaults_to_assisted(client, app):
    from services.api.db import Session, Project
    source = create(client)
    for legacy_mode in ('operator', '', 'AUTO'):
        with Session.begin() as session:
            session.get(Project, source['id']).mode = legacy_mode
        duplicate = ok(client.post(f"/api/projects/{source['id']}/duplicate"))
        assert duplicate['mode'] == 'assisted'
        assert ok(client.get(f"/api/projects/{duplicate['id']}"))['mode'] == 'assisted'
        assert ok(client.get(f"/api/projects/{source['id']}"))['mode'] == legacy_mode


def case_retry_refreshes_node_after_project_lock(client, app):
    from services.api.db import Session, Node
    from services.worker.scheduler import enqueue
    project = create(client)
    node = add_node(client, project, config={'kind': 'command', 'command': [sys.executable, '-c', 'pass']})
    with Session() as stale:
        old = stale.get(Node, node['id'])
        revision = old.revision
        with Session.begin() as editor:
            current = editor.get(Node, node['id'])
            current.revision += 1
            current.extra = {**current.extra, 'latest_run_id': 'current-run'}
            current.execution_status = 'completed'
        assert old.revision == revision
        retry = enqueue(stale, project['id'], 'command', old.config, 'stale-retry', old,
                        node_revision=revision)
        stale.commit()
        assert retry.node_revision == revision
    with Session() as session:
        current = session.get(Node, node['id'])
        assert current.revision == revision + 1
        assert current.extra['latest_run_id'] == 'current-run'
        assert current.execution_status == 'completed'



def case_launch_refreshes_execution_kind_after_project_lock(client, app):
    from services.api.db import Session, Node
    from services.worker.scheduler import enqueue_nodes, enqueue_selected

    for launch in ('single', 'selected'):
        project = create(client)
        nodes = [add_node(client, project, config={'kind': 'agent'}) for _ in range(2)]
        with Session() as pending:
            # Both nodes were read before the project lock, while an owner save
            # could still commit. Hold the stale identities through scheduling.
            cached = [pending.get(Node, node['id']) for node in nodes]
            with Session.begin() as editor:
                for node in nodes:
                    current = editor.get(Node, node['id'])
                    current.config = {'kind': 'command', 'command': [sys.executable, '-c', 'pass']}
                    current.revision += 1
            assert all(node.config['kind'] == 'agent' for node in cached)
            if launch == 'single':
                runs = enqueue_nodes(pending, nodes[0]['id'], request_id='fresh-kind')
            else:
                runs = enqueue_selected(pending, [node['id'] for node in nodes], request_id='fresh-kind')
            pending.commit()
            assert all(run.kind == run.config['kind'] == 'command' for run in runs)
            assert all(run.node_revision == node['revision'] + 1 for run in runs
                       for node in nodes if node['id'] == run.node_id)


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_api_scenario(tmp_path, case):
    if case == "posix_backslash_file_revision_keys" and os.name != "posix":
        pytest.skip("Backslashes are filename characters only on POSIX")
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"), "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "isolated.sqlite"),
           "FOREST_OWNER_TOKEN": "isolated-test-owner-token", "FOREST_MODEL": "", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case], cwd=ROOT, env=env, capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SCENARIO PASSED" in result.stdout


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app
    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()["case_" + selected](client, app)
    print("SCENARIO PASSED:", selected)
