"""CLI contracts against HTTP responses, without importing the server runtime."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from forest_cli import main as cli
from forest_cli.client import CliError, ForestClient


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch, tmp_path):
    for name in ("FOREST_SERVER", "FOREST_OWNER_TOKEN", "FOREST_TOKEN_FILE", "FOREST_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOREST_CLI_CONFIG", str(tmp_path / "cli.json"))
    monkeypatch.chdir(tmp_path)


def object_file(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


class HTTPScenario:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.connections = []

    def dispatch(self, request):
        body = json.loads(request.content) if request.content else None
        call = {"method": request.method, "path": request.url.path,
                "params": dict(request.url.params), "body": body,
                "timeout": request.extensions.get("timeout")}
        self.calls.append(call)
        value = self.handler(call)
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    def factory(self, endpoint, **kwargs):
        self.connections.append({"endpoint": endpoint, **kwargs})
        return ForestClient(endpoint, **kwargs, transport=httpx.MockTransport(self.dispatch))

    def execute(self, arguments):
        return cli.execute(cli.build_parser().parse_args(arguments), client_factory=self.factory)


def unexpected(call):
    raise AssertionError(f"Unexpected API request: {call}")


@pytest.mark.parametrize("arguments", [
    ["--server", "http://selected.test:9000", "--project", "p1", "--json", "--timeout", "4", "node", "list"],
    ["node", "--server", "http://selected.test:9000", "--project", "p1", "--json", "--timeout", "4", "list"],
    ["node", "list", "--server", "http://selected.test:9000", "--project", "p1", "--json", "--timeout", "4"],
])
def test_global_flags_work_at_each_command_level(arguments, capsys):
    scenario = HTTPScenario(lambda call: {"revision": 7, "nodes": []}
                            if call["path"] == "/api/projects/p1/graph" else unexpected(call))
    assert scenario.execute(arguments) == 0
    assert scenario.connections == [{"endpoint": "http://selected.test:9000", "token": None, "timeout": 4.0}]
    assert json.loads(capsys.readouterr().out) == {"revision": 7, "nodes": []}


def test_init_transmits_goal_mode_and_explicit_budgets_and_binds(tmp_path, capsys):
    goal = tmp_path / "goal.md"
    goal.write_text("Compare measured integration errors.\n保留原始测量。", encoding="utf-8")
    budget = {"allow_paid": False, "max_runs": 4, "seconds": 60}
    config = {"publication_profile": {"id": "operational"}}
    scenario = HTTPScenario(lambda call: {"id": "created", **call["body"]}
                            if call["method"] == "POST" and call["path"] == "/api/projects" else unexpected(call))
    assert scenario.execute(["--json", "init", "--name", "Measurement", "--goal-file", str(goal),
                             "--mode", "manual", "--budget-file", object_file(tmp_path, "budget.json", budget),
                             "--config-file", object_file(tmp_path, "project.json", config)]) == 0
    assert scenario.calls[0]["body"] == {"name": "Measurement", "goal": goal.read_text(),
                                         "description": "", "mode": "manual", "budget": budget, "config": config}
    binding = json.loads((tmp_path / ".forest/project.json").read_text())
    assert binding == {"endpoint": "http://127.0.0.1:8000", "project_id": "created"}
    assert json.loads(capsys.readouterr().out)["project"]["id"] == "created"
    assert len(scenario.calls) == 1  # Creating a project never starts a controller.


def test_project_create_keeps_existing_binding_and_default_paid_policy(tmp_path):
    local = tmp_path / ".forest/project.json"
    local.parent.mkdir()
    local.write_text(json.dumps({"endpoint": "http://bound.test", "project_id": "original"}))
    scenario = HTTPScenario(lambda call: {"id": "new", **call["body"]})
    assert scenario.execute(["project", "create", "--name", "New"]) == 0
    assert scenario.calls[0]["body"]["mode"] == "assisted"
    assert "budget" not in scenario.calls[0]["body"]  # Retain the server's unpaid default.
    assert json.loads(local.read_text())["project_id"] == "original"


def test_connect_stores_only_token_file_reference(tmp_path):
    token = tmp_path / "owner-token"
    token.write_text("private-owner-token\n")
    config = tmp_path / "custom-config.json"
    scenario = HTTPScenario(lambda call: {"status": "ok"} if call["path"] == "/api/health" else [])
    assert scenario.execute(["connect", "https://remote.test", "--token-file", str(token), "--config", str(config)]) == 0
    assert scenario.connections[0]["token"] == "private-owner-token"
    saved = config.read_text()
    assert "private-owner-token" not in saved
    assert json.loads(saved) == {"endpoint": "https://remote.test", "token_file": str(token)}


def test_connect_to_another_server_does_not_forward_saved_owner_token(tmp_path):
    token = tmp_path / "old-server-token"
    token.write_text("only-for-original-server")
    configuration = tmp_path / "cli.json"
    configuration.write_text(json.dumps({"endpoint": "https://original.test", "token_file": str(token)}))
    scenario = HTTPScenario(lambda call: {"status": "ok"} if call["path"] == "/api/health" else [])
    assert scenario.execute(["connect", "https://different.test"]) == 0
    assert scenario.connections[0]["endpoint"] == "https://different.test"
    assert scenario.connections[0]["token"] is None
    assert json.loads(configuration.read_text()) == {"endpoint": "https://different.test"}


def test_init_refuses_existing_binding_before_any_http_request(tmp_path):
    binding = tmp_path / ".forest/project.json"
    binding.parent.mkdir()
    binding.write_text(json.dumps({"endpoint": "http://bound.test", "project_id": "existing"}))
    original = binding.read_text()
    scenario = HTTPScenario(unexpected)
    with pytest.raises(CliError) as error:
        scenario.execute(["init", "--name", "Accidental replacement"])
    assert error.value.exit_code == 2
    assert scenario.calls == [] and scenario.connections == []
    assert binding.read_text() == original


def test_json_parser_error_redacts_invalid_argument_value(monkeypatch, capsys):
    monkeypatch.setattr(cli, "execute", lambda _: pytest.fail("Invalid arguments must never execute a command"))
    assert cli.main(["--json", "serve", "--port", "secret-string"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "secret-string" not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("explicit_revision", [None, 3])
@pytest.mark.parametrize("dry_run", [False, True])
def test_node_edit_uses_revision_and_preview_never_applies(tmp_path, explicit_revision, dry_run):
    patch = {"instructions": "Use the held-out split."}
    if explicit_revision is not None:
        patch["expected_revision"] = explicit_revision

    def handler(call):
        if call["path"] == "/api/nodes/n1": return {"id": "n1", "project_id": "p1"}
        if call["path"] == "/api/projects/p1/graph": return {"revision": 9}
        if call["path"].endswith("/preview" if dry_run else "/commands"): return {"changed_nodes": ["n1"]}
        return unexpected(call)

    scenario = HTTPScenario(handler)
    args = ["--project", "p1", "node", "edit", "n1", "--file", object_file(tmp_path, "edit.json", patch), "--request-id", "one-edit"]
    if dry_run: args.append("--dry-run")
    assert scenario.execute(args) == 0
    post = scenario.calls[-1]
    assert post["body"] == {"request_id": "one-edit", "expected_revision": explicit_revision or 9,
                            "operation": "edit_node", "targets": ["n1"],
                            "params": {"instructions": "Use the held-out split."}, "run": False}
    assert len([call for call in scenario.calls if call["method"] == "POST"]) == 1
    assert not any(call["path"].endswith("/run") for call in scenario.calls)


def test_node_edit_rejects_cross_project_before_mutation(tmp_path):
    scenario = HTTPScenario(lambda call: {"id": "n1", "project_id": "other"})
    with pytest.raises(CliError) as error:
        scenario.execute(["--project", "p1", "node", "edit", "n1", "--file", object_file(tmp_path, "edit.json", {"title": "Changed"})])
    assert error.value.code == "CROSS_PROJECT" and error.value.exit_code == 2
    assert [call["method"] for call in scenario.calls] == ["GET"]


def test_node_add_generates_one_submission_id_and_reuses_explicit_id(tmp_path, monkeypatch):
    generated = []

    def uuid():
        generated.append("generated-id")
        return "generated-id"

    monkeypatch.setattr(cli.uuid, "uuid4", uuid)

    def handler(call):
        if call["method"] == "GET": return {"revision": 0, "branches": [{"id": "main", "is_main": True}]}
        return {"request_id": call["body"]["request_id"]}

    scenario = HTTPScenario(handler)
    node = object_file(tmp_path, "node.json", {"title": "Baseline", "type": "experiment"})
    assert scenario.execute(["--project", "p1", "node", "add", "--file", node]) == 0
    assert generated == ["generated-id"]
    assert scenario.calls[-1]["body"]["params"]["branch_id"] == "main"
    for _ in range(2):
        assert scenario.execute(["--project", "p1", "node", "add", "--file", node, "--request-id", "recovered-add"]) == 0
        assert scenario.calls[-1]["body"]["request_id"] == "recovered-add"
    assert generated == ["generated-id"]


def test_submission_is_accepted_without_wait_and_failed_ancestor_ends_multi_wait(capsys):
    def handler(call):
        if call["method"] == "POST": return {"runs": [{"id": "ancestor"}, {"id": "child"}]}
        if call["path"] == "/api/runs/ancestor": return {"id": "ancestor", "status": "failed", "error": "Baseline crashed"}
        if call["path"] == "/api/runs/child": return {"id": "child", "status": "queued"}
        return unexpected(call)

    scenario = HTTPScenario(handler)
    assert scenario.execute(["--json", "node", "run", "n1", "--scope", "descendants", "--request-id", "same-launch"]) == 0
    assert json.loads(capsys.readouterr().out)["submission"] == "accepted"
    assert len(scenario.calls) == 1
    assert scenario.execute(["--json", "node", "run", "n1", "--scope", "descendants", "--request-id", "same-launch", "--wait"]) == 5
    response = json.loads(capsys.readouterr().out)
    assert response["outcome"] == "needs_attention" and response["pending_run_ids"] == ["child"]
    assert len([call for call in scenario.calls if call["method"] == "GET"]) == 2
    assert all(call["body"]["request_id"] == "same-launch" for call in scenario.calls if call["method"] == "POST")


def test_resume_updates_total_agent_limits_but_retry_submits_new_run(tmp_path, capsys):
    limits = {"steps": 15, "cost": 2.5, "active_seconds": 120}
    scenario = HTTPScenario(lambda call: {"id": "new-run" if call["path"].endswith("retry") else "original", "status": "queued"})
    assert scenario.execute(["run", "resume", "original", "--budget-file", object_file(tmp_path, "agent.json", limits)]) == 0
    assert scenario.calls[-1]["path"] == "/api/runs/original/resume"
    assert scenario.calls[-1]["body"] == {"agent_budget": limits}
    assert scenario.execute(["--json", "run", "retry", "original", "--request-id", "retry-one"]) == 0
    assert scenario.calls[-1]["body"] == {"request_id": "retry-one"}
    assert scenario.calls[-1]["path"] == "/api/runs/original/retry"
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1])["result"]["id"] == "new-run"


@pytest.mark.parametrize("arguments, body", [
    (["research", "start"], {}),
    (["research", "start", "--no-autonomous"], {"autonomous": False}),
    (["research", "start", "--autonomous", "--branch", "branch-1", "--max-cycles", "3"],
     {"autonomous": True, "branch_id": "branch-1", "max_cycles": 3}),
])
def test_research_policy_is_explicit_and_partial_control_errors_are_not_success(arguments, body, capsys):
    scenario = HTTPScenario(lambda call: {"status": "running", "process_control_errors": [{"run_id": "r1", "error": "process control failed"}]})
    assert scenario.execute(["--project", "p1", "--json", *arguments]) == 5
    assert scenario.calls[0]["body"] == body
    assert json.loads(capsys.readouterr().out)["process_control_errors"][0]["run_id"] == "r1"


@pytest.mark.parametrize("value", [{}, {"run_ids": []}, {"run_ids": "r1"}, {"run_ids": [None]}])
def test_paper_generation_requires_explicit_evidence_before_post(tmp_path, value):
    scenario = HTTPScenario(unexpected)
    with pytest.raises(CliError) as error:
        scenario.execute(["--project", "p1", "paper", "generate", "--evidence", object_file(tmp_path, "evidence.json", value)])
    assert error.value.code == "EVIDENCE_REQUIRED" and error.value.exit_code == 2
    assert scenario.calls == []


def test_paper_generation_preserves_selected_evidence_and_compile_revision(tmp_path):
    evidence = {"run_ids": ["r-old", "r-confirm"], "figure_ids": ["f1"]}

    def handler(call):
        if call["method"] == "GET": return {"id": "paper", "revision": 12, "data": {}}
        return {"id": "paper-task", "status": "queued"}

    scenario = HTTPScenario(handler)
    assert scenario.execute(["--project", "p1", "paper", "generate", "--evidence", object_file(tmp_path, "evidence.json", evidence), "--request-id", "draft-once"]) == 0
    assert scenario.calls[-1]["body"] == {**evidence, "request_id": "draft-once"}
    assert scenario.execute(["--project", "p1", "paper", "compile", "--request-id", "compile-once"]) == 0
    assert scenario.calls[-1]["body"] == {"expected_revision": 12, "request_id": "compile-once"}


def test_stale_pdf_is_rejected_before_download(tmp_path):
    scenario = HTTPScenario(lambda call: {"revision": 8, "data": {"compiled_revision": 7}}
                            if call["method"] == "GET" else unexpected(call))
    output = tmp_path / "paper.pdf"
    with pytest.raises(CliError) as error:
        scenario.execute(["--project", "p1", "paper", "export", "--format", "pdf", "--output", str(output)])
    assert error.value.code == "STALE_PDF" and error.value.exit_code == 4
    assert not output.exists()
    assert len(scenario.calls) == 1


def test_status_keeps_execution_and_incomplete_delivery_separate(capsys):
    responses = {
        "/api/projects/p1": {"id": "p1", "name": "Study", "goal": "Resolve effect", "mode": "manual", "revision": 4},
        "/api/projects/p1/research": {"controller": {"status": "submission_incomplete"}, "counts": {"completed": 1, "failed": 0}, "active_runs": []},
        "/api/projects/p1/publication": {"ready": False, "gaps": [{"kind": "missing_independent_analysis"}]},
        "/api/projects/p1/graph": {"nodes": [{"id": "n1", "execution_status": "completed", "deliverable_status": "needs_update"}]},
        "/api/projects/p1/usage": {"budget": {"allow_paid": False}, "used_usd": 0},
    }
    scenario = HTTPScenario(lambda call: responses[call["path"]])
    assert scenario.execute(["--project", "p1", "--json", "status"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["research"]["counts"]["completed"] == 1
    assert value["delivery"]["ready"] is False
    assert value["stale_nodes"][0]["id"] == "n1"
    assert scenario.execute(["--project", "p1", "publication", "check"]) == 5


def test_wait_timeout_does_not_cancel_backend_work(monkeypatch, capsys):
    clock = iter([0.0, 0.1, 1.0])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cli.time, "sleep", lambda _: pytest.fail("Timeout must return without another sleep"))
    scenario = HTTPScenario(lambda call: {"id": "r1", "status": "running"}
                            if call["method"] == "GET" else unexpected(call))
    assert scenario.execute(["--json", "run", "wait", "r1", "--timeout", ".5", "--request-timeout", "2"]) == 6
    assert json.loads(capsys.readouterr().out)["outcome"] == "wait_timeout"
    assert scenario.connections[0]["timeout"] == 2
    assert [call["method"] for call in scenario.calls] == ["GET"]
    assert scenario.calls[0]["timeout"]["read"] == pytest.approx(.4)


def test_wait_checks_deadline_before_querying_next_run(monkeypatch):
    clock = iter([0.0, 0.1, 1.0])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    scenario = HTTPScenario(lambda call: {"id": call["path"].rsplit("/", 1)[-1], "status": "running"})
    with scenario.factory("http://localhost:8000") as client:
        result, exit_code = cli.wait_runs(client, ["r1", "r2"], timeout=.5)
    assert exit_code == 6 and result["outcome"] == "wait_timeout"
    assert len(scenario.calls) == 1
    assert result["pending_run_ids"] == ["r1", "r2"]


def test_wait_http_timeout_reports_observation_timeout(monkeypatch, capsys):
    clock = iter([0.0, 0.1, 0.5])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))

    def timed_out(call):
        assert call["method"] == "GET"
        raise httpx.ReadTimeout("Observation deadline reached")

    scenario = HTTPScenario(timed_out)
    assert scenario.execute(["--json", "run", "wait", "r1", "--timeout", ".5"]) == 6
    assert json.loads(capsys.readouterr().out)["outcome"] == "wait_timeout"
    assert len(scenario.calls) == 1


@pytest.mark.parametrize("status", ["paused", "waiting_input", "budget_exhausted", "interrupted", "failed", "cancelled", "lost"])
def test_wait_returns_needs_attention_without_controlling_stopped_run(status, capsys):
    scenario = HTTPScenario(lambda call: {"id": "r1", "status": status}
                            if call["method"] == "GET" else unexpected(call))
    assert scenario.execute(["--json", "run", "wait", "r1"]) == 5
    assert json.loads(capsys.readouterr().out)["outcome"] == "needs_attention"
    assert [call["method"] for call in scenario.calls] == ["GET"]


def test_toml_project_config_is_transmitted_as_object(tmp_path):
    configuration = tmp_path / "project.toml"
    configuration.write_text('[publication_profile]\nid = "operational"\n')
    scenario = HTTPScenario(lambda call: {"id": "created", **call["body"]})
    assert scenario.execute(["project", "create", "--name", "Operational", "--config-file", str(configuration)]) == 0
    assert scenario.calls[0]["body"]["config"] == {"publication_profile": {"id": "operational"}}


def test_interrupting_observation_returns_130_without_cancel(monkeypatch, capsys):
    scenario = HTTPScenario(lambda call: {"id": "r1", "status": "running"})
    execute = cli.execute
    monkeypatch.setattr(cli, "execute", lambda args: execute(args, client_factory=scenario.factory))

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", interrupt)
    assert cli.main(["--json", "run", "wait", "r1"]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "INTERRUPTED"
    assert [call["method"] for call in scenario.calls] == ["GET"]


def test_follow_logs_drains_terminal_output_and_emits_json_lines(monkeypatch, capsys):
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    pages = iter([
        {"offset": 4, "text": "测量", "status": "completed"},
        {"offset": 8, "text": " done", "status": "completed"},
        {"offset": 8, "text": "", "status": "completed"},
    ])
    scenario = HTTPScenario(lambda call: next(pages))
    assert scenario.execute(["--json", "run", "logs", "r1", "--follow"]) == 0
    values = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "".join(page["text"] for page in values) == "测量 done"
    assert [call["params"]["offset"] for call in scenario.calls] == ["0", "4", "8"]


@pytest.mark.parametrize("command", [["--help"], ["node", "edit", "--help"], ["--version"]])
def test_help_and_version_do_not_resolve_config_or_contact_server(tmp_path, command):
    root = Path(__file__).resolve().parents[1]
    program = """
import builtins, sys
original = builtins.__import__
def block(name, *args, **kwargs):
    if name == 'services' or name.startswith('services.') or name == 'research' or name.startswith('research.'):
        raise AssertionError('CLI help imported backend: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = block
from forest_cli.main import main
raise SystemExit(main(sys.argv[1:]))
"""
    import os
    env = {**os.environ, "PYTHONPATH": str(root), "FOREST_CLI_CONFIG": str(tmp_path / "invalid.json")}
    (tmp_path / "invalid.json").write_text("not JSON")
    result = subprocess.run([sys.executable, "-c", program, *command], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FOREST" in result.stdout or "usage: forest" in result.stdout
