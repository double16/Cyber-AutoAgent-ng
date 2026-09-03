"""Shared normalization for model-facing fixed-enum values."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any


def normalize_semantic_enum(
    value: Any,
    *,
    aliases: Mapping[str, str],
    field_name: str,
    logger: logging.Logger,
) -> Any:
    """Return a canonical enum value while leaving unknown values for validation."""

    if not isinstance(value, str):
        return value
    normalized = re.sub(r"[-\s]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized)
    canonical = aliases.get(normalized, normalized)
    if canonical != normalized:
        logger.info(
            "Normalized semantic enum field=%s alias=%s canonical=%s",
            field_name,
            normalized,
            canonical,
        )
    return canonical
