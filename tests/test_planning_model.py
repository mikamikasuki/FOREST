"""Offline structural checks; no provider requests or research results are made."""
import json
from pathlib import Path

import pytest

from research.agents.context_store import ContextStore
from research.agents.budget import BudgetExceeded
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

    def __init__(self, root, *, early_submit=False, reject_direct=False, direct_text='{"ok":true}', repair_text=None, repair_error=None):
        self.root, self.early_submit, self.reject_direct = root, early_submit, reject_direct
        self.direct_text, self.repair_text, self.repair_error, self.requests = direct_text, repair_text, repair_error, []

    def complete(self, messages, json_mode=True, tools=None):
        self.requests.append({'messages': messages, 'tools': tools})
        number = len(self.requests)
        assert number < 200  # Test-harness guard, not a runtime limit.
        if self.reject_direct:
            self.reject_direct = False
            raise ProviderError('Explicit request-window rejection', code='context_window_exceeded')
        if number == 2 and self.repair_error is not None:
            raise self.repair_error
        if tools is None:
            text = self.repair_text if number > 1 and self.repair_text is not None else self.direct_text
            return {'text': text, 'model': 'offline-protocol-driver', 'usage': {'cost': 0}, 'elapsed': 0}
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


class TruncatedOllamaDriver:
    api = 'ollama'
    base = 'http://127.0.0.1:11434'
    native_tools = False

    def __init__(self, cap=1024, *, error=None):
        self.config = {'max_output_tokens': cap, 'context_length': 8192}
        self.error = error or ProviderError('Local response truncated', code='incomplete_response',
            incomplete_reason='max_output_tokens', partial_output='private partial JSON',
            usage={'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 0,
                   'reasoning_tokens': 0, 'cost': None})
        self.requests = []
        self.request_caps = []

    def complete(self, messages, json_mode=True, tools=None):
        self.requests.append(messages)
        self.request_caps.append(self.config['max_output_tokens'])
        if len(self.requests) == 1:
            raise self.error
        return {'text': '{"ideas":[]}', 'model': 'offline-local-model',
                'usage': {'input_tokens': 20, 'output_tokens': 8, 'cached_input_tokens': 0,
                          'reasoning_tokens': 0, 'cost': None}, 'elapsed': .25}


def test_provider_rejected_controls_are_paged_and_early_submission_is_rejected(tmp_path):
    goal = 'A mandatory original constraint. ' * 1500 + 'FINAL_UNSHORTENED_GOAL_CONDITION'
    driver = ProtocolDriver(tmp_path, early_submit=True, reject_direct=True)
    result, response = planning_model_json(driver, context(goal), tmp_path, system='Read the original research goal.', char_hint=4000)
    assert result['rationale'] == 'Protocol test only'
    directory = Path(response['context_session_path'])
    session = json.loads((directory / 'session.json').read_text())
    assert session['format_errors'][0]['next_required']
    assert len(driver.requests) > 4
    assert ContextStore(directory).pending() is None
    assert response['model_requests'] == len(driver.requests)
    assert response['usage']['input_tokens'] == len(driver.requests) - 1
    assert goal in json.dumps(driver.requests[0]['messages'])
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


@pytest.mark.parametrize(
    'wrapped',
    [
        'Here is the result:\n{"ideas": []}\nDone.',
        '```json\n{"ideas": []}\n```',
    ],
)
def test_small_document_accepts_local_model_json_wrapped_in_prose(tmp_path, wrapped):
    driver = ProtocolDriver(tmp_path, direct_text=wrapped)
    result, response = context_model_json(
        driver,
        [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'Propose ideas.'}],
        tmp_path,
    )
    assert result == {'ideas': []}
    assert response['model_requests'] == 1


def test_local_output_truncation_retries_once_and_aggregates_usage(tmp_path):
    driver = TruncatedOllamaDriver()
    result, response = context_model_json(
        driver,
        [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'Propose ideas.'}],
        tmp_path,
        output_retry_attempts=1,
    )
    assert result == {'ideas': []}
    assert driver.request_caps == [1024, 2048]
    assert response['model_requests'] == 2
    assert response['output_recovery_attempts'] == 1
    assert response['usage']['input_tokens'] == 30
    assert response['usage']['output_tokens'] == 13
    assert response['usage']['cost'] is None
    assert 'private partial JSON' not in json.dumps(driver.requests[1])
    assert 'complete JSON response from the beginning' in driver.requests[1][-1]['content']


def test_local_output_retry_respects_cap_and_ignores_ambiguous_truncation(tmp_path):
    capped = TruncatedOllamaDriver(cap=32768)
    with pytest.raises(ProviderError):
        context_model_json(capped, [{'role': 'user', 'content': 'Return JSON.'}], tmp_path,
                           output_retry_attempts=1)
    assert capped.request_caps == [32768]

    ambiguous = TruncatedOllamaDriver(error=ProviderError(
        'Unknown completion outcome', code='incomplete_response',
        incomplete_reason='max_output_tokens', ambiguous=True))
    with pytest.raises(ProviderError, match='Unknown completion outcome'):
        context_model_json(ambiguous, [{'role': 'user', 'content': 'Return JSON.'}], tmp_path,
                           output_retry_attempts=1)
    assert ambiguous.request_caps == [1024]


def test_invalid_direct_json_gets_one_bounded_repair_request(tmp_path):
    driver = ProtocolDriver(
        tmp_path,
        direct_text='{"commands":[{"operation":"add_node"}]]}',
        repair_text='{"commands":[{"operation":"add_node"}],"rationale":"repaired"}',
    )
    parsed, response = context_model_json(
        driver,
        [{'role': 'system', 'content': 'Return a JSON object.'}, {'role': 'user', 'content': 'Suggest one path.'}],
        tmp_path,
    )
    assert parsed == {'commands': [{'operation': 'add_node'}], 'rationale': 'repaired'}
    assert response['model_requests'] == 2
    assert response['format_repair_attempts'] == 1
    assert len(driver.requests) == 2
    session = Path(response['context_session_path'])
    assert json.loads((session / 'direct_response.json').read_text())['text'].endswith(']]}')
    assert json.loads((session / 'direct_repair_response.json').read_text())['text'] == response['text']


def test_invalid_direct_json_repair_failure_keeps_both_responses(tmp_path):
    driver = ProtocolDriver(tmp_path, direct_text='{"commands":[}', repair_text='not json')
    with pytest.raises(ValueError, match='invalid JSON twice'):
        context_model_json(
            driver,
            [{'role': 'system', 'content': 'Return a JSON object.'}, {'role': 'user', 'content': 'Suggest one path.'}],
            tmp_path,
        )
    session = next((tmp_path / 'context_sessions').iterdir())
    assert (session / 'direct_response.json').is_file()
    assert (session / 'direct_repair_response.json').is_file()
    assert json.loads((session / 'direct_repair_failure.json').read_text())['error']


def test_repair_budget_failure_remains_recoverable(tmp_path):
    driver = ProtocolDriver(
        tmp_path,
        direct_text='{"commands":[}',
        repair_error=BudgetExceeded('request budget exhausted'),
    )

    with pytest.raises(BudgetExceeded, match='request budget exhausted'):
        context_model_json(
            driver,
            [{'role': 'system', 'content': 'Return a JSON object.'}, {'role': 'user', 'content': 'Suggest one path.'}],
            tmp_path,
        )

    session = next((tmp_path / 'context_sessions').iterdir())
    assert json.loads((session / 'direct_repair_failure.json').read_text())['error'] == 'request budget exhausted'


def test_repair_context_rejection_enters_managed_context_recovery(tmp_path):
    driver = ProtocolDriver(
        tmp_path,
        direct_text='{"commands":[}',
        repair_error=ProviderError(
            'Repair request exceeded provider context window',
            code='context_window_exceeded',
        ),
    )
    proposal, response = context_model_json(
        driver,
        [{'role': 'system', 'content': 'Return a JSON object.'}, {'role': 'user', 'content': 'Suggest one path.'}],
        tmp_path,
    )

    assert proposal['rationale'] == 'Protocol test only'
    assert response['model_requests'] == len(driver.requests) > 2
    directory = Path(response['context_session_path'])
    session = json.loads((directory / 'session.json').read_text())
    assert session['context_recovery_history'][0]['reason'].startswith('Explicit provider context-window rejection')
    assert session['failures'][-1]['code'] == 'context_window_exceeded'
    assert session['model_requests'] == len(driver.requests)


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
