"""Verify the recorded request digest against the actual inference child input."""
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

from research.agents.schemas import json_action_schema, decode_tool_call

from research.agents.provider import ModelClient


def test_codex_receipt_hashes_actual_cli_prompt(monkeypatch):
    from research.agents import codex_transport
    provider = {'id':'test-only', 'kind':'codex_cli', 'base_url':'http://127.0.0.1',
                'model':'gpt-6-luna', 'config':{'reasoning_effort':'xhigh'}}
    client = ModelClient(provider, request_guard=lambda event: None)
    messages = [{'role':'system','content':'Current controls'}, {'role':'user','content':'Owner correction 中文'},
                {'role':'assistant','content':'Previous public action'}]
    path, payload = client.build_request(messages)
    recorded = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    captured = []
    def child(argv, **kwargs):
        captured.append(kwargs['input'])
        return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(event) for event in [
            {'type':'item.completed','item':{'type':'agent_message','text':'{"tool":"finish","arguments":{}}'}},
            {'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5,'cached_input_tokens':0}}]), stderr='')
    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value:'/verified/test/codex')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)
    client.complete(messages)
    assert path == 'codex_cli'
    assert hashlib.sha256(captured[0].encode()).hexdigest() == recorded
    assert json.loads(captured[0])['messages'] == messages


def test_codex_action_schema_follows_effective_permissions(monkeypatch):
    from research.agents import codex_transport
    schema = json_action_schema(['read_file', 'finish'])
    client = ModelClient({'id': 'test-only', 'kind': 'codex_cli', 'base_url': 'http://127.0.0.1', 'model': 'gpt-6-luna',
                         'config': {'_forest_action_schema': schema}}, request_guard=lambda event: None)
    messages = [{'role': 'user', 'content': 'Review existing evidence'}]
    path, payload = client.build_request(messages)
    assert payload['response_schema'] == schema
    def child(argv, **kwargs):
        assert json.loads(Path(argv[argv.index('--output-schema') + 1]).read_text()) == schema
        assert json.loads(kwargs['input']) == payload
        assert schema['properties']['tool']['enum'] == ['read_file', 'finish']
        action = {'tool': 'read_file', 'arguments': {'path': 'actual.json', 'offset': None, 'limit': 16000}, 'summary': 'Inspect evidence'}
        decoded = decode_tool_call({'name': action['tool'], 'arguments': action['arguments'], 'call_id': None}, ['read_file', 'finish'])
        assert decoded['arguments'] == {'path': 'actual.json', 'limit': 16000}
        return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(event) for event in [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(action)}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}]), stderr='')
    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value: '/verified/test/codex')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)
    client.complete(messages)
