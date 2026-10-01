"""HTTP transport and errors shared by all FOREST CLI commands."""
from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class CliError(Exception):
    """An actionable command failure, with a stable machine-readable code."""

    def __init__(self, message: str, code: str = "CLI_ERROR", exit_code: int = 1,
                 details: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.details = details

    @property
    def suggestion(self) -> str:
        return str(self.details.get("suggestion") or "") if isinstance(self.details, dict) else ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.suggestion:
            result["suggestion"] = self.suggestion
        if self.details is not None:
            result["details"] = self.details
        return result


def normalize_endpoint(value: str) -> str:
    """Accept an HTTP API root, never credentials embedded in an URL."""
    try:
        if not isinstance(value, str) or not value.strip():
            raise ValueError
        value = value.strip()
        if any(character.isspace() for character in value):
            raise ValueError
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.query or parsed.fragment:
            raise ValueError
        parsed.port  # Validate the port without including the URL in an error.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    except (TypeError, ValueError):
        raise CliError("Server must be an HTTP(S) URL without credentials, query, or fragment.",
                       code="INVALID_SERVER", exit_code=2) from None


_CREDENTIAL_KEYS = {"authorization", "token", "owner_token", "api_key", "password", "secret",
                    "access_token", "refresh_token", "private_key", "secret_key", "client_secret"}
_ECHOED_REQUEST_KEYS = {"input", "body", "payload", "request"}


def _credential_key(key: Any) -> bool:
    name = str(key).lower().replace("-", "_")
    return name in _CREDENTIAL_KEYS or name.endswith(("_api_key", "_token", "_password", "_secret"))


def _request_secrets(value: Any) -> set[str]:
    """Recognize explicit credential fields, never include a request in errors."""
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if _credential_key(key) and isinstance(item, str) and item:
                result.add(item)
                if item.lower().startswith("bearer "):
                    result.add(item[7:])
            else:
                result.update(_request_secrets(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_request_secrets(item))
    return result


def _redact_text(value: str, secrets: set[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def _clean_detail(value: Any, secrets: set[str]) -> Any:
    if isinstance(value, dict):
        return {_redact_text(str(key), secrets): _clean_detail(item, secrets)
                for key, item in value.items() if not _credential_key(key)
                and str(key).lower().replace("-", "_") not in _ECHOED_REQUEST_KEYS}
    if isinstance(value, list):
        return [_clean_detail(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


class ForestClient:
    """A synchronous, thin API client. Requests are attempted exactly once.

    ``transport`` is injectable for tests and embedded clients. Redirects are
    not followed, so an owner token cannot move to a different endpoint.
    ``request_id`` is included in JSON objects as well as the tracing header.
    """

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30,
                 *, transport: httpx.BaseTransport | None = None):
        self.base_url = normalize_endpoint(base_url)
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise CliError("Timeout must be greater than zero.", "INVALID_TIMEOUT", 2)
        if token and (not isinstance(token, str) or "\r" in token or "\n" in token):
            raise CliError("Owner token must be a single line.", "INVALID_TOKEN", 2)
        self._token = token
        self.request_timeout = timeout
        headers = {"Accept": "application/json", "User-Agent": "forest-cli/0.1.0"}
        if token:
            headers["Authorization"] = "Bearer " + token
        self._client = httpx.Client(timeout=timeout, headers=headers,
                                    transport=transport, follow_redirects=False)

    def __enter__(self) -> ForestClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _url(self, path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise CliError("API paths must start with a single slash.", "INVALID_API_PATH", 2)
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or "\\" in path:
            raise CliError("API path must stay on the configured server.", "INVALID_API_PATH", 2)
        return self.base_url + path

    def _check_response(self, response: httpx.Response, *, payload: Any = None) -> None:
        if 200 <= response.status_code < 300:
            return
        status = response.status_code
        fallback = "Server returned HTTP " + str(status) + "."
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        detail = body.get("detail", body) if isinstance(body, dict) else None
        secrets = _request_secrets(payload)
        if self._token:
            secrets.add(self._token)
        detail = _clean_detail(detail, secrets)
        if isinstance(detail, dict):
            code = str(detail.get("code") or "HTTP_ERROR")
            message = str(detail.get("message") or fallback)
        elif status == 422 and isinstance(detail, list):
            code, message = "VALIDATION_ERROR", "Request validation failed."
            first = next((item for item in detail if isinstance(item, dict)), None)
            if first:
                location = ".".join(str(part) for part in first.get("loc", []))
                reason = str(first.get("msg") or "Invalid input")
                message = "Request validation failed: " + (location + ": " if location else "") + reason
            detail = {"validation_errors": detail, "suggestion": "Review the input fields and retry."}
        elif isinstance(detail, str):
            code, message = "HTTP_ERROR", detail
        else:
            code, message = "HTTP_ERROR", fallback
        exit_code = 3 if status in {401, 403} else 4 if status == 409 else 1
        if not isinstance(detail, dict):
            detail = {"status_code": status}
        else:
            detail = {**detail, "status_code": status}
        raise CliError(message, code, exit_code, detail)

    @staticmethod
    def _network_error() -> CliError:
        return CliError("Could not reach the FOREST server.", "CONNECTION_ERROR", details={
            "suggestion": "Check the server address, network connection, and whether the API is running."})

    def _recovery_error(self, error: CliError, method: str, payload: Any) -> CliError:
        """Retain an existing mutation ID when acceptance cannot be confirmed."""
        if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"} or not isinstance(payload, dict):
            return error
        identity = payload.get("request_id")
        if not isinstance(identity, str) or not identity:
            return error
        secrets = _request_secrets(payload)
        if self._token:
            secrets.add(self._token)
        identity = _redact_text(identity, secrets)
        # JSON quoting also prevents a pasted newline/control character in an
        # ID from changing the human-readable diagnostic's terminal layout.
        import json as json_module
        diagnostic_id = json_module.dumps(identity, ensure_ascii=False)
        details = dict(error.details) if isinstance(error.details, dict) else {}
        suggestion = str(details.get("suggestion") or "")
        recovery = ("Acceptance is unconfirmed. Check the original receipt or run list before submitting again. "
                    "Recover that same operation with --request-id " + diagnostic_id + "; do not use a new ID.")
        details.update(request_id=identity, suggestion=(suggestion + " " + recovery).strip())
        error.details = details
        return error

    def request(self, method: str, path: str, *, json: Any = None,
                params: Any = None, request_id: str | None = None,
                timeout: float | None = None) -> Any:
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or not math.isfinite(timeout) or timeout <= 0):
            raise CliError("Timeout must be a positive finite number.", "INVALID_TIMEOUT", 2)
        headers = None
        if request_id:
            if not isinstance(request_id, str) or "\r" in request_id or "\n" in request_id:
                raise CliError("Request ID must be a single line.", "INVALID_REQUEST_ID", 2)
            headers = {"X-Request-ID": request_id}
            if isinstance(json, dict):
                if json.get("request_id") is not None and json["request_id"] != request_id:
                    raise CliError("Request ID does not match the JSON request.", "INVALID_REQUEST_ID", 2)
                json = {**json, "request_id": request_id}
        try:
            options = {"timeout": min(timeout, self.request_timeout)} if timeout is not None else {}
            response = self._client.request(method, self._url(path), json=json,
                                            params=params, headers=headers, **options)
        except httpx.HTTPError:
            raise self._recovery_error(self._network_error(), method, json) from None
        self._check_response(response, payload=json)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except (ValueError, UnicodeDecodeError):
            error = CliError("Server returned an invalid JSON response.", "INVALID_RESPONSE")
            raise self._recovery_error(error, method, json) from None

    def download(self, method: str, path: str, destination: str | Path, *,
                 json: Any = None, params: Any = None, overwrite: bool = False) -> dict[str, Any]:
        """Stream a file and publish it only after the entire download succeeds.

        A hard link publishes without overwriting an existing path, including
        a path created by another process while downloading. Explicit overwrite
        uses atomic replacement. Partial files are always removed.
        """
        target = Path(destination).expanduser()
        if not overwrite and (target.exists() or target.is_symlink()):
            raise CliError("Destination already exists: " + str(target), "FILE_EXISTS", 2)
        temporary: Path | None = None
        try:
            with self._client.stream(method, self._url(path), json=json, params=params) as response:
                if not 200 <= response.status_code < 300:
                    response.read()
                self._check_response(response, payload=json)
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix="." + target.name + ".",
                                                 suffix=".part", delete=False) as stream:
                    temporary = Path(stream.name)
                    size = 0
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
                        size += len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if overwrite:
                    os.replace(temporary, target)
                else:
                    os.link(temporary, target)
                content_type = response.headers.get("content-type", "application/octet-stream")
                secrets = _request_secrets(json)
                if self._token:
                    secrets.add(self._token)
                return {"path": str(target.resolve()), "bytes": size,
                        "content_type": _redact_text(content_type, secrets)}
        except FileExistsError:
            raise CliError("Destination already exists: " + str(target), "FILE_EXISTS", 2) from None
        except httpx.HTTPError:
            raise self._network_error() from None
        except OSError:
            raise CliError("Could not write download: " + str(target), "FILE_WRITE_ERROR", details={
                "suggestion": "Choose an existing writable directory and a new file name."}) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
