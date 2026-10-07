"""Real HTTP API, database, durable worker, and operating-system processes.

No API, database, process, or timing mocks are used. All state lives beneath the
pytest temporary directory; production projects/providers are never touched.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx
import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKLOAD = ROOT / "scripts" / "test_worker_case.py"


def install_fake_docker(directory):
    """Install a small Docker CLI stand-in for worker lifecycle integration tests."""
    binary_dir = directory / "fake-docker-bin"
    state_dir = directory / "fake-docker-state"
    binary_dir.mkdir()
    state_dir.mkdir()
    docker = binary_dir / "docker"
    fake_docker_source = '''
import json, os, signal, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
import psutil

state_dir = Path(os.environ["FOREST_FAKE_DOCKER_STATE"])
args = sys.argv[1:]
action = args[0]

def state_path(ref):
    for path in state_dir.glob("*.json"):
        state = json.loads(path.read_text())
        if ref in (state["Id"], state["name"]):
            return path, state
    return None, None

def inspect(state):
    pid = state.get("pid")
    alive = False
    if pid:
        try:
            alive = psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
    if state["State"]["Paused"] and alive:
        state["State"].update(Status="paused", Running=True)
    elif alive:
        state["State"].update(Status="running", Running=True, Paused=False)
    elif state["State"]["Status"] != "created":
        state["State"].update(Status="exited", Running=False, Paused=False, ExitCode=0)
    return {"Id": state["Id"], "Created": state["Created"], "Config": {"Labels": state["labels"]}, "State": state["State"]}

if action == "create":
    name = args[args.index("--name") + 1]
    labels = {}
    env = {}
    workspace = None
    for index, value in enumerate(args[:-1]):
        if value == "--label":
            key, item = args[index + 1].split("=", 1)
            labels[key] = item
        elif value == "--env":
            key, item = args[index + 1].split("=", 1)
            env[key] = item
        elif value == "--mount":
            workspace = args[index + 1].split("src=", 1)[1].split(",", 1)[0]
    command = args[args.index("forest-task:local") + 1:]
    ident = uuid.uuid4().hex
    state = {"Id": ident, "Created": datetime.now(timezone.utc).isoformat(), "name": name,
             "labels": labels, "env": env, "workspace": workspace, "command": command,
             "State": {"Status": "created", "Running": False, "Paused": False,
                       "StartedAt": "0001-01-01T00:00:00Z", "ExitCode": None}}
    (state_dir / (ident + ".json")).write_text(json.dumps(state))
    print(ident)
elif action == "inspect":
    path, state = state_path(args[1])
    if not state:
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
    print(json.dumps([inspect(state)]))
elif action == "start":
    path, state = state_path(args[1])
    if not state:
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
    process = subprocess.Popen(state["command"], cwd=state["workspace"], env={**state["env"], "PATH": os.environ.get("PATH", "")}, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state["pid"] = process.pid
    state["State"].update(Status="running", Running=True, Paused=False, StartedAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    path.write_text(json.dumps(state))
elif action in ("pause", "unpause", "stop"):
    path, state = state_path(args[1])
    if not state:
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
    pid = state.get("pid")
    if pid:
        try:
            sig = signal.SIGSTOP if action == "pause" else signal.SIGCONT if action == "unpause" else signal.SIGTERM
            os.killpg(pid, sig)
        except ProcessLookupError:
            pass
    state["State"]["Paused"] = action == "pause"
    if action == "stop":
        state["State"].update(Status="exited", Running=False, Paused=False, ExitCode=143)
    path.write_text(json.dumps(state))
elif action == "logs":
    pass
else:
    print("Unsupported fake Docker action: " + action, file=sys.stderr)
    sys.exit(2)
'''
    docker.write_text("#!" + sys.executable + "\n" + fake_docker_source)
    docker.chmod(0o755)
    return binary_dir, state_dir


def wait_until(callback, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = callback()
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"Condition did not become true in {timeout}s; last={last!r}")


class Harness:
    def __init__(self, directory):
        self.directory = directory
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "FOREST_DATABASE_URL": f"sqlite:///{directory / 'integration.db'}", "FOREST_DATA_DIR": str(directory / "data"), "FOREST_MODEL": "", "FOREST_OWNER_TOKEN": "worker-test-owner", "FOREST_WORKER_CONCURRENCY": "2", "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
        self.worker = None
        self.api = None
        self.processes = []
        self.log_handles = []
        self.run_processes = []
        self.client = httpx.Client(base_url=self.base_url, timeout=12)

    def spawn(self, name, command):
        handle = (self.directory / (name + ".log")).open("ab")
        self.log_handles.append(handle)
        process = subprocess.Popen(command, cwd=ROOT, env=self.env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes.append(process)
        return process

    def start_api(self):
        self.api = self.spawn("api", [sys.executable, "-m", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port", str(self.port)])
        def healthy():
            try:
                return self.client.get("/api/health").status_code == 200
            except httpx.TransportError:
                return False
        wait_until(healthy, 20)

    def start_worker(self):
        self.worker = self.spawn("worker", [sys.executable, "-m", "services.worker.main"])

    def stop(self, process):
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        assert response.is_success, f"{method} {path}: {response.status_code} {response.text}"
        return response.json()

    def project_node(self, seconds=3, child=False, budget=None, timeout=30):
        project = self.request("POST", "/api/projects", json={"name": "Worker integration " + str(uuid.uuid4())[:8], "goal": "Exercise actual worker lifecycle", "budget": budget or {"max_runs": 20, "seconds": 120, "allow_paid": False}})
        graph = self.request("GET", f"/api/projects/{project['id']}/graph")
        command = [sys.executable, str(WORKLOAD), "--seconds", str(seconds)]
        if child:
            command.append("--spawn-child")
        nid = str(uuid.uuid4())
        response = self.request("POST", f"/api/projects/{project['id']}/graph/commands", json={"request_id": str(uuid.uuid4()), "expected_revision": graph["revision"], "operation": "add_node", "targets": [], "params": {"id": nid, "branch_id": graph["branches"][0]["id"], "type": "experiment", "title": "Real process lifecycle", "config": {"kind": "command", "command": command, "timeout": timeout}}})
        node = next(n for n in response["graph"]["nodes"] if n["id"] == nid)
        return project, node

    def launch(self, node, request_id=None):
        return self.request("POST", f"/api/nodes/{node['id']}/run", json={"request_id": request_id or str(uuid.uuid4()), "scope": "single"})

    def run(self, run):
        return self.request("GET", f"/api/runs/{run['id']}")

    def running(self, run):
        current = wait_until(lambda: (r if (r := self.run(run))["status"] == "running" and r.get("pid") else None))
        self.run_processes.append((current["pid"], current["process_created"]))
        return current

    def terminal(self, run, timeout=20):
        return wait_until(lambda: (r if (r := self.run(run))["status"] in ("completed", "failed", "cancelled", "interrupted") else None), timeout)

    def output(self, run):
        return self.directory / "data" / "projects" / run["project_id"] / run["output_path"]

    def cleanup(self):
        for pid, created in self.run_processes:
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - created) < 0.2:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (psutil.NoSuchProcess, ProcessLookupError):
                pass
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    self.stop(process)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.client.close()
        for handle in self.log_handles:
            handle.close()


@pytest.fixture(scope="module")
def actual_worker(tmp_path_factory):
    harness = Harness(tmp_path_factory.mktemp("actual-worker"))
    try:
        harness.start_api()
        harness.start_worker()
        yield harness
    finally:
        harness.cleanup()


def test_real_process_survives_client_close_and_api_restart(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=3)
    run = h.launch(node)
    h.running(run)
    h.client.close()
    h.stop(h.api)
    time.sleep(1)
    h.client = httpx.Client(base_url=h.base_url, timeout=12)
    h.start_api()
    completed = h.terminal(run)
    assert completed["status"] == "completed", completed
    assert completed["metrics"]["ticks"] > 10
    assert (h.output(run) / "heartbeat.txt").is_file()
    assert (h.output(run) / "result.json").is_file()


def test_pause_resume_stops_and_restarts_actual_process_group(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=4, child=True)
    run = h.launch(node)
    h.running(run)
    heartbeat = h.output(run) / "heartbeat.txt"
    child_heartbeat = h.output(run) / "child_heartbeat.txt"
    wait_until(lambda: child_heartbeat.exists() and heartbeat.exists())
    paused = h.request("POST", f"/api/runs/{run['id']}/pause", json={})
    assert paused["status"] == "paused"
    time.sleep(0.2)
    before = (heartbeat.read_text(), child_heartbeat.read_text())
    time.sleep(0.5)
    assert (heartbeat.read_text(), child_heartbeat.read_text()) == before
    h.request("POST", f"/api/runs/{run['id']}/resume", json={})
    wait_until(lambda: heartbeat.read_text() != before[0])
    assert h.terminal(run)["status"] == "completed"


def test_live_container_executor_uses_recalculated_timeout_after_resume(tmp_path):
    h = Harness(tmp_path)
    binary_dir, state_dir = install_fake_docker(tmp_path)
    h.env = {**h.env, "PATH": str(binary_dir) + os.pathsep + h.env["PATH"],
             "FOREST_FAKE_DOCKER_STATE": str(state_dir)}
    try:
        h.start_api()
        h.start_worker()
        project, node = h.project_node(seconds=4, timeout=30)
        project = h.request("GET", f"/api/projects/{project['id']}")
        initial_budget = {**project["budget"], "seconds": 2}
        h.request("PATCH", f"/api/projects/{project['id']}",
                  json={"expected_revision": project["revision"], "budget": initial_budget})
        script = "import pathlib,time; p=pathlib.Path('starts.txt'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); time.sleep(3); pathlib.Path('finished.txt').write_text('ok')"
        h.request("PATCH", f"/api/nodes/{node['id']}", json={"config": {
            "kind": "command", "command": [sys.executable, "-c", script],
            "execution_backend": "container", "container": {"memory": "256m"}, "timeout": 30}})
        run = h.launch(node)
        active = h.running(run)
        output = h.output(run)
        handle_path = output / "container_task.json"
        job = wait_until(lambda: json.loads(handle_path.read_text()) if handle_path.exists() else None)
        wait_until(lambda: (output / "workspace" / "starts.txt").exists())
        assert active["config"]["timeout"] == 2

        paused = h.request("POST", f"/api/runs/{run['id']}/pause", json={})
        assert paused["status"] == "paused"
        current_project = h.request("GET", f"/api/projects/{project['id']}")
        increased_budget = {**current_project["budget"], "seconds": 20}
        h.request("PATCH", f"/api/projects/{project['id']}",
                  json={"expected_revision": current_project["revision"], "budget": increased_budget})
        resumed = h.request("POST", f"/api/runs/{run['id']}/resume", json={})
        assert resumed["status"] == "running"
        assert resumed["pid"] == active["pid"]
        assert resumed["config"]["timeout"] == 20

        completed = h.terminal(run, timeout=15)
        assert completed["status"] == "completed", completed
        assert (output / "workspace" / "finished.txt").read_text() == "ok"
        assert (output / "workspace" / "starts.txt").read_text() == "1"
        assert json.loads(handle_path.read_text())["container_id"] == job["container_id"]
    finally:
        h.cleanup()


def test_cancel_terminates_executor_and_descendant(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=25, child=True)
    run = h.launch(node)
    active = h.running(run)
    child_file = h.output(run) / "child.pid"
    # File creation and PID write are separate observable events.
    child_pid = int(wait_until(lambda: child_file.read_text().strip() if child_file.exists() else ''))
    cancelled = h.request("POST", f"/api/runs/{run['id']}/cancel", json={})
    assert cancelled["status"] == "cancelled"
    def stopped(pid):
        return not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    wait_until(lambda: stopped(active["pid"]) and stopped(child_pid))
    assert h.run(run)["status"] == "cancelled"
    # The executor is a direct child of the live worker. Cancellation must not
    # leave it as a zombie after the worker has stopped the command process.
    wait_until(lambda: not psutil.pid_exists(active["pid"]), timeout=5)
    assert h.worker.poll() is None


def test_worker_restart_recovers_running_job_without_duplicate_execution(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=4)
    run = h.launch(node)
    active = h.running(run)
    heartbeat = h.output(run) / "heartbeat.txt"
    wait_until(heartbeat.exists)
    h.stop(h.worker)
    before = int(heartbeat.read_text())
    time.sleep(0.5)
    assert int(heartbeat.read_text()) > before
    h.start_worker()
    complete = h.terminal(run)
    assert complete["status"] == "completed", complete
    assert complete["pid"] == active["pid"]
    assert complete["worker_id"] != active["worker_id"]


def test_old_revision_result_does_not_overwrite_edited_node(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=2)
    run = h.launch(node)
    active = h.running(run)
    h.request("PATCH", f"/api/nodes/{node['id']}", json={"title": "Edited while running", "instructions": "New research content"})
    before = h.request("GET", f"/api/nodes/{node['id']}")
    assert before["revision"] > active["node_revision"]
    assert h.terminal(run)["status"] == "completed"
    after = h.request("GET", f"/api/nodes/{node['id']}")
    assert after["title"] == "Edited while running"
    assert after["instructions"] == "New research content"
    assert after["outputs"] == before["outputs"]
    assert after["deliverable_status"] == before["deliverable_status"]


def test_idempotent_run_budget_timeout_and_measured_resources(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=12, timeout=1.7, budget={"max_runs": 1, "seconds": 4, "allow_paid": False})
    request_id = str(uuid.uuid4())
    run = h.launch(node, request_id)
    duplicate = h.launch(node, request_id)
    assert duplicate["id"] == run["id"]
    h.running(run)
    measured = wait_until(lambda: (r if (r := h.run(run)).get("resource", {}).get("rss_bytes", 0) else None))
    assert measured["resource"]["rss_bytes"] > 0
    assert measured["resource"]["elapsed_seconds"] > 0
    stopped = h.terminal(run)
    assert stopped["status"] == "failed"
    assert "budget" in stopped["error"].lower() or "timed out" in stopped["error"].lower()
    over_budget = h.client.post(f"/api/nodes/{node['id']}/run", json={"request_id": str(uuid.uuid4())})
    assert over_budget.status_code == 409
    assert over_budget.json()["detail"]["code"] == "RUN_BUDGET_EXHAUSTED"


def test_exhausted_time_budget_blocks_actual_job_creation(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=1, budget={"max_runs": 10, "seconds": 0, "allow_paid": False})
    response = h.client.post(f"/api/nodes/{node['id']}/run", json={"request_id": str(uuid.uuid4())})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TIME_BUDGET_EXHAUSTED"


def test_paused_queued_job_stays_pending_and_does_not_occupy_process_slot(actual_worker):
    h = actual_worker
    h.stop(h.worker)
    _, node = h.project_node(seconds=0.5)
    run = h.launch(node)
    assert run["status"] == "queued"
    paused = h.request("POST", f"/api/runs/{run['id']}/pause", json={})
    assert paused["status"] == "paused" and paused["pid"] is None
    h.start_worker()
    time.sleep(0.8)
    assert h.run(run)["status"] == "paused"
    _, other_node = h.project_node(seconds=0.5)
    other = h.launch(other_node)
    h.running(other)
    assert h.terminal(other)["status"] == "completed"
    h.request("POST", f"/api/runs/{run['id']}/resume", json={})
    h.running(run)
    assert h.terminal(run)["status"] == "completed"


def test_missing_deferred_producer_file_blocks_consumer_before_process_start(actual_worker):
    h = actual_worker
    project, producer = h.project_node(seconds=0.5)
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    consumer_id = str(uuid.uuid4())
    created = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={"request_id": str(uuid.uuid4()), "expected_revision": graph["revision"], "operation": "add_node", "targets": [], "params": {"id": consumer_id, "branch_id": producer["branch_id"], "type": "experiment", "title": "Requires real produced file", "inputs": [{"node_id": producer["id"], "path": "never_produced.txt", "destination": "input.txt"}], "config": {"kind": "command", "command": [sys.executable, "-c", "from pathlib import Path; Path('consumer_started.txt').write_text('unexpected')"]}}})
    launched = h.request("POST", f"/api/nodes/{consumer_id}/run", json={"request_id": str(uuid.uuid4()), "scope": "ancestors"})
    runs = launched["runs"]
    producer_run = next(r for r in runs if r["node_id"] == producer["id"])
    consumer_run = next(r for r in runs if r["node_id"] == consumer_id)
    h.running(producer_run)
    assert h.terminal(producer_run)["status"] == "completed"
    blocked = wait_until(lambda: (r if (r := h.run(consumer_run))["status"] == "waiting_input" else None))
    assert blocked["pid"] is None
    assert "never_produced.txt" in blocked["error"]
    assert not (h.output(consumer_run) / "workspace" / "consumer_started.txt").exists()


def test_successful_current_descendant_consumes_rerun_before_affected_scope(actual_worker):
    h = actual_worker
    project = h.request("POST", "/api/projects", json={
        "name": "Consume rerun after current success",
        "budget": {"max_runs": 10, "seconds": 60, "allow_paid": False},
    })
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    branch_id = graph["branches"][0]["id"]

    def add_command_node(title, command):
        current = h.request("GET", f"/api/projects/{project['id']}/graph")
        node_id = str(uuid.uuid4())
        result = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": current["revision"],
            "operation": "add_node",
            "params": {
                "id": node_id,
                "branch_id": branch_id,
                "type": "experiment",
                "title": title,
                "config": {"kind": "command", "command": command, "timeout": 10},
            },
        })
        return next(node for node in result["graph"]["nodes"] if node["id"] == node_id)

    root = add_command_node("Root", [sys.executable, "-c", "print('root')"])
    root_run = h.launch(root)
    assert h.terminal(root_run)["status"] == "completed"

    child = add_command_node("Child", [sys.executable, "-c", "print('child')"])
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": current["revision"],
        "operation": "add_dependency",
        "params": {"source": root["id"], "target": child["id"]},
    })

    child_run = h.launch(child)
    assert h.terminal(child_run)["status"] == "completed"
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    completed_child = next(node for node in current["nodes"] if node["id"] == child["id"])
    assert completed_child["results_current"] is True
    assert completed_child.get("needs_rerun") is not True, completed_child

    affected = h.request("POST", f"/api/nodes/{root['id']}/run", json={
        "request_id": str(uuid.uuid4()), "scope": "affected",
    })
    scheduled = affected.get("runs", [affected])
    assert [run["node_id"] for run in scheduled] == [root["id"]]


def test_upstream_invalidation_after_enqueue_preserves_descendant_rerun(actual_worker):
    h = actual_worker
    project = h.request("POST", "/api/projects", json={
        "name": "Preserve invalidation after enqueue",
        "budget": {"max_runs": 12, "seconds": 90, "allow_paid": False},
    })
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    branch_id = graph["branches"][0]["id"]

    def add_command_node(title, command):
        current = h.request("GET", f"/api/projects/{project['id']}/graph")
        node_id = str(uuid.uuid4())
        result = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": current["revision"],
            "operation": "add_node",
            "params": {
                "id": node_id,
                "branch_id": branch_id,
                "type": "experiment",
                "title": title,
                "config": {"kind": "command", "command": command, "timeout": 10},
            },
        })
        return next(node for node in result["graph"]["nodes"] if node["id"] == node_id)

    def edit_node(node_id, command):
        current = h.request("GET", f"/api/projects/{project['id']}/graph")
        return h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": current["revision"],
            "operation": "edit_node",
            "targets": [node_id],
            "params": {"config": {"kind": "command", "command": command, "timeout": 10}},
        })

    root = add_command_node("Upstream", [sys.executable, "-c", "print('root v1')"])
    assert h.terminal(h.launch(root))["status"] == "completed"
    child = add_command_node("Descendant", [sys.executable, "-c", "import time; time.sleep(1.5); print('child')"])
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": current["revision"],
        "operation": "add_dependency",
        "params": {"source": root["id"], "target": child["id"]},
    })
    accepted_child_run = h.launch(child)
    assert h.terminal(accepted_child_run)["status"] == "completed"

    child_run = h.launch(child)
    h.running(child_run)
    edit_node(root["id"], [sys.executable, "-c", "print('root v2')"])
    assert h.terminal(child_run)["status"] == "completed"

    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    current_child = next(node for node in current["nodes"] if node["id"] == child["id"])
    assert current_child.get("needs_rerun") is True, current_child
    assert current_child.get("results_current") is False, current_child
    assert current_child.get("last_run_id") == accepted_child_run["id"], current_child
    assert current_child["outputs"][0]["id"] == accepted_child_run["id"], current_child

    affected = h.request("POST", f"/api/nodes/{root['id']}/run", json={
        "request_id": str(uuid.uuid4()), "scope": "affected",
    })
    scheduled = affected.get("runs", [affected])
    assert child["id"] in [run["node_id"] for run in scheduled]


def test_failed_current_run_keeps_rerun_marker(actual_worker):
    h = actual_worker
    project = h.request("POST", "/api/projects", json={
        "name": "Keep rerun marker after failure",
        "budget": {"max_runs": 5, "seconds": 30, "allow_paid": False},
    })
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    node_id = str(uuid.uuid4())
    created = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": graph["revision"],
        "operation": "add_node",
        "params": {
            "id": node_id,
            "branch_id": graph["branches"][0]["id"],
            "type": "experiment",
            "title": "Fails without satisfying the rerun",
            "config": {"kind": "command", "command": [sys.executable, "-c", "raise SystemExit(7)"]},
        },
    })
    node = next(value for value in created["graph"]["nodes"] if value["id"] == node_id)
    run = h.launch(node)
    assert h.terminal(run)["status"] == "failed"
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    failed_node = next(value for value in current["nodes"] if value["id"] == node_id)
    assert failed_node["results_current"] is False
    assert failed_node.get("needs_rerun") is True


def test_stale_run_does_not_consume_rerun_marker_or_promote_results(actual_worker):
    h = actual_worker
    project = h.request("POST", "/api/projects", json={
        "name": "Keep rerun marker after stale run",
        "budget": {"max_runs": 5, "seconds": 30, "allow_paid": False},
    })
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    node_id = str(uuid.uuid4())
    initial_config = {"kind": "command", "command": [sys.executable, "-c", "import time; time.sleep(1.2)"]}
    created = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": graph["revision"],
        "operation": "add_node",
        "params": {
            "id": node_id,
            "branch_id": graph["branches"][0]["id"],
            "type": "experiment",
            "title": "Becomes stale while running",
            "config": initial_config,
        },
    })
    node = next(value for value in created["graph"]["nodes"] if value["id"] == node_id)
    run = h.launch(node)
    h.running(run)
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": current["revision"],
        "operation": "edit_node",
        "targets": [node_id],
        "params": {"config": {"kind": "command", "command": [sys.executable, "-c", "print('new revision')"]}},
    })
    assert h.terminal(run)["status"] == "completed"
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    stale_node = next(value for value in current["nodes"] if value["id"] == node_id)
    assert stale_node.get("needs_rerun") is True
    assert stale_node.get("results_current") is not True


@pytest.mark.parametrize("stage", ["after_model", "before_tool"])
def test_real_debug_checkpoint_can_edit_payload_then_resume(actual_worker, stage):
    h = actual_worker
    _, node = h.project_node(seconds=0.3)
    command = [sys.executable, str(WORKLOAD), "--seconds", "0.3", "--checkpoint", stage]
    h.request("PATCH", f"/api/nodes/{node['id']}", json={"config": {"kind": "command", "command": command, "timeout": 20}})
    run = h.launch(node)
    stopped = wait_until(lambda: (r if (r := h.run(run))["status"] == "waiting_input" and r["resource"].get("checkpoint") else None))
    h.run_processes.append((stopped["pid"], stopped["process_created"]))
    assert stopped["resource"]["checkpoint"]["stage"] == stage
    wait_until(lambda: psutil.Process(stopped["pid"]).status() == psutil.STATUS_STOPPED)
    override = {"text": "owner edited callback payload"} if stage == "after_model" else {"tool": "write_file", "arguments": {"path": "checkpoint_output.txt", "content": "owner edited callback payload"}}
    h.request("PATCH", f"/api/runs/{run['id']}/checkpoint", json={"payload": override})
    h.request("POST", f"/api/runs/{run['id']}/resume", json={})
    completed = h.terminal(run)
    assert completed["status"] == "completed", completed
    assert completed["metrics"]["checkpoint_value"] == override
    assert json.loads((h.output(run) / "checkpoint_return.json").read_text()) == override
    if stage == "before_tool":
        assert (h.output(run) / "workspace" / "checkpoint_output.txt").read_text() == "owner edited callback payload"


def test_cancel_at_real_debug_checkpoint_terminates_stopped_process(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=0.3)
    command = [sys.executable, str(WORKLOAD), "--seconds", "0.3", "--checkpoint", "before_tool"]
    h.request("PATCH", f"/api/nodes/{node['id']}", json={"config": {"kind": "command", "command": command, "timeout": 20}})
    run = h.launch(node)
    stopped = wait_until(lambda: (r if (r := h.run(run))["status"] == "waiting_input" and r["resource"].get("checkpoint") else None))
    h.run_processes.append((stopped["pid"], stopped["process_created"]))
    h.request("POST", f"/api/runs/{run['id']}/cancel", json={})
    wait_until(lambda: not psutil.pid_exists(stopped["pid"]) or psutil.Process(stopped["pid"]).status() == psutil.STATUS_ZOMBIE)
    assert h.run(run)["status"] == "cancelled"
    assert not (h.output(run) / "workspace" / "checkpoint_output.txt").exists()
