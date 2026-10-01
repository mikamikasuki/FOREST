"""Read saved execution progress without starting Git, Docker or SSH commands."""
from fastapi import APIRouter
from .db import Session, TaskRun
from .common import get, project_dir, safe_path
from research.execution.diagnostics import run_diagnostics

router = APIRouter()


@router.get('/api/runs/{ident}/diagnostics')
def diagnostics(ident: str):
    with Session() as session:
        run = get(session, TaskRun, ident)
        folder = safe_path(project_dir(run.project_id), run.output_path)
        return run_diagnostics(folder, run_id=run.id, status=run.status,
                               config=run.config, resource=run.resource)
