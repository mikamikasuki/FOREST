"""Independent check of final saved observations and manuscript bindings."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
paths=json.loads((ROOT/'var/qa/final-paths.json').read_text());pid=paths['project_id'];project=ROOT/'var/projects'/pid
original=project/'runs'/paths['experiment_id'];repeat=json.loads((ROOT/'var/qa/reproduction-run.json').read_text());rep=project/repeat['output_path']
a=pd.read_csv(original/'predictions.csv',float_precision='round_trip'); b=pd.read_csv(rep/'predictions.csv',float_precision='round_trip')
assert a.equals(b),'Repeated predictions differ'
metrics=json.loads((original/'metrics.json').read_text());table={(r['dataset'],r['method']):r for r in metrics['summary']}
measurements=[]
for (dataset,method),rows in a.groupby(['dataset','method']):
    p=rows.probability.to_numpy();y=rows.y_true.to_numpy(); measured=float(np.sum((p-y)**2)/len(y)); reported=table[dataset,method]['brier'];assert abs(measured-reported)<1e-12,(dataset,method,measured,reported)
    measurements.append({'dataset':dataset,'method':method,'brier':measured,'saved_brier':reported,'n':len(rows)})
for name in a.dataset.unique():
    splits=[np.load(p) for p in original.glob(f'split_{name}_*.npz')];test=set(splits[0]['test'].tolist())
    assert all(set(s['test'].tolist())==test and not(test & set(s['train'].tolist())) and not(test & set(s['calibration'].tolist())) for s in splits)
paper=Path(paths['paper_dir']);bindings=json.loads((paper/'bindings.json').read_text()); bound=json.loads((paper/'metrics.json').read_text())
for binding in bindings:
    value=bound
    for part in binding['pointer'].strip('/').split('/'): value=value[int(part)] if isinstance(value,list) else value[part]
    assert value==binding['value'],binding['macro']
reader=PdfReader(paths['pdf']);pdftext='\n'.join(p.extract_text() for p in reader.pages)
assert 'Research draft' in pdftext and 'ICLR 2027' in pdftext
assert 'under double-blind review' not in pdftext
result={'status':'passed','protocol_version':2,'prediction_rows':len(a),'exact_reproduction':True,'maximum_probability_difference':float(np.max(abs(a.probability-b.probability))),'independent_brier_rows':measurements,'fixed_test_partition_checked':True,'paper_numeric_bindings_checked':len(bindings),'pdf_pages':len(reader.pages),'checks':'Saved outputs compared by exact values; independent NumPy Brier computation; fixed test partition; LaTeX macro source bindings. Timings excluded.'}
(ROOT/'var/qa/research-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='independent_brier_rows'},indent=2))
