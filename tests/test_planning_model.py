"""Offline structural checks; no provider requests or research results are made."""
import json
from pathlib import Path

import pytest

from research.agents.context_store import ContextStore
from research.agents.provider import ProviderError, responses_input
from research.planning.loop import compact_planning_context
from research.planning.model import context_model_json, planning_model_json


def context(goal):
    return {'project': {'id': 'structural-test', 'goal': goal, 'budget': {'cost': 10},
                        'controller': {'status': 'running'}, 'objective': None},
            'graph': {'revision': 7, 'nodes': [], 'edges': [], 'branches': []},
            'runs': [], 'trials': [], 'sources': [], 'claims': []}


def test_soft_hint_never_excerpts_or_rejects_mandatory_goal_and_metadata():
    original = context('Required condition. ' * 15000 + 'FINAL CONDITION MUST REMAIN.')
    original['project']['controller']['required_artifacts'] = ['result-' + str(n) for n in range(5000)]
    selected = compact_planning_context(original, 500)
    assert selected['project'] == original['project']
    assert selected['context_coverage']['mandatory_project_complete'] is True
    assert selected['context_coverage']['exceeds_working_set_hint'] is True
    assert selected['project']['goal'].endswith('FINAL CONDITION MUST REMAIN.')


class ProtocolDriver:
    """Only exercises the transport state machine, never claims model quality."""
    native_tools = True
    config = {}
    api = 'responses'

    def __init__(self, root, *, early_submit=False, reject_direct=False, direct_text='{"ok":true}'):
        self.root, self.early_submit, self.reject_direct = root, early_submit, reject_direct
        self.direct_text, self.requests = direct_text, []

    def complete(self, messages, json_mode=True, tools=None):
        self.requests.append({'messages': messages, 'tools': tools})
        number = len(self.requests)
        assert number < 200  # Test-harness guard, not a runtime limit.
        if tools is None:
            if self.reject_direct:
                self.reject_direct = False
                raise ProviderError('Explicit request-window rejection', code='context_window_exceeded')
            return {'text': self.direct_text, 'model': 'offline-protocol-driver', 'usage': {'cost': 0}, 'elapsed': 0}
        session = next((self.root / 'context_sessions').iterdir())
        pending = ContextStore(session).pending()
        if self.early_submit or not pending:
            self.early_submit = False
            name, arguments = 'submit_result', {'result_json': '{"action":"blocked","rationale":"Protocol test only","commands":[]}'}
        else:
            name, arguments = 'read_context_segment', {**{key: pending[key] for key in ('segment_id', 'offset')},
                                                      'limit': 6000, 'notes': 'Public protocol test notes.'}
        call_id = f'call-{number}'
        output = [{'type': 'function_call', 'name': name, 'arguments': json.dumps(arguments), 'call_id': call_id}]
        return {'text': '', 'output': output, 'tool_calls': [{'name': name, 'arguments': arguments, 'call_id': call_id}],
                'model': 'offline-protocol-driver', 'usage': {'input_tokens': 1, 'output_tokens': 1, 'cost': 0}, 'elapsed': 0}


def test_full_controls_are_paged_and_early_submission_is_rejected(tmp_path):
    goal = 'A mandatory original constraint. ' * 1500 + 'FINAL_UNSHORTENED_GOAL_CONDITION'
    driver = ProtocolDriver(tmp_path, early_submit=True)
    result, response = planning_model_json(driver, context(goal), tmp_path, system='Read the original research goal.', char_hint=4000)
    assert result['rationale'] == 'Protocol test only'
    directory = Path(response['context_session_path'])
    session = json.loads((directory / 'session.json').read_text())
    assert session['format_errors'][0]['next_required']
    assert len(driver.requests) > 4
    assert ContextStore(directory).pending() is None
    assert response['model_requests'] == len(driver.requests)
    assert response['usage']['input_tokens'] == len(driver.requests)
    # The final goal clause actually enters a submitted model request. Presence
    # on disk or a retrieval receipt alone does not satisfy this assertion.
    assert any('FINAL_UNSHORTENED_GOAL_CONDITION' in json.dumps(request['messages']) for request in driver.requests)
    assert goal in (directory / 'original_messages.json').read_text()
    for request in driver.requests:
        seen = set()
        for item in responses_input(request['messages']):
            if item.get('type') == 'function_call':
                seen.add(item['call_id'])
            elif item.get('type') == 'function_call_output':
                assert item['call_id'] in seen


def test_small_document_keeps_the_original_json_request_and_response(tmp_path):
    driver = ProtocolDriver(tmp_path)
    messages = [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'A short document.'}]
    parsed, response = context_model_json(driver, messages, tmp_path)
    assert parsed == {'ok': True}
    assert len(driver.requests) == 1
    assert driver.requests[0] == {'messages': messages, 'tools': None}
    assert json.loads((Path(response['context_session_path']) / 'direct_response.json').read_text())['text'] == '{"ok":true}'


def test_unparsed_direct_response_is_available_for_existing_writer_repair(tmp_path):
    driver = ProtocolDriver(tmp_path, direct_text='{"incomplete_json":')
    parsed, response = context_model_json(driver, [{'role': 'user', 'content': 'Return a draft.'}], tmp_path, parse_result=False)
    assert parsed is None
    assert response['text'] == '{"incomplete_json":'


def test_explicit_provider_window_rejection_rebases_without_dropping_originals(tmp_path):
    driver = ProtocolDriver(tmp_path, reject_direct=True)
    original = [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'Required goal retained.'}]
    result, response = context_model_json(driver, original, tmp_path)
    assert result['action'] == 'blocked'
    directory = Path(response['context_session_path'])
    assert json.loads((directory / 'original_messages.json').read_text()) == original
    assert json.loads((directory / 'direct_failure.json').read_text())['code'] == 'context_window_exceeded'
    assert response['model_requests'] == len(driver.requests)


def test_ambiguous_transport_failure_is_not_retried(tmp_path):
    class Broken:
        native_tools = True
        calls = 0

        def complete(self, messages):
            self.calls += 1
            raise ProviderError('Outcome unknown', code='transport_error', ambiguous=True)

    client = Broken()
    with pytest.raises(ProviderError, match='Outcome unknown'):
        context_model_json(client, [{'role': 'user', 'content': 'Return JSON.'}], tmp_path)
    assert client.calls == 1
