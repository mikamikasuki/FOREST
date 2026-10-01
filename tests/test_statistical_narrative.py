"""Semantic-review protocol tests use a test double, without live judgments."""
from copy import deepcopy
import json

import pytest

from research.paper.statistical_narrative import review_statistical_explanations


class Reviewer:
    def __init__(self, result=None, failure=None):
        self.result = result or {'verdict': 'pass', 'edits': [], 'remaining_issues': []}
        self.failure = failure
        self.calls = []

    def complete(self, messages):
        self.calls.append(deepcopy(messages))
        if self.failure:
            raise self.failure
        return {'text': json.dumps(self.result), 'model': 'actual-review-model',
                'usage': {'input_tokens': 731, 'output_tokens': 118, 'cost': .017},
                'request_id': 'review-request', 'response_id': 'review-response', 'elapsed': .8}


@pytest.fixture
def context():
    records = [{'id': 'record-a', 'dataset': 'dataset-a', 'method': 'ours', 'metric': 'accuracy',
                'condition': 'fixed-budget', 'x': None, 'mean': .81, 'n': 5, 'ci_low': .78,
                'ci_high': .84, 'uncertainty': {'kind': 'ci', 'unit': 'seed'},
                'refs': {'mean': 'mean-a', 'n': 'n-a', 'ci_low': 'lower-a', 'ci_high': 'upper-a'},
                'source_locations': [{'run_id': 'run-a', 'pointer': '/accuracy'}]},
               {'id': 'record-b', 'dataset': 'dataset-b', 'method': 'baseline', 'metric': 'accuracy',
                'condition': 'fixed-budget', 'x': None, 'mean': .73, 'n': 5,
                'refs': {'mean': 'mean-b', 'n': 'n-b'},
                'source_locations': [{'run_id': 'run-b', 'pointer': '/accuracy'}]}]
    metrics = []
    for record in records:
        for field, reference in record['refs'].items():
            metrics.append({'id': reference, 'value': record[field],
                            'statistical_identity': {key: record[key] for key in ('dataset', 'method', 'metric', 'condition', 'x')}})
    evidence = {'statistics': {'records': records, 'comparisons': [], 'analysis_plan': {'sampling_unit': 'seed'}},
                'metrics': metrics, 'figures': [
                    {'id': 'curve-b', 'path': '/unchanged/curve-b.pdf',
                     'render_metadata': {'selected_record_ids': ['record-b'], 'kind': 'bar'},
                     'caption_context': {'record_ids': ['record-b'], 'unit': '%', 'uncertainty': 'none'}}],
                'runs': [{'id': 'run-a', 'method_context': {'method': 'ours', 'budget': 'fixed'}},
                         {'id': 'run-b', 'method_context': {'method': 'baseline', 'budget': 'fixed'}}],
                'sources': [{'id': 'source-method', 'text': 'The fixed-budget objective is accurate classification.'}],
                'claims': [{'text': 'A budget-constrained accuracy comparison.'}]}
    table = {'type': 'table', 'label': 'tab:main', 'anchor': {'after': 'result-a'},
             'caption': 'Unfortunately, Table [[ref:tab:main]] summarizes fixed-budget accuracy.',
             'columns': ['Method', 'Accuracy ↑'], 'rows': [['Ours', '[[metric:mean-a]]']],
             'notes': ['Seed-level 95% intervals; n is independent training runs.'],
             'statistics_binding': {'record_ids': ['record-a'], 'identities': [{'dataset': 'dataset-a', 'method': 'ours'}],
                                    'displayed_uncertainty': ['ci'], 'bold_policy': 'best_point_estimate'},
             'statistical_spec': {'kind': 'matrix', 'datasets': ['dataset-a']}}
    draft = {'title': 'Budget-aware prediction',
             'abstract': 'Unfortunately, fixed-budget accuracy is [[metric:mean-a]] on dataset-a. The evidence defines the operating condition.',
             'conclusion': 'Unfortunately, the fixed-budget finding is supported by [[metric:mean-a]]. This is the intended operating condition.',
             'sections': [{'title': 'Method', 'role': 'method',
                           'blocks': [{'type': 'paragraph', 'id': 'method', 'text': 'The method uses a fixed training budget.'}]},
                          {'title': 'Results', 'role': 'results', 'blocks': [
                              {'type': 'paragraph', 'id': 'result-a',
                               'text': 'Table [[ref:tab:main]] gives fixed-budget accuracy. Unfortunately, accuracy reaches [[metric:mean-a]] on dataset-a.'},
                              table,
                              {'type': 'paragraph', 'id': 'following-a',
                               'text': 'The result supports accurate prediction within the stated budget.'},
                              {'type': 'paragraph', 'id': 'unrelated', 'text': 'This independent paragraph describes the implementation.'},
                              {'type': 'paragraph', 'id': 'result-b',
                               'text': 'Figure [[ref:fig:curve-b]] reports dataset-b accuracy [[metric:mean-b]].'},
                              {'type': 'figure', 'label': 'fig:curve-b', 'figure_id': 'curve-b',
                               'anchor': {'after': 'result-b'}, 'caption': 'Fixed-budget accuracy on dataset-b.'}]}]}
    return evidence, draft


def edit(*, section=1, block=0, field='text', before='Unfortunately, ', after='', refs=None):
    return {'section_index': section, 'block_index': block, 'field': field,
            'before': before, 'after': after, 'reason': 'State the actual conditional finding directly.',
            'evidence_refs': refs or ['mean-a']}


def revise(edits):
    return Reviewer({'verdict': 'revise', 'edits': edits, 'remaining_issues': []})


def test_without_statistics_or_typed_visuals_copies_without_provider(context):
    evidence, draft = context
    reviewer = Reviewer()
    for ev, dr in (({}, draft), (evidence, {'sections': [{'title': 'Results', 'paragraphs': ['Plain discussion.']}]})):
        result, report = review_statistical_explanations(reviewer, ev, dr)
        assert result == dr and result is not dr
        assert report['status'] == 'not_applicable' and not report['actual_model_call']
    assert reviewer.calls == []
    # Irrelevant, unfinished statistics are not validated when no typed visual is being discussed.
    result, report = review_statistical_explanations(None, {'statistics': {'records': [{'id': None}]}}, {'sections': []})
    assert result == {'sections': []} and report['status'] == 'not_applicable'


def test_one_actual_batch_contains_identity_uncertainty_and_true_neighbors(context):
    evidence, draft = context
    reviewer = Reviewer()
    revised, report = review_statistical_explanations(reviewer, evidence, draft)
    assert revised == draft and revised is not draft
    assert len(reviewer.calls) == 1
    payload = json.loads(reviewer.calls[0][1]['content'])
    assert payload['full_statistics'] == evidence['statistics']
    assert payload['metric_bindings'] == evidence['metrics']
    assert payload['method_context'][0]['method_context']['method'] == 'ours'
    assert payload['source_context'] == evidence['sources']
    assert payload['statistical_visuals'][0]['visual']['notes'] == draft['sections'][1]['blocks'][1]['notes']
    assert payload['statistical_visuals'][1]['actual_figure_context'][0]['path'] == '/unchanged/curve-b.pdf'
    targets = {(t['section_index'], t['block_index'], t['field']): t for t in payload['targets']}
    assert (1, 0, 'text') in targets and (1, 2, 'text') in targets
    assert (1, 3, 'text') not in targets and (0, 0, 'text') not in targets
    assert targets[1, 0, 'text']['available_evidence_refs'] == ['lower-a', 'mean-a', 'n-a', 'upper-a']
    assert targets[1, 4, 'text']['available_evidence_refs'] == ['mean-b', 'n-b']
    assert (None, None, 'abstract') in targets and (None, None, 'conclusion') in targets
    assert report['status'] == 'passed' and report['actual_model_call']
    assert report['usage'] == {'input_tokens': 731, 'output_tokens': 118, 'cost': .017}
    assert report['model'] == 'actual-review-model' and report['response_id'] == 'review-response'


def test_minimal_edits_preserve_every_numeric_and_visual_field(context):
    evidence, draft = context
    original = deepcopy(draft)
    edits = [edit(), edit(block=1, field='caption'),
             edit(section=None, block=None, field='abstract'),
             edit(section=None, block=None, field='conclusion')]
    reviewer = revise(edits)
    revised, report = review_statistical_explanations(reviewer, evidence, draft)
    expected = deepcopy(original)
    expected['sections'][1]['blocks'][0]['text'] = expected['sections'][1]['blocks'][0]['text'].replace('Unfortunately, ', '')
    expected['sections'][1]['blocks'][1]['caption'] = expected['sections'][1]['blocks'][1]['caption'].replace('Unfortunately, ', '')
    expected['abstract'] = expected['abstract'].replace('Unfortunately, ', '')
    expected['conclusion'] = expected['conclusion'].replace('Unfortunately, ', '')
    assert revised == expected and draft == original
    assert report['edits'] == edits and report['status'] == 'revised'
    assert revised['sections'][1]['blocks'][1]['statistics_binding'] == original['sections'][1]['blocks'][1]['statistics_binding']


def test_legacy_appendix_locations_and_real_authored_indices(context):
    evidence, draft = context
    table = deepcopy(draft['sections'][1]['blocks'][1])
    table['anchor'] = {'after': 'paragraph-0'}
    table['label'] = 'tab:appendix'
    table['caption'] = 'Dataset-a accuracy.'
    draft['appendices'] = [{'title': 'Detailed results', 'role': 'appendix',
                            'paragraphs': ['Table [[ref:tab:appendix]] reports the result. Unfortunately, it uses the same fixed budget.'],
                            'blocks': [table]}]
    revised, _ = review_statistical_explanations(revise([edit(section=2, block=0)]), evidence, draft)
    assert revised['appendices'][0]['paragraphs'][0].endswith('it uses the same fixed budget.')
    assert 'Unfortunately' not in revised['appendices'][0]['paragraphs'][0]
    assert revised['appendices'][0]['blocks'] == draft['appendices'][0]['blocks']


@pytest.mark.parametrize('mutation,match', [
    ({'block_index': 3}, 'outside'),
    ({'section_index': True}, 'outside'),
    ({'field': 'notes'}, 'outside'),
    ({'evidence_refs': ['mean-b']}, 'identity scope'),
    ({'evidence_refs': []}, 'identity scope'),
    ({'before': 'not in the paragraph'}, 'exactly once'),
    ({'reason': ''}, 'evidence-grounded'),
    ({'after': '\n'}, 'paragraph structure'),
    ({'before': '[[metric:mean-a]]', 'after': '[[metric:mean-b]]'}, 'needs_draft_repair'),
    ({'after': 'Accuracy is 92%. '}, 'numerical assertions'),
    ({'after': '[[metric:mean-a]] '}, 'needs_draft_repair'),
])
def test_rejects_out_of_scope_unbound_or_nonlocal_edits(context, mutation, match):
    evidence, draft = context
    proposed = edit()
    proposed.update(mutation)
    with pytest.raises(ValueError, match=match):
        review_statistical_explanations(revise([proposed]), evidence, draft)


@pytest.mark.parametrize('field,section,block', [('text', 1, 0), ('abstract', None, None), ('conclusion', None, None)])
def test_whole_paragraph_and_root_rewrites_are_rejected(context, field, section, block):
    evidence, draft = context
    before = draft[field] if section is None else draft['sections'][section]['blocks'][block][field]
    proposed = edit(section=section, block=block, field=field, before=before,
                    after=before.replace('Unfortunately, ', ''))
    with pytest.raises(ValueError, match='entire paragraph'):
        review_statistical_explanations(revise([proposed]), evidence, draft)


def test_duplicate_and_overlapping_spans_rejected(context):
    evidence, draft = context
    draft['sections'][1]['blocks'][0]['text'] += ' aaaa'
    with pytest.raises(ValueError, match='exactly once'):
        review_statistical_explanations(revise([edit(before='aaa', after='aa')]), evidence, draft)
    with pytest.raises(ValueError, match='overlap'):
        review_statistical_explanations(revise([edit(), edit(before='Unfortunately, accuracy', after='Accuracy')]), evidence, draft)


def test_whole_single_paragraph_in_multiline_abstract_is_rejected(context):
    evidence, draft = context
    draft['abstract'] += '\n\nThe second paragraph describes the application.'
    before = draft['abstract'].split('\n\n')[0].rstrip('.')
    with pytest.raises(ValueError, match='entire paragraph'):
        review_statistical_explanations(revise([edit(section=None, block=None, field='abstract',
                                                   before=before, after=before.replace('Unfortunately, ', ''))]), evidence, draft)


def test_existing_protocol_numbers_and_token_identity_are_retained(context):
    evidence, draft = context
    draft['sections'][1]['blocks'][0]['text'] += ' The ImageNet-1K protocol uses setting 2.'
    revised, _ = review_statistical_explanations(revise([edit()]), evidence, draft)
    assert revised['sections'][1]['blocks'][0]['text'].endswith('The ImageNet-1K protocol uses setting 2.')
    assert '[[metric:mean-a]]' in revised['sections'][1]['blocks'][0]['text']


@pytest.mark.parametrize('result,match', [
    ({'verdict': 'needs_draft_repair', 'edits': [], 'remaining_issues': ['Wrong method token.']}, 'Wrong method token'),
    ({'verdict': 'pass', 'edits': [], 'remaining_issues': ['Unsupported mechanism.']}, 'Unsupported mechanism'),
    ({'verdict': 'failed', 'edits': [], 'remaining_issues': []}, 'Unsupported reviewer verdict'),
    ({'verdict': 'pass', 'edits': [edit()], 'remaining_issues': []}, 'disagrees'),
    ({'verdict': 'revise', 'edits': [], 'remaining_issues': []}, 'disagrees'),
    ({'verdict': 'pass', 'edits': [], 'remaining_issues': [], 'extra': True}, 'verdict, edits'),
])
def test_review_protocol_failures_do_not_become_pass(context, result, match):
    with pytest.raises(ValueError, match=match):
        review_statistical_explanations(Reviewer(result), *context)


def test_provider_failures_and_unrecorded_response_do_not_become_pass(context):
    with pytest.raises(RuntimeError, match='provider unavailable'):
        review_statistical_explanations(Reviewer(failure=RuntimeError('provider unavailable')), *context)
    with pytest.raises(ValueError, match='actual semantic-review'):
        review_statistical_explanations(None, *context)
    class Unrecorded:
        def complete(self, messages):
            return {'text': '{"verdict":"pass","edits":[],"remaining_issues":[]}'}
    with pytest.raises(ValueError, match='actual provider text, model and usage'):
        review_statistical_explanations(Unrecorded(), *context)


def test_identity_or_binding_mismatch_fails_before_provider(context):
    evidence, draft = context
    reviewer = Reviewer()
    evidence['metrics'][0]['statistical_identity']['method'] = 'baseline'
    with pytest.raises(ValueError, match='wrong scientific identity'):
        review_statistical_explanations(reviewer, evidence, draft)
    assert reviewer.calls == []
    evidence['metrics'][0]['statistical_identity']['method'] = 'ours'
    evidence['metrics'][0]['value'] = .99
    with pytest.raises(ValueError, match='differ from their computed record'):
        review_statistical_explanations(reviewer, evidence, draft)
    assert reviewer.calls == []


def test_caption_context_fallback_and_multiple_panels_keep_complete_scope(context):
    evidence, draft = context
    evidence['figures'][0].pop('render_metadata')
    evidence['figures'].append({'id': 'curve-a', 'caption_context': {'record_ids': ['record-a']}})
    figure = draft['sections'][1]['blocks'][5]
    figure.pop('figure_id')
    figure['panels'] = [{'figure_id': 'curve-a', 'caption': 'Dataset-a.'}, {'figure_id': 'curve-b', 'caption': 'Dataset-b.'}]
    reviewer = Reviewer()
    _, report = review_statistical_explanations(reviewer, evidence, draft)
    payload = json.loads(reviewer.calls[0][1]['content'])
    target = next(target for target in payload['targets'] if target['section_index'] == 1 and target['block_index'] == 5)
    assert set(target['record_ids']) == {'record-a', 'record-b'}
    assert set(target['available_evidence_refs']) == {'mean-a', 'n-a', 'lower-a', 'upper-a', 'mean-b', 'n-b'}
    assert report['reviewed_visuals'] == 2
