"""Actual calculations and isolated controller/database delivery checks.

No model responses or accepted papers are substituted. Small local calculations
exercise the audit and are correctly kept incomplete as scientific submissions.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research.publication import publication_profile, assess_submission
from research.agents.policy import RESEARCH_POLICY, ROLES
from research.agents.defaults import VERSION_FOUR_TOOLS,upgrade_default_tools,TOOLSET_VERSION
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


def test_full_submission_is_default_and_budgets_never_shrink_it():
    profile=publication_profile({'budget':{'seconds':1,'cost':.01}})
    assert profile['id']=='full_submission' and profile['manuscript_type']=='full_paper'
    assert (profile['accepted_papers'],profile['datasets'],profile['baselines'],profile['seeds'],profile['ablations'])==(15,3,5,5,4)
    profile['datasets']=1
    assert publication_profile()['datasets']==3
    assert publication_profile({'publication_profile':'operational'})['id']=='operational'
    with pytest.raises(ValueError,match='twelve'):
        publication_profile({'publication_profile':{'accepted_papers':3}})
    with pytest.raises(ValueError,match='relative'):
        publication_profile({'publication_profile':{'evidence_manifest':'../other.json'}})
    with pytest.raises(ValueError,match='full_paper'):
        publication_profile({'publication_profile':{'manuscript_type':'measurement_excerpt'}})
    with pytest.raises(ValueError,match='four'):
        publication_profile({'publication_profile':{'experiment_duties':['effectiveness']}})
    assert {'Literature Scout','Benchmark Curator','Experiment Designer','Baseline Reproducer','Submission Reviewer','Visual Selector','Layout Reviewer'}<=set(ROLES)
    assert 'anti-defensive' in RESEARCH_POLICY and 'AI-writing declarations' in RESEARCH_POLICY


def test_old_factory_agents_gain_executable_publication_tools_without_changing_custom_permissions():
    old=SimpleNamespace(role='Researcher',name='Researcher',instructions=ROLES['Researcher'],enabled=True,provider_id=None,
                        tools=list(VERSION_FOUR_TOOLS),config={'builtin_role':'Researcher','builtin_toolset_version':4})
    restricted=copy.deepcopy(old);restricted.config['tools_customized']=True
    assert not upgrade_default_tools(restricted)
    assert upgrade_default_tools(old)
    assert {'literature_import','literature_read','figure_create','paper_generate'}<=set(old.tools)
    assert old.config['builtin_toolset_version']==TOOLSET_VERSION and not upgrade_default_tools(old)


def test_actual_pdf_small_caps_title_spacing_preserves_identity(tmp_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from research.literature.sources import extract_pdf
    from research.literature.agent_tools import title_in_text
    figure=plt.figure(figsize=(8,3))
    figure.text(.04,.7,'AGENT BENCH : E VALUATING LLM S AS AGENTS',fontsize=12)
    figure.text(.04,.4,'PDF typography regression; no acceptance record is substituted.',fontsize=9)
    path=tmp_path/'title-typesetting.pdf';figure.savefig(path);plt.close(figure)
    extracted=' '.join(p['text'] for p in extract_pdf(path))
    assert title_in_text('AgentBench: Evaluating LLMs as Agents',extracted)
    assert not title_in_text('AgentBench: Evaluating Language Models as Scientists',extracted)
    assert not title_in_text('DSPy: Compiling Declarative Language Model Calls into State-of-the-Art Pipelines',
                             'DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines')


@pytest.mark.parametrize('host', ['roboticsproceedings.org', 'www.roboticsproceedings.org'])
def test_rss_droid_paper_landing_url_is_eligible_but_not_an_acceptance_receipt(host):
    from research.publication.quality import _official
    # Actual DROID RSS 2024 paper landing path; this tests URL eligibility,
    # without substituting a fetched page, paper text or acceptance receipt.
    url = f'https://{host}/rss20/p120.html'
    assert _official(url, 'official_proceedings')
    assert not _official(url, 'official_decision')
    assert not _official(url, 'unverified')


@pytest.mark.parametrize('url', [
    'https://www.roboticsproceedings.org/',
    'https://www.roboticsproceedings.org/rss20/',
    'https://www.roboticsproceedings.org/rss20/index.html',
    'https://www.roboticsproceedings.org/rss20/p120.pdf',
    'https://www.roboticsproceedings.org/rss20/p120-supplement.html',
    'https://www.roboticsproceedings.org/rss20/p120.html.pdf',
    'https://www.roboticsproceedings.org/rss20/p120.html/more',
    'https://www.roboticsproceedings.org/rss20/p120.html;download.pdf',
    'https://www.roboticsproceedings.org/rss00/p120.html',
    'https://www.roboticsproceedings.org/rss20/p000.html',
    'https://www.roboticsproceedings.org/rss20/p12.html',
    'https://www.roboticsproceedings.org/rss20/p1200.html',
    'https://www.roboticsproceedings.org/not-rss20/p120.html',
    'https://www.roboticsproceedings.org.evil.example/rss20/p120.html',
    'https://evil.example/roboticsproceedings.org/rss20/p120.html',
    'https://roboticsproceedings.org@evil.example/rss20/p120.html',
    'https://user@www.roboticsproceedings.org/rss20/p120.html',
    'https://www.roboticsproceedings.org:8443/rss20/p120.html',
    'http://www.roboticsproceedings.org/rss20/p120.html',
    'https://roboticsconference.org/2024/',
    'https://droid-dataset.github.io/',
    'https://arxiv.org/abs/2403.12945',
])
def test_rss_paper_record_rejects_indexes_pdf_only_and_impostor_urls(url):
    from research.publication.quality import _official
    assert not _official(url, 'official_proceedings')


@pytest.mark.parametrize('scope,passages', [('metadata', []), ('full_text', [])])
def test_rss_url_does_not_count_a_droid_peer_without_observed_full_text(tmp_path, scope, passages):
    url = 'https://www.roboticsproceedings.org/rss20/p120.html'
    peer = {'source_id': 'droid', 'acceptance_url': url, 'acceptance_kind': 'official_proceedings'}
    source = {'id': 'droid', 'data': {'title': 'DROID: A Large-Scale In-The-Wild Robot Manipulation Dataset',
                                    'read_scope': scope, 'acceptance_url': url, 'passages': passages}}
    audit = assess_submission(tmp_path, sources=[source], manifest={'comparable_papers': [peer]})
    assert not audit['ready'] and audit['counts']['accepted_papers'] == 0
    assert any(gap['code'] == 'unverified_peer' and 'droid' in gap['finding'] for gap in audit['gaps'])


def actual_cells(tmp_path):
    workspace=tmp_path/'runs'/'actual-calculation'/'workspace'
    workspace.mkdir(parents=True)
    script='''import csv, json
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
data=load_iris()
rows=[]
for seed in range(5):
    training,test=train_test_split(range(len(data.target)),test_size=.2,random_state=seed,stratify=data.target)
    for method,model in [('logistic',LogisticRegression(max_iter=300)),('candidate',make_pipeline(StandardScaler(),LogisticRegression(max_iter=300)))]:
        model.fit(data.data[training],data.target[training])
        prediction=model.predict(data.data[test])
        for unit,prediction in zip(test,prediction):
            rows.append({'dataset':'iris','method':method,'seed':seed,'study':'main','unit_id':unit,'correct':int(prediction==data.target[unit])})
with open('predictions.csv','w',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
json.dump({'observations':len(rows)},open('metrics.json','w'))
'''
    (workspace/'fit.py').write_text(script)
    process=subprocess.run([sys.executable,'fit.py'],cwd=workspace,capture_output=True,text=True,timeout=30)
    assert process.returncode==0,process.stderr
    run={'id':'actual-calculation','status':'completed','kind':'command','output_path':'runs/actual-calculation',
         'config':{'command':[sys.executable,'fit.py']},'metrics':json.loads((workspace/'metrics.json').read_text())}
    (workspace.parent/'result.json').write_text(json.dumps({'status':'completed','exit_code':process.returncode,'metrics':run['metrics']}))
    methods=['logistic','nearest_neighbors','naive_bayes','random_forest','svm']
    ablations=['no_scale','no_feature','no_regularization','component_removed']
    manifest={'target_venue':'A user-selected conference','central_claim':'A measured methodological comparison','candidate':'candidate',
              'datasets':[{'id':name,'source':'Published dataset','split_policy':'Held-out stratified test','sample_size':30} for name in ('iris','wine','breast_cancer')],
              'baselines':[{'id':name,'source':'Primary method reference','fair_budget':'Matched data and tuning budget'} for name in methods],
              'seeds':list(range(5)),'ablations':[{'id':name,'mechanism':'Declared component removal'} for name in ablations],
              'studies':[{'id':'main','argumentative_duty':'effectiveness'},
                         {'id':'mechanism','argumentative_duty':'mechanism','datasets':['iris'],'methods':['candidate',*ablations],'scope_rationale':'This dataset exposes the mechanism'},
                         {'id':'scenario','argumentative_duty':'scenario_value','datasets':['iris'],'methods':['candidate'],'scope_rationale':'Specific deployment condition'},
                         {'id':'alternative','argumentative_duty':'alternative_explanation','datasets':['wine'],'methods':['candidate','logistic'],'scope_rationale':'Matched competing explanation'}],
              'measured_cells':[{'dataset':'iris','method':method,'seed':seed,'study':'main','run_id':run['id'],'raw_path':'predictions.csv'} for method in ('logistic','candidate') for seed in range(5)]}
    return run,manifest,workspace


def test_real_observations_count_but_pilots_never_finish_a_submission(tmp_path):
    run,manifest,workspace=actual_cells(tmp_path)
    audit=assess_submission(tmp_path,runs=[run],manifest=manifest)
    assert not audit['ready'] and audit['status']=='needs_revision'
    assert audit['counts']['measured_cells']==10
    # Full main comparisons, a targeted mechanism study, and scoped scenarios:
    # not all ablation methods multiplied across unrelated datasets/studies.
    assert audit['counts']['required_cells']==130 and audit['counts']['remaining_cells']==120
    codes={x['code'] for x in audit['gaps']}
    assert {'accepted_corpus','incomplete_matrix','independent_analysis','independent_review','compiled_manuscript'}<=codes
    assert 'insufficient_real_data' not in codes
    excerpt=copy.deepcopy(manifest)
    excerpt['manuscript']={'compile_run_id':run['id'],'source_path':'demo.tex','pdf_path':'demo.pdf','layout_report':'demo-layout.json'}
    rejected=assess_submission(tmp_path,runs=[run],manifest=excerpt)
    assert not rejected['ready']
    assert any(g['code']=='compiled_manuscript' and 'actual completed manuscript compilation' in g['finding'] for g in rejected['gaps'])
    run['status']='queued'
    assert assess_submission(tmp_path,runs=[run],manifest=manifest)['counts']['measured_cells']==0
    run['status']='completed'
    (workspace/'predictions.csv').unlink()
    assert assess_submission(tmp_path,runs=[run],manifest=manifest)['counts']['measured_cells']==0


def test_invalid_manifest_fields_remain_an_actionable_research_gap(tmp_path):
    audit=assess_submission(tmp_path,manifest={'comparable_papers':None,'measured_cells':{},'statistics':None,'manuscript':None})
    assert not audit['ready']
    assert {'invalid_comparable_papers','invalid_measured_cells','invalid_statistics'}<={g['code'] for g in audit['gaps']}


def test_actual_controller_rejects_submission_completion_and_honors_explicit_operational_scope(tmp_path):
    env={**os.environ,'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'api.db'),'FOREST_DATA_DIR':str(tmp_path/'data'),'FOREST_MODEL':'','PYTHONPATH':str(ROOT)}
    process=subprocess.run([sys.executable,__file__,'isolated'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    assert process.returncode==0,process.stdout+process.stderr


if __name__=='__main__':
    from fastapi.testclient import TestClient
    from services.api.main import app
    from services.api.db import Session,Project,TaskRun,Node,uid
    from services.api.common import project_dir
    from services.worker.scheduler import enqueue
    from services.worker.controller import advance_projects
    from research.planning.loop import apply_plan,planning_context
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'Real delivery audit','goal':'Calculate the actual sequence sum','mode':'auto'}).json()
        pid=project['id'];graph=client.get(f'/api/projects/{pid}/graph').json()
        branch=graph['branches'][0]['id']
        script="import json;from pathlib import Path;Path('metrics.json').write_text(json.dumps({'sum':sum(range(100))}))"
        body={'operation':'add_node','request_id':uid(),'expected_revision':graph['revision'],'params':{'id':'measured','branch_id':branch,'type':'implementation','title':'Actual calculation','instructions':'Execute and inspect the sum','config':{'kind':'command','command':[sys.executable,'-c',script]}}}
        response=client.post(f'/api/projects/{pid}/graph/commands',json=body);response.raise_for_status()
        with Session.begin() as session:
            p=session.get(Project,pid);node=session.get(Node,'measured')
            # A dirty ORM object must not autoflush between the scheduler's
            # SQLite transaction check and BEGIN IMMEDIATE.
            p.config={**p.config,'controller':{'status':'running','autonomous':True}}
            run=enqueue(session,pid,'command',{},node=node);rid=run.id
        executed=subprocess.run([sys.executable,'-m','services.worker.execute','--run-id',rid],cwd=ROOT,capture_output=True,text=True,timeout=20)
        assert executed.returncode==0,executed.stdout+executed.stderr
        with Session.begin() as session:
            run=session.get(TaskRun,rid)
            receipt=json.loads((project_dir(pid)/run.output_path/'result.json').read_text())
            assert receipt['metrics']['sum']==4950
            run.status='completed';run.metrics=receipt['metrics']
            session.get(Node,'measured').execution_status='completed'
            revision=session.get(Project,pid).revision
        proposed={'action':'completed','rationale':'The actual calculation completed','commands':[],'evidence_run_ids':[rid]}
        result=apply_plan(pid,'delivery-review',proposed,revision)
        assert result['status']=='needs_revision' and result['action']=='continue'
        context=planning_context(pid)
        assert context['project']['publication_profile']['id']=='full_submission'
        assert not context['project']['submission_quality']['ready']
        with Session.begin() as session:
            p=session.get(Project,pid);p.config={**p.config,'controller':{**p.config['controller'],'autonomous':False}}
        advance_projects()
        with Session() as session:assert session.get(Project,pid).config['controller']['status']=='submission_incomplete'
        response=client.patch(f'/api/projects/{pid}/publication',json={'id':'operational'});response.raise_for_status()
        with Session.begin() as session:
            p=session.get(Project,pid);p.config={**p.config,'controller':{**p.config['controller'],'status':'running'}};revision=p.revision
        result=apply_plan(pid,'operational-review',proposed,revision)
        assert result['action']=='completed'
        assert client.get(f'/api/projects/{pid}/publication').json()['status']=='not_requested'
        # The first generation request must create and flush its PaperDocument
        # and then acquire/reuse the same actual SQLite writer transaction.
        requested={'run_ids':[rid],'request_id':'first-api-generation'}
        generated_api=client.post(f'/api/papers/{pid}/generate',json=requested)
        generated_api.raise_for_status()
        assert generated_api.json()['kind']=='paper_generate' and generated_api.json()['status']=='queued'
        assert client.post(f'/api/papers/{pid}/generate',json=requested).json()['id']==generated_api.json()['id']
        from services.api.db import PaperDocument
        from sqlalchemy import select
        with Session.begin() as session:
            paper=session.scalar(select(PaperDocument).where(PaperDocument.project_id==pid))
            paper.title='Pending editable manuscript title'
            additional=enqueue(session,pid,'paper_generate',{'run_ids':[rid]},'pending-paper-enqueue')
            assert additional.kind=='paper_generate'
        with Session() as session:
            assert session.scalar(select(PaperDocument).where(PaperDocument.project_id==pid)).title=='Pending editable manuscript title'
        from research.agents.runtime import ToolRuntime
        from services.api.db import Figure
        with Session() as session:run=session.get(TaskRun,rid);workspace=project_dir(pid)/run.output_path/'workspace'
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.plot(range(100),[x*x for x in range(100)]);plt.xlabel('Sequence index');plt.ylabel('Squared value');plt.title('Actual computation import');plt.savefig(workspace/'source.pdf');plt.close()
        tools=ToolRuntime(rid,workspace)
        imported=tools.dispatch('literature_import',{'identifier':'source.pdf'},pid,'actual-local-source')
        assert imported['read_scope']=='full_text' and imported['passage_count']>0
        source_id=imported['source']['id']
        assert tools.dispatch('literature_import',{'identifier':'source.pdf'},pid,'actual-local-source')['source']['id']==source_id
        assert tools.dispatch('literature_read',{'source_id':source_id},pid)['passages']
        arguments={'title':'Actual sequence computation','kind':'method','caption':'Executable method structure','purpose':'mechanism','run_ids':[rid],
                   'data_json':json.dumps({'nodes':[{'id':'sum','label':'Sum the actual sequence'}]})}
        created=tools.dispatch('figure_create',arguments,pid,'actual-figure-spec')
        assert created['figure']['status']=='draft' and not created['figure']['data'].get('outputs')
        assert tools.dispatch('figure_create',arguments,pid,'actual-figure-spec')['figure_id']==created['figure_id']
        generated=tools.dispatch('paper_generate',{'run_ids':[rid],'instructions':'Complete a real full manuscript'},pid,'actual-paper-request')
        assert generated['status']=='queued'
        assert tools.dispatch('paper_generate',{'run_ids':[rid],'instructions':'Complete a real full manuscript'},pid,'actual-paper-request')['run_id']==generated['run_id']
        with Session() as session:assert session.get(TaskRun,generated['run_id']).config['manuscript_type']=='full_paper'
        print('Real execution, publication audit, controller iteration and explicit operational completion passed')
