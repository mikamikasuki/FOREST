"""Integration checks for graph revisions and effects across write entry points."""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import psutil
import pytest

from tests.test_worker import Harness, wait_until


ROOT = Path(__file__).resolve().parents[1]


def run_child(harness, code, *args):
    result = subprocess.run(
        [sys.executable, "-c", code, *map(str, args)],
        cwd=ROOT,
        env=harness.env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def make_agent_node(harness):
    project = harness.request("POST", "/api/projects", json={
        "name": "Stale graph write " + str(uuid.uuid4())[:8],
        "goal": "Preserve the owner's latest edit",
    })
    graph = harness.request("GET", f"/api/projects/{project['id']}/graph")
    node_id = str(uuid.uuid4())
    created = harness.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
        "request_id": str(uuid.uuid4()),
        "expected_revision": graph["revision"],
        "operation": "add_node",
        "targets": [],
        "params": {
            "id": node_id,
            "branch_id": graph["branches"][0]["id"],
            "type": "experiment",
            "title": "Agent mutation target",
            "instructions": "Initial instructions",
            "config": {"kind": "agent", "role": "Researcher"},
        },
    })
    node = next(node for node in created["graph"]["nodes"] if node["id"] == node_id)
    return project, node, created["revision"]


def test_agent_graph_command_rejects_old_revision_instead_of_overwriting_owner_edit(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.start_api()
        project, node, old_revision = make_agent_node(harness)
        run = harness.launch(node)
        harness.request("PATCH", f"/api/nodes/{node['id']}", json={
            "expected_revision": old_revision,
            "instructions": "OWNER_NEW_INSTRUCTIONS",
        })

        code = """
import json, sys, uuid
from research.agents.runtime import ToolRuntime
run_id, workspace, revision, node_id = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
tool = ToolRuntime(run_id, workspace, allowed=['graph_command'])
args = {'expected_revision': revision, 'operation': 'edit_node', 'targets': [node_id],
        'params': {'patch': {'instructions': 'AGENT_STALE_INSTRUCTIONS'}}}
first = tool.execute('graph_command', args, action_id=str(uuid.uuid4()))
second = tool.execute('graph_command', {key: value for key, value in args.items() if key != 'expected_revision'},
                      action_id=str(uuid.uuid4()))
print(json.dumps({'first': first, 'second': second}))
"""
        observed = run_child(harness, code, run["id"], harness.output(run) / "workspace", old_revision, node["id"])
        current = harness.request("GET", f"/api/nodes/{node['id']}")
        graph = harness.request("GET", f"/api/projects/{project['id']}/graph")
        assert observed["first"]["exit_code"] == 1
        assert observed["second"]["exit_code"] == 1
        assert current["instructions"] == "OWNER_NEW_INSTRUCTIONS"
        assert graph["revision"] > old_revision
    finally:
        harness.cleanup()


def _seed_paper(harness, project_id, node_id):
    code = """
import json, sys
from services.api.db import Session, PaperDocument
with Session.begin() as session:
    paper = PaperDocument(project_id=sys.argv[1], title='Mutation invalidation control',
                          data={'dependency_bindings': [{'source_kind': 'node', 'source_id': sys.argv[2],
                                                         'target_path': '/sections/results/paragraphs/1'}]},
                          status='ready')
    session.add(paper)
    session.flush()
    print(json.dumps({'id': paper.id}))
"""
    return run_child(harness, code, project_id, node_id)["id"]


def _apply_entrypoint(harness, entrypoint, project, node, revision, run):
    params = {"patch": {"instructions": f"Edited through {entrypoint}"}, "stop_current_run": True}
    command = {
        "operation": "edit_node",
        "targets": [node["id"]],
        "params": params,
    }
    if entrypoint == "single":
        return harness.request("POST", f"/api/projects/{project['id']}/graph/commands", json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": revision,
            **command,
        })
    if entrypoint == "batch":
        return harness.request("POST", f"/api/projects/{project['id']}/graph/batch", json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": revision,
            "commands": [command],
        })
    if entrypoint == "proposal":
        proposal = harness.request("POST", "/api/ideas", json={
            "project_id": project["id"],
            "title": "Stop the active run before applying this edit",
            "data": {"commands": [command]},
        })
        return harness.request("POST", f"/api/research/proposals/{proposal['id']}/apply", json={
            "expected_revision": revision,
            "indices": [0],
        })
    if entrypoint == "agent":
        code = """
import json, sys, time
from research.agents.runtime import ToolRuntime
from services.api.db import Session, TaskRun
run_id, workspace, revision, node_id = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
tool = ToolRuntime(run_id, workspace, allowed=['graph_command'])
args = {'expected_revision': revision, 'operation': 'edit_node', 'targets': [node_id],
        'params': {'patch': {'instructions': 'Edited through agent'}, 'stop_current_run': True}}
result = tool.execute('graph_command', args, action_id='graph-effect-test-' + node_id)
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    with Session() as session:
        status = session.get(TaskRun, run_id).status
    if status == 'cancelled':
        break
    time.sleep(.05)
print(json.dumps(result))
        """
        return run_child(harness, code, run["id"], harness.output(run) / "workspace", revision, node["id"])
    if entrypoint == "planning":
        code = """
import json, sys, uuid
from services.api.db import Session, Project
from research.planning.loop import apply_plan
project_id, revision, node_id = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with Session.begin() as session:
    project = session.get(Project, project_id)
    project.config = {**project.config, 'controller': {'status': 'running'}}
plan = {'action': 'continue', 'rationale': 'Apply the selected graph edit to continue the route',
        'commands': [{'operation': 'edit_node', 'targets': [node_id],
                      'params': {'patch': {'instructions': 'Edited through planning'}, 'stop_current_run': True}}]}
print(json.dumps(apply_plan(project_id, str(uuid.uuid4()), plan, revision)))
"""
        return run_child(harness, code, project["id"], revision, node["id"])
    raise AssertionError(f"Unknown entry point: {entrypoint}")


@pytest.mark.parametrize("entrypoint", ["single", "batch", "proposal", "agent", "planning"])
def test_every_graph_mutation_entrypoint_cancels_and_invalidates(tmp_path, entrypoint):
    harness = Harness(tmp_path)
    try:
        harness.start_api()
        project, node = harness.project_node(seconds=60, timeout=90)
        paper_id = _seed_paper(harness, project["id"], node["id"])
        harness.start_worker()
        run = harness.launch(node)
        active = harness.running(run)
        graph = harness.request("GET", f"/api/projects/{project['id']}/graph")

        result = _apply_entrypoint(harness, entrypoint, project, node, graph["revision"], run)
        if entrypoint == "agent":
            assert result["exit_code"] == 0
        if entrypoint == "planning":
            assert result["applied_commands"] == 1

        cancelled = harness.terminal(run)
        assert cancelled["status"] == "cancelled"
        paper = harness.request("GET", f"/api/papers/{paper_id}")
        assert paper["status"] == "needs_update"
        assert paper["data"]["stale_dependencies"][0]["target_path"] == "/sections/results/paragraphs/1"
        edited = harness.request("GET", f"/api/nodes/{node['id']}")
        assert edited["context_stale"] is True

        def process_stopped():
            try:
                process = psutil.Process(active["pid"])
                if abs(process.create_time() - active["process_created"]) >= 0.2:
                    return True
                return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return True

        wait_until(process_stopped, timeout=10)
    finally:
        harness.cleanup()
