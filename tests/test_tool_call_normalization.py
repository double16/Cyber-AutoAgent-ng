import json

import pytest

from modules.utils.tool_call_normalization import (
    normalize_json_container_arguments,
    normalize_tool_call_payload,
    repair_model_response_tool_input,
)


@pytest.mark.parametrize("text,expected", [
    ('[{"value": "unchanged"}', [{"value": "unchanged"}]),
    ('{"values": [1', {"values": [1]}),
    (json.dumps('[{"value": "unchanged"}'), [{"value": "unchanged"}]),
])
def test_complete_missing_container_closers(text, expected):
    schema = {"type": "array" if isinstance(expected, list) else "object"}
    diagnostics = []
    assert normalize_json_container_arguments(text, schema, diagnostics=diagnostics) == (expected, ("$",))
    assert diagnostics == []


@pytest.mark.parametrize("text", ['[{"value":', '["unterminated', '[1}', '[1] trailing', '[1,'])
def test_unrecoverable_container_syntax_has_safe_diagnostics(text):
    diagnostics = []
    schema = {"type": "object", "properties": {"values": {"type": "array"}}}
    value = {"values": text}
    assert normalize_json_container_arguments(value, schema, diagnostics=diagnostics) == (value, ())
    assert len(diagnostics) == 1
    assert diagnostics[0].path == "$.values"
    assert diagnostics[0].expected_types == ("array",)
    assert diagnostics[0].line == 1
    assert diagnostics[0].column >= 1


def test_normalize_tool_call_payload_accepts_text_wrapped_direct_call():
    normalized = normalize_tool_call_payload(
        'Use this call: {"name":"shell","arguments":{"command":"id"}}',
        registered_tool_names={"shell"},
    )

    assert normalized.name == "shell"
    assert normalized.arguments == {"command": "id"}


def test_normalize_tool_call_payload_accepts_supported_wrappers():
    generic = normalize_tool_call_payload(
        {"name": "tool_use", "tool_name": "http_request", "parameters": {"url": "https://example.test"}},
        registered_tool_names={"http_request"},
    )
    nested = normalize_tool_call_payload(
        {"tool_call": {"name": "http_request", "parameters": {"url": "https://example.test"}}},
        registered_tool_names={"http_request"},
    )

    assert generic == nested
    assert generic.arguments == {"url": "https://example.test"}


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"name": "", "arguments": {}}, "non-empty"),
        ({"name": "shell", "arguments": "id"}, "arguments"),
        ({"label": "ordinary JSON"}, "name"),
    ],
)
def test_normalize_tool_call_payload_rejects_invalid_shapes(payload, error):
    with pytest.raises(ValueError, match=error):
        normalize_tool_call_payload(payload, registered_tool_names={"shell"})


def test_normalize_tool_call_payload_rejects_unknown_registered_tool():
    with pytest.raises(ValueError, match="not registered"):
        normalize_tool_call_payload(
            {"name": "unknown", "arguments": {}}, registered_tool_names={"shell"}
        )


def test_repair_model_response_tool_input_recovers_json_value_with_atem_suffix():
    repaired, fields = repair_model_response_tool_input(
        {
            "content": "Observation",
            "artifacts": (
                '["artifact:artifacts/api_config_headers_fresh.txt"]</atem:invoke>\n'
                '<atem:parameter name="metadata">{"target":"http://example.test"}'
            ),
        }
    )

    assert repaired == {
        "content": "Observation",
        "artifacts": ["artifact:artifacts/api_config_headers_fresh.txt"],
    }
    assert fields == ("artifacts",)


@pytest.mark.parametrize(
    "value",
    [
        '["artifact:artifacts/proof.txt"] trailing text',
        '["artifact:artifacts/proof.txt"]</unexpected:invoke>',
        '["artifact:artifacts/proof.txt"',
    ],
)
def test_repair_model_response_tool_input_leaves_unrecognized_or_invalid_values_unchanged(value):
    payload = {"artifacts": value}

    repaired, fields = repair_model_response_tool_input(payload)

    assert repaired == payload
    assert fields == ()


def test_tool_call_normalization_handles_non_mapping_repair_and_unregistered_free_calls():
    assert repair_model_response_tool_input(["not", "a", "mapping"]) == (["not", "a", "mapping"], ())

    normalized = normalize_tool_call_payload({"name": " shell ", "input": {"command": "id"}})
    assert normalized.name == "shell"
    assert normalized.arguments == {"command": "id"}

    with pytest.raises(ValueError, match="JSON object"):
        normalize_tool_call_payload(["not", "a", "mapping"])


@pytest.mark.parametrize("value", [[{"values": [1]}], '[{"values": "[1]"}]',
                                  json.dumps('[{"values": "[1]"}]')])
def test_schema_directed_containers_normalize_nested_references(value):
    schema = {"type": "object", "properties": {"subjects": {"type": "array", "items": {"$ref": "#/$defs/Subject"}}},
              "$defs": {"Subject": {"type": "object", "properties": {"values": {"type": "array"}}}}}
    payload = {"subjects": value}
    normalized, paths = normalize_json_container_arguments(payload, schema)
    assert normalized == {"subjects": [{"values": [1]}]}
    assert payload == {"subjects": value}
    assert normalize_json_container_arguments(normalized, schema) == (normalized, ())
    if isinstance(value, str):
        assert paths == ("$.subjects", "$.subjects[0].values")


@pytest.mark.parametrize("schema,value", [
    ({"type": "string"}, "[]"), ({"type": ["string", "array"]}, "[]"),
    ({"anyOf": [{"type": "string"}, {"type": "array"}]}, "[]"),
    ({"anyOf": [{}, {"type": "array"}]}, "[]"), ({}, "[]"),
    ({"type": "array"}, "[NaN]"), ({"type": "array"}, "[Infinity]"),
    ({"type": "array"}, "[-Infinity]"), ({"type": "array"}, "not JSON"), ({"type": "array"}, "```json\n[]\n```"),
    ({"type": "array"}, "{}"), ({"type": "object"}, "[]"),
    ({"type": "array"}, "null"), ({"type": "array"}, "true"), ({"type": "array"}, "42"),
    ({"type": "array"}, json.dumps(json.dumps("[]"))),
    ({"$ref": "https://example.test/schema"}, "[]"), ({"$ref": "#/missing"}, "[]"),
    ({"$ref": "#/$defs/loop", "$defs": {"loop": {"$ref": "#/$defs/loop"}}}, "[]"),
    ({"type": "object", "properties": {}}, {"untyped": "[]"}),
    ({"type": "array", "items": {"type": "string"}}, ['{"literal": true}']),
    ({"type": "object", "additionalProperties": False}, {"unknown": "[]"}),
])
def test_schema_directed_normalization_preserves_literal_or_invalid_values(schema, value):
    assert normalize_json_container_arguments(value, schema) == (value, ())


@pytest.mark.parametrize("schema", [
    {"anyOf": [{"type": "array", "items": {"type": "object"}}, {"type": "null"}]},
    {"oneOf": [{"type": "array", "items": {"type": "object"}}, {"type": "object"}]},
    {"type": ["array", "null"], "items": {"type": "object"}},
])
def test_unambiguous_decoded_container_and_nested_items(schema):
    normalized, paths = normalize_json_container_arguments('["{}"]', schema)
    assert normalized == [{}]
    assert paths == ("$", "$[0]")


def test_additional_properties_prefix_items_and_escaped_local_reference():
    schema = {"type": "object", "additionalProperties": {"$ref": "#/$defs/a~1b~0c"},
              "$defs": {"a/b~c": {"type": "array", "prefixItems": [{"type": "object"}]}}}
    normalized, paths = normalize_json_container_arguments({"dynamic": '["{}"]'}, schema)
    assert normalized == {"dynamic": [{}]}
    assert paths == ("$.dynamic", "$.dynamic[0]")


def test_ambiguous_object_variants_do_not_guess_nested_types():
    schema = {"oneOf": [{"type": "object", "properties": {"a": {"type": "array"}}},
                        {"type": "object", "properties": {"a": {"type": "string"}}}]}
    assert normalize_json_container_arguments('{"a": "[]"}', schema) == ({"a": "[]"}, ("$",))


def test_recursive_arguments_are_depth_bounded_and_invalid_deep_json_is_preserved(monkeypatch):
    schema = {"$ref": "#/$defs/node", "$defs": {"node": {"type": "object", "properties": {
        "child": {"$ref": "#/$defs/node"}, "values": {"type": "array"},
    }}}}
    value = {"values": "[]"}
    for _ in range(70):
        value = {"child": value}
    normalized, paths = normalize_json_container_arguments(value, schema)
    assert normalized == value and paths == ()
    def reject_deep_json(_text, **_kwargs):
        raise RecursionError("decoder depth")

    monkeypatch.setattr("modules.utils.tool_call_normalization.json.loads", reject_deep_json)
    assert normalize_json_container_arguments("[]", {"type": "array"}) == ("[]", ())
