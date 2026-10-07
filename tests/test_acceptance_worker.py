"""Purpose gates with actual producer/checker/consumer processes on both DBs."""
import json
import sys

import pytest
from tests.test_intervention_worker import intervention_harness
from tests.test_research_verification_workflow import add_node, arithmetic_command, verification_config
from tests.test_worker import wait_until


@pytest.mark.parametrize('purpose', ['raw_data', 'comparison', 'major_claim'])
def test_actual_purpose_handoff_and_changed_evidence(intervention_harness, purpose):
    h = intervention_harness
    h.start_api(); h.start_worker()
    project = h.request('POST', '/api/projects', json={'name': 'Purpose-bound numerical handoff',
        'mode': 'manual', 'goal': 'Check arithmetic protocol admission',
        'budget': {'max_runs': 30, 'seconds': 180, 'allow_paid': False}})
    signature = {'dataset': 'integer-sequence', 'dataset_version': '1', 'split': 'fixed-1-to-500',
        'evaluation_protocol': 'integer-square-sum', 'metric': 'score', 'statistical_unit': 'integer',
        'budget': {'evaluations': 500}}
    sources = []
    for index in range(3 if purpose == 'major_claim' else 2 if purpose == 'comparison' else 1):
        config = {'kind': 'experiment', 'command': arithmetic_command(500),
                  'comparison': signature, 'research_phase': 'confirmation' if index == 2 else 'selection'}
        producer = add_node(h, project, type='experiment', title='Actual independent run '+str(index), config=config)
        if purpose == 'raw_data':
            script = """import csv,json
from pathlib import Path
with Path('data.csv').open('w') as handle:
    writer=csv.writer(handle); writer.writerow(['sample','split','value'])
    for i in range(500): writer.writerow(['sample-'+str(i),'train' if i<400 else 'test',i*i])
Path('units.json').write_text(json.dumps({'units':{'value':'dimensionless'}}))
Path('metrics.json').write_text(json.dumps({'score':sum(i*i for i in range(500))}))
"""
            h.request('PATCH', '/api/nodes/'+producer['id'], json={'config': {**config,
                'command': [sys.executable, '-c', script]}})
            verifier_config = {'kind': 'verification', 'command': arithmetic_command(5), 'verification': {
                'producer_node_id': producer['id'], 'checks': [{'id': 'raw-schema', 'kind': 'data_contract',
                    'source': 'data.csv', 'units_file': 'units.json', 'identity_columns': ['sample'],
                    'columns': {'sample': {'type': 'string', 'nullable': False},
                        'split': {'type': 'string', 'nullable': False},
                        'value': {'type': 'integer', 'nullable': False, 'unit': 'dimensionless'}},
                    'split': {'column': 'split', 'identity_columns': ['sample'],
                              'expected_counts': {'train': 400, 'test': 100}}}]}}
        else: verifier_config = verification_config(producer)
        verifier = add_node(h, project, type='verification', title='Executed distinct checker '+str(index), config=verifier_config)
        source = h.launch(producer); assert h.terminal(source)['status'] == 'completed'
        checked = h.launch(verifier); assert h.terminal(checked)['status'] == 'completed'
        assert h.request('GET', f"/api/runs/{source['id']}/verification")['verification_status'] == 'accepted'
        sources.append(source)
    contract = {'purpose': purpose}
    if purpose == 'raw_data': contract['artifact_paths'] = ['data.csv']
    if purpose == 'major_claim': contract['confirmation_run_ids'] = [sources[2]['id']]
    config = {'kind': 'command', 'command': arithmetic_command(5),
        'run_ids': [source['id'] for source in sources[:2]], 'acceptance_contract': contract}
    consumer = add_node(h, project, type='analysis', title='Purpose-gated use', config=config)
    consumed = h.launch(consumer)
    assert h.terminal(consumed)['status'] == 'completed'
    admitted = h.request('GET', f"/api/runs/{consumed['id']}/acceptance")
    assert admitted['ready'] and admitted['purpose'] == purpose, admitted
    if purpose == 'raw_data':
        actual = h.output(sources[0])/'workspace'/'data.csv'
        rows = actual.read_text().splitlines(); assert len(rows) == 501
        assert sum(int(row.rsplit(',', 1)[1]) for row in rows[1:]) == sum(i*i for i in range(500))
        (h.output(sources[0])/'workspace'/'units.json').write_text(json.dumps({'units': {'value': 'kg'}}))
    elif purpose == 'major_claim':
        h.request('PATCH', f"/api/runs/{sources[2]['id']}/configuration", json={'research_phase': 'selection'})
    else:
        h.request('PATCH', f"/api/runs/{sources[1]['id']}/configuration", json={
            'comparison': {**signature, 'budget': {'evaluations': 1000}}})
    denied = h.request('GET', f"/api/runs/{consumed['id']}/acceptance")
    assert not denied['ready'] and denied['failures'], denied
    queued = h.launch(consumer)
    blocked = wait_until(lambda: (value if (value := h.run(queued))['status'] == 'waiting_input' else None))
    assert blocked['resource']['blocked_reason'] == 'acceptance_contract', blocked
    assert not (h.output(queued)/'workspace'/'metrics.json').exists()
    h.control_request(f"/api/runs/{queued['id']}/cancel", json={})
