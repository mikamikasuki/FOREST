"""Local contract checks and real transport failures, not model qualification.

No fake provider, response transport, or successful model output is used here.
Live paid-model qualification is separate and requires explicit authorization.
"""
import json
import socket

import pytest

from research.agents.provider import ModelClient, ProviderError, configured_cost, http_error_policy, normalize_usage, parse_response, responses_input
from research.agents.schemas import SPECS, decode_tool_call, tool_definitions
from research.agents.policy import TOOLS
from research.agents.runtime import session_messages


def local_config(**config):
    return {'kind': 'openai', 'base_url': 'http://127.0.0.1:1/v1', 'model': 'explicit-model-selection', 'config': {'api': 'responses', **config}}


def test_responses_payload_has_native_schema_and_no_assumed_model_parameters():
    client = ModelClient(local_config(temperature=.2, max_output_tokens=8192))
    path, payload = client.build_request([{'role': 'system', 'content': 'Use actual evidence'}], tools=tool_definitions(['read_file', 'finish']))
    assert path == '/responses'
    assert payload['model'] == 'explicit-model-selection'
    assert payload['max_output_tokens'] == 8192
    assert payload['store'] is False and payload['tool_choice'] == 'required'
    assert payload['parallel_tool_calls'] is False
    assert 'temperature' not in payload and 'reasoning' not in payload
    assert payload['include'] == ['reasoning.encrypted_content']
    assert len(payload['tools']) == 2
    assert payload['tools'][0]['strict'] is True


def test_model_parameters_are_explicit_and_cannot_replace_input_or_tools():
    client = ModelClient(local_config(model_parameters={'reasoning': {'effort': 'high'}, 'text': {'verbosity': 'low'}}))
    _, payload = client.build_request([{'role': 'user', 'content': 'Return JSON'}])
    assert payload['reasoning'] == {'effort': 'high'}
    assert payload['text'] == {'verbosity': 'low', 'format': {'type': 'json_object'}}
    for parameters in ({'input': []}, {'tools': []}, {'text': {'format': {'type': 'text'}}}):
        with pytest.raises(ProviderError, match='model_parameters'):
            ModelClient(local_config(model_parameters=parameters)).build_request([])


def test_local_ollama_and_explicit_compatible_chat_payloads_remain_available():
    provider = local_config(api='chat_completions')
    path, payload = ModelClient(provider).build_request([{'role': 'user', 'content': 'Return JSON'}])
    assert path == '/chat/completions' and payload['response_format'] == {'type': 'json_object'}
    provider['kind'] = 'ollama'
    path, payload = ModelClient(provider).build_request([{'role': 'user', 'content': 'Return JSON'}])
    assert path == '/api/chat' and payload['format'] == 'json' and payload['stream'] is False


def test_every_enabled_native_tool_uses_a_closed_strict_schema():
    assert set(SPECS) == set(TOOLS)
    for tool in tool_definitions():
        schema = tool['parameters']
        assert tool['strict'] is True
        assert schema['additionalProperties'] is False
        assert set(schema['required']) == set(schema['properties'])
    with pytest.raises(ValueError, match='no native schema'):
        tool_definitions(['not-a-real-tool'])


def test_paper_compile_native_scope_and_explicit_workspace_paths():
    empty = {key: None for key in SPECS['paper_compile'][1]}
    call = {'name': 'paper_compile', 'call_id': 'compile-scope-contract', 'arguments': empty}
    assert decode_tool_call(call, ['paper_compile'])['arguments'] == {}
    explicit = {**empty, 'source_scope': 'workspace', 'source_path': 'draft/paper.tex',
                'asset_paths': ['draft/figures', 'draft/style.sty']}
    decoded = decode_tool_call({**call, 'arguments': explicit}, ['paper_compile'])
    assert decoded['arguments'] == {key: value for key, value in explicit.items() if value is not None}
    with pytest.raises(ValueError):
        decode_tool_call({**call, 'arguments': {**explicit, 'source_scope': 'another-project'}}, ['paper_compile'])


def test_native_argument_validation_and_dynamic_records():
    call = {'name': 'read_file', 'call_id': 'contract-check', 'arguments': {'path': 'actual.txt', 'offset': None, 'limit': None}}
    assert decode_tool_call(call, ['read_file']) == {'tool': 'read_file', 'arguments': {'path': 'actual.txt'}, 'call_id': 'contract-check'}
    with pytest.raises(ValueError, match='not enabled'):
        decode_tool_call(call, ['finish'])
    with pytest.raises(ValueError, match='unknown fields'):
        decode_tool_call({**call, 'arguments': {'path': 'actual.txt', 'undeclared': True}}, ['read_file'])
    with pytest.raises(ValueError):
        decode_tool_call({**call, 'arguments': {'path': 'actual.txt', 'offset': -1, 'limit': None}}, ['read_file'])
    update = {'name': 'update_memory', 'call_id': 'contract-check', 'arguments': {'arguments_json': '{"next_experiment":"inspect raw evidence"}'}}
    assert decode_tool_call(update, ['update_memory'])['arguments'] == {'next_experiment': 'inspect raw evidence'}
    with pytest.raises(ValueError, match='must encode an object'):
        decode_tool_call({**update, 'arguments': {'arguments_json': '[]'}}, ['update_memory'])


def test_cost_only_uses_explicit_rates_and_includes_cached_and_reasoning_usage():
    usage = {'input_tokens': 1_000_000, 'cached_input_tokens': 250_000, 'output_tokens': 100_000, 'reasoning_tokens': 50_000}
    pricing = {'input_per_million': 2, 'cached_input_per_million': .5, 'output_per_million': 10}
    assert configured_cost(usage, pricing) == 2.625
    assert configured_cost(usage, {}) is None
    assert configured_cost({**usage, 'input_tokens': None}, pricing) is None
    assert configured_cost({**usage, 'cached_input_tokens': None}, pricing) is None
    assert configured_cost(usage, {**pricing, 'output_per_million': float('nan')}) is None
    assert configured_cost(usage, {**pricing, 'currency': 'EUR'}) is None
    assert normalize_usage({}, 'responses', pricing)['cost'] is None


@pytest.mark.parametrize('body,code', [
    ({}, 'response_not_completed'),
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}, 'incomplete_response'),
    ({'status': 'failed', 'error': {'message': 'Do not expose server body'}}, 'response_not_completed'),
    ({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'Do not expose refusal body'}]}]}, 'refusal'),
    ({'status': 'completed', 'output': []}, 'empty_response'),
    ({'status': 'completed', 'output': [{'type': 'function_call', 'name': 'finish', 'call_id': 'bad', 'arguments': '[]'}]}, 'invalid_tool_call'),
])
def test_unusable_responses_are_rejected_without_echoing_raw_content(body, code):
    with pytest.raises(ProviderError) as caught:
        parse_response(body, 'responses', request_id='public-request-id')
    assert caught.value.code == code
    assert caught.value.request_id == 'public-request-id'
    assert 'Do not expose' not in str(caught.value)


def test_real_connection_refusal_is_bounded_and_emits_accounting_events():
    # Bind a real local port without listening: the OS rejects the connection.
    # The model transport is not replaced or mocked.
    with socket.socket() as held:
        held.bind(('127.0.0.1', 0))
        provider = local_config(max_retries=1, timeout=.2, max_output_tokens=4096)
        provider['base_url'] = f'http://127.0.0.1:{held.getsockname()[1]}/v1'
        messages = [{'role': 'user', 'content': 'Do not send to a remote model'}]
        definitions = tool_definitions()
        events = []
        def record(event):
            events.append(event)
            return len(events)
        with pytest.raises(ProviderError) as caught:
            ModelClient(provider, request_guard=record).complete(messages, tools=definitions)
    assert caught.value.code == 'transport_error' and caught.value.ambiguous is False
    assert [event['phase'] for event in events] == ['before', 'error', 'before', 'error']
    assert all(event['ambiguous'] is False for event in events if event['phase'] == 'error')
    _, actual_payload = ModelClient(provider).build_request(messages, tools=definitions)
    # Reservation accounting covers the entire real serialized request,
    # including every native schema, and the configured output ceiling.
    assert events[0]['input_bytes'] == len(json.dumps(actual_payload, ensure_ascii=False).encode('utf-8'))
    assert events[0]['input_bytes'] > len(json.dumps(definitions).encode('utf-8'))
    assert events[0]['max_output_tokens'] == 4096
    assert events[2]['input_bytes'] == events[0]['input_bytes']
    assert all('content' not in event and 'key' not in event for event in events)


def test_only_explicit_http_rejections_release_spending_reservations():
    for status in (400, 401, 403, 404, 405, 413, 415, 422, 429):
        assert http_error_policy(status)[1] is False
    for status in (301, 302, 307, 308, 408, 409, 499, 500, 502, 503, 504):
        assert http_error_policy(status)[1] is True
    assert http_error_policy(429) == (True, False)
    assert http_error_policy(503) == (True, True)
    assert http_error_policy(307) == (False, True)


def test_provider_endpoint_validation_happens_before_network():
    for url in ('https://name:secret@api.example.test/v1', 'https://api.example.test/v1?api_key=secret', 'file:///credentials'):
        with pytest.raises(ProviderError, match='without credentials'):
            ModelClient({**local_config(), 'base_url': url})
    with pytest.raises(ProviderError, match='disabled'):
        ModelClient({**local_config(), 'base_url': 'https://api.openai.com/v1'})


def test_compaction_keeps_native_items_and_their_results_together(tmp_path):
    # These are local context records to inspect, not model output fixtures.
    pairs = []
    for index in range(8):
        call_id = str(index)
        pairs.extend([
            {'role': 'assistant', 'content': '', 'native_output': [{'type': 'function_call', 'call_id': call_id}]},
            {'role': 'user', 'content': 'Observed evidence ' + 'x' * 200, 'native_call_id': call_id},
        ])
    state = {'messages': [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'goal'}] + pairs, 'transcript': []}
    compact = session_messages(state, tmp_path, 600)
    items = responses_input(compact)
    calls = {item['call_id'] for item in items if item.get('type') == 'function_call'}
    results = {item['call_id'] for item in items if item.get('type') == 'function_call_output'}
    assert calls == results and '7' in calls
    assert state['context_management']['legacy_preference'] == 600
    assert len(state['messages']) == 18
