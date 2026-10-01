"""One durable task process. Parent worker owns scheduling and process control."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from services.api.db import *
from services.api.common import *
from services.api.config import ROOT

def local_command(command, workspace, output, config):
    """Save the actual subprocess phase independently of the whole run receipt."""
    from research.execution.repository import task_environment
    from research.execution.repository import _write_manifest
    started = datetime.now(timezone.utc).isoformat()
    receipt = {'backend':'local','command':command,'status':'running','started_at':started}
    _write_manifest(output/'execution.json',receipt)
    env={**task_environment(config.get('env',{})),'FOREST_RUN_DIR':str(output),'PYTHONPATH':str(ROOT)}
    try:
        proc=subprocess.run(command,cwd=workspace,env=env,text=True)
    except Exception:
        receipt.update(status='failed',finished_at=datetime.now(timezone.utc).isoformat())
        _write_manifest(output/'execution.json',receipt)
        raise
    receipt.update(status='completed' if proc.returncode==0 else 'failed',exit_code=proc.returncode,
                   finished_at=datetime.now(timezone.utc).isoformat())
    _write_manifest(output/'execution.json',receipt)
    return proc

def relative_paths(value,root):
    if isinstance(value,dict): return {k:relative_paths(v,root) for k,v in value.items()}
    if isinstance(value,list): return [relative_paths(v,root) for v in value]
    if isinstance(value,str) and value.startswith(str(root)): return str(Path(value).relative_to(root))
    return value

def latest_experiment(s,pid,ids=None):
    if ids is not None:
        if not isinstance(ids,list) or not ids or any(not isinstance(rid,str) or not rid for rid in ids) or len(ids)!=len(set(ids)):
            raise ValueError('Explicit evidence run_ids must be a nonempty list of unique run IDs')
        result=[get(s,TaskRun,rid) for rid in ids]
        if any(r.project_id!=pid for r in result): raise ValueError('Cross-project run reference')
        if any(r.status!='completed' for r in result): raise ValueError('Evidence must come from completed runs')
        return result
    return list(s.scalars(select(TaskRun).where(TaskRun.project_id==pid,TaskRun.kind.in_(('experiment','agent','command')),TaskRun.status=='completed').order_by(TaskRun.created_at.desc()).limit(1)))


def manuscript_figures(session, project_id, root, selected, figure_ids=None):
    """Bridge separately rendered studio figures into scientific-run evidence."""
    allowed = {run.id for run in selected}
    rows = list(session.scalars(select(Figure).where(Figure.project_id == project_id)))
    if figure_ids is not None:
        if not isinstance(figure_ids,list) or len(set(figure_ids)) != len(figure_ids):
            raise ValueError('figure_ids must be a list of unique project figure IDs')
        lookup = {row.id:row for row in rows}
        if set(figure_ids)-set(lookup): raise ValueError('A selected manuscript figure is unavailable in this project')
        rows = [lookup[identifier] for identifier in figure_ids]
    records = []
    for row in rows:
        data = row.data; outputs = data.get('outputs',{})
        linked = data.get('source_run_ids') or data.get('run_ids') or []
        if linked and not set(linked)<=allowed:
            if figure_ids is not None: raise ValueError('Selected figure uses runs outside the manuscript evidence scope')
            continue
        if row.status!='ready_for_review':
            if figure_ids is not None: raise ValueError('Render and review selected figures before generating the manuscript')
            continue
        path = next((outputs.get(key) for key in ('pdf','png','jpg','jpeg') if outputs.get(key)),None)
        if not path: continue
        asset = safe_path(root,path,True)
        artifacts = {}
        for key in ('source','data','style','report','selection','caption_context'):
            if outputs.get(key):
                original=safe_path(root,outputs[key],True)
                if original.parent==asset.parent: artifacts[key]=original.name
        records.append({'id':row.id,'run_id':linked[0] if linked else selected[0].id,
            'source_run_ids':linked or [selected[0].id],'directory':str(asset.parent),'path':asset.name,
            'caption':data.get('caption') or row.title,'purpose':data.get('purpose',data.get('kind')),
            'anchor':data.get('anchor'),'artifacts':artifacts})
    return records


def saved_paper_response(session, project_id, config, current_run_id):
    """Resolve only a named response artifact of a terminal paper-generation run."""
    import re
    origin_id, relative = config.get('saved_response_run_id'), config.get('saved_response_path')
    if not isinstance(origin_id,str) or not isinstance(relative,str):
        raise ValueError('Saved response reuse requires saved_response_run_id and saved_response_path')
    origin=get(session,TaskRun,origin_id)
    if origin.project_id!=project_id: raise ValueError('Saved response belongs to another project')
    if origin.id==current_run_id or origin.kind!='paper_generate' or origin.status not in ('completed','failed','cancelled','interrupted'):
        raise ValueError('Reuse a terminal original paper_generate run')
    parts=Path(relative).parts
    if Path(relative).is_absolute() or len(parts)!=4 or parts[:2]!=('paper','model_responses') or not re.fullmatch(r'[A-Za-z0-9-]+',parts[2]) or not re.fullmatch(r'attempt-[1-9][0-9]*\.json',parts[3]):
        raise ValueError('Select paper/model_responses/GENERATION_ID/attempt-N.json from the originating run')
    if not isinstance(config.get('run_ids'),list) or not config['run_ids'] or config['run_ids']!=origin.config.get('run_ids'):
        raise ValueError('Saved response reuse requires the original explicit ordered evidence run_ids')
    origin_root=safe_path(project_dir(project_id),origin.output_path,True)
    lexical=origin_root/relative
    if any(path.is_symlink() for path in [lexical,*list(lexical.parents)[:3]]):
        raise ValueError('Saved response artifacts must be regular files, not symlinks')
    path=safe_path(origin_root,relative,True)
    if not path.is_file(): raise ValueError('Saved response artifact is not a file')
    generation_evidence=path.parent/'input_evidence.json'
    return origin,path,generation_evidence if generation_evidence.is_file() else origin_root/'paper'/'input_evidence.json'

def model_json(config,prompt,system=''):
    from research.agents.provider import ModelClient
    from research.agents.policy import RESEARCH_POLICY
    provider=config.get('provider_snapshot')
    if not provider: raise ValueError('Connect a model provider in Settings before running this task')
    from research.planning.model import context_model_json
    client=ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False))
    return context_model_json(client,[{'role':'system','content':RESEARCH_POLICY+'\n'+system},{'role':'user','content':prompt}],
                              Path(config['_context_workspace']),char_hint=config.get('context_char_budget',64000))

def execute(run_id):
    with Session() as s:
        run=get(s,TaskRun,run_id); config=run.config; pid=run.project_id; kind=run.kind; node_id=run.node_id; branch=s.get(Branch,run.branch_id) if run.branch_id else s.scalar(select(Branch).where(Branch.project_id==pid,Branch.is_main==True)); branch_workspace=branch.workspace if branch else '.'
        output=safe_path(project_dir(pid),run.output_path); root=project_dir(pid); source_workspace=safe_path(root,branch_workspace)
    output.mkdir(parents=True,exist_ok=True); workspace=output/'workspace'; workspace.mkdir(exist_ok=True)
    # Capture editable working files at task start, excluding previous run output.
    if source_workspace!=root and config.get('execution_attempt',{}).get('number',1)==1:
        for f in source_workspace.rglob('*'):
            if f.is_file() and not f.is_symlink():
                target=workspace/f.relative_to(source_workspace); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(f,target)
    for binding in (config.get('resolved_inputs',[]) if config.get('execution_attempt',{}).get('number',1)==1 else []):
        origin=safe_path(root,binding['source_path'],True); destination=safe_path(workspace,binding['destination']); destination.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(origin,destination)
    config={k:v for k,v in config.items() if k!='_repository_source'}
    repository_source=None
    if config.get('repository') is not None or kind=='repository_clone':
        from research.execution.repository import normalize_repository,prepare_repository
        if kind not in ('repository_clone','agent','command','experiment'):
            raise ValueError('Repository sources are supported for agent, command, experiment, and repository_clone tasks')
        config={**config,'repository':normalize_repository(config.get('repository'))}
        repository_source=prepare_repository(config['repository'],workspace,output)
    # Entry points run inside this interpreter. Scrub inherited host Git auth
    # references here too, after the host-only repository preparation phase.
    from research.execution.repository import task_environment
    task_environment(config.get('env',{}))  # Reject forbidden explicit overlays.
    for variable in set(os.environ)-set(task_environment()): os.environ.pop(variable,None)
    (output/'task_config.json').write_text(json.dumps({k:v for k,v in config.items() if k!='provider_snapshot'},ensure_ascii=False,indent=2))
    config={**config,'_context_workspace':str(output/'model_context')}
    if repository_source is not None: config={**config,'_repository_source':repository_source}
    def with_repository(result):
        return {**result,'repository_source':repository_source} if repository_source is not None else result
    if kind=='repository_clone': return repository_source
    if kind=='agent':
        from research.agents.runtime import run_agent
        result=run_agent(run_id,workspace,config)
        # Completed agent files are a proposed output; explicit branch copy keeps active edits safe.
        return with_repository(result)
    if kind=='research_plan':
        from research.planning.loop import planning_context,compact_planning_context,apply_plan,PLANNER_INSTRUCTIONS
        context=planning_context(pid)
        context['full_context_artifact']=str((output/'planning_context.json').relative_to(root))
        (output/'planning_context.json').write_text(json.dumps(context,ensure_ascii=False,indent=2,default=str))
        context_budget=int(config.get('planner_context_chars',context['project'].get('planner_context_chars',24000)))
        view=compact_planning_context(context,context_budget)
        (output/'planning_context_view.json').write_text(json.dumps(view,ensure_ascii=False,indent=2,default=str))
        from research.planning.model import planning_model_json
        from research.agents.provider import ModelClient
        from research.agents.policy import RESEARCH_POLICY
        provider=config.get('provider_snapshot')
        if not provider: raise ValueError('Connect a model provider in Settings before planning')
        client=ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False))
        repair_context=None
        for repair in range(int(config.get('structured_repair_attempts',3))):
            try:
                proposal,response=planning_model_json(client,context,output/'planning_sessions',system=RESEARCH_POLICY+'\n'+PLANNER_INSTRUCTIONS,
                                                       char_hint=context_budget,repair=repair_context)
                (output/f'research_plan_{repair+1}.json').write_text(json.dumps({'proposal':proposal,'model':response['model'],'usage':response['usage']},indent=2,ensure_ascii=False))
                result=apply_plan(pid,run_id,proposal,context['graph']['revision'])
                (output/'research_plan.json').write_text(json.dumps({'proposal':proposal,'model':response['model'],'usage':response['usage'],'result':result},indent=2,ensure_ascii=False))
                return result
            except (ValueError,KeyError,TypeError) as exc:
                (output/f'plan_error_{repair+1}.txt').write_text(str(exc))
                if repair+1>=int(config.get('structured_repair_attempts',3)): raise
                repair_context={'validation_error':str(exc)[:1000],'previous_proposal_excerpt':json.dumps(locals().get('proposal'),ensure_ascii=False,default=str)[:min(6000,context_budget//4)], 'instruction':'Correct this concrete error. Return a complete valid plan with executable graph commands. Do not claim the rejected proposal was applied.'}
    if kind in ('command','experiment') and config.get('execution_backend')=='container':
        if config.get('remote'): raise ValueError('Choose container execution or remote SSH, not both')
        from runners.container import execute_container
        return with_repository(execute_container(config,workspace,output,require_metrics=(kind=='experiment')))
    if kind in ('command','experiment') and config.get('remote'):
        from runners.remote import execute_remote
        return with_repository(execute_remote(config,workspace,output,require_metrics=(kind=='experiment')))
    if kind=='command':
        command=(config.get('recovery',{}).get('resume_command') if config.get('execution_attempt',{}).get('mode')=='checkpoint' else None) or config.get('command')
        if not command: raise ValueError('Node config requires command argv or string')
        if isinstance(command,str): command=['/bin/sh','-c',command]
        # The worker owns the task deadline and terminates this executor's
        # whole process group. subprocess.run(timeout=...) kills only its
        # direct child and can leave grandchildren after a competing receipt.
        started=time.monotonic(); proc=local_command(command,workspace,output,config)
        if proc.returncode: raise RuntimeError(f'Command exited with code {proc.returncode}')
        metrics_path=safe_path(workspace,config.get('metrics_file','metrics.json'))
        return with_repository({'command_exit_code':proc.returncode,'elapsed':time.monotonic()-started,**(json.loads(metrics_path.read_text()) if metrics_path.is_file() else {})})
    if kind=='experiment':
        if config.get('command'):
            command=(config.get('recovery',{}).get('resume_command') if config.get('execution_attempt',{}).get('mode')=='checkpoint' else None) or config['command']; command=['/bin/sh','-c',command] if isinstance(command,str) else command
            # Use the same worker-owned whole-task deadline as command runs.
            proc=local_command(command,workspace,output,config)
            if proc.returncode: raise RuntimeError(f'Experiment exited {proc.returncode}')
            metrics_path=safe_path(workspace,config.get('metrics_file','metrics.json'))
            if not metrics_path.is_file(): raise ValueError('Experiment completed without required metrics.json')
            result=json.loads(metrics_path.read_text()); shutil.copy2(metrics_path,output/'metrics.json'); return with_repository(result)
        if config.get('entrypoint'):
            import importlib
            module,function=config['entrypoint'].split(':',1); sys.path.insert(0,str(workspace)); result=getattr(importlib.import_module(module),function)(config.get('parameters',{}),str(output)); return with_repository(result)
        if config.get('example')!='class_weight_calibration':
            raise ValueError('Experiment requires real command or entrypoint. Implement the research task first; no default case is substituted.')
        snapshots=output/'source'
        for source_file in ('research/experiments/case.py','research/analysis/statistics.py','research/figures/render.py','research/paper/manuscript.py','requirements.lock.txt'):
            original=ROOT/source_file; copied=snapshots/source_file; copied.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(original,copied)
        from research.experiments.case import run_case,DEFAULT_CONFIG
        scientific={k:v for k,v in config.items() if k in DEFAULT_CONFIG or k=='cache_dir'}
        stage=config.get('stage','all')
        if stage=='evaluate':
            from research.experiments.case import evaluate
            source=safe_path(root,config['source_path'],True)
            # Re-evaluation is a new run; preserve the original run's outputs.
            for pattern in ('config.json','datasets.json','training.json','environment.json','design.json','data_*.csv','split_*.npz','probabilities_*.npz'):
                for artifact in source.glob(pattern):
                    if artifact.is_file(): shutil.copy2(artifact,output/artifact.name)
            result=evaluate(output,scientific or None)
        else: result=run_case(output,scientific)
        with Session.begin() as s:
            for name,meta in json.loads((output/'datasets.json').read_text()).items():
                existing=s.scalar(select(DatasetAsset).where(DatasetAsset.project_id==pid,DatasetAsset.title==name))
                if not existing: s.add(DatasetAsset(project_id=pid,title=name,data={**meta,'path':str((output/f'data_{name}.csv').relative_to(root))},status='available'))
            s.add(Analysis(project_id=pid,title='Experiment Statistics · '+run_id[:8],data={**result,'run_ids':[run_id],'path':str((output/'metrics_summary.csv').relative_to(root))},status='ready_for_review'))
            emit(s,pid,'metric_available',{'run_id':run_id})
        return result
    if kind=='analysis' and config.get('analysis_type')=='paired':
        from research.validation.statistics import paired_csv
        data=safe_path(root,config['path'],True)
        result=paired_csv(data,unit_column=config['unit_column'],baseline_column=config['baseline_column'],candidate_column=config['candidate_column'],direction=config.get('direction','lower'),confidence=float(config.get('confidence',.95)),bootstrap_samples=int(config.get('bootstrap_samples',5000)),seed=int(config.get('seed',0)),meaningful_effect=float(config.get('meaningful_effect',0)),output=output/'metrics.json')
        with Session.begin() as s:
            item=Analysis(project_id=pid,title=config.get('title','Paired statistical analysis'),data={**result,'run_ids':config.get('run_ids',[]),'path':str(data.relative_to(root))},status='ready_for_review');s.add(item)
        return result
    if kind=='review' and config.get('review_scope')=='statistics':
        from research.validation.review import statistical_review_prompt,validate_statistical_review
        from research.planning.loop import planning_context
        result,response=model_json(config,statistical_review_prompt(planning_context(pid)))
        result=validate_statistical_review(result)
        (output/'statistical_review.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
        with Session.begin() as s:s.add(Review(project_id=pid,title='Statistical evidence review',data={**result,'run_id':run_id,'origin':'model_proposal'},status='proposed'))
        return result
    if kind=='analysis':
        from research.analysis.statistics import analyze
        with Session() as s: selected=latest_experiment(s,pid,config.get('run_ids')); paths=[safe_path(root,r.output_path,True) for r in selected]
        if not paths: raise ValueError('No completed experiment predictions are available')
        results=[]
        for i,path in enumerate(paths):
            value=analyze(path/'predictions.csv',output/f'analysis_{i}',config)
            with Session() as s:
                edited=s.scalar(select(FileRevision).where(FileRevision.project_id==pid,FileRevision.path==str((path/'predictions.csv').relative_to(root))))
            value['source_origin']=edited.origin if edited else 'executor_measurement'
            if edited and edited.origin=='user_edited': value['evidence_label']='DERIVED_FROM_USER_EDITED_DATA'
            (output/f'analysis_{i}'/'metrics.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
            results.append(value)
        result=results[0] if len(results)==1 else {'analyses':results}
        with Session.begin() as s:
            a=Analysis(project_id=pid,title=config.get('title','Independent Recalculation'),data={**result,'run_ids':[r.id for r in selected],'path':str((output/'analysis_0/metrics_summary.csv').relative_to(root))},status='ready_for_review'); s.add(a); s.flush(); emit(s,pid,'artifact_available',{'kind':'analysis','id':a.id})
        return result
    if kind in ('figure','figure_revise'):
        from research.figures.render import render_figure,revise_style
        fig=config['figure']; fid=config['figure_id']
        if kind=='figure_revise':
            try: fig={**fig,'style':revise_style(config['instruction'],fig.get('style',{}))}
            except ValueError:
                changes,response=model_json(config,json.dumps({'figure':fig,'instruction':config['instruction'],'region':config.get('region')},ensure_ascii=False),'Return JSON {"style":{...},"explanation":"..."}; change only the requested plot styling, not measurements.')
                fig={**fig,'style':{**fig.get('style',{}),**changes['style']}}
        with Session() as s: selected=latest_experiment(s,pid,fig.get('run_ids') or config.get('run_ids'))
        style={**fig.get('style',{}),'metric':fig.get('metric',fig.get('style',{}).get('metric','brier'))}
        if fig.get('kind')=='image': data={**fig.get('data',{}),'mechanism':fig.get('image_prompt') or fig.get('caption')}
        elif fig.get('kind')=='method': data=fig.get('data',{})
        elif fig.get('data'): data=fig['data']
        elif fig.get('kind')=='calibration' and selected:
            import pandas as pd
            source=safe_path(root,selected[0].output_path,True)
            predictions=pd.read_csv(source/'predictions.csv')
            dataset=style.get('dataset',sorted(predictions.dataset.unique())[0])
            seed=style.get('seed',sorted(predictions.seed.unique())[0])
            data=predictions[(predictions.dataset==dataset)&(predictions.seed==seed)].to_dict('records')
        elif selected: data=selected[0].metrics
        else: raise ValueError('Choose real completed runs or explicitly imported figure data')
        target=output/'figure'; selection=None
        custom_code=fig.get('code') and fig.get('code_origin')!='renderer'
        provider=config.get('provider_snapshot')
        if fig.get('kind')=='method' and not data.get('nodes'):
            if not provider: raise ValueError('Supply method nodes/edges or connect the Method Illustrator model')
            from research.figures.model_workflow import design_method_graph
            from research.agents.provider import ModelClient
            from research.paper.evidence import _method_context, _numbers
            client=ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False))
            data=design_method_graph(client,{'goal':config.get('project_goal'),'caption':fig.get('caption'),
                'method':data,'layout_width_in':style.get('layout_width_in',6.5),
                'source_runs':[{'id':r.id,'status':r.status,'metrics':r.metrics} for r in selected],
                'metrics':[{'id':str(r.id)+':'+pointer,'run_id':r.id,'pointer':pointer,'value':value}
                    for r in selected for pointer,value in _numbers(r.metrics)],
                'method_context':[_method_context(run.config) for run in selected],
                'narrative_mode':fig.get('narrative_mode',data.get('narrative_mode','scientific_story'))},target/'design')
        if provider:
            from research.figures.model_workflow import reviewed_render,reviewed_image,reviewed_custom_plot
            from research.agents.provider import ModelClient
            client=ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False))
            context={'goal':config.get('project_goal'),'caption':fig.get('caption'),'purpose':fig.get('purpose'),
                'source_run_ids':[r.id for r in selected],'layout_width_in':style.get('layout_width_in',6.5),
                'source_runs':[{'id':r.id,'kind':r.kind,'status':r.status,'metrics':r.metrics} for r in selected]}
            if fig.get('kind') in ('image','method'):
                from research.paper.evidence import _numbers, _method_context
                context.update(method=data,method_context=[_method_context(r.config) for r in selected],
                    narrative_mode=fig.get('narrative_mode',data.get('narrative_mode','scientific_story') if fig.get('kind')=='image' or data.get('storyboard') else 'method_only'))
                context['metrics']=[{'id':str(r.id)+':'+pointer,'run_id':r.id,'pointer':pointer,'value':value}
                    for r in selected for pointer,value in _numbers(r.metrics)]
                if data.get('storyboard'):context['storyboard']=data['storyboard']
            if fig.get('kind')=='image':
                prompt=fig.get('image_prompt') or fig.get('caption')
                if not prompt:raise ValueError('Describe the actual scientific mechanism in image_prompt')
                existing_bundle=None
                if config.get('image_candidate_run_id'):
                    with Session() as s:origin=get(s,TaskRun,config['image_candidate_run_id'])
                    if origin.project_id!=pid or origin.kind not in ('figure','figure_revise') or origin.config.get('figure_id')!=fid or origin.status not in ('completed','failed','cancelled','interrupted'):
                        raise ValueError('Reuse image candidates from a terminal rendering run of this project figure')
                    iteration=config.get('image_candidate_iteration',1)
                    if isinstance(iteration,bool) or not isinstance(iteration,int) or iteration<1:raise ValueError('Choose an actual positive image candidate iteration')
                    manifest=safe_path(root,origin.output_path+f'/figure/iterations/{iteration}/candidate_manifest.json',True)
                    existing_bundle=json.loads(manifest.read_text())
                    if existing_bundle.get('kind')!='image' or len(existing_bundle.get('candidates',[]))<2:
                        raise ValueError('The selected run has no complete actual image candidate bundle')
                    admitted_root=manifest.parent.resolve()
                    for candidate in existing_bundle['candidates']:
                        for filename in candidate.get('outputs',{}).values():
                            path=Path(filename).resolve()
                            if not path.is_relative_to(admitted_root) or not path.is_file():raise ValueError('Retained image candidate assets must stay within their original rendering iteration')
                paths,selection=reviewed_image(client,target,prompt,context=context,
                    variants=fig.get('image_variants'),attempts=int(config.get('visual_review_attempts',3)),existing_bundle=existing_bundle)
            elif custom_code:
                paths,selection=reviewed_custom_plot(client,target,data,fig['code'],style,context=context)
            else:
                paths,selection=reviewed_render(client,target,data,style,fig.get('kind','bar'),
                    context=context,attempts=int(config.get('visual_review_attempts',3)),candidates=fig.get('candidates'))
        else:
            if fig.get('kind')=='image':raise ValueError('Connect and configure an actual image-generation provider before generating illustrations')
            paths=render_figure(target,data,style,fig.get('kind','bar'))
        if custom_code and not provider:
            for ext in ('svg','png','pdf'): (target/f'figure.{ext}').unlink(missing_ok=True)
            code=target/'custom_plot.py'; code.write_text(fig['code']); proc=subprocess.run([sys.executable,str(code)],cwd=target,env={**os.environ,'MPLBACKEND':'Agg','PYTHONPATH':str(ROOT)},timeout=90)
            if proc.returncode: raise ValueError('Custom plotting code failed; inspect stdout')
            if not any((target/f'figure.{ext}').exists() for ext in ('svg','png','pdf')): raise ValueError('Plot code must save figure.svg/png/pdf in its working directory')
            paths={k:v for k,v in paths.items() if k not in ('svg','png','pdf') or Path(v).is_file()}; paths['source']=str(code)
        rel=relative_paths(paths,root)
        if fig.get('kind')=='image' and rel.get('prompt'):
            rel['source']=rel['prompt']
        with Session.begin() as s:
            f=get(s,Figure,fid)
            if f.revision==config['figure_revision']:
                reviewed=selection is not None
                f.data={**fig,'data':data,'outputs':rel,'source_run_ids':[r.id for r in selected],
                    'visual_selection':selection,'visual_review_status':'selected' if reviewed else 'awaiting_independent_reviews',
                    'code_origin':'custom' if custom_code else 'renderer',
                    'code':Path(paths['source']).read_text() if paths.get('source') and Path(paths['source']).suffix=='.py' else fig.get('code','')}
                f.revision+=1;touch_dependents(s,pid,fid)
                f.status='ready_for_review' if reviewed else 'needs_review'
            emit(s,pid,'artifact_available',{'kind':'figure','id':fid,'run_id':run_id})
        return {'outputs':rel,'figure_revision_used':config['figure_revision']}
    if kind=='paper_generate':
        from research.paper.manuscript import generate_paper,compile_paper,collect_evidence,manuscript_prompt
        from research.paper.layout import normalize_layout
        selected_layout=normalize_layout(config.get('layout'),config.get('template','article'))
        from services.api.paper_state import publish_generation
        with Session() as s: selected=latest_experiment(s,pid,config.get('run_ids'))
        if not selected: raise ValueError('A completed scientific run is required')
        for evidence_run in selected:
            if evidence_run.kind=='agent' and not any(x.get('status')=='completed' and x.get('exit_code')==0 for x in evidence_run.metrics.get('executions',[])):
                raise ValueError('Agent metric evidence requires an actual successful computation receipt: '+evidence_run.id)
        source=safe_path(root,selected[0].output_path,True); folder=output/'paper'; reuse=None
        has_saved_response='saved_response_run_id' in config or 'saved_response_path' in config
        if 'draft_revision_path' in config and not has_saved_response:
            raise ValueError('draft_revision_path requires an explicitly selected saved response')
        if has_saved_response and config.get('example'):
            raise ValueError('Saved-response reuse uses the generic evidence-bound manuscript path')
        if config.get('example')=='class_weight_calibration':
            from examples.class_weight_paper import generate_example_paper
            generated=generate_example_paper(source,folder,config.get('title'),config.get('template','article'))
        else:
            with Session() as s:
                sources=[asdict(x) for x in s.scalars(select(SourcePaper).where(SourcePaper.project_id==pid))]
                claims=[{'id':x.id,**x.data} for x in s.scalars(select(ResearchClaim).where(ResearchClaim.project_id==pid))]
                figures=manuscript_figures(s,pid,root,selected,config.get('figure_ids'))
            evidence=collect_evidence(runs=[{'id':r.id,'status':r.status,'directory':str(safe_path(root,r.output_path,True)), 'metrics_file':('metrics.json' if (safe_path(root,r.output_path,True)/'metrics.json').exists() else 'workspace/'+r.config.get('metrics_file','metrics.json')), 'config':{k:v for k,v in r.config.items() if k not in ('provider_snapshot','env')}} for r in selected],sources=sources,claims=claims,required_run_ids=config.get('run_ids'),figures=figures)
            if has_saved_response:
                from research.paper.model_draft import draft_from_saved_response
                with Session() as s: origin,response_path,original_evidence=saved_paper_response(s,pid,config,run_id)
                revision_path=None
                if 'draft_revision_path' in config:
                    relative=config['draft_revision_path']
                    if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts or Path(relative).suffix.lower()!='.json':
                        raise ValueError('Draft revision must be an explicit project-relative JSON file')
                    revision_path=safe_path(root,relative,True)
                    if not revision_path.is_file(): raise ValueError('Draft revision path must identify a file')
                draft,response,reuse=draft_from_saved_response(response_path,evidence,folder,origin_run_id=origin.id,
                    origin_response_path=config['saved_response_path'],expected_type=config.get('manuscript_type'),original_evidence_path=original_evidence,
                    revision_path=revision_path,revision_project_path=config.get('draft_revision_path'),required_figure_ids=config.get('figure_ids'))
            else:
                from research.paper.model_draft import draft_with_model
                from research.agents.provider import ModelClient
                from research.agents.policy import RESEARCH_POLICY
                provider=config.get('provider_snapshot')
                if not provider: raise ValueError('Connect a real model provider before drafting a manuscript')
                client=ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False))
                # Manuscript responses need room for a full article. Preserve
                # explicitly configured limits and account every request normally.
                if not any(key in client.config for key in ('max_output_tokens','max_tokens')):
                    client.config={**client.config,'max_output_tokens':32768}
                draft,response=draft_with_model(client,evidence,config.get('instructions') or config.get('project_goal',''),folder,title=config.get('title'),attempts=int(config.get('paper_draft_attempts',3)),system=RESEARCH_POLICY,expected_type=config.get('manuscript_type','full_paper'),layout=selected_layout,publication=config.get('publication_profile'),required_figure_ids=config.get('figure_ids'))
                from research.paper.visual_review import review_placements
                draft,placement_review=review_placements(client,evidence,draft,folder/'visual_placement_reviews')
            generated=generate_paper(None,folder,config.get('title'),config.get('template','article'),evidence=evidence,draft=draft,layout=config.get('layout'))
        compiled=compile_paper(folder,repair_layout=True)
        if compiled['status']!='completed': raise RuntimeError(compiled.get('log','LaTeX compile failed'))
        with Session.begin() as s:
            data={'source':(folder/'paper.tex').read_text(),'bibtex':(folder/'references.bib').read_text(),'pdf_path':str((folder/'paper.pdf').relative_to(root)),'log':compiled['log'],'bindings':([{'run_id':r['id'],'path':str((Path(r['directory'])/r['metrics_file']).relative_to(root))} for r in evidence['runs']] if config.get('example')!='class_weight_calibration' else [{'run_id':selected[0].id,'path':str((source/'metrics.json').relative_to(root))}]),'numeric_bindings':generated['bindings'],'source_run_ids':[r.id for r in selected],'source_dir':str(folder.relative_to(root))}
            if reuse is not None: data['draft_reuse']=reuse
            from research.paper.style import writing_profile
            plan=json.loads((folder/'layout_plan.json').read_text()) if (folder/'layout_plan.json').exists() else None
            data.update(template=config.get('template','article'),layout=plan.get('config') if plan else None,
                        layout_plan=plan,layout_preflight=compiled.get('preflight'),writing_profile=writing_profile())
            publication=publish_generation(s,pid,generated.get('title',config.get('title','Research manuscript')),data,config.get('paper_snapshot'),run_id)
            for fname in ('comparison','calibration','method'):
                fdir=folder/'figures'/fname
                if fdir.exists():
                    s.add(Figure(project_id=pid,title={'comparison':'Main Results Comparison','calibration':'Calibration Reliability','method':'Method Architecture'}[fname],status='ready_for_review',data={'kind':'bar' if fname=='comparison' else fname,'run_ids':[selected[0].id],'metric':'brier','style':{},'outputs':{ext:str((fdir/f'figure.{ext}').relative_to(root)) for ext in ('svg','pdf','png') if (fdir/f'figure.{ext}').exists()},'code':(fdir/'plot.py').read_text() if (fdir/'plot.py').exists() else ''}))
            emit(s,pid,'compile_finished',{'paper_id':publication['paper_id'],'run_id':run_id,**publication})
        return {'pdf_path':str((folder/'paper.pdf').relative_to(root)),'paper_source':str((folder/'paper.tex').relative_to(root)),'actual_compilation':True,**({'draft_reuse':reuse} if reuse is not None else {}),**publication}
    if kind=='paper_compile':
        from research.paper.manuscript import compile_paper
        from services.api.paper_state import locked_paper
        if config.get('source_scope')=='layout':
            from research.paper.layout import apply_layout
            from services.api.paper_state import publish_layout
            source=safe_path(root,config['compile_input_dir'],True); folder=output/'paper'
            if source!=output/'paper_inputs': raise ValueError('Layout inputs must belong to this compilation run')
            shutil.copytree(source,folder,dirs_exist_ok=True)
            plan=apply_layout(folder,config['layout'],config['template'])
            compiled=compile_paper(folder)
            if compiled['status']!='completed': raise RuntimeError(compiled.get('log','LaTeX compile failed'))
            data={**config['paper_data'],'source':(folder/'paper.tex').read_text(),
                  'bibtex':(folder/'references.bib').read_text(),
                  'pdf_path':str((folder/'paper.pdf').relative_to(root)),'source_dir':str(folder.relative_to(root)),
                  'log':compiled['log'],'errors':compiled.get('errors',[]),
                  'template':config['template'],'layout':plan['config'],'layout_plan':plan,
                  'layout_preflight':compiled.get('preflight')}
            if (folder/'bindings.json').exists(): data['numeric_bindings']=json.loads((folder/'bindings.json').read_text())
            with Session.begin() as s:
                publication=publish_layout(s,pid,data,config,run_id)
                emit(s,pid,'compile_finished',{'paper_id':publication['paper_id'],'run_id':run_id,**publication})
            return {'pdf_path':data['pdf_path'],'paper_source':str((folder/'paper.tex').relative_to(root)),
                    'actual_compilation':True,'layout_plan':plan,'layout_preflight':compiled.get('preflight'),**publication}
        if config.get('source_scope')=='workspace':
            from services.api.paper_state import publish_generation
            source=safe_path(root,config['compile_input_dir'],True); folder=output/('paper-'+uid())
            if source!=output/'paper_inputs': raise ValueError('Workspace compilation inputs must belong to this compilation run')
            shutil.copytree(source,folder,dirs_exist_ok=True)
            generated_names={'paper.pdf','paper.aux','paper.log','paper.bbl','paper.blg','paper.out','paper.synctex.gz'}
            submitted_files={str(path.relative_to(folder)):path.read_bytes() for path in folder.rglob('*') if path.is_file() and str(path.relative_to(folder)) not in generated_names}
            compiled=compile_paper(folder)
            if compiled['status']!='completed': raise RuntimeError(compiled.get('log','LaTeX compile failed'))
            if any(not (folder/name).is_file() or (folder/name).read_bytes()!=content for name,content in submitted_files.items()):
                raise ValueError('Manuscript inputs changed during compilation; compile the edited bundle again')
            data={'source':(folder/'paper.tex').read_text(),'bibtex':(folder/'references.bib').read_text() if (folder/'references.bib').exists() else '',
                  'pdf_path':str((folder/'paper.pdf').relative_to(root)),'log':compiled['log'],
                  'source_dir':str(folder.relative_to(root)),'bindings':[],
                  'source_run_ids':[config['origin_agent_run_id']], 'source_scope':'workspace',
                  'origin_source_path':config['origin_source_path'],'compile_input_files':config['compile_input_files'],
                  'layout_preflight':compiled.get('preflight')}
            with Session.begin() as s:
                publication=publish_generation(s,pid,config.get('title') or 'Agent manuscript',data,config.get('paper_snapshot'),run_id)
                emit(s,pid,'compile_finished',{'paper_id':publication['paper_id'],'run_id':run_id,**publication})
            return {'pdf_path':data['pdf_path'],'paper_source':str((folder/'paper.tex').relative_to(root)),
                    'actual_compilation':True,'source_scope':'workspace','origin_agent_run_id':config['origin_agent_run_id'],
                    'origin_source_path':config['origin_source_path'],**publication}
        folder=output/'paper'; source=root/'paper'
        if source.exists(): shutil.copytree(source,folder,dirs_exist_ok=True)
        else: folder.mkdir(exist_ok=True)
        (folder/'paper.tex').write_text(config['source']); (folder/'references.bib').write_text(config.get('bibtex','')); compiled=compile_paper(folder)
        with Session.begin() as s:
            p=locked_paper(s,config['paper_id'])
            if p.revision==config['paper_revision']:
                p.data={**p.data,'log':compiled['log'],'errors':compiled.get('errors',[]),'layout_preflight':compiled.get('preflight'),'compiled_revision':p.revision,'pdf_path':str((folder/'paper.pdf').relative_to(root)) if compiled.get('pdf_path') else None}; p.status='ready_for_review' if compiled['status']=='completed' else 'compile_failed'
            emit(s,pid,'compile_finished',{'paper_id':p.id,'status':compiled['status'],'run_id':run_id})
        if compiled['status']!='completed': raise RuntimeError(compiled['log'][-12000:])
        return {'pdf_path':str((folder/'paper.pdf').relative_to(root)),'compiled_revision':config['paper_revision']}
    if kind=='literature':
        from research.literature.sources import search, import_identifier
        query=config.get('query') or config.get('instructions') or config.get('project_goal')
        sources=search(query,config.get('source','crossref'),int(config.get('limit',5)))
        if config.get('identifiers'):
            for identifier in config['identifiers']: sources.append(import_identifier(identifier,output/'library'))
        (output/'sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2))
        with Session.begin() as s:
            ids=[]
            for item in sources:
                existing=s.scalar(select(SourcePaper).where(SourcePaper.project_id==pid,SourcePaper.title==item['title']))
                if existing: ids.append(existing.id); continue
                item=relative_paths(item,root); paper=SourcePaper(project_id=pid,title=item['title'],data=item,status='available'); s.add(paper); s.flush(); ids.append(paper.id)
                for passage in item.get('passages',[]): s.add(SourcePassage(project_id=pid,title=paper.title,data={'paper_id':paper.id,**passage},status='available'))
            emit(s,pid,'artifact_available',{'kind':'library','ids':ids})
        return {'source_ids':ids,'source_count':len(sources),'source_path':str((output/'sources.json').relative_to(root)),'evidence_label':'REPORTED'}
    if kind=='ideas':
        from research.literature.sources import search
        query=config.get('prompt') or config.get('project_goal')
        sources=search(query,'crossref',8)
        with Session.begin() as s:
            source_ids=[]
            for item in sources:
                source=s.scalar(select(SourcePaper).where(SourcePaper.project_id==pid,SourcePaper.title==item['title']))
                if not source: source=SourcePaper(project_id=pid,title=item['title'],data=item,status='available'); s.add(source); s.flush()
                source_ids.append(source.id)
        prompt=json.dumps({'research_goal':query,'source_material_untrusted':sources,'count':min(int(config.get('count',8)),12)},ensure_ascii=False)
        ideas,response=model_json(config,prompt,'Propose distinct testable routes grounded in the supplied sources. Return JSON {"ideas":[{"title":"...","claim":"...","mechanism":"...","nearest_work":"source title","baseline":"strong fair comparator","experiment":"...","experiment_duty":"effectiveness|mechanism|scenario_value|alternative_explanation","metric":"...","best_estimate":"...","probability_range":[0,1],"confidence":"Low|Medium|High","why":"...","against":"...","decisive_unknown":"...","cheapest_resolution":"...","expected_cost":"...","base_case":{"most_likely_outcome":"...","current_best_estimate":"...","pre_experiment_bet":"...","probability_any_signal":[0,1],"probability_meaningful_improvement":[0,1],"probability_publishable_finding":[0,1],"biggest_reason_to_work":"...","biggest_reason_to_fail":"...","first_experiment":"..."}}]}. Probability ranges are subjective ESTIMATED values with reasons, not measured frequencies. Do not claim novelty from metadata. Never invent source findings or cite unseen full text. Prefer a concrete modest experiment over an unsupported promise.')
        if not isinstance(ideas.get('ideas'),list) or not ideas['ideas']: raise ValueError('Model returned no valid research ideas')
        from research.validation.protocol import validate_idea
        ideas['ideas']=[validate_idea({**item,'evidence_label':'ESTIMATED'}) for item in ideas['ideas']]
        with Session.begin() as s:
            ids=[]
            for item in ideas['ideas']:
                r=Hypothesis(project_id=pid,title=item.get('title','Candidate'),data={**item,'source_ids':source_ids,'source_titles':[x['title'] for x in sources],'source_reading_scope':'retrieved_metadata_and_available_abstracts','evidence_label':'ESTIMATED'},status='proposed'); s.add(r); s.flush(); ids.append(r.id)
            emit(s,pid,'artifact_available',{'kind':'ideas','ids':ids})
        return {'ideas':ideas['ideas'],'source_count':len(sources),'usage':response['usage']}
    if kind=='suggest_paths':
        with Session() as s: p=get(s,Project,pid); graph=graph_from_db(s,p)
        proposal,response=model_json(config,json.dumps({'prompt':config.get('prompt'),'node_id':node_id,'graph':{k:v for k,v in graph.items() if k!='_history'}},ensure_ascii=False),'Return JSON {"commands":[{"operation":"add_node|fork_branch|edit_node|add_dependency","targets":[],"params":{}}],"rationale":"...","alternatives":[{"purpose":"advance|falsify|repair","experiment":"...","stop_condition":"..."}]}. These are editable proposals, not applied commands.')
        (output/'proposal.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2))
        with Session.begin() as s:
            r=Hypothesis(project_id=pid,title='Research Path Revision',data={**proposal,'node_id':node_id},status='proposed'); s.add(r); emit(s,pid,'artifact_available',{'kind':'proposal','run_id':run_id})
        return proposal
    if kind in ('paper_revise','review'):
        from research.paper.style import writing_contract,writing_profile
        from research.paper.writing import review_defensive_writing
        payload={'instruction':config.get('instruction',config.get('prompt','Check the strongest evidence and identify concrete issues')),
                 'source':config.get('source',''),'project_goal':config.get('project_goal'),
                 'literal_findings':review_defensive_writing(config.get('source',''))['edits']}
        result,response=model_json(config,json.dumps(payload,ensure_ascii=False),writing_contract('revision')+'\nReturn JSON {"summary":"...","edits":[{"original":"exact short span","replacement":"minimal replacement","reason":"concrete evidence or writing reason","start":0}],"issues":[]}. Inspect all manuscript prose, including defensive framing beyond literal matches. An offset is the exact zero-based source character index. Report substantive unresolved scientific issues separately from wording edits.')
        from research.paper.writing import validate_revision_proposal
        result=validate_revision_proposal(config.get('source',''),result)
        result['writing_profile']=writing_profile()
        (output/'revision_proposal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        with Session.begin() as s:
            r=Review(project_id=pid,title='Revision Suggestions' if kind=='paper_revise' else 'Research Review',data={**result,'paper_id':config.get('paper_id'),'paper_revision':config.get('paper_revision'),'origin':'model_proposal'},status='proposed'); s.add(r); emit(s,pid,'artifact_available',{'kind':'review','run_id':run_id})
        return result
    raise ValueError('Unsupported task kind '+kind)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--run-id',required=True); args=parser.parse_args(); start=time.monotonic()
    with Session() as s: run=get(s,TaskRun,args.run_id); out=safe_path(project_dir(run.project_id),run.output_path)
    out.mkdir(parents=True,exist_ok=True)
    try: result=execute(args.run_id); receipt={'status':'completed','exit_code':0,'metrics':result,'elapsed_seconds':time.monotonic()-start}
    except __import__('research.agents.runtime',fromlist=['AgentYield']).AgentYield as exc:
        receipt={'status':exc.status,'wait_for':exc.wait_for,'resume_after':exc.resume_after,'reason':exc.reason,'exit_code':0,'elapsed_seconds':time.monotonic()-start}
    except __import__('research.agents.budget',fromlist=['BudgetExceeded']).BudgetExceeded as exc:
        receipt={'status':'budget_exhausted','reason':str(exc),'error':str(exc),'exit_code':0,'elapsed_seconds':time.monotonic()-start}
    except Exception as exc:
        traceback.print_exc(); receipt={'status':'failed','exit_code':1,'error':str(exc),'elapsed_seconds':time.monotonic()-start}
    receipt['attempt_id']=os.environ.get('FOREST_ATTEMPT_ID')
    tmp=out/('result.'+str(os.environ.get('FOREST_ATTEMPT_ID','initial'))+'.tmp'); tmp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str)); tmp.replace(out/'result.json')
    sys.exit(receipt['exit_code'])
if __name__=='__main__': main()
