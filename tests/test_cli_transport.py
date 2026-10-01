"""Transport, secret handling, atomic downloads, and project binding contracts."""
from argparse import Namespace
import json
from pathlib import Path
import stat

import httpx
import pytest

from forest_cli.client import CliError, ForestClient
from forest_cli.config import config_path, resolve_context, save_connection, save_project_binding
from forest_cli.output import emit, redact, report_error


@pytest.fixture(autouse=True)
def isolated_cli_configuration(tmp_path, monkeypatch):
    for name in ("FOREST_SERVER", "FOREST_OWNER_TOKEN", "FOREST_TOKEN_FILE", "FOREST_PROJECT_ID",
                 "FOREST_CLI_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


def options(**kwargs):
    return Namespace(server=None, token_file=None, project=None, config=None, **kwargs)


def test_auth_request_id_and_no_retry():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer local-owner-secret"
        assert request.headers["x-request-id"] == "logical-start"
        assert json.loads(request.content) == {"scope": "self", "request_id": "logical-start"}
        assert request.url.path == "/api/nodes/n1/run"
        assert request.url.params["mode"] == "test"
        return httpx.Response(200, json={"id": "run-1", "status": "queued"})

    body = {"scope": "self"}
    with ForestClient("http://localhost:8000/", "local-owner-secret",
                      transport=httpx.MockTransport(handle)) as client:
        result = client.request("POST", "/api/nodes/n1/run", json=body,
                                params={"mode": "test"}, request_id="logical-start")
    assert result["id"] == "run-1"
    assert body == {"scope": "self"}
    assert len(requests) == 1


@pytest.mark.parametrize("status,exit_code", [(401, 3), (403, 3), (409, 4), (422, 1), (503, 1)])
def test_api_error_keeps_code_message_and_suggestion(status, exit_code, capsys):
    def handle(request):
        return httpx.Response(status, json={"detail": {"code": "revision_conflict",
            "message": "Reload local-owner-secret", "suggestion": "Read the current graph.",
            "current_revision": 7, "input": {"token": "local-owner-secret"}}})

    with ForestClient("http://localhost:8000", "local-owner-secret",
                      transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CliError) as caught:
            client.request("PATCH", "/api/projects/p1", json={"goal": "private goal"})
    assert caught.value.code == "revision_conflict"
    assert caught.value.exit_code == exit_code
    assert caught.value.suggestion == "Read the current graph."
    assert caught.value.details["current_revision"] == 7
    report_error(caught.value, json_output=True)
    output = capsys.readouterr()
    assert output.out == ""
    assert "local-owner-secret" not in output.err and "private goal" not in output.err
    assert json.loads(output.err)["error"]["code"] == "revision_conflict"


def test_transport_failure_never_retries_or_exposes_request_body():
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ConnectError("URL and token should never appear in a CLI error", request=request)

    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CliError, match="Could not reach") as caught:
            client.request("POST", "/api/projects", json={"goal": "private research"})
    assert len(requests) == 1
    assert caught.value.code == "CONNECTION_ERROR"
    assert "private research" not in str(caught.value.to_dict())


def test_observation_deadline_reduces_but_never_extends_http_timeout():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "running"})

    with ForestClient("http://localhost:8000", timeout=2,
                      transport=httpx.MockTransport(handle)) as client:
        client.request("GET", "/api/runs/r1", timeout=.4)
        client.request("GET", "/api/runs/r1", timeout=9)
    assert requests[0].extensions["timeout"] == {key: .4 for key in ("connect", "read", "write", "pool")}
    assert requests[1].extensions["timeout"] == {key: 2 for key in ("connect", "read", "write", "pool")}


def test_schema_validation_error_retains_fields_without_request_input():
    response = httpx.Response(422, json={"detail": [{"type": "missing",
        "loc": ["body", "expected_revision"], "msg": "Field required",
        "input": {"goal": "private research", "api_key": "private-key"}}]})
    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(CliError) as caught:
            client.request("POST", "/api/projects", json={"goal": "private research"})
    assert caught.value.code == "VALIDATION_ERROR"
    assert "body.expected_revision" in caught.value.message
    assert caught.value.details["validation_errors"][0]["msg"] == "Field required"
    assert "private research" not in str(caught.value.to_dict())
    assert "private-key" not in str(caught.value.to_dict())


@pytest.mark.parametrize("failure", ["network", "invalid_json"])
def test_unconfirmed_mutation_keeps_existing_request_id_without_retry(failure):
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "network":
            raise httpx.ReadError("Response lost after acceptance", request=request)
        return httpx.Response(200, text="invalid response")

    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CliError) as caught:
            client.request("POST", "/api/nodes/n1/run", json={"request_id": "same-submission", "scope": "single"})
    assert len(requests) == 1
    assert caught.value.details["request_id"] == "same-submission"
    assert "unconfirmed" in caught.value.suggestion
    assert '--request-id "same-submission"' in caught.value.suggestion
    assert "do not use a new ID" in caught.value.suggestion


@pytest.mark.parametrize("method,payload", [("GET", {"request_id": "read-only"}),
                                           ("POST", {"scope": "single"})])
def test_transport_failure_does_not_invent_recovery_ids(method, payload):
    def handle(request):
        raise httpx.ConnectError("Offline", request=request)

    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CliError) as caught:
            client.request(method, "/api/runs/r1", json=payload)
    assert "request_id" not in caught.value.details
    assert "--request-id" not in caught.value.suggestion


def test_recovery_diagnostic_redacts_credentials_and_quotes_control_characters(capsys):
    def handle(request):
        raise httpx.ReadError("private-owner", request=request)

    with ForestClient("http://localhost:8000", "private-owner", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CliError) as caught:
            client.request("POST", "/api/nodes/n1/run", json={"request_id": "private-owner\nmalformed-id",
                                                              "api_key": "provider-secret"})
    report_error(caught.value)
    output = capsys.readouterr()
    assert "private-owner" not in output.err and "provider-secret" not in output.err
    assert '\\nmalformed-id' in output.err
    assert len(output.err.splitlines()) == 2


@pytest.mark.parametrize("endpoint", ["file:///tmp/server", "ftp://localhost", "localhost:8000",
    "http://alice:secret@localhost", "http://localhost/?token=secret", "https://localhost/#token",
    "http://localhost:bad", "http://local host", ""])
def test_invalid_endpoint_is_rejected_without_echoing_credentials(endpoint):
    with pytest.raises(CliError) as caught:
        ForestClient(endpoint)
    assert caught.value.code == "INVALID_SERVER"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf"), float("nan"), "30"])
def test_invalid_timeout_is_rejected_before_any_request(timeout):
    with pytest.raises(CliError) as caught:
        ForestClient("http://localhost:8000", timeout=timeout)
    assert caught.value.code == "INVALID_TIMEOUT"


def test_external_path_is_rejected_and_redirect_is_not_followed():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://other.invalid/"})

    with ForestClient("http://localhost:8000", token="secret", transport=httpx.MockTransport(handle)) as client:
        for path in ("https://other.invalid/", "//other.invalid/", "/\\other.invalid/"):
            with pytest.raises(CliError) as caught:
                client.request("GET", path)
            assert caught.value.code == "INVALID_API_PATH"
        with pytest.raises(CliError) as caught:
            client.request("GET", "/api/health")
        assert caught.value.details["status_code"] == 307
    assert len(requests) == 1


def test_empty_and_invalid_success_responses():
    responses = iter([httpx.Response(204), httpx.Response(200, text="<html>Not the API</html>")])
    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(lambda _: next(responses))) as client:
        assert client.request("DELETE", "/api/projects/p1") is None
        with pytest.raises(CliError) as caught:
            client.request("GET", "/api/health")
    assert caught.value.code == "INVALID_RESPONSE"


def test_download_is_atomic_and_refuses_overwrite(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b"archive bytes", headers={"Content-Type": "application/zip"})

    target = tmp_path / "project.zip"
    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(handle)) as client:
        result = client.download("GET", "/api/projects/p1/export", target)
        assert result == {"path": str(target), "bytes": 13, "content_type": "application/zip"}
        with pytest.raises(CliError) as caught:
            client.download("GET", "/api/projects/p1/export", target)
        assert caught.value.code == "FILE_EXISTS"
        assert len(requests) == 1  # Fail before initiating another export.
        client.download("GET", "/api/projects/p1/export", target, overwrite=True)
    assert target.read_bytes() == b"archive bytes"
    assert list(tmp_path.iterdir()) == [target]


def test_failed_stream_preserves_existing_destination_and_removes_partial(tmp_path):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial archive"
            raise httpx.ReadError("Connection closed during download")

    target = tmp_path / "project.zip"
    target.write_bytes(b"previous archive")
    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=BrokenStream()))) as client:
        with pytest.raises(CliError) as caught:
            client.download("GET", "/api/projects/p1/export", target, overwrite=True)
    assert caught.value.code == "CONNECTION_ERROR"
    assert target.read_bytes() == b"previous archive"
    assert list(tmp_path.iterdir()) == [target]


def test_download_does_not_overwrite_a_concurrently_created_file(tmp_path):
    target = tmp_path / "project.zip"

    class RacingStream(httpx.SyncByteStream):
        def __iter__(self):
            target.write_bytes(b"another export")
            yield b"new archive"

    with ForestClient("http://localhost:8000", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=RacingStream()))) as client:
        with pytest.raises(CliError) as caught:
            client.download("GET", "/api/projects/p1/export", target)
    assert caught.value.code == "FILE_EXISTS"
    assert target.read_bytes() == b"another export"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("download", [False, True])
def test_response_error_redacts_submitted_credentials(tmp_path, capsys, download):
    body = {"config": {"env": {"OPENAI_API_KEY": "submitted-provider-secret"},
                       "headers": {"Authorization": "Bearer submitted-header-secret"}},
            "password": "submitted-password"}

    def handle(request):
        return httpx.Response(422, json={"detail": {"code": "INVALID_CONFIG",
            "message": "Failed: submitted-provider-secret / submitted-header-secret / submitted-password",
            "suggestion": "Replace owner-secret and submitted-provider-secret.",
            "OPENAI_API_KEY": "submitted-provider-secret", "input": body}})

    with ForestClient("http://localhost:8000", "owner-secret",
                      transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CliError) as caught:
            if download:
                client.download("POST", "/api/export", tmp_path / "result.zip", json=body)
            else:
                client.request("POST", "/api/projects", json=body)
    report_error(caught.value, json_output=True)
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["code"] == "INVALID_CONFIG"
    for secret in ["owner-secret", "submitted-provider-secret", "submitted-header-secret", "submitted-password"]:
        assert secret not in output.err
    assert list(tmp_path.iterdir()) == []


def test_download_metadata_does_not_echo_owner_token(tmp_path):
    response = httpx.Response(200, content=b"file bytes",
                             headers={"content-type": "application/octet-stream; owner=owner-secret"})
    with ForestClient("http://localhost:8000", "owner-secret",
                      transport=httpx.MockTransport(lambda _: response)) as client:
        result = client.download("GET", "/api/export", tmp_path / "result.zip")
    assert "owner-secret" not in json.dumps(result)
    assert (tmp_path / "result.zip").read_bytes() == b"file bytes"


def test_configuration_defaults(tmp_path):
    context = resolve_context(options(), tmp_path)
    assert context.endpoint == "http://127.0.0.1:8000"
    assert context.project_id is None and context.token is None


def test_nearest_binding_and_configuration_precedence(tmp_path, monkeypatch):
    global_path = tmp_path / "custom.json"
    global_path.write_text(json.dumps({"endpoint": "http://global:8000", "project_id": "global-project"}))
    monkeypatch.setenv("FOREST_CLI_CONFIG", str(global_path))
    repository = tmp_path / "repo"
    repository.mkdir()
    save_project_binding("root-project", "http://bound:8000/", repository)
    nested = repository / "nested"
    nested.mkdir()
    save_project_binding("near-project", "http://nearest:8000/", nested)
    cwd = nested / "src"
    cwd.mkdir()
    context = resolve_context(options(), cwd)
    assert context.endpoint == "http://nearest:8000" and context.project_id == "near-project"
    monkeypatch.setenv("FOREST_SERVER", "http://env:8000")
    assert resolve_context(options(), cwd).project_id is None
    monkeypatch.setenv("FOREST_PROJECT_ID", "env-project")
    assert resolve_context(options(), cwd).project_id == "env-project"
    args = options()
    args.server, args.project = "http://flag:8000", "flag-project"
    assert resolve_context(args, cwd).endpoint == "http://flag:8000"
    assert resolve_context(args, cwd).project_id == "flag-project"


def test_token_file_precedence_and_no_credentials_cross_servers(tmp_path, monkeypatch):
    token_file = tmp_path / "owner-token"
    token_file.write_text("saved-owner-token\n")
    path = tmp_path / "cli.json"
    save_connection("http://saved:8000", token_file, path)
    monkeypatch.setenv("FOREST_CLI_CONFIG", str(path))
    assert resolve_context(options(), tmp_path).token == "saved-owner-token"
    monkeypatch.setenv("FOREST_SERVER", "http://another:8000")
    assert resolve_context(options(), tmp_path).token is None
    monkeypatch.setenv("FOREST_OWNER_TOKEN", "env-owner-token")
    assert resolve_context(options(), tmp_path).token == "env-owner-token"
    args = options()
    args.token_file = token_file
    context = resolve_context(args, tmp_path)
    assert context.token == "saved-owner-token"
    assert "saved-owner-token" not in repr(context)


def test_relative_config_token_path_is_relative_to_config_directory(tmp_path, monkeypatch):
    folder = tmp_path / "settings"
    folder.mkdir()
    (folder / "token.txt").write_text("relative-token")
    config = folder / "cli.json"
    config.write_text(json.dumps({"endpoint": "http://localhost:8000", "token_file": "token.txt"}))
    monkeypatch.setenv("FOREST_CLI_CONFIG", str(config))
    assert resolve_context(options(), tmp_path).token == "relative-token"


def test_config_saves_only_token_reference_with_private_permissions(tmp_path):
    token_file = tmp_path / "owner-token"
    token_file.write_text("sensitive-content")
    path = save_connection("http://localhost:8000/", token_file)
    assert path == config_path()
    data = json.loads(path.read_text())
    assert data == {"endpoint": "http://localhost:8000", "token_file": str(token_file)}
    assert "sensitive-content" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    save_connection("http://different:8000", path=path)
    assert json.loads(path.read_text()) == {"endpoint": "http://different:8000"}


@pytest.mark.parametrize("content", ["{invalid", "[]", '{"endpoint": "file:///private"}'])
def test_malformed_config_is_an_actionable_error(tmp_path, content):
    path = tmp_path / "cli.json"
    path.write_text(content)
    args = options()
    args.config = path
    with pytest.raises(CliError) as caught:
        resolve_context(args, tmp_path)
    assert caught.value.exit_code == 2


def test_json_output_and_errors_have_separate_streams(capsys):
    value = {"goal": "研究证据", "runs": [{"id": "r1", "status": "queued"}]}
    emit(value, json_output=True)
    report_error(CliError("Budget reached", "BUDGET_EXCEEDED", details={"suggestion": "Inspect the budget."}),
                 json_output=True)
    output = capsys.readouterr()
    assert json.loads(output.out) == value
    assert json.loads(output.err)["error"]["suggestion"] == "Inspect the budget."


def test_human_list_retains_full_identifiers(capsys):
    identifier = "2ac2c18e-806f-4e46-9f07-aabdd3417a1f"
    emit([{"id": identifier, "name": "Research", "status": "running", "goal": "x" * 200}])
    output = capsys.readouterr()
    assert identifier in output.out and "running" in output.out
    assert "x" * 200 not in output.out
    assert output.err == ""


def test_redaction_preserves_nonsecret_environment_and_token_accounting():
    value = {"config": {"provider_snapshot": {"credential_ref": "private-provider-reference", "model": "model-x"},
                        "env": {"OPENAI_API_KEY": "private-key", "HF_TOKEN": "private-token", "DATA_PATH": "data/train.csv"}},
             "usage": {"input_tokens": 123, "output_tokens": 45, "tokens": 168},
             "nested": [{"Authorization": "Bearer private-owner", "Password": "private-password", "value": 2}],
             "goal": "Explain token accounting"}
    result = redact(value)
    assert result["config"]["env"] == {"OPENAI_API_KEY": "[redacted]", "HF_TOKEN": "[redacted]", "DATA_PATH": "data/train.csv"}
    assert result["config"]["provider_snapshot"] == {"credential_ref": "[redacted]", "model": "model-x"}
    assert result["usage"] == {"input_tokens": 123, "output_tokens": 45, "tokens": 168}
    assert result["goal"] == value["goal"]
    assert value["config"]["env"]["HF_TOKEN"] == "private-token"
    assert result["nested"][0]["Password"] == "[redacted]"


@pytest.mark.parametrize("json_output", [False, True])
def test_emit_masks_credentials_in_human_and_json_output(capsys, json_output):
    value = {"config": {"owner_token": "private-owner-token", "api_key": "private-api-key",
                        "env": {"OTHER_API_KEY": "private-env-key", "SEED": "42"}}}
    emit(value, json_output=json_output)
    output = capsys.readouterr()
    assert "private-owner-token" not in output.out
    assert "private-api-key" not in output.out
    assert "private-env-key" not in output.out
    assert "42" in output.out
    if json_output:
        assert json.loads(output.out) == redact(value)
