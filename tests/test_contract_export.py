"""Check generated frontend types and deterministic schema drift detection."""
import pytest

from scripts.export_api_contract import export, schema_type, typescript


def test_nullable_and_extensible_wire_types():
    assert schema_type({"anyOf": [{"type": "integer"}, {"type": "null"}]}) == "(number) | (null)"
    assert schema_type({"type": "string", "format": "binary"}) == "Blob"
    assert schema_type(False) == "never"
    assert schema_type({}) == "unknown"
    value = schema_type({"type": "object", "required": ["id"], "properties": {
        "id": {"type": "string"}, "config": {"type": "object"},
    }})
    assert '"id": string;' in value
    assert '"config"?: { [key: string]: unknown; };' in value
    assert value.endswith("[key: string]: unknown; }")


@pytest.fixture
def document():
    return {
        "openapi": "3.1.0",
        "components": {"schemas": {"Item": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}}},
        "paths": {"/api/items/{ident}": {"get": {
            "operationId": "read_item",
            "parameters": [{"in": "path", "name": "ident", "required": True, "schema": {"type": "string"}},
                           {"in": "query", "name": "offset", "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "Item", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}}}},
        }, "delete": {"operationId": "delete_item", "responses": {"204": {"description": "Deleted"}}}}},
    }


def test_path_parameters_responses_and_operation_references(document):
    result = typescript(document)
    assert 'path: { "ident": string; };' in result
    assert 'query?: { "offset"?: number; };' in result
    assert '"application/json": components["schemas"]["Item"];' in result
    assert '"204": { headers: { [name: string]: unknown }; content?: never; }' in result
    assert '"read_item": paths["/api/items/{ident}"]["get"];' in result


def test_multipart_required_body_and_nullable_request(document):
    document["paths"]["/api/upload"] = {"post": {
        "operationId": "upload",
        "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
            "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}},
        }}}}, "responses": {"200": {"description": "Done"}},
    }}
    result = typescript(document)
    assert 'requestBody: { content: { "multipart/form-data": { "file": Blob;' in result


def test_export_check_detects_either_stale_artifact(document, tmp_path):
    assert export(document, tmp_path) == (1, 2)
    assert export(document, tmp_path, check=True) == (1, 2)
    exported = tmp_path / "api.generated.ts"
    exported.write_text("stale types\n")
    with pytest.raises(ValueError, match="out of date"):
        export(document, tmp_path, check=True)
    export(document, tmp_path)
    (tmp_path / "openapi.json").unlink()
    with pytest.raises(ValueError, match="openapi.json"):
        export(document, tmp_path, check=True)


def test_generator_rejects_unsupported_refs_instead_of_losing_type_information():
    with pytest.raises(ValueError, match="Unsupported schema reference"):
        schema_type({"$ref": "external.json#/Item"})


def test_named_fields_do_not_conflict_with_typed_additional_properties():
    value = schema_type({"type": "object", "required": ["id"], "properties": {
        "id": {"type": "string"}, "enabled": {"type": "boolean"},
    }, "additionalProperties": {"type": "number"}})
    assert '"id": string;' in value
    assert '"enabled"?: boolean;' in value
    assert "[key: string]: (number) | (string) | (boolean) | (undefined);" in value
    assert "Record<string, number>" not in value
