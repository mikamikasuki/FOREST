"""Submit actual research workloads through ordinary APIs, preserving evidence."""
import argparse,json,sys,time,uuid,math
from datetime import datetime
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--nodes',type=int,default=200);p.add_argument('--seconds',type=float,default=180);p.add_argument('--url',default='http://127.0.0.1:8000');p.add_argument('--submit-only',action='store_true');p.add_argument('--resume-project');a=p.parse_args()
if a.nodes<1:p.error('--nodes must be positive')
if not math.isfinite(a.seconds) or a.seconds<0:p.error('--seconds must be nonnegative and finite')
c=httpx.Client(base_url=a.url,timeout=120)
def req(method,url,**kwargs):
 r=c.request(method,url,**kwargs);r.raise_for_status();return r.json()
def save_report(path,report):
 path.parent.mkdir(parents=True,exist_ok=True)
 temporary=path.with_name(path.name+'.'+str(uuid.uuid4())+'.tmp')
 try:
  temporary.write_text(json.dumps(report,indent=2));temporary.replace(path)
 finally:temporary.unlink(missing_ok=True)
if not a.resume_project:
 project=req('POST','/api/projects',json={'name':'Runtime Qualification · Real Digits Training','goal':'Validate real execution, task lineage and editable large research graphs using measured softmax classification runs. This is platform qualification, not a novelty claim.','mode':'manual','budget':{'allow_paid':False}});pid=project['id'];g=req('GET',f'/api/projects/{pid}/graph');b=g['branches'][0]
 req('PUT',f'/api/projects/{pid}/file',json={'path':b['workspace']+'/train_digits.py','content':(ROOT/'scripts/qualify_task.py').read_text()})
 ids=[str(uuid.uuid4()) for _ in range(a.nodes)];commands=[]
 for i,nid in enumerate(ids):
  cfg={'kind':'experiment','command':[sys.executable,'train_digits.py','--seed',str(17+i),'--epochs','8','--regularization',str(.0001*(1+i%10))], 'metrics_file':'metrics.json','experiment_duty':'alternative_explanation','resources':{'cpu':1,'memory_gb':.4},'recovery':{'mode':'checkpoint','checkpoint':'state.json','max_attempts':5}}
  if i==0 and a.seconds: cfg['command']+=['--seconds',str(a.seconds)]
  params={'id':nid,'branch_id':b['id'],'type':'experiment','title':f'Digits training · seed {17+i}','instructions':'Run real softmax training and retain measured held-out predictions. Assess execution and reproducibility, not scientific novelty.','config':cfg,'position':{'x':i%10*280,'y':i//10*160}}
  if i>0: params['parent_id']=ids[(i-1)//2]
  commands.append({'operation':'add_node','targets':[],'params':params})
 revision=g['revision']
 for offset in range(0,len(commands),200):
  result=req('POST',f'/api/projects/{pid}/graph/batch',json={'request_id':str(uuid.uuid4()),'expected_revision':revision,'commands':commands[offset:offset+200]})
  revision=result['revision']
 started=time.monotonic();launched=req('POST',f'/api/nodes/{ids[0]}/run',json={'scope':'descendants','request_id':str(uuid.uuid4())})
 report={'project_id':pid,'root_node_id':ids[0],'node_count':a.nodes,'long_training_seconds_requested':a.seconds,'status':'running','url':a.url+f'/projects/{pid}/workspace'}
 path=ROOT/'var/qa/runtime-qualification.json';save_report(path,report);print(json.dumps(report),flush=True)
 if a.submit_only:raise SystemExit()
else:
 pid=a.resume_project;path=ROOT/'var/qa/runtime-qualification.json';report=json.loads(path.read_text());started=time.monotonic()
 if report.get('project_id')!=pid:p.error('--resume-project must match the recorded project')
last=None
while True:
 try:
  observed={};offset=0
  while True:
   page=req('GET',f'/api/projects/{pid}/runs?limit=500&offset={offset}')
   observed.update((r['id'],r) for r in page)
   if len(page)<500:break
   offset+=len(page)
  runs=list(observed.values())
 except httpx.TransportError:
  time.sleep(2);continue
 counts={x:sum(r['status']==x for r in runs) for x in set(r['status'] for r in runs)}
 if counts!=last:print(json.dumps(counts),flush=True);last=counts
 if all(r['status'] in ('completed','failed','interrupted','cancelled','skipped') for r in runs):break
 time.sleep(2)
if not runs:
 report.update(status='failed',counts={},failures=['No recorded runs in the qualification project'])
 save_report(path,report);print(json.dumps(report),flush=True);raise SystemExit(1)
accuracies=[]
for r in runs:
 value=r.get('metrics',{}).get('accuracy')
 if r['status']=='completed' and isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0<=value<=1:accuracies.append(value)
lineage=req('GET',f'/api/runs/{runs[0]["id"]}/lineage');report.update(status='passed' if all(r['status']=='completed' for r in runs) and len(runs)==report['node_count'] and len(accuracies)==len(runs) else 'failed',counts=counts,monitor_elapsed_seconds=time.monotonic()-started,wall_clock_seconds=(max(datetime.fromisoformat(r['finished_at']) for r in runs)-min(datetime.fromisoformat(r['created_at']) for r in runs)).total_seconds(),longest_actual_run_seconds=max(r.get('resource',{}).get('elapsed_seconds',0) for r in runs),trace_runs=len(lineage['runs']),lineage_scope='Ancestors of one representative run; the paged run list covers the complete qualification project',measured_accuracy_range=[min(accuracies),max(accuracies)] if accuracies else None)
save_report(path,report);print(json.dumps(report),flush=True)
if report['status']!='passed':raise SystemExit(1)
