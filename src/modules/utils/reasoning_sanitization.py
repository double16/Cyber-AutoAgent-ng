"""Remove known provider control delimiters from generated reasoning."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_CHANNEL_LABELS = "thought|analysis|commentary|final"
_CHANNEL_MARKER_PREFIXES = (
    "<|channel>",
    "<|channel|>",
    "<|channel>|",
    "<|channel|>|",
)
_CHANNEL_MARKER = re.compile(
    rf"<\|channel(?:\|>|>)(?:\|)?\s*(?P<label>{_CHANNEL_LABELS})?(?=\s|<|$)",
    re.IGNORECASE,
)
_CONTROL_MARKER = re.compile(r"<\|(?:message|start|end)(?:\|>|>)", re.IGNORECASE)
_KNOWN_MARKERS = (
    *_CHANNEL_MARKER_PREFIXES,
    *(f"{prefix}{label}" for prefix in _CHANNEL_MARKER_PREFIXES for label in _CHANNEL_LABELS.split("|")),
    "<|message>",
    "<|message|>",
    "<|start>",
    "<|start|>",
    "<|end>",
    "<|end|>",
)


@dataclass
class ReasoningSanitizationState:
    """Small per-stream state for provider delimiter fragments."""

    trailing: dict[str, str] = field(default_factory=dict)
    awaiting_channel_label: set[str] = field(default_factory=set)


def _trailing_marker_prefix(text: str) -> str:
    """Return a suffix that could become one recognized marker in the next chunk."""

    for length in range(min(len(text), max(map(len, _KNOWN_MARKERS)) - 1), 1, -1):
        suffix = text[-length:]
        if any(marker.startswith(suffix) for marker in _KNOWN_MARKERS):
            return suffix
    return ""


def sanitize_reasoning_control_text(
    text: str,
    state: ReasoningSanitizationState | None = None,
    stream_key: str = "reasoning",
) -> tuple[str, int]:
    """Return text without recognized provider delimiters and a removal count."""

    value = str(text or "")
    if state is not None:
        value = state.trailing.pop(stream_key, "") + value
        if stream_key in state.awaiting_channel_label:
            label = re.match(rf"^\s*(?:\|\s*)?(?:{_CHANNEL_LABELS})(?=\s|<|$)", value, re.IGNORECASE)
            if label:
                value = value[label.end():]
            state.awaiting_channel_label.discard(stream_key)

        trailing = _trailing_marker_prefix(value)
        if trailing:
            value = value[: -len(trailing)]
            state.trailing[stream_key] = trailing

    removed = 0

    def remove_channel(match: re.Match[str]) -> str:
        nonlocal removed
        removed += 1
        if state is not None and not match.group("label"):
            state.awaiting_channel_label.add(stream_key)
        return ""

    value = _CHANNEL_MARKER.sub(remove_channel, value)
    value, control_removed = _CONTROL_MARKER.subn("", value)
    return value, removed + control_removed


def sanitize_reasoning_event(
    event: dict[str, Any], state: ReasoningSanitizationState | None = None
) -> int:
    """Sanitize reasoning fields in one provider event before it reaches telemetry."""

    removed = 0
    visited: set[int] = set()

    def sanitize_string(container: dict[str, Any], key: str, stream_key: str) -> None:
        nonlocal removed
        value = container.get(key)
        if isinstance(value, str):
            clean, count = sanitize_reasoning_control_text(value, state, stream_key)
            container[key] = clean
            removed += count

    def visit_content(content: Any, stream_key: str) -> None:
        if not isinstance(content, list):
            return
        cleaned_blocks = []
        for block in content:
            if not isinstance(block, dict) or id(block) in visited:
                cleaned_blocks.append(block)
                continue
            visited.add(id(block))
            reasoning = block.get("reasoningContent")
            if isinstance(reasoning, dict):
                reasoning_text = reasoning.get("reasoningText")
                if isinstance(reasoning_text, dict):
                    sanitize_string(reasoning_text, "text", f"{stream_key}.reasoning")
                    if not reasoning_text.get("text") and set(block) == {"reasoningContent"}:
                        continue
            cleaned_blocks.append(block)
        content[:] = cleaned_blocks

    sanitize_string(event, "reasoningText", "reasoningText")
    visit_content(event.get("content"), "content")

    message = event.get("message")
    if isinstance(message, dict):
        visit_content(message.get("content"), "message")

    delta = event.get("delta")
    if isinstance(delta, dict):
        visit_content(delta.get("content"), "delta")

    for container_name in ("contentBlockDelta", "contentBlockStart"):
        container = event.get(container_name)
        if not isinstance(container, dict):
            continue
        nested = container.get("delta") if container_name == "contentBlockDelta" else container.get("start")
        if isinstance(nested, dict):
            sanitize_string(nested, "text", f"{container_name}.text")

    return removed
