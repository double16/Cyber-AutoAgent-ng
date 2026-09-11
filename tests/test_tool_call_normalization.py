import pytest

from modules.utils.tool_call_normalization import (
    normalize_tool_call_payload,
    repair_model_response_tool_input,
)


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
