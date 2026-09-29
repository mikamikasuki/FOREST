"""Local context protocol tests and actual file observations; no model transport."""
import copy
import json
from types import SimpleNamespace

from research.agents.defaults import LEGACY_TOOLS, default_config, explicit_agent_config, upgrade_default_tools
from research.agents.policy import ROLES, TOOLS
from research.agents.provider import responses_input
from research.agents.runtime import ToolRuntime, model_task_message, observed_progress, progress_feedback, session_messages


def test_task_controls_and_configuration_drop_only_exact_duplicate_instruction():
    instruction = 'Compute the requested metric from actual supplied observations.'
    packet = {'controls': {'instructions': instruction, 'goal': 'measure', 'constraints': ['retain failures']},
              'capacity': {'max_chars': 6000}, 'materials': [
                  {'kind': 'configuration', 'text': json.dumps({'instructions': instruction, 'seed': 17})},
                  {'kind': 'configuration', 'text': json.dumps({'instructions': 'Independent branch instruction'})}]}
    original = copy.deepcopy(packet)
    message = model_task_message(packet, {'instructions': instruction, 'required_outputs': ['result.json']})
    assert message['content'].count(instruction) == 1
    assert 'Independent branch instruction' in message['content'] and 'retain failures' in message['content']
    assert json.loads(json.loads(message['content'])['context']['untrusted_materials'][0]['text']) == {'seed': 17}
    assert packet == original


def test_actual_file_reads_remain_visible_across_compaction(tmp_path):
    tool = ToolRuntime('local-context-observations', tmp_path)
    transcript, messages = [], [{'role': 'system', 'content': 'policy ' * 60}, {'role': 'user', 'content': 'Analyze actual inputs.'}]
    for index, name in enumerate(['input.json', 'provenance.json', 'input.json', 'provenance.json'], 1):
        (tmp_path / name).write_text(json.dumps({'file': name, 'value': index % 2}))
        args = {'path': name}
        result = tool.dispatch('read_file', args, None)
        action = {'tool': 'read_file', 'arguments': args}
        transcript.append({'step': index, 'content': json.dumps(action), 'tool_result': result})
        messages.extend([{'role': 'assistant', 'content': json.dumps(action)}, {'role': 'user', 'content': json.dumps(result) + ' ' * 1000}])
    state = {'messages': messages, 'transcript': transcript}
    before = copy.deepcopy(state)
    compact = session_messages(state, tmp_path, 2600)
    rendered = json.dumps(compact, ensure_ascii=False)
    assert len(rendered) <= state['context_management']['working_target'] + 100
    assert state['context_management']['legacy_preference'] == 2600
    ledger = observed_progress(state)
    assert {e['arguments']['path'] for e in ledger} == {'input.json', 'provenance.json'}
    assert 'RECORDED TOOL OBSERVATIONS' in rendered
    assert state['messages'] == before['messages'] and state['transcript'] == before['transcript']


def test_native_group_retains_opaque_items_and_correlated_result(tmp_path):
    # Explicit wire-protocol input records, never a substitute provider.
    native = [{'type': 'reasoning', 'id': 'opaque-item', 'encrypted_content': 'opaque-wire-data'},
              {'type': 'function_call', 'call_id': 'call-a', 'name': 'read_file', 'arguments': '{}'}]
    state = {'messages': [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'task'},
                          {'role': 'assistant', 'content': '', 'native_output': native},
                          {'role': 'user', 'content': 'actual-text-record ' * 1000, 'native_call_id': 'call-a'}], 'transcript': []}
    original = copy.deepcopy(state)
    compact = session_messages(state, tmp_path, 32000)
    assert sum(len(json.dumps(m, ensure_ascii=False)) for m in compact) <= 32000
    output = responses_input(compact)
    assert output[0]['role'] == 'system'
    assert next(x for x in output if x.get('type') == 'reasoning') == native[0]
    assert [x['call_id'] for x in output if x.get('type') == 'function_call'] == ['call-a']
    assert [x['call_id'] for x in output if x.get('type') == 'function_call_output'] == ['call-a']
    assert state['messages'] == original['messages'] and state['transcript'] == original['transcript']


def test_read_cycle_feedback_uses_actual_unchanged_values(tmp_path):
    tool = ToolRuntime('local-progress-observations', tmp_path)
    for name in ('input.json', 'provenance.json'): (tmp_path / name).write_text('{}')
    state = {}
    actions = [('list_files', {}), ('read_file', {'path': 'input.json'}), ('read_file', {'path': 'provenance.json'})]
    feedback = []
    for name, args in actions * 2:
        feedback.append(progress_feedback(state, name, args, tool.dispatch(name, args, None)))
    assert feedback[:5] == [None] * 5
    assert 'Previously read inputs' in feedback[-1]
    # A real changed file breaks the previous cycle; polling is legitimate.
    (tmp_path / 'input.json').write_text('{"changed":true}')
    assert progress_feedback(state, 'read_file', {'path': 'input.json'}, tool.dispatch('read_file', {'path': 'input.json'}, None)) is None
    for _ in range(4):
        assert progress_feedback(state, 'inspect_process', {'process_id': 'p'}, {'status': 'running'}) is None
    assert state['progress_monitor']['read_observations'] == []


def factory(**changes):
    values = dict(name='Engineer', role='Engineer', instructions=ROLES['Engineer'],
                  tools=list(LEGACY_TOOLS), config={}, provider_id=None, enabled=True)
    return SimpleNamespace(**{**values, **changes})


def test_versioned_default_migration_and_custom_tool_protection():
    agent = factory()
    assert upgrade_default_tools(agent)
    assert agent.tools == TOOLS and agent.config == default_config('Engineer')
    assert not upgrade_default_tools(agent)
    for changes in ({'tools': ['read_file', 'finish']}, {'instructions': 'My own role'}, {'name': 'Custom'},
                    {'config': {'mine': True}}, {'config': explicit_agent_config({})}, {'enabled': False}, {'provider_id': 'custom'}):
        custom = factory(**changes)
        original = copy.deepcopy(vars(custom))
        assert not upgrade_default_tools(custom)
        assert vars(custom) == original


def test_optional_observation_ledger_cannot_crowd_out_native_exchange(tmp_path):
    tool = ToolRuntime('local-capacity-observations', tmp_path)
    (tmp_path / 'input.json').write_text('a' * 1800)
    result = tool.dispatch('read_file', {'path': 'input.json'}, None)
    transcript = [{'step': 1, 'content': json.dumps({'tool': 'read_file', 'arguments': {'path': 'input.json'}}), 'tool_result': result}]
    native = [{'type': 'reasoning', 'encrypted_content': 'opaque' * 80}, {'type': 'function_call', 'call_id': 'latest'}]
    messages = [{'role': 'system', 'content': 'policy' * 130}, {'role': 'user', 'content': 'task' * 180},
                {'role': 'user', 'content': 'older context ' * 1000},
                {'role': 'assistant', 'content': '', 'native_output': native},
                {'role': 'user', 'content': 'observed receipt' * 20, 'native_call_id': 'latest'}]
    state = {'messages': messages, 'transcript': transcript}
    compact = session_messages(state, tmp_path, 2900)
    assert sum(len(json.dumps(message, ensure_ascii=False)) for message in compact) <= state['context_management']['working_target']
    assert compact[-1] == messages[-1]
    assert compact[-2]['native_output'] == native


def test_tool_text_excerpt_is_explicit_and_does_not_mutate_observation(tmp_path):
    from research.agents.runtime import _public_excerpt
    (tmp_path / 'large.txt').write_text('actual file text ' * 4000)
    result = ToolRuntime('local-excerpt-observation', tmp_path).dispatch('read_file', {'path': 'large.txt', 'limit': 100000}, None)
    original = copy.deepcopy(result)
    excerpt = _public_excerpt(json.dumps(result), 24000)
    assert excerpt.endswith('[excerpt; full value in transcript]')
    assert result == original and len(result['content']) == 68000


def test_actual_wait_control_does_not_become_process_success_receipt(tmp_path):
    import sys
    tool = ToolRuntime('local-wait-observation', tmp_path)
    args = {'command': [sys.executable, '-c', 'import time; time.sleep(8)']}
    started = tool.dispatch('start_process', args, None)
    process_id = started['process_id']
    try:
        waiting = tool.dispatch('wait_for_process', {'process_id': process_id}, None)
        assert waiting['status'] == 'waiting' and waiting['exit_code'] == 0
        state = {'transcript': [
            {'step': 1, 'content': json.dumps({'tool': 'start_process', 'arguments': args}), 'tool_result': started},
            {'step': 2, 'content': json.dumps({'tool': 'wait_for_process', 'arguments': {'process_id': process_id}}), 'tool_result': waiting}]}
        entry = observed_progress(state)[0]
        assert entry['process_receipt']['step'] == 1
        assert entry['process_receipt']['value']['status'] == started['status']
        assert entry['process_receipt']['value'].get('exit_code') is None
        assert entry['latest_tool_observation']['status'] == 'waiting'
        assert entry['latest_tool_observation']['exit_code'] == 0
    finally:
        tool.processes.cancel(process_id)


def test_default_context_reserves_static_space_and_records_live_budget_edits():
    from research.agents.runtime import CONTEXT_POLICY_VERSION, context_char_budget, context_packet_char_budget, record_context_budget
    from research.agents.policy import RESEARCH_POLICY
    assert context_char_budget({}) == 64000
    assert context_char_budget({'context_char_budget': 24000}) == 24000
    assert context_packet_char_budget({}) == (64000-len(RESEARCH_POLICY)-6000)//2
    assert context_packet_char_budget({'context_char_budget':24000}) == (24000-len(RESEARCH_POLICY)-6000)//2
    state={'transcript':[]}
    assert record_context_budget(state,{})
    assert not record_context_budget(state,{})
    state['transcript']=[{'step':1}]
    assert record_context_budget(state,{'context_char_budget':24000})
    state['transcript'].append({'step':2})
    assert record_context_budget(state,{'context_char_budget':64000})
    history=state['context_budget_history']
    assert [entry['context_char_budget'] for entry in history] == [64000,24000,64000]
    assert [entry['before_step'] for entry in history] == [1,2,3]
    assert [entry['explicit_configuration'] for entry in history] == [False,True,True]
    assert all(entry['runtime_context_policy_version']==CONTEXT_POLICY_VERSION and entry['recorded_at']>0 for entry in history)
