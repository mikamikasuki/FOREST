"""Actual file reads and durable action replay, with no model/network call."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('protocol', ['json', 'native'])
def test_edited_action_attribution_and_replay(tmp_path, protocol):
    env = {**os.environ, 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'actions.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'), 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, __file__, str(tmp_path), protocol], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    import copy
    import json
    from sqlalchemy import select, func
    from services.api.db import migrate, Session, Project, TaskRun, ToolExecution, uid
    from research.agents.runtime import AgentYield, ToolRuntime, observed_progress, run_agent
    from research.agents.processes import atomic_json

    directory, protocol = Path(sys.argv[1]), sys.argv[2]
    migrate()
    with Session.begin() as s:
        project = Project(name='Actual local action attribution')
        s.add(project); s.flush()
        run = TaskRun(project_id=project.id, request_id=uid(), status='running', kind='agent')
        other_run = TaskRun(project_id=project.id, request_id=uid(), status='running', kind='agent')
        s.add_all([run, other_run]); s.flush()
        run_id, other_id = run.id, other_run.id

    workspace = directory / 'workspace'; workspace.mkdir()
    (workspace / 'requested.txt').write_text('Original requested file')
    (workspace / 'selected.txt').write_text('Actual debugger-selected file')
    original_action = {'tool': 'read_file', 'arguments': {'path': 'requested.txt'}}
    action = {'tool': 'read_file', 'arguments': {'path': 'selected.txt'}, 'id': uid()}
    # These are explicit persisted protocol inputs, not a provider response or
    # a claimed model success. The one-step budget prevents any model request.
    turn = {'step': 1, 'model': 'local-protocol-record', 'content': json.dumps(original_action), 'tool_calls': []}
    if protocol == 'native':
        turn['content'] = ''
        turn['tool_calls'] = [{'name': 'read_file', 'arguments': {'path': 'requested.txt', 'offset': None, 'limit': None}, 'call_id': 'local-call'}]
        action['call_id'] = 'local-call'
    original_turn = copy.deepcopy(turn)
    state = {'run_id': run_id, 'status': 'executing', 'messages': [{'role': 'system', 'content': 'local test'}, {'role': 'user', 'content': 'local test'}],
             'transcript': [turn], 'pending_action': action, 'totals': {'input_tokens': 0, 'output_tokens': 0, 'cost': None}, 'active_seconds': 0}
    pending = copy.deepcopy(state)
    config = {'provider_snapshot': {'kind': 'ollama', 'base_url': 'http://127.0.0.1:9', 'model': 'no-request-is-made'},
              'agent_budget': {'steps': 1}, 'instructions': 'Execute the persisted local action only.'}

    def resume_pending():
        atomic_json(workspace / 'agent_session.json', pending)
        try:
            run_agent(run_id, workspace, config)
        except AgentYield as exc:
            assert exc.status == 'budget_exhausted'
        else:
            raise AssertionError('Expected a budget yield before any model request')
        return json.loads((workspace / 'agent_session.json').read_text())

    first = resume_pending()
    for stored in (first['transcript'][0], json.loads((workspace / 'agent_transcript.json').read_text())[0]):
        assert stored['content'] == original_turn['content']
        assert stored['tool_calls'] == original_turn['tool_calls']
        assert stored['executed_action'] == action
        assert stored['tool_result']['path'] == 'selected.txt'
        assert stored['tool_result']['content'] == 'Actual debugger-selected file'
    observed = observed_progress(first)[0]
    assert observed['arguments']['path'] == observed['observed']['path'] == 'selected.txt'
    if protocol == 'native':
        assert first['messages'][-1]['native_call_id'] == 'local-call'

    # Recreate a crash after the durable tool result but before session save.
    # Valid replay must return the actual prior read, not read the changed file.
    (workspace / 'selected.txt').write_text('Changed after the completed read')
    resumed = resume_pending()
    assert resumed['transcript'][0]['executed_action'] == action
    assert resumed['transcript'][0]['tool_result'] == first['transcript'][0]['tool_result']
    assert observed_progress(resumed) == observed_progress(first)
    with Session() as s:
        receipt = s.get(ToolExecution, action['id'])
        recorded = {'tool': receipt.tool, 'arguments': receipt.arguments, 'result': receipt.result, 'status': receipt.status}
        assert recorded['arguments'] == action['arguments']
        assert s.scalar(select(func.count()).select_from(ToolExecution)) == 1

    for target, name, arguments in ((run_id, 'read_file', {'path': 'requested.txt'}),
                                    (run_id, 'list_files', action['arguments']),
                                    (other_id, 'read_file', action['arguments'])):
        try:
            ToolRuntime(target, workspace).execute(name, arguments, action['id'])
        except ValueError as exc:
            assert 'another request' in str(exc)
        else:
            raise AssertionError('Reused action ID accepted a different execution identity')
    with Session() as s:
        receipt = s.get(ToolExecution, action['id'])
        assert {'tool': receipt.tool, 'arguments': receipt.arguments, 'result': receipt.result, 'status': receipt.status} == recorded
    print('Original action retained; actual edited read and durable replay attributed correctly; identity mismatch rejected')
