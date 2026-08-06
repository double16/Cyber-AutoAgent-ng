import pytest

from modules.utils.tool_call_normalization import normalize_tool_call_payload


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
