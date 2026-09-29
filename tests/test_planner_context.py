"""Read-only acceptance against an explicitly selected real research project."""
import json
import os
from copy import deepcopy

import pytest

PROJECT=os.environ.get('FOREST_PLANNER_CONTEXT_PROJECT')
pytestmark=pytest.mark.skipif(not PROJECT,reason='Set FOREST_PLANNER_CONTEXT_PROJECT to an existing real large project for read-only acceptance')


def test_real_large_graph_prompt_is_bounded_and_retains_current_focus():
    from research.planning.loop import planning_context,compact_planning_context
    context=planning_context(PROJECT)
    assert len(context['graph']['nodes'])>=200
    # A user-selected existing node stays visible even when it is old.
    current=context['graph']['nodes'][0]['id']
    context['project']['controller']={**context['project'].get('controller',{}),'current_node':current}
    view=compact_planning_context(context,24000)
    assert len(json.dumps(view,ensure_ascii=False,default=str))<=24000
    assert current in {node['id'] for node in view['graph']['nodes']}
    assert view['graph']['revision']==context['graph']['revision']
    assert view['project']['goal']==context['project']['goal']
    assert view['context_coverage']['total']['nodes']==len(context['graph']['nodes'])
    assert view['context_coverage']['omitted']['nodes']>0
    assert view['graph_summary']['execution_status_counts']


def test_display_window_preserves_real_full_history_comparisons():
    from sqlalchemy import select
    from services.api.db import Session,TaskRun
    from research.planning.loop import planning_context,compact_planning_context
    from research.planning.scoreboard import compare_trials
    with Session() as session:
        rows=[{'id':r.id,'kind':r.kind,'status':r.status,'metrics':r.metrics,'config':r.config,'created_at':r.created_at} for r in session.scalars(select(TaskRun).where(TaskRun.project_id==PROJECT).order_by(TaskRun.created_at))]
    assert len(rows)>40
    objective={'metric':'log_loss','direction':'min','comparison_fields':['dataset','regularization']}
    trials=compare_trials(rows,objective)
    context=planning_context(PROJECT)
    assert len(context['runs'])==len(rows)
    context['project']['objective']=objective
    context['trials']=trials
    view=compact_planning_context(context,24000)
    assert len(json.dumps(view,ensure_ascii=False,default=str))<=24000
    by_id={row['run_id']:row for row in trials}
    assert view['trials']
    for row in view['trials']: assert row==by_id[row['run_id']]
    for champion in view['global_best_by_conditions']:
        candidates=[row['value'] for row in trials if row.get('conditions')==champion['conditions'] and row.get('value') is not None]
        assert champion['value']==min(candidates)
        assert champion['scope']=='entire_project_history'
    assert any(row['run_id'] not in {r['id'] for r in rows[-40:]} for row in view['global_best_by_conditions'])


def test_repair_context_has_room_for_validation_details():
    from research.planning.loop import planning_context,compact_planning_context
    context=planning_context(PROJECT)
    repair={'validation_error':'Actual schema error','previous_proposal_excerpt':'x'*6000,'instruction':'Correct the rejected proposal.'}
    reserve=len(json.dumps(repair,ensure_ascii=False))+100
    repair['context']=compact_planning_context(context,24000-reserve)
    assert len(json.dumps(repair,ensure_ascii=False,default=str))<=24000
