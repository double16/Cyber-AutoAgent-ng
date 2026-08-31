"""Resolve controller-required optional tools from structured task contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "system" / "optional_tool_selection.yaml"
_SUPPORTED_OUTPUT_KINDS = frozenset({"artifact", "inventory_manifest"})
_SUPPORTED_EVIDENCE_KINDS = frozenset(
    {
        "artifact",
        "inventory_manifest",
        "durable_evidence",
        "observation",
        "finding_candidate",
        "verified_finding",
        "memory",
    }
)


def load_optional_tool_selection_rules() -> list[dict[str, Any]]:
    """Load and validate the small controller-owned optional-tool selection catalog."""

    try:
        payload = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError("optional tool selection catalog is unavailable or invalid") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("optional tool selection catalog must declare version 1")
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise TypeError("optional tool selection catalog rules must be a list")

    validated = []
    seen_ids = set()
    for raw_rule in rules:
        if not isinstance(raw_rule, dict):
            raise TypeError("optional tool selection rules must be objects")
        rule_id = str(raw_rule.get("id") or "").strip()
        output_kinds = raw_rule.get("output_kinds", [])
        evidence_kinds = raw_rule.get("evidence_requirement_kinds", [])
        tool_names = raw_rule.get("tools")
        if (
            not rule_id
            or rule_id in seen_ids
            or not isinstance(output_kinds, list)
            or not isinstance(evidence_kinds, list)
            or not isinstance(tool_names, list)
            or not tool_names
            or not all(isinstance(name, str) and name.strip() for name in tool_names)
            or not all(kind in _SUPPORTED_OUTPUT_KINDS for kind in output_kinds)
            or not all(kind in _SUPPORTED_EVIDENCE_KINDS for kind in evidence_kinds)
            or not output_kinds and not evidence_kinds
        ):
            raise ValueError(f"optional tool selection rule {rule_id or '<unknown>'} is invalid")
        seen_ids.add(rule_id)
        validated.append(
            {
                "id": rule_id,
                "output_kinds": frozenset(output_kinds),
                "evidence_requirement_kinds": frozenset(evidence_kinds),
                "tools": tuple(dict.fromkeys(name.strip() for name in tool_names)),
            }
        )
    return validated


def required_optional_tool_names(task: Any) -> list[str]:
    """Return catalog-selected optional tools for persisted task metadata only."""

    procedure = getattr(getattr(task, "acceptance", None), "basis", None)
    procedure = getattr(procedure, "procedure", None)
    output_kind = str(getattr(procedure, "output_kind", "") or "")
    evidence_kinds = {
        requirement.kind
        for criterion in getattr(getattr(task, "acceptance", None), "criteria", ())
        for requirement in getattr(criterion, "evidence_requirements", ())
    }
    tool_names = []
    for rule in load_optional_tool_selection_rules():
        matches_output = output_kind in rule["output_kinds"]
        matches_evidence = bool(evidence_kinds & rule["evidence_requirement_kinds"])
        if matches_output or matches_evidence:
            tool_names.extend(rule["tools"])
    return list(dict.fromkeys(tool_names))
