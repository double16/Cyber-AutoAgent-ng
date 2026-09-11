"""Canonical parsing helpers for operation-local artifact references."""

from __future__ import annotations

import re
from typing import Any

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

_TRAILING_REFERENCE_PUNCTUATION = "`'\".,;:)]}>"
_LEADING_REFERENCE_WRAPPERS = "`'\"[({<"
_DELIMITED_REFERENCE_PATTERN = re.compile(r"[,|\n]")
_TOOL_REFERENCE_LIST_FIELDS = {
    "store_observation": ("artifacts",),
    "store_finding": ("artifacts",),
    "record_finding_validation": ("evidence_artifacts", "control_artifacts"),
    "record_task_acceptance": ("evidence_refs",),
    "store_objective_candidate": ("evidence_artifacts",),
    "record_objective_validation": ("evidence_artifacts",),
    "discover_flag_candidates": ("evidence_artifacts",),
}


def normalize_artifact_reference_token(value: Any) -> str:
    """Trim presentation syntax from one artifact reference without resolving it."""

    if not isinstance(value, str):
        raise TypeError("artifact reference must be a string")
    reference = value.strip().lstrip(_LEADING_REFERENCE_WRAPPERS).rstrip(_TRAILING_REFERENCE_PUNCTUATION)
    if not reference:
        raise ValueError("artifact reference must not be empty")
    return reference


def split_delimited_reference_values(value: Any, *, allow_delimited_strings: bool) -> list[str]:
    """Return reference values while accepting model-friendly delimited strings when enabled."""

    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    references: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise TypeError("artifact reference must be a string")
        text = item.strip()
        if allow_delimited_strings:
            text = text.rstrip(_TRAILING_REFERENCE_PUNCTUATION)
            parts = _DELIMITED_REFERENCE_PATTERN.split(text)
        else:
            parts = [text]
        if any(not part.strip() for part in parts):
            raise ValueError("artifact reference list contains an empty value")
        references.extend(normalize_artifact_reference_token(part) for part in parts)
    return references


class ArtifactReferenceInputNormalizationHook(HookProvider):
    """Normalize legacy string-shaped reference lists before standard tool validation."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._normalize_tool_input)

    def _normalize_tool_input(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_name = str(tool_use.get("name") or "")
        fields = _TOOL_REFERENCE_LIST_FIELDS.get(tool_name, ())
        tool_input = tool_use.get("input")
        if not fields or not isinstance(tool_input, dict):
            return
        normalized_input = dict(tool_input)
        changed = False
        for field_name in fields:
            if field_name not in normalized_input or normalized_input[field_name] is None:
                continue
            try:
                normalized_value = split_delimited_reference_values(
                    normalized_input[field_name],
                    allow_delimited_strings=True,
                )
            except (TypeError, ValueError):
                continue
            if normalized_value != normalized_input[field_name]:
                normalized_input[field_name] = normalized_value
                changed = True
        if changed:
            tool_use["input"] = normalized_input
