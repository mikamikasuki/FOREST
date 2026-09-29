"""Actual read observations and command outputs survive read-loop recovery.

These tests exercise filesystem and process behavior without a model call.
No provider completion is substituted or claimed.
"""
import copy
import json
import sys
import time
from types import SimpleNamespace

from research.agents.defaults import VERSION_TWO_TOOLS, default_config, upgrade_default_tools
from research.agents.policy import ROLES, TOOLS
from research.agents.progress import prepare_progress_recovery, restore_read_progress, task_progress, track_read_progress
from research.agents.provider import responses_input
from research.agents.runtime import ToolRuntime, progress_feedback, session_messages


def test_exact_version_two_factory_migration_preserves_custom_choices():
    agent = SimpleNamespace(role='Reviewer', name='Reviewer', instructions=ROLES['Reviewer'],
                            provider_id=None, enabled=True, tools=list(VERSION_TWO_TOOLS),
                            config={'builtin_role': 'Reviewer', 'builtin_toolset_version': 2})
    custom = copy.deepcopy(agent)
    custom.config['tools_customized'] = True
    assert not upgrade_default_tools(custom)
    assert custom.tools == VERSION_TWO_TOOLS
    different = copy.deepcopy(agent)
    different.tools.remove('python')
    assert not upgrade_default_tools(different)
    assert 'write_file_chunk' not in different.tools
    assert upgrade_default_tools(agent)
    assert agent.tools == TOOLS and agent.config == default_config('Reviewer')
    assert not upgrade_default_tools(agent)


def test_redundant_read_detection_ignores_limits_but_respects_changed_evidence(tmp_path):
    tool = ToolRuntime('actual-file-observations', tmp_path)
    (tmp_path / 'a.txt').write_text('observed a')
    (tmp_path / 'b.txt').write_text('observed b')
    state = {'transcript': []}
    for step, path in enumerate(['a.txt', 'b.txt', 'a.txt', 'b.txt', 'a.txt'], 1):
        args = {'path': path, 'offset': 0, 'limit': step * 100}
        result = tool.dispatch('read_file', args, None)
        feedback = progress_feedback(state, 'read_file', args, result)
        state['transcript'].append({'step': step, 'executed_action': {'tool': 'read_file', 'arguments': args}, 'tool_result': result})
    assert state['progress_monitor']['redundant_read_streak'] == 3
    assert feedback.startswith('PROGRESS CHECK')
    # An old saved session reconstructs the same detector from actual receipts.
    restored = {'transcript': copy.deepcopy(state['transcript'])}
    restore_read_progress(restored)
    assert restored['progress_monitor']['redundant_read_streak'] == 3
    snapshot = task_progress(restored, tmp_path, ['checks.py', 'metrics.json'], 'Implement and execute checks.py.', tool.processes)
    assert prepare_progress_recovery(restored, snapshot, {}, tool.allowed)
    assert restored['progress_recovery']['target_path'] == 'checks.py'
    assert not prepare_progress_recovery(restored, snapshot, {}, tool.allowed)
    assert restored['progress_recovery']['interventions'] == 1
    (tmp_path / 'a.txt').write_text('changed external evidence')
    result = tool.dispatch('read_file', {'path': 'a.txt'}, None)
    progress_feedback(state, 'read_file', {'path': 'a.txt'}, result)
    assert state['progress_monitor']['redundant_read_streak'] == 0
    track_read_progress(state['progress_monitor'], 'write_file_chunk', {'path': 'checks.py'}, {'exit_code': 0})
    assert state['progress_monitor']['read_evidence'] == []
    for missing in (['metrics.json'], ['paper.pdf'], ['image.png']):
        other = {'transcript': [], 'progress_monitor': {'read_evidence': [], 'redundant_read_streak': 5}}
        assert not prepare_progress_recovery(other, {'missing_outputs': missing}, {}, tool.allowed)


def test_actual_process_summary_and_task_survive_large_read_compaction(tmp_path):
    tool = ToolRuntime('actual-read-loop-context', tmp_path)
    (tmp_path / 'source.csv').write_text('method,status,value\na,completed,1\nb,failed,2\na,completed,3\n')
    (tmp_path / 'summarize.py').write_text('import csv,json\nfrom collections import Counter\nrows=list(csv.DictReader(open("source.csv")))\nprint(json.dumps({"columns":list(rows[0]),"rows":len(rows),"status_counts":dict(Counter(r["status"] for r in rows))}))\n')
    process = tool.dispatch('start_process', {'command': [sys.executable, 'summarize.py']}, None)
    process_id = process['process_id']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        receipt = tool.processes.inspect(process_id)
        if receipt['status'] in ('completed', 'failed', 'lost', 'cancelled'):
            break
        time.sleep(.03)
    assert receipt['status'] == 'completed' and receipt['exit_code'] == 0
    output = tool.processes.read_output(process_id)
    assert json.loads(output['content'])['status_counts'] == {'completed': 2, 'failed': 1}
    state = {'transcript': [{'step': 1, 'executed_action': {'tool': 'read_process_output', 'arguments': {'process_id': process_id}}, 'tool_result': output}],
             'messages': [{'role': 'system', 'content': 'controls ' * 300},
                          {'role': 'user', 'content': 'Implement audit.py using observed CSV values. ' + 'static material ' * 300}]}
    for index in range(2, 12):
        path = 'design.txt' if index % 2 else 'source.txt'
        if not (tmp_path / path).exists():
            (tmp_path / path).write_text(path + '\n' + 'long but unchanged source text ' * 150)
        arguments = {'path': path, 'offset': 0, 'limit': 50000 + index}
        result = tool.dispatch('read_file', arguments, None)
        call_id = f'actual-read-{index}'
        state['transcript'].append({'step': index, 'executed_action': {'tool': 'read_file', 'arguments': arguments}, 'tool_result': result})
        # Persisted correlation records for actual dispatched reads, not model responses.
        state['messages'].extend([{'role': 'assistant', 'content': '', 'native_output': [{'type': 'function_call', 'call_id': call_id, 'name': 'read_file', 'arguments': json.dumps(arguments)}]},
                                  {'role': 'user', 'native_call_id': call_id, 'content': json.dumps(result)}])
    original = copy.deepcopy(state['transcript'])
    restore_read_progress(state)
    state['task_progress'] = task_progress(state, tmp_path, ['audit.py', 'computed.json'], 'Implement audit.py; execute it and inspect computed.json.', tool.processes)
    assert prepare_progress_recovery(state, state['task_progress'], {}, tool.allowed)
    messages = session_messages(state, tmp_path, 18000)
    assert sum(len(json.dumps(message, ensure_ascii=False)) for message in messages) <= 18000
    assert messages[-1]['content'].startswith('CURRENT TASK AND OBSERVED PROGRESS')
    assert 'write_file_chunk' in messages[-1]['content'] and 'audit.py' in messages[-1]['content']
    assert 'status_counts' in messages[-1]['content'] and 'completed' in messages[-1]['content']
    assert state['task_progress']['observed_process_outputs'][0]['step'] == 1
    assert state['task_progress']['process_receipts'][0]['exit_code'] == 0
    assert state['transcript'] == original
    native = responses_input(messages)
    assert {item['call_id'] for item in native if item.get('type') == 'function_call'} == {item['call_id'] for item in native if item.get('type') == 'function_call_output'}
