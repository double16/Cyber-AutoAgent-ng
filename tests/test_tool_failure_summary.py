from modules.handlers.tool_failure_summary import (
    FAILURE_SUMMARY_MAX_CHARS,
    UNKNOWN_TOOL_FAILURE_MESSAGE,
    failure_summary,
    normalize_failed_tool_result,
)


def test_normalize_failed_tool_result_uses_redacted_exception_when_content_is_empty():
    result, summary = normalize_failed_tool_result(
        {"status": "error", "content": [], "toolUseId": "tool-1"},
        RuntimeError("authorization: Bearer secret-value"),
    )

    assert result["status"] == "error"
    assert result["content"] == [{"text": "authorization: Bearer [REDACTED]"}]
    assert summary == "authorization: Bearer [REDACTED]"


def test_normalize_failed_tool_result_uses_fallback_and_preserves_existing_error_text():
    fallback, fallback_summary = normalize_failed_tool_result(
        {"status": "error", "content": []},
        tool_use_id="tool-1",
    )
    existing = {"status": "error", "content": [{"text": "Validation failed"}]}
    preserved, preserved_summary = normalize_failed_tool_result(existing, RuntimeError("ignored"))

    assert fallback["toolUseId"] == "tool-1"
    assert fallback["content"] == [{"text": UNKNOWN_TOOL_FAILURE_MESSAGE}]
    assert fallback_summary == UNKNOWN_TOOL_FAILURE_MESSAGE
    assert preserved is existing
    assert preserved_summary == "Validation failed"


def test_failure_summary_redacts_and_bounds_existing_text():
    result = {"status": "error", "content": [{"text": "token=abc " + ("x" * 2_000)}]}

    summary = failure_summary(result)

    assert "token=[REDACTED]" in summary
    assert len(summary) <= FAILURE_SUMMARY_MAX_CHARS + len("…[truncated]")
