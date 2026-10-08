"""Focused controls for worker-owned executor reaping after run-row changes."""

import datetime as dt
import json


class ControlledProcess:
    def __init__(self, *poll_results):
        self.poll_results = iter(poll_results)
        self.poll_calls = 0
        self.returncode = None
        self.terminate_calls = 0

    def poll(self):
        self.poll_calls += 1
        result = next(self.poll_results)
        if result is not None:
            self.returncode = result
        return result

    def terminate(self):
        self.terminate_calls += 1


class QueryResult(list):
    def all(self):
        return list(self)


class QueryRow:
    """Project the selected run identity/status while retaining fixture context."""

    def __init__(self, ident, status, resource=None):
        self.ident = ident
        self.status = status
        self.resource = resource or {}

    def __iter__(self):
        yield self.ident
        yield self.status


class QuerySession:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        return QueryResult(self.rows)

    def scalars(self, statement):
        cancelled = []
        for row in self.rows:
            ident, status = tuple(row)
            if status == "cancelled":
                cancelled.append(ident)
        return QueryResult(cancelled)


class SessionFactory:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        return QuerySession(self.rows)


def make_worker(process):
    from services.worker.main import WorkerLoop

    worker = WorkerLoop.__new__(WorkerLoop)
    worker.id = "owned-worker"
    worker.processes = {"owned-run": process}
    worker.log_offsets = {"owned-run": 37}
    return worker


class MonitorSession:
    def __init__(self, run):
        self.run = run
        self.scalar_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def scalar(self, statement):
        self.scalar_calls += 1
        return self.run

    def get(self, model, ident):
        return None


class MonitorSessionFactory:
    def __init__(self, rows, session):
        self.rows = rows
        self.session = session

    def __call__(self):
        return QuerySession(self.rows)

    def begin(self):
        return self.session


def check_exited_active_monitor_reconciliation(monkeypatch, tmp_path, receipt):
    from services.worker import main
    from services.api import verification

    attempt_id = "attempt-1"
    process = ControlledProcess(0)
    worker = make_worker(process)
    output = tmp_path / "project" / "runs" / "owned-run"
    output.mkdir(parents=True)
    if receipt is not None:
        (output / "result.json").write_text(json.dumps(receipt))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    config = {"execution_attempt": {"id": attempt_id}}
    run = type("Run", (), {})()
    run.id = "owned-run"
    run.project_id = "project"
    run.node_id = None
    run.worker_id = worker.id
    run.status = "running"
    run.pid = 1234
    run.process_created = 1.0
    run.started_at = now
    run.output_path = "runs/owned-run"
    run.kind = "command"
    run.config = config
    run.resource = {}
    run.node_revision = 0
    session = MonitorSession(run)

    monkeypatch.setattr(
        main, "Session", MonitorSessionFactory([("owned-run", "running")], session)
    )
    monkeypatch.setattr(main, "project_dir", lambda ident: tmp_path / "project")
    monkeypatch.setattr(main, "process_matches", lambda *args: False)
    monkeypatch.setattr(main, "_lock_project", lambda session, project_id: None)
    monkeypatch.setattr(main, "begin_sqlite_write", lambda session: None)
    monkeypatch.setattr(main, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "interrupt_run_reservations", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "recovery_decision", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        verification,
        "verification_for_run",
        lambda *args: {"verification_status": "unverified", "verification": {}},
    )
    value = {
        "id": run.id,
        "project_id": run.project_id,
        "node_id": None,
        "worker_id": worker.id,
        "status": "running",
        "pid": run.pid,
        "process_created": run.process_created,
        "started_at": now,
        "output_path": run.output_path,
        "kind": run.kind,
        "config": config,
        "resource": {},
        "node_revision": 0,
    }

    worker.reap_cancelled()
    assert process.poll_calls == 0
    assert worker.processes == {"owned-run": process}
    assert worker.log_offsets == {"owned-run": 37}

    worker.monitor(value)
    assert process.poll_calls == 1
    assert worker.processes == {}
    return run


def test_missing_row_exited_executor_is_reaped_and_both_maps_are_cleaned(monkeypatch):
    from services.worker import main

    process = ControlledProcess(0)
    worker = make_worker(process)
    monkeypatch.setattr(main, "Session", SessionFactory([]))

    worker.reap_cancelled()

    assert process.poll_calls == 1
    assert process.terminate_calls == 0
    assert worker.processes == {}
    assert worker.log_offsets == {}


def test_missing_row_live_executor_keeps_handle_and_offset_until_later_exit(monkeypatch):
    from services.worker import main

    process = ControlledProcess(None, -15)
    worker = make_worker(process)
    monkeypatch.setattr(main, "Session", SessionFactory([]))

    worker.reap_cancelled()
    assert process.poll_calls == 1
    assert worker.processes == {"owned-run": process}
    assert worker.log_offsets == {"owned-run": 37}

    worker.reap_cancelled()
    assert process.poll_calls == 2
    assert worker.processes == {}
    assert worker.log_offsets == {}
    assert process.terminate_calls == 0


def test_cancelled_row_live_executor_keeps_handle_until_exit(monkeypatch):
    from services.worker import main

    process = ControlledProcess(None, 143)
    worker = make_worker(process)
    monkeypatch.setattr(
        main, "Session", SessionFactory([("owned-run", "cancelled")])
    )

    worker.reap_cancelled()
    assert process.poll_calls == 1
    assert worker.processes == {"owned-run": process}
    assert worker.log_offsets == {"owned-run": 37}

    worker.reap_cancelled()
    assert process.poll_calls == 2
    assert worker.processes == {}
    assert worker.log_offsets == {}
    assert process.terminate_calls == 0


def test_active_and_paused_rows_are_not_reaped(monkeypatch):
    from services.worker import main

    for status in ("running", "paused"):
        process = ControlledProcess(0)
        worker = make_worker(process)
        monkeypatch.setattr(
            main, "Session", SessionFactory([("owned-run", status)])
        )

        worker.reap_cancelled()

        assert process.poll_calls == 0
        assert worker.processes == {"owned-run": process}
        assert worker.log_offsets == {"owned-run": 37}
        assert process.terminate_calls == 0


def test_non_cancelled_pending_intervention_handle_is_not_reaped(monkeypatch):
    from services.worker import main

    process = ControlledProcess(0)
    worker = make_worker(process)
    row = QueryRow(
        "owned-run",
        "running",
        {"pending_intervention": {"kind": "review", "status": "pending"}},
    )
    assert row.resource["pending_intervention"]["status"] == "pending"
    monkeypatch.setattr(main, "Session", SessionFactory([row]))

    worker.reap_cancelled()

    assert process.poll_calls == 0
    assert worker.processes == {"owned-run": process}
    assert worker.log_offsets == {"owned-run": 37}
    assert process.terminate_calls == 0


def test_exited_active_handle_remains_for_normal_monitor_reconciliation(monkeypatch):
    from services.worker import main

    process = ControlledProcess(0)
    assert process.poll() == 0
    worker = make_worker(process)
    monkeypatch.setattr(
        main, "Session", SessionFactory([("owned-run", "running")])
    )

    worker.reap_cancelled()

    assert process.poll_calls == 1
    assert worker.processes == {"owned-run": process}
    assert worker.log_offsets == {"owned-run": 37}


def test_exited_active_current_receipt_uses_normal_completion_reconciliation(
    monkeypatch, tmp_path
):
    run = check_exited_active_monitor_reconciliation(
        monkeypatch,
        tmp_path,
        {
            "attempt_id": "attempt-1",
            "status": "completed",
            "exit_code": 0,
            "elapsed_seconds": 0.1,
            "metrics": {"value": 7},
        },
    )

    assert run.status == "completed"
    assert run.exit_code == 0
    assert run.metrics == {"value": 7}


def test_exited_active_without_receipt_uses_normal_interrupted_reconciliation(
    monkeypatch, tmp_path
):
    run = check_exited_active_monitor_reconciliation(monkeypatch, tmp_path, None)

    assert run.status == "interrupted"
    assert run.exit_code == 0
    assert run.error == "Task process ended without a current completion receipt."
