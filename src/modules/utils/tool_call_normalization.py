"""Provider-neutral normalization for model-emitted tool-call payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from modules.utils.json_repair import parse_json_response


@dataclass(frozen=True)
class NormalizedToolCall:
    """The canonical tool name and object-shaped arguments used for dispatch."""

    name: str
    arguments: dict[str, Any]


_ATEM_TRAILING_ENVELOPE_RE = re.compile(
    r"^</atem:invoke>(?:\s*<atem:parameter\b[^>]*>.*)?$", re.DOTALL
)


def repair_model_response_tool_input(payload: Any) -> tuple[Any, tuple[str, ...]]:
    """Repair JSON values contaminated by a known trailing model tool envelope.

    Some model responses serialize a tool argument as JSON and then append an
    ``atem`` envelope to that same value. Only a complete leading JSON object or
    array followed exclusively by that known envelope is accepted. Other malformed
    values remain unchanged for normal tool-schema validation.
    """

    if not isinstance(payload, dict):
        return payload, ()

    repaired = dict(payload)
    repaired_fields: list[str] = []
    decoder = json.JSONDecoder()
    for field_name, value in payload.items():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text.startswith(("{", "[")):
            continue
        try:
            parsed, end = decoder.raw_decode(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, (dict, list)):
            continue
        if not _ATEM_TRAILING_ENVELOPE_RE.fullmatch(text[end:].strip()):
            continue
        repaired[field_name] = parsed
        repaired_fields.append(field_name)
    return repaired, tuple(repaired_fields)


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
