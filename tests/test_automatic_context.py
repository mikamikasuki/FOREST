"""Real editable file retrieval and local request packing, with no model calls."""
import copy
import json
from types import SimpleNamespace

import pytest

from research.agents.context_store import ContextStore, pack_context, recover_context_rejection, provider_working_preference
from research.agents.provider import ProviderError, context_window_rejection, responses_input
from research.agents.runtime import ToolRuntime, session_messages
from research.agents.output_recovery import record_provider_failure
from research.kernel import ContextBuilder


def test_required_original_controls_are_fully_retrievable_before_other_actions(tmp_path):
    intent = '\n'.join(f'Requirement {i}: preserve this exact text αβγ.' for i in range(5000))
    state = {'messages': [{'role': 'system', 'content': 'Use actual evidence.'},
                          {'role': 'user', 'content': intent}], 'transcript': []}
    original = copy.deepcopy(state)
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
    assert next(item['text'] for item in packet['retrievable_materials'] if item['id'] == 'actual') == material
    assert packet['capacity']['soft_preview_hint']


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
