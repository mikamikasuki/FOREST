"""Human-readable output and clean JSON streams for shell automation."""
from __future__ import annotations

import json
import sys
from typing import Any

from .client import CliError


_CREDENTIAL_FIELDS = {"api_key", "password", "authorization", "owner_token", "access_token",
                      "refresh_token", "credential_ref", "secret", "private_key", "secret_key",
                      "client_secret"}


def redact(value: Any) -> Any:
    """Hide explicit credentials while retaining reproducibility information.

    JSON output has the same structure, with credential values replaced by
    ``[redacted]``. Nonsecret environment settings and numeric token accounting
    remain available. Inputs are never mutated, and ordinary text is retained.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key).lower().replace("-", "_")
            sensitive = name in _CREDENTIAL_FIELDS or name.endswith(("_api_key", "_token"))
            result[key] = "[redacted]" if sensitive and item is not None else redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value


def _text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _table(rows: list[dict[str, Any]]) -> None:
    keys = list(dict.fromkeys(key for row in rows for key in row))
    preferred = [key for key in ("id", "name", "title", "status", "phase", "kind") if key in keys]
    keys = (preferred + [key for key in keys if key not in preferred])[:8]
    cells = [[_text(row.get(key)).replace("\n", " ") for key in keys] for row in rows]
    # Bound wide fields while retaining the IDs needed for follow-up commands.
    limits = [48 if key.endswith("id") or key == "id" else 60 for key in keys]
    cells = [[cell if len(cell) <= limit else cell[:limit - 1] + "…"
              for cell, limit in zip(row, limits)] for row in cells]
    widths = [max(len(str(key)), *(len(row[index]) for row in cells))
              for index, key in enumerate(keys)]
    print("  ".join(str(key).ljust(width) for key, width in zip(keys, widths)))
    print("  ".join("-" * width for width in widths))
    for row in cells:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


def emit(value: Any, *, json_output: bool = False) -> None:
    value = redact(value)
    if json_output:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    elif isinstance(value, list):
        if value and all(isinstance(row, dict) and row for row in value):
            _table(value)
        elif value:
            for item in value:
                print(_text(item))
        else:
            print("No results.")
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, list) and item and all(isinstance(row, dict) and row for row in item):
                print(str(key) + ":")
                _table(item)
            else:
                print(str(key) + ": " + _text(item))
    elif value is not None:
        print(_text(value))


def report_error(error: CliError, *, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps({"error": redact(error.to_dict())}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    else:
        print(error.code + ": " + error.message, file=sys.stderr)
        if error.suggestion:
            print(error.suggestion, file=sys.stderr)
