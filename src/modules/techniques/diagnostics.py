"""Private CLI diagnostics: human progress and redacted, complete textual conversations."""

import copy
import io
import json
import logging
import os
import re
import sys
import threading
import traceback
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
)

from modules.techniques.events import TechniqueEvents
from modules.utils.redaction import redact, redact_text


class DiagnosticWarnings(logging.Handler):
    """Capture Python warnings without enabling provider debug payloads."""

    def __init__(self, session):
        super().__init__(logging.WARNING)
        self.session = session

    def emit(self, record):
        fields = {"logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            fields["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        self.session.emit("python_warning", level=record.levelname.lower(), **fields)


class DiagnosticOutput(io.TextIOBase):
    """Buffer incidental printed lines so split writes cannot bypass redaction."""

    def __init__(self, session):
        self.session = session
        self.buffered = ""

    def write(self, text):
        with self.session.lock:
            self.buffered += text
            while "\n" in self.buffered:
                line, self.buffered = self.buffered.split("\n", 1)
                if line.strip():
                    self.session.emit("incidental_output", level="debug", message=line)
        return len(text)

    def flush(self):
        with self.session.lock:
            if self.buffered:
                self.session.emit("incidental_output", level="debug", message=self.buffered)
                self.buffered = ""


class DiagnosticSession:
    def __init__(self, *, verbose=False, quiet=False, stderr=None):
        self.run_id = str(uuid.uuid4())
        self.verbose = verbose
        self.quiet = quiet
        self.stderr = stderr if stderr is not None else sys.stderr
        self.private_values: tuple[str, ...] = ()
        self.path: Path | None = None
        self.stream = None
        self.lock = threading.RLock()
        self.warning_handler = None
        self.output = DiagnosticOutput(self)

    def _safe(self, value: Any):
        if isinstance(value, dict):
            value = {
                str(key): "[binary omitted]" if key in {"image", "video", "audio", "bytes"} else self._safe(item)
                for key, item in value.items()
            }
        elif isinstance(value, (list, tuple)):
            value = [self._safe(item) for item in value]
        elif isinstance(value, str):
            value = redact_text(value)
            for private in self.private_values:
                value = re.sub(re.escape(private), "[operation-specific]", value, flags=re.IGNORECASE)
        elif isinstance(value, bytes):
            value = "[binary omitted]"
        elif value is not None and not isinstance(value, (int, float, bool)):
            value = self._safe(str(value))
        return redact(value)

    def open(self, output_dir: str, override: str | None = None):
        path = Path(override) if override else (
            Path(output_dir) / "techniques" / "logs"
            / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S.%fZ')}_{self.run_id}.jsonl"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.stream = open(path, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600))
        except OSError as error:
            if override:
                raise
            self.emit("log_unavailable", level="warning", message=f"Automatic log unavailable: {error}")
            return
        self.path = path
        self.emit("log_opened", message=f"Diagnostic log: {path}")

    @contextmanager
    def capture_warnings(self):
        handler = DiagnosticWarnings(self)
        self.warning_handler = handler
        root = logging.getLogger()
        restored = []
        attached = [root]
        consoles = (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)
        loggers = [root, *[item for item in root.manager.loggerDict.copy().values()
                          if isinstance(item, logging.Logger)]]
        for logger in loggers:
            noisy = [item for item in logger.handlers if isinstance(item, logging.StreamHandler)
                     and not isinstance(item, logging.FileHandler)
                     and any(item.stream is stream for stream in consoles)]
            if noisy:
                restored.append((logger, list(logger.handlers)))
                for item in noisy:
                    logger.removeHandler(item)
                if logger is not root and not logger.propagate:
                    logger.addHandler(handler)
                    attached.append(logger)
        root.addHandler(handler)
        try:
            yield
        finally:
            for logger in attached:
                logger.removeHandler(handler)
            for logger, handlers in restored:
                logger.handlers[:] = handlers
            handler.close()
            self.warning_handler = None

    def emit(self, event_type: str, *, level="info", **fields):
        with self.lock:
            record = self._safe({
                "timestamp": datetime.now(UTC).isoformat(), "run_id": self.run_id,
                **TechniqueEvents.current_context(), **fields, "type": event_type, "level": level,
            })
            if self.stream is not None:
                try:
                    self.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    self.stream.flush()
                except (OSError, ValueError):
                    try:
                        self.stream.close()
                    except OSError:
                        pass
                    self.stream = None
                    print("Warning: diagnostic log write failed; continuing with stderr progress", file=self.stderr)
            if (level == "debug" and not self.verbose) or (self.quiet and level not in {"warning", "error", "critical"}):
                return
            message = record.get("message") or record.get("reason") or record.get("diagnostic") or ""
            label = record.get("stage") or record.get("role") or event_type.removeprefix("technique_")
            if level == "debug":
                label = f"{label} {event_type}"
            status = record.get("status", "")
            if (record.get("stage") == "request_alignment" and record.get("adjusted_request")
                    and record.get("original_request") != record["adjusted_request"]):
                message = f"Aligned request: {record['adjusted_request']}. {message}"
            details = []
            for key in ("component_id", "research_round", "attempt", "fetched_count", "selected_count",
                        "delivered_count", "unresolved_count", "elapsed_seconds", "added", "updated", "unchanged",
                        "outcome", "title", "revision_id"):
                if key in record:
                    details.append(f"{key}={record[key]}")
            if self.verbose:
                for key in ("role", "provider", "model_id", "prompt_limit", "input_tokens",
                            "activity_id", "role_invocation_id", "model_attempt", "tool_call_id"):
                    if key in record:
                        details.append(f"{key}={record[key]}")
            text = " ".join(str(item) for item in (label, status, *details, message) if item)
            # Keep terminal records on one line; complete conversations are file-only.
            text = " ".join(text.split())[:2000]
            print(f"[{level}] {text}", file=self.stderr, flush=True)
            if self.verbose and record.get("traceback"):
                print(record["traceback"], file=self.stderr)

    def lifecycle(self, event):
        fields = dict(event)
        event_type = fields.pop("type")
        level = "warning" if event_type == "technique_warning" else "info"
        if fields.get("status") in {"failed", "unresolved"}:
            level = "warning"
        self.emit(event_type, level=level, **fields)

    @contextmanager
    def role(self, role, provider, model):
        invocation = str(uuid.uuid4())
        fields = {"role": role, "role_invocation_id": invocation, "provider": provider, "model_id": model}
        started = monotonic()
        self.emit("role_started", level="debug", **fields)
        try:
            yield ConversationDiagnostics(self, fields)
        except BaseException as error:
            self.failure("role_failed", error, level="debug", **fields,
                         elapsed_seconds=round(monotonic() - started, 3))
            raise
        else:
            self.emit("role_completed", level="debug", **fields,
                      elapsed_seconds=round(monotonic() - started, 3))

    def failure(self, event_type, error, **fields):
        self.emit(event_type, status="failed", message=str(error), exception_type=type(error).__name__,
                  traceback="".join(traceback.format_exception(error)), **fields)

    def close(self):
        with self.lock:
            if self.stream is not None:
                try:
                    self.stream.close()
                except OSError:
                    print("Warning: diagnostic log close failed", file=self.stderr)
                self.stream = None


class ConversationDiagnostics(HookProvider):
    """One provider per role invocation; model attempts and research tools share its correlation."""

    def __init__(self, session, fields):
        self.session = session
        self.fields = fields
        self.attempt = 0
        self.started = 0.0

    @staticmethod
    def structured_fields(agent):
        from modules.agents.native_structured import NativeFinalizer

        model = getattr(agent, "model", None)
        if not isinstance(model, NativeFinalizer):
            return {}
        return {"schema_id": model.schema_id, "schema_name": model.output_model.__name__,
                "structured_mode": "native" if model.native else "tool",
                **({"fallback_reason": "native_unsupported"} if not model.native
                   and getattr(model.model, "_cyber_native_structured_output", False) else {})}

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def before_model(self, event):
        self.attempt += 1
        self.started = monotonic()
        self.session.emit(
            "model_input", level="debug", **self.fields, model_attempt=self.attempt,
            system_prompt=event.agent.system_prompt, messages=copy.deepcopy(event.agent.messages),
            tools=[item.tool_spec for item in event.agent.tool_registry.registry.values()],
            **({"input_tokens": event.projected_input_tokens} if event.projected_input_tokens is not None else {}),
            prompt_limit=event.agent._prompt_token_limit,
            **self.structured_fields(event.agent),
        )

    def after_model(self, event):
        fields = {**self.fields, "model_attempt": self.attempt,
                  "elapsed_seconds": round(monotonic() - self.started, 3),
                  **self.structured_fields(getattr(event, "agent", None))}
        if event.exception is not None:
            self.session.failure(
                "model_failed", event.exception, level="debug", **fields,
                rejected_response=getattr(event.exception, "rejected_response", None),
                validation_errors=getattr(event.exception, "validation_errors", None),
            )
        else:
            response = event.stop_response
            self.session.emit("model_output", level="debug", **fields,
                              response=response.message if response else None,
                              stop_reason=response.stop_reason if response else None,
                              **({"validation_outcome": "validated" if fields["structured_mode"] == "native"
                                 else "schema_tool_pending"} if "schema_id" in fields else {}))

    def before_tool(self, event):
        self.session.emit("tool_input", level="debug", **self.fields, model_attempt=self.attempt,
                          tool_call_id=event.tool_use["toolUseId"], tool_use=event.tool_use)

    def after_tool(self, event):
        fields = {**self.fields, "model_attempt": self.attempt, "tool_call_id": event.tool_use["toolUseId"],
                  "elapsed_seconds": event.duration, "result": event.result}
        if event.exception is not None:
            self.session.failure("tool_failed", event.exception, level="debug", **fields)
        else:
            self.session.emit("tool_output", level="debug", **fields)
