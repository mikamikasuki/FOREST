"""Autoresearch-style measured trial comparison without discarding editable work."""
import math

def metric_value(metrics,path):
    value=metrics
    for part in path.strip('/').replace('/','.').split('.'):
        value=value[int(part)] if isinstance(value,list) else value[part]
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('Objective must resolve to a finite measured number')
    return float(value)

def compare_trials(runs,objective):
    if not objective or not objective.get('metric'):return []
    direction=objective.get('direction','min')
    if direction not in ('min','max'):raise ValueError('Objective direction must be min or max')
    best={};rows=[]
    for run in sorted(runs,key=lambda r:r.get('created_at','')):
        if run.get('kind') not in ('experiment','command','agent'):continue
        row={'run_id':run['id'],'status':run['status'],'metric':objective['metric'],'value':None,'disposition':'unmeasured'}
        if run['status']!='completed':
            row['disposition']='failed' if run['status'] in ('failed','interrupted') else 'pending';rows.append(row);continue
        try:value=metric_value(run.get('metrics',{}),objective['metric'])
        except (KeyError,ValueError,IndexError,TypeError):rows.append(row);continue
        conditions={k:run.get('config',{}).get(k,run.get('metrics',{}).get(k)) for k in objective.get('comparison_fields',['dataset','protocol_version'])}
        key=repr(sorted(conditions.items()));previous=best.get(key)
        improved=previous is None or (value<previous['value'] if direction=='min' else value>previous['value'])
        row.update(value=value,conditions=conditions,disposition='baseline' if previous is None else 'improved' if improved else 'no_improvement',comparator_run_id=previous['run_id'] if previous else None,evidence_label='MEASURED')
        if improved:best[key]=row
        rows.append(row)
    return rows
