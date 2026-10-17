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

import psutil
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


def _process_identity_alive(pid, created):
    if not pid or created is None:
        return False
    try:
        process = psutil.Process(int(pid))
        return (abs(process.create_time() - float(created)) < .1
                and process.status() != psutil.STATUS_ZOMBIE)
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


def _owned_group_has_live_member(group_id, members):
    bindings = dict(members or {})
    for pid, created in bindings.items():
        if not _process_identity_alive(pid, created):
            continue
        try:
            if os.getpgid(int(pid)) == int(group_id):
                return True
        except (ProcessLookupError, PermissionError, ValueError):
            continue
    return False


def _process_bindings(processes):
    bindings = {}
    groups = {}
    for process in processes:
        try:
            created = process.create_time()
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            bindings[str(process.pid)] = created
            group_id = str(os.getpgid(process.pid))
            groups.setdefault(group_id, {})[str(process.pid)] = created
        except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied,
                PermissionError):
            continue
    return {'processes': bindings, 'groups': groups}


def _kill_bound_snapshot(snapshot, owner_group=None):
    """Kill only processes and groups bound by PID creation identity."""
    for group_id, members in snapshot.get('groups', {}).items():
        if owner_group is not None and int(group_id) == int(owner_group):
            continue
        if not _owned_group_has_live_member(group_id, members):
            continue
        # Recheck immediately before the one signal sent to this group.
        if _owned_group_has_live_member(group_id, members):
            try:
                os.killpg(int(group_id), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    for pid, created in snapshot.get('processes', {}).items():
        if not _process_identity_alive(pid, created):
            continue
        # Recheck immediately before the individual fallback signal.
        if _process_identity_alive(pid, created):
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _new_owned_descendants(owner, baseline, direct=False):
    try:
        processes = owner.children(recursive=not direct)
    except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
        return []
    owned = []
    for process in processes:
        try:
            if (process.pid, process.create_time()) not in baseline:
                owned.append(process)
        except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return owned


def _stop_owned_process(process):
    try:
        created = process.create_time()
        if (process.status() != psutil.STATUS_ZOMBIE
                and _process_identity_alive(process.pid, created)):
            os.kill(process.pid, signal.SIGSTOP)
    except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied,
            PermissionError):
        return


def _freeze_owned_descendants(owner, baseline, owner_group, after_census=None):
    """Stop supervisors, rescan until stable, and emergency-clean unstable owners."""
    deadline = time.monotonic() + 1
    previous = None
    while time.monotonic() < deadline:
        for process in _new_owned_descendants(owner, baseline, direct=True):
            _stop_owned_process(process)
        processes = _new_owned_descendants(owner, baseline)
        if after_census is not None:
            callback, after_census = after_census, None
            callback()
        for process in processes:
            _stop_owned_process(process)
        pending = []
        identities = []
        for process in processes:
            try:
                identity = (process.pid, process.create_time())
                identities.append(identity)
                if process.status() not in (psutil.STATUS_STOPPED,
                                            psutil.STATUS_ZOMBIE):
                    pending.append(process)
            except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        identities = tuple(sorted(identities))
        if not pending and identities == previous:
            return processes
        previous = identities
        time.sleep(.01)
    emergency = _process_bindings(_new_owned_descendants(owner, baseline))
    _kill_bound_snapshot(emergency, owner_group=owner_group)
    deadline = time.monotonic() + .5
    while (time.monotonic() < deadline
           and (any(_process_identity_alive(pid, created)
                   for pid, created in emergency['processes'].items())
                or _new_owned_descendants(owner, baseline))):
        time.sleep(.01)
    if (any(_process_identity_alive(pid, created)
            for pid, created in emergency['processes'].items())
            or _new_owned_descendants(owner, baseline)):
        raise RuntimeError('test process cleanup could not obtain a stable stopped census')
    return []


class _ManagedProcessTestScope:
    """Own only descendants created by this test's live pytest process."""

    def __init__(self, workspace, request):
        self.manager = ManagedProcesses(workspace)
        self.owner = psutil.Process()
        self.baseline = {(process.pid, process.create_time())
                         for process in self.owner.children(recursive=True)}
        request.addfinalizer(self.cleanup)

    def _new_descendants(self, direct=False):
        return _new_owned_descendants(self.owner, self.baseline, direct)

    @staticmethod
    def _stop(process):
        _stop_owned_process(process)

    def _freeze(self):
        return _freeze_owned_descendants(self.owner, self.baseline,
                                         os.getpgid(self.owner.pid))

    def cleanup(self):
        self._freeze()
        snapshot = _process_bindings(self._new_descendants())
        metadata = os.environ.get('FOREST_TEST_CLEANUP_METADATA')
        if metadata:
            Path(metadata).write_text(json.dumps({**snapshot, 'ready': True}))
        owner_group = os.getpgid(self.owner.pid)
        _kill_bound_snapshot(snapshot, owner_group=owner_group)

    def __getattr__(self, name):
        return getattr(self.manager, name)


@pytest.fixture
def managed_process_scope(tmp_path, request):
    return _ManagedProcessTestScope(tmp_path, request)


def _wait_for_nested_cleanup_ready(metadata, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = json.loads(Path(metadata).read_text())
        except (FileNotFoundError, OSError, ValueError, UnicodeError):
            time.sleep(.02)
            continue
        if state.get('ready'):
            return state
        time.sleep(.02)
    pytest.fail(f'nested process did not publish a ready ownership record: {metadata}')


def _assert_nested_process_cleanup(state, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (not any(_process_identity_alive(pid, created)
                    for pid, created in state.get('processes', {}).items())
                and not any(_owned_group_has_live_member(group_id, members)
                            for group_id, members in state.get('groups', {}).items())):
            break
        time.sleep(.03)
    assert not any(_process_identity_alive(pid, created)
                   for pid, created in state.get('processes', {}).items())
    assert not any(_owned_group_has_live_member(group_id, members)
                   for group_id, members in state.get('groups', {}).items())


def _external_cleanup_metadata(metadata):
    try:
        snapshot = json.loads(Path(metadata).read_text())
    except (FileNotFoundError, OSError, ValueError, UnicodeError):
        return
    _kill_bound_snapshot(snapshot)


def _external_cleanup_process(pid, metadata=None):
    """Freeze a nested runner and all supervisors before killing the bound set."""
    runner = None
    created = None
    try:
        runner = psutil.Process(int(pid))
        created = runner.create_time()
        if not _process_identity_alive(runner.pid, created):
            return False
        if _process_identity_alive(runner.pid, created):
            os.kill(runner.pid, signal.SIGSTOP)
        deadline = time.monotonic() + 1
        while (time.monotonic() < deadline
               and runner.status() not in (psutil.STATUS_STOPPED, psutil.STATUS_ZOMBIE)):
            time.sleep(.01)
        if runner.status() != psutil.STATUS_STOPPED:
            raise RuntimeError('nested runner did not stop before external cleanup census')
        outer_group = os.getpgid(os.getpid())
        processes = _freeze_owned_descendants(runner, set(), outer_group)
        processes = [runner] + processes
        snapshot = _process_bindings(processes)
        snapshot['processes'][str(runner.pid)] = created
        if metadata:
            Path(metadata).write_text(json.dumps({**snapshot, 'ready': True}))
        _kill_bound_snapshot(snapshot, owner_group=outer_group)
        deadline = time.monotonic() + .5
        while (time.monotonic() < deadline
               and any(_process_identity_alive(process_pid, process_created)
                       for process_pid, process_created in snapshot['processes'].items())):
            time.sleep(.01)
        if any(_process_identity_alive(process_pid, process_created)
               for process_pid, process_created in snapshot['processes'].items()):
            raise RuntimeError('external cleanup left a creation-bound survivor')
        return True
    except (FileNotFoundError, ProcessLookupError, psutil.NoSuchProcess,
            psutil.AccessDenied, PermissionError):
        return False
    except RuntimeError:
        if runner is not None and created is not None and _process_identity_alive(runner.pid, created):
            os.kill(runner.pid, signal.SIGKILL)
        raise


def _run_nested_cleanup_case(tmp_path, outcome):
    project = Path(__file__).resolve().parents[1]
    metadata = tmp_path / f'nested-{outcome}.json'
    script = tmp_path / f'nested-{outcome}.py'
    script.write_text(f'''import json, os, signal, time
from pathlib import Path
import pytest
from test_agent_processes import completed

pytest_plugins = ("test_agent_processes",)


def test_nested_process_lifetime(managed_process_scope):
    manager = managed_process_scope
    process_id = "interrupt-start" if {outcome!r} == "interrupt-start" else None
    if process_id:
        folder = manager.folder(process_id)
        def interrupt_start():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    record = json.loads((folder / "state.json").read_text())
                    if record.get("host_pid") and record.get("process_group_id"):
                        os.kill(os.getpid(), signal.SIGINT)
                        return
                except (FileNotFoundError, OSError, ValueError):
                    pass
                time.sleep(.005)
        interrupter = __import__("threading").Thread(target=interrupt_start)
        interrupter.start()
        interrupted = False
        try:
            state = manager.start([os.environ["PYTHON"], "-c", "import time; end=time.monotonic()+8; n=0\\nwhile time.monotonic()<end: n=(n*n+1)%1000000007"], process_id=process_id)
        except KeyboardInterrupt:
            interrupted = True
            state = {{"process_id": process_id}}
        finally:
            interrupter.join(timeout=2)
        assert interrupted
        assert state["process_id"] == process_id
        return
    state = manager.start([os.environ["PYTHON"], "-c", "import time; end=time.monotonic()+8; n=0\\nwhile time.monotonic()<end: n=(n*n+1)%1000000007"])
    deadline = time.monotonic() + 5
    ownership = None
    while time.monotonic() < deadline:
        ownership = manager.inspect(state["process_id"])
        if (ownership.get("pid") and ownership.get("process_created") is not None
                and ownership.get("process_group_id")):
            break
        time.sleep(.02)
    assert ownership and ownership.get("process_created") is not None
    Path(os.environ["FOREST_TEST_READY"]).touch()
    if {outcome!r} == "fail":
        raise AssertionError("intentional nested failure")
    if {outcome!r} == "observation-timeout":
        completed(manager, state["process_id"], timeout=.05)
    if {outcome!r} == "ctrl-c":
        end = time.monotonic() + 8
        while time.monotonic() < end:
            time.sleep(1)
        pytest.fail("Ctrl-C did not interrupt the nested test")
''')
    environment = os.environ.copy()
    environment.update({'FOREST_TEST_READY': str(tmp_path / f'ready-{outcome}'),
                        'FOREST_TEST_CLEANUP_METADATA': str(metadata),
                        'PYTHON': sys.executable,
                        'PYTHONPATH': str(project / 'tests') + os.pathsep + str(project)})
    command = [sys.executable, '-m', 'pytest', '-q', '--basetemp',
               str(tmp_path / '.cleanup-small-worker'), str(script)]
    child = subprocess.Popen(command, cwd=project, env=environment,
                             start_new_session=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    try:
        if outcome == 'ctrl-c':
            ready = Path(environment['FOREST_TEST_READY'])
            assert wait_for_file(ready, timeout=8)
            os.killpg(child.pid, signal.SIGINT)
        observer_timeout = .2 if outcome == 'observer-timeout' else 12
        output, _ = child.communicate(timeout=observer_timeout)
    except subprocess.TimeoutExpired as timeout:
        # This is the negative control: the nested finalizer has not published metadata.
        if outcome == 'observer-timeout':
            assert not metadata.exists()
        output = timeout.stdout or ''
        if isinstance(output, bytes):
            output = output.decode(errors='replace')
        _external_cleanup_process(child.pid, metadata)
        trailing, _ = child.communicate(timeout=4)
        if isinstance(trailing, bytes):
            trailing = trailing.decode(errors='replace')
        output += trailing or ''
    finally:
        if child.poll() is None:
            _external_cleanup_process(child.pid, metadata)
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.wait(timeout=4)
    return child.returncode, output, _wait_for_nested_cleanup_ready(metadata)


@pytest.mark.parametrize('outcome', ('pass', 'fail', 'observation-timeout', 'ctrl-c',
                                    'interrupt-start', 'observer-timeout'))
def test_nested_process_cleanup_for_each_test_outcome(tmp_path, outcome):
    metadata = tmp_path / f'nested-{outcome}.json'
    try:
        returncode, output, state = _run_nested_cleanup_case(tmp_path, outcome)
        _assert_nested_process_cleanup(state)
        # Keep the nested runner's result meaningful, while allowing pytest's
        # normal failure and KeyboardInterrupt exits to exercise the finalizer.
        if outcome in ('pass', 'interrupt-start'):
            assert returncode == 0, output
        else:
            assert returncode != 0, output
    finally:
        _external_cleanup_metadata(metadata)


def test_cleanup_census_captures_descendant_created_after_first_scan():
    owner = psutil.Process()
    baseline = {(process.pid, process.create_time())
                for process in owner.children(recursive=True)}
    child = None
    owner_group = os.getpgid(owner.pid)

    def launch_late_child():
        nonlocal child
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'],
                                 start_new_session=True)

    try:
        captured = _freeze_owned_descendants(owner, baseline, owner_group,
                                             after_census=launch_late_child)
        snapshot = _process_bindings(captured)
        assert child is not None
        assert str(child.pid) in snapshot['processes']
        _kill_bound_snapshot(snapshot, owner_group=owner_group)
        child.wait(timeout=2)
    finally:
        if child is not None and child.poll() is None:
            if _process_identity_alive(child.pid, child.create_time()):
                os.kill(child.pid, signal.SIGKILL)
            child.wait(timeout=2)


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


def test_signal_all_reaches_child_after_parent_exit(tmp_path):
    manager = ManagedProcesses(tmp_path)
    child = """from pathlib import Path
import os
import time
Path('child.started').write_text(str(os.getpid()))
ticks = Path('ticks')
while not Path('release').exists():
    ticks.write_text(ticks.read_text() + 'x' if ticks.exists() else 'x')
    time.sleep(.03)
Path('child.finished').write_text('done')
"""
    state = manager.start(parent_starting_same_group_child(child))
    process_id = state['process_id']
    assert wait_for_file(tmp_path / 'child.started')
    state = manager.inspect(process_id)
    deadline = time.monotonic() + 4
    while is_alive(state.get('pid'), state.get('process_created')) and time.monotonic() < deadline:
        time.sleep(.01)
        state = manager.inspect(process_id)
    assert not is_alive(state.get('pid'), state.get('process_created'))

    manager.signal_all(signal.SIGSTOP)
    ticks_while_stopped = (tmp_path / 'ticks').read_text()
    time.sleep(.12)
    assert (tmp_path / 'ticks').read_text() == ticks_while_stopped

    manager.signal_all(signal.SIGCONT)
    (tmp_path / 'release').write_text('release')
    result = completed(manager, process_id)
    assert result['status'] == 'completed'
    assert (tmp_path / 'child.finished').read_text() == 'done'


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
            'model': 'local-process-lifecycle-fixture', 'allow_paid': False,
            'config': {'model_capabilities': {'context_window_tokens': 8192,
                                               'max_output_tokens': 4096,
                                               'source': 'test_transport_contract'}}})
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


def test_real_failure_cancel_timeout_and_environment(managed_process_scope):
    manager = managed_process_scope
    state = managed_process_scope.start([sys.executable, '-c', "import os,sys; print(os.environ['FOREST_TEST_VALUE'],flush=True); sys.exit(7)"], env={'FOREST_TEST_VALUE': 'observed'})
    result = completed(manager, state['process_id'])
    assert result['status'] == 'failed' and result['exit_code'] == 7
    assert manager.read_output(state['process_id'])['content'].strip() == 'observed'
    calculating = ("import time\nend=time.monotonic()+8\n"
                   "n=0\nwhile time.monotonic()<end:\n"
                   " n=(n*n+1)%1_000_000_007")
    state = managed_process_scope.start([sys.executable, '-c', calculating])
    manager.cancel(state['process_id'])
    result = completed(manager, state['process_id'])
    assert result['status'] == 'cancelled' and result['exit_code'] != 0
    timed = managed_process_scope.start([sys.executable, '-c', calculating], timeout=.2)
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
    assert budget_reason(state, {'agent_budget': {'steps': 120}}) is None
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
    unrelated = tool.processes.start([sys.executable, '-c', 'print("syntax check only")'])
    assert completed(tool.processes, unrelated['process_id'])['status'] == 'completed'
    with pytest.raises(ValueError, match='bound to this metrics file'):
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
    tool.dispatch('write_file', {'path': 'metrics.json', 'content': '{"squared_sum": 0}'}, None)
    with pytest.raises(ValueError, match='bound to this metrics file'):
        collect_agent_metrics(tmp_path, {}, tool.processes)


def test_nonmetric_research_output_does_not_require_process_execution(tmp_path):
    from research.agents.runtime import ToolRuntime, collect_agent_metrics
    tool = ToolRuntime('local-report-completion-check', tmp_path, allowed=['write_file'])
    tool.dispatch('write_file', {'path': 'source_review.md', 'content': 'This is a source review, not an experimental measurement.'}, None)
    assert collect_agent_metrics(tmp_path, {}, tool.processes) == {}
