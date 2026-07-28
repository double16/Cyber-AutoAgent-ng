"""Best-effort normalization for JSON returned by language models."""

from __future__ import annotations

import json
import re
from typing import Any


_JSON_ESCAPES = frozenset('"\\/bfnrtu')
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def strip_js_comments(text: str) -> str:
    """Remove JavaScript comments without changing comment-like string content."""

    output: list[str] = []
    in_string = False
    quote = ""
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        if char in ('"', "'"):
            in_string = True
            quote = char
            output.append(char)
            index += 1
        elif text.startswith("//", index):
            index = text.find("\n", index + 2)
            if index < 0:
                break
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
        else:
            output.append(char)
            index += 1
    return "".join(output)


def _candidate(text: str) -> str:
    blocks = _CODE_BLOCK_RE.findall(text)
    if blocks:
        return blocks[0].strip()
    starts = [position for position in (text.find("{"), text.find("[")) if position >= 0]
    if not starts:
        return text.strip()
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1] if end >= start else text.strip()


def _next_significant(text: str, index: int) -> str:
    while index < len(text) and text[index].isspace():
        index += 1
    return text[index] if index < len(text) else ""


def repair_json_text(text: str) -> str:
    """Repair common model JSON mistakes while preserving valid JSON semantics."""

    source = strip_js_comments(_candidate(text)).strip()
    output: list[str] = []
    in_string = False
    string_is_key = False
    escaped = False
    previous_significant = ""
    index = 0
    while index < len(source):
        char = source[index]
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
                string_is_key = previous_significant in ("{", ",")
            elif not char.isspace():
                previous_significant = char
            index += 1
            continue

        if escaped:
            if char == "'" or char not in _JSON_ESCAPES:
                output.append(char)
            else:
                output.extend(("\\", char))
            escaped = False
            index += 1
            continue
        if char == "\\":
            if index + 1 < len(source) and source[index + 1] == "'":
                output.append("'")
                index += 2
            elif index + 1 < len(source) and source[index + 1] not in _JSON_ESCAPES:
                output.append(source[index + 1])
                index += 2
            else:
                escaped = True
                output.append(char)
                index += 1
            continue
        if char == '"':
            following = _next_significant(source, index + 1)
            if following in (",", "}", "]", "") or (following == ":" and string_is_key):
                output.append(char)
                in_string = False
            else:
                output.extend(("\\", char))
            index += 1
            continue
        output.append(char)
        index += 1

    repaired = "".join(output)
    return re.sub(r",\s*([}\]])", r"\1", repaired)


def parse_json_response(text: str, *, require_object: bool = False) -> Any:
    """Normalize and parse a model response intended to contain JSON."""

    if not isinstance(text, str):
        raise ValueError("agent response must be text")
    parsed = json.loads(repair_json_text(text))
    if require_object and not isinstance(parsed, dict):
        raise ValueError("agent response must be a JSON object")
    return parsed
