"""Actual OS-process validation; no substitute model or execution transport."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from research.agents.processes import ManagedProcesses, TERMINAL
from research.agents.runtime import budget_reason, session_messages


def completed(manager, process_id, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.inspect(process_id)
        if state['status'] in TERMINAL:
            return state
        time.sleep(.03)
    manager.cancel(process_id)
    pytest.fail('Actual process did not terminate within test observation window')


def test_real_general_command_and_incremental_log(tmp_path):
    manager = ManagedProcesses(tmp_path)
    state = manager.start(['/bin/sh', '-c', 'printf first; printf second; printf problem >&2'])
    result = completed(manager, state['process_id'])
    assert result['status'] == 'completed' and result['exit_code'] == 0
    one = manager.read_output(state['process_id'], limit=5)
    two = manager.read_output(state['process_id'], offset=one['next_offset'])
    assert one['content'] + two['content'] == 'firstsecond'
    assert manager.read_output(state['process_id'], stream='stderr')['content'] == 'problem'
    assert Path(tmp_path / '.forest-processes' / state['process_id'] / 'request.json').is_file()


def test_launch_replay_never_executes_same_command_twice(tmp_path):
    manager = ManagedProcesses(tmp_path)
    command = [sys.executable, '-c', "from pathlib import Path; p=Path('count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"]
    first = manager.start(command, process_id='action-1')
    second = ManagedProcesses(tmp_path).start(command, process_id='action-1')
    assert first['process_id'] == second['process_id']
    assert completed(manager, 'action-1')['status'] == 'completed'
    assert (tmp_path / 'count').read_text() == '1'
    with pytest.raises(ValueError, match='different command'):
        manager.start(['/bin/echo', 'different'], process_id='action-1')


def test_process_survives_launching_interpreter_exit(tmp_path):
    # Real numerical integration continues after the initiating Python exits.
    script = tmp_path / 'integrate.py'
    script.write_text("from pathlib import Path\nimport json\nn=2_000_000\nvalue=sum(4/(1+((i+.5)/n)**2) for i in range(n))/n\nPath('integral.json').write_text(json.dumps({'value':value,'intervals':n}))\n")
    project = Path(__file__).resolve().parents[1]
    code = "from research.agents.processes import ManagedProcesses; import sys; print(ManagedProcesses(sys.argv[1]).start([sys.executable,'integrate.py'],process_id='continued')['process_id'])"
    owner = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=project, capture_output=True, text=True, check=True)
    assert owner.stdout.strip() == 'continued'
    manager = ManagedProcesses(tmp_path)
    assert completed(manager, 'continued')['status'] == 'completed'
    observed = json.loads((tmp_path / 'integral.json').read_text())
    assert abs(observed['value'] - 3.141592653589793) < 1e-10


def test_real_failure_cancel_timeout_and_environment(tmp_path):
    manager = ManagedProcesses(tmp_path)
    state = manager.start([sys.executable, '-c', "import os,sys; print(os.environ['FOREST_TEST_VALUE'],flush=True); sys.exit(7)"], env={'FOREST_TEST_VALUE': 'observed'})
    result = completed(manager, state['process_id'])
    assert result['status'] == 'failed' and result['exit_code'] == 7
    assert manager.read_output(state['process_id'])['content'].strip() == 'observed'
    calculating = "n=0\nwhile True:\n n=(n*n+1)%1_000_000_007"
    state = manager.start([sys.executable, '-c', calculating])
    manager.cancel(state['process_id'])
    result = completed(manager, state['process_id'])
    assert result['status'] == 'cancelled' and result['exit_code'] != 0
    timed = manager.start([sys.executable, '-c', calculating], timeout=.2)
    result = completed(manager, timed['process_id'])
    assert result['status'] == 'failed' and 'timeout' in result['error']


def test_process_paths_cannot_escape_and_outputs_remain_editable(tmp_path):
    manager = ManagedProcesses(tmp_path)
    with pytest.raises(ValueError):
        manager.start(['/bin/pwd'], cwd='..')
    with pytest.raises(ValueError):
        manager.inspect('../../outside')
    state = manager.start([sys.executable, '-c', "from pathlib import Path; Path('editable.txt').write_text('original')"])
    assert completed(manager, state['process_id'])['status'] == 'completed'
    (tmp_path / 'editable.txt').write_text('user revision')
    assert (tmp_path / 'editable.txt').read_text() == 'user revision'


def test_no_default_agent_turn_limit_and_explicit_budget():
    state = {'transcript': [{'step': n} for n in range(120)], 'totals': {'input_tokens': 500, 'output_tokens': 400, 'cost': None}, 'active_seconds': 200}
    assert budget_reason(state, {}) is None
    assert 'steps=120' in budget_reason(state, {'agent_budget': {'steps': 120}})
    assert budget_reason(state, {'agent_budget': {'steps': 121}}) is None
    assert 'output_tokens' in budget_reason(state, {'agent_budget': {'output_tokens': 300}})


def test_context_compaction_keeps_complete_editable_transcript(tmp_path):
    messages = [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'goal'}] + [{'role': 'user', 'content': 'evidence ' + str(n) + 'x' * 200} for n in range(100)]
    state = {'messages': messages, 'transcript': [{'step': n} for n in range(100)]}
    (tmp_path / 'research_memory.json').write_text(json.dumps({'decision': 'Compare the strongest baseline first'}))
    compact = session_messages(state, tmp_path, 3000)
    assert compact[0]['content'] == 'policy'
    assert compact[-1]['content'] == messages[-1]['content']
    assert any('strongest baseline' in message['content'] for message in compact)
    assert len(state['messages']) == 102 and len(state['transcript']) == 100


def test_concurrent_launch_intents_execute_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    command = [sys.executable, '-c', "from pathlib import Path; p=Path('executions'); f=p.open('a'); f.write('x'); f.close(); print(sum(i*i for i in range(1000000)))"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        launched = list(pool.map(lambda _: ManagedProcesses(tmp_path).start(command, process_id='concurrent-intent'), range(6)))
    assert {p['process_id'] for p in launched} == {'concurrent-intent'}
    assert completed(ManagedProcesses(tmp_path), 'concurrent-intent')['status'] == 'completed'
    assert (tmp_path / 'executions').read_text() == 'x'


def test_repeated_unchanged_actions_receive_concrete_progress_feedback():
    from research.agents.runtime import progress_feedback
    state = {}
    assert progress_feedback(state, 'update_memory', {'evidence': 'failure'}, {'changed': False}) is None
    feedback = progress_feedback(state, 'update_memory', {'evidence': 'failure'}, {'changed': False})
    assert 'edit the cause' in feedback
    assert progress_feedback(state, 'write_file', {'path': 'actual.py', 'content': 'print(1)'}, {'bytes': 8}) is None


def test_plain_python_command_uses_actual_active_environment(tmp_path):
    manager = ManagedProcesses(tmp_path)
    state = manager.start(['python', '-c', 'import sys; print(sys.prefix)'])
    assert completed(manager, state['process_id'])['status'] == 'completed'
    assert manager.read_output(state['process_id'])['content'].strip() == sys.prefix


def test_metric_file_written_by_agent_requires_successful_real_process(tmp_path):
    from research.agents.runtime import ToolRuntime, collect_agent_metrics
    tool = ToolRuntime('local-completion-guard-check', tmp_path, allowed=['write_file'])
    # Exercise the actual file-writing tool; no model or tool response substitute.
    tool.dispatch('write_file', {'path': 'metrics.json', 'content': '{"unverified_number": 17}'}, None)
    for config in ({}, {'metrics_file': 'metrics.json'}):
        with pytest.raises(ValueError, match='actual successful managed-process receipt'):
            collect_agent_metrics(tmp_path, config, tool.processes)
    failed = tool.processes.start([sys.executable, '-c', 'raise SystemExit(2)'])
    assert completed(tool.processes, failed['process_id'])['exit_code'] == 2
    with pytest.raises(ValueError, match='actual successful managed-process receipt'):
        collect_agent_metrics(tmp_path, {}, tool.processes)
    launched = tool.processes.start([sys.executable, '-c', "import json; from pathlib import Path; Path('metrics.json').write_text(json.dumps({'squared_sum':sum(i*i for i in range(1000))}))"])
    assert completed(tool.processes, launched['process_id'])['status'] == 'completed'
    observed = collect_agent_metrics(tmp_path, {}, tool.processes)
    assert observed['observed_metrics'] == {'squared_sum': 332833500}
    with pytest.raises(ValueError, match='missing required fields: sample_count'):
        collect_agent_metrics(tmp_path, {'metrics_required_keys':['squared_sum','sample_count']}, tool.processes)
    assert collect_agent_metrics(tmp_path, {'metrics_required_keys':['squared_sum']}, tool.processes)['observed_metrics']==observed['observed_metrics']
    assert observed['metrics_evidence']['label'] == 'FILE_OBSERVATION'
    assert observed['metrics_evidence']['successful_process_ids'] == [launched['process_id']]
    assert 'does not establish scientific validity' in observed['metrics_evidence']['scientific_validity']


def test_nonmetric_research_output_does_not_require_process_execution(tmp_path):
    from research.agents.runtime import ToolRuntime, collect_agent_metrics
    tool = ToolRuntime('local-report-completion-check', tmp_path, allowed=['write_file'])
    tool.dispatch('write_file', {'path': 'source_review.md', 'content': 'This is a source review, not an experimental measurement.'}, None)
    assert collect_agent_metrics(tmp_path, {}, tool.processes) == {}
