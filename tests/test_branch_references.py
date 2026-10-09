from research.kernel.artifacts import resolve_branch_references
from services.worker.execute import materialize_branch_references


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
        {"source_path": "data/train.csv", "destination": "data/train.csv"}
    ]
    assert missing == []

    local = tmp_path / "branches" / "fork" / "data" / "train.csv"
    local.parent.mkdir(parents=True)
    local.write_text("branch-local override")
    assert resolve_branch_references(tmp_path, graph, "fork") == ([], [])
