"""Real editable file retrieval and local request packing, with no model calls."""
import copy
import json
from types import SimpleNamespace

import pytest

from research.agents.context_store import ContextStore, pack_context, recover_context_rejection, provider_working_preference
from research.agents.provider import ProviderError, context_window_rejection, responses_input
from research.agents.runtime import ToolRuntime, assemble_model_request, session_messages
from research.agents.output_recovery import record_provider_failure
from research.kernel import ContextBuilder


def reject_current_context(state):
    # Exercise the explicit rejection branch without invoking a provider or
    # presenting this protocol error as a real remote receipt.
    error = ProviderError('Context-window protocol case', code='context_window_exceeded')
    assert recover_context_rejection(state, error)


def runtime_controls():
    from research.agents.policy import RESEARCH_POLICY

    ending = '\nFINAL MANDATORY CONTROL: preserve strongest fair baselines and decisive contrary evidence.'
    padding = 'Additional original control.\n'
    size = 32323 - len(RESEARCH_POLICY) - len(ending)
    system = RESEARCH_POLICY + (padding * (size // len(padding) + 1))[:size] + ending
    task = json.dumps({'task': 'Apply the complete original research policy and output contract.',
                      'required_outputs': ['screening.json', 'screening.md'],
                      'constraints': {'strongest_baseline': 'Retain the strongest relevant comparator.',
                                      'counterevidence': 'Retain every decisive contrary observation.'},
                      'context': {'project': {'goal': 'Screen the declared falsifiable research question.'},
                                  'untrusted_materials': [{'id': 'source', 'text': 'Original source passage. ' * 500}]}},
                     ensure_ascii=False)
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': task}]


@pytest.mark.parametrize('char_hint,native', [(32000, True), (64000, True), (64000, False)])
def test_actual_runtime_request_keeps_32323_character_policy_before_provider_rejection(tmp_path, char_hint, native):
    # This is the same assembly entry called by run_agent immediately before
    # ModelClient.complete; no isolated direct-attempt helper or model stand-in.
    original = runtime_controls()
    assert len(original[0]['content']) == 32323
    state = {'messages': copy.deepcopy(original), 'transcript': []}
    config = {'context_char_budget': char_hint, 'agent_budget': {'cost': 0.75},
              'project_budget': {'cost': 15}}
    client = SimpleNamespace(api='responses', native_tools=native, config={'working_context_chars': 8000})
    allowed = ['read_file', 'finish', 'read_context_segment']
    before = copy.deepcopy((state['messages'], config, client.config))
    request, tools = assemble_model_request(state, tmp_path, config, client, allowed)
    assert request[0] == original[0]
    controls = json.loads(request[1]['content'])
    expected = json.loads(original[1]['content'])
    materials = expected['context'].pop('untrusted_materials')
    assert controls.pop('context')['project'] == expected.pop('context')['project']
    assert controls == expected
    assert tools == allowed
    assert state['context_management']['next_required'] is None
    assert state['context_management']['required_segments'] == []
    assert state['context_management']['mandatory_controls_delivery'] == 'full_originals'
    assert state['context_management']['input_characters'] > state['context_management']['working_target']
    assert 'context_recovery_history' not in state
    store = ContextStore(tmp_path)
    assert (store.root / (store.index['current']['original-message:1'] + '.txt')).read_text() == original[1]['content']
    assert json.loads((store.root / (store.index['current']['task-materials'] + '.txt')).read_text()) == materials
    assert responses_input(request)[0] == original[0]
    assert (state['messages'], config, client.config) == before
    # A resumed legacy session can still contain a local pending-read decision
    # despite never receiving a provider rejection. Full delivery clears it.
    store.require([store.index['current']['controls:0']])
    resumed, tools = assemble_model_request(state, tmp_path, config, client, allowed)
    assert resumed[0] == original[0] and tools == allowed
    assert ContextStore(tmp_path).pending() is None


def test_actual_runtime_request_enables_paged_controls_only_after_explicit_provider_rejection(tmp_path):
    original = runtime_controls()
    state = {'messages': copy.deepcopy(original), 'transcript': [], 'totals': {'cost': 0.125},
             'cost_complete': True}
    config = {'context_char_budget': 64000, 'agent_budget': {'cost': 0.75}}
    client = SimpleNamespace(api='responses', native_tools=True, config={})
    allowed = ['read_file', 'finish', 'read_context_segment']
    initial, tools = assemble_model_request(state, tmp_path, config, client, allowed)
    assert initial[0] == original[0] and tools == allowed
    for error in (ProviderError('Other protocol failure', code='http_error'),
                  ProviderError('Uncertain protocol outcome', code='context_window_exceeded', ambiguous=True)):
        assert not recover_context_rejection(state, error)
    assert 'context_recovery_history' not in state
    error = ProviderError('Explicit pre-generation context-window protocol rejection', code='context_window_exceeded',
                          usage={'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0,
                                 'reasoning_tokens': 0, 'cost': 0})
    record_provider_failure(state, error, 0.01, 'protocol-error-case')
    assert recover_context_rejection(state, error)
    recovered, tools = assemble_model_request(state, tmp_path, config, client, allowed)
    assert tools == ['read_context_segment']
    assert state['context_management']['next_required']
    assert state['context_management']['mandatory_controls_delivery'] == 'provider_rejection_recovery'
    assert state['context_management']['input_characters'] < sum(len(m['content']) for m in initial)
    store = ContextStore(tmp_path)
    identifier = store.index['current']['controls:0']
    assert (store.root / (identifier + '.txt')).read_text() == original[0]['content']
    pages = []
    while store.pending():
        pending = store.pending()
        page = store.read(pending['segment_id'], pending['offset'])
        pages.append(page['content'])
        request, tools = assemble_model_request(state, tmp_path, config, client, allowed)
        delivered = next(message['content'] for message in request
                         if message['content'].startswith('RETRIEVED ORIGINAL CONTEXT PAGE'))
        assert json.loads(delivered.split(': ', 1)[1])['content'] == page['content']
        store = ContextStore(tmp_path)
    assert ''.join(pages) == original[0]['content']
    assert tools == allowed
    assert state['messages'] == original
    assert state['totals']['cost'] == 0.125 and config['agent_budget']['cost'] == 0.75
    assert len(state['context_recovery_history']) == 1
    assert state['transcript'][0]['tool_executed'] is False


def test_required_original_controls_are_fully_retrievable_before_other_actions(tmp_path):
    intent = '\n'.join(f'Requirement {i}: preserve this exact text αβγ.' for i in range(5000))
    state = {'messages': [{'role': 'system', 'content': 'Use actual evidence.'},
                          {'role': 'user', 'content': intent}], 'transcript': []}
    original = copy.deepcopy(state)
    direct = session_messages(state, tmp_path, 2000)
    assert direct[:2] == state['messages']
    assert state['context_management']['next_required'] is None
    reject_current_context(state)
    session_messages(state, tmp_path, 2000)
    assert state['context_management']['next_required']
    store = ContextStore(tmp_path)
    pieces = []
    tool = ToolRuntime('local-page-read', tmp_path, allowed=[])
    assert tool.allowed == ['read_context_segment']
    while store.pending():
        required = store.pending()
        receipt = tool.dispatch('read_context_segment', {**required, 'limit': 3700}, None)
        assert receipt['authority'] == 'user'
        pieces.append(receipt['content'])
        store = ContextStore(tmp_path)
    assert ''.join(pieces) == intent
    session_messages(state, tmp_path, 2000)
    assert state['context_management']['next_required'] is None
    assert state['messages'] == original['messages']
    # A real user edit creates a fresh retained original and fresh required read.
    state['messages'][1]['content'] += '\nAdditional required result: independent verification.'
    session_messages(state, tmp_path, 2000)
    assert state['context_management']['next_required']['offset'] == 0
    with pytest.raises(ValueError, match='Unknown context segment'):
        tool.dispatch('read_context_segment', {'segment_id': '../../outside'}, None)


def test_large_actual_tool_output_and_native_exchange_are_retained_in_full(tmp_path):
    source = ''.join(f'{i},actual observed row\n' for i in range(15000))
    (tmp_path / 'large.csv').write_text(source)
    tool = ToolRuntime('local-large-read', tmp_path)
    observed = tool.dispatch('read_file', {'path': 'large.csv', 'limit': 1_000_000}, None)
    native = [{'type': 'reasoning', 'encrypted_content': 'opaque-wire-item' * 10000},
              {'type': 'function_call', 'name': 'read_file', 'call_id': 'actual-read', 'arguments': '{"path":"large.csv"}'}]
    state = {'messages': [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'Inspect rows.'},
                          {'role': 'assistant', 'content': '', 'native_output': native},
                          {'role': 'user', 'content': source[:24000], 'native_call_id': 'actual-read'}],
             'transcript': [{'step': 1, 'executed_action': {'tool': 'read_file', 'arguments': {'path': 'large.csv'}}, 'tool_result': observed}]}
    before = copy.deepcopy(state)
    packed = session_messages(state, tmp_path, 48000)
    wire = responses_input(packed)
    assert not any(item.get('type') in ('reasoning', 'function_call', 'function_call_output') for item in wire)
    assert state['messages'] == before['messages'] and state['transcript'] == before['transcript']
    store = ContextStore(tmp_path)
    identifier = store.index['current']['turn:1']
    offset, pages = 0, []
    while True:
        page = store.read(identifier, offset, 7000)
        pages.append(page['content'])
        offset = page['next_offset']
        if page['complete']:
            break
    assert json.loads(''.join(pages))['tool_result']['content'] == source
    public = store.read(state['context_management']['latest_public_exchange'], limit=32000)
    assert 'encrypted_content' not in public['content']
    assert 'opaque-wire-item' not in public['content']


def test_context_preview_never_rejects_required_goal_and_retains_all_materials(tmp_path):
    goal = 'A required scientific goal. ' * 2000
    material = 'Original observation ' * 2000
    graph = {'project_id': 'local', 'revision': 1, 'goal': goal,
             'branches': [{'id': 'main', 'name': 'Main'}],
             'nodes': [{'id': 'n', 'branch_id': 'main', 'type': 'task', 'title': 'Task', 'config': {}}], 'edges': []}
    packet = ContextBuilder(graph, tmp_path).build('n', overrides={'max_chars': 512, 'materials': [{'id': 'actual', 'text': material}]})
    assert packet['controls']['goal'] == goal
    assert packet['capacity']['used_content_chars'] > packet['capacity']['max_chars']
    assert packet['capacity']['used_content_chars'] == len(packet['text'])
    assert packet['capacity']['used_content_chars'] <= packet['capacity']['effective_preview_chars']
    assert packet['capacity']['required_control_chars'] > packet['capacity']['max_chars']
    assert next(item['text'] for item in packet['retrievable_materials'] if item['id'] == 'actual') == material
    assert packet['capacity']['soft_preview_hint']


@pytest.mark.parametrize('preview_hint,requirements', [(512, 100), (24000, 1200), (64000, 3500)])
def test_whole_task_and_output_contract_survive_small_preview_and_request_paging(tmp_path, preview_hint, requirements):
    from research.agents.runtime import model_task_message

    goal = 'Measure the real experimental mechanism under the declared protocol.'
    instructions = '\n'.join(f'Requirement {i}: retain actual failures and report measured values αβγ.'
                             for i in range(requirements))
    result_schema = {'type': 'object', 'required': [f'metric_{i}' for i in range(100)],
                     'properties': {f'metric_{i}': {'type': 'number'} for i in range(100)}}
    config = {'instructions': instructions, 'required_outputs': ['result.json', 'report.md'],
              'metrics_file': 'result.json', 'metrics_required_keys': result_schema['required'],
              'constraints': {'result_schema': result_schema}}
    graph = {'goal': goal, 'branches': [{'id': 'main', 'workspace': 'workspace'}],
             'nodes': [{'id': 'task', 'branch_id': 'main', 'instructions': instructions, 'config': config}], 'edges': []}
    packet = ContextBuilder(graph, tmp_path).build('task', overrides={'max_chars': preview_hint})
    assert packet['capacity']['required_control_chars'] > preview_hint
    assert packet['capacity']['used_content_chars'] <= packet['capacity']['effective_preview_chars']
    assert packet['controls']['instructions'] == instructions
    assert packet['controls']['constraints']['result_schema'] == result_schema
    (tmp_path / 'context_packet.json').write_text(json.dumps(packet, ensure_ascii=False))
    state = {'messages': [{'role': 'system', 'content': 'Use actual evidence.'}, model_task_message(packet, config)],
             'transcript': []}
    direct = session_messages(state, tmp_path, preview_hint)
    assert json.loads(direct[1]['content'])['task'] == instructions
    assert state['context_management']['next_required'] is None
    reject_current_context(state)
    session_messages(state, tmp_path, preview_hint)
    store = ContextStore(tmp_path)
    identifier = store.index['current']['controls:1']
    pages, offset = [], 0
    while True:
        receipt = store.read(identifier, offset)
        assert receipt['authority'] == 'user'
        pages.append(receipt['content'])
        offset = receipt['next_offset']
        if receipt['complete']:
            break
    delivered = json.loads(''.join(pages))
    assert delivered['task'] == instructions
    assert delivered['context']['controls']['goal'] == goal
    assert delivered['context']['controls']['constraints']['result_schema'] == result_schema
    assert delivered['required_outputs'] == config['required_outputs']
    assert delivered['metrics_required_keys'] == result_schema['required']
    session_messages(state, tmp_path, preview_hint)
    assert state['context_management']['next_required'] is None


def test_only_explicit_context_rejections_allow_bounded_smaller_requests(tmp_path):
    assert context_window_rejection({'error': {'code': 'context_length_exceeded'}}, 400)
    for value, status in [({'error': {'code': 'invalid_request_error'}}, 400),
                          ({'error': {'code': 'context_length_exceeded'}}, 500), ({'error': 'context too long'}, 400)]:
        assert not context_window_rejection(value, status)
    state = {'messages': [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'required task ' * 10000}],
             'transcript': [], 'totals': {'cost': 0.12}, 'cost_complete': True}
    error = ProviderError('Explicit context rejection', code='context_window_exceeded', request_id='retained-rejection',
                          usage={'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0, 'reasoning_tokens': 0, 'cost': 0})
    targets = []
    for _ in range(3):
        session_messages(state, tmp_path, 64000)
        record_provider_failure(state, error, 0.01, 'unchanged-model')
        if not recover_context_rejection(state, error):
            break
        targets.append(state['context_management']['repack_target'])
    assert targets == sorted(set(targets), reverse=True)
    assert not recover_context_rejection(state, ProviderError('Unknown outcome', code='context_window_exceeded', ambiguous=True))
    assert not recover_context_rejection(state, ProviderError('Different failure', code='http_error'))
    assert state['totals']['cost'] == 0.12
    assert all(turn['tool_executed'] is False for turn in state['transcript'])


def test_provider_working_estimate_respects_configured_local_window():
    client = SimpleNamespace(api='ollama', config={'context_length': 8192, 'max_output_tokens': 2048})
    assert provider_working_preference(client, 64000) < 64000
    client = SimpleNamespace(api='responses', config={})
    assert provider_working_preference(client, 64000) == 64000


def test_required_page_is_delivered_even_when_its_native_exchange_is_rebased(tmp_path):
    task = 'Original required objective. ' * 2000
    state = {'messages': [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': task}], 'transcript': []}
    pack_context(state, tmp_path, 8000)
    reject_current_context(state)
    pack_context(state, tmp_path, 8000)
    store = ContextStore(tmp_path)
    page = store.read(store.pending()['segment_id'], limit=6000)
    state['messages'].extend([
        {'role': 'assistant', 'content': '', 'native_output': [
            {'type': 'reasoning', 'encrypted_content': 'opaque' * 10000},
            {'type': 'function_call', 'call_id': 'read-original-page'}]},
        {'role': 'user', 'content': json.dumps(page), 'native_call_id': 'read-original-page'}])
    packed = pack_context(state, tmp_path, 8000)
    delivered = next(message for message in packed if message.get('content', '').startswith('RETRIEVED ORIGINAL CONTEXT PAGE'))
    assert page['content'] in delivered['content']
    assert not any(message.get('native_output') or message.get('native_call_id') for message in packed)


def test_provider_repacking_pages_withheld_controls_again_instead_of_losing_tail(tmp_path):
    state = {'messages': [{'role': 'system', 'content': 'policy'},
                          {'role': 'user', 'content': 'Required original text. ' * 4000}], 'transcript': []}
    pack_context(state, tmp_path, 64000)
    reject_current_context(state)
    pack_context(state, tmp_path, 64000)
    store = ContextStore(tmp_path)
    identifier = store.pending()['segment_id']
    page = store.read(identifier)
    assert page['next_offset'] == 6000
    state['context_management']['repack_target'] = 8000
    packed = pack_context(state, tmp_path, 64000)
    pending = ContextStore(tmp_path).pending()
    assert pending['offset'] == 2000
    assert state['context_management']['next_required'] == pending
    delivered = next(message['content'] for message in packed if message['content'].startswith('RETRIEVED ORIGINAL CONTEXT PAGE'))
    assert json.loads(delivered.split(': ', 1)[1])['next_offset'] == 2000
