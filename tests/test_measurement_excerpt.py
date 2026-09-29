"""Regression for a real planner failure: arrays hid the measured scalar result."""
import json
from research.planning.loop import measurement_excerpt


def test_large_actual_computation_keeps_scalar_metrics():
    values=[i*i for i in range(10001)]
    result=measurement_excerpt({'per_example':values,'count':len(values),'sum':sum(values),'checks':{'nonnegative':all(x>=0 for x in values)}})
    assert result['values']['count']==10001
    assert result['values']['sum']==333383335000
    assert result['values']['checks']['nonnegative'] is True
    assert result['values']['per_example']=={'omitted_array_length':10001}
    assert '/per_example' in result['omitted_paths']
    assert result['complete'] is False
    assert len(json.dumps(result))<3200
