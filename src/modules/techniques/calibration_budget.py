"""Persistent bounded accounting and opt-in transport instrumentation for isolated trials."""

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path


class CalibrationBudgetExhausted(RuntimeError):
    """An incomplete evaluation must remain incomplete when its allowance is exhausted."""


class CalibrationBudget:
    def __init__(self, path, limit=30):
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError("Calibration limit must be an integer from 1 to 30")
        self.path = Path(path)
        self.limit = limit
        self.lock = threading.RLock()
        if not self.path.exists():
            self._write({"limit": limit, "requests": 0, "roles": 0})
        if self.counts()["limit"] != limit:
            raise ValueError("Existing calibration budget has a different limit")

    def _write(self, payload):
        temporary = self.path.with_name(f".{uuid.uuid4()}.tmp")
        try:
            with open(temporary, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
                json.dump(payload, stream)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def counts(self):
        return json.loads(self.path.read_text())

    def reserve(self, kind):
        if kind not in {"requests", "roles"}:
            raise ValueError("Unknown calibration counter")
        with self.lock:
            counts = self.counts()
            if counts[kind] >= self.limit or counts["requests"] >= self.limit:
                raise CalibrationBudgetExhausted("Cumulative calibration allowance exhausted")
            counts[kind] += 1
            self._write(counts)
            return counts[kind]

    @contextmanager
    def instrument(self, transport, method, directory, *, validate_request=None):
        """Instrument a selected transport adapter; generic accounting has no provider assumptions."""
        original = getattr(transport, method)
        destination = Path(directory)

        async def counted(client, *args, **kwargs):
            if validate_request is not None:
                validate_request(kwargs)
            number = self.reserve("requests")
            path = destination / f"provider-request-{number:02d}.json"
            with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
                json.dump({"args": args, "kwargs": kwargs}, stream, ensure_ascii=False, default=str)
            return await original(client, *args, **kwargs)

        setattr(transport, method, counted)
        try:
            yield
        finally:
            setattr(transport, method, original)
