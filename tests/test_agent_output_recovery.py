"""Real chunk/file/process/ledger checks and offline protocol contracts.

No provider output is substituted and no model/network success is claimed.
The transport check uses an actual OS connection refusal. Incomplete-response
objects below are parser inputs, not simulated research or model execution.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research.agents.code_writes import CODE_CHUNK_CHARS, write_file_chunk
from research.agents.output_recovery import account_usage, model_turn_tools, prepare_output_recovery, record_provider_failure
from research.agents.provider import ProviderError, parse_response, responses_input
from research.agents.runtime import budget_reason
from research.agents.schemas import decode_tool_call

ROOT = Path(__file__).resolve().parents[1]


def empty_state():
    return {'status': 'executing', 'transcript': [], 'messages': [], 'pending_action': None,
            'totals': {'cost': None}, 'cost_complete': True, 'active_seconds': 0}


def incomplete_error():
    # Values follow the API's public incomplete-response contract. Never a
    # returned model fixture: directly invoke only the parser/error handler.
    body = {'id': 'contract-response', 'model': 'contract-input', 'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'},
            'usage': {'input_tokens': 12826, 'input_tokens_details': {'cached_tokens': 10880},
                      'output_tokens': 16384, 'output_tokens_details': {'reasoning_tokens': 5238}},
            'output': [{'type': 'reasoning', 'encrypted_content': 'private-opaque-input'},
                       {'type': 'function_call', 'status': 'incomplete', 'name': 'write_file',
                        'call_id': 'contract-call', 'arguments': '{"path":"never.py","content":"unfinished'}]}
    try:
        parse_response(body, 'responses', request_id='contract-request',
                       pricing={'input_per_million': 2.5, 'cached_input_per_million': .25, 'output_per_million': 15})
    except ProviderError as error:
        return error
    raise AssertionError('An incomplete call was accepted')


def test_incomplete_usage_and_one_charged_recovery_are_retained():
    state = empty_state()
    account_usage(state, {'input_tokens': 3, 'output_tokens': 2, 'cost': .05}, 1)
    error = incomplete_error()
    turn = record_provider_failure(state, error, 223.577, 'contract-input')
    assert turn['status'] == 'incomplete' and not turn['tool_executed']
    assert turn['tool_calls'] == [] and state['pending_action'] is None
    assert state['totals']['cost'] == pytest.approx(.303345)
    assert state['totals']['output_tokens'] == 16386
    assert state['totals']['reasoning_tokens'] == 5238
    assert state['active_seconds'] == pytest.approx(224.577)
    assert 'private-opaque-input' not in json.dumps(turn)
    assert turn['incomplete_public_output'][0]['arguments'].endswith('unfinished')
    allowed = ['read_file', 'write_file', 'write_file_chunk', 'finish']
    assert prepare_output_recovery(state, error, {}, allowed)
    assert model_turn_tools(state, allowed) == ['write_file_chunk']
    assert not any(item.get('type') == 'function_call' for item in responses_input(state['messages']))
    assert budget_reason(state, {'agent_budget': {'cost': .3}})
    assert budget_reason(state, {'agent_budget': {'steps': 1}})
    assert budget_reason(state, {'agent_budget': {'active_seconds': 200}})
    resumed = json.loads(json.dumps(state))
    assert not prepare_output_recovery(resumed, error, {}, allowed)
    assert resumed['output_recovery']['used'] == 1
    assert not prepare_output_recovery(empty_state(), error, {'output_recovery_attempts': 0}, allowed)


def test_ambiguous_failure_and_unknown_usage_never_become_free_retries():
    state = empty_state()
    account_usage(state, {'input_tokens': 100, 'output_tokens': 30, 'cost': .075}, 1)
    error = ProviderError('Unknown outcome', code='transport_error', ambiguous=True)
    record_provider_failure(state, error, 120, 'contract-input')
    assert state['totals']['cost'] is None
    assert state['totals']['known_cost'] == .075
    assert state['cost_complete'] is False and state['usage_complete'] is False
    assert state['active_seconds'] == 121
    assert not prepare_output_recovery(state, error, {}, ['write_file_chunk'])
    assert 'output_recovery' not in state
    assert not prepare_output_recovery(empty_state(), incomplete_error(), {}, ['read_file'])


def test_chunk_bounds_validate_native_and_real_writes(tmp_path):
    content = 'x' * (CODE_CHUNK_CHARS + 1)
    call = {'name': 'write_file_chunk', 'call_id': 'local-contract',
            'arguments': {'path': 'source.py', 'offset': 0, 'content': content}}
    with pytest.raises(ValueError, match='pattern or size'):
        decode_tool_call(call, ['write_file_chunk'])
    with pytest.raises(ValueError, match='characters'):
        write_file_chunk(tmp_path / 'source.py', content, 0)
    assert not (tmp_path / 'source.py').exists()
    for name, arguments in [('write_file', {'path': 'source.py', 'content': content}),
                             ('python', {'path': None, 'code': content, 'cwd': None, 'env_json': None, 'timeout': None})]:
        with pytest.raises(ValueError, match='pattern or size'):
            decode_tool_call({'name': name, 'arguments': arguments, 'call_id': 'local-contract'}, [name])
    for offset in (True, -1, 0.5):
        with pytest.raises(ValueError, match='byte offset'):
            write_file_chunk(tmp_path / 'source.py', 'print(1)', offset)


@pytest.mark.parametrize('scenario', ['chunks', 'transport', 'budget'])
def test_actual_recovery_components(tmp_path, scenario):
    env = {**os.environ, 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'recovery.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'), 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, __file__, str(tmp_path), scenario], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    import socket
    import time
    from sqlalchemy import select
    from services.api.db import migrate, Session, Project, TaskRun, ToolExecution, Provider, ModelRequest, asdict, uid
    from research.agents.runtime import ToolRuntime, run_agent
    from research.agents.budget import BudgetExceeded, make_request_guard

    directory, scenario = Path(sys.argv[1]), sys.argv[2]
    migrate()
    with Session.begin() as s:
        project = Project(name='Offline recovery component validation', budget={'allow_paid': True, 'cost_usd': 10})
        s.add(project); s.flush()
        run = TaskRun(project_id=project.id, request_id=uid(), status='running', kind='agent')
        s.add(run); s.flush()
        project_id, run_id = project.id, run.id
    workspace = directory / 'workspace'; workspace.mkdir()

    if scenario == 'chunks':
        tool = ToolRuntime(run_id, workspace, ['write_file', 'read_file', 'start_process'])
        assert 'write_file_chunk' in tool.allowed
        assert 'write_file_chunk' not in ToolRuntime(run_id, workspace, ['read_file']).allowed
        first = '# UTF-8 byte offsets: π\nimport json\n'
        arguments = {'path': 'large_program.py', 'content': first, 'offset': 0}
        action_id = uid()
        # Interrupt after the real atomic file write but before a completion
        # receipt is saved. Replay the actual persisted intent, without a model.
        with Session.begin() as s:
            s.add(ToolExecution(id=action_id, run_id=run_id, tool='write_file_chunk', arguments=arguments))
        observed = tool.dispatch('write_file_chunk', arguments, project_id, action_id)
        replayed = tool.execute('write_file_chunk', arguments, action_id)
        assert replayed['replayed'] and observed['next_offset'] == len(first.encode())
        assert (workspace / 'large_program.py').read_text() == first
        offset = replayed['next_offset']
        source = first
        for index in range(150):
            chunk = f'def value_{index}():\n    return {index} * {index}\n\n'
            result = tool.execute('write_file_chunk', {'path': 'large_program.py', 'content': chunk, 'offset': offset})
            assert result['exit_code'] == 0
            offset = result['next_offset']; source += chunk
        last = "values = [" + ','.join(f'value_{index}()' for index in range(150)) + "]\nprint(json.dumps({'sum':sum(values),'count':len(values)}))\n"
        result = tool.execute('write_file_chunk', {'path': 'large_program.py', 'content': last, 'offset': offset})
        source += last
        assert len(source) > CODE_CHUNK_CHARS and (workspace / 'large_program.py').read_text() == source
        # A different intent must not append at a stale offset or overwrite a user edit.
        conflict = tool.execute('write_file_chunk', arguments)
        assert conflict['exit_code'] == 1 and (workspace / 'large_program.py').read_text() == source
        escape = tool.execute('write_file_chunk', {'path': '../escape.py', 'content': 'bad', 'offset': 0})
        assert escape['exit_code'] == 1 and not (directory / 'escape.py').exists()
        launched = tool.execute('start_process', {'command': [sys.executable, 'large_program.py']})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            actual = tool.processes.inspect(launched['process_id'])
            if actual['status'] in ('completed', 'failed', 'lost', 'cancelled'):
                break
            time.sleep(.03)
        assert actual['status'] == 'completed' and actual['exit_code'] == 0, actual
        output = json.loads(tool.processes.read_output(launched['process_id'])['content'])
        assert output == {'sum': sum(i*i for i in range(150)), 'count': 150}
        print('152 real chunks -> editable Python source -> actual interpreter result; crash replay and path/offset checks passed')

    elif scenario == 'transport':
        with socket.socket() as held:
            held.bind(('127.0.0.1', 0))
            config = {'provider_snapshot': {'kind': 'openai', 'base_url': f'http://127.0.0.1:{held.getsockname()[1]}/v1',
                      'model': 'no-model-server', 'config': {'api': 'responses', 'max_retries': 0, 'timeout': .2}},
                      'instructions': 'No remote request is authorized.'}
            try:
                run_agent(run_id, workspace, config)
            except ProviderError as error:
                assert error.code == 'transport_error'
            else:
                raise AssertionError('An OS connection refusal was accepted')
        saved = json.loads((workspace / 'agent_session.json').read_text())
        assert saved['status'] == 'provider_error' and saved['active_seconds'] > 0
        assert len(saved['transcript']) == 1 and saved['pending_action'] is None
        assert saved['transcript'][0]['error']['code'] == 'transport_error'
        assert saved['transcript'][0]['usage'] is None and saved['totals']['cost'] is None
        assert 'output_recovery' not in saved
        print('Actual connection refusal persisted as a failed turn with unknown usage and measured elapsed time')

    elif scenario == 'budget':
        pricing = {'input_per_million': 1, 'output_per_million': 1, 'cached_input_per_million': .1, 'currency': 'USD'}
        with Session.begin() as s:
            s.get(TaskRun, run_id).config = {'agent_budget': {'cost': .004198}}
            provider = Provider(name='Ledger only; no HTTP', kind='openai', base_url='https://api.openai.com/v1',
                                model='ledger-input', allow_paid=True, config={'budget_usd': 10, 'pricing': pricing})
            s.add(provider); s.flush(); value = asdict(provider, True)
        guard = make_request_guard({**value, '_usage_context': {'project_id': project_id, 'run_id': run_id}})
        before = {'phase': 'before', 'model': 'ledger-input', 'pricing': pricing, 'input_bytes': 0, 'max_output_tokens': 1}
        first = guard(before)
        guard({'phase': 'after', 'reservation': first, 'usage': {'input_tokens': 100, 'output_tokens': 0},
               'status': 'incomplete', 'incomplete_reason': 'max_output_tokens'})
        uncertain = guard(before)
        guard({'phase': 'error', 'reservation': uncertain, 'ambiguous': True})
        final = guard(before)  # Exactly 100 + 2049 + 2049 micro-USD.
        try:
            guard(before)
        except BudgetExceeded as error:
            assert 'Agent API spending limit' in str(error)
        else:
            raise AssertionError('Continuation bypassed the original run cost budget')
        with Session() as s:
            rows = list(s.scalars(select(ModelRequest).where(ModelRequest.run_id == run_id)))
            assert len(rows) == 3
            assert s.get(ModelRequest, first).details['response_status'] == 'incomplete'
            assert s.get(ModelRequest, uncertain).status == 'uncertain'
            assert s.get(ModelRequest, final).status == 'reserved'
        print('Original run budget counts received incomplete usage and uncertain reservations before allowing a new request')
