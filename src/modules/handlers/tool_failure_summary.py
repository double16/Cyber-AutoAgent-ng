"""Safe, bounded diagnostics for failed tool invocations."""

from __future__ import annotations

from typing import Any

from modules.utils.redaction import bounded_redacted_text

FAILURE_SUMMARY_MAX_CHARS = 1_000
UNKNOWN_TOOL_FAILURE_MESSAGE = "Tool failed without a diagnostic message."


def tool_result_text(result: Any) -> str:
    """Extract displayable text blocks from an SDK tool result."""

    if not isinstance(result, dict):
        return ""
    content = result.get("content", [])
    if not isinstance(content, list):
        return str(content or "").strip() if isinstance(content, str) else ""
    return "\n".join(
        str(item.get("text", ""))
        for item in content
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ).strip()


def failure_summary(result: Any, exception: Any = None) -> str:
    """Return a redacted diagnostic for a failed result or exception."""

    diagnostic = tool_result_text(result) or str(exception or "").strip()
    return bounded_redacted_text(diagnostic or UNKNOWN_TOOL_FAILURE_MESSAGE, FAILURE_SUMMARY_MAX_CHARS)


def normalize_failed_tool_result(
    result: Any,
    exception: Any = None,
    tool_use_id: str = "",
) -> tuple[Any, str]:
    """Attach a safe textual diagnostic to failures that otherwise have none."""

    is_error_result = isinstance(result, dict) and result.get("status", "success") == "error"
    if not is_error_result and exception is None:
        return result, ""

    summary = failure_summary(result, exception)
    if tool_result_text(result):
        return result, summary

    if isinstance(result, dict):
        normalized = dict(result)
        existing_content = result.get("content", [])
        normalized["content"] = list(existing_content) if isinstance(existing_content, list) else []
        normalized["content"].append({"text": summary})
    else:
        normalized = {"content": [{"text": summary}]}
    normalized["status"] = "error"
    if tool_use_id and not normalized.get("toolUseId"):
        normalized["toolUseId"] = tool_use_id
    return normalized, summary
