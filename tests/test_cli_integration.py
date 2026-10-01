"""CLI/API/worker integration in an isolated interpreter and data directory.

The pytest parent imports no backend modules. Child scenarios use real API
transactions, real graph receipts and a real CPU experiment subprocess. No
model provider, paid call, user service or production research directory is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def integration_scenario(api, directory):
    import contextlib
    import io
    import time
    import zipfile

    import httpx
    from forest_cli.client import CliError, ForestClient
    from forest_cli.main import build_parser, execute
    from services.worker.main import WorkerLoop

    class APITransport(httpx.BaseTransport):
        def handle_request(self, request):
            response = api.request(request.method, str(request.url),
                                   headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=response.content, request=request)

    def factory(endpoint, **kwargs):
        return ForestClient(endpoint, **kwargs, transport=APITransport())

    def command(*arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = execute(build_parser().parse_args(["--json", *arguments]), client_factory=factory)
        return code, json.loads(output.getvalue())

    def write(name, value):
        path = directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    goal = directory / "goal.md"
    goal.write_text("Measure trapezoidal integration error using actual CPU computation.", encoding="utf-8")
    configuration = write("project-config.json", {"publication_profile": {"id": "operational"}})
    budget = write("budget.json", {"allow_paid": False, "max_runs": 8, "seconds": 60})
    code, initialized = command("--server", "http://testserver", "init", "--name", "CLI measured integration",
                                "--goal-file", str(goal), "--mode", "manual",
                                "--config-file", configuration, "--budget-file", budget)
    assert code == 0
    project = initialized["project"]
    pid = project["id"]
    assert project["mode"] == "manual" and project["budget"]["allow_paid"] is False
    assert project["config"]["publication_profile"]["id"] == "operational"
    binding = json.loads((directory / ".forest/project.json").read_text())
    assert binding == {"endpoint": "http://testserver", "project_id": pid}

    calculation = "\n".join([
        "import json",
        "from pathlib import Path",
        "n = 5000",
        "h = 1.0 / n",
        "observed = h * (sum((i*h)**2 for i in range(1, n)) + 0.5)",
        "metrics = {'integral': observed, 'absolute_error': abs(observed - 1.0/3.0), 'intervals': n}",
        "Path('metrics.json').write_text(json.dumps(metrics))",
        "print('MEASURED ' + json.dumps(metrics), flush=True)",
    ])
    node_file = write("node.json", {"title": "Trapezoid experiment", "type": "experiment",
                                   "instructions": "Execute the numerical method and record actual error.",
                                   "config": {"kind": "experiment", "command": [sys.executable, "-c", calculation]}})
    code, added = command("node", "add", "--file", node_file, "--request-id", "one-node")
    assert code == 0
    node = added["graph"]["nodes"][0]
    nid = node["id"]
    code, repeated_add = command("node", "add", "--file", node_file, "--request-id", "one-node")
    assert code == 0 and repeated_add == added
    assert len(api.get(f"/api/projects/{pid}/graph").json()["nodes"]) == 1

    graph_before = api.get(f"/api/projects/{pid}/graph").json()
    preview_file = write("node-preview.json", {"instructions": "A preview must not change this experiment."})
    code, preview = command("node", "edit", nid, "--file", preview_file, "--dry-run")
    assert code == 0 and preview["changed_nodes"]
    graph_after = api.get(f"/api/projects/{pid}/graph").json()
    assert graph_after["revision"] == graph_before["revision"]
    assert graph_after["nodes"][0]["instructions"] == node["instructions"]

    stale_file = write("stale-edit.json", {"expected_revision": 0, "instructions": "Stale edit"})
    with pytest.raises(CliError) as conflict:
        command("node", "edit", nid, "--file", stale_file)
    assert conflict.value.exit_code == 4
    assert api.get(f"/api/projects/{pid}/graph").json()["revision"] == graph_before["revision"]

    code, submitted = command("node", "run", nid, "--request-id", "one-real-experiment")
    assert code == 0 and submitted["submission"] == "accepted"
    run = submitted["result"]
    rid = run["id"]
    code, repeated_run = command("node", "run", nid, "--request-id", "one-real-experiment")
    assert code == 0 and repeated_run["result"]["id"] == rid
    assert len(api.get(f"/api/projects/{pid}/runs").json()) == 1

    worker = WorkerLoop()
    deadline = time.monotonic() + 25
    try:
        while time.monotonic() < deadline:
            worker.tick()
            actual = api.get(f"/api/runs/{rid}").json()
            if actual["status"] in ("completed", "failed", "interrupted", "waiting_input", "budget_exhausted"):
                break
            time.sleep(.05)
        assert actual["status"] == "completed", actual
        assert actual["kind"] == "experiment"
        assert abs(actual["metrics"]["integral"] - 1/3) < 1e-7
        assert 0 < actual["metrics"]["absolute_error"] < 1e-7
        assert actual["metrics"]["intervals"] == 5000
        output = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / pid / actual["output_path"]
        measured = json.loads((output / "metrics.json").read_text())
        assert measured == actual["metrics"]
        assert (output / "result.json").is_file()
        code, waited = command("run", "wait", rid, "--timeout", "1", "--interval", ".01")
        assert code == 0 and waited["outcome"] == "completed"
        code, logs = command("run", "logs", rid)
        assert code == 0 and "MEASURED" in logs["text"]
        code, evidence = command("run", "evidence", rid)
        assert code == 0 and evidence["lineage"]["run_id"] == rid
        record = evidence["lineage"]["runs"][0]
        assert record["current"] is True and record["metrics"] == measured
        assert any(item["path"].endswith("metrics.json") for item in record["files"])

        code, status = command("status")
        assert code == 0
        assert status["research"]["counts"]["completed"] == 1
        assert status["project"]["mode"] == "manual"
        assert status["delivery"]["profile"]["id"] == "operational"
        # This explicitly scoped operational check is not a paper-readiness claim.
        assert "submission_ready" not in status["delivery"]

        archive = directory / "project-export.zip"
        code, exported = command("project", "export", "--output", str(archive))
        assert code == 0 and exported["bytes"] == archive.stat().st_size
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read("forest-project.json"))
            assert manifest["project"]["id"] == pid
            assert len(manifest["runs"]) == 1 and manifest["runs"][0]["status"] == "completed"
            assert any(name.endswith("metrics.json") for name in bundle.namelist())
        with pytest.raises(CliError) as exists:
            command("project", "export", "--output", str(archive))
        assert exists.value.code == "FILE_EXISTS"

        code, paper = command("paper", "show")
        assert code == 0 and paper["status"] == "draft"
        with pytest.raises(CliError) as stale_pdf:
            command("paper", "export", "--format", "pdf", "--output", str(directory / "stale.pdf"))
        assert stale_pdf.value.code == "STALE_PDF"
        assert not (directory / "stale.pdf").exists()
        assert command("paper", "export", "--format", "source", "--output", str(directory / "source.zip"))[0] == 0
        # No draft or compile task was secretly enqueued by inspection/export.
        assert len(api.get(f"/api/projects/{pid}/runs").json()) == 1
        assert command("project", "use", pid)[0] == 0
        assert command("node", "context", nid)[1]["controls"]["goal"] == goal.read_text()
    finally:
        # A failed assertion must not leave a real numerical process behind.
        states = api.get(f"/api/projects/{pid}/runs").json()
        for state in states:
            if state["status"] not in ("completed", "failed", "cancelled", "interrupted", "skipped"):
                api.post(f"/api/runs/{state['id']}/cancel", json={})
        for process in worker.processes.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        worker.shutdown()


def test_cli_real_api_and_measured_experiment(tmp_path):
    data = tmp_path / "data"
    client_directory = tmp_path / "client"
    client_directory.mkdir()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "FOREST_DATA_DIR": str(data),
           "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "isolated.sqlite"),
           "FOREST_OWNER_TOKEN": "isolated-cli-integration-owner", "FOREST_MODEL": "",
           "FOREST_SERVER": "", "FOREST_PROJECT_ID": "", "FOREST_TOKEN_FILE": "",
           "FOREST_CLI_CONFIG": str(tmp_path / "client-config.json")}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--integration", str(client_directory)],
                            cwd=client_directory, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLI INTEGRATION PASSED" in result.stdout
    assert (tmp_path / "isolated.sqlite").is_file()


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app

    assert sys.argv[1] == "--integration"
    with TestClient(app) as client:
        integration_scenario(client, Path(sys.argv[2]))
    print("CLI INTEGRATION PASSED")
