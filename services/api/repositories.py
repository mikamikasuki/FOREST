"""Durable repository preparation and credential-free source provenance."""
import json
import re

from fastapi import APIRouter, Body
from sqlalchemy import select

from research.execution.repository import normalize_repository, RepositoryError
from services.worker.scheduler import enqueue, _lock_project
from .common import error, get, project_dir, safe_path
from .db import Session, TaskRun, asdict


router = APIRouter()


@router.post('/api/projects/{ident}/repositories/clone')
def clone_repository(ident: str, body: dict = Body(...)):
    """Validate an input and enqueue work; Git/network calls happen in a worker."""
    if set(body) - {'repository', 'request_id'}:
        error('INVALID_REPOSITORY', 'Only repository and request_id fields are accepted', 422)
    try:
        repository = normalize_repository(body.get('repository'))
    except RepositoryError as exc:
        error(exc.code, str(exc), 422)
    request_id = body.get('request_id')
    if request_id is not None and (not isinstance(request_id, str) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', request_id)):
        error('INVALID_REQUEST_ID', 'request_id must be 1–128 letters, digits, dots, underscores, colons or hyphens', 422)
    with Session.begin() as session:
        # Reuse the scheduler's project writer lock so competing requests with
        # one ID cannot bypass the payload comparison before enqueue sees it.
        _lock_project(session, ident)
        if request_id:
            previous = session.scalar(select(TaskRun).where(
                TaskRun.project_id == ident, TaskRun.request_id == request_id))
            if previous and (previous.kind != 'repository_clone' or
                    previous.config.get('repository') != repository):
                error('REQUEST_ID_CONFLICT', 'This request ID belongs to a different submission', 409,
                      'Reuse an ID only for the same repository request, or choose a new ID.')
        return asdict(enqueue(session, ident, 'repository_clone', {'repository': repository}, request_id))


@router.get('/api/runs/{ident}/repository')
def repository_manifest(ident: str):
    """Expose only validated provenance fields, never credential material."""
    with Session() as session:
        run = get(session, TaskRun, ident)
        response = {'run_id': ident, 'project_id': run.project_id,
                    'status': run.status, 'repository': None}
        if not run.output_path:
            return response
        folder = safe_path(project_dir(run.project_id), run.output_path)
        manifest = safe_path(folder, 'repository.json')
        if not manifest.exists():
            return response
        try:
            if not manifest.is_file() or manifest.stat().st_size > 65536:
                raise ValueError('Invalid manifest file')
            data = json.loads(manifest.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Invalid manifest object')
            spec = normalize_repository({'url': data.get('url'),
                'ref': data.get('requested_ref'), 'directory': data.get('directory'),
                'transport': data.get('transport')})
            commit = data.get('commit')
            if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
                raise ValueError('Invalid source commit')
        except (OSError, UnicodeError, ValueError, TypeError):
            error('INVALID_REPOSITORY_MANIFEST', 'The saved repository manifest is invalid', 422)
        response['repository'] = {'url': spec['url'], 'requested_ref': spec['ref'],
            'commit': commit, 'directory': spec['directory'], 'transport': spec['transport']}
        return response
