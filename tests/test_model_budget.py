"""Real database concurrency and accounting checks; no model or HTTP substitution."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def test_shared_budget_concurrency_and_unknown_usage(tmp_path):
    env={**os.environ,'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'budget.db'),'FOREST_DATA_DIR':str(tmp_path/'data'),'PYTHONPATH':str(ROOT)}
    result=subprocess.run([sys.executable,__file__,'check'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr

if __name__=='__main__':
    from concurrent.futures import ThreadPoolExecutor
    from services.api.db import migrate,Session,Provider,asdict
    from research.agents.budget import make_request_guard,usage_summary,BudgetExceeded
    migrate()
    pricing={'input_per_million':1,'output_per_million':1,'cached_input_per_million':.1,'currency':'USD'}
    with Session.begin() as s:
        p=Provider(name='Database accounting test',kind='openai',base_url='https://api.openai.com/v1',model='accounting-input',allow_paid=True,config={'budget_usd':.010245,'pricing':pricing})
        s.add(p);s.flush();provider=asdict(p,True)
    guard=make_request_guard(provider)
    event={'phase':'before','model':'accounting-input','input_bytes':0,'max_output_tokens':1,'pricing':pricing}
    def reserve(_):
        try:return guard(event)
        except BudgetExceeded:return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted=[x for x in pool.map(reserve,range(32)) if x]
    assert len(accepted)==5,accepted
    status=usage_summary(provider['id']);assert abs(status['reserved_usd']-.010245)<1e-10 and status['remaining_usd']==0
    # Unknown post-send usage retains the reservation; it never becomes free.
    guard({'phase':'error','reservation':accepted[0],'ambiguous':True,'http_status':503})
    assert usage_summary(provider['id'])['uncertain_requests']==1
    assert reserve(0) is None
    # An explicit rejected request releases exactly its reservation.
    guard({'phase':'error','reservation':accepted[1],'ambiguous':False,'http_status':429})
    assert reserve(0) is not None
    # Settling known token counts uses configured rates, never an invoice claim.
    guard({'phase':'after','reservation':accepted[2],'usage':{'input_tokens':100,'cached_input_tokens':50,'output_tokens':20},'request_id':'accounting-unit-input'})
    status=usage_summary(provider['id']);assert abs(status['estimated_cost_usd']-.000075)<1e-10,status
    guard({'phase':'after','reservation':accepted[2],'usage':{'input_tokens':100,'output_tokens':20}})
    assert usage_summary(provider['id'])['estimated_cost_usd']==status['estimated_cost_usd']
    with Session.begin() as s:s.get(Provider,provider['id']).allow_paid=False
    assert reserve(0) is None
    print('Concurrent shared reservations, ambiguous usage, rejection release and idempotent settlement passed')
