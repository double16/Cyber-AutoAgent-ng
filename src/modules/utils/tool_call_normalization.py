"""Provider-neutral normalization for model-emitted tool-call payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from modules.utils.json_repair import close_unbalanced_json_containers, parse_json_response


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


@dataclass(frozen=True)
class JSONContainerDiagnostic:
    """Describe a container syntax failure without retaining argument values."""

    path: str
    expected_types: tuple[str, ...]
    line: int
    column: int


def normalize_json_container_arguments(
    payload: Any,
    schema: dict[str, Any],
    *,
    diagnostics: list[JSONContainerDiagnostic] | None = None,
) -> tuple[Any, tuple[str, ...]]:
    """Decode JSON-wrapped containers only where the tool schema excludes string inputs."""

    paths: list[str] = []

    def reject_constant(_value: str) -> None:
        raise ValueError("Non-standard JSON constant")

    def branches(node: Any, seen: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        if not isinstance(node, dict):
            return [{}]
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/") or reference in seen:
                return [{}]
            resolved: Any = schema
            for part in reference[2:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(resolved, dict) or key not in resolved:
                    return [{}]
                resolved = resolved[key]
            # Sibling constraints are still enforced by normal validation.
            return branches(resolved, seen | {reference})
        alternatives = node.get("anyOf", node.get("oneOf"))
        if isinstance(alternatives, list):
            return [variant for alternative in alternatives for variant in branches(alternative, seen)]
        return [node]

    def normalize(value: Any, node: Any, path: str, depth: int = 0) -> Any:
        if depth > 64:
            return value
        variants = branches(node)
        # String-accepting and unconstrained schemas must preserve the supplied text.
        if isinstance(value, str):
            if any(variant.get("type") is None or "string" in (
                variant["type"] if isinstance(variant["type"], list) else [variant["type"]]
            ) for variant in variants):
                return value
            expected_types = tuple(sorted({kind for variant in variants for kind in (
                variant["type"] if isinstance(variant["type"], list) else [variant["type"]]
            ) if kind in {"array", "object"}}))
            if not expected_types:
                return value
            decoded = value
            for _ in range(2):
                if not isinstance(decoded, str):
                    break
                try:
                    decoded = json.loads(decoded, parse_constant=reject_constant)
                except json.JSONDecodeError as error:
                    try:
                        decoded = json.loads(close_unbalanced_json_containers(decoded),
                                             parse_constant=reject_constant)
                    except (ValueError, RecursionError):
                        if diagnostics is not None:
                            diagnostics.append(JSONContainerDiagnostic(
                                path, expected_types, error.lineno, error.colno,
                            ))
                        return value
                except (ValueError, RecursionError):
                    return value
            kind = "object" if isinstance(decoded, dict) else "array" if isinstance(decoded, list) else None
            if kind is None or not any(kind in (
                variant.get("type") if isinstance(variant.get("type"), list) else [variant.get("type")]
            ) for variant in variants):
                return value
            paths.append(path)
            value = decoded
        kind = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else None
        matching = [variant for variant in variants if kind is not None and kind in (
            variant.get("type") if isinstance(variant.get("type"), list) else [variant.get("type")]
        )]
        # Ambiguous container alternatives retain normal schema validation without nested guesses.
        if len(matching) != 1:
            return value
        selected = matching[0]
        if isinstance(value, dict):
            properties = selected.get("properties", {})
            additional = selected.get("additionalProperties", {})
            return {key: normalize(item, properties.get(key, additional), f"{path}.{key}", depth + 1)
                    for key, item in value.items()}
        if isinstance(value, list):
            prefix = selected.get("prefixItems", [])
            return [normalize(item, prefix[index] if index < len(prefix) else selected.get("items", {}),
                              f"{path}[{index}]", depth + 1) for index, item in enumerate(value)]
        return value

    normalized = normalize(payload, schema, "$")
    return normalized, tuple(paths)
