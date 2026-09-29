"""Export the current real demo through ordinary APIs and check import bindings."""
import argparse
import json
import zipfile
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-import', action='store_true')
    args = parser.parse_args()
    state = json.loads((ROOT / 'var/demo.json').read_text())
    pid = state['project_id']
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    client = httpx.Client(base_url='http://127.0.0.1:8000', timeout=180)
    paper_response = client.get('/api/papers/' + pid)
    paper_response.raise_for_status()
    paper = paper_response.json()
    assert paper['data'].get('compiled_revision') == paper['revision'], 'Compile the current paper before final export'
    for fmt, name in [('pdf', 'forest-paper.pdf'), ('source', 'forest-paper-source.zip')]:
        response = client.post('/api/papers/' + pid + '/export', json={'format': fmt})
        response.raise_for_status()
        (output / name).write_bytes(response.content)
    response = client.post('/api/projects/' + pid + '/export', json={})
    response.raise_for_status()
    archive = response.content
    (output / 'forest-project.zip').write_bytes(archive)
    excluded = {'.git', '.venv', 'node_modules', 'dist', 'var', 'output', '__pycache__', '.pytest_cache', 'test-results', 'playwright-report'}
    with zipfile.ZipFile(output / 'forest-source.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in ROOT.rglob('*'):
            relative = path.relative_to(ROOT)
            if (not path.is_file() or path.is_symlink() or set(relative.parts) & excluded
                    or path.name in {'.env', 'secrets.json', 'owner-token', '.DS_Store'} or path.suffix == '.pyc'):
                continue
            bundle.write(path, 'forest/' + relative.as_posix())
    report = {'project_id': pid, 'paper_id': paper['id'], 'paper_revision': paper['revision'],
              'exports': {p.name: p.stat().st_size for p in output.glob('forest-*') if p.is_file()},
              'import_check': 'not_requested'}
    if args.verify_import:
        imported = client.post('/api/projects/import', files={'file': ('forest-project.zip', archive, 'application/zip')})
        imported.raise_for_status()
        imported_id = imported.json()['id']
        try:
            graph = client.get(f'/api/projects/{imported_id}/graph').json()
            original_graph = client.get(f'/api/projects/{pid}/graph').json()
            assert len(graph['nodes']) == len(original_graph['nodes'])
            assert not ({n['id'] for n in graph['nodes']} & {n['id'] for n in original_graph['nodes']})
            imported_paper = client.get('/api/papers/' + imported_id).json()
            assert imported_paper['data']['source'] == paper['data']['source']
            pdf = client.get(f'/api/projects/{imported_id}/download', params={'path': imported_paper['data']['pdf_path']})
            pdf.raise_for_status()
            assert pdf.content == (output / 'forest-paper.pdf').read_bytes()
            runs = client.get(f'/api/projects/{imported_id}/runs').json()
            assert set(imported_paper['data']['source_run_ids']) <= {r['id'] for r in runs}
            source = next(r for r in runs if r['id'] == imported_paper['data']['source_run_ids'][0])
            table = client.get(f'/api/projects/{imported_id}/file/preview', params={'path': source['output_path'] + '/predictions.csv', 'limit': 2})
            table.raise_for_status()
            assert table.json()['total'] == 939850
            report['import_check'] = {'status': 'passed', 'nodes': len(graph['nodes']), 'runs': len(runs), 'prediction_rows': table.json()['total'], 'temporary_project_deleted': True}
        finally:
            client.delete('/api/projects/' + imported_id).raise_for_status()
    (ROOT / 'var/qa/export-check.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
