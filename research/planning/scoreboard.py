"""Declared measured trial comparison without discarding editable work."""
import math
from research.validation.comparability import comparison_declaration, comparison_key

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
        metrics=run.get('metrics',{})
        try:value=metric_value(metrics,objective['metric'])
        except (KeyError,ValueError,IndexError,TypeError):
            try:value=metric_value(metrics.get('observed_metrics',{}),objective['metric'])
            except (KeyError,ValueError,IndexError,TypeError):rows.append(row);continue
        declaration=comparison_declaration(run,objective)
        verification=run.get('verification_status','unverified')
        numerical=run.get('numerical_verification',{})
        numerical_ready=isinstance(numerical,dict) and numerical.get('ready') is True
        required=run.get('verification_policy')=='required' or run.get('config',{}).get('verification_policy')=='required' or run.get('kind')=='agent'
        row.update(value=value,conditions=declaration['signature'],comparison_declaration=declaration,
                   comparison_eligible=declaration['complete'] and (not required or (verification=='accepted' and numerical_ready)),
                   verification_status=verification,numerical_verification=numerical,
                   evidence_label='MEASURED' if run.get('kind')!='agent' or numerical_ready else 'REPORTED')
        if not declaration['complete'] or (required and (verification!='accepted' or not numerical_ready)):
            row.update(disposition='incomparable' if declaration['conflicting_fields'] else 'unverified',
                       comparison_status='incomparable' if declaration['conflicting_fields'] else 'unverified',comparator_run_id=None)
            if objective.get('exploratory_grouping') is True:
                # An explicit tentative display group does not select a winner.
                row['exploratory_group']={key:value for key,value in declaration['signature'].items() if key!='comparison_fields'}
            rows.append(row);continue
        key=comparison_key(declaration['signature']);previous=best.get(key)
        improved=previous is None or (value<previous['value'] if direction=='min' else value>previous['value'])
        row.update(disposition='baseline' if previous is None else 'improved' if improved else 'no_improvement',
                   comparison_status='declared' if previous is None else 'directly_comparable',
                   comparator_run_id=previous['run_id'] if previous else None)
        if improved:best[key]=row
        rows.append(row)
    return rows
