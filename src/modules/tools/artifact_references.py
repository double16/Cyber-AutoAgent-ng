"""Canonical parsing helpers for operation-local artifact references."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

_REFERENCE_PREFIX_PATTERN = re.compile(r"^(artifact(?:_id)?):\s*", re.IGNORECASE)
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
    reference = value.strip().lstrip(_LEADING_REFERENCE_WRAPPERS)
    prefix = _REFERENCE_PREFIX_PATTERN.match(reference)
    if prefix and not reference[prefix.end():].strip().strip(_TRAILING_REFERENCE_PUNCTUATION):
        raise ValueError("artifact reference must not be empty")
    reference = reference.rstrip(_TRAILING_REFERENCE_PUNCTUATION)
    prefix = _REFERENCE_PREFIX_PATTERN.match(reference)
    if prefix:
        payload = reference[prefix.end():].strip().strip("`'\"")
        if not payload:
            raise ValueError("artifact reference must not be empty")
        reference = f"{prefix.group(1).lower()}:{payload}"
    if not reference:
        raise ValueError("artifact reference must not be empty")
    return reference



def require_artifact_reference(value: Any) -> str:
    """Validate reference kind before file resolution so memories are not reported as missing files."""

    reference = normalize_artifact_reference_token(value)
    if not _REFERENCE_PREFIX_PATTERN.match(reference):
        raise ValueError(
            "Expected artifact: or artifact_id: evidence reference; memory and other kinds are unsupported"
        )
    return reference


@dataclass(frozen=True)
class ArtifactReferenceMatch:
    """A normalized reference and its original text span for safe replacement."""

    reference: str
    start: int
    end: int
    raw: str


_REFERENCE_TEXT_PATTERN = re.compile(
    r"""(?<![A-Za-z0-9_:/.-])(?:"""
    r"""(?P<quote>[`'"])(?P<wrapped>artifact(?:_id)?:[ \t]*[^\n]+?)(?P=quote)|"""
    r"""(?P<prefix>artifact(?:_id)?):[ \t]*"""
    r"""(?:"(?P<double>[^"\n]+)"|'(?P<single>[^'\n]+)'|`(?P<tick>[^`\n]+)`|"""
    r"""(?P<plain>[^\s,|\\\]\}\)\[\{\(<>\"'`]+)))""",
    re.IGNORECASE,
)
_BARE_REFERENCE_TEXT_PATTERN = re.compile(
    r"""(?<![A-Za-z0-9_:/.-])(?:artifacts|outputs)/[^\s,|\\\]\}\)\[\{\(<>\"'`]+""",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_TEXT_PATTERN = re.compile(r"""(?<![A-Za-z0-9_.:/\-])(/[^\s'"`<>()\[\]{}\\]+)""")
_LEGACY_ARTIFACT_LABEL_PATTERN = re.compile(r"\bartifact\s+paths?:[ \t]*(\S+)", re.IGNORECASE)


def iter_artifact_reference_matches(
    text: str, *, include_bare_paths: bool = False,
) -> Iterator[ArtifactReferenceMatch]:
    """Match explicit references, optionally including supported bare report paths."""

    matches = list(_REFERENCE_TEXT_PATTERN.finditer(text))
    spans = [(match.start(), match.end()) for match in matches]
    if include_bare_paths:
        matches.extend(
            match for match in _BARE_REFERENCE_TEXT_PATTERN.finditer(text)
            if not any(start <= match.start() < end for start, end in spans)
        )
    for match in sorted(matches, key=lambda item: item.start()):
        raw = match.group(0)
        end = match.end()
        # Keep sentence punctuation outside replacement spans.
        if raw[-1:] not in {"'", '"', "`"}:
            raw = raw.rstrip(_TRAILING_REFERENCE_PUNCTUATION)
            end = match.start() + len(raw)
        try:
            reference = normalize_artifact_reference_token(raw)
        except (TypeError, ValueError):
            continue
        if reference.lower().startswith(("artifacts/", "outputs/")):
            reference = f"artifact:{reference}"
        yield ArtifactReferenceMatch(reference, match.start(), end, raw)


def extract_artifact_references(
    *values: Any, include_bare_paths: bool = False, allow_legacy_labels: bool = False,
) -> list[str]:
    """Extract references in encounter order without resolving or authorizing files."""

    references: list[str] = []

    def add(value: str) -> None:
        try:
            reference = normalize_artifact_reference_token(value)
        except (TypeError, ValueError):
            return
        if reference not in references:
            references.append(reference)

    def visit(value: Any, field: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, str(key).lower())
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item, field)
        elif isinstance(value, str):
            if field == "artifact_id" and value.strip():
                add(value if value.strip().lower().startswith("artifact_id:") else f"artifact_id:{value}")
                return
            elif field in {"artifact", "artifact_ref"} and value.strip().lower().startswith(
                ("artifact:", "artifact_id:")
            ):
                add(value)
                return
            text = (
                _LEGACY_ARTIFACT_LABEL_PATTERN.sub(
                    lambda match: ",".join(f"artifact:{path}" for path in match.group(1).split(",")), value,
                ) if allow_legacy_labels else value
            )
            for match in iter_artifact_reference_matches(text, include_bare_paths=include_bare_paths):
                add(match.reference)

    for value in values:
        visit(value)
    return references


def replace_artifact_references(
    text: str, replacement: Callable[[str], str], *, include_bare_paths: bool = False,
    accept_reference: Callable[[str], bool] | None = None,
) -> tuple[str, int]:
    """Replace complete original reference spans without changing surrounding text."""

    matches = [
        match for match in iter_artifact_reference_matches(text, include_bare_paths=include_bare_paths)
        if accept_reference is None or accept_reference(match.reference)
    ]
    for match in reversed(matches):
        text = text[:match.start] + replacement(match.reference) + text[match.end:]
    return text, len(matches)


def extract_absolute_artifact_paths(text: str) -> list[str]:
    """Extract bare absolute path candidates; callers enforce operation scope."""

    return [match.group(0).rstrip(_TRAILING_REFERENCE_PUNCTUATION)
            for match in _ABSOLUTE_PATH_TEXT_PATTERN.finditer(text)]

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
