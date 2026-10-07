from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from services.api.db import TaskRun
from services.api.verification import _lock_verification_run


class LockSession:
    def __init__(self, dialect):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.events = []
        self.run = SimpleNamespace(project_id='project-id')

    def connection(self):
        return self

    def exec_driver_sql(self, statement):
        self.events.append(statement)

    def get(self, model, ident, **kwargs):
        self.events.append(('get', model, ident))
        return self.run

    def scalar(self, statement):
        self.events.append(('lock-project', statement))

    def refresh(self, run, *, with_for_update):
        self.events.append(('refresh-run', run, with_for_update))


@pytest.mark.parametrize('dialect', ['sqlite', 'postgresql'])
def test_verification_write_locks_project_before_refreshing_run(dialect):
    session = LockSession(dialect)

    run = _lock_verification_run(session, 'run-id')

    assert run is session.run
    names = [event if isinstance(event, str) else event[0] for event in session.events]
    if dialect == 'sqlite':
        assert session.events[0] == 'BEGIN IMMEDIATE'
        names = names[1:]
    assert names == ['get', 'lock-project', 'refresh-run']
    project_lock = next(event[1] for event in session.events if isinstance(event, tuple) and event[0] == 'lock-project')
    if dialect == 'postgresql':
        assert 'FOR UPDATE' in str(project_lock.compile(dialect=postgresql.dialect()))
    assert session.events[-1][2] is True
