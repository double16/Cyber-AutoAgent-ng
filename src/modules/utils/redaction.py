"""Shared, conservative redaction helpers for diagnostics and exports."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

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


def redact_text(value: Any) -> str:
    """Remove common credentials from text while retaining diagnostic context."""

    redacted = str(value or "")
    for pattern in TEXT_REDACTION_PATTERNS:
        if pattern.groups >= 1:
            redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
        else:
            redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact(value: Any) -> Any:
    """Recursively redact common secret-bearing keys and text values."""

    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if SENSITIVE_KEY_PATTERN.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return redact_text(value) if isinstance(value, str) else value


def bounded_redacted_text(value: Any, limit: int = 4_000) -> str:
    """Return a redacted diagnostic excerpt with a deterministic upper bound."""

    text = redact_text(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[truncated]"
