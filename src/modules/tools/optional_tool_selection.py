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
CREDENTIAL_OPTIONAL_TOOL_NAMES = frozenset({
    "store_credential", "query_credentials", "checkout_credential", "exchange_oauth2_client_credentials",
    "set_task_auth_context", "mark_credential_status", "plan_access_control_comparisons",
    "plan_authenticated_coverage", "prepare_api_key_authentication", "prepare_login_form_authentication",
    "generate_password", "generate_registration_email", "generate_mfa_code", "begin_email_mfa_retrieval", "request_mfa_code",
    "retrieve_email_mfa_code", "stage_credential_rotation", "complete_credential_rotation",
    "fail_credential_rotation", "rotate_credential",
})
CREDENTIAL_PROVISIONING_BROWSER_TOOL_NAMES = (
    "browser_goto_url",
    "browser_observe_page",
    "browser_get_page_html",
    "browser_perform_action",
    "browser_get_cookies",
    "browser_evaluate_js",
)
CREDENTIAL_PROVISIONING_OPTIONAL_TOOL_NAMES = (
    "generate_password",
    "generate_registration_email",
    "store_credential",
    "query_credentials",
    "checkout_credential",
    "mark_credential_status",
)
VALIDATION_CREDENTIAL_OPTIONAL_TOOL_NAMES = (
    "checkout_credential", "set_task_auth_context", "mark_credential_status",
    "exchange_oauth2_client_credentials", "prepare_api_key_authentication",
    "prepare_login_form_authentication", "generate_mfa_code", "begin_email_mfa_retrieval",
    "request_mfa_code", "retrieve_email_mfa_code",
)


def workflow_optional_tool_names(task: Any) -> list[str]:
    """Return mandatory workflow tools from controller-owned workstream metadata."""

    context = getattr(task, "recovery_context", {}) or {}
    contract = context.get("phase_task_contract", {}) if isinstance(context, dict) else {}
    workstream = str(contract.get("workstream") or "") if isinstance(contract, dict) else ""
    if workstream == "client_side_api":
        return ["client_bundle_inventory"]
    return []


def credential_optional_tool_names(task: Any) -> list[str]:
    """Return credential tools required by durable task metadata, never task prose."""

    context = getattr(task, "recovery_context", {}) or {}
    contract = context.get("phase_task_contract", {}) if isinstance(context, dict) else {}
    conditional = context.get("conditional_phase", {}) if isinstance(context, dict) else {}
    workstream = str(contract.get("workstream") or "") if isinstance(contract, dict) else ""
    auth_context = getattr(task, "auth_context", {}) or {}
    if (
        str(getattr(task, "kind", "")) in {"finding_validation", "objective_validation"}
        and auth_context.get("mode") == "authenticated"
    ):
        return list(VALIDATION_CREDENTIAL_OPTIONAL_TOOL_NAMES)
    if str(getattr(task, "kind", "")) == "credential_rotation":
        return [
            "query_credentials", "checkout_credential", "stage_credential_rotation",
            "complete_credential_rotation", "fail_credential_rotation", "rotate_credential",
        ]
    if isinstance(conditional, dict) and conditional.get("kind") == "credential_provisioning":
        return [
            *CREDENTIAL_PROVISIONING_OPTIONAL_TOOL_NAMES,
            *CREDENTIAL_PROVISIONING_BROWSER_TOOL_NAMES,
        ]
    if workstream == "authorization_comparison":
        return [
            "plan_authenticated_coverage", "plan_access_control_comparisons", "checkout_credential",
            "set_task_auth_context", "mark_credential_status", "prepare_login_form_authentication",
        ]
    if workstream == "authenticated_credential_coverage" or auth_context.get("mode") == "authenticated":
        return [
            "plan_authenticated_coverage", "query_credentials", "checkout_credential", "set_task_auth_context",
            "mark_credential_status", "exchange_oauth2_client_credentials", "prepare_api_key_authentication",
            "prepare_login_form_authentication", "generate_mfa_code", "begin_email_mfa_retrieval",
            "request_mfa_code", "retrieve_email_mfa_code",
        ]
    return []


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
            or (not output_kinds and not evidence_kinds)
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
    return list(
        dict.fromkeys(
            [*tool_names, *workflow_optional_tool_names(task), *credential_optional_tool_names(task)]
        )
    )
