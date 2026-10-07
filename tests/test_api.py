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
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "revision_conflict"
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
    fork = ok(command(client, p, "fork_branch", [n["id"]], name="Candidate"))["graph"]["branches"][-1]
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


def case_import_running_controller_requires_explicit_start(client, app):
    p = create(client)
    started = ok(client.post(f"/api/projects/{p['id']}/research/start", json={"autonomous": True}))
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
    imported_id = imported["id"]
    before_tick = ok(client.get(f"/api/projects/{imported_id}/research"))
    imported_runs = ok(client.get(f"/api/projects/{imported_id}/runs"))
    assert before_tick["controller"]["status"] == "paused"
    assert before_tick["controller"]["autonomous"] is True
    assert "last_run" not in before_tick["controller"]
    assert before_tick["active_runs"] == []
    assert len(imported_runs) == 1 and imported_runs[0]["status"] == "interrupted"

    advance_projects()

    after_tick = ok(client.get(f"/api/projects/{imported_id}/research"))
    assert after_tick["controller"]["status"] == "paused"
    assert after_tick["active_runs"] == []
    assert ok(client.get(f"/api/projects/{imported_id}/runs")) == imported_runs


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
    assert client.get(f"/api/projects/{copied['id']}/file", params={"path": "secret.txt"}).status_code == 404


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


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_api_scenario(tmp_path, case):
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
