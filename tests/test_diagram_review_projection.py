"""Real arithmetic and complete binding projection; no provider substitutes."""
from copy import deepcopy
import json

from research.figures.workflow import diagram_review_material


def test_review_receives_actual_caption_aliases_and_statistical_definitions(tmp_path):
    from research.figures.workflow import review_requests
    companion={'caption_text':'Run R1 supplies the observed integer total. Variation across reused splits is descriptive.',
        'run_aliases':{'R1':'integer-run'},'binding_aliases':{'B1':{'parent_pointer':'/rows/0'}},
        'display_rounding':'Exact integer totals; no rounding.'}
    path=tmp_path/'figure_caption_context.json'
    path.write_text(json.dumps(companion))
    candidates=[{'id':name,'outputs':{'caption_context':str(path)},'report':{},'style':{}}
                for name in ('compact','print')]
    jobs=review_requests({'kind':'method','candidates':candidates})
    assert len(jobs)==3
    for job in jobs:
        assert json.loads(job['prompt'])['actual_caption_contexts']=={name:companion for name in ('compact','print')}


def test_conceptual_review_retains_every_bound_metric_and_parent_identity():
    rows=[{'dataset':'integer operands','method':'sum','value':sum(range(n))} for n in (100,200)]
    graph={'nodes':[{'id':'sum','label':'Integer sum'}],'edges':[],
           'storyboard':{'mechanism':{'operations':[{'evidence_refs':['context:method']}],
                        'example':{'metric_refs':['total']}},
                        'consequence':{'text':'Observed total [[metric:total]].','evidence_refs':['total']}},
           'story_context':{'method':'Accumulate actual integer operands.',
               'metrics':[{'id':'total','run_id':'arithmetic','pointer':'/rows/0/value','value':rows[0]['value']},
                          {'id':'unused','run_id':'arithmetic','pointer':'/rows/1/value','value':rows[1]['value']}],
               'source_runs':[{'id':'arithmetic','metrics':{'rows':rows}}]}}
    original=deepcopy(graph)
    material=diagram_review_material(graph)
    assert graph==original
    assert set(material['actual_bound_evidence_catalog'])=={'total','context:method'}
    assert material['actual_example_parent_records']==[
        {'run_id':'arithmetic','pointer':'/rows/0','actual_record':rows[0]}]
    assert material['storyboard']==graph['storyboard']
    assert material['nodes']==graph['nodes'] and material['edges']==graph['edges']
    assert graph['story_context']['metrics'][1]['id']=='unused'


def test_story_presentation_repair_preserves_original_science_and_bindings():
    from test_visual_narrative import actual_story,graph_data
    from research.figures.model_workflow import apply_story_presentation
    context,story=actual_story();data=graph_data(context,story);original=deepcopy(data)
    revised=apply_story_presentation(data,{'display':{'problem':'A single total hides accumulation behavior.'},
        'operations':[{'id':'compare','display_transform':'integer total minus compensated total'}],
        'edges':[{'source':'sum','target':'compare','display_label':'Integer total'}]})
    assert data==original
    assert revised['story_context']==original['story_context']
    for key in ('central_message','problem','consequence','inputs','outputs','boundary_conditions','baseline'):
        assert revised['storyboard'][key]==original['storyboard'][key]
    for before,after in zip(original['nodes'],revised['nodes']):
        assert {k:v for k,v in after.items() if k!='display_transform'}==before
    assert revised['storyboard']['display']['problem']=='A single total hides accumulation behavior.'
