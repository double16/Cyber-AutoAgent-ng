"""Bounded, best-effort technique lifecycle events with task-local correlation."""

import logging
import re
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from modules.utils.redaction import redact

logger = logging.getLogger(__name__)
_context = ContextVar("technique_event_context", default=None)


class TechniqueEvents:
    def __init__(self) -> None:
        self.callback = None
        self.private_values = ()
        self._warnings = deque(maxlen=256)

    @contextmanager
    def scope(self, **metadata):
        token = _context.set({**(_context.get() or {}), **metadata})
        try:
            yield
        finally:
            _context.reset(token)

    def _private_fields(self, value):
        if isinstance(value, str):
            for private in self.private_values:
                value = re.sub(re.escape(private), "[operation-specific]", value, flags=re.IGNORECASE)
            return value
        if isinstance(value, dict):
            return {key: self._private_fields(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._private_fields(item) for item in value]
        return value

    def emit(self, event_type, **fields):
        if self.callback is None:
            return
        event = self._private_fields(redact({"type": event_type, **(_context.get() or {}), **fields}))
        for key in ("operation_id", "task_uid"):
            if not event.get(key):
                event.pop(key, None)
        for key, value in event.items():
            if isinstance(value, str):
                event[key] = value[:1000]
        if event_type == "technique_warning":
            key = tuple(
                event.get(field) for field in ("operation_id", "task_uid", "stage", "diagnostic", "retry_pending")
            )
            if key in self._warnings:
                return
            self._warnings.append(key)
        try:
            self.callback(event)
        except Exception:
            logger.debug("Technique event delivery unavailable", exc_info=True)

    @contextmanager
    def stage(self, stage, attempt=None):
        started = monotonic()
        fields = {"stage": stage}
        if attempt is not None:
            fields["attempt"] = attempt
        self.emit("technique_build_progress", status="started", **fields)
        with self.scope(stage=stage, **({"attempt": attempt} if attempt is not None else {})):
            try:
                yield fields
            except BaseException as error:
                self.emit(
                    "technique_build_progress", status="failed", elapsed_seconds=round(monotonic() - started, 3),
                    reason=str(error), **fields,
                )
                raise
            else:
                self.emit(
                    "technique_build_progress", status="completed", elapsed_seconds=round(monotonic() - started, 3),
                    **fields,
                )

    @staticmethod
    def current_context():
        return dict(_context.get() or {})

    @staticmethod
    def current_activity_id():
        return _context.get()["activity_id"]

    @staticmethod
    def activity_id():
        return str(uuid.uuid4())
