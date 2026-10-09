"""Theory checks classify symbolic computation as inference, never measured evidence (issue #96)."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_api import create, ok

ROOT = Path(__file__).resolve().parents[1]


def check(client, project, **body):
    return client.post("/api/theory/check", json={"project_id": project["id"], **body})


def theories(client, project):
    return ok(client.get("/api/theories", params={"project_id": project["id"]}))


def case_symbolic_computation_is_not_measured_evidence(client):
    from research.validation.protocol import LABELS
    project = create(client)
    checked = ok(check(client, project, expression="x**2 + 2*x + 1", variable="x", kind="simplify"))
    data = checked["data"]
    assert data["check_type"] == "symbolic_computation"
    assert isinstance(data["result"], str) and data["result"].strip()
    # No empirical observations or measurements were supplied, so the record must not
    # claim measurement; a symbolic derivation is an inference from the supplied expression.
    assert data["evidence_label"] != "MEASURED", data
    assert data["evidence_label"] == "INFERRED", data
    assert data["evidence_label"] in LABELS, data
    assert "not a proof of unprovided assumptions" in data["scope"]
    persisted = [row for row in theories(client, project) if row["id"] == checked["id"]]
    assert len(persisted) == 1, persisted
    assert persisted[0]["data"]["evidence_label"] == data["evidence_label"], persisted
    assert persisted[0]["data"]["check_type"] == "symbolic_computation", persisted


def case_every_symbolic_operation_avoids_measured_evidence(client):
    from research.validation.protocol import LABELS
    project = create(client)
    operations = (
        {"expression": "x**2 + 2*x + 1", "variable": "x", "kind": "simplify"},
        {"expression": "sin(x)*x", "variable": "x", "kind": "differentiate"},
        {"expression": "x**2 - 1", "variable": "x", "kind": "solve"},
        {"expression": "x**2 + 2*x + 1", "variable": "x", "kind": "numeric", "values": {"x": 2}},
    )
    for body in operations:
        data = ok(check(client, project, **body))["data"]
        assert data["check_type"] == "symbolic_computation"
        assert data["evidence_label"] == "INFERRED", (body, data)
        assert data["evidence_label"] in LABELS, (body, data)
    records = theories(client, project)
    assert len(records) == len(operations)
    assert {row["data"]["evidence_label"] for row in records} == {"INFERRED"}


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_theory_evidence(tmp_path, case):
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"), "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "theory.sqlite"),
           "FOREST_OWNER_TOKEN": "theory-test-owner", "FOREST_MODEL": "", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app
    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()["case_" + selected](client)
