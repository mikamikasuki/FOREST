"""Actual database interruption races; bookkeeping inputs, no model requests."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['cap', 'atomic', 'settlement_race', 'start_race'])
def test_interrupted_reservations(tmp_path, scenario):
    env = {**os.environ, 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'budget.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'), 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, __file__, scenario], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import select, text
    from services.api.db import migrate, Session, Provider, Project, TaskRun, ModelRequest, asdict, uid
    from research.agents.budget import BudgetExceeded, interrupt_run_reservations, make_request_guard, usage_summary

    migrate()
    scenario = sys.argv[1]
    pricing = {'input_per_million': 1, 'output_per_million': 1,
               'cached_input_per_million': .1, 'currency': 'USD'}
    limit = .006147 if scenario == 'cap' else 5
    with Session.begin() as s:
        project = Project(name='Accounting lifecycle test', budget={'allow_paid': True, 'cost_usd': limit})
        provider = Provider(name='Accounting only; no model called', kind='openai',
                            base_url='https://api.openai.com/v1', model='accounting-input',
                            allow_paid=True, config={'budget_usd': limit, 'pricing': pricing})
        s.add_all([project, provider]); s.flush()
        project_id, provider_id = project.id, provider.id
        provider_value = asdict(provider, True)

    def new_run():
        with Session.begin() as s:
            run = TaskRun(project_id=project_id, request_id=uid(), kind='command', status='running')
            s.add(run); s.flush()
            ident = run.id
        snapshot = {**provider_value, '_usage_context': {'project_id': project_id, 'run_id': ident}}
        return ident, make_request_guard(snapshot)

    before = {'phase': 'before', 'model': 'accounting-input', 'input_bytes': 0,
              'max_output_tokens': 1, 'pricing': pricing}

    def settle(guard, reservation):
        # This supplies accounting test values directly to the ledger, not a
        # fabricated model/HTTP response or a claimed task completion.
        return guard({'phase': 'after', 'reservation': reservation,
                      'usage': {'input_tokens': 100, 'output_tokens': 20},
                      'request_id': 'ledger-test-input'})

    def stop_run(ident):
        with Session.begin() as s:
            s.execute(text('BEGIN IMMEDIATE'))
            s.scalar(select(Project).where(Project.id == project_id).with_for_update())
            run = s.scalar(select(TaskRun).where(TaskRun.id == ident).with_for_update())
            run.status = 'cancelled'
            return interrupt_run_reservations(ident, session=s)

    if scenario == 'cap':
        ident, guard = new_run()
        reservations = [guard(before) for _ in range(3)]
        initial = usage_summary(provider_id)
        assert initial['remaining_usd'] == 0
        assert stop_run(ident) == 3
        assert interrupt_run_reservations(ident) == 0
        assert interrupt_run_reservations(None) == 0
        assert interrupt_run_reservations('missing-run') == 0
        interrupted = usage_summary(provider_id)
        assert interrupted['reserved_usd'] == initial['reserved_usd'] == .006147
        assert interrupted['uncertain_requests'] == 3
        other, other_guard = new_run()
        try:
            other_guard(before)
        except BudgetExceeded:
            pass
        else:
            raise AssertionError('Interruption released budget')
        settle(guard, reservations[0])
        settled = usage_summary(provider_id)
        assert settled['estimated_cost_usd'] == .00012
        assert settled['reserved_usd'] == .004098
        assert settled['uncertain_requests'] == 2
        settle(guard, reservations[0])
        assert usage_summary(provider_id) == settled
        assert interrupt_run_reservations(other) == 0
        print('Cap preserved; interruption and late settlement are idempotent')
    elif scenario == 'atomic':
        ident, guard = new_run(); reservation = guard(before)
        try:
            with Session.begin() as s:
                s.get(TaskRun, ident).status = 'failed'
                assert interrupt_run_reservations(ident, session=s) == 1
                raise RuntimeError('Roll back the whole lifecycle transition')
        except RuntimeError:
            pass
        with Session() as s:
            assert s.get(TaskRun, ident).status == 'running'
            assert s.get(ModelRequest, reservation).status == 'reserved'
        stop_run(ident)
        with Session.begin() as s:
            s.delete(s.get(Project, project_id))
        assert usage_summary(provider_id)['uncertain_requests'] == 1
        settle(guard, reservation)
        assert usage_summary(provider_id)['estimated_cost_usd'] == .00012
        assert usage_summary(provider_id)['reserved_usd'] == 0
        print('Lifecycle rollback is atomic; deleted projects retain settleable accounting')
    elif scenario == 'settlement_race':
        for _ in range(24):
            ident, guard = new_run(); reservation = guard(before); barrier = Barrier(2)
            def interrupt():
                barrier.wait(); return interrupt_run_reservations(ident)
            def acknowledge():
                barrier.wait(); return settle(guard, reservation)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(interrupt), pool.submit(acknowledge)]
                for future in futures: future.result(timeout=10)
            with Session() as s:
                record = s.get(ModelRequest, reservation)
                assert record.status == 'settled' and record.estimated_microusd == 120
            assert interrupt_run_reservations(ident) == 0
        assert usage_summary(provider_id)['reserved_usd'] == 0
        print('Concurrent acknowledgments always win over interruption without double accounting')
    elif scenario == 'start_race':
        for _ in range(24):
            ident, guard = new_run(); barrier = Barrier(2)
            def start():
                barrier.wait()
                try: return guard(before)
                except BudgetExceeded: return None
            def stop():
                barrier.wait(); return stop_run(ident)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(start), pool.submit(stop)]
                for future in futures: future.result(timeout=10)
            with Session() as s:
                rows = list(s.scalars(select(ModelRequest).where(ModelRequest.run_id == ident)))
                assert all(row.status == 'uncertain' for row in rows)
            try:
                guard(before)
            except BudgetExceeded:
                pass
            else:
                raise AssertionError('Stopped run created a new request reservation')
        print('A concurrent request is either rejected or retained as uncertain after cancellation')
