"""Provider-neutral normalization for model-emitted tool-call payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection

from modules.utils.json_repair import parse_json_response


@dataclass(frozen=True)
class NormalizedToolCall:
    """The canonical tool name and object-shaped arguments used for dispatch."""

    name: str
    arguments: dict[str, Any]


def normalize_tool_call_payload(
    payload: Any,
    *,
    registered_tool_names: Collection[str] | None = None,
) -> NormalizedToolCall:
    """Normalize supported direct and wrapped tool-call forms before dispatch.

    The function deliberately accepts only explicit tool-call envelopes. Ordinary JSON
    with unrelated fields is never converted into a tool invocation.
    """

    if isinstance(payload, str):
        payload = parse_json_response(payload, require_object=True)
    if not isinstance(payload, dict):
        raise ValueError("tool-call payload must be a JSON object")

    if payload.get("name") == "tool_use":
        nested_name = payload.get("tool_name") or payload.get("target")
        nested_arguments = payload.get("parameters", payload.get("arguments"))
    elif isinstance(payload.get("tool_call"), dict):
        nested = payload["tool_call"]
        nested_name = nested.get("name")
        nested_arguments = nested.get("arguments", nested.get("parameters"))
    else:
        nested_name = payload.get("name")
        nested_arguments = payload.get("arguments", payload.get("parameters", payload.get("input")))

    if not isinstance(nested_name, str) or not nested_name.strip():
        raise ValueError("tool-call name must be a non-empty string")
    if not isinstance(nested_arguments, dict):
        raise ValueError("tool-call arguments must be a JSON object")

    name = nested_name.strip()
    if registered_tool_names is not None and name not in registered_tool_names:
        available = ", ".join(sorted(registered_tool_names)) or "none"
        raise ValueError(f"tool-call name {name!r} is not registered; available tools: {available}")
    return NormalizedToolCall(name=name, arguments=nested_arguments)
