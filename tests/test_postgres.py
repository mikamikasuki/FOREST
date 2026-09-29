"""Opt-in PostgreSQL integration with real HTTP API and two real workers.

FOREST_TEST_POSTGRES=1 uses the current local PostgreSQL role on port 5432.
A postgresql:// DSN can instead identify the local test server. Tests create and
finally drop only a newly generated forest_test_* database; application and demo
databases are never opened by the application under test.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import quote, unquote, urlparse
import uuid

import httpx
import psutil
import psycopg
import pytest

from test_worker import Harness, wait_until

pytestmark = pytest.mark.skipif(not os.environ.get("FOREST_TEST_POSTGRES"), reason="Set FOREST_TEST_POSTGRES to explicitly authorize a temporary PostgreSQL test database")


def _postgres_parameters():
    requested = os.environ.get("FOREST_TEST_POSTGRES", "1")
    if requested.lower() in {"1", "true", "yes"}:
        requested = f"postgresql://{getpass.getuser()}@127.0.0.1:5432/postgres"
    requested = requested.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlparse(requested)
    assert parsed.scheme == "postgresql", "Use FOREST_TEST_POSTGRES=1 or a postgresql:// test-server DSN"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    user = unquote(parsed.username or getpass.getuser())
    maintenance = parsed.path.lstrip("/") or "postgres"
    # No database supplied in the variable becomes the application database.
    # It is used only for administrative CREATE/DROP connections.
    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    flags = ["--host", host, "--port", str(port), "--username", user, "--maintenance-db", maintenance]
    password = ":" + quote(unquote(parsed.password), safe="") if parsed.password else ""
    authority = f"{quote(user, safe='')}{password}@{host}:{port}"
    return env, flags, authority


@pytest.fixture(scope="module")
def postgres_workers(tmp_path_factory):
    createdb, dropdb = shutil.which("createdb"), shutil.which("dropdb")
    assert createdb and dropdb, "PostgreSQL client tools createdb/dropdb must be installed for this explicit test"
    directory = tmp_path_factory.mktemp("postgres-workers")
    database = "forest_test_" + uuid.uuid4().hex
    assert database.startswith("forest_test_") and len(database) < 63
    env, flags, authority = _postgres_parameters()
    created = False
    harness = None
    try:
        subprocess.run([createdb, *flags, database], env=env, check=True, capture_output=True, text=True, timeout=20)
        created = True
        harness = Harness(directory)
        harness.env["FOREST_DATABASE_URL"] = f"postgresql+psycopg://{authority}/{database}"
        harness.postgres_dsn = f"postgresql://{authority}/{database}"
        harness.test_database = database
        harness.start_api()
        assert harness.request("GET", "/api/health")["database"] == "postgresql"
        harness.start_worker()
        harness.workers = [harness.worker, harness.spawn("worker-2", [sys.executable, "-m", "services.worker.main"])]
        wait_until(lambda: len([w for w in harness.request("GET", "/api/system")["workers"] if w["online"]]) == 2)
        yield harness
    finally:
        if harness:
            harness.cleanup()
        if created:
            # The name is freshly generated above. Never accept a target name
            # from configuration or use a production database as fallback.
            subprocess.run([dropdb, *flags, "--force", database], env=env, check=True, capture_output=True, text=True, timeout=20)


def test_postgres_concurrent_graph_edits_and_receipts(postgres_workers):
    h = postgres_workers
    project, node = h.project_node(seconds=1)
    path = f"/api/projects/{project['id']}/graph/commands"
    graph = h.request("GET", f"/api/projects/{project['id']}/graph")
    def edit(index):
        return h.client.post(path, json={"request_id": f"concurrent-edit-{index}", "expected_revision": graph["revision"], "operation": "edit_node",
                            "targets": [node["id"]], "params": {"instructions": f"window-{index}"}})
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(edit, range(8)))
    assert sorted(r.status_code for r in responses) == [200] + [409] * 7, [(r.status_code, r.text) for r in responses]
    winner = next(i for i, response in enumerate(responses) if response.status_code == 200)
    saved = h.request("GET", f"/api/projects/{project['id']}/graph")
    assert saved["revision"] == graph["revision"] + 1
    assert next(n for n in saved["nodes"] if n["id"] == node["id"])["instructions"] == f"window-{winner}"
    replay = h.client.post(path, json={"request_id": f"concurrent-edit-{winner}", "expected_revision": graph["revision"], "operation": "edit_node",
                          "targets": [node["id"]], "params": {"instructions": f"window-{winner}"}})
    assert replay.status_code == 200 and replay.json() == responses[winner].json()
    assert h.request("GET", f"/api/projects/{project['id']}/graph")["revision"] == saved["revision"]


def test_postgres_concurrent_run_clicks_create_one_job(postgres_workers):
    h = postgres_workers
    project, node = h.project_node(seconds=1)
    def launch(_):
        return h.client.post(f"/api/nodes/{node['id']}/run", json={"request_id": "one-postgres-launch", "scope": "single"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(launch, range(8)))
    assert all(r.status_code == 200 for r in responses), [(r.status_code, r.text) for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    runs = h.request("GET", f"/api/projects/{project['id']}/runs")
    assert len(runs) == 1
    finished = h.terminal(runs[0])
    assert finished["status"] == "completed", finished
    assert finished["metrics"]["ticks"] > 0


def test_postgres_two_workers_enforce_shared_cpu_reservations_across_projects(postgres_workers):
    h = postgres_workers
    # Queue the contenders before both workers begin to claim them; two
    # different Project row locks cannot substitute for the global slot lock.
    for worker in h.workers:
        h.stop(worker)
    p1, n1 = h.project_node(seconds=1.5)
    p2, n2 = h.project_node(seconds=1.5)
    h.env['FOREST_WORKER_CPU_SLOTS']='2'
    for node in (n1,n2):
        h.request('PATCH',f"/api/nodes/{node['id']}",json={'config':{**node['config'],'resources':{'cpu':2}}})
    jobs = [h.launch(n1 if index % 2 == 0 else n2, f"global-slot-{index}") for index in range(6)]
    job_ids = [j["id"] for j in jobs]
    h.start_worker()
    h.workers = [h.worker, h.spawn("worker-2-restarted", [sys.executable, "-m", "services.worker.main"])]
    observed_active = set()
    deadline = time.monotonic() + 40
    with psycopg.connect(h.postgres_dsn, autocommit=True) as connection:
        while time.monotonic() < deadline:
            rows = connection.execute("SELECT id,status,pid,process_created,worker_id FROM task_runs WHERE id=ANY(%s)", (job_ids,)).fetchall()
            active = [r for r in rows if r[1] in ("running", "paused", "pausing")]
            assert len(active) <= 1, f"Two jobs exceeded the configured CPU reservation pool: {[(r[0], r[1], r[4]) for r in active]}"
            for row in active:
                observed_active.add(row[0])
                if row[2] and (row[2], row[3]) not in h.run_processes:
                    h.run_processes.append((row[2], row[3]))
            if len(rows) == 6 and all(r[1] in ("completed", "failed", "cancelled", "interrupted") for r in rows):
                break
            time.sleep(0.025)
        else:
            pytest.fail("The queued PostgreSQL jobs did not finish within 40 seconds")
        records = connection.execute("SELECT id,status,started_at,finished_at,worker_id FROM task_runs WHERE id=ANY(%s) ORDER BY started_at", (job_ids,)).fetchall()
    assert len(observed_active) == 6, "Every real process must have been observed running"
    assert all(row[1] == "completed" for row in records), records
    for previous, following in zip(records, records[1:]):
        assert dt.datetime.fromisoformat(following[2]) >= dt.datetime.fromisoformat(previous[3]), "Measured exclusive CPU-reservation lifetimes overlapped"
    assert len([w for w in h.request("GET", "/api/system")["workers"] if w["online"]]) == 2
    assert {j["project_id"] for j in jobs} == {p1["id"], p2["id"]}
    assert all((h.output(job) / "result.json").is_file() for job in jobs)


def test_postgres_holds_allocation_until_owner_records_terminal_state(postgres_workers):
    h = postgres_workers
    for worker in h.workers:
        h.stop(worker)
    h.env['FOREST_WORKER_CPU_SLOTS'] = '2'
    _, first_node = h.project_node(seconds=1.5)
    _, next_node = h.project_node(seconds=.3)
    for node in (first_node, next_node):
        h.request('PATCH', f"/api/nodes/{node['id']}", json={'config': {**node['config'], 'resources': {'cpu': 2}}})
    h.start_worker()
    owner = h.worker
    h.workers = [owner]
    first = h.launch(first_node)
    running = h.running(first)
    # Stop only the scheduling worker. Its independently owned executor really
    # finishes, leaving a real receipt and an un-reconciled running database row.
    os.kill(owner.pid, signal.SIGSTOP)
    observations = []
    try:
        contender = h.spawn('worker-terminal-gap', [sys.executable, '-m', 'services.worker.main'])
        h.workers.append(contender)
        following = h.launch(next_node)
        wait_until((h.output(first) / 'result.json').exists, timeout=8)
        def executor_stopped():
            try:
                return psutil.Process(running['pid']).status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return True
        wait_until(executor_stopped, timeout=5)
        with psycopg.connect(h.postgres_dsn, autocommit=True) as connection:
            wait_until(lambda: connection.execute('SELECT id FROM workers WHERE pid=%s', (contender.pid,)).fetchone(), timeout=5)
            until = time.monotonic() + 2
            while time.monotonic() < until:
                rows = connection.execute('SELECT id,status,pid,worker_id FROM task_runs WHERE id=ANY(%s)', ([first['id'], following['id']],)).fetchall()
                observations.append({'rows': rows, 'first_executor_stopped': executor_stopped()})
                by_id = {row[0]: row for row in rows}
                assert by_id[first['id']][1] == 'running'
                assert by_id[following['id']][1] == 'queued', 'An unreconciled running reservation was released before its owner committed the terminal state'
                time.sleep(.05)
    finally:
        (h.directory / 'terminal-gap-observations.json').write_text(json.dumps(observations, indent=2))
        os.kill(owner.pid, signal.SIGCONT)
    assert h.terminal(first)['status'] == 'completed'
    assert h.terminal(following)['status'] == 'completed'


def test_postgres_graph_and_completed_results_survive_api_restart(postgres_workers):
    h = postgres_workers
    project, node = h.project_node(seconds=1)
    h.request("PATCH", f"/api/nodes/{node['id']}", json={"instructions": "Persist this exact PostgreSQL content"})
    run = h.launch(node, "persisted-launch")
    completed = h.terminal(run)
    assert completed["status"] == "completed", completed
    before = h.request("GET", f"/api/projects/{project['id']}/graph")
    h.stop(h.api)
    h.client.close()
    h.client = httpx.Client(base_url=h.base_url, timeout=12)
    h.start_api()
    after = h.request("GET", f"/api/projects/{project['id']}/graph")
    assert after == before
    assert h.run(run)["status"] == "completed"
    assert h.run(run)["metrics"] == completed["metrics"]
    assert h.request("GET", f"/api/runs/{run['id']}/output")["text"]
    with psycopg.connect(h.postgres_dsn) as connection:
        database = connection.execute("SELECT current_database()").fetchone()[0]
        assert database == h.test_database and database.startswith("forest_test_")
        assert connection.execute("SELECT COUNT(*) FROM projects WHERE id=%s", (project["id"],)).fetchone()[0] == 1
