"""Create and run a real demonstration through the ordinary public product API."""
import json
import sys
import time
from pathlib import Path
import httpx
BASE='http://127.0.0.1:8000'
c=httpx.Client(base_url=BASE,timeout=180)
def request(method,path,**kwargs):
    r=c.request(method,path,**kwargs)
    if r.status_code>=400: raise RuntimeError(f'{method} {path}: {r.status_code} {r.text}')
    return r.json()
def wait(run,timeout=600):
    ident=run['id']; start=time.time(); last=None
    while time.time()-start<timeout:
        value=request('GET','/api/runs/'+ident)
        if value['status']!=last: print(ident[:8],value['status'],flush=True); last=value['status']
        if value['status'] in ('completed','failed','interrupted','cancelled','waiting_input'):
            if value['status']!='completed':
                output=request('GET',f'/api/runs/{ident}/output'); raise RuntimeError(value.get('error','')+'\n'+output['text'][-9000:])
            return value
        time.sleep(1)
    raise TimeoutError(ident)
def main():
    p=request('POST','/api/projects',json={'name':'Probability Correction Without Calibration Labels','description':'UCI Adult × Bank Marketing · CPU mechanism replication · 5 fixed seeds · 10 methods','goal':'Test whether analytic correction for known class weights recovers at least 80% of the Brier improvement from sigmoid calibration without additional calibration labels; use unweighted logistic regression and gradient boosting to test alternative explanations.','mode':'auto','budget':{'max_runs':100,'seconds':7200,'allow_paid':False}})
    pid=p['id']; graph=request('GET',f'/api/projects/{pid}/graph'); branch=graph['branches'][0]
    def add(title,typ,instruction,config,x,y,inputs=None):
        g=request('GET',f'/api/projects/{pid}/graph')
        out=request('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(time.time_ns()),'expected_revision':g['revision'],'operation':'add_node','params':{'title':title,'type':typ,'instructions':instruction,'config':config,'branch_id':branch['id'],'position':{'x':x,'y':y},'inputs':inputs or []}})
        old={n['id'] for n in g['nodes']}; return next(n for n in out['graph']['nodes'] if n['id'] not in old)
    def edge(left,right,relation='depends_on'):
        g=request('GET',f'/api/projects/{pid}/graph'); request('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(time.time_ns()),'expected_revision':g['revision'],'operation':'add_dependency','params':{'source':left['id'],'target':right['id'],'relation':relation}})
    providers=request('GET','/api/providers'); provider=providers[0]
    request('POST',f'/api/providers/{provider["id"]}/test')
    print('Connected real model',provider['model'],flush=True)
    goal=add('Research Question and Experimental Constraints','goal','Define the question, evaluation unit, and comparison scope. All numerical results must come from actual executions.',{'kind':'command','command':[sys.executable,'-c','from pathlib import Path; Path("goal.md").write_text("Compare analytic inverse class-weight correction to balanced, sigmoid, unweighted logistic and boosting baselines. Measure Brier on fixed held-out public observations; no novelty claim."); print("Research goal saved")']},0,160)
    request('PUT',f'/api/projects/{pid}/file',json={'path':branch['workspace']+'/input.txt','content':'FOREST real process validation: sum of squares 1 through 10. Expected mathematical value 385.','expected_revision':0})
    smoke=add('Local Agent · File Access and Process Execution','implementation','Read input.txt. Write verify.py that computes sum(i*i for i in range(1,11)), prints it and writes it into verification.txt. Run verify.py using run_command with command ["python", "verify.py"]. Check the tool result. Finish with artifacts ["verify.py", "verification.txt"].',{'kind':'agent','role':'Engineer','provider_id':provider['id'],'max_steps':8},320,-30)
    literature=add('Primary Sources and Closely Related Methods','literature','Search Crossref for probability calibration and class imbalance papers; label sources by the extent of access.',{'kind':'literature','query':'probability calibration class imbalance','limit':5},320,200)
    theory=add('Inverse Class-Weight Transformation','theory','Verify the inverse of q=wp/(wp+1-p) analytically and numerically within its valid domain.',{'kind':'command','command':[sys.executable,'-c','import sympy as s,json; from pathlib import Path; p,w=s.symbols("p w", positive=True); q=w*p/(w*p+1-p); inverse=s.simplify(q/(w+(1-w)*q)); result={"expression":str(inverse),"equals_original":s.simplify(inverse-p)==0,"scope":"p in (0,1), w>0; population weighted-risk identity"}; Path("theory_check.json").write_text(json.dumps(result)); print(json.dumps(result))']},660,100)
    experiment=add('Strong Baselines, Correction, and Mechanism Ablations','experiment','Protocol v2: full Adult and Bank Marketing datasets; a shared, untouched fixed test set; select C within training data only; 5 seeds; retain per-observation predictions for every method.',{'kind':'experiment','example':'class_weight_calibration','protocol_version':2,'seeds':[7,19,43,71,101],'bootstrap_samples':1000,'timeout':1800},990,120)
    analysis=add('Paired Recalculation and Uncertainty','analysis','Cluster bootstrap by observation identity; independently compute Brier score and log loss from predictions and report each task separately.',{'kind':'analysis','bootstrap_samples':1000},1320,120)
    paper=add('Evidence-Linked Figures and Paper PDF','paper','Build the strongest scoped mechanism claim from measured results and primary sources; retain strong baseline comparisons.',{'kind':'paper_generate','example':'class_weight_calibration','template':'iclr2027'},1650,120)
    for a,b in [(goal,smoke),(goal,literature),(literature,theory),(theory,experiment),(experiment,analysis),(analysis,paper)]: edge(a,b)
    print('PROJECT',pid,flush=True)
    marker=Path(__file__).resolve().parents[1]/'var'/'demo.json'; marker.write_text(json.dumps({'project_id':pid,'url':BASE+f'/projects/{pid}/workspace','nodes':{n['type']:n['id'] for n in (goal,smoke,literature,theory,experiment,analysis,paper)}},indent=2))
    for n in (goal,literature,smoke,theory,experiment,analysis,paper):
        print('Running',n['title'],flush=True); result=wait(request('POST',f'/api/nodes/{n["id"]}/run',json={'request_id':'demo:'+n['id'],'scope':'single'})); print('Completed',n['title'],result['id'],flush=True)
    print('DEMO READY',BASE+f'/projects/{pid}/workspace',flush=True)
def resume():
    marker=Path(__file__).resolve().parents[1]/'var'/'demo.json'
    state=json.loads(marker.read_text()); pid=state['project_id']
    for kind in ('goal','literature','implementation','theory','experiment','analysis','paper'):
        nid=state['nodes'][kind]
        existing=[r for r in request('GET',f'/api/projects/{pid}/runs') if r['node_id']==nid]
        if any(r['status']=='completed' for r in existing): continue
        active=next((r for r in existing if r['status'] in ('queued','running')),None)
        print('Running',kind,flush=True)
        value=wait(active or request('POST',f'/api/nodes/{nid}/run',json={'request_id':'demo-resume:'+str(time.time_ns()),'scope':'single'}))
        print('Completed',kind,value['id'],flush=True)
    print('DEMO READY',BASE+f'/projects/{pid}/workspace',flush=True)
if __name__=='__main__':
    marker=Path(__file__).resolve().parents[1]/'var'/'demo.json'
    if marker.exists() and '--new' not in sys.argv: resume()
    else: main()
