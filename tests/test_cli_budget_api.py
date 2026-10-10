"""New CLI API views exercise real isolated database and export boundaries."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_project_usage_and_revision_checked_pdf_export(tmp_path):
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"),
           "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "api.db"),
           "FOREST_MODEL": "", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, __file__, "check"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app
    from services.api.db import Session, ModelRequest, Provider, TaskRun, PaperDocument
    from services.api.common import project_dir

    with TestClient(app) as client:
        with Session.begin() as session:
            provider = Provider(name="Ledger only", kind="openai", base_url="https://example.invalid",
                                model="not-called", allow_paid=True, config={"budget_usd": 10})
            session.add(provider)
            session.flush()
            provider_id = provider.id
        project = client.post("/api/projects", json={"name": "CLI accounting", "mode": "manual",
            "budget": {"allow_paid": True, "cost_usd": 5, "max_runs": 10},
            "config": {"provider_id": provider_id, "publication_profile": "operational"}}).json()
        other = client.post("/api/projects", json={"name": "Other ledger"}).json()
        pid = project["id"]
        with Session.begin() as session:
            session.add_all([
                ModelRequest(provider_id=provider_id, project_id=pid, model="not-called", status="completed", estimated_microusd=1000000),
                ModelRequest(provider_id=provider_id, project_id=pid, model="not-called", status="uncertain", reserved_microusd=500000),
                ModelRequest(provider_id=provider_id, project_id=other["id"], model="not-called", status="completed", estimated_microusd=2000000),
                TaskRun(project_id=pid, request_id="ledger-only", kind="command", status="paused",
                        config={"agent_budget": {"cost": 1}}, resource={"elapsed_seconds": 12}),
            ])
        response = client.get(f"/api/projects/{pid}/usage")
        assert response.status_code == 200, response.text
        usage = response.json()
        assert usage["estimated_cost_usd"] == 1
        assert usage["reserved_usd"] == .5
        assert usage["remaining_usd"] == 3.5
        assert usage["uncertain_requests"] == 1
        assert usage["elapsed_seconds"] == 12
        assert usage["run_count"] == 1
        assert usage["active_run_limits"][0]["agent_budget"] == {"cost": 1}
        provider_usage = usage["providers"][0]
        assert provider_usage["scope"] == "all_projects"
        assert provider_usage["estimated_cost_usd"] == 3
        assert provider_usage["remaining_usd"] == 6.5
        assert "not-called" not in response.text
        assert "credential" not in response.text
        assert client.get("/api/projects/missing/usage").status_code == 404
        fresh = client.get(f"/api/projects/{other['id']}/usage").json()
        assert fresh["remaining_usd"] is None

        with Session.begin() as session:
            unpriced = Provider(name="Signed-in CLI", kind="codex_cli", base_url="http://127.0.0.1", model="gpt-6-luna",
                                config={"budget_usd": 5})
            session.add(unpriced)
            session.flush()
            unpriced_id = unpriced.id
            unpriced_snapshot = {"id": unpriced.id, "model": unpriced.model,
                "kind": unpriced.kind, "base_url": unpriced.base_url,
                "allow_paid": unpriced.allow_paid, "config": unpriced.config}
        from research.agents.budget import make_request_guard
        guard = make_request_guard({**unpriced_snapshot,
            "_usage_context": {"project_id": pid}})
        reservation = guard({"phase": "before", "api": "codex_cli", "model": "gpt-6-luna",
            "input_bytes": 100, "max_output_tokens": 1024, "attempt": 1})
        guard({"phase": "after", "reservation": reservation, "request_id": "local-cli-turn",
            "status": "completed", "usage": {"input_tokens": 100, "output_tokens": 20,
            "cached_input_tokens": 10, "cost": None, "cost_source": "codex_subscription_unpriced"}})
        incomplete = client.get(f"/api/projects/{pid}/usage").json()
        assert incomplete["estimated_cost_usd"] is None
        assert incomplete["known_cost_usd"] == 1
        assert incomplete["unknown_cost_requests"] == 1
        assert incomplete["cost_source"] == "mixed_known_and_unknown"
        assert incomplete["remaining_usd"] is None
        cli_usage = client.get(f"/api/providers/{unpriced_id}/usage").json()
        assert cli_usage["estimated_cost_usd"] is None
        assert cli_usage["known_cost_usd"] == 0
        assert cli_usage["unknown_cost_requests"] == 1
        assert cli_usage["uncertain_requests"] == 0
        assert cli_usage["requests"][0]["status"] == "completed"
        assert cli_usage["cost_source"] == "unpriced_local_provider"
        assert cli_usage["remaining_usd"] is None
        assert cli_usage["requests"][0]["reserved_usd"] == 0
        assert cli_usage["requests"][0]["usage"]["output_tokens"] == 20

        paper = client.get(f"/api/papers/{pid}").json()
        folder = project_dir(pid)
        (folder / "paper").mkdir(exist_ok=True)
        (folder / "paper" / "paper.pdf").write_bytes(b"%PDF-1.4\nexport boundary fixture\n")
        with Session.begin() as session:
            saved = session.get(PaperDocument, paper["id"])
            saved.data = {**saved.data, "pdf_path": "paper/paper.pdf", "compiled_revision": saved.revision}
        assert client.post(f"/api/papers/{pid}/export", json={"format": "pdf", "expected_revision": paper["revision"]}).status_code == 200
        changed = client.patch(f"/api/papers/{pid}", json={"expected_revision": paper["revision"], "data": {"source": "New source"}}).json()
        conflict = client.post(f"/api/papers/{pid}/export", json={"format": "pdf", "expected_revision": paper["revision"]})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "REVISION_CONFLICT"
        stale = client.post(f"/api/papers/{pid}/export", json={"format": "pdf", "expected_revision": changed["revision"]})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "STALE_PDF"
        assert client.post(f"/api/papers/{pid}/export", json={"format": "source"}).status_code == 200
    print("Project-specific accounting and current-revision PDF exports passed")
