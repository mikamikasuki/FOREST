"""FOREST's terminal client. All research mutations use the existing HTTP API."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
import tomllib
from urllib.parse import quote
import uuid
import webbrowser

from .client import CliError, ForestClient
from .config import resolve_context, save_connection, save_project_binding
from .output import emit, redact, report_error


VERSION = "0.1.0"
SUCCESS = {"completed"}
STOPPED = {"failed", "cancelled", "interrupted", "lost", "skipped", "paused",
           "budget_exhausted", "waiting_input", "needs_revision", "submission_incomplete"}


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Argparse normally echoes unknown argument values, which may contain a
        # mistakenly supplied credential. Keep errors actionable without them.
        if message.startswith("unrecognized arguments:"):
            message = "Unrecognized arguments. See this command's --help."
        elif ": invalid choice:" in message:
            message = message.split(": invalid choice:", 1)[0] + ": choose a value shown in --help."
        elif ": invalid " in message and " value:" in message:
            message = message.split(": invalid ", 1)[0] + ": invalid value; see --help."
        raise CliError(message, code="INVALID_ARGUMENTS", exit_code=2)


def positive(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a positive number of seconds.") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Use a positive finite number.")
    return number


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a positive integer.") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("Use a positive integer.")
    return number


def _globals(parser: argparse.ArgumentParser, *, root=False, wait=False):
    # Suppressed defaults let flags work before or after subcommands without
    # a child parser silently overwriting values read by its parent.
    defaults = {"server": None, "token_file": None, "project": None,
                "config": None, "json_output": False, "request_timeout": 30.0}
    default = lambda name: defaults[name] if root else argparse.SUPPRESS
    parser.add_argument("--server", default=default("server"), help="FOREST API URL")
    parser.add_argument("--token-file", default=default("token_file"), help="Owner token file (never stored inline)")
    parser.add_argument("--project", default=default("project"), help="Project ID; overrides the directory binding")
    parser.add_argument("--config", default=default("config"), help="CLI connection configuration JSON")
    parser.add_argument("--json", dest="json_output", action="store_true", default=default("json_output"), help="Machine-readable JSON")
    names = ["--request-timeout"] if wait else ["--request-timeout", "--timeout"]
    parser.add_argument(*names, dest="request_timeout", type=positive,
                        default=default("request_timeout"), help="HTTP timeout in seconds (default: 30)")


def _command(subparsers, name, help, *, wait=False):
    parser = subparsers.add_parser(name, help=help, description=help)
    _globals(parser, wait=wait)
    return parser


def _file(parser, name="--file", *, required=True, help="JSON or TOML object file"):
    parser.add_argument(name, required=required, help=help)


def _queued(parser):
    parser.add_argument("--request-id", help="Reuse this ID only when recovering the same submission")
    parser.add_argument("--wait", action="store_true", help="Wait for completion; Ctrl+C only exits the observer")
    parser.add_argument("--wait-timeout", type=positive, help="Maximum local wait in seconds")
    parser.add_argument("--interval", type=positive, default=1.0, help="Observation interval in seconds")


def _project_create(parser):
    parser.add_argument("--name", required=True)
    parser.add_argument("--goal-file", help="UTF-8 research goal file")
    parser.add_argument("--goal", default="", help="Short research goal; use --goal-file for longer text")
    parser.add_argument("--description", default="")
    parser.add_argument("--mode", choices=["manual", "assisted", "auto"], default="assisted")
    _file(parser, "--budget-file", required=False, help="Explicit project budget; paid calls stay disabled by default")
    _file(parser, "--config-file", required=False, help="Project configuration, including provider/profile references")


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(prog="forest", description="Persistent research control from your terminal.")
    _globals(parser, root=True)
    parser.add_argument("--version", action="version", version=f"FOREST {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    p = _command(commands, "connect", "Check and save a FOREST service connection")
    p.add_argument("url")
    p = _command(commands, "init", "Create a project and bind this directory")
    _project_create(p)
    _command(commands, "doctor", "Inspect API health and runtime capabilities")
    p = _command(commands, "status", "Show execution, research, delivery and evidence state")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=positive, default=2.0)
    p = _command(commands, "watch", "Observe project state; Ctrl+C does not cancel research")
    p.add_argument("--interval", type=positive, default=2.0)
    p = _command(commands, "serve", "Start the local API and worker in the foreground")
    p.add_argument("--headless", action="store_true", help="Start without Node.js or a Web build")
    p.add_argument("--dev", action="store_true")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--data-dir")
    p.add_argument("--database-url")
    p = _command(commands, "open", "Open the current project in the Web workspace")
    p.add_argument("--page", choices=["workspace", "paper", "figures", "library"], default="workspace")
    p.add_argument("--print-url", action="store_true", help="Print the URL without opening a browser")

    p = _command(commands, "repo", "Validate and prepare reproducible repository inputs")
    group = p.add_subparsers(dest="action", required=True)
    p = _command(group, "validate", "Validate a repository specification locally without network access")
    _file(p)
    p = _command(group, "clone", "Queue a repository checkout in the selected project's run workspace")
    p.add_argument("url")
    p.add_argument("--ref", default="HEAD", help="Branch, tag or commit; the resolved commit is recorded")
    p.add_argument("--directory", default="source", help="Relative directory inside the run workspace")
    p.add_argument("--transport", choices=["auto", "https", "ssh"], default="auto")
    p.add_argument("--credential", help="Operator-defined authentication profile name; never a token or host path")
    _queued(p)
    p = _command(group, "inspect", "Read the recorded source commit for a persisted run")
    p.add_argument("id")

    p = _command(commands, "project", "Manage projects and directory bindings")
    group = p.add_subparsers(dest="action", required=True)
    p = _command(group, "list", "List projects")
    p.add_argument("--archived", action="store_true")
    p.add_argument("--limit", type=positive_int, default=100)
    p = _command(group, "create", "Create a project without changing the directory binding")
    _project_create(p)
    p = _command(group, "use", "Check a project and bind this directory")
    p.add_argument("id")
    p = _command(group, "show", "Show the selected project")
    p.add_argument("id", nargs="?")
    p = _command(group, "edit", "Apply a revision-checked project patch")
    p.add_argument("id", nargs="?")
    _file(p)
    p = _command(group, "export", "Export the project bundle without overwriting existing files")
    p.add_argument("--output", required=True)

    p = _command(commands, "node", "Inspect, edit and execute research nodes")
    group = p.add_subparsers(dest="action", required=True)
    _command(group, "list", "List nodes in the selected project")
    for action in ["show", "context"]:
        p = _command(group, action, f"Read a node's {action}")
        p.add_argument("id")
    for action in ["add", "edit"]:
        p = _command(group, action, "Preview or apply a revision-checked graph edit")
        if action == "edit": p.add_argument("id")
        _file(p)
        p.add_argument("--dry-run", action="store_true", help="Only preview the impact; do not modify or run anything")
        p.add_argument("--request-id")
    p = _command(group, "run", "Submit node work; success means the submission was accepted")
    p.add_argument("id")
    p.add_argument("--scope", choices=["single", "ancestors", "descendants", "affected"], default="single")
    _file(p, "--config-file", required=False)
    _queued(p)

    p = _command(commands, "run", "Observe and control persisted runs")
    group = p.add_subparsers(dest="action", required=True)
    p = _command(group, "list", "List project runs")
    p.add_argument("--limit", type=positive_int, default=100)
    for action in ["show", "evidence", "diagnostics", "pause", "resume", "cancel", "retry"]:
        help_text = "Inspect saved execution diagnostics" if action == "diagnostics" else f"{action.capitalize()} a persisted run"
        p = _command(group, action, help_text)
        p.add_argument("id")
        if action == "resume": _file(p, "--budget-file", required=False, help="Absolute agent budget limits; does not raise outer limits")
        if action == "retry": _queued(p)
    p = _command(group, "logs", "Read run output; following only observes")
    p.add_argument("id")
    p.add_argument("--follow", action="store_true")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--interval", type=positive, default=1.0)
    p = _command(group, "wait", "Wait for a run; return 5 when work needs attention", wait=True)
    p.add_argument("id")
    p.add_argument("--timeout", dest="wait_timeout", type=positive, help="Maximum local wait in seconds")
    p.add_argument("--interval", type=positive, default=1.0)

    p = _command(commands, "research", "Control the project's research controller")
    group = p.add_subparsers(dest="action", required=True)
    p = _command(group, "start", "Start/continue research; explicit planning policy is recommended")
    autonomous = p.add_mutually_exclusive_group()
    autonomous.add_argument("--autonomous", action="store_true", dest="autonomous")
    autonomous.add_argument("--no-autonomous", action="store_false", dest="autonomous")
    p.set_defaults(autonomous=None)
    p.add_argument("--branch")
    p.add_argument("--max-cycles", type=positive_int)
    for action in ["pause", "stop"]: _command(group, action, f"{action.capitalize()} the controller and report process-control failures")

    for name in ["budget", "publication"]:
        p = _command(commands, name, f"Inspect project {name}")
        group = p.add_subparsers(dest="action", required=True)
        _command(group, "show" if name == "budget" else "check", f"Read the authoritative {name} state")

    p = _command(commands, "paper", "Generate, compile and export evidence-bound manuscripts")
    group = p.add_subparsers(dest="action", required=True)
    for action in ["show", "check"]: _command(group, action, f"{action.capitalize()} the current manuscript")
    p = _command(group, "compile", "Compile the current revision")
    _queued(p)
    p = _command(group, "generate", "Draft from explicitly selected completed runs and figures")
    _file(p, "--evidence", help="JSON/TOML object containing run_ids and optional figure_ids")
    _queued(p)
    p = _command(group, "export", "Export the current PDF or editable source bundle")
    p.add_argument("--format", choices=["pdf", "source"], default="source")
    p.add_argument("--output", required=True)
    return parser


def read_text(path) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CliError(f"Cannot read UTF-8 input file: {path}", code="INVALID_INPUT", exit_code=2) from exc


def read_object(path) -> dict:
    try:
        text = read_text(path)
        result = tomllib.loads(text) if Path(path).suffix.lower() == ".toml" else json.loads(text)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise CliError(f"Invalid JSON/TOML object file: {path}", code="INVALID_INPUT", exit_code=2) from exc
    if not isinstance(result, dict):
        raise CliError(f"Expected an object in {path}", code="INVALID_INPUT", exit_code=2)
    return result


def repository_spec(value) -> dict:
    # The shared validator uses the standard library only. Client-only installs
    # never load the API, database or worker to check repository input.
    from research.execution.repository import normalize_repository, RepositoryError
    try:
        return normalize_repository(value)
    except RepositoryError as exc:
        raise CliError(str(exc), code=exc.code, exit_code=2) from exc


def run_configuration(path) -> dict:
    config = read_object(path) if path else {}
    if '_repository_source' in config:
        raise CliError('Source provenance is managed by the runner.', code='INVALID_CONFIGURATION', exit_code=2)
    if 'repository' in config:
        config['repository'] = repository_spec(config['repository'])
    return config


def identifier(value) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise CliError("A nonempty object ID is required.", code="INVALID_INPUT", exit_code=2)
    return quote(value, safe="")


def require_project(context, explicit=None) -> str:
    project = explicit or context.project_id
    if not project:
        raise CliError("Choose a project with --project ID or forest project use ID.", code="PROJECT_REQUIRED", exit_code=2)
    return project


def request_id(args) -> str:
    return getattr(args, "request_id", None) or str(uuid.uuid4())


def _project_body(args):
    if args.goal_file and args.goal:
        raise CliError("Choose --goal or --goal-file, not both.", code="INVALID_INPUT", exit_code=2)
    body = {"name": args.name, "goal": read_text(args.goal_file) if args.goal_file else args.goal,
            "description": args.description, "mode": args.mode}
    if args.budget_file: body["budget"] = read_object(args.budget_file)
    if args.config_file: body["config"] = read_object(args.config_file)
    return body


def _run_ids(value):
    if isinstance(value, dict) and value.get("id"): return [value["id"]]
    if isinstance(value, dict) and value.get("run_ids"): return value["run_ids"]
    if isinstance(value, dict) and isinstance(value.get("runs"), list): return [r["id"] for r in value["runs"]]
    raise CliError("The service did not return a run ID.", code="INVALID_RESPONSE")


def wait_runs(client, ids, *, interval=1.0, timeout=None):
    started = time.monotonic()
    states = {}
    pending = set(ids)
    while pending:
        for rid in list(pending):
            remaining = timeout - (time.monotonic() - started) if timeout is not None else None
            if remaining is not None and remaining <= 0:
                return {"runs": list(states.values()), "pending_run_ids": sorted(pending), "outcome": "wait_timeout"}, 6
            try:
                options = {"timeout": remaining} if remaining is not None else {}
                run = client.request("GET", f"/api/runs/{identifier(rid)}", **options)
            except CliError as exc:
                if timeout is not None and exc.code == "CONNECTION_ERROR" and time.monotonic() - started >= timeout:
                    return {"runs": list(states.values()), "pending_run_ids": sorted(pending), "outcome": "wait_timeout"}, 6
                raise
            states[rid] = run
            status = run.get("status")
            if status in SUCCESS or status in STOPPED: pending.remove(rid)
        # Avoid waiting forever for queued descendants of a failed ancestor.
        if any(r.get("status") in STOPPED for r in states.values()):
            return {"runs": list(states.values()), "pending_run_ids": sorted(pending), "outcome": "needs_attention"}, 5
        if not pending: return {"runs": list(states.values()), "outcome": "completed"}, 0
        if timeout is not None and time.monotonic() - started >= timeout:
            return {"runs": list(states.values()), "pending_run_ids": sorted(pending), "outcome": "wait_timeout"}, 6
        remaining = timeout - (time.monotonic() - started) if timeout is not None else interval
        time.sleep(max(0, min(interval, remaining)))
    return {"runs": [], "outcome": "completed"}, 0


def _submitted(client, value, args):
    if args.wait:
        return wait_runs(client, _run_ids(value), interval=args.interval, timeout=args.wait_timeout)
    return {"submission": "accepted", "result": value}, 0


def project_status(client, pid):
    path = f"/api/projects/{identifier(pid)}"
    project = client.request("GET", path)
    research = client.request("GET", path + "/research")
    delivery = client.request("GET", path + "/publication")
    graph = client.request("GET", path + "/graph")
    stale = [{"id": n["id"], "title": n.get("title"), "deliverable_status": n.get("deliverable_status")}
             for n in graph.get("nodes", []) if n.get("deliverable_status") == "needs_update" or n.get("results_current") is False]
    return {"project": {k: project.get(k) for k in ["id", "name", "goal", "mode", "revision"]},
            "research": research, "delivery": delivery, "stale_nodes": stale,
            "budget": client.request("GET", path + "/usage")}


def emit_status(value, *, json_output=False):
    if json_output:
        emit(value, json_output=True)
        return
    project = value["project"]
    research = value["research"]
    control = research.get("controller", {})
    delivery = value["delivery"]
    budget = value["budget"]
    emit({"Project": f"{project.get('name')} ({project.get('id')})",
          "Goal": project.get("goal"), "Mode": project.get("mode"),
          "Graph revision": project.get("revision"),
          "Controller": control.get("status", "idle"), "Phase": control.get("phase", "—"),
          "Reason": control.get("reason") or control.get("last_rationale") or "—",
          "Execution counts": research.get("counts", {}),
          "Delivery": delivery.get("status", "unknown"),
          "Delivery ready": delivery.get("ready", False),
          "Estimated USD": budget.get("estimated_cost_usd"),
          "Reserved USD": budget.get("reserved_usd"), "Remaining USD": budget.get("remaining_usd"),
          "Uncertain requests": budget.get("uncertain_requests", 0),
          "Stale nodes": len(value["stale_nodes"])})
    if research.get("active_runs"):
        emit(research["active_runs"])
    gaps = delivery.get("gaps", [])
    if gaps:
        print(f"Delivery gaps ({len(gaps)}):")
        for gap in gaps[:8]:
            print("  " + str(gap.get("finding", gap) if isinstance(gap, dict) else gap))
        if len(gaps) > 8: print("  Use forest publication check for the full audit.")
    print("Inspect: forest run list | forest publication check | forest open --page workspace", flush=True)


def emit_result(value, args):
    if args.json_output:
        emit(value, json_output=True)
        return
    if isinstance(value, dict) and value.get("submission") == "accepted":
        emit({"Submission": "accepted (execution is in the background)",
              "runs": [{"id": run.get("id"), "status": run.get("status"), "kind": run.get("kind"),
                        "request_id": run.get("request_id")}
                       for run in (value["result"].get("runs") or [value["result"]])]})
    elif isinstance(value, dict) and "outcome" in value and "runs" in value:
        emit({"outcome": value["outcome"],
              "runs": [{k: run.get(k) for k in ("id", "status", "error")} for run in value["runs"]],
              "pending_run_ids": value.get("pending_run_ids", [])})
    elif args.command == "node" and args.action in {"add", "edit"}:
        emit({"operation": "preview" if args.dry_run else "applied", "revision": value.get("revision"),
              "impact": value.get("impact", value if args.dry_run else {}), "run_ids": value.get("run_ids", [])})
    elif args.command == "run" and args.action == "evidence":
        lineage = value.get("lineage", {})
        emit({"run_id": lineage.get("run_id"), "project_id": lineage.get("project_id"),
              "lineage": [{k: run.get(k) for k in ("id", "node_title", "status", "node_revision", "current_node_revision", "current")}
                          for run in lineage.get("runs", [])], "session": value.get("session")})
        for run in lineage.get("runs", []):
            if run.get("source_freshness"):
                print("Source freshness for " + str(run.get("id")) + ":")
                emit(run["source_freshness"])
        print("Use --json for full artifact paths and dependency records.")
    else:
        emit(value)


def _graph_change(client, context, args):
    params = read_object(args.file)
    if args.action == "edit":
        node = client.request("GET", f"/api/nodes/{identifier(args.id)}")
        pid = node["project_id"]
        if context.project_id and pid != context.project_id:
            raise CliError("The node belongs to a different project.", code="CROSS_PROJECT", exit_code=2)
    else:
        pid = require_project(context)
    path = f"/api/projects/{identifier(pid)}/graph"
    graph = client.request("GET", path)
    expected = params.pop("expected_revision", graph["revision"])
    fields = params.get("node", params) if args.action == "add" else params
    if not isinstance(fields, dict):
        raise CliError("The node field must be an object.", code="INVALID_INPUT", exit_code=2)
    if args.action == "add" and "branch_id" not in fields:
        branch = next((b for b in graph.get("branches", []) if b.get("is_main")), None)
        if not branch: raise CliError("Select branch_id in the node file.", code="INVALID_INPUT", exit_code=2)
        fields["branch_id"] = branch["id"]
    body = {"request_id": request_id(args), "expected_revision": expected,
            "operation": "add_node" if args.action == "add" else "edit_node",
            "targets": [] if args.action == "add" else [args.id], "params": params, "run": False}
    return client.request("POST", path + ("/preview" if args.dry_run else "/commands"), json=body)


def _logs(client, args):
    if args.offset < 0: raise CliError("Log offset must be nonnegative.", code="INVALID_INPUT", exit_code=2)
    offset = args.offset
    while True:
        value = client.request("GET", f"/api/runs/{identifier(args.id)}/output", params={"offset": offset})
        next_offset = value.get("offset", offset)
        if args.json_output:
            # Following emits one JSON object per line, rather than one invalid
            # document containing repeated pretty-printed JSON objects.
            print(json.dumps(redact(value), ensure_ascii=False, separators=(",", ":")), flush=True)
        else:
            print(value.get("text", ""), end="", flush=True)
        if not args.follow: return 0
        # Drain output before stopping: a completed run may have >100 KB unread.
        if next_offset == offset and value.get("status") in SUCCESS | STOPPED: return 0
        offset = next_offset
        time.sleep(args.interval)


def execute(args, *, client_factory=ForestClient):
    if args.command == "repo" and args.action == "validate":
        emit_result({"valid": True, "repository": repository_spec(read_object(args.file))}, args)
        return 0
    repository = None
    if args.command == "repo" and args.action == "clone":
        spec = {"url": args.url, "ref": args.ref, "directory": args.directory,
                "transport": args.transport}
        if args.credential:
            spec["credential"] = args.credential
        repository = repository_spec(spec)
    if args.command == "serve":
        if args.json_output:
            raise CliError("serve streams service logs; --json is available on client commands.", code="INVALID_INPUT", exit_code=2)
        from .server import serve
        return serve(host=args.host, port=args.port, data_dir=args.data_dir,
                     database_url=args.database_url, headless=args.headless, dev=args.dev)
    if args.command == "init" and (Path.cwd() / ".forest" / "project.json").exists():
        raise CliError("This directory already has a project binding. Use project create, then project use ID to change it.",
                       code="ALREADY_INITIALIZED", exit_code=2)
    # Select the new server before resolving credentials so a saved token for
    # the old server can never leak through `connect OTHER_URL`.
    context_args = argparse.Namespace(**vars(args))
    if args.command == "connect": context_args.server = args.url
    context = resolve_context(context_args)
    endpoint = context.endpoint
    with client_factory(endpoint, token=context.token, timeout=args.request_timeout) as client:
        value, code = None, 0
        if args.command == "connect":
            health = client.request("GET", "/api/health")
            client.request("GET", "/api/projects", params={"limit": 1})
            token_file = args.token_file or (os.environ.get("FOREST_TOKEN_FILE") if not os.environ.get("FOREST_OWNER_TOKEN") else None)
            config = save_connection(endpoint, token_file, path=args.config)
            value = {"connected": True, "endpoint": endpoint, "health": health, "config_path": str(config)}
        elif args.command == "doctor":
            value = {"health": client.request("GET", "/api/health"), "system": client.request("GET", "/api/system")}
        elif args.command == "repo":
            if args.action == "clone":
                path = f"/api/projects/{identifier(require_project(context))}/repositories/clone"
                value = client.request("POST", path, json={"repository": repository, "request_id": request_id(args)})
                value, code = _submitted(client, value, args)
            elif args.action == "inspect":
                value = client.request("GET", f"/api/runs/{identifier(args.id)}/repository")
        elif args.command == "init" or (args.command == "project" and args.action == "create"):
            value = client.request("POST", "/api/projects", json=_project_body(args))
            if args.command == "init":
                binding = save_project_binding(value["id"], endpoint)
                value = {"project": value, "binding_path": str(binding)}
        elif args.command == "project":
            if args.action == "list":
                value = client.request("GET", "/api/projects", params={"limit": args.limit, "archived": args.archived})
            elif args.action == "use":
                value = client.request("GET", f"/api/projects/{identifier(args.id)}")
                binding = save_project_binding(value["id"], endpoint)
                value = {"project": value, "binding_path": str(binding)}
            else:
                pid = require_project(context, getattr(args, "id", None))
                path = f"/api/projects/{identifier(pid)}"
                if args.action == "show": value = client.request("GET", path)
                elif args.action == "edit":
                    patch = read_object(args.file)
                    current = client.request("GET", path)
                    patch.setdefault("expected_revision", current["revision"])
                    value = client.request("PATCH", path, json=patch)
                elif args.action == "export": value = client.download("POST", path + "/export", args.output, json={})
        elif args.command in {"status", "watch"}:
            pid = require_project(context)
            while True:
                value = project_status(client, pid)
                if args.json_output and (args.command == "watch" or args.watch):
                    print(json.dumps(redact(value), ensure_ascii=False, separators=(",", ":")), flush=True)
                else: emit_status(value, json_output=args.json_output)
                if args.command == "status" and not args.watch: return 0
                time.sleep(args.interval)
        elif args.command == "node":
            if args.action in {"add", "edit"}: value = _graph_change(client, context, args)
            elif args.action == "list":
                graph = client.request("GET", f"/api/projects/{identifier(require_project(context))}/graph")
                value = {"revision": graph["revision"], "nodes": graph.get("nodes", [])}
            else:
                path = f"/api/nodes/{identifier(args.id)}"
                if args.action == "show": value = client.request("GET", path)
                elif args.action == "context": value = client.request("GET", path + "/context")
                elif args.action == "run":
                    config = run_configuration(args.config_file)
                    value = client.request("POST", path + "/run", json={"request_id": request_id(args), "scope": args.scope, "config": config})
                    value, code = _submitted(client, value, args)
        elif args.command == "run":
            if args.action == "list":
                value = client.request("GET", f"/api/projects/{identifier(require_project(context))}/runs", params={"limit": args.limit})
            else:
                path = f"/api/runs/{identifier(args.id)}"
                if args.action == "show": value = client.request("GET", path)
                elif args.action == "diagnostics": value = client.request("GET", path + "/diagnostics")
                elif args.action == "logs": return _logs(client, args)
                elif args.action == "wait": value, code = wait_runs(client, [args.id], interval=args.interval, timeout=args.wait_timeout)
                elif args.action == "evidence":
                    value = {"lineage": client.request("GET", path + "/lineage"),
                             "session": client.request("GET", path + "/session", params={"summary": True})}
                else:
                    body = {}
                    if args.action == "resume" and args.budget_file: body["agent_budget"] = read_object(args.budget_file)
                    if args.action == "retry": body["request_id"] = request_id(args)
                    value = client.request("POST", path + "/" + args.action, json=body)
                    if args.action == "retry": value, code = _submitted(client, value, args)
        elif args.command == "research":
            body = {}
            if args.action == "start":
                if args.autonomous is not None: body["autonomous"] = args.autonomous
                if args.branch: body["branch_id"] = args.branch
                if args.max_cycles is not None: body["max_cycles"] = args.max_cycles
            value = client.request("POST", f"/api/projects/{identifier(require_project(context))}/research/{args.action}", json=body)
            if value.get("process_control_errors"): code = 5
        elif args.command == "budget":
            value = client.request("GET", f"/api/projects/{identifier(require_project(context))}/usage")
        elif args.command == "publication":
            value = client.request("GET", f"/api/projects/{identifier(require_project(context))}/publication")
            if not value.get("ready"): code = 5
        elif args.command == "paper":
            path = f"/api/papers/{identifier(require_project(context))}"
            if args.action == "show": value = client.request("GET", path)
            elif args.action == "check":
                value = client.request("POST", path + "/check")
                if any(issue.get("severity") == "error" for issue in value.get("issues", [])): code = 5
            elif args.action == "compile":
                paper = client.request("GET", path)
                value = client.request("POST", path + "/compile", json={"expected_revision": paper["revision"], "request_id": request_id(args)})
                value, code = _submitted(client, value, args)
            elif args.action == "generate":
                body = read_object(args.evidence)
                runs = body.get("run_ids")
                if not isinstance(runs, list) or not runs or any(not isinstance(r, str) or not r for r in runs):
                    raise CliError("Evidence must contain a nonempty run_ids array.", code="EVIDENCE_REQUIRED", exit_code=2)
                body["request_id"] = request_id(args)
                value = client.request("POST", path + "/generate", json=body)
                value, code = _submitted(client, value, args)
            elif args.action == "export":
                export_body = {"format": args.format}
                if args.format == "pdf":
                    paper = client.request("GET", path)
                    if paper.get("data", {}).get("compiled_revision") != paper.get("revision"):
                        raise CliError("Compile the current manuscript revision before exporting its PDF.", code="STALE_PDF", exit_code=4)
                    export_body["expected_revision"] = paper["revision"]
                value = client.download("POST", path + "/export", args.output, json=export_body)
        elif args.command == "open":
            pid = require_project(context)
            client.request("GET", f"/api/projects/{identifier(pid)}")
            url = endpoint.rstrip("/") + f"/projects/{identifier(pid)}/{args.page}"
            if not args.print_url and not args.json_output:
                if not webbrowser.open(url): raise CliError(f"Cannot open a browser. Open {url} manually.", code="BROWSER_UNAVAILABLE")
            value = {"url": url}
        if value is not None: emit_result(value, args)
        return code


def main(argv=None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in arguments
    try:
        args = parser.parse_args(arguments)
        json_output = args.json_output
        return execute(args)
    except CliError as exc:
        report_error(exc, json_output=json_output)
        return exc.exit_code
    except KeyboardInterrupt:
        report_error(CliError("Observation stopped; submitted background work was not cancelled.", code="INTERRUPTED", exit_code=130), json_output=json_output)
        return 130
    except BrokenPipeError:
        # A consumer such as `head` closing stdout should not print a traceback.
        return 0
    except OSError as exc:
        report_error(CliError(str(exc), code="LOCAL_IO_ERROR"), json_output=json_output)
        return 1
    except (KeyError, TypeError, ValueError) as exc:
        report_error(CliError("The service returned data that does not match the CLI contract. Check that client and server are from the same release.",
                              code="INVALID_RESPONSE"), json_output=json_output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
