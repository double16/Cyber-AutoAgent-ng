"""Export the prompt-review portion of a Langfuse session as an LLM-ready document.

This module intentionally lives outside ``modules.evaluation`` so this standalone
command does not initialize Ragas or the assessment evaluation stack.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from langfuse import Langfuse
import yaml


DEFAULT_LANGFUSE_HOST = "http://localhost:3000"
DEFAULT_OUTPUT_FORMAT = "yaml"
OUTPUT_FORMATS = ("json", "yaml")
REDACTED = "[REDACTED]"
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|secret|password|token|authorization|cookie|credential|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)
TEXT_REDACTION_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)\b((?:api[_-]?key|secret|password|token|access[_-]?key)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@"),
)

REVIEW_INSTRUCTIONS = (
    "Review each generation's prompts, recorded reasoning, response, and tool decisions. "
    "Assess prompt clarity and effectiveness, identify ambiguity or unnecessary context, "
    "and recommend specific improvements. Do not infer missing reasoning or rely on omitted tool results."
)


class ExportError(Exception):
    """Raised when a session cannot be exported."""


def _get(value: Any, name: str, default: Any = None) -> Any:
    """Read an API field from either a generated model or a mapping."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def redact(value: Any) -> Any:
    """Recursively remove common secrets while retaining the surrounding review context."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if SENSITIVE_KEY_PATTERN.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value

    redacted = value
    for pattern in TEXT_REDACTION_PATTERNS:
        if pattern.groups >= 1:
            redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
        else:
            redacted = pattern.sub(REDACTED, redacted)
    return redacted


def _as_json(value: Any) -> Any:
    """Decode serialized Langfuse I/O when it is valid JSON."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _text(value: Any) -> str:
    """Represent textual content without serializing arbitrary observation payloads."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("text", "content", "value", "reasoningText"):
            if key in value:
                return _text(value[key])
        return ""
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return "\n".join(part for item in value if (part := _text(item)))
    return str(value)


def _messages(value: Any) -> list[dict[str, str]]:
    """Normalize a generation input into only the system and user prompt messages."""
    value = _as_json(value)
    if isinstance(value, Mapping) and isinstance(value.get("messages"), list):
        value = value["messages"]
    if isinstance(value, Mapping) and "role" in value:
        value = [value]
    if not isinstance(value, list):
        text = _text(value)
        return [{"role": "user", "content": redact(text)}] if text else []

    messages = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role", "user")).lower()
        if role not in {"system", "user"}:
            continue
        content = _text(item.get("content", item.get("text")))
        if content:
            messages.append({"role": role, "content": redact(content)})
    return messages


def _tool_decision(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    name = value.get("name") or value.get("tool_name") or value.get("function", {}).get("name")
    if not name:
        return None
    arguments = value.get("input", value.get("arguments", value.get("function", {}).get("arguments", {})))
    return {"name": str(name), "arguments": redact(_as_json(arguments))}


def _model_output(value: Any) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Extract explicit reasoning, response text, and tool choices from a generation output."""
    reasoning: list[str] = []
    responses: list[str] = []
    tools: list[dict[str, Any]] = []

    def collect(item: Any, *, response_context: bool = True) -> None:
        item = _as_json(item)
        if isinstance(item, str):
            if response_context and item:
                responses.append(item)
            return
        if isinstance(item, list):
            for child in item:
                collect(child, response_context=response_context)
            return
        if not isinstance(item, Mapping):
            return

        for key in ("reasoningContent", "reasoning_content", "reasoning", "thinking"):
            if key in item:
                text = _text(item[key])
                if text:
                    reasoning.append(text)

        for key in ("toolUse", "tool_call"):
            if key in item:
                decision = _tool_decision(item[key])
                if decision:
                    tools.append(decision)
        for decision_data in item.get("tool_calls", []):
            decision = _tool_decision(decision_data)
            if decision:
                tools.append(decision)

        if "text" in item:
            text = _text(item["text"])
            if text:
                responses.append(text)
        for key in ("content", "message", "output"):
            if key in item:
                collect(item[key])

    collect(value)
    return ([redact(item) for item in reasoning], [redact(item) for item in responses], tools)


class LangfuseSessionExporter:
    """Fetch and reduce one Langfuse session to reviewable model-generation records."""

    def __init__(self, client: Any):
        self.client = client

    def export(self, session_id: str) -> dict[str, Any]:
        try:
            session = self.client.api.sessions.get(session_id)
        except Exception as error:
            raise ExportError(f"Unable to retrieve Langfuse session {session_id!r}: {error}") from error

        trace_summaries = list(_get(session, "traces", []) or [])
        if not trace_summaries:
            raise ExportError(f"Langfuse session {session_id!r} has no traces.")

        traces = []
        unavailable_traces = []
        for summary in trace_summaries:
            trace_id = _get(summary, "id")
            if not trace_id:
                continue
            try:
                traces.append(self.client.api.trace.get(trace_id))
            except Exception as error:
                unavailable_traces.append({"trace_id": str(trace_id), "error": redact(str(error))})

        if not traces:
            raise ExportError(f"No traces in Langfuse session {session_id!r} could be retrieved.")

        records = [self._trace_record(trace) for trace in sorted(traces, key=self._trace_sort_key)]
        packet: dict[str, Any] = {
            "schema_version": "1.0",
            "review_instructions": REVIEW_INSTRUCTIONS,
            "session_id": session_id,
            "traces": records,
        }
        if unavailable_traces:
            packet["unavailable_traces"] = unavailable_traces
        return packet

    @staticmethod
    def _trace_sort_key(trace: Any) -> str:
        return _timestamp(_get(trace, "timestamp") or _get(trace, "start_time") or _get(trace, "startTime")) or ""

    def _trace_record(self, trace: Any) -> dict[str, Any]:
        metadata = _get(trace, "metadata", {}) or {}
        attributes = _get(metadata, "attributes", {}) or {}
        role = _get(attributes, "langfuse.agent.type") or _get(metadata, "agent_type")
        generations = [
            self._generation_record(observation)
            for observation in sorted(_get(trace, "observations", []) or [], key=self._observation_sort_key)
            if str(_get(observation, "type", "")).upper() == "GENERATION"
        ]
        record: dict[str, Any] = {
            "trace_id": str(_get(trace, "id", "unknown")),
            "trace_name": redact(str(_get(trace, "name", "Unnamed trace"))),
            "generations": generations,
        }
        if role:
            record["agent_role"] = redact(str(role))
        return record

    @staticmethod
    def _observation_sort_key(observation: Any) -> str:
        return _timestamp(_get(observation, "start_time") or _get(observation, "startTime")) or ""

    @staticmethod
    def _generation_record(observation: Any) -> dict[str, Any]:
        reasoning, response, tools = _model_output(_get(observation, "output"))
        record: dict[str, Any] = {
            "generation_id": str(_get(observation, "id", "unknown")),
            "prompts": _messages(_get(observation, "input")),
        }
        if reasoning:
            record["recorded_reasoning"] = reasoning
        if response:
            record["response"] = "\n".join(response)
        if tools:
            record["tool_decisions"] = tools
        return record


def _resolve_output_format(output: str | None, requested_format: str | None) -> str:
    """Choose the explicit format, then a recognized file extension, then YAML."""
    if requested_format:
        return requested_format
    if output:
        suffix = Path(output).suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix in {".yaml", ".yml"}:
            return "yaml"
    return DEFAULT_OUTPUT_FORMAT


def _serialize_packet(packet: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    return yaml.safe_dump(packet, allow_unicode=True, sort_keys=False)


def _write_packet(packet: dict[str, Any], output: str | None, output_format: str) -> None:
    serialized = _serialize_packet(packet, output_format)
    if not output:
        sys.stdout.write(serialized)
        return

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as file:
        file.write(serialized)
        temporary_path = Path(file.name)
    temporary_path.replace(destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export a Langfuse session as an LLM-ready prompt review packet.")
    parser.add_argument("--session-id", required=True, help="Langfuse session ID to export")
    parser.add_argument("--output", help="Destination file; defaults to stdout")
    parser.add_argument(
        "--format",
        choices=OUTPUT_FORMATS,
        help="Output format. Defaults to the output extension, then YAML.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = os.getenv("LANGFUSE_HOST", DEFAULT_LANGFUSE_HOST)
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        print("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set.", file=sys.stderr)
        return 2

    client = Langfuse(public_key=public_key, secret_key=secret_key, base_url=host)
    try:
        _write_packet(
            LangfuseSessionExporter(client).export(args.session_id),
            args.output,
            _resolve_output_format(args.output, args.format),
        )
    except ExportError as error:
        print(f"Langfuse session export failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
