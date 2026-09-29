"""Opt-in live-provider acceptance. Every model response and process is real.

Run: FOREST_LIVE_AGENT_TEST=1 .venv/bin/python -m pytest tests/test_live_agent_runtime.py -q
Requires an already configured local Ollama qwen2.5:3b service.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


def execute_real_agent(directory):
    from services.api.db import migrate, Session, Project, TaskRun, Agent, uid
    from services.api.common import project_dir
    from research.agents.runtime import run_agent, AgentYield
    migrate()
    manifest_path = directory / 'manifest.json'
    if not manifest_path.exists():
        with Session.begin() as session:
            project = Project(name='Live agent runtime validation', goal='Numerically verify the integral of 4/(1+x*x) over [0,1] with actual code execution.')
            session.add(project)
            session.flush()
            run = TaskRun(project_id=project.id, request_id=uid(), kind='agent', status='running')
            session.add(run)
            session.add(Agent(name='Runtime Verifier', role='Runtime Verifier', instructions='Execute the given process and verify its requested output. After reading a valid output, finish. Do not design further experiments for this operational verification.', tools=['start_process','wait_for_process','read_process_output','read_file','finish']))
            session.flush()
            manifest = {'project_id': project.id, 'run_id': run.id}
        manifest_path.write_text(json.dumps(manifest))
    manifest = json.loads(manifest_path.read_text())
    workspace = project_dir(manifest['project_id']) / 'live_workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    script = workspace / 'integrate.py'
    if not script.exists():
        script.write_text("from pathlib import Path\nimport json,math,time\nstart=time.monotonic()\nn=150_000_000\nvalue=sum(4/(1+((i+.5)/n)**2) for i in range(n))/n\nPath('integral.json').write_text(json.dumps({'integral':value,'absolute_error':abs(value-math.pi),'intervals':n,'elapsed_seconds':time.monotonic()-start}))\nprint(value,flush=True)\n")
    config = {'role': 'Runtime Verifier', 'required_outputs': ['integral.json'], 'metrics_file': 'integral.json', 'provider_snapshot': {'kind': 'ollama', 'base_url': 'http://127.0.0.1:11434', 'model': 'qwen2.5:3b', 'config': {'temperature': 0, 'max_tokens': 400, 'timeout': 120}},
              'agent_budget': {'steps': 12, 'active_seconds': 500},
              'instructions': f'Execute the existing integrate.py without changing it. Use start_process with command=["{sys.executable}","integrate.py"]. Then use wait_for_process for that returned process_id. After completion read integral.json, report its actual integral and absolute_error, then finish with artifacts=["integral.json"]. Do not rerun completed work. This is a numerical process verification task, not an idea analysis. The file already exists; launch it now.'}
    try:
        result = run_agent(manifest['run_id'], workspace, config)
    except AgentYield as exc:
        (directory / 'yield.json').write_text(json.dumps({'status': exc.status, 'wait_for': exc.wait_for, 'resume_after': exc.resume_after}))
        return 20
    (directory / 'verified_result.json').write_text(json.dumps({'result': result, 'measurement': json.loads((workspace / 'integral.json').read_text()), 'workspace': str(workspace)}, indent=2))
    return 0


@pytest.mark.skipif(os.environ.get('FOREST_LIVE_AGENT_TEST') != '1', reason='Explicit opt-in required for actual local model calls')
def test_real_model_async_command_and_durable_resume(tmp_path):
    env = {**os.environ, 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'agent.db'), 'FOREST_DATA_DIR': str(tmp_path / 'data'), 'PYTHONPATH': str(ROOT)}
    deadline = time.monotonic() + 500
    launches = 0
    with (tmp_path / 'actual_agent.log').open('w') as log:
        while time.monotonic() < deadline:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--execute', str(tmp_path)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            launches += 1
            assert process.returncode in (0, 20), (tmp_path / 'actual_agent.log').read_text()
            if process.returncode == 0:
                break
            waiting = json.loads((tmp_path / 'yield.json').read_text())
            assert waiting['status'] == 'waiting', waiting
            time.sleep(max(.1, min(5, waiting['resume_after'] - time.time())))
        else:
            pytest.fail('Actual local model did not complete within acceptance observation window')
    verified = json.loads((tmp_path / 'verified_result.json').read_text())
    assert verified['measurement']['absolute_error'] < 1e-10
    session = json.loads((Path(verified['workspace']) / 'agent_session.json').read_text())
    assert session['status'] == 'completed'
    assert any('tool_result' in turn for turn in session['transcript'])
    assert verified['result']['usage']['input_tokens'] > 0
    assert verified['result']['artifacts'] == ['integral.json']
    assert verified['result']['observed_metrics'] == verified['measurement']
    print(json.dumps({'executor_launches': launches, 'steps': verified['result']['steps'], 'measurement': verified['measurement'], 'evidence_directory': str(tmp_path)}))


if __name__ == '__main__' and '--execute' in sys.argv:
    sys.exit(execute_real_agent(Path(sys.argv[-1])))
