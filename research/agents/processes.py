"""Real detached command execution with editable state and incremental logs.

A process ID is an ordinary random identifier. No content addressing, artifact
locking, simulated completion, or model service is involved in this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

import psutil

TERMINAL = {'completed', 'failed', 'cancelled', 'lost'}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(uuid.uuid4()) + '.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def is_alive(pid, created):
    if not pid or created is None:
        return False
    try:
        process = psutil.Process(int(pid))
        return abs(process.create_time() - float(created)) < .1 and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


def signal_process_group(process_group_id, signum):
    """Signal an owned command group, even after its original command exits."""
    if not process_group_id:
        return False
    try:
        os.killpg(int(process_group_id), signum)
        return True
    except ProcessLookupError:
        return False


class ManagedProcesses:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.directory = self.workspace / '.forest-processes'
        self.directory.mkdir(parents=True, exist_ok=True)

    def folder(self, process_id):
        if not process_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in str(process_id)):
            raise ValueError('Invalid process ID')
        return self.directory / str(process_id)

    def start(self, command, *, process_id=None, cwd='.', env=None, timeout=None):
        process_id = process_id or str(uuid.uuid4())
        folder = self.folder(process_id)
        folder.mkdir(parents=True, exist_ok=True)
        requested_cwd = (self.workspace / cwd).resolve()
        if not requested_cwd.is_relative_to(self.workspace) or not requested_cwd.is_dir():
            raise ValueError('Process working directory must exist inside the workspace')
        if isinstance(command, str):
            command = ['/bin/sh', '-c', command]
        if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
            raise ValueError('command must be a nonempty argv list or shell command string')
        if timeout is not None and float(timeout) <= 0:
            raise ValueError('timeout must be positive or omitted')
        request = {'process_id': process_id, 'command': command, 'cwd': str(requested_cwd), 'env': env or {}, 'timeout': timeout, 'created_at': time.time()}
        request_path = folder / 'request.json'
        if request_path.exists():
            previous = read_json(request_path)
            if any(previous.get(key) != request.get(key) for key in ('command', 'cwd', 'env', 'timeout')):
                raise ValueError('This process ID already belongs to a different command; choose a new ID')
            state = self.inspect(process_id)
            if state.get('pid') or state['status'] in TERMINAL or is_alive(state.get('host_pid'), state.get('host_created')):
                return state
        else:
            # Persist launch intent before creating any process.
            atomic_json(request_path, request)
        # The supervisor holds a coordination lease for its entire lifetime; a
        # duplicate launcher cannot execute the command twice. This lease never
        # prevents the user editing source code, task state, or output files.
        host = Path(__file__).with_name('process_host.py')
        with (folder / 'supervisor.log').open('ab') as log:
            subprocess.Popen([sys.executable, str(host), str(folder)], cwd=self.workspace,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            state = self.inspect(process_id)
            if state.get('pid') or state['status'] in TERMINAL:
                return state
            time.sleep(.02)
        return state

    def inspect(self, process_id):
        folder = self.folder(process_id)
        request = read_json(folder / 'request.json')
        if not request:
            raise ValueError('Unknown process ID: ' + str(process_id))
        state = read_json(folder / 'state.json', {'status': 'starting'})
        identity = read_json(folder / 'identity.json')
        if identity:
            state.update(identity)
        value = {'process_id': process_id, 'command': request['command'], 'created_at': request['created_at'], **state}
        if value['status'] not in TERMINAL and is_alive(value.get('pid'), value.get('process_created')):
            try:
                proc = psutil.Process(value['pid'])
                value.update(status='paused' if proc.status() == psutil.STATUS_STOPPED else 'running', rss_bytes=proc.memory_info().rss)
            except psutil.NoSuchProcess:
                pass
        elif value['status'] not in TERMINAL and not is_alive(value.get('host_pid'), value.get('host_created')) and time.time() - request['created_at'] > 5:
            value.update(status='lost', error='Process exited without a completion receipt. Validate outputs or explicitly restart; success is unconfirmed.')
        return value

    def read_output(self, process_id, offset=0, limit=24000, stream='stdout'):
        if stream not in ('stdout', 'stderr'):
            raise ValueError('stream must be stdout or stderr')
        self.inspect(process_id)
        path = self.folder(process_id) / (stream + '.log')
        if offset < 0 or not 0 < limit <= 1_000_000:
            raise ValueError('offset must be nonnegative and limit between 1 and 1000000 bytes')
        if not path.exists():
            return {'process_id': process_id, 'stream': stream, 'content': '', 'next_offset': offset}
        with path.open('rb') as handle:
            handle.seek(offset)
            content = handle.read(limit)
            position = handle.tell()
        return {'process_id': process_id, 'stream': stream, 'content': content.decode('utf-8', errors='replace'), 'next_offset': position}

    def cancel(self, process_id):
        state = self.inspect(process_id)
        atomic_json(self.folder(process_id) / 'cancel.json', {'requested_at': time.time()})
        if state['status'] not in TERMINAL:
            group_id = state.get('process_group_id') or state.get('pid')
            signal_process_group(group_id, signal.SIGCONT)
            signal_process_group(group_id, signal.SIGTERM)
        return self.inspect(process_id)

    def all(self):
        return [self.inspect(p.name) for p in sorted(self.directory.iterdir()) if p.is_dir() and (p / 'request.json').exists()]

    def cancel_all(self):
        return [self.cancel(p['process_id']) for p in self.all() if p['status'] not in TERMINAL]

    def signal_all(self, signum):
        for state in self.all():
            if state['status'] not in TERMINAL:
                group_id = state.get('process_group_id') or state.get('pid')
                signal_process_group(group_id, signum)
