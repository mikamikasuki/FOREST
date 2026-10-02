#!/usr/bin/env python3
"""Export the API schema, frontend types and reference, or check for drift.

No API service, database migration, model call or project is needed. The export
uses the same OpenAPI function as the running service, in an isolated directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "packages" / "contracts"
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def literal(value):
    return json.dumps(value, ensure_ascii=False)


def reference(value):
    if not value.startswith("#/components/"):
        raise ValueError(f"Unsupported schema reference: {value}")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in value[2:].split("/")]
    return parts[0] + "".join(f"[{literal(part)}]" for part in parts[1:])


def schema_type(schema):
    """Translate JSON Schema without narrowing extensible payloads."""
    if schema is False:
        return "never"
    if schema is True or not schema:
        return "unknown"
    if "$ref" in schema:
        result = reference(schema["$ref"])
    elif "const" in schema:
        result = literal(schema["const"])
    elif "enum" in schema:
        result = " | ".join(literal(value) for value in schema["enum"]) or "never"
    elif "oneOf" in schema or "anyOf" in schema:
        result = " | ".join(f"({schema_type(item)})" for item in schema.get("oneOf", schema.get("anyOf")))
    elif "allOf" in schema:
        result = " & ".join(f"({schema_type(item)})" for item in schema["allOf"])
    elif isinstance(schema.get("type"), list):
        result = " | ".join(schema_type({**schema, "type": kind}) for kind in schema["type"])
    elif schema.get("type") == "null":
        result = "null"
    elif schema.get("type") == "boolean":
        result = "boolean"
    elif schema.get("type") in ("number", "integer"):
        result = "number"
    elif schema.get("type") == "string":
        result = "Blob" if schema.get("format") == "binary" else "string"
    elif schema.get("type") == "array":
        result = f"Array<{schema_type(schema.get('items', {}))}>"
    elif schema.get("type") == "object" or "properties" in schema or "additionalProperties" in schema:
        required = set(schema.get("required", []))
        fields = [
            f"{literal(name)}{'' if name in required else '?'}: {schema_type(value)};"
            for name, value in sorted(schema.get("properties", {}).items())
        ]
        additional = schema.get("additionalProperties", True)
        if additional is not False:
            value = schema_type(additional) if isinstance(additional, dict) else "unknown"
            if fields and value != "unknown":
                # JSON Schema's additionalProperties excludes named fields,
                # whereas a TypeScript index signature also includes them.
                # Keep named fields exact while widening the index signature
                # enough to admit their values and optional undefined values.
                values = [value] + [schema_type(item) for item in schema.get("properties", {}).values()]
                if any(name not in required for name in schema.get("properties", {})):
                    values.append("undefined")
                value = " | ".join(f"({item})" for item in dict.fromkeys(values))
            fields.append(f"[key: string]: {value};")
        result = "{ " + " ".join(fields) + " }"
    else:
        result = "unknown"
    return f"({result}) | null" if schema.get("nullable") and result != "null" else result


def resolve(value, document):
    if "$ref" not in value:
        return value
    current = document
    for part in value["$ref"][2:].split("/"):
        current = current[part.replace("~1", "/").replace("~0", "~")]
    return current


def content_type(content):
    return "{ " + " ".join(
        f"{literal(media)}: {schema_type(value.get('schema', {}))};"
        for media, value in sorted(content.items())
    ) + " }"


def parameter_type(parameters, document):
    groups = {}
    for entry in parameters:
        entry = resolve(entry, document)
        groups.setdefault(entry["in"], {})[entry["name"]] = entry
    fields = []
    for group in ("path", "query", "header", "cookie"):
        values = groups.get(group, {})
        if not values:
            fields.append(f"{group}?: never;")
            continue
        required = group == "path" or any(value.get("required", False) for value in values.values())
        members = " ".join(
            f"{literal(name)}{'' if value.get('required', False) or group == 'path' else '?'}: {schema_type(value.get('schema', {}))};"
            for name, value in sorted(values.items())
        )
        fields.append(f"{group}{'' if required else '?'}: {{ {members} }};")
    return "{ " + " ".join(fields) + " }"


def response_type(response, document):
    response = resolve(response, document)
    headers = "{ [name: string]: unknown }"
    content = response.get("content")
    return f"{{ headers: {headers}; " + (
        f"content: {content_type(content)}; }}" if content else "content?: never; }"
    )


def operation_type(operation, path_parameters, document):
    fields = [f"parameters: {parameter_type(path_parameters + operation.get('parameters', []), document)};"]
    body = operation.get("requestBody")
    if body:
        body = resolve(body, document)
        fields.append(f"requestBody{'' if body.get('required', False) else '?'}: {{ content: {content_type(body.get('content', {}))}; }};")
    else:
        fields.append("requestBody?: never;")
    responses = " ".join(
        f"{literal(code)}: {response_type(value, document)};"
        for code, value in sorted(operation.get("responses", {}).items())
    )
    fields.append(f"responses: {{ {responses} }};")
    return "{ " + " ".join(fields) + " }"


def typescript(document):
    lines = [
        "// Generated by scripts/export_api_contract.py. Do not edit by hand.",
        "// Transport, authentication and revision semantics: docs/FRONTEND_API_GUIDE.md.",
        "export interface components {",
    ]
    for section, values in sorted(document.get("components", {}).items()):
        if section == "securitySchemes":
            continue
        lines.append(f"  {literal(section)}: {{")
        for name, value in sorted(values.items()):
            if section == "schemas":
                kind = schema_type(value)
            elif section == "responses":
                kind = response_type(value, document)
            elif section == "requestBodies":
                kind = "{ content: " + content_type(resolve(value, document).get("content", {})) + "; }"
            elif section in ("parameters", "headers"):
                kind = schema_type(resolve(value, document).get("schema", {}))
            else:
                kind = "unknown"
            lines.append(f"    {literal(name)}: {kind};")
        lines.append("  };")
    lines.extend(["}", "", "export interface paths {"])
    for path, item in sorted(document.get("paths", {}).items()):
        lines.append(f"  {literal(path)}: {{")
        for method, operation in sorted(item.items()):
            if method in HTTP_METHODS:
                lines.append(f"    {method}: {operation_type(operation, item.get('parameters', []), document)};")
        lines.append("  };")
    lines.extend(["}", "", "export interface operations {"])
    for path, item in sorted(document.get("paths", {}).items()):
        for method, operation in sorted(item.items()):
            if method in HTTP_METHODS:
                lines.append(f"  {literal(operation['operationId'])}: paths[{literal(path)}][{literal(method)}];")
    lines.extend(["}", ""])
    return "\n".join(lines)


def api_schema():
    # Settings create their data directory at import. Isolate even that setup
    # from real installations; obtaining OpenAPI does not run the app lifespan.
    with tempfile.TemporaryDirectory(prefix="forest-contract-") as directory:
        os.environ["FOREST_DATA_DIR"] = directory
        os.environ["FOREST_DATABASE_URL"] = "sqlite:///" + str(Path(directory) / "contract.db")
        os.environ["FOREST_MODEL"] = ""
        sys.path.insert(0, str(ROOT))
        from services.api.main import app
        return app.openapi()


def export(document, destination=DESTINATION, check=False):
    outputs = {
        "openapi.json": json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        "api.generated.ts": typescript(document),
    }
    stale = []
    for name, text in outputs.items():
        path = destination / name
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path))
        else:
            destination.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if stale:
        raise ValueError("API contracts are out of date: " + ", ".join(stale) + ". Run python scripts/export_api_contract.py.")
    return len(document.get("paths", {})), sum(
        method in HTTP_METHODS for item in document.get("paths", {}).values() for method in item
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed schema or types differ from the current API")
    arguments = parser.parse_args()
    try:
        document = api_schema()
        paths, operations = export(document, check=arguments.check)
        from services.api.contracts import render_api_reference
        reference_path = ROOT / "docs" / "API_REFERENCE.md"
        reference_text = render_api_reference(document)
        if arguments.check:
            if not reference_path.is_file() or reference_path.read_text(encoding="utf-8") != reference_text:
                raise ValueError("API reference is out of date: docs/API_REFERENCE.md. Run python scripts/export_api_contract.py.")
        else:
            reference_path.parent.mkdir(parents=True, exist_ok=True)
            reference_path.write_text(reference_text, encoding="utf-8")
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"{'Checked' if arguments.check else 'Exported'} {operations} operations across {paths} API paths.")


if __name__ == "__main__":
    main()
