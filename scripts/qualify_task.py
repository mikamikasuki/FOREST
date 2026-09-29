"""Real minibatch softmax training on the public handwritten digits dataset.

Runtime qualification workload; measured classification output is not a novelty claim.
"""
import argparse,json,os,time
from pathlib import Path
import numpy as np
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split
from research.execution.checkpoint import CheckpointStore

p=argparse.ArgumentParser();p.add_argument('--seed',type=int,default=17);p.add_argument('--epochs',type=int,default=8);p.add_argument('--seconds',type=float,default=0);p.add_argument('--regularization',type=float,default=.001);p.add_argument('--checkpoint-seconds',type=float,default=5);a=p.parse_args()
if a.checkpoint_seconds<=0:p.error('--checkpoint-seconds must be positive')
X,y=load_digits(return_X_y=True);X=X/16
train,test=train_test_split(np.arange(len(y)),test_size=.25,random_state=19,stratify=y)
rng=np.random.default_rng(a.seed);store=CheckpointStore(os.environ.get('FOREST_CHECKPOINT_PATH','state.json'));saved=store.load()
if saved:
 W=np.array(saved['weights']);b=np.array(saved['bias']);epoch=saved['epoch'];rng.bit_generator.state=saved['rng'];elapsed=saved.get('elapsed',0)
else: W=np.zeros((64,10));b=np.zeros(10);epoch=0;elapsed=0
start=time.monotonic();initial=epoch;last_checkpoint=start
while epoch<a.epochs or (a.seconds and elapsed+time.monotonic()-start<a.seconds):
 order=rng.permutation(train)
 for lo in range(0,len(order),64):
  ii=order[lo:lo+64];z=X[ii]@W+b;z-=z.max(axis=1,keepdims=True);q=np.exp(z);q/=q.sum(axis=1,keepdims=True);q[np.arange(len(ii)),y[ii]]-=1
  W-=.2*(X[ii].T@q/len(ii)+a.regularization*W);b-=.2*q.mean(axis=0)
 epoch+=1
 if time.monotonic()-last_checkpoint>=a.checkpoint_seconds or epoch==a.epochs:
  store.save({'weights':W.tolist(),'bias':b.tolist(),'epoch':epoch,'rng':rng.bit_generator.state,'elapsed':elapsed+time.monotonic()-start},progress={'epoch':epoch})
  print(json.dumps({'epoch':epoch,'elapsed':elapsed+time.monotonic()-start}),flush=True)
  last_checkpoint=time.monotonic()
z=X[test]@W+b;z-=z.max(axis=1,keepdims=True);q=np.exp(z);q/=q.sum(axis=1,keepdims=True)
metrics={'dataset':'sklearn_digits','seed':a.seed,'regularization':a.regularization,'epochs':epoch,'resumed_from_epoch':initial,'accuracy':float((q.argmax(axis=1)==y[test]).mean()),'log_loss':float(-np.log(q[np.arange(len(test)),y[test]]).mean()),'n_test':len(test),'elapsed_seconds':elapsed+time.monotonic()-start}
Path('metrics.json').write_text(json.dumps(metrics,indent=2));np.savetxt('predictions.csv',np.column_stack([test,y[test],q]),delimiter=',',header='observation_id,label,'+','.join('p'+str(i) for i in range(10)),comments='')
print(json.dumps(metrics),flush=True)
