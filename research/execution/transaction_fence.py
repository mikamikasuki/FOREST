"""Pause an executor only between its FOREST database transactions."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time


def acquire_session_fence(session, transaction, connection):
    path = os.environ.get('FOREST_EXECUTOR_TRANSACTION_FENCE')
    if not path or session.info.get('_executor_transaction_fence') is not None:
        return
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
    except BaseException:
        os.close(descriptor)
        raise
    session.info['_executor_transaction_fence'] = descriptor


def release_session_fence(session, transaction):
    if transaction.parent is not None:
        return
    descriptor = session.info.pop('_executor_transaction_fence', None)
    if descriptor is not None:
        os.close(descriptor)


@contextmanager
def pause_boundary(output, timeout=10):
    path = Path(output) / '.executor-transactions.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Executor database transaction has not reached a safe pause boundary')
                time.sleep(.02)
        yield
    finally:
        os.close(descriptor)
