"""Actual local-model planner creates and executes a route from an empty graph."""
import json
import ast
import os
from pathlib import Path
import time
import sqlite3

import pytest
from test_worker import Harness


@pytest.mark.skipif(os.environ.get('FOREST_LIVE_AGENT_TEST') != '1', reason='Requires explicitly enabled real local model execution')
def test_real_autonomous_plan_and_prime_verification(tmp_path):
    harness = Harness(tmp_path)
    latest = {}
    try:
        harness.start_api()
        provider = harness.request('POST', '/api/providers', json={'name': 'Actual local planner', 'kind': 'ollama', 'base_url': 'http://127.0.0.1:11434', 'model': 'qwen2.5:3b', 'config': {'temperature': 0, 'max_tokens': 4096, 'context_length': 16384, 'timeout': 120}})
        project = harness.request('POST', '/api/projects', json={'name': 'Actual autonomous prime verification', 'goal': 'Operational verification, not a novel scientific contribution: create the simplest executable research path with one Engineer Agent node. That node must implement two independent Python prime-count algorithms (trial division and sieve), actually run both for integers <=10000, compare their counts for exact equality, and save metrics.json plus report.md. Read the actual output before reporting it. Do not look up prime counts or use precomputed numbers. No literature search is needed. The node config must include kind=agent, role=Engineer, expected_outputs=["metrics.json","report.md"], metrics_file=metrics.json. After the completed measured run, cite that actual run ID and conclude completed. Start from this empty graph; do not assume code or nodes exist.', 'mode': 'auto', 'budget': {'seconds': 450, 'max_runs': 10, 'allow_paid': False}, 'config': {'provider_id': provider['id']}})
        graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        assert graph['nodes'] == []
        harness.request('POST', f"/api/projects/{project['id']}/research/start", json={'autonomous': True, 'max_cycles': 2})
        harness.start_worker()
        deadline = time.monotonic() + 400
        while time.monotonic() < deadline:
            latest = harness.request('GET', f"/api/projects/{project['id']}")
            control = latest['config'].get('controller', {})
            if control.get('status') in ('completed', 'blocked', 'budget_exhausted'):
                break
            time.sleep(.4)
        runs = harness.request('GET', f"/api/projects/{project['id']}/runs")
        graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        (tmp_path / 'live_controller_evidence.json').write_text(json.dumps({'project': latest, 'graph': graph, 'runs': runs}, indent=2))
        assert latest['config']['controller']['status'] == 'completed', {'control': latest['config']['controller'], 'runs': [(r['kind'], r['status'], r.get('error')) for r in runs]}
        assert graph['nodes'] and any(r['kind'] == 'research_plan' and r['status'] == 'completed' for r in runs)
        agents = [r for r in runs if r['kind'] == 'agent' and r['status'] == 'completed']
        assert agents
        matching = []
        for run in agents:
            workspace = harness.output(run) / 'workspace'
            if (workspace / 'metrics.json').is_file() and (workspace / 'report.md').is_file():
                metrics = json.loads((workspace / 'metrics.json').read_text())
                matching.append({'metrics': metrics, 'report': (workspace / 'report.md').read_text(), 'run_id': run['id']})
                # Exact mathematical reference, independent of model narrative.
                def numeric_values(value):
                    if isinstance(value, dict):
                        return [n for x in value.values() for n in numeric_values(x)]
                    if isinstance(value, list):
                        return [n for x in value for n in numeric_values(x)]
                    return [value] if isinstance(value, (int, float)) and not isinstance(value, bool) else []
                assert numeric_values(metrics).count(1229) >= 2, metrics
                functions = []
                for source in workspace.rglob('*.py'):
                    if '.forest-processes' in source.parts:
                        continue
                    for function in ast.walk(ast.parse(source.read_text())):
                        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            functions.append({'source': str(source.relative_to(workspace)), 'name': function.name,
                                              'remainder_operations': sum(isinstance(node, ast.Mod) for node in ast.walk(function)),
                                              'body': ast.dump(ast.Module(body=function.body, type_ignores=[]))})
                trial = [f for f in functions if f['remainder_operations'] > 0]
                sieve = [f for f in functions if ('sieve' in f['name'].lower() or 'eratosthenes' in f['name'].lower()) and f['remainder_operations'] == 0]
                (workspace / 'independent_algorithm_review.json').write_text(json.dumps({'functions': functions, 'criterion': 'Trial division uses divisibility remainders; independently named sieve uses a different body without trial remainder operations.'}, indent=2))
                assert trial and sieve and any(a['body'] != b['body'] for a in trial for b in sieve), 'Actual code did not establish two independent trial-division and sieve implementations'

        assert matching
        print(json.dumps({'nodes': len(graph['nodes']), 'runs': len(runs), 'measurements': matching, 'evidence_directory': str(tmp_path)}))
    finally:
        # Cancel real detached children if the actual model fails or times out.
        from research.agents.processes import ManagedProcesses
        database = tmp_path / 'integration.db'
        if database.exists():
            with sqlite3.connect(database) as connection:
                harness.run_processes.extend((pid, created) for pid, created in connection.execute('SELECT pid, process_created FROM task_runs WHERE pid IS NOT NULL AND process_created IS NOT NULL'))
        harness.cleanup()
        for workspace in (tmp_path / 'data' / 'projects').glob('*/runs/*/workspace'):
            ManagedProcesses(workspace).cancel_all()
