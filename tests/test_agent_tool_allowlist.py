"""Node tool allowlists constrain both provider schemas and runtime dispatch."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from research.agents.policy import effective_agent_tools
from tests.test_worker import Harness

ROOT = Path(__file__).resolve().parents[1]


def test_node_tools_are_a_ceiling_within_role_tools():
    role_tools = ['read_file', 'write_file', 'finish']
    assert effective_agent_tools(role_tools, {'tools': ['read_file', 'finish']}) == ['read_file', 'finish']
    assert effective_agent_tools(role_tools, {'tools': ['write_file', 'read_file', 'finish']}) == ['write_file', 'read_file', 'finish']
    assert effective_agent_tools(role_tools, {}) == role_tools
    assert effective_agent_tools(role_tools, {'tools': []}) == []


def test_queued_run_tool_allowlist_stays_on_saved_node_revision(tmp_path):
    requests = []

    class Responses(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            if len(requests) == 1:
                name = 'read_file'
                arguments = {'path': 'allowed.txt', 'offset': 0, 'limit': 1000}
            else:
                name = 'finish'
                arguments = {'summary': 'Read the configured input and completed.', 'artifacts': ['allowed.txt']}
            body = json.dumps({
                'id': 'local-response-' + str(len(requests)),
                'model': 'local-allowlist-contract',
                'status': 'completed',
                'output': [{'type': 'function_call', 'status': 'completed', 'name': name,
                            'call_id': 'local-call-' + str(len(requests)),
                            'arguments': json.dumps(arguments)}],
                'usage': {'input_tokens': 20, 'output_tokens': 4},
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Responses)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    harness = Harness(tmp_path)
    role = 'Tool Allowlist Verifier'
    try:
        harness.start_api()
        provider = harness.request('POST', '/api/providers', json={
            'name': 'Local allowlist test server', 'kind': 'openai',
            'base_url': f'http://127.0.0.1:{server.server_port}/v1',
            'model': 'local-allowlist-contract', 'allow_paid': False,
            'config': {'api': 'responses', 'max_retries': 0},
        })
        harness.request('POST', '/api/agents', json={
            'name': role, 'role': role,
            'instructions': 'Read the supplied file, then finish.',
            'tools': ['read_file', 'write_file', 'finish'],
        })
        project = harness.request('POST', '/api/projects', json={
            'name': 'Node tool allowlist ' + str(uuid.uuid4())[:8],
            'goal': 'Read the supplied file and finish.',
            'config': {'provider_id': provider['id']},
            'budget': {'max_runs': 3, 'seconds': 60, 'allow_paid': False},
        })
        graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        node_id = str(uuid.uuid4())
        response = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
            'operation': 'add_node', 'targets': [],
            'params': {
                'id': node_id, 'branch_id': graph['branches'][0]['id'],
                'type': 'experiment', 'title': 'Read-only configured agent',
                'instructions': 'Read allowed.txt and finish without editing files.',
                'config': {'kind': 'agent', 'role': role, 'tools': ['read_file', 'finish'],
                           'required_outputs': ['allowed.txt'], 'agent_budget': {'steps': 4}, 'timeout': 30},
            },
        })
        node = next(item for item in response['graph']['nodes'] if item['id'] == node_id)
        run = harness.launch(node)
        assert run['config']['tools'] == ['read_file', 'finish']
        latest_graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        edited = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': str(uuid.uuid4()), 'expected_revision': latest_graph['revision'],
            'operation': 'edit_node', 'targets': [node_id],
            'params': {'config': {'tools': ['write_file', 'read_file', 'finish']}},
        })
        current_node = next(item for item in edited['graph']['nodes'] if item['id'] == node_id)
        assert current_node['config']['tools'] == ['write_file', 'read_file', 'finish']
        assert harness.run(run)['status'] == 'queued'
        workspace = harness.output(run) / 'workspace'
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / 'allowed.txt').write_text('Configured read-only input')
        harness.start_worker()
        completed = harness.terminal(run, timeout=30)
        assert completed['status'] == 'completed', completed

        offered = [tool['name'] for tool in requests[0]['tools']]
        assert offered == ['read_file', 'finish', 'read_context_segment']
        assert all([tool['name'] for tool in request['tools']] == offered for request in requests)
        session = json.loads((workspace / 'agent_session.json').read_text())
        assert session['context_history'][-1]['enabled_tools'] == offered
        assert session['transcript'][0]['tool_result']['content'] == 'Configured read-only input'
        assert session['transcript'][-1]['executed_action']['tool'] == 'finish'

        code = """
import json, sys
from pathlib import Path
from research.agents.runtime import ToolRuntime
run_id, workspace = sys.argv[1], Path(sys.argv[2])
session = json.loads((workspace / 'agent_session.json').read_text())
tool = ToolRuntime(run_id, workspace, allowed=session['context_history'][-1]['enabled_tools'])
result = tool.execute('write_file', {'path': 'blocked.txt', 'content': 'must not exist'})
assert result['exit_code'] == 1 and 'not enabled' in result['error']
assert not (workspace / 'blocked.txt').exists()
print(json.dumps(result))
"""
        denied = subprocess.run(
            [sys.executable, '-c', code, run['id'], str(workspace)],
            cwd=ROOT,
            env=harness.env, capture_output=True, text=True, timeout=15,
        )
        assert denied.returncode == 0, denied.stdout + denied.stderr
    finally:
        harness.cleanup()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
