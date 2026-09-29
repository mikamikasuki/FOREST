"""Opt-in real model, detached numeric computation, worker restart and resume."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from test_worker import Harness, wait_until


@pytest.mark.skipif(os.environ.get('FOREST_LIVE_AGENT_TEST')!='1',reason='Requires the real local Ollama provider')
def test_real_agent_wait_survives_worker_restart(tmp_path):
    h=Harness(tmp_path)
    try:
        h.start_api(); h.start_worker()
        provider=h.request('POST','/api/providers',json={'name':'Live local runtime verification','kind':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen2.5:3b','config':{'temperature':0,'max_tokens':400,'timeout':120}})
        h.request('POST','/api/agents',json={'name':'Runtime Verifier','role':'Runtime Verifier','instructions':'Execute the existing process and verify the requested output. After reading a valid output, finish. Do not design further experiments for this operational verification.','tools':['start_process','wait_for_process','read_process_output','read_file','finish']})
        project,node=h.project_node(budget={'allow_paid':False})
        graph=h.request('GET',f"/api/projects/{project['id']}/graph")
        workspace=h.directory/'data/projects'/project['id']/graph['branches'][0]['workspace']
        workspace.mkdir(parents=True,exist_ok=True)
        (workspace/'integrate.py').write_text("from pathlib import Path\nimport json,math,time\nstart=time.monotonic()\nn=150_000_000\nvalue=sum(4/(1+((i+.5)/n)**2) for i in range(n))/n\nPath('integral.json').write_text(json.dumps({'integral':value,'absolute_error':abs(value-math.pi),'intervals':n,'elapsed_seconds':time.monotonic()-start}))\nprint(value,flush=True)\n")
        config={'kind':'agent','timeout':None,'role':'Runtime Verifier','required_outputs':['integral.json'],'metrics_file':'integral.json','provider_id':provider['id'],'agent_budget':{'steps':12,'active_seconds':500},'resources':{'cpu':1}}
        instruction=f'Execute the existing integrate.py without changing it. Use start_process with command=["{sys.executable}","integrate.py"]. Then use wait_for_process for that returned process_id. After completion read integral.json, report its actual integral and absolute_error, then finish with artifacts=["integral.json"]. Do not rerun completed work. This is a numerical process verification task, not an idea analysis. The file already exists; launch it now.'
        h.request('PATCH',f"/api/nodes/{node['id']}",json={'config':config,'instructions':instruction})
        run=h.launch(node)
        h.running(run)
        waiting=wait_until(lambda: (current if (current:=h.run(run))['status']=='waiting' else None),timeout=240)
        assert waiting['resource']['wait_for']['process_id']
        h.stop(h.worker)
        # The actual detached calculation owns its own process supervisor.
        h.start_worker()
        result=h.terminal(run,timeout=400)
        assert result['status']=='completed',result
        measured=result['metrics']['observed_metrics']
        assert measured['absolute_error']<1e-10 and measured['intervals']==150_000_000
        assert len(result['resource']['attempts'])>=2
        assert any(attempt['status']=='waiting' for attempt in result['resource']['attempts'])
        state=json.loads((h.output(run)/'workspace/agent_session.json').read_text())
        assert state['status']=='completed'
        print(json.dumps({'run_id':run['id'],'measurement':measured,'attempts':len(result['resource']['attempts']),'evidence_directory':str(tmp_path)}))
    finally:
        # Cancel active managed work through the real API before cleaning up services.
        try:
            if 'run' in locals() and h.run(run)['status'] not in ('completed','failed','cancelled','interrupted'):
                h.request('POST',f"/api/runs/{run['id']}/cancel",json={})
        finally: h.cleanup()
