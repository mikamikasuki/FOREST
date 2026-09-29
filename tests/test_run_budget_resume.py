"""Public resume API budget edits, actual HTTP and SQLite, no model or worker."""
import json
import subprocess
import sys

from tests.test_worker import Harness


def test_public_budget_resume_preserves_run_evidence_and_outer_limits(tmp_path):
    harness = Harness(tmp_path)
    harness.start_api()
    try:
        project = harness.request('POST', '/api/projects', json={'name': 'Resume accounting validation', 'budget': {'allow_paid': True, 'cost_usd': 2}})
        code = '''
import json
from pathlib import Path
from services.api.db import Session,TaskRun,ModelRequest,Provider
from services.api.common import project_dir
with Session.begin() as s:
 p=Provider(name='No model calls',kind='openai',base_url='https://api.openai.com/v1',model='ledger-only',allow_paid=True,config={'budget_usd':10,'pricing':{'input_per_million':1,'output_per_million':1}})
 s.add(p);s.flush()
 r=TaskRun(project_id=PROJECT,request_id='budget-resume-test',kind='agent',status='budget_exhausted',config={'agent_budget':{'cost':.8,'steps':90},'instructions':'Retain prior evidence.'})
 s.add(r);s.flush();r.output_path='runs/'+r.id
 s.add(ModelRequest(provider_id=p.id,project_id=PROJECT,run_id=r.id,model='ledger-only',status='settled',estimated_microusd=540000,reserved_microusd=600000,details={'usage':{'cost':.54}}))
 workspace=project_dir(PROJECT)/r.output_path/'workspace';workspace.mkdir(parents=True)
 (workspace/'agent_session.json').write_text(json.dumps({'run_id':r.id,'transcript':[{'step':1,'observation':'actual previous accounting input'}],'totals':{'cost':.54}}))
 print(json.dumps({'id':r.id,'provider_id':p.id,'workspace':str(workspace)}))
'''.replace('PROJECT', repr(project['id']))
        seeded = subprocess.run([sys.executable, '-c', code], env=harness.env, cwd=tmp_path,
                                capture_output=True, text=True, timeout=15)
        assert seeded.returncode == 0, seeded.stdout + seeded.stderr
        run = json.loads(seeded.stdout)
        from pathlib import Path
        session = Path(run['workspace']) / 'agent_session.json'
        previous = session.read_bytes()
        endpoint = f"/api/runs/{run['id']}/resume"
        for update in ({'cost': -1}, {'cost': True}, {'steps': 2.5}, {'unknown': 2}, {}):
            response = harness.client.post(endpoint, json={'agent_budget': update})
            assert response.status_code == 422
            unchanged = harness.request('GET', f"/api/runs/{run['id']}")
            assert unchanged['status'] == 'budget_exhausted'
            assert unchanged['config']['agent_budget'] == {'cost': .8, 'steps': 90}
        resumed = harness.request('POST', endpoint, json={'agent_budget': {'cost': 1.1}})
        assert resumed['id'] == run['id'] and resumed['status'] == 'queued'
        assert resumed['config']['agent_budget'] == {'cost': 1.1, 'steps': 90}
        assert resumed['config']['_next_attempt']['mode'] == 'continue'
        change = resumed['config']['agent_budget_changes'][-1]
        assert change['previous']['cost'] == .8 and change['updated']['cost'] == 1.1
        assert session.read_bytes() == previous
        verify = '''
import json
from sqlalchemy import select
from services.api.db import Session,TaskRun,ModelRequest,Provider,Project,Event
from research.agents.budget import make_request_guard,BudgetExceeded
with Session() as s:
 r=s.get(TaskRun,RUN);p=s.get(Provider,PROVIDER);project=s.get(Project,PROJECT)
 rows=list(s.scalars(select(ModelRequest).where(ModelRequest.run_id==RUN)))
 assert len(rows)==1 and rows[0].estimated_microusd==540000
 assert p.config['budget_usd']==10 and project.budget['cost_usd']==2
 assert len(list(s.scalars(select(Event).where(Event.project_id==PROJECT,Event.type=='agent_budget_changed'))))==1
 print('Existing usage, provider/project caps and explicit budget edit event preserved')
'''.replace('RUN', repr(run['id'])).replace('PROVIDER', repr(run['provider_id'])).replace('PROJECT', repr(project['id']))
        checked = subprocess.run([sys.executable, '-c', verify], env=harness.env, cwd=tmp_path,
                                 capture_output=True, text=True, timeout=15)
        assert checked.returncode == 0, checked.stdout + checked.stderr
    finally:
        harness.cleanup()
