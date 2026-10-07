"""Connection preferences and a directory's project binding."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any

from .client import CliError, normalize_endpoint


DEFAULT_ENDPOINT = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class Context:
    endpoint: str
    token: str | None = field(default=None, repr=False)
    project_id: str | None = None


def _setting(value: Any, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, Path)):
        raise CliError("Configuration field must be a string: " + name, "INVALID_CONFIG", 2)
    return str(value)


def config_path(path: str | Path | None = None) -> Path:
    value = path or os.environ.get("FOREST_CLI_CONFIG")
    return Path(value).expanduser() if value else Path.home() / ".config" / "forest" / "cli.json"


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise CliError("Could not read configuration JSON: " + str(path), "INVALID_CONFIG", 2) from None
    if not isinstance(result, dict):
        raise CliError("Configuration must be a JSON object: " + str(path), "INVALID_CONFIG", 2)
    return result


def _local_binding(cwd: Path) -> tuple[Path | None, dict[str, Any]]:
    for parent in (cwd, *cwd.parents):
        candidate = parent / ".forest" / "project.json"
        if candidate.exists():
            return candidate, _read_config(candidate)
    return None, {}


def _absolute_path(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def resolve_context(args: Any, cwd: str | Path | None = None) -> Context:
    """Resolve flags > environment > nearest binding > user config > defaults.

    A binding's project and token file only apply to its own server. The same
    check applies to user config, preventing credentials from crossing servers.
    Explicit project/token options remain usable with an explicitly chosen server.
    """
    directory = Path(cwd or Path.cwd()).expanduser().resolve()
    user_path = _absolute_path(config_path(getattr(args, "config", None)), directory)
    user = _read_config(user_path)
    local_path, local = _local_binding(directory)
    local_endpoint = _setting(local.get("endpoint"), "endpoint")
    user_endpoint = _setting(user.get("endpoint"), "endpoint")
    if local_endpoint:
        local_endpoint = normalize_endpoint(local_endpoint)
    if user_endpoint:
        user_endpoint = normalize_endpoint(user_endpoint)
    endpoint = normalize_endpoint(
        _setting(getattr(args, "server", None), "server") or
        _setting(os.environ.get("FOREST_SERVER"), "FOREST_SERVER") or
        local_endpoint or user_endpoint or DEFAULT_ENDPOINT)
    local_matches = bool(local_path) and (local_endpoint or user_endpoint or DEFAULT_ENDPOINT) == endpoint
    user_matches = (user_endpoint or DEFAULT_ENDPOINT) == endpoint
    project_id = (
        _setting(getattr(args, "project", None), "project") or
        _setting(os.environ.get("FOREST_PROJECT_ID"), "FOREST_PROJECT_ID") or
        (_setting(local.get("project_id"), "project_id") if local_matches else None) or
        (_setting(user.get("project_id"), "project_id") if user_matches else None))
    token_file: str | None = _setting(getattr(args, "token_file", None), "token_file")
    token_base = directory
    token: str | None = None
    if token_file is None:
        token = _setting(os.environ.get("FOREST_OWNER_TOKEN"), "FOREST_OWNER_TOKEN")
    if token is None and token_file is None:
        token_file = _setting(os.environ.get("FOREST_TOKEN_FILE"), "FOREST_TOKEN_FILE")
        if token_file is None and local_matches:
            token_file = _setting(local.get("token_file"), "token_file")
            if local_path:
                token_base = local_path.parent
        if token_file is None and user_matches:
            token_file = _setting(user.get("token_file"), "token_file")
            token_base = user_path.parent
    if token_file is not None:
        path = _absolute_path(token_file, token_base)
        try:
            token = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise CliError("Could not read owner token file: " + str(path), "TOKEN_FILE_ERROR", 2) from None
        if not token:
            raise CliError("Owner token file is empty: " + str(path), "TOKEN_FILE_ERROR", 2)
    if token and ("\r" in token or "\n" in token):
        raise CliError("Owner token must be a single line.", "INVALID_TOKEN", 2)
    return Context(endpoint, token, project_id)


def _write_config(path: Path, value: dict[str, Any]) -> Path:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path
    except OSError:
        raise CliError("Could not save configuration: " + str(path), "CONFIG_WRITE_ERROR") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_connection(endpoint: str, token_file: str | Path | None = None,
                    path: str | Path | None = None) -> Path:
    destination = config_path(path)
    endpoint = normalize_endpoint(endpoint)
    previous = _read_config(destination)
    same_server = bool(previous.get("endpoint")) and normalize_endpoint(previous["endpoint"]) == endpoint
    value: dict[str, Any] = {"endpoint": endpoint}
    if same_server:
        for key in ("project_id", "token_file"):
            if previous.get(key):
                value[key] = _setting(previous[key], key)
    if token_file is not None:
        value["token_file"] = str(Path(token_file).expanduser().resolve())
    return _write_config(destination, value)


def save_project_binding(project_id: str, endpoint: str,
                         cwd: str | Path | None = None) -> Path:
    project_id = _setting(project_id, "project_id")
    if not project_id:
        raise CliError("Project ID cannot be empty.", "INVALID_PROJECT", 2)
    path = Path(cwd or Path.cwd()).expanduser().resolve() / ".forest" / "project.json"
    return _write_config(path, {"endpoint": normalize_endpoint(endpoint), "project_id": project_id})


def remember_graph_request(context: Context, project_id: str, request_id: str,
                           intent: dict, body: dict, path=None) -> dict:
    """Freeze the first observed graph and implicit defaults before sending.

    The journal stores no credentials or server response. An atomic hard link
    chooses one complete request across concurrent CLI processes; retries send
    that exact body, including after a response was lost.
    """
    key = json.dumps([context.endpoint, project_id, request_id], ensure_ascii=False)
    destination = config_path(path).parent / 'requests' / (hashlib.sha256(key.encode()).hexdigest() + '.json')
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                             prefix='.request-', delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                json.dump({'identity': key, 'intent': intent, 'body': body}, stream, ensure_ascii=False)
                stream.flush(); os.fsync(stream.fileno())
            try: os.link(temporary, destination)
            except FileExistsError: pass
        recorded = _read_config(destination)
        if (recorded.get('identity') != key or
                json.dumps(recorded.get('intent'), sort_keys=True) != json.dumps(intent, sort_keys=True)):
            raise CliError('This request identity belongs to different intent; use a new request ID.',
                           'REQUEST_ID_CONFLICT', 4)
        if not isinstance(recorded.get('body'), dict):
            raise CliError('The stored request is unreadable: ' + str(destination), 'INVALID_CONFIG', 2)
        return recorded['body']
    except OSError:
        raise CliError('Could not persist the request before submission: ' + str(destination),
                       'REQUEST_JOURNAL_ERROR') from None
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
