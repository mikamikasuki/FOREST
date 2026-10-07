"""Published API descriptions and the existing HTTP behavior must stay aligned.

Each scenario uses the real FastAPI handlers and an isolated SQLite database.
No worker, remote provider, or scientific experiment is started by these tests.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def resolve(schema, document):
    if not isinstance(schema, dict) or "$ref" not in schema:
        return schema
    value = document
    for part in schema["$ref"].removeprefix("#/").split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def matches(value, schema, document):
    """Check the shape, required fields and unions exposed to API consumers."""
    schema = resolve(schema, document)
    if isinstance(schema, bool):
        assert schema
        return
    if "allOf" in schema:
        for item in schema["allOf"]:
            matches(value, item, document)
    alternatives = schema.get("anyOf", schema.get("oneOf"))
    if alternatives:
        for item in alternatives:
            try:
                matches(value, item, document)
                break
            except AssertionError:
                pass
        else:
            raise AssertionError(f"No schema union accepts {value!r}")
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    types = {"object": dict, "array": list, "string": str, "integer": int,
             "number": (int, float), "boolean": bool, "null": type(None)}
    if kind:
        assert any(isinstance(value, types[item]) and
                   (item not in ("integer", "number") or not isinstance(value, bool))
                   for item in kinds), (value, schema)
    if "enum" in schema:
        assert value in schema["enum"], (value, schema)
    if isinstance(value, dict):
        assert set(schema.get("required", [])) <= value.keys(), (value, schema)
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert value.keys() <= properties.keys(), (value, schema)
        for key, item in value.items():
            if key in properties:
                matches(item, properties[key], document)
    if isinstance(value, list) and "items" in schema:
        for item in value:
            matches(item, schema["items"], document)


def response(client, document, method, template, path=None, status=200, **kwargs):
    result = client.request(method.upper(), path or template, **kwargs)
    assert result.status_code == status, result.text
    payload = result.json()
    contract = document["paths"][template][method]["responses"][str(status)]
    matches(payload, contract["content"]["application/json"]["schema"], document)
    return payload


def project(client, document):
    return response(client, document, "post", "/api/projects", json={
        "name": "API transport compatibility", "mode": "manual",
        "config": {"publication_profile": "operational", "custom_ui": {"editable": True}},
        "budget": {"max_runs": 10, "allow_paid": False}})


def case_schema_registry(client, app):
    from fastapi.openapi.utils import get_openapi
    from fastapi.routing import APIRoute
    from services.api.contracts import enhance_openapi
    from starlette.routing import Match

    document = app.openapi()
    # Newer FastAPI versions retain include_router containers. Use its native
    # traversal for effective paths and matching rather than counting only
    # routes registered directly on the root application.
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:
        contexts = list(app.routes)
    else:
        contexts = list(iter_route_contexts(app.routes))
    api_routes = [route for route in contexts
                  if isinstance(getattr(route, "original_route", route), APIRoute)
                  and route.path.startswith("/api/")]
    raw = get_openapi(title=app.title, version=app.version, routes=app.routes)
    raw["paths"] = {path: item for path, item in raw["paths"].items() if path.startswith("/api/")}
    original = deepcopy(raw)
    assert enhance_openapi(raw) == document
    assert raw == original
    registered = {(route.path_format, method.lower()) for route in api_routes
                  for method in route.methods}
    published = {(path, method) for path, item in document["paths"].items()
                 for method in item if method in {"get", "post", "put", "patch", "delete"}}
    assert published == registered
    # A documented static operation must not be intercepted by an earlier
    # parameterized route with the same method (for example route-review/action).
    for intended in api_routes:
        witness = intended.path_format
        for name, converter in intended.param_convertors.items():
            value = next(candidate for candidate in ("contract-id", "1", "1.0", "00000000-0000-4000-8000-000000000001")
                         if re.fullmatch(converter.regex, candidate))
            witness = witness.replace("{" + name + "}", value)
        for method in intended.methods:
            scope = {"type": "http", "method": method, "path": witness, "root_path": ""}
            selected = next((route for route in contexts if route.matches(scope)[0] == Match.FULL), None)
            assert selected is intended, (method, intended.path, getattr(selected, "path", None))
    ids = []
    for path, method in sorted(registered):
        operation = document["paths"][path][method]
        assert operation["operationId"] == raw["paths"][path][method]["operationId"]
        ids.append(operation["operationId"])
        assert operation.get("description") and operation.get("tags"), (path, method)
        parameters = {p["name"] for p in operation.get("parameters", []) if p["in"] == "path"}
        assert parameters == set(re.findall(r"{([^}]+)}", path))
        assert operation["responses"]["200"].get("content"), (path, method)
        for media_type, content in operation["responses"]["200"]["content"].items():
            assert content.get("schema"), (path, method, media_type)
        raw_body = raw["paths"][path][method].get("requestBody")
        if raw_body:
            for media_type, content in raw_body["content"].items():
                original = content["schema"]
                if "$ref" in original:
                    # Original typed Pydantic bodies are not replaced or tightened.
                    assert operation["requestBody"]["content"][media_type]["schema"] == original
    assert len(ids) == len(set(ids))

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/"), value
                assert resolve(value, document), value
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(document)
    assert app.openapi() == document


def case_documented_transports(client, app):
    document = app.openapi()
    paths = document["paths"]
    for endpoint in ("/api/health", "/api/auth/login", "/api/auth/status", "/api/shares/{token}"):
        assert paths[endpoint]["get" if endpoint != "/api/auth/login" else "post"].get("security") == []
    schemes = document["components"]["securitySchemes"]
    assert any(s.get("scheme") == "bearer" for s in schemes.values())
    assert any(s.get("in") == "cookie" and s.get("name") == "forest_owner" for s in schemes.values())
    assert "text/event-stream" in paths["/api/projects/{ident}/events"]["get"]["responses"]["200"]["content"]
    assert "multipart/form-data" in paths["/api/projects/{ident}/upload"]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in paths["/api/projects/import"]["post"]["requestBody"]["content"]
    for endpoint, method, media in (("/api/projects/{ident}/download", "get", "application/octet-stream"),
                                  ("/api/projects/{ident}/export", "post", "application/zip"),
                                  ("/api/library/export-bibtex", "get", "application/x-bibtex"),
                                  ("/api/papers/{ident}/export", "post", "application/pdf")):
        assert media in paths[endpoint][method]["responses"]["200"]["content"]
    assert "/ws/projects/{ident}/terminal" in json.dumps(document["x-forest-websockets"])


def case_crud_compatibility(client, app):
    document = app.openapi()
    p = project(client, document)
    pid = p["id"]
    fetched = response(client, document, "get", "/api/projects/{ident}", f"/api/projects/{pid}")
    assert "graph_meta" in p and "graph_meta" not in fetched
    changed = response(client, document, "patch", "/api/projects/{ident}", f"/api/projects/{pid}",
                       json={"name": "Changed by a future UI", "unused_ui_field": True})
    assert changed["name"] == "Changed by a future UI" and "unused_ui_field" not in changed
    assert changed["config"]["custom_ui"]["editable"] is True
    response(client, document, "get", "/api/projects")
    from services.api.db import RESOURCE_MODELS

    for name in RESOURCE_MODELS:
        if name == "reviews":  # POST /reviews schedules an Agent review rather than generic CRUD.
            continue
        item = response(client, document, "post", f"/api/{name}", json={
            "project_id": pid, "title": name, "data": {"future_extension": {"nested": [1, "two", None]}}})
        response(client, document, "get", f"/api/{name}", params={"project_id": pid})
        response(client, document, "get", f"/api/{name}/{{ident}}", f"/api/{name}/{item['id']}")
        edited = response(client, document, "patch", f"/api/{name}/{{ident}}", f"/api/{name}/{item['id']}",
                          json={"title": "Editable", "status": "custom_status", "data": {"custom": True}})
        assert edited["data"] == {"future_extension": {"nested": [1, "two", None]}, "custom": True}
        assert edited["status"] == "custom_status"
        response(client, document, "delete", f"/api/{name}/{{ident}}", f"/api/{name}/{item['id']}")
    paper = response(client, document, "get", "/api/papers/{ident}", f"/api/papers/{pid}")
    assert isinstance(paper["data"]["source"], str)
    revised = response(client, document, "patch", "/api/papers/{ident}", f"/api/papers/{pid}",
                       json={"title": "Editable manuscript", "data": {"client_extension": True, "pdf_path": None}})
    assert revised["revision"] == paper["revision"] + 1
    assert revised["data"]["source"] == paper["data"]["source"]
    assert revised["data"]["pdf_path"] is None
    assert "null" in document["components"]["schemas"]["PaperData"]["properties"]["pdf_path"]["type"]
    source_archive = client.post(f"/api/papers/{pid}/export", json={"format": "source"})
    assert source_archive.status_code == 200 and source_archive.headers["content-type"] == "application/zip"


def case_graph_runs_and_files(client, app):
    document = app.openapi()
    p = project(client, document)
    pid = p["id"]
    g = response(client, document, "get", "/api/projects/{ident}/graph", f"/api/projects/{pid}/graph")
    command_path = f"/api/projects/{pid}/graph/commands"
    body = {"request_id": "contract-add", "expected_revision": g["revision"], "operation": "add_node",
            "params": {"title": "Command transport", "type": "experiment",
                       "config": {"kind": "command", "command": [sys.executable, "-c", "pass"]}}}
    preview = response(client, document, "post", "/api/projects/{ident}/graph/preview", command_path.replace("commands", "preview"), json=body)
    assert preview["changed_nodes"]
    assert client.get(f"/api/projects/{pid}/graph").json()["nodes"] == []
    result = response(client, document, "post", "/api/projects/{ident}/graph/commands", command_path, json=body)
    assert client.post(command_path, json=body).json() == result
    assert client.post(command_path, json={**body, "unexpected": True}).status_code == 422
    nid = result["graph"]["nodes"][0]["id"]
    run = response(client, document, "post", "/api/nodes/{ident}/run", f"/api/nodes/{nid}/run",
                   json={"request_id": "contract-run", "config": {"extra_client_setting": "retained"}})
    assert run["started_at"] is None and run["exit_code"] is None and "tools" not in run
    detail = response(client, document, "get", "/api/runs/{ident}", f"/api/runs/{run['id']}")
    assert detail["tools"] == [] and detail["config"]["extra_client_setting"] == "retained"
    configured = response(client, document, "patch", "/api/runs/{ident}/configuration", f"/api/runs/{run['id']}/configuration",
                          json={"future_research_parameter": {"value": 2}})
    assert configured["config"]["future_research_parameter"] == {"value": 2}
    assert client.patch(f"/api/runs/{run['id']}/configuration", json={"provider_snapshot": {}}).status_code == 422
    for suffix in ("session", "lineage", "verification", "diagnostics", "output"):
        response(client, document, "get", "/api/runs/{ident}/" + suffix, f"/api/runs/{run['id']}/{suffix}")
    response(client, document, "get", "/api/projects/{ident}/runs", f"/api/projects/{pid}/runs")
    selected = response(client, document, "post", "/api/projects/{ident}/runs/selected", f"/api/projects/{pid}/runs/selected",
                        json={"node_ids": [nid], "request_id": "contract-selection"})
    assert selected["run_ids"] == [r["id"] for r in selected["runs"]]
    second = response(client, document, "post", "/api/projects/{ident}/graph/commands", command_path,
                      json={**body, "request_id": "contract-second", "expected_revision": result["graph"]["revision"]})
    child = second["graph"]["nodes"][-1]["id"]
    response(client, document, "post", "/api/projects/{ident}/graph/commands", command_path,
             json={"request_id": "contract-link", "expected_revision": second["graph"]["revision"],
                   "operation": "add_dependency", "params": {"source": nid, "target": child}})
    multiple = response(client, document, "post", "/api/nodes/{ident}/run", f"/api/nodes/{child}/run",
                        json={"request_id": "contract-ancestors", "scope": "ancestors"})
    assert len(multiple["runs"]) == 2
    assert multiple["run_ids"] == [r["id"] for r in multiple["runs"]]
    file_path = f"/api/projects/{pid}/file"
    response(client, document, "put", "/api/projects/{ident}/file", file_path,
             json={"path": "notes.txt", "content": "first", "expected_revision": 0})
    # Optional revision checks remain optional for clients doing an explicit overwrite.
    response(client, document, "put", "/api/projects/{ident}/file", file_path,
             json={"path": "notes.txt", "content": "second"})
    saved = response(client, document, "get", "/api/projects/{ident}/file", file_path, params={"path": "notes.txt"})
    assert saved["revision"] == 2 and saved["content"] == "second"
    uploaded = response(client, document, "post", "/api/projects/{ident}/upload", f"/api/projects/{pid}/upload",
                        files={"file": ("source.txt", b"source bytes", "text/plain")})
    download = client.get(f"/api/projects/{pid}/download", params={"path": uploaded["path"]})
    assert download.status_code == 200 and download.content == b"source bytes"
    archive = client.post(f"/api/projects/{pid}/export", json={})
    assert archive.status_code == 200 and archive.headers["content-type"] == "application/zip"
    restored = response(client, document, "post", "/api/projects/import",
                        files={"file": ("project.zip", archive.content, "application/zip")})
    assert restored["id"] != pid
    clone_path = f"/api/projects/{pid}/repositories/clone"
    clone_body = {"request_id": "contract-repository", "repository": {
        "url": "https://github.com/mikamikasuki/FOREST.git", "ref": "main"}}
    cloned = response(client, document, "post", "/api/projects/{ident}/repositories/clone", clone_path, json=clone_body)
    assert cloned["kind"] == "repository_clone" and cloned["status"] == "queued"
    assert client.post(clone_path, json=clone_body).json()["id"] == cloned["id"]
    assert client.post(clone_path, json={**clone_body, "unsupported": True}).status_code == 422
    manifest = response(client, document, "get", "/api/runs/{ident}/repository", f"/api/runs/{cloned['id']}/repository")
    assert manifest["repository"] is None


def case_auth_and_error_shapes(client, app):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    document = app.openapi()
    p = project(client, document)
    missing = client.get("/api/projects/missing")
    assert missing.status_code == 404
    matches(missing.json(), document["paths"]["/api/projects/{ident}"]["get"]["responses"]["404"]["content"]["application/json"]["schema"], document)
    assert client.post("/api/projects", json={"name": ""}).status_code == 422
    with TestClient(app, base_url="http://remote.example", client=("198.51.100.3", 4000)) as remote:
        assert remote.get("/api/projects").status_code == 401
        assert remote.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
        logged_in = remote.post("/api/auth/login", json={"token": os.environ["FOREST_OWNER_TOKEN"]})
        assert logged_in.status_code == 200 and logged_in.json() == {"authenticated": True}
        assert remote.get("/api/projects").status_code == 200
        assert remote.patch(f"/api/projects/{p['id']}", json={"name": "blocked"},
                            headers={"origin": "https://other.example"}).status_code == 403
        remote.cookies.clear()
        with pytest.raises(WebSocketDisconnect) as denied:
            with remote.websocket_connect(f"/ws/projects/{p['id']}/terminal"):
                pytest.fail("An unauthenticated remote terminal must not connect")
        assert denied.value.code == 1008
        assert remote.get("/api/projects", headers={"authorization": "Bearer " + os.environ["FOREST_OWNER_TOKEN"]}).status_code == 200


def case_sse_replay(client, app):
    from starlette.requests import Request
    from services.api.db import Event, Session
    from services.api.main import events

    p = project(client, app.openapi())
    with Session.begin() as session:
        session.add_all([Event(project_id=p["id"], sequence=900, type="node_changed", data={"revision": 1}),
                         Event(project_id=p["id"], sequence=901, type="run_changed", data={"status": "queued"})])
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "method": "GET", "path": f"/api/projects/{p['id']}/events",
                       "headers": [(b"last-event-id", b"900")]}, receive=receive)

    async def read_frames():
        stream = await events(p["id"], request)
        assert stream.media_type == "text/event-stream"
        assert stream.headers["cache-control"] == "no-cache"
        iterator = stream.body_iterator
        try:
            assert await anext(iterator) == "event: connected\ndata: {}\n\n"
            frame = await anext(iterator)
            assert "id: 901\n" in frame and "event: run_changed\n" in frame
            assert json.loads(frame.split("data: ", 1)[1].strip()) == {"status": "queued"}
            assert await anext(iterator) == ": heartbeat\n\n"
        finally:
            await iterator.aclose()

    asyncio.run(read_frames())


def case_sse_cursor_gap_reset(client, app):
    from sqlalchemy import select
    from starlette.requests import Request
    from services.api.common import emit
    from services.api.db import Event, Session
    from services.api.main import events

    document = app.openapi()
    description = document["paths"]["/api/projects/{ident}/events"]["get"]["description"]
    assert "cursor_reset" in description and "latest_available_sequence" in description
    p = project(client, document)
    with Session.begin() as session:
        for index in range(1200):
            emit(session, p["id"], "run_changed", {"n": index + 1})
    with Session() as session:
        retained = list(session.scalars(
            select(Event.sequence).where(Event.project_id == p["id"]).order_by(Event.sequence)))
    assert len(retained) == 501 and retained[0] == 700 and retained[-1] == 1200

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def first_frame(cursor):
        headers = [] if cursor is None else [(b"last-event-id", str(cursor).encode())]
        request = Request({"type": "http", "method": "GET", "path": f"/api/projects/{p['id']}/events",
                           "headers": headers}, receive=receive)
        stream = await events(p["id"], request)
        iterator = stream.body_iterator
        try:
            assert (await anext(iterator)).startswith("event: connected")
            return await anext(iterator)
        finally:
            await iterator.aclose()

    async def read_controls():
        stale = await first_frame(1)
        assert "id: 1200\n" in stale and "event: cursor_reset\n" in stale
        payload = json.loads(stale.split("data: ", 1)[1].strip())
        assert payload == {"requested_after_sequence": 1, "oldest_available_sequence": 700,
                           "latest_available_sequence": 1200, "resume_after_sequence": 1200}

        missing = await first_frame(None)
        assert "event: cursor_reset\n" in missing
        missing_payload = json.loads(missing.split("data: ", 1)[1].strip())
        assert missing_payload["requested_after_sequence"] == 0
        assert missing_payload["oldest_available_sequence"] == 700
        assert missing_payload["latest_available_sequence"] == 1200
        assert missing_payload["resume_after_sequence"] == 1200

        invalid = await first_frame("not-an-integer")
        assert "event: cursor_reset\n" in invalid
        invalid_payload = json.loads(invalid.split("data: ", 1)[1].strip())
        assert invalid_payload["requested_after_sequence"] == 0
        assert invalid_payload["resume_after_sequence"] == 1200

        with Session.begin() as session:
            emit(session, p["id"], "run_changed", {"n": 1201})
        resumed = await first_frame(1200)
        assert "id: 1201\n" in resumed and "event: run_changed\n" in resumed
        assert json.loads(resumed.split("data: ", 1)[1].strip()) == {"n": 1201}

        current = await first_frame(1199)
        assert "id: 1200\n" in current and "event: run_changed\n" in current
        assert json.loads(current.split("data: ", 1)[1].strip()) == {"n": 1200}

    asyncio.run(read_controls())


def case_sse_cursor_legacy_repair(client, app):
    from sqlalchemy import select, text
    from services.api.common import emit
    from services.api.db import Event, EventSequence, Session, migrate

    p = project(client, app.openapi())
    with Session.begin() as session:
        session.add_all([
            Event(project_id=p["id"], sequence=900, type="node_changed", data={"revision": 1}),
            Event(project_id=p["id"], sequence=901, type="run_changed", data={"status": "queued"}),
        ])
    with Session.begin() as session:
        emit(session, p["id"], "run_changed", {"status": "running"})
        emit(session, p["id"], "run_changed", {"status": "completed"})
    with Session() as session:
        assert session.scalar(select(EventSequence.sequence).where(EventSequence.project_id == p["id"])) == 903

    # Model legacy event rows whose concurrent writers reused IDs.
    with Session.begin() as session:
        session.execute(text("DROP INDEX IF EXISTS uq_events_project_sequence"))
        session.execute(text("DELETE FROM schema_versions WHERE version = 2"))
        session.add_all([
            Event(project_id=p["id"], sequence=904, type="run_changed", data={"run_id": "a"}),
            Event(project_id=p["id"], sequence=904, type="run_changed", data={"run_id": "b"}),
            Event(project_id=p["id"], sequence=904, type="run_changed", data={"run_id": "c"}),
        ])
    migrate()

    with Session() as session:
        sequences = list(session.scalars(
            select(Event.sequence).where(Event.project_id == p["id"]).order_by(Event.sequence)))
        assert len(sequences) == len(set(sequences))
        assert sequences[-3:] == [904, 905, 906]
        assert session.scalar(select(EventSequence.sequence).where(EventSequence.project_id == p["id"])) == 906
    with Session.begin() as session:
        emit(session, p["id"], "run_changed", {"status": "recovered"})
    with Session() as session:
        assert session.scalar(select(EventSequence.sequence).where(EventSequence.project_id == p["id"])) == 907


def case_aliased_data_directory(client, app):
    from services.api.config import settings

    document = app.openapi()
    assert settings.data_dir == Path(os.environ["FOREST_DATA_DIR"]).resolve()
    p = project(client, document)
    pid = p["id"]
    uploaded = response(client, document, "post", "/api/projects/{ident}/upload", f"/api/projects/{pid}/upload",
                        files={"file": ("notes.txt", b"editable source material", "text/plain")})
    assert uploaded["path"] == "uploads/notes.txt"
    saved = response(client, document, "get", "/api/projects/{ident}/file", f"/api/projects/{pid}/file",
                     params={"path": uploaded["path"]})
    assert saved["content"] == "editable source material"
    physical = settings.data_dir / "projects" / pid / uploaded["path"]
    assert physical.read_bytes() == b"editable source material"
    download = client.get(f"/api/projects/{pid}/download", params={"path": uploaded["path"]})
    assert download.status_code == 200 and download.content == physical.read_bytes()
    node = response(client, document, "post", "/api/projects/{ident}/graph/commands", f"/api/projects/{pid}/graph/commands",
                    json={"request_id": "alias-node", "expected_revision": 0, "operation": "add_node", "params": {
                        "title": "Transport path", "config": {"kind": "command", "command": [sys.executable, "-c", "pass"]}}})["graph"]["nodes"][0]
    run = response(client, document, "post", "/api/nodes/{ident}/run", f"/api/nodes/{node['id']}/run", json={})
    relative = run["output_path"] + "/workspace/notes.txt"
    response(client, document, "put", "/api/projects/{ident}/file", f"/api/projects/{pid}/file",
             json={"path": relative, "content": "saved transport input"})
    lineage = response(client, document, "get", "/api/runs/{ident}/lineage", f"/api/runs/{run['id']}/lineage")
    assert any(item["path"] == relative for item in lineage["runs"][0]["files"])


def case_context_state_and_configuration(client, app):
    document = app.openapi()
    p = project(client, document)
    pid = p["id"]
    for endpoint in ("/api/writing-policy", "/api/publication-profile", "/api/system", "/api/agents", "/api/hosts", "/api/providers", "/api/settings"):
        response(client, document, "get", endpoint)
    response(client, document, "patch", "/api/settings", json={"theme": "dark", "ui_extension": {"tab": "graph"}})
    provider = response(client, document, "post", "/api/providers", json={
        "name": "Transport configuration", "kind": "openai", "base_url": "https://example.invalid/v1",
        "model": "not-called", "allow_paid": False, "api_key": "contract-test-key", "config": {"budget_usd": 2}})
    assert provider["has_key"] is True and "contract-test-key" not in json.dumps(provider)
    response(client, document, "patch", "/api/providers/{ident}", f"/api/providers/{provider['id']}", json={"name": "Editable provider"})
    response(client, document, "get", "/api/providers/{ident}/usage", f"/api/providers/{provider['id']}/usage")
    # External provider is disabled, so /system performs no remote availability request.
    response(client, document, "get", "/api/system")
    host = response(client, document, "post", "/api/hosts", json={"name": "Local transport", "kind": "local"})
    response(client, document, "patch", "/api/hosts/{ident}", f"/api/hosts/{host['id']}", json={"name": "Editable host"})
    response(client, document, "post", "/api/hosts/{ident}/test", f"/api/hosts/{host['id']}/test")
    agent = response(client, document, "post", "/api/agents", json={"name": "Configuration only", "role": "custom", "tools": []})
    response(client, document, "patch", "/api/agents/{ident}", f"/api/agents/{agent['id']}", json={"enabled": False})
    batch = response(client, document, "post", "/api/projects/{ident}/graph/batch", f"/api/projects/{pid}/graph/batch", json={
        "expected_revision": 0, "commands": [{"operation": "add_node", "params": {
            "title": "Editable context", "type": "implementation", "instructions": "Read uploaded source material"}}]})
    assert batch["node_count"] == 1
    graph = response(client, document, "get", "/api/projects/{ident}/graph", f"/api/projects/{pid}/graph")
    node = graph["nodes"][0]
    response(client, document, "get", "/api/nodes/{ident}", f"/api/nodes/{node['id']}")
    context = response(client, document, "get", "/api/nodes/{ident}/context", f"/api/nodes/{node['id']}/context")
    assert isinstance(context["summaries_stale"], list)
    response(client, document, "post", "/api/nodes/{ident}/context/rebuild", f"/api/nodes/{node['id']}/context/rebuild",
             json={"max_chars": 8000, "pin": []})
    response(client, document, "patch", "/api/nodes/{ident}", f"/api/nodes/{node['id']}",
             json={"title": "Revised context", "expected_revision": graph["revision"]})
    response(client, document, "get", "/api/projects/{ident}/graph/page", f"/api/projects/{pid}/graph/page", params={"limit": 1})
    for suffix in ("research", "research/route-health", "publication", "usage"):
        response(client, document, "get", "/api/projects/{ident}/" + suffix, f"/api/projects/{pid}/{suffix}")
    response(client, document, "patch", "/api/projects/{ident}/publication", f"/api/projects/{pid}/publication", json={})
    response(client, document, "patch", "/api/projects/{ident}/objective", f"/api/projects/{pid}/objective", json={"metric": "", "direction": "min"})
    review = response(client, document, "post", "/api/projects/{ident}/writing/review", f"/api/projects/{pid}/writing/review",
                      json={"source": "Unfortunately, the method merely improves accuracy."})
    assert isinstance(review["style_version"], int) and review["edits"]
    for action in ("start", "pause", "stop"):
        control = response(client, document, "post", "/api/projects/{ident}/research/{action}", f"/api/projects/{pid}/research/{action}",
                           json={"autonomous": False})
        assert control["process_control_errors"] == []


def case_research_route_review_dispatch(client, app):
    document = app.openapi()
    p = project(client, document)
    pid = p["id"]
    path = f"/api/projects/{pid}/research/route-review"
    body = {"request_id": "contract-route-review"}
    queued = response(client, document, "post", "/api/projects/{ident}/research/route-review", path, json=body)
    assert queued["kind"] == "research_route_review" and queued["status"] == "queued"
    replayed = response(client, document, "post", "/api/projects/{ident}/research/route-review", path, json=body)
    assert replayed["id"] == queued["id"]
    state = response(client, document, "get", "/api/projects/{ident}/research", f"/api/projects/{pid}/research")
    assert state["controller"]["route_review"]["run_id"] == queued["id"]
    for action, expected in (("start", "queued"), ("pause", "paused"), ("start", "queued"), ("stop", "cancelled")):
        control = response(client, document, "post", "/api/projects/{ident}/research/{action}", f"/api/projects/{pid}/research/{action}",
                           json={"autonomous": False})
        assert control["process_control_errors"] == []
        run = response(client, document, "get", "/api/runs/{ident}", f"/api/runs/{queued['id']}")
        assert run["status"] == expected


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_api_contract(tmp_path, case):
    data_dir = tmp_path / "data"
    if case == "aliased_data_directory":
        data_dir.mkdir()
        alias = tmp_path / "data-alias"
        alias.symlink_to(data_dir, target_is_directory=True)
        data_dir = alias
    env = {**os.environ, "FOREST_DATA_DIR": str(data_dir),
           "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "api.db"),
           "FOREST_OWNER_TOKEN": "api-contract-owner-token", "FOREST_MODEL": "",
           "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app

    with TestClient(app) as client:
        globals()["case_" + sys.argv[2]](client, app)
    print("API CONTRACT PASSED:", sys.argv[2])
