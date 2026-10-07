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
from types import SimpleNamespace

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


def test_worker_claim_locks_project_before_task_run(monkeypatch):
    from services.worker import main
    from sqlalchemy.dialects import postgresql
    events = []
    project = object()
    run = SimpleNamespace(status="queued")

    class Session:
        def scalar(self, statement):
            events.append("run")
            assert "FOR UPDATE SKIP LOCKED" in str(statement.compile(dialect=postgresql.dialect()))
            return run

    def lock_project(session, project_id):
        events.append(("project", project_id))
        return project

    monkeypatch.setattr(main, "_lock_project", lock_project)
    claimed, locked_project = main._claim_queued_candidate(
        Session(), SimpleNamespace(id="run-id", project_id="project-id")
    )
    assert claimed is run
    assert locked_project is project
    assert events == [("project", "project-id"), "run"]


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


def test_cancel_records_consumed_time_before_releasing_project_reservation(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=25, timeout=25,
                            budget={"max_runs": 20, "seconds": 8, "allow_paid": False})
    first = h.launch(node)
    h.running(first)
    time.sleep(0.25)
    cancelled = h.request("POST", f"/api/runs/{first['id']}/cancel", json={})
    assert cancelled["status"] == "cancelled"
    consumed = cancelled["resource"]["elapsed_seconds"]
    assert consumed >= 0.2

    second = h.launch(node)
    assert 0 < second["config"]["timeout"] < 8
    assert abs(second["config"]["timeout"] - (8 - consumed)) < 0.4


def test_cancel_while_paused_records_wall_elapsed_before_releasing_reservation(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=25, timeout=25,
                            budget={"max_runs": 20, "seconds": 8, "allow_paid": False})
    first = h.launch(node)
    h.running(first)
    time.sleep(0.25)
    paused = h.request("POST", f"/api/runs/{first['id']}/pause", json={})
    assert paused["status"] == "paused"
    time.sleep(0.3)
    cancelled = h.request("POST", f"/api/runs/{first['id']}/cancel", json={})
    assert cancelled["status"] == "cancelled"
    consumed = cancelled["resource"]["elapsed_seconds"]
    assert consumed >= 0.45

    second = h.launch(node)
    assert 0 < second["config"]["timeout"] < 8
    assert abs(second["config"]["timeout"] - (8 - consumed)) < 0.4


def test_resume_recalculates_against_current_project_time_budget(actual_worker):
    h = actual_worker
    project, node = h.project_node(seconds=25, timeout=25,
                                   budget={"max_runs": 20, "seconds": 8, "allow_paid": False})
    run = h.launch(node)
    h.running(run)
    time.sleep(0.25)
    paused = h.request("POST", f"/api/runs/{run['id']}/pause", json={})
    assert paused["status"] == "paused"

    h.request("PATCH", f"/api/projects/{project['id']}", json={
        "budget": {"max_runs": 20, "seconds": 4, "allow_paid": False},
    })
    resumed = h.request("POST", f"/api/runs/{run['id']}/resume", json={})
    assert resumed["status"] == "running"
    assert resumed["config"]["timeout"] == 4
    assert resumed["resource"]["time_budget"]["effective_total_timeout_seconds"] == 4
    cancelled = h.request("POST", f"/api/runs/{run['id']}/cancel", json={})
    assert cancelled["status"] == "cancelled"


def test_resume_restores_requested_timeout_when_project_time_budget_is_removed(actual_worker):
    h = actual_worker
    project, node = h.project_node(seconds=25, timeout=30,
                                   budget={"max_runs": 20, "seconds": 4, "allow_paid": False})
    run = h.launch(node)
    assert run["config"]["timeout"] == 4
    h.running(run)
    time.sleep(0.25)
    paused = h.request("POST", f"/api/runs/{run['id']}/pause", json={})
    assert paused["status"] == "paused"

    h.request("PATCH", f"/api/projects/{project['id']}", json={
        "budget": {"max_runs": 20, "allow_paid": False},
    })
    resumed = h.request("POST", f"/api/runs/{run['id']}/resume", json={})
    assert resumed["status"] == "running"
    assert resumed["config"]["timeout"] == 30
    assert resumed["resource"]["time_budget"]["effective_total_timeout_seconds"] == 30
    assert resumed["resource"]["elapsed_seconds"] >= 0.2

    cancelled = h.request("POST", f"/api/runs/{run['id']}/cancel", json={})
    assert cancelled["status"] == "cancelled"


def test_resume_rejects_live_paused_run_after_wall_clock_budget_is_spent(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=25, timeout=25,
                            budget={"max_runs": 20, "seconds": 1, "allow_paid": False})
    run = h.launch(node)
    h.running(run)
    time.sleep(0.15)
    paused = h.request("POST", f"/api/runs/{run['id']}/pause", json={})
    assert paused["status"] == "paused" and paused["pid"] is not None

    # A live paused executor still consumes the wall-clock project budget.
    time.sleep(1.1)
    resumed = h.client.post(f"/api/runs/{run['id']}/resume", json={})
    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["detail"]["code"] == "TIME_BUDGET_EXHAUSTED"
    current = h.run(run)
    assert current["status"] == "paused"
    cancelled = h.request("POST", f"/api/runs/{run['id']}/cancel", json={})
    assert cancelled["status"] == "cancelled"


def test_waiting_resume_admission_charges_time_since_worker_checkpoint():
    from services.worker.scheduler import reserve_project_time

    waiting_since = time.time() - 1.25
    run = SimpleNamespace(
        id="waiting-run",
        status="waiting",
        pid=None,
        started_at=None,
        config={"timeout": 10},
        resource={
            "elapsed_seconds": 1.0,
            "elapsed_before_wait": 1.0,
            "waiting_started_at": waiting_since,
            "time_budget": {
                "requested_task_timeout_seconds": 10,
                "effective_total_timeout_seconds": 10,
                "reservation_state": "held",
            },
        },
    )

    class Session:
        def scalars(self, statement):
            return [run]

    project = SimpleNamespace(id="project", budget={"seconds": 2}, revision=4)
    reserved, held_by_other = reserve_project_time(Session(), project, run)
    assert not reserved
    assert held_by_other == 0
    assert run.resource["elapsed_seconds"] >= 2
    assert run.resource["time_budget"]["reservation_state"] == "deferred"


def test_paused_container_elapsed_limits_the_next_run_reservation():
    from datetime import datetime, timedelta, timezone
    from services.worker.scheduler import reserve_project_time

    paused = SimpleNamespace(
        id="paused-container-run",
        status="paused",
        pid=None,
        started_at=(datetime.now(timezone.utc) - timedelta(seconds=1.5)).isoformat(),
        config={"timeout": 0.6},
        resource={
            "elapsed_seconds": 0.2,
            "elapsed_before_attempt": 0.0,
            "paused_live_attempt": True,
            "time_budget": {
                "requested_task_timeout_seconds": 0.6,
                "effective_total_timeout_seconds": 0.6,
                "reservation_state": "held",
            },
        },
    )
    next_run = SimpleNamespace(
        id="next-run",
        status="queued",
        pid=None,
        started_at=None,
        config={"timeout": 10},
        resource={
            "time_budget": {
                "requested_task_timeout_seconds": 10,
                "effective_total_timeout_seconds": 10,
                "reservation_state": "held",
            },
        },
    )

    class Session:
        def scalars(self, statement):
            return [paused, next_run]

    project = SimpleNamespace(id="project", budget={"seconds": 2.5}, revision=4)
    reserved, held_by_other = reserve_project_time(Session(), project, next_run)
    assert reserved
    assert held_by_other == 0
    # The paused container used about 1.5 seconds, leaving about 1 second.
    # A checkpoint-only calculation would incorrectly leave about 1.9 seconds.
    assert 0.8 < next_run.config["timeout"] < 1.1


@pytest.mark.parametrize("pending_marker", [
    "container_reconnect_pending_dispatch",
    "live_process_pending_dispatch",
])
def test_resumed_live_attempt_queue_interval_is_included_before_worker_dispatch(pending_marker):
    from datetime import datetime, timedelta, timezone
    from services.worker.scheduler import reserve_project_time

    resumed = SimpleNamespace(
        id="resumed-container-run",
        status="queued",
        pid=None,
        started_at=(datetime.now(timezone.utc) - timedelta(seconds=1.5)).isoformat(),
        config={"timeout": 10},
        resource={
            "elapsed_seconds": 0.2,
            "elapsed_before_attempt": 0.0,
            pending_marker: True,
            "time_budget": {
                "requested_task_timeout_seconds": 10,
                "effective_total_timeout_seconds": 10,
                "reservation_state": "held",
            },
        },
    )

    class Session:
        def scalars(self, statement):
            return [resumed]

    project = SimpleNamespace(id="project", budget={"seconds": 8}, revision=5)
    reserved, held_by_other = reserve_project_time(Session(), project, resumed)
    assert reserved
    assert held_by_other == 0
    assert resumed.resource["elapsed_seconds"] >= 1.4
    assert resumed.resource[pending_marker] is True


def test_live_child_continues_only_when_worker_dispatches_after_admission(tmp_path, monkeypatch):
    import signal
    from services.worker.main import resume_live_process_for_dispatch

    events = []

    class FakeManager:
        def signal_all(self, signum):
            events.append(signum)

    monkeypatch.setattr("services.worker.main.process_manager", lambda workspace, config: FakeManager())
    resume_live_process_for_dispatch(
        {"execution_backend": "local"}, tmp_path,
        {"live_process_pending_dispatch": True},
    )
    assert events == [signal.SIGCONT]
    events.clear()
    resume_live_process_for_dispatch(
        {"execution_backend": "container"}, tmp_path,
        {"live_process_pending_dispatch": True},
    )
    assert events == []


def test_waiting_snapshot_refresh_does_not_overwrite_concurrent_resume():
    from services.api.db import Project, TaskRun
    from services.worker.main import _lock_waiting_snapshot

    resumed = SimpleNamespace(
        id="waiting-run",
        status="queued",
        resource={
            "live_process_pending_dispatch": True,
            "time_budget": {"effective_total_timeout_seconds": 12, "reservation_state": "held"},
        },
    )
    project = SimpleNamespace(id="project")
    statements = []

    class Session:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def scalar(self, statement):
            statements.append(statement)
            entity = statement.column_descriptions[0]["entity"]
            return project if entity is Project else resumed

    stale_waiting_snapshot = {
        "id": resumed.id,
        "project_id": project.id,
        "status": "waiting",
        "resource": {"waiting_started_at": 1234.0, "elapsed_before_wait": 4.0},
    }
    original_resource = dict(resumed.resource)

    assert _lock_waiting_snapshot(Session(), stale_waiting_snapshot) is None
    assert resumed.resource == original_resource
    assert len(statements) == 2
    assert all(statement._for_update_arg is not None for statement in statements)
    assert statements[-1]._execution_options["populate_existing"] is True


def test_budget_exhausted_elapsed_freezes_when_awaited_child_stops():
    from datetime import datetime, timezone
    from services.worker.scheduler import _elapsed_seconds_at, freeze_budget_exhausted_elapsed

    exhausted = SimpleNamespace(status="budget_exhausted", resource={
        "elapsed_seconds": 60,
        "elapsed_before_wait": 40,
        "waiting_started_at": 100.0,
        "time_budget": {"effective_total_timeout_seconds": 80},
    })
    frozen_at = datetime.fromtimestamp(150.0, timezone.utc)

    assert freeze_budget_exhausted_elapsed(exhausted, frozen_at) == 90
    assert exhausted.resource["elapsed_seconds"] == 90
    assert "waiting_started_at" not in exhausted.resource
    assert "elapsed_before_wait" not in exhausted.resource
    later = datetime.fromtimestamp(900.0, timezone.utc)
    assert _elapsed_seconds_at(exhausted, later) == 90


def test_budget_exhausted_receipt_does_not_start_an_idle_wait_clock():
    from services.worker.main import _waiting_outcome_resource

    resource = {
        "elapsed_seconds": 75,
        "waiting_started_at": 100.0,
        "elapsed_before_wait": 60,
    }
    exhausted = _waiting_outcome_resource(
        resource, {"status": "budget_exhausted", "wait_for": {"process_id": "p1"}},
        observed_at=200.0,
    )
    assert exhausted["elapsed_seconds"] == 75
    assert "waiting_started_at" not in exhausted
    assert "elapsed_before_wait" not in exhausted

    waiting = _waiting_outcome_resource(
        {"elapsed_seconds": 75}, {"status": "waiting", "wait_for": {"process_id": "p1"}},
        observed_at=200.0,
    )
    assert waiting["waiting_started_at"] == 200
    assert waiting["elapsed_before_wait"] == 75


def test_recovery_pauses_detached_running_container_before_queueing_reconnect(tmp_path, monkeypatch):
    from services.worker.main import pause_container_for_reconnect

    (tmp_path / "container_task.json").write_text('{"container_id":"detached"}')
    events = []

    class FakeRunner:
        def __init__(self, config):
            assert config == {"memory": "512m"}

        def status(self, job):
            events.append(("status", job["container_id"]))
            return {"status": "running"}

        def pause(self, job):
            events.append(("pause", job["container_id"]))
            return {"status": "paused"}

    monkeypatch.setattr("services.worker.main.ContainerRunner", FakeRunner)
    state = pause_container_for_reconnect({"container": {"memory": "512m"}}, tmp_path)

    assert state == {"status": "paused"}
    assert events == [("status", "detached"), ("pause", "detached")]


def test_late_completion_receipt_fails_and_charges_actual_project_time(actual_worker):
    h = actual_worker
    h.stop(h.worker)
    _, node = h.project_node(seconds=0.25, timeout=10,
                            budget={"max_runs": 20, "seconds": 0.1, "allow_paid": False})
    run = h.launch(node)
    assert run["status"] == "queued"
    # The first worker tick dispatches the job and then sleeps for 0.4s. The
    # command finishes after its 0.1s reservation but before the next poll,
    # exercising the completed-receipt path that previously bypassed timeout.
    h.start_worker()
    stopped = h.terminal(run, timeout=10)
    assert stopped["status"] == "failed"
    assert "budget" in stopped["error"].lower()
    consumed = stopped["resource"]["elapsed_seconds"]
    assert consumed > 0.1
    assert consumed <= 0.85

    exhausted = h.client.post(f"/api/nodes/{node['id']}/run", json={"request_id": str(uuid.uuid4())})
    assert exhausted.status_code == 409
    assert exhausted.json()["detail"]["code"] == "TIME_BUDGET_EXHAUSTED"


def test_edit_triggered_cancel_records_consumed_time(actual_worker):
    h = actual_worker
    project, node = h.project_node(seconds=25, timeout=25,
                                   budget={"max_runs": 20, "seconds": 8, "allow_paid": False})
    run = h.launch(node)
    h.running(run)
    time.sleep(0.25)
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": graph["revision"],
        "operation": "edit_node",
        "targets": [node["id"]],
        "params": {"instructions": "Updated while cancelling the active execution", "stop_current_run": True},
    })
    cancelled = h.terminal(run)
    assert cancelled["status"] == "cancelled"
    assert cancelled["resource"]["elapsed_seconds"] >= 0.2


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


def test_retry_of_edited_node_preserves_source_revision_and_current_result(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=1)
    marker = h.directory / ("retry-source-" + str(uuid.uuid4()))
    # The marker's existence is checked before it is created so the first run
    # fails and the retry of the saved config can succeed.
    source_script = (
        "import pathlib,sys; marker=pathlib.Path(sys.argv[1]); existed=marker.exists(); "
        "print('OLD_CONFIG_SUCCESS' if existed else 'FIRST_OLD_CONFIG'); "
        "marker.touch(); sys.exit(0 if existed else 7)"
    )
    source_config = {"kind": "command", "command": [sys.executable, "-c", source_script, str(marker)], "timeout": 10}
    h.request("PATCH", f"/api/nodes/{node['id']}", json={"config": source_config, "expected_revision": 1})
    edited_node = h.request("GET", f"/api/nodes/{node['id']}")
    assert edited_node["revision"] == 1

    source = h.launch(edited_node)
    assert h.terminal(source)["status"] == "failed"
    failed_source = h.run(source)
    assert failed_source["node_revision"] == 1
    before_edit = h.request("GET", f"/api/nodes/{node['id']}")
    assert before_edit["latest_run_id"] == source["id"]

    edited_config = {"kind": "command", "command": [sys.executable, "-c", "print('EDITED_CURRENT_CONFIG')"], "timeout": 10}
    h.request("PATCH", f"/api/nodes/{node['id']}", json={"config": edited_config, "expected_revision": 2})
    current_before_retry = h.request("GET", f"/api/nodes/{node['id']}")
    assert current_before_retry["revision"] == 2
    preserved_fields = ("execution_status", "outputs", "deliverable_status", "latest_run_id",
                        "verification_status", "results_current", "last_run_id", "result_revision")
    preserved = {key: current_before_retry.get(key) for key in preserved_fields}

    retry = h.request("POST", f"/api/runs/{source['id']}/retry", json={"request_id": str(uuid.uuid4())})
    assert retry["node_revision"] == failed_source["node_revision"] == 1
    assert retry["config"]["command"] == source_config["command"]
    assert h.terminal(retry)["status"] == "completed"
    completed_retry = h.run(retry)
    assert completed_retry["exit_code"] == 0
    output = (h.output(retry) / "stdout.txt").read_text()
    assert "OLD_CONFIG_SUCCESS" in output

    current_after_retry = h.request("GET", f"/api/nodes/{node['id']}")
    assert current_after_retry["revision"] == 2
    assert current_after_retry["config"] == edited_config
    assert {key: current_after_retry.get(key) for key in preserved} == preserved
    assert current_after_retry.get("latest_run_id") != retry["id"]

    control = h.launch(current_after_retry)
    assert control["config"]["command"] == edited_config["command"]
    assert control["node_revision"] == 2
    assert h.terminal(control)["status"] == "completed"
    assert "EDITED_CURRENT_CONFIG" in (h.output(control) / "stdout.txt").read_text()
    current_after_control = h.request("GET", f"/api/nodes/{node['id']}")
    assert current_after_control["latest_run_id"] == control["id"]
    assert current_after_control["results_current"] is True
    assert current_after_control["result_revision"] == 2


def test_historical_retry_does_not_shadow_current_verified_producer(actual_worker):
    from test_research_verification_workflow import add_node, arithmetic_command, verification_config
    h = actual_worker
    project, node = h.project_node(seconds=0.2)
    marker = h.directory / ('historical-retry-' + str(uuid.uuid4()))
    old_config = {'kind': 'command', 'timeout': 10, 'command': [sys.executable, '-c',
        "import sys; from pathlib import Path; p=Path(sys.argv[1]); existed=p.exists(); p.touch(); sys.exit(0 if existed else 7)", str(marker)]}
    h.request('PATCH', f"/api/nodes/{node['id']}", json={'config': old_config, 'expected_revision': 1})
    source = h.launch(node)
    assert h.terminal(source)['status'] == 'failed'
    current_config = {'kind': 'experiment', 'command': arithmetic_command(500), 'timeout': 10}
    h.request('PATCH', f"/api/nodes/{node['id']}", json={'config': current_config, 'expected_revision': 2})
    current = h.launch(node)
    assert h.terminal(current)['status'] == 'completed'
    verifier = add_node(h, project, type='verification', title='Verify current revision', config=verification_config(node))
    checked = h.launch(verifier)
    assert h.terminal(checked)['status'] == 'completed'
    verdict = h.request('GET', f"/api/runs/{current['id']}/verification")
    assert verdict['verification_status'] == 'accepted', verdict

    historical = h.request('POST', f"/api/runs/{source['id']}/retry", json={'request_id': str(uuid.uuid4())})
    assert h.terminal(historical)['status'] == 'completed'
    assert historical['node_revision'] != current['node_revision']
    verdict = h.request('GET', f"/api/runs/{current['id']}/verification")
    assert verdict['verification_status'] == 'accepted', verdict
    rechecked = h.launch(verifier)
    assert rechecked['dependencies'] == [current['id']]
    assert h.terminal(rechecked)['status'] == 'completed'
    consumer = add_node(h, project, type='analysis', title='Consume current measurements',
        config={'kind': 'command', 'command': [sys.executable, '-c', "from pathlib import Path; assert Path('observed.json').is_file()"]},
        inputs=[{'node_id': node['id'], 'path': 'metrics.json', 'destination': 'observed.json'}])
    graph = h.request('GET', f"/api/projects/{project['id']}/graph")
    h.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
        'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'], 'operation': 'add_dependency',
        'params': {'source': node['id'], 'target': consumer['id']}})
    consumed = h.launch(consumer)
    assert consumed['dependencies'] == [current['id']]
    assert h.terminal(consumed)['status'] == 'completed'


def test_same_revision_retry_remains_current(actual_worker):
    h = actual_worker
    _, node = h.project_node(seconds=1)
    marker = h.directory / ("same-revision-retry-" + str(uuid.uuid4()))
    script = (
        "import pathlib,sys; marker=pathlib.Path(sys.argv[1]); existed=marker.exists(); "
        "print('RETRIED' if existed else 'FIRST'); marker.touch(); sys.exit(0 if existed else 7)"
    )
    config = {"kind": "command", "command": [sys.executable, "-c", script, str(marker)], "timeout": 10}
    h.request("PATCH", f"/api/nodes/{node['id']}", json={"config": config, "expected_revision": 1})
    current = h.request("GET", f"/api/nodes/{node['id']}")
    source = h.launch(current)
    assert h.terminal(source)["status"] == "failed"
    retry = h.request("POST", f"/api/runs/{source['id']}/retry", json={"request_id": str(uuid.uuid4())})
    assert retry["node_revision"] == source["node_revision"]
    assert h.terminal(retry)["status"] == "completed"
    latest = h.request("GET", f"/api/nodes/{node['id']}")
    assert latest["latest_run_id"] == retry["id"]
    assert latest["results_current"] is True


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


def test_dependent_run_reserves_only_remaining_project_time(actual_worker):
    h = actual_worker
    project, producer = h.project_node(seconds=0.6, budget={"max_runs": 20, "seconds": 2, "allow_paid": False}, timeout=20)
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    consumer_id = str(uuid.uuid4())
    created = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": graph["revision"],
        "operation": "add_node",
        "targets": [],
        "params": {
            "id": consumer_id,
            "branch_id": producer["branch_id"],
            "type": "experiment",
            "title": "Consumer runs after its producer",
            "config": {"kind": "command", "command": [sys.executable, "-c", "import time; time.sleep(5)"], "timeout": 10},
        },
    })
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": created["graph"]["revision"],
        "operation": "add_dependency",
        "targets": [],
        "params": {"source": producer["id"], "target": consumer_id},
    })

    queued = h.request("POST", f"/api/nodes/{consumer_id}/run", json={"request_id": str(uuid.uuid4()), "scope": "ancestors"})
    producer_run = next(run for run in queued["runs"] if run["node_id"] == producer["id"])
    consumer_run = next(run for run in queued["runs"] if run["node_id"] == consumer_id)
    assert consumer_run["resource"]["time_budget"]["reservation_state"] == "deferred"
    assert consumer_run["config"]["timeout"] == 10

    h.running(producer_run)
    completed_producer = h.terminal(producer_run)
    assert completed_producer["status"] == "completed"
    started_consumer = wait_until(lambda: (r if (r := h.run(consumer_run)).get("resource", {}).get("time_budget", {}).get("reservation_state") == "held" else None))
    assert 0 < started_consumer["config"]["timeout"] < 2
    assert started_consumer["resource"]["time_budget"]["effective_total_timeout_seconds"] == started_consumer["config"]["timeout"]
    stopped_consumer = h.terminal(consumer_run, timeout=15)
    assert stopped_consumer["status"] == "failed"
    assert "budget" in stopped_consumer["error"].lower() or "timed out" in stopped_consumer["error"].lower()
    aggregate_elapsed = (completed_producer["resource"]["elapsed_seconds"] +
                         stopped_consumer["resource"]["elapsed_seconds"])
    assert aggregate_elapsed <= 2.75


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


@pytest.mark.parametrize("node_type", ["experiment", "figure", "paper"])
def test_upstream_invalidation_after_enqueue_preserves_descendant_rerun(actual_worker, node_type):
    h = actual_worker
    project = h.request("POST", "/api/projects", json={
        "name": "Preserve invalidation after enqueue",
        "budget": {"max_runs": 12, "seconds": 90, "allow_paid": False},
    })
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    branch_id = graph["branches"][0]["id"]

    def add_command_node(title, command, kind="experiment"):
        current = h.request("GET", f"/api/projects/{project['id']}/graph")
        node_id = str(uuid.uuid4())
        result = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": current["revision"],
            "operation": "add_node",
            "params": {
                "id": node_id,
                "branch_id": branch_id,
                "type": kind,
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
    child = add_command_node("Descendant", [sys.executable, "-c", "import time; time.sleep(1.5); print('child')"], node_type)
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
    assert bool(current_child.get("needs_rerun")) == (node_type == "experiment"), current_child
    assert current_child["deliverable_status"] == "needs_update", current_child
    assert current_child.get("results_current") is False, current_child
    assert current_child.get("last_run_id") == accepted_child_run["id"], current_child
    assert current_child["outputs"][0]["id"] == accepted_child_run["id"], current_child

    affected = h.request("POST", f"/api/nodes/{root['id']}/run", json={
        "request_id": str(uuid.uuid4()), "scope": "affected",
    })
    scheduled = affected.get("runs", [affected])
    assert child["id"] in [run["node_id"] for run in scheduled]



@pytest.mark.parametrize("node_type", ["figure", "paper"])
def test_restart_does_not_promote_invalidated_first_refresh_result(actual_worker, node_type):
    from test_research_verification_workflow import add_node
    h = actual_worker
    project = h.request("POST", "/api/projects", json={
        "name": "First refresh result remains stale after restart", "mode": "manual",
        "budget": {"max_runs": 12, "seconds": 90, "allow_paid": False},
    })
    source = add_node(h, project, type="experiment", title="Source", config={
        "kind": "command", "command": [sys.executable, "-c", "print('source v1')"], "timeout": 10})
    child = add_node(h, project, type=node_type, title="First refresh", config={
        "kind": "command", "command": [sys.executable, "-c", "import time; time.sleep(1.5); print('old snapshot')"], "timeout": 10})
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()), "expected_revision": graph["revision"], "operation": "add_dependency",
        "params": {"source": source["id"], "target": child["id"], "relation": "evidence"}})
    run = h.launch(child)
    assert not run["dependencies"]  # Evidence propagation is not an execution dependency.
    h.running(run)
    h.request("PATCH", f"/api/nodes/{source['id']}", json={"config": {
        "kind": "command", "command": [sys.executable, "-c", "print('source v2')"], "timeout": 10}})
    assert h.terminal(run)["status"] == "completed"
    before = h.request("GET", f"/api/nodes/{child['id']}")
    assert before.get("results_current") is False, before
    assert before["deliverable_status"] == "needs_update", before
    assert not before.get("result_revision") and not before["outputs"], before

    h.stop(h.worker)
    h.start_worker()
    # A real command completion establishes that startup reconciliation finished.
    assert h.terminal(h.launch(source))["status"] == "completed"
    after = h.request("GET", f"/api/nodes/{child['id']}")
    assert after.get("results_current") is False, after
    assert after["deliverable_status"] == "needs_update", after
    assert not after.get("result_revision") and not after["outputs"], after
    assert after["latest_run_id"] == run["id"]


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


def test_latest_same_revision_failure_rearms_rerun_for_affected_scope(actual_worker):
    h = actual_worker
    project = h.request("POST", "/api/projects", json={
        "name": "Rearm rerun after a same-revision failure",
        "budget": {"max_runs": 8, "seconds": 60, "allow_paid": False},
    })
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    branch_id = graph["branches"][0]["id"]

    def add_command_node(title, command):
        current = h.request("GET", f"/api/projects/{project['id']}/graph")
        node_id = str(uuid.uuid4())
        created = h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
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
        return next(node for node in created["graph"]["nodes"] if node["id"] == node_id)

    root = add_command_node("Root", [sys.executable, "-c", "print('root')"])
    child = add_command_node("Child", [sys.executable, "-c", "print('child')"])
    grandchild = add_command_node("Grandchild", [sys.executable, "-c", "print('grandchild')"])
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": current["revision"],
        "operation": "add_dependency",
        "params": {"source": root["id"], "target": child["id"]},
    })
    current = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": current["revision"],
        "operation": "add_dependency",
        "params": {"source": child["id"], "target": grandchild["id"]},
    })

    assert h.terminal(h.launch(root))["status"] == "completed"
    first_success = h.launch(child)
    assert h.terminal(first_success)["status"] == "completed"
    first_grandchild_success = h.launch(grandchild)
    assert h.terminal(first_grandchild_success)["status"] == "completed"
    after_success = h.request("GET", f"/api/projects/{project['id']}/graph")
    successful_child = next(node for node in after_success["nodes"] if node["id"] == child["id"])
    assert successful_child["results_current"] is True
    assert successful_child.get("needs_rerun") is not True

    control = h.request("POST", f"/api/nodes/{root['id']}/run", json={
        "request_id": str(uuid.uuid4()), "scope": "affected",
    })
    control_runs = control.get("runs", [control])
    assert [run["node_id"] for run in control_runs] == [root["id"]]
    assert h.terminal(control_runs[0])["status"] == "completed"

    failed = h.request("POST", f"/api/nodes/{child['id']}/run", json={
        "request_id": str(uuid.uuid4()),
        "scope": "single",
        "config": {"kind": "command", "command": [sys.executable, "-c", "raise SystemExit(7)"], "timeout": 10},
    })
    assert h.terminal(failed)["status"] == "failed"

    after_failure = h.request("GET", f"/api/projects/{project['id']}/graph")
    failed_child = next(node for node in after_failure["nodes"] if node["id"] == child["id"])
    assert failed_child["latest_run_id"] == failed["id"]
    assert failed_child["results_current"] is False
    assert failed_child.get("needs_rerun") is True, failed_child
    stale_grandchild = next(node for node in after_failure["nodes"] if node["id"] == grandchild["id"])
    assert stale_grandchild["results_current"] is False, stale_grandchild
    assert stale_grandchild["deliverable_status"] == "needs_update", stale_grandchild
    assert stale_grandchild.get("needs_rerun") is True, stale_grandchild

    affected = h.request("POST", f"/api/nodes/{root['id']}/run", json={
        "request_id": str(uuid.uuid4()), "scope": "affected",
    })
    affected_runs = affected.get("runs", [affected])
    run_by_node = {run["node_id"]: run for run in affected_runs}
    assert child["id"] in run_by_node
    assert grandchild["id"] in run_by_node
    assert run_by_node[grandchild["id"]]["dependencies"] == [run_by_node[child["id"]]["id"]]
    for run in affected_runs:
        assert h.terminal(run)["status"] == "completed"
    after_recovery = h.request("GET", f"/api/projects/{project['id']}/graph")
    current_grandchild = next(node for node in after_recovery["nodes"] if node["id"] == grandchild["id"])
    assert current_grandchild["results_current"] is True, current_grandchild
    assert current_grandchild["latest_run_id"] == run_by_node[grandchild["id"]]["id"]


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
