from modules.utils.reasoning_sanitization import ReasoningSanitizationState, sanitize_reasoning_event


def test_reasoning_event_sanitizes_nested_and_stream_delta_fields():
    event = {
        "reasoningText": "<|start|>plan<|end|>",
        "content": [
            {"reasoningContent": {"reasoningText": {"text": "<|channel>thought examine"}}},
            {"text": "leave unchanged"},
        ],
        "message": {"content": [{"reasoningContent": {"reasoningText": {"text": "<|message|>message"}}}]},
        "delta": {"content": [{"reasoningContent": {"reasoningText": {"text": "<|end>delta"}}}]},
        "contentBlockDelta": {"delta": {"text": "<|start|>block"}},
        "contentBlockStart": {"start": {"text": "<|end|>start"}},
    }

    assert sanitize_reasoning_event(event, ReasoningSanitizationState()) == 6
    assert event["reasoningText"] == "plan"
    assert event["content"][0]["reasoningContent"]["reasoningText"]["text"] == " examine"
    assert event["message"]["content"][0]["reasoningContent"]["reasoningText"]["text"] == "message"
    assert event["delta"]["content"][0]["reasoningContent"]["reasoningText"]["text"] == "delta"
    assert event["contentBlockDelta"]["delta"]["text"] == "block"
    assert event["contentBlockStart"]["start"]["text"] == "start"


def test_reasoning_event_removes_empty_reasoning_only_blocks_and_ignores_invalid_shapes():
    block = {"reasoningContent": {"reasoningText": {"text": "<|message|>"}}}
    event = {"content": [block, "invalid"], "message": {"content": "invalid"}}

    assert sanitize_reasoning_event(event) == 1
    assert event["content"] == ["invalid"]
