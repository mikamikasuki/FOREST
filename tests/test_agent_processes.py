"""Actual OS-process validation; no substitute model or execution transport."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

import pytest

from research.agents.processes import ManagedProcesses, TERMINAL, is_alive
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


def parent_starting_same_group_child(child_code):
    parent = "import subprocess,sys\nsubprocess.Popen([sys.executable,'-c'," + repr(child_code) + "])\n"
    return [sys.executable, '-c', parent]


def wait_for_file(path, timeout=4):
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    return path.exists()


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


def test_parent_exit_does_not_complete_while_child_can_write(tmp_path):
    manager = ManagedProcesses(tmp_path)
    child = """from pathlib import Path
import os
import time
Path('child.started').write_text(str(os.getpid()))
time.sleep(.6)
Path('late-marker.txt').write_text('child finished')
"""
    state = manager.start(parent_starting_same_group_child(child))
    process_id = state['process_id']
    assert wait_for_file(tmp_path / 'child.started')
    assert manager.inspect(process_id)['status'] not in TERMINAL
    result = completed(manager, process_id)
    assert result['status'] == 'completed' and result['exit_code'] == 0
    assert (tmp_path / 'late-marker.txt').read_text() == 'child finished'


def test_cancel_stops_child_after_parent_exit(tmp_path):
    manager = ManagedProcesses(tmp_path)
    child = """from pathlib import Path
import os
import time
Path('child.started').write_text(str(os.getpid()))
ticks = Path('ticks')
while True:
    ticks.write_text(ticks.read_text() + 'x' if ticks.exists() else 'x')
    time.sleep(.03)
"""
    state = manager.start(parent_starting_same_group_child(child))
    process_id = state['process_id']
    assert wait_for_file(tmp_path / 'child.started')
    manager.cancel(process_id)
    result = completed(manager, process_id)
    assert result['status'] == 'cancelled'
    ticks_at_terminal = (tmp_path / 'ticks').read_text()
    time.sleep(.1)
    assert (tmp_path / 'ticks').read_text() == ticks_at_terminal


def test_timeout_stops_child_after_parent_exit(tmp_path):
    manager = ManagedProcesses(tmp_path)
    child = """from pathlib import Path
import os
import time
Path('child.started').write_text(str(os.getpid()))
ticks = Path('ticks')
while True:
    ticks.write_text(ticks.read_text() + 'x' if ticks.exists() else 'x')
    time.sleep(.03)
"""
    state = manager.start(parent_starting_same_group_child(child), timeout=.5)
    process_id = state['process_id']
    assert wait_for_file(tmp_path / 'child.started')
    result = completed(manager, process_id)
    assert result['status'] == 'failed' and 'timeout' in result['error']
    ticks_at_terminal = (tmp_path / 'ticks').read_text()
    time.sleep(.1)
    assert (tmp_path / 'ticks').read_text() == ticks_at_terminal


def test_agent_finish_waits_for_child_through_real_worker(tmp_path):
    from tests.test_worker import Harness

    captures = []
    shared = {'workspace': None, 'process_id': None, 'finish_rejected_while_child_active': False}

    class Transport(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captures.append(payload)
            stage = len(captures)
            workspace = shared['workspace']
            if stage == 1:
                child = """from pathlib import Path
import os
import time
Path('child.started').write_text(str(os.getpid()))
while not Path('finish_attempted').exists():
    time.sleep(.01)
Path('late-marker.txt').write_text('descendant wrote after parent exit')
while not Path('finish_done').exists():
    time.sleep(.01)
"""
                action = {'tool': 'start_process', 'arguments': {'command': parent_starting_same_group_child(child)}}
            elif stage == 2:
                manager = ManagedProcesses(workspace)
                processes = manager.all()
                if not processes:
                    raise AssertionError('The process launch receipt was absent from the next model request')
                process_id = processes[-1]['process_id']
                shared['process_id'] = process_id
                deadline = time.monotonic() + 8
                state = manager.inspect(process_id)
                while (not (workspace / 'child.started').exists()
                       or is_alive(state.get('pid'), state.get('process_created'))):
                    if time.monotonic() >= deadline:
                        raise AssertionError('The parent command did not exit while its child remained')
                    time.sleep(.01)
                    state = manager.inspect(process_id)
                (workspace / 'finish_attempted').write_text('attempt')
                action = {'tool': 'finish', 'arguments': {'summary': 'Finished after process completion.', 'artifacts': []}}
            else:
                process_id = shared['process_id']
                manager = ManagedProcesses(workspace)
                state = manager.inspect(process_id)
                shared['finish_rejected_while_child_active'] = state['status'] not in TERMINAL
                if not shared['finish_rejected_while_child_active']:
                    raise AssertionError(f"The run advanced without a live child: process={state!r}")
                (workspace / 'finish_done').write_text('release')
                deadline = time.monotonic() + 8
                while manager.inspect(process_id)['status'] not in TERMINAL:
                    if time.monotonic() >= deadline:
                        raise AssertionError('The descendant did not reach a terminal process state')
                    time.sleep(.02)
                action = {'tool': 'finish', 'arguments': {'summary': 'Finished after process completion.', 'artifacts': []}}

            response = json.dumps({'model': 'local-process-lifecycle-fixture',
                                   'message': {'role': 'assistant', 'content': json.dumps(action)},
                                   'done': True, 'prompt_eval_count': 1, 'eval_count': 1}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Transport)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    harness = Harness(tmp_path)
    run = None
    try:
        harness.start_api()
        provider = harness.request('POST', '/api/providers', json={
            'name': 'Local process lifecycle fixture', 'kind': 'ollama',
            'base_url': f'http://127.0.0.1:{server.server_port}',
            'model': 'local-process-lifecycle-fixture', 'allow_paid': False})
        project = harness.request('POST', '/api/projects', json={
            'name': 'Managed child completion check',
            'goal': 'Run the managed command, keep the agent active until its child exits, then finish.',
            'config': {'provider_id': provider['id']},
            'budget': {'max_runs': 3, 'seconds': 60, 'allow_paid': False}})
        graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        node_id = str(uuid.uuid4())
        response = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
            'operation': 'add_node', 'targets': [],
            'params': {'id': node_id, 'branch_id': graph['branches'][0]['id'],
                       'type': 'experiment', 'title': 'Wait for managed child before finish',
                       'instructions': 'Start the managed command and finish only after it is complete.',
                       'config': {'kind': 'agent', 'required_outputs': [],
                                  'agent_budget': {'steps': 8, 'active_seconds': 45}}}})
        node = next(item for item in response['graph']['nodes'] if item['id'] == node_id)
        run = harness.launch(node)
        shared['workspace'] = harness.output(run) / 'workspace'
        harness.start_worker()
        result = harness.terminal(run, timeout=30)
        marker_at_terminal = (shared['workspace'] / 'late-marker.txt').exists()
        assert result['status'] == 'completed', result
        assert shared['finish_rejected_while_child_active']
        assert marker_at_terminal
        assert len(captures) == 3
        agent_result = json.loads((shared['workspace'] / 'agent_result.json').read_text())
        executions = agent_result['executions']
        assert executions and executions[0]['status'] == 'completed'
    finally:
        if shared['workspace'] is not None:
            shared['workspace'].mkdir(parents=True, exist_ok=True)
            (shared['workspace'] / 'finish_done').write_text('release')
            if shared['process_id']:
                manager = ManagedProcesses(shared['workspace'])
                for process in manager.all():
                    if process['status'] not in TERMINAL:
                        manager.cancel(process['process_id'])
        harness.cleanup()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


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
