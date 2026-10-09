from research.kernel.artifacts import resolve_branch_references
from services.worker.execute import materialize_branch_references
from services.api import verification
from services.interventions import acceptance
from types import SimpleNamespace


def test_materialize_branch_references_into_isolated_run_workspace(tmp_path):
    project = tmp_path / "project"
    source = project / "branches" / "main" / "data" / "train.csv"
    source.parent.mkdir(parents=True)
    source.write_text("source data")
    workspace = tmp_path / "run" / "workspace"
    workspace.mkdir(parents=True)

    materialize_branch_references(
        project,
        workspace,
        [
            {
                "source_path": "branches/main/data/train.csv",
                "destination": "data/train.csv",
            }
        ],
    )

    materialized = workspace / "data" / "train.csv"
    assert materialized.read_text() == "source data"
    materialized.write_text("run-local changes")
    assert source.read_text() == "source data"


def test_resolve_branch_references_and_allow_local_override(tmp_path):
    source = tmp_path / "data" / "train.csv"
    source.parent.mkdir()
    source.write_text("source data")
    graph = {
        "project_id": "project",
        "branches": [
            {"id": "main", "workspace": "."},
            {
                "id": "fork",
                "workspace": "branches/fork",
                "input_mapping": [
                    {
                        "path": "data/train.csv",
                        "branch_id": "main",
                        "destination": "data/train.csv",
                    }
                ],
            },
        ],
    }
    (tmp_path / "branches" / "fork").mkdir(parents=True)

    bindings, missing = resolve_branch_references(tmp_path, graph, "fork")

    assert bindings == [
        {"source_path": "data/train.csv", "destination": "data/train.csv", "source": "data"}
    ]
    assert missing == []

    local = tmp_path / "branches" / "fork" / "data" / "train.csv"
    local.parent.mkdir(parents=True)
    local.write_text("branch-local override")
    assert resolve_branch_references(tmp_path, graph, "fork") == ([], [])


def test_materialize_duplicate_destinations_uses_nearest_binding(tmp_path):
    project = tmp_path / "project"
    old = project / "branches" / "main" / "data" / "train.csv"
    new = project / "branches" / "parent" / "data" / "train.csv"
    old.parent.mkdir(parents=True)
    new.parent.mkdir(parents=True)
    old.write_text("original")
    new.write_text("parent override")
    workspace = tmp_path / "run" / "workspace"
    workspace.mkdir(parents=True)

    materialize_branch_references(project, workspace, [
        {"source_path": "branches/main/data/train.csv", "destination": "data/train.csv"},
        {"source_path": "branches/parent/data/train.csv", "destination": "data/train.csv"},
    ])

    assert (workspace / "data/train.csv").read_text() == "parent override"


def test_nested_fork_copy_policy_respects_inherited_data_strategy(tmp_path):
    from research.kernel.artifacts import BranchWorkspace

    source = tmp_path / "data" / "train.csv"
    source.parent.mkdir()
    source.write_text("inherited data")
    graph = {
        "nodes": [{"id": "fork-node", "branch_id": "fork"}],
        "branches": [
            {"id": "main", "workspace": ".", "is_main": True},
            {
                "id": "fork",
                "workspace": "branches/fork",
                "input_mapping": [{
                    "path": "data/train.csv",
                    "branch_id": "main",
                    "destination": "data/train.csv",
                    "source": "data",
                }],
            },
        ],
    }
    (tmp_path / "branches/fork").mkdir(parents=True)

    excluded = BranchWorkspace(tmp_path, graph).fork(
        "fork-node", {"data": "exclude"}, branch_id="excluded", dry_run=False,
    )
    assert excluded["input_mapping"] == []
    assert not (tmp_path / excluded["workspace"] / "data/train.csv").exists()

    copied = BranchWorkspace(tmp_path, graph).fork(
        "fork-node", {"data": "copy"}, branch_id="copied", dry_run=False,
    )
    assert copied["input_mapping"] == []
    assert (tmp_path / copied["workspace"] / "data/train.csv").read_text() == "inherited data"


def test_guarded_run_rejects_unbound_branch_result_references(monkeypatch):
    monkeypatch.setattr(verification, "required_policy", lambda session, run: True)
    monkeypatch.setattr(verification, "evidence_sources", lambda session, run: [])
    monkeypatch.setattr(
        acceptance,
        "acceptance_gate",
        lambda session, run, sources: {"ready": True, "failures": []},
    )
    run = SimpleNamespace(
        kind="agent",
        node_id=None,
        project_id="project",
        config={"resolved_branch_references": [
            {"source_path": "branches/source/results/metrics.csv",
             "destination": "results/metrics.csv", "source": "results"},
        ]},
        dependencies=[],
        resource={},
    )

    result = verification.verification_gate(SimpleNamespace(get=lambda *args: None), run)

    assert result["ready"] is False
    assert result["blocked_reason"] == "verification_scope"
