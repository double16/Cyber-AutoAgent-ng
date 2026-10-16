"""Filesystem diagnostics for native provider responses, separate from task evidence."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from opentelemetry import trace

from modules.utils.redaction import bounded_redacted_text

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ProviderResponseCapture:
    """Spool one invocation without retaining response history in memory."""

    def __init__(self, directory: Path | None, role: str, model: str) -> None:
        self.invocation_id = uuid.uuid4().hex
        self.path = directory / f"{self.invocation_id}.jsonl" if directory is not None else None
        self.role = role
        self.model = model
        self.bytes_written = 0
        self.truncated = False
        self.finished = False
        self.excerpt = ""
        self.stop_reason = None
        self.provider_complete = False
        self.usage: dict[str, Any] = {}
        self.file = None
        context = trace.get_current_span().get_span_context()
        self.correlation = {
            "trace_id": format(context.trace_id, "032x") if context.is_valid else "",
            "span_id": format(context.span_id, "016x") if context.is_valid else "",
        }
        if self.path is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
                self._write({"type": "start", "invocation_id": self.invocation_id,
                             "role": role, "model": model, **self.correlation})
            except OSError:
                self._disable()

    def _disable(self) -> None:
        logger.warning("Provider response capture unavailable invocation=%s", self.invocation_id)
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
        self.file = None

    def _write(self, record: dict[str, Any], *, response: bool = False) -> None:
        if self.file is None:
            return
        try:
            encoded = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode()
            if response and self.bytes_written + len(encoded) > MAX_RESPONSE_BYTES:
                self.truncated = True
                return
            self.file.write(encoded)
            self.file.flush()
            if response:
                self.bytes_written += len(encoded)
        except (OSError, ValueError, TypeError):
            self._disable()

    def request(self, request: dict[str, Any]) -> None:
        """Record only effective generation controls, never request content or credentials."""
        options = request.get("options") or {}
        self._write({"type": "settings", "think": request.get("think"), "stream": request.get("stream"),
                     "num_predict": options.get("num_predict"), "num_ctx": options.get("num_ctx"),
                     "temperature": options.get("temperature"), "structured": "format" in request})

    def response(self, event: Any) -> None:
        """Persist native SDK fields before any conversion or output validation."""
        if self.file is None:
            return
        try:
            data = event.model_dump(mode="json")
        except Exception:
            self._disable()
            return
        self.stop_reason = data.get("done_reason") or self.stop_reason
        self.provider_complete = bool(data.get("done") or data.get("done_reason"))
        self.usage = {key: data[key] for key in ("eval_count", "prompt_eval_count", "total_duration")
                      if data.get(key) is not None} or self.usage
        message = data.get("message") or {}
        if len(self.excerpt) < 4000:
            self.excerpt += str(message.get("content") or message.get("thinking") or "")[:4000 - len(self.excerpt)]
        self._write({"type": "response", "data": data}, response=True)

    def finish(self, success: bool) -> None:
        """Retain failed/incomplete responses; publish only redacted diagnostic text."""
        if self.finished:
            return
        self.finished = True
        if self.file is None:
            return
        retained = not success or not self.provider_complete or self.stop_reason in {"length", "max_tokens"}
        self._write({"type": "end", "complete": success and self.provider_complete, "retained": retained,
                     "stop_reason": self.stop_reason, "truncated": self.truncated, "usage": self.usage})
        try:
            if self.file is not None:
                self.file.close()
                self.file = None
            if not retained:
                self.path.unlink()
                return
            attributes = {
                "langfuse.observation.type": "event",
                "provider.capture.invocation_id": self.invocation_id,
                "provider.capture.path": str(self.path),
                "provider.capture.role": self.role,
                "provider.capture.model": self.model,
                "provider.capture.stop_reason": self.stop_reason or "incomplete",
                "provider.capture.truncated": self.truncated,
                "provider.capture.usage": json.dumps(self.usage),
                "provider.capture.excerpt": bounded_redacted_text(self.excerpt),
                **{f"provider.capture.{key}": value for key, value in self.correlation.items()},
            }
            with trace.get_tracer(__name__).start_as_current_span("provider_response_capture", attributes=attributes):
                pass
        except Exception:
            self._disable()
