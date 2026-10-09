"""Project copy operations must remap references without rewriting authored text.

Issue #126: duplicating a project (and importing an exported archive) applied a
blind substring replacement of every copied identifier to every string, so an
authored note or manuscript that merely mentioned the source project, node or
run identifier for provenance was silently rewritten in the copy.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from test_api import add_node, create, ok

ROOT = Path(__file__).resolve().parents[1]


def note(client, project, statement, **data):
    return ok(client.post('/api/ideas', json={
        'project_id': project['id'], 'title': 'Provenance note',
        'data': {'statement': statement, **data},
    }))


def launch(client, project):
    node = add_node(client, project, config={'kind': 'command', 'command': [sys.executable, '-c', 'pass']})
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={'request_id': 'authored-text-source'}))
    return node, run


def case_duplicate_preserves_authored_text_ids(client):
    source = create(client)
    node, run = launch(client, source)
    statement = (f'For provenance, this note names source project ID {source["id"]}, '
                 f'source node ID {node["id"]} and source run ID {run["id"]}; '
                 'preserve this sentence verbatim.')
    original = note(client, source, statement, run_ids=[run['id']], node_id=node['id'])

    copied = ok(client.post(f"/api/projects/{source['id']}/duplicate"))
    assert copied['id'] != source['id']
    copied_ideas = ok(client.get('/api/ideas', params={'project_id': copied['id']}))
    assert len(copied_ideas) == 1
    copied_idea = copied_ideas[0]

    assert copied_idea['id'] != original['id']
    assert copied_idea['title'] == 'Provenance note'
    assert copied_idea['data']['statement'] == statement, copied_idea['data']['statement']

    # Structured references still follow the copied records.
    copied_graph = ok(client.get(f"/api/projects/{copied['id']}/graph"))
    copied_node = copied_graph['nodes'][0]
    copied_run = ok(client.get(f"/api/projects/{copied['id']}/runs"))[0]
    assert copied_node['id'] != node['id'] and copied_run['id'] != run['id']
    assert copied_idea['data']['node_id'] == copied_node['id']
    assert copied_idea['data']['run_ids'] == [copied_run['id']]


def case_duplicate_preserves_manuscript_authored_source(client):
    source = create(client)
    node, run = launch(client, source)
    paper = ok(client.get(f"/api/papers/{source['id']}"))
    manuscript = (
        f'% Provenance: this manuscript names source project ID {source["id"]} '
        f'and source node ID {node["id"]} verbatim.\n'
        '\\documentclass{article}\n\\begin{document}\n\\end{document}\n'
    )
    bindings = [{'run_id': run['id'], 'path': run['output_path'] + '/workspace/metrics.json'}]
    saved = ok(client.patch(f"/api/papers/{source['id']}", json={
        'expected_revision': paper['revision'],
        'data': {'source': manuscript, 'bindings': bindings, 'source_dir': run['output_path']},
    }))
    assert saved['data']['source'] == manuscript

    copied = ok(client.post(f"/api/projects/{source['id']}/duplicate"))
    copied_paper = ok(client.get(f"/api/papers/{copied['id']}"))
    copied_run = ok(client.get(f"/api/projects/{copied['id']}/runs"))[0]
    assert copied_run['id'] != run['id']

    assert copied_paper['data']['source'] == manuscript, copied_paper['data']['source']
    assert copied_paper['data']['bindings'][0]['run_id'] == copied_run['id']
    assert copied_paper['data']['bindings'][0]['path'].startswith(f'runs/{copied_run["id"]}/')
    assert copied_paper['data']['source_dir'] == copied_run['output_path']


def case_import_preserves_authored_text_ids(client):
    source = create(client)
    node, run = launch(client, source)
    statement = (f'Provenance note for source node ID {node["id"]} and source run ID {run["id"]}; '
                 'retain this historical reference.')
    note(client, source, statement)

    archive = client.post(f"/api/projects/{source['id']}/export", json={})
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        manifest = json.loads(zipped.read('forest-project.json'))
    assert manifest['resources']['ideas'][0]['data']['statement'] == statement

    imported = ok(client.post(
        '/api/projects/import',
        files={'file': ('forest-project.zip', archive.content, 'application/zip')},
    ))
    imported_ideas = ok(client.get('/api/ideas', params={'project_id': imported['id']}))
    assert len(imported_ideas) == 1
    imported_idea = imported_ideas[0]
    assert imported_idea['data']['statement'] == statement, imported_idea['data']['statement']

    imported_graph = ok(client.get(f"/api/projects/{imported['id']}/graph"))
    imported_run = ok(client.get(f"/api/projects/{imported['id']}/runs"))[0]
    assert imported_idea['id'] != manifest['resources']['ideas'][0]['id']
    assert imported_run['id'] != run['id']
    assert imported_run['node_id'] == imported_graph['nodes'][0]['id']


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_duplicate_authored_text(tmp_path, case):
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"),
           "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "duplicate.sqlite"),
           "FOREST_OWNER_TOKEN": "duplicate-test-owner", "FOREST_MODEL": "",
           "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app

    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()["case_" + selected](client)
    print("SCENARIO PASSED:", selected)
