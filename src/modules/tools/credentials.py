"""Typed credential-store tools backed by the application SQLite database.

Credential payloads are intentionally retained only in the application database
and are never included in list or status responses. When configured, database
encryption protects those payloads at rest; callers must never write returned
secret values to artifacts, logs, reports, or tool descriptions.
"""

from __future__ import annotations

import base64
import email
import hashlib
import hmac
import imaplib
import json
import os
import re
import secrets
import string
import struct
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from urllib.parse import urlsplit, urlunsplit

from strands import ToolContext, tool

from modules.tools.memory import (
    _get_database_store,
    _operation_id,
    active_credential_task,
    emit_memory_event,
)
from modules.utils.redaction import register_runtime_secret

_CREDENTIAL_TYPES = frozenset({"username_password", "email_login", "api_key", "oauth2_client"})
_STATUSES = frozenset({"unknown", "pending", "valid", "invalid", "expired", "revoked", "retired"})
_DURABLE_EVIDENCE_REF_PREFIXES = ("artifact:", "artifact_id:", "memory:", "finding:")
_OAUTH_CLIENT_AUTH_METHODS = frozenset({"client_secret_basic", "client_secret_post"})
_FORM_FIELD_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.\-\[\]]{0,127}")
_IMAP_INTERNALDATE_PATTERN = re.compile(r'INTERNALDATE "(?P<timestamp>[^"]+)"')
_OBJECTIVE_LOGIN_PATTERN = re.compile(
    r"(?is)\b(?:username|user)\s*[:=]\s*(?P<username>[^\s,;]+).*?\b(?:password|pass)\s*[:=]\s*(?P<password>[^\s,;]+)"
)
_OBJECTIVE_EMAIL_LOGIN_PATTERN = re.compile(
    r"(?is)\bemail\s*[:=]\s*(?P<email>[^\s,;]+).*?\b(?:password|pass)\s*[:=]\s*(?P<password>[^\s,;]+)"
)
_OBJECTIVE_API_KEY_PATTERN = re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?key)\s*[:=]\s*(?P<api_key>[^\s,;]+)")
_OBJECTIVE_OAUTH_PATTERN = re.compile(
    r"(?is)\b(?:oauth2?[_ -]?)?(?:client[_ -]?id|client)\s*[:=]\s*(?P<client_id>[^\s,;]+)"
    r".*?\b(?:oauth2?[_ -]?)?(?:client[_ -]?secret|secret)\s*[:=]\s*(?P<client_secret>[^\s,;]+)"
    r"(?:.*?\b(?:oauth2?[_ -]?)?token[_ -]?url\s*[:=]\s*(?P<token_url>[^\s,;]+))?"
)
_REGISTRATION_EMAIL_RESERVATIONS: dict[str, set[str]] = {}
_STORE_CREDENTIAL_INPUT_SCHEMA = {
    "json": {
        "type": "object",
        "properties": {
            "credential_type": {
                "type": "string",
                "enum": ["username_password", "email_login", "api_key", "oauth2_client"],
                "description": (
                    "Credential kind. A registered web account is username_password; email_login is only for an "
                    "IMAP mailbox credential."
                ),
            },
            "values": {
                "type": "object",
                "description": (
                    "Secret payload. username_password requires username and password and accepts optional email; "
                    "email_login requires email, password, and mailbox {host, port?, tls?, folder?}; api_key "
                    "requires api_key and name; oauth2_client requires client_id and client_secret."
                ),
            },
            "target": {"type": "string", "description": "Exact active target for non-mailbox credentials."},
            "role": {"type": "string", "description": "Role for non-mailbox credentials."},
            "operation_scope": {"type": "string", "description": "Optional current-operation storage scope."},
            "origin": {
                "type": "string",
                "enum": ["found", "registered"],
                "description": "Use registered only after authorized self-registration succeeds.",
            },
            "account_label": {"type": "string", "description": "Safe account label."},
            "tenant_label": {"type": "string", "description": "Safe tenant label."},
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required durable artifact, memory, or finding references.",
            },
        },
        "required": ["credential_type", "values"],
    }
}


def extract_objective_credentials(objective: str) -> tuple[str, list[dict[str, Any]]]:
    """Best-effort local extraction that removes recognized secrets before model use."""

    drafts: list[dict[str, Any]] = []

    def login_replacement(match: re.Match[str]) -> str:
        drafts.append(
            {
                "credential_type": "username_password",
                "role": "user",
                "values": {"username": match.group("username"), "password": match.group("password")},
            }
        )
        return "use the credential store for the supplied login"

    sanitized = _OBJECTIVE_LOGIN_PATTERN.sub(login_replacement, str(objective or ""))

    def email_login_replacement(match: re.Match[str]) -> str:
        drafts.append(
            {
                "credential_type": "username_password",
                "role": "user",
                "values": {
                    "username": match.group("email"),
                    "email": match.group("email"),
                    "password": match.group("password"),
                },
            }
        )
        return "use the credential store for the supplied email login"

    sanitized = _OBJECTIVE_EMAIL_LOGIN_PATTERN.sub(email_login_replacement, sanitized)

    def api_replacement(match: re.Match[str]) -> str:
        drafts.append(
            {
                "credential_type": "api_key",
                "role": "api_user",
                "values": {"api_key": match.group("api_key"), "placement": "header", "name": "Authorization"},
            }
        )
        return "use the credential store for the supplied API key"

    sanitized = _OBJECTIVE_API_KEY_PATTERN.sub(api_replacement, sanitized)

    def oauth_replacement(match: re.Match[str]) -> str:
        values = {"client_id": match.group("client_id"), "client_secret": match.group("client_secret")}
        if match.group("token_url"):
            values["token_url"] = match.group("token_url")
        drafts.append({"credential_type": "oauth2_client", "role": "api_user", "values": values})
        return "use the credential store for the supplied OAuth client"

    sanitized = _OBJECTIVE_OAUTH_PATTERN.sub(oauth_replacement, sanitized)
    return sanitized, drafts


def extract_config_credentials(raw_value: str | None) -> list[dict[str, Any]]:
    """Validate credential drafts supplied by the React configuration or environment.

    The accepted value is a JSON array, or an object with a `credentials` array. Each item uses the same fields as
    `store_user_credential`: credential_type, optional target, optional role, values, optional operation_scope,
    account_label, and tenant_label. Validation errors intentionally identify only field names, never values.
    """

    value = str(raw_value or "").strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("assessment credential configuration must be valid JSON") from error
    entries = parsed.get("credentials") if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list):
        raise ValueError("assessment credential configuration must contain a credentials array")
    drafts: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"assessment credential {index} must be an object")
        credential_type = str(entry.get("credential_type") or "").strip().lower()
        values = entry.get("values")
        validate_credential_payload(credential_type, values)
        target = str(entry.get("target") or "").strip() or None
        role = str(entry.get("role") or "").strip() or None
        if credential_type != "email_login" and role is None:
            raise ValueError(f"assessment credential {index} requires role")
        drafts.append(
            {
                "credential_type": credential_type,
                "target": target,
                "role": role,
                "values": values,
                "operation_scope": str(entry.get("operation_scope") or "").strip() or None,
                "account_label": str(entry.get("account_label") or "").strip() or None,
                "tenant_label": str(entry.get("tenant_label") or "").strip() or None,
            }
        )
    return drafts


def canonicalize_credential_target(target: str) -> str:
    """Normalize syntax while preserving the resolved target's meaning."""

    value = str(target or "").strip()
    if not value:
        raise ValueError("credential target is required")
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value.rstrip("/") or value
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    if not hostname:
        raise ValueError("credential target URL requires a host")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("credential target URL has an invalid port") from error
    default_port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
    netloc = hostname if port in {None, default_port} else f"{hostname}:{port}"
    if parsed.username or parsed.password:
        raise ValueError("credential target must not embed userinfo")
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "", parsed.query, ""))


def _require_string(values: dict[str, Any], key: str) -> str:
    value = str(values.get(key) or "").strip()
    if not value:
        raise ValueError(f"credential values.{key} is required")
    return value


def _validate_mfa(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("credential values.mfa must be an object")
    method = str(value.get("type") or "").strip().lower()
    if method == "totp":
        secret = _require_string(value, "secret")
        digits = int(value.get("digits", 6))
        period = int(value.get("period", 30))
        algorithm = str(value.get("algorithm", "SHA1")).upper()
        if digits < 6 or digits > 10 or period <= 0 or algorithm not in {"SHA1", "SHA256", "SHA512"}:
            raise ValueError("invalid TOTP configuration")
        return {"type": method, "secret": secret, "digits": digits, "period": period, "algorithm": algorithm}
    if method == "email":
        recipient = _require_string(value, "recipient")
        mailbox_credential_id = _require_string(value, "mailbox_credential_id")
        return {"type": method, "recipient": recipient, "mailbox_credential_id": mailbox_credential_id}
    raise ValueError("credential MFA type must be totp or email")


def validate_credential_payload(credential_type: str, values: dict[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize one credential payload at the trust boundary."""

    kind = str(credential_type or "").strip().lower()
    if kind not in _CREDENTIAL_TYPES:
        raise ValueError("credential_type must be username_password, email_login, api_key, or oauth2_client")
    if not isinstance(values, dict):
        raise ValueError("credential values must be an object")
    if kind == "username_password":
        payload = {"username": _require_string(values, "username"), "password": _require_string(values, "password")}
        if values.get("email"):
            payload["email"] = _require_string(values, "email")
        mfa = _validate_mfa(values.get("mfa"))
        if mfa:
            payload["mfa"] = mfa
        return payload
    if kind == "email_login":
        payload = {"email": _require_string(values, "email"), "password": _require_string(values, "password")}
        mailbox = values.get("mailbox")
        if not isinstance(mailbox, dict):
            raise ValueError("email_login values.mailbox is required")
        host = _require_string(mailbox, "host")
        port = int(mailbox.get("port", 993))
        if port <= 0 or port > 65535 or mailbox.get("tls", True) is not True:
            raise ValueError("email mailbox requires a valid TLS IMAP port")
        payload["mailbox"] = {"host": host, "port": port, "tls": True, "folder": str(mailbox.get("folder") or "INBOX")}
        mfa = _validate_mfa(values.get("mfa"))
        if mfa:
            payload["mfa"] = mfa
        return payload
    if kind == "api_key":
        placement = str(values.get("placement") or "header").strip().lower()
        if placement not in {"header", "query"}:
            raise ValueError("API key placement must be header or query")
        return {
            "api_key": _require_string(values, "api_key"),
            "placement": placement,
            "name": _require_string(values, "name"),
            "prefix": str(values.get("prefix") or ""),
        }
    client_auth_method = str(values.get("client_auth_method") or "client_secret_basic").strip()
    if client_auth_method not in _OAUTH_CLIENT_AUTH_METHODS:
        raise ValueError("OAuth client_auth_method must be client_secret_basic or client_secret_post")
    payload = {
        "client_id": _require_string(values, "client_id"),
        "client_secret": _require_string(values, "client_secret"),
        "scopes": [str(scope).strip() for scope in values.get("scopes", []) if str(scope).strip()],
        "audience": str(values.get("audience") or "").strip(),
        "client_auth_method": client_auth_method,
    }
    if values.get("token_url"):
        payload["token_url"] = canonicalize_credential_target(_require_string(values, "token_url"))
    return payload


def _normalize_credential_evidence_refs(evidence_refs: list[str] | None) -> list[str]:
    """Require safe durable references for credentials created by an operation."""

    if evidence_refs is None:
        return []
    if not isinstance(evidence_refs, list):
        raise ValueError("credential evidence_refs must be a list of durable references")
    normalized = []
    for reference in evidence_refs:
        value = str(reference or "").strip()
        if not value.startswith(_DURABLE_EVIDENCE_REF_PREFIXES):
            raise ValueError("credential evidence_refs must use durable references")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _validate_credential_target_scope(store: Any, operation_id: str, target: str) -> str:
    """Require target credentials to use an operation's exact resolved target value."""

    normalized_target = canonicalize_credential_target(target)
    plan = store.get_plan(operation_id)
    if plan is None:
        return normalized_target
    resolved_targets = {
        canonicalize_credential_target(str(operation_target.value))
        for operation_target in plan.targets
        if str(operation_target.value or "").strip()
    }
    if normalized_target not in resolved_targets:
        raise ValueError("credential target must be an exact resolved operation target")
    return normalized_target


def resolve_credential_target_for_operation(
    credential_type: str,
    target: str | None,
    operation_targets: list[Any],
) -> str | None:
    """Validate a configuration credential against the preflight-resolved operation targets."""

    kind = str(credential_type or "").strip().lower()
    supplied_target = str(target or "").strip()
    if kind == "email_login":
        if supplied_target:
            raise ValueError("email_login credentials must not specify a target")
        return None
    if not supplied_target:
        raise ValueError("credential target is required")
    normalized_target = canonicalize_credential_target(supplied_target)
    resolved_targets = {
        canonicalize_credential_target(str(operation_target.value))
        for operation_target in operation_targets
        if str(getattr(operation_target, "value", "") or "").strip()
    }
    if normalized_target not in resolved_targets:
        raise ValueError("credential target must be an exact resolved operation target")
    return normalized_target


def _resolve_credential_operation_scope(operation_id: str, operation_scope: str | None) -> str | None:
    """Normalize an optional scope to the operation currently importing the credential."""

    scope = str(operation_scope or "").strip()
    if not scope:
        return None
    if scope.casefold() in {"current", "$current"}:
        return operation_id
    if scope != operation_id:
        raise ValueError("credential operation_scope must be current or match the importing operation")
    return scope


def store_user_credential(
    *,
    operation_id: str,
    credential_type: str,
    target: str | None,
    role: str | None,
    values: dict[str, Any],
    operation_scope: str | None = None,
    account_label: str | None = None,
    tenant_label: str | None = None,
    origin: str = "provided",
    management_policy: str = "user",
    creation_evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Store an explicitly user-provided credential for UI and headless import callers."""

    kind = str(credential_type or "").strip().lower()
    if kind != "email_login" and not str(target or "").strip():
        raise ValueError("target is required for target credentials")
    if kind != "email_login" and not str(role or "").strip():
        raise ValueError("role is required for target credentials")
    payload = validate_credential_payload(kind, values)
    if account_label:
        payload["account_label"] = str(account_label).strip()
    if tenant_label:
        payload["tenant_label"] = str(tenant_label).strip()
    store = _get_database_store()
    normalized_target = _validate_credential_target_scope(store, operation_id, target) if target else None
    normalized_operation_scope = _resolve_credential_operation_scope(operation_id, operation_scope)
    return store.store_credential(
        operation_id,
        {
            "credential_type": kind,
            "target": normalized_target,
            "role": role,
            "operation_id": normalized_operation_scope,
            "payload": payload,
            "origin": origin,
            "management_policy": management_policy,
            "status": "unknown",
            "initial_status_evidence_refs": creation_evidence_refs or [],
        },
    )


@tool(name="store_credential", inputSchema=_STORE_CREDENTIAL_INPUT_SCHEMA)
def store_credential(
    credential_type: str,
    values: dict[str, Any],
    target: str | None = None,
    role: str | None = None,
    operation_scope: str | None = None,
    origin: str = "found",
    account_label: str | None = None,
    tenant_label: str | None = None,
    evidence_refs: list[str] | None = None,
) -> str:
    """Store a credential found in a target or created by authorized self-registration.

    Use `registered` for a successful self-registration and `found` for a credential recovered from target-owned
    evidence. Give `evidence_refs` durable references for the discovery or successful registration. Target may be
    the active task's target ID or its resolved target value. Never store user-provided credentials with this tool.
    Passwords and other secret values are not echoed. For a registered web account, use
    `credential_type="username_password"` with `values.username` set to the registered login/email and
    `values.password`; `values.email` is optional. `email_login` is an IMAP mailbox credential, not an email-based
    website login, and requires `values.email`, `values.password`, and a `values.mailbox` object with an IMAP host.
    """

    normalized_origin = str(origin or "").strip().lower()
    if normalized_origin not in {"found", "registered"}:
        raise ValueError("agent credential origin must be found or registered")
    normalized_evidence_refs = _normalize_credential_evidence_refs(evidence_refs)
    if not normalized_evidence_refs:
        raise ValueError("operation-created credentials require at least one durable evidence reference")
    store = _get_database_store()
    operation_id = _operation_id()
    _active_task_target_values(store, operation_id)
    normalized_type = str(credential_type or "").strip().lower()
    normalized_target = (
        None
        if normalized_type == "email_login"
        else _resolve_active_task_target(store, operation_id, str(target or ""))
    )
    record = store_user_credential(
        operation_id=operation_id,
        credential_type=credential_type,
        target=normalized_target,
        role=role,
        values=values,
        operation_scope=operation_scope,
        account_label=account_label,
        tenant_label=tenant_label,
        origin=normalized_origin,
        management_policy="operation",
        creation_evidence_refs=normalized_evidence_refs,
    )
    return json.dumps({"stored": True, "credential": record})


@tool(name="query_credentials")
def query_credentials(
    target: str | None = None,
    role: str | None = None,
    credential_type: str | None = None,
) -> str:
    """List eligible credential metadata without returning secret values.

    When supplied, target may be the active task's target ID or its resolved target value.
    """

    normalized_type = str(credential_type or "").strip().lower() or None
    if normalized_type is not None and normalized_type not in _CREDENTIAL_TYPES:
        raise ValueError("unknown credential_type")
    store = _get_database_store()
    operation_id = _operation_id()
    _, target_values = _active_task_target_values(store, operation_id)
    requested_target = _resolve_active_task_target(store, operation_id, target) if target else None
    records = [
        record
        for target_value in ([requested_target] if requested_target else sorted(target_values))
        for record in store.list_credentials(
            operation_id,
            target=target_value,
            role=str(role).strip() if role else None,
            credential_type=normalized_type,
        )
    ]
    return json.dumps({"credentials": records})


@tool(name="plan_access_control_comparisons")
def plan_access_control_comparisons(target: str) -> str:
    """List safe, credential-ID-only account, role, and tenant comparison pairs for authorized IDOR testing.

    Target may be the active task's target ID or its resolved target value. Use the returned IDs to check out two
    distinct credentials and test only the assigned target. No pair is invented: an empty comparison list is a
    coverage gap that must be reported rather than bypassed.
    """

    store = _get_database_store()
    operation_id = _operation_id()
    normalized_target = _resolve_active_task_target(store, operation_id, target)
    records = store.list_credentials(operation_id, target=normalized_target)
    comparisons = _credential_comparison_pairs(records)
    return json.dumps(
        {
            "target": normalized_target,
            "comparisons": comparisons,
            "coverage_gap": None if comparisons else "No distinct eligible account, role, or tenant credential pair.",
        },
        sort_keys=True,
    )


def _credential_comparison_pairs(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return stable, credential-ID-only access-control comparisons from safe metadata."""

    comparisons: list[dict[str, str]] = []
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            left_id = str(left["credential_id"])
            right_id = str(right["credential_id"])
            left_account = str(left.get("account_label") or "")
            right_account = str(right.get("account_label") or "")
            left_tenant = str(left.get("tenant_label") or "")
            right_tenant = str(right.get("tenant_label") or "")
            if left_account and right_account and left_account != right_account:
                comparisons.append({"kind": "account", "left_credential_id": left_id, "right_credential_id": right_id})
            if str(left.get("role") or "") != str(right.get("role") or ""):
                comparisons.append({"kind": "role", "left_credential_id": left_id, "right_credential_id": right_id})
            if left_tenant and right_tenant and left_tenant != right_tenant:
                comparisons.append({"kind": "tenant", "left_credential_id": left_id, "right_credential_id": right_id})
    unique_comparisons = {
        (item["kind"], item["left_credential_id"], item["right_credential_id"]): item for item in comparisons
    }
    return [unique_comparisons[key] for key in sorted(unique_comparisons)]


@tool(name="plan_authenticated_coverage")
def plan_authenticated_coverage(target: str) -> str:
    """Return deterministic safe contexts for unauthenticated, authenticated, and IDOR coverage.

    Call this before authenticated testing. It never invents an account: empty credential contexts or comparison pairs
    are explicit coverage gaps that must be reported. Check out only the returned credential IDs for the assigned
    target, establish the unauthenticated baseline first, and use one authenticated context at a time. Target may
    be the active task's target ID or its resolved target value.
    """

    store = _get_database_store()
    operation_id = _operation_id()
    normalized_target = _resolve_active_task_target(store, operation_id, target)
    records = store.list_credentials(operation_id, target=normalized_target)
    contexts = [
        {
            "credential_id": str(record["credential_id"]),
            "credential_type": str(record["credential_type"]),
            "role": str(record.get("role") or ""),
            "account_label": str(record.get("account_label") or ""),
            "tenant_label": str(record.get("tenant_label") or ""),
        }
        for record in records
    ]
    comparisons = _credential_comparison_pairs(records)
    return json.dumps(
        {
            "target": normalized_target,
            "unauthenticated_required": True,
            "authenticated_contexts": contexts,
            "authenticated_coverage_gap": None if contexts else "No eligible credentials for authenticated testing.",
            "comparisons": comparisons,
            "comparison_coverage_gap": (
                None if comparisons else "No distinct eligible account, role, or tenant credential pair."
            ),
        },
        sort_keys=True,
    )


def _task_resolved_targets(store: Any, operation_id: str, task: Any) -> list[str]:
    """Return the resolved target values authorized for one durable task."""

    plan = store.get_plan(operation_id)
    if plan is None:
        return []
    selected_target_ids = set(task.target_ids) if task.target_scope == "subset" else None
    values = [
        canonicalize_credential_target(str(target.value))
        for target in plan.targets
        if selected_target_ids is None or target.target_id in selected_target_ids
    ]
    return list(dict.fromkeys(values))


def _active_task_target_values(store: Any, operation_id: str) -> tuple[Any, set[str]]:
    """Return the active task and the exact resolved targets it is allowed to access."""

    active_task = active_credential_task(store, operation_id)
    if active_task is None:
        raise ValueError("an active task is required for credential access")
    target_values = set(_task_resolved_targets(store, operation_id, active_task))
    if not target_values:
        raise ValueError("active task does not have a resolved target scope")
    return active_task, target_values


def _frozen_validation_credential_ids(task: Any) -> set[str] | None:
    """Return validation-bound credential IDs, preserving legacy tasks without a binding."""

    if str(getattr(task, "kind", "")) not in {"finding_validation", "objective_validation"}:
        return None
    recovery_context = getattr(task, "recovery_context", {}) or {}
    binding = recovery_context.get("validation_auth_context") if isinstance(recovery_context, dict) else None
    if not isinstance(binding, dict):
        return None
    if str(binding.get("mode") or "").strip().lower() != "authenticated":
        return set()
    return {
        str(credential_id).strip()
        for credential_id in binding.get("credential_ids", [])
        if str(credential_id).strip()
    }


def _resolve_active_task_target(store: Any, operation_id: str, target: str) -> str:
    """Resolve an active task target ID or validate a canonical target value."""

    active_task, target_values = _active_task_target_values(store, operation_id)
    supplied_target = str(target or "").strip()
    if not supplied_target:
        raise ValueError("credential target is required")

    plan = store.get_plan(operation_id)
    selected_target_ids = set(active_task.target_ids) if active_task.target_scope == "subset" else None
    if plan is not None:
        for operation_target in plan.targets:
            if selected_target_ids is not None and operation_target.target_id not in selected_target_ids:
                continue
            if operation_target.target_id == supplied_target:
                return canonicalize_credential_target(str(operation_target.value))

    normalized_target = canonicalize_credential_target(supplied_target)
    if normalized_target not in target_values:
        raise ValueError("credential target is outside the active task target scope")
    return normalized_target


@tool(name="set_task_auth_context")
def set_task_auth_context(credential_ids: list[str]) -> str:
    """Bind checked-out, target-scoped credentials to the active task's authenticated context.

    Call this after checkout and before an authenticated request or authentication finding. The controller derives
    roles and account/tenant labels from stored credential metadata; callers cannot supply or spoof that provenance.
    """

    normalized_ids = list(dict.fromkeys(str(item).strip() for item in credential_ids if str(item).strip()))
    if not normalized_ids:
        raise ValueError("credential_ids requires at least one credential ID")
    store = _get_database_store()
    operation_id = _operation_id()
    active_task, target_values = _active_task_target_values(store, operation_id)
    frozen_ids = _frozen_validation_credential_ids(active_task)
    if frozen_ids is not None and set(normalized_ids) != frozen_ids:
        raise ValueError("validation authentication context must use exactly the frozen credential IDs")
    selected_ids = store.credential_ids_selected_by_task(operation_id, active_task.task_uid)
    missing_selection = sorted(set(normalized_ids) - selected_ids)
    if missing_selection:
        raise ValueError("authentication context credentials must be checked out by the active task")
    eligible_ids = {
        str(record["credential_id"])
        for target_value in target_values
        for record in store.list_credentials(operation_id, target=target_value)
    }
    if not set(normalized_ids).issubset(eligible_ids):
        raise ValueError("authentication context credential is outside the active task target scope")
    records = [store.get_credential(credential_id) for credential_id in normalized_ids]
    if any(record is None or record["status"] not in {"unknown", "valid"} for record in records):
        raise ValueError("authentication context credential is unavailable")
    role_values = sorted({
        str(record.get("role") or "") for record in records if record and record.get("role")
    })
    account_values = sorted({
        str(record.get("account_label") or "") for record in records if record and record.get("account_label")
    })
    tenant_values = sorted({
        str(record.get("tenant_label") or "") for record in records if record and record.get("tenant_label")
    })
    task = store.patch_task(
        operation_id,
        active_task.task_uid,
        auth_context={
            "mode": "authenticated",
            "credential_ids": normalized_ids,
            "roles": role_values,
            "account_labels": account_values,
            "tenant_labels": tenant_values,
        },
    )
    return json.dumps({"task_uid": task.task_uid, "auth_context": task.auth_context}, sort_keys=True)


@tool(name="checkout_credential")
def checkout_credential(credential_id: str, purpose: str) -> str:
    """Retrieve one eligible credential for the active task and record selection for reporting.

    Use the returned values only with in-scope authentication tools. Do not copy them to artifacts, findings, or prose.
    """

    if not str(purpose or "").strip():
        raise ValueError("credential checkout purpose is required")
    store = _get_database_store()
    record = store.get_credential(str(credential_id), include_payload=True)
    if record is None or record["status"] not in {"unknown", "valid"}:
        raise ValueError("credential is unavailable")
    scoped_operation = record.get("operation_id")
    if scoped_operation and scoped_operation != _operation_id():
        raise ValueError("credential is scoped to another operation")
    active_task, target_values = _active_task_target_values(store, _operation_id())
    frozen_ids = _frozen_validation_credential_ids(active_task)
    if frozen_ids is not None and str(credential_id) not in frozen_ids:
        raise ValueError("credential is not authorized by the frozen validation authentication context")
    eligible_ids = {
        str(candidate["credential_id"])
        for target_value in target_values
        for candidate in store.list_credentials(_operation_id(), target=target_value)
    }
    if str(credential_id) not in eligible_ids:
        raise ValueError("credential is outside the active task target scope")
    store.record_credential_usage(
        _operation_id(),
        str(credential_id),
        task_uid=active_task.task_uid,
        outcome="selected",
    )
    _register_credential_payload_secrets(record)
    return json.dumps({"credential_id": record["credential_id"], "credential_type": record["credential_type"], "values": record["payload"]})


@tool(name="prepare_api_key_authentication")
def prepare_api_key_authentication(credential_id: str) -> str:
    """Build the configured header or query material for a checked-out API key.

    Use exactly one returned mapping in the next in-scope target request. The API key remains task-local and must not
    be copied to an artifact, finding, report, or credential record.
    """

    store = _get_database_store()
    operation_id = _operation_id()
    _active_task, record = _active_checked_out_credential(store, operation_id, str(credential_id))
    if record["credential_type"] != "api_key":
        raise ValueError("credential must be an api_key credential")
    payload = record["payload"]
    value = f"{payload['prefix']}{payload['api_key']}"
    if payload["placement"] == "header":
        return json.dumps(
            {"credential_id": record["credential_id"], "headers": {payload["name"]: value}, "query_params": {}}
        )
    return json.dumps(
        {"credential_id": record["credential_id"], "headers": {}, "query_params": {payload["name"]: value}}
    )


def _validated_form_field_name(value: str | None, label: str, *, required: bool) -> str:
    """Normalize one detected login form field name without accepting selector syntax."""

    normalized = str(value or "").strip()
    if not normalized and required:
        raise ValueError(f"{label} is required")
    if normalized and not _FORM_FIELD_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} is invalid")
    return normalized


@tool(name="prepare_login_form_authentication")
def prepare_login_form_authentication(
    credential_id: str,
    username_field: str = "username",
    password_field: str = "password",
    email_field: str | None = None,
) -> str:
    """Build task-local fields for a checked-out username/password login form.

    Map the form and its CSRF/session requirements first with the authentication-chain and browser tools. Supply only
    detected field names; this tool does not submit the form or persist the returned values. Do not write the fields
    to artifacts, findings, reports, or credential records.
    """

    normalized_username_field = _validated_form_field_name(username_field, "username_field", required=True)
    normalized_password_field = _validated_form_field_name(password_field, "password_field", required=True)
    normalized_email_field = _validated_form_field_name(email_field, "email_field", required=False)
    if normalized_username_field == normalized_password_field:
        raise ValueError("username_field and password_field must differ")
    if normalized_email_field in {normalized_username_field, normalized_password_field}:
        raise ValueError("email_field must differ from username_field and password_field")
    store = _get_database_store()
    operation_id = _operation_id()
    _active_task, record = _active_checked_out_credential(store, operation_id, str(credential_id))
    if record["credential_type"] != "username_password":
        raise ValueError("credential must be a username_password credential")
    payload = record["payload"]
    fields = {
        normalized_username_field: str(payload["username"]),
        normalized_password_field: str(payload["password"]),
    }
    if normalized_email_field:
        if not payload.get("email"):
            raise ValueError("credential does not include an email value")
        fields[normalized_email_field] = str(payload["email"])
    return json.dumps({"credential_id": record["credential_id"], "form_fields": fields})


def build_checked_out_idor_login_contexts(
    credential_ids: list[str],
    target_url: str,
    login_url: str,
    username_field: str = "username",
    password_field: str = "password",
    email_field: str | None = None,
    extra_form_fields: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build two task-local login form contexts for an IDOR comparison."""

    normalized_ids = list(dict.fromkeys(str(item).strip() for item in credential_ids if str(item).strip()))
    if len(normalized_ids) != 2:
        raise ValueError("IDOR credential-backed login requires exactly two distinct credential IDs")
    normalized_username_field = _validated_form_field_name(username_field, "username_field", required=True)
    normalized_password_field = _validated_form_field_name(password_field, "password_field", required=True)
    normalized_email_field = _validated_form_field_name(email_field, "email_field", required=False)
    credential_field_names = {normalized_username_field, normalized_password_field}
    if normalized_username_field == normalized_password_field:
        raise ValueError("username_field and password_field must differ")
    if normalized_email_field:
        if normalized_email_field in credential_field_names:
            raise ValueError("email_field must differ from username_field and password_field")
        credential_field_names.add(normalized_email_field)

    normalized_extra_fields: dict[str, str] = {}
    for key, value in (extra_form_fields or {}).items():
        normalized_key = _validated_form_field_name(key, "extra_form_fields key", required=True)
        if normalized_key in credential_field_names:
            raise ValueError("extra_form_fields must not override credential fields")
        normalized_extra_fields[normalized_key] = str(value)

    store = _get_database_store()
    operation_id = _operation_id()
    active_task, _target_values = _active_task_target_values(store, operation_id)
    auth_context = active_task.auth_context if isinstance(active_task.auth_context, dict) else {}
    if auth_context.get("mode") != "authenticated":
        raise ValueError("IDOR credential-backed login requires an authenticated task context")
    context_ids = {str(item) for item in auth_context.get("credential_ids", [])}
    if not set(normalized_ids).issubset(context_ids):
        raise ValueError("IDOR credential IDs must be bound to the active task authentication context")

    target_origin = _credential_target_origin(target_url, "target_url")
    login_origin = _credential_target_origin(login_url, "login_url")
    if login_origin != target_origin:
        raise ValueError("login_url must share the target_url origin for credential-backed IDOR login")

    contexts: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for credential_id in normalized_ids:
        _selected_task, record = _active_checked_out_credential(store, operation_id, credential_id)
        if record["credential_type"] != "username_password":
            raise ValueError("IDOR credential-backed login requires username_password credentials")
        if _credential_target_origin(str(record["target"]), "credential target") != target_origin:
            raise ValueError("IDOR credential target must share the requested target origin")
        records.append(record)

    eligible_records = store.list_credentials(operation_id, target=str(records[0]["target"]))
    comparison_pairs = _credential_comparison_pairs(eligible_records)
    requested_pair = {normalized_ids[0], normalized_ids[1]}
    if not any(
        {pair["left_credential_id"], pair["right_credential_id"]} == requested_pair for pair in comparison_pairs
    ):
        raise ValueError("IDOR credentials must match a planned account, role, or tenant comparison pair")

    for credential_id, record in zip(normalized_ids, records, strict=True):
        payload = record["payload"]
        fields = dict(normalized_extra_fields)
        fields[normalized_username_field] = str(payload["username"])
        fields[normalized_password_field] = str(payload["password"])
        if normalized_email_field:
            if not payload.get("email"):
                raise ValueError("credential does not include an email value")
            fields[normalized_email_field] = str(payload["email"])
        contexts.append({"credential_id": credential_id, "form_fields": fields})
    return contexts


def record_checked_out_credential_usage(credential_ids: list[str], outcome: str) -> None:
    """Record IDOR specialist use of selected credentials without exposing payloads."""

    normalized_outcome = str(outcome or "").strip()
    if normalized_outcome not in {"succeeded", "failed"}:
        raise ValueError("credential usage outcome must be succeeded or failed")
    store = _get_database_store()
    operation_id = _operation_id()
    active_task, _target_values = _active_task_target_values(store, operation_id)
    for credential_id in list(dict.fromkeys(str(item).strip() for item in credential_ids if str(item).strip())):
        _selected_task, record = _active_checked_out_credential(store, operation_id, credential_id)
        store.record_credential_usage(
            operation_id,
            str(record["credential_id"]),
            task_uid=active_task.task_uid,
            authentication_mode="authenticated",
            outcome=normalized_outcome,
        )


def _credential_target_origin(target: str, label: str = "OAuth token URL") -> tuple[str, str, int]:
    """Return one canonical URL origin for secret-safe credential exchanges."""

    parsed = urlparse.urlsplit(canonicalize_credential_target(target))
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        raise ValueError(f"{label} must have a target origin")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as error:
        raise ValueError(f"{label} has an invalid port") from error
    return scheme, host, port


@tool(name="exchange_oauth2_client_credentials")
def exchange_oauth2_client_credentials(credential_id: str, timeout_seconds: int = 15) -> str:
    """Exchange a checked-out, same-origin OAuth2 client credential for a short-lived access token.

    The credential must configure `token_url` on the resolved target's origin. The returned token is transient: it is
    not stored in SQLite, artifacts, findings, or reports. Bind the checked-out credential to the task authentication
    context before using the token for authenticated target requests.
    """

    if timeout_seconds < 1 or timeout_seconds > 60:
        raise ValueError("OAuth token timeout must be between 1 and 60 seconds")
    store = _get_database_store()
    operation_id = _operation_id()
    active_task, record = _active_checked_out_credential(store, operation_id, str(credential_id))
    if record["credential_type"] != "oauth2_client":
        raise ValueError("credential must be an oauth2_client credential")
    payload = record["payload"]
    token_url = str(payload.get("token_url") or "").strip()
    if not token_url:
        raise ValueError("OAuth credential requires token_url")
    if _credential_target_origin(token_url) != _credential_target_origin(str(record["target"])):
        raise ValueError("OAuth token URL must use the checked-out credential target origin")

    form_values = {"grant_type": "client_credentials"}
    if payload.get("scopes"):
        form_values["scope"] = " ".join(str(scope) for scope in payload["scopes"])
    if payload.get("audience"):
        form_values["audience"] = str(payload["audience"])
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    if payload["client_auth_method"] == "client_secret_basic":
        basic_value = base64.b64encode(
            f"{payload['client_id']}:{payload['client_secret']}".encode()
        ).decode("ascii")
        headers["Authorization"] = f"Basic {basic_value}"
    else:
        form_values["client_id"] = str(payload["client_id"])
        form_values["client_secret"] = str(payload["client_secret"])
    request = urlrequest.Request(
        token_url,
        data=urlparse.urlencode(form_values).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urlerror.HTTPError, ValueError, json.JSONDecodeError) as error:
        store.record_credential_usage(
            operation_id,
            record["credential_id"],
            task_uid=active_task.task_uid,
            outcome="failed",
        )
        raise ValueError("OAuth client-credentials exchange failed") from error
    access_token = response_payload.get("access_token") if isinstance(response_payload, dict) else None
    if not isinstance(access_token, str) or not access_token.strip():
        store.record_credential_usage(
            operation_id,
            record["credential_id"],
            task_uid=active_task.task_uid,
            outcome="failed",
        )
        raise ValueError("OAuth token response did not include an access_token")
    expires_in = response_payload.get("expires_in") if isinstance(response_payload, dict) else None
    safe_expires_in = expires_in if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) else None
    store.record_credential_usage(
        operation_id,
        record["credential_id"],
        task_uid=active_task.task_uid,
        outcome="succeeded",
    )
    return json.dumps(
        {
            "credential_id": record["credential_id"],
            "access_token": access_token,
            "token_type": str(response_payload.get("token_type") or "Bearer"),
            "expires_in": safe_expires_in,
        }
    )


@tool(name="mark_credential_status")
def mark_credential_status(
    credential_id: str,
    status: str,
    reason: str,
    evidence_refs: list[str] | None = None,
) -> str:
    """Record evidence-linked credential validity. Do not mark transient failures invalid."""

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _STATUSES:
        raise ValueError("unknown credential status")
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("credential status reason is required")
    normalized_evidence_refs = _normalize_credential_evidence_refs(evidence_refs)
    if not normalized_evidence_refs:
        raise ValueError("credential status requires at least one durable evidence reference")
    store = _get_database_store()
    operation_id = _operation_id()
    _active_checked_out_credential(store, operation_id, str(credential_id))
    record = store.record_credential_status(
        operation_id,
        str(credential_id),
        normalized_status,
        "operation",
        normalized_reason,
        normalized_evidence_refs,
    )
    return json.dumps({"credential": record})


@tool(name="rotate_credential")
def rotate_credential(
    credential_id: str,
    values: dict[str, Any],
    reason: str,
    evidence_refs: list[str] | None = None,
) -> str:
    """Replace an operation-managed credential while retaining the retired credential's audit history.

    This tool is limited to credentials created by an operation. Cite durable evidence for the successful rotation.
    User-provided credentials must be changed through the React configuration or environment import path. The old
    record is retained with status `retired`.
    """

    if os.getenv("CYBER_CREDENTIAL_ROTATION_REQUEST", "").strip():
        raise ValueError("maintenance rotations must stage and complete the queued request")
    if not str(reason or "").strip():
        raise ValueError("credential rotation reason is required")
    normalized_evidence_refs = _normalize_credential_evidence_refs(evidence_refs)
    if not normalized_evidence_refs:
        raise ValueError("credential rotation requires at least one durable evidence reference")
    store = _get_database_store()
    operation_id = _operation_id()
    _, previous = _active_checked_out_credential(store, operation_id, str(credential_id))
    if previous is None:
        raise ValueError("credential is unavailable")
    if previous["management_policy"] != "operation":
        raise ValueError("user-provided credentials can only be updated by the user")
    if previous["status"] in {"invalid", "revoked", "retired"}:
        raise ValueError("credential is unavailable for rotation")
    payload = validate_credential_payload(str(previous["credential_type"]), values)
    for label in ("account_label", "tenant_label"):
        if previous["payload"].get(label):
            payload[label] = previous["payload"][label]
    replacement = store.store_credential(
        operation_id,
        {
            "credential_type": previous["credential_type"],
            "target": previous["target"],
            "role": previous["role"],
            "operation_id": previous["operation_id"],
            "payload": payload,
            "origin": "registered",
            "management_policy": "operation",
            "status": "unknown",
            "supersedes_credential_id": previous["credential_id"],
            "initial_status_evidence_refs": normalized_evidence_refs,
        },
    )
    store.record_credential_status(
        operation_id,
        previous["credential_id"],
        "retired",
        "operation",
        str(reason).strip(),
        normalized_evidence_refs,
    )
    return json.dumps({"retired_credential_id": previous["credential_id"], "credential": replacement})


@tool(name="generate_password")
def generate_password(length: int = 20) -> str:
    """Generate a cryptographically secure password for an authorized self-registration flow."""

    if length < 12 or length > 128:
        raise ValueError("password length must be between 12 and 128")
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    required = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase), secrets.choice(string.digits), secrets.choice("!@#$%^&*-_")]
    remaining = [secrets.choice(alphabet) for _ in range(length - len(required))]
    characters = required + remaining
    secrets.SystemRandom().shuffle(characters)
    return register_runtime_secret("".join(characters))


@tool(name="generate_registration_email")
def generate_registration_email() -> str:
    """Generate a collision-resistant test email for an authorized self-registration flow."""

    operation_id = _operation_id()
    store = _get_database_store()
    existing = set()
    for record in store.list_credentials(
        operation_id,
        statuses=_STATUSES,
        include_payload=True,
    ):
        payload = record.get("payload") or {}
        if isinstance(payload, dict):
            existing.update(
                str(payload.get(field) or "").strip().lower()
                for field in ("email", "username")
            )
    reservations = _REGISTRATION_EMAIL_RESERVATIONS.setdefault(operation_id, set())
    for _ in range(100):
        candidate = f"testuser{secrets.randbelow(90_000_000) + 10_000_000}@example.com"
        if candidate not in existing and candidate not in reservations:
            reservations.add(candidate)
            return candidate
    raise RuntimeError("unable to generate a unique registration email")


@tool(name="generate_registration_profile")
def generate_registration_profile() -> str:
    """Generate reusable, non-secret values for required registration profile fields."""

    suffix = secrets.randbelow(9_000_000) + 1_000_000
    return json.dumps(
        {
            "first_name": "Test",
            "last_name": "User",
            "company": "TestCo",
            "phone_number": f"555{suffix:07d}",
            "card_number": "4111111111111111",
        },
        sort_keys=True,
    )


def _require_rotation_request(store: Any, request_id: str) -> str:
    """Restrict maintenance mutations to the request supplied by the launcher."""

    normalized = str(request_id or "").strip()
    if not normalized or normalized != os.getenv("CYBER_CREDENTIAL_ROTATION_REQUEST", "").strip():
        raise ValueError("credential rotation request is unavailable to this operation")
    request = store.get_credential_rotation_request(normalized)
    if request is None or request["status"] != "claimed" or request["maintenance_operation_id"] != _operation_id():
        raise ValueError("credential rotation request does not match this maintenance operation")
    active_task = active_credential_task(store, _operation_id())
    if active_task is None or active_task.task_uid != request["claimed_task_uid"]:
        raise ValueError("credential rotation request is unavailable to this task")
    return normalized


@tool(name="stage_credential_rotation")
def stage_credential_rotation(request_id: str, replacement_credential_id: str) -> str:
    """Stage a replacement credential for the active maintenance request without retiring the predecessor."""

    store = _get_database_store()
    request = store.stage_credential_rotation_request(
        _require_rotation_request(store, request_id), str(replacement_credential_id or "").strip()
    )
    return json.dumps(request, sort_keys=True)


@tool(name="complete_credential_rotation")
def complete_credential_rotation(request_id: str, evidence_refs: list[str]) -> str:
    """Complete a staged credential rotation only after durable target verification evidence exists."""

    store = _get_database_store()
    request = store.complete_credential_rotation_request(_require_rotation_request(store, request_id), evidence_refs)
    return json.dumps(request, sort_keys=True)


@tool(name="fail_credential_rotation")
def fail_credential_rotation(request_id: str, reason: str) -> str:
    """Record a terminal maintenance failure while retaining the predecessor and staged replacement for audit."""

    store = _get_database_store()
    request = store.fail_credential_rotation_request(_require_rotation_request(store, request_id), reason)
    return json.dumps(request, sort_keys=True)


@tool(name="generate_mfa_code", context=True)
def generate_mfa_code(
    provisioning_secret: str | None = None,
    digits: int = 6,
    period: int = 30,
    algorithm: str = "SHA1",
    credential_id: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Generate a current TOTP code. The code and provisioning secret are never persisted by this tool.

    Workflow agents must pass a checked-out `credential_id` for configured TOTP MFA. The direct
    `provisioning_secret` argument remains available only for standalone compatibility.
    """

    if tool_context is not None and provisioning_secret:
        raise ValueError("workflow agent TOTP generation must use a checked-out credential_id")
    if credential_id:
        if provisioning_secret:
            raise ValueError("credential_id cannot be combined with a provisioning_secret")
        store = _get_database_store()
        operation_id = _operation_id()
        active_task, record = _active_checked_out_credential(store, operation_id, str(credential_id))
        payload = record["payload"]
        mfa = payload.get("mfa") if isinstance(payload, dict) else None
        if not isinstance(mfa, dict) or str(mfa.get("type") or "").lower() != "totp":
            raise ValueError("credential does not have configured TOTP MFA")
        code = _generate_totp_code(
            str(mfa["secret"]),
            int(mfa.get("digits", 6)),
            int(mfa.get("period", 30)),
            str(mfa.get("algorithm", "SHA1")),
        )
        store.record_credential_usage(
            operation_id,
            record["credential_id"],
            task_uid=active_task.task_uid,
            authentication_mode="mfa",
            outcome="used",
        )
        return register_runtime_secret(code)

    if not provisioning_secret:
        raise ValueError("provisioning_secret or credential_id is required")

    return register_runtime_secret(_generate_totp_code(provisioning_secret, digits, period, algorithm))


def _generate_totp_code(provisioning_secret: str, digits: int = 6, period: int = 30, algorithm: str = "SHA1") -> str:
    """Generate a TOTP value from validated direct or credential-backed inputs."""

    if digits < 6 or digits > 10 or period <= 0:
        raise ValueError("invalid TOTP digits or period")
    normalized_algorithm = str(algorithm or "").upper()
    if normalized_algorithm not in {"SHA1", "SHA256", "SHA512"}:
        raise ValueError("invalid TOTP algorithm")
    normalized_secret = provisioning_secret.strip().replace(" ", "")
    try:
        secret = base64.b32decode(normalized_secret.upper() + "=" * (-len(normalized_secret) % 8))
    except Exception as error:
        raise ValueError("invalid TOTP provisioning secret") from error
    counter = int(time.time() // period)
    digest = hmac.new(secret, struct.pack(">Q", counter), getattr(hashlib, normalized_algorithm.lower())).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def _active_checked_out_credential(store: Any, operation_id: str, credential_id: str) -> tuple[Any, dict[str, Any]]:
    """Return an active task and its selected, target-scoped target credential."""

    active_task, target_values = _active_task_target_values(store, operation_id)
    selected_ids = store.credential_ids_selected_by_task(operation_id, active_task.task_uid)
    if credential_id not in selected_ids:
        raise ValueError("credential must be checked out by the active task")
    record = store.get_credential(credential_id, include_payload=True)
    if record is None or record["status"] not in {"unknown", "valid"}:
        raise ValueError("eligible credential is required for MFA")
    if record["credential_type"] == "email_login":
        raise ValueError("credential access requires a target credential, not a mailbox credential")
    if str(record.get("target") or "") not in target_values:
        raise ValueError("MFA credential is outside the active task target scope")
    _register_credential_payload_secrets(record)
    return active_task, record


def _register_credential_payload_secrets(record: dict[str, Any]) -> None:
    """Register payload secret values before a worker can reuse them in generic tool input."""

    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    for key in ("password", "api_key", "client_secret"):
        if isinstance(payload.get(key), str):
            register_runtime_secret(payload[key])
    mfa = payload.get("mfa") if isinstance(payload.get("mfa"), dict) else {}
    if isinstance(mfa.get("secret"), str):
        register_runtime_secret(mfa["secret"])


def _active_mfa_mailbox_task(store: Any, operation_id: str, mailbox_credential_id: str) -> tuple[Any, dict[str, Any]]:
    """Require an active selected target credential that explicitly references a mailbox."""

    active_task = active_credential_task(store, operation_id)
    if active_task is None:
        raise ValueError("an active task is required for email MFA")
    selected_ids = store.credential_ids_selected_by_task(operation_id, active_task.task_uid)
    for credential_id in selected_ids:
        try:
            _, record = _active_checked_out_credential(store, operation_id, credential_id)
        except ValueError:
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        mfa = payload.get("mfa") if isinstance(payload.get("mfa"), dict) else {}
        if (
            str(mfa.get("type") or "").lower() == "email"
            and str(mfa.get("mailbox_credential_id") or "") == mailbox_credential_id
        ):
            return active_task, record
    raise ValueError("email MFA mailbox is not configured by an active checked-out credential")


def _imap_internal_date(value: Any) -> datetime | None:
    """Parse IMAP's server-assigned INTERNALDATE, not an untrusted message Date header."""

    metadata = value.decode("ascii", errors="replace") if isinstance(value, bytes) else str(value or "")
    match = _IMAP_INTERNALDATE_PATTERN.search(metadata)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group("timestamp"), "%d-%b-%Y %H:%M:%S %z").astimezone(UTC)
    except ValueError:
        return None


@tool(name="request_mfa_code")
def request_mfa_code(
    credential_id: str,
    prompt: str = "Enter the one-time code from your authenticator or email.",
    code_pattern: str = r"\d{6,10}",
    ttl_seconds: int = 300,
) -> str:
    """Request a one-time MFA code through the terminal UI without persisting the code.

    Call only after the target has requested MFA. The controller records a pending challenge and emits a sensitive
    user handoff; the React terminal or interactive CLI sends the response to this waiting tool. For TOTP configured
    in the checked-out credential, prefer `generate_mfa_code` instead of requesting a user code.
    """

    if ttl_seconds < 30 or ttl_seconds > 900:
        raise ValueError("MFA challenge TTL must be between 30 and 900 seconds")
    try:
        pattern = re.compile(code_pattern)
    except re.error as error:
        raise ValueError("invalid MFA code pattern") from error
    if pattern.groups:
        raise ValueError("MFA code pattern must not contain capture groups")
    store = _get_database_store()
    operation_id = _operation_id()
    active_task, record = _active_checked_out_credential(store, operation_id, str(credential_id))
    payload = record["payload"]
    mfa = payload.get("mfa") if isinstance(payload, dict) else None
    if not isinstance(mfa, dict) or str(mfa.get("type") or "").lower() not in {"totp", "email"}:
        raise ValueError("credential does not have a configured MFA method")
    if str(mfa.get("type")).lower() == "totp":
        raise ValueError("configured TOTP credentials must use generate_mfa_code")
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
    challenge = store.create_mfa_challenge(
        operation_id,
        record["credential_id"],
        "email",
        expires_at,
        {"code_pattern": code_pattern, "prompt": str(prompt or "")[:240]},
        task_uid=active_task.task_uid,
    )
    emit_memory_event(
        {
            "type": "user_handoff",
            "message": str(prompt or "Enter the one-time MFA code.")[:500],
            "breakout": False,
            "sensitive": True,
            "handoff_kind": "mfa_code",
            "challenge_id": challenge["challenge_id"],
            "expires_at": challenge["expires_at"],
        }
    )
    try:
        code = input().strip()
    except (EOFError, OSError) as error:
        store.block_mfa_challenge(operation_id, challenge["challenge_id"], task_uid=active_task.task_uid)
        store.record_credential_usage(
            operation_id,
            record["credential_id"],
            task_uid=active_task.task_uid,
            authentication_mode="mfa",
            outcome="blocked",
        )
        raise ValueError("MFA code handoff is unavailable") from error
    if not pattern.fullmatch(code):
        store.block_mfa_challenge(operation_id, challenge["challenge_id"], task_uid=active_task.task_uid)
        store.record_credential_usage(
            operation_id,
            record["credential_id"],
            task_uid=active_task.task_uid,
            authentication_mode="mfa",
            outcome="blocked",
        )
        raise ValueError("MFA code did not match the requested format")
    store.complete_mfa_challenge(operation_id, challenge["challenge_id"], task_uid=active_task.task_uid)
    store.record_credential_usage(
        operation_id,
        record["credential_id"],
        task_uid=active_task.task_uid,
        authentication_mode="mfa",
        outcome="used",
    )
    return register_runtime_secret(code)


@tool(name="begin_email_mfa_retrieval")
def begin_email_mfa_retrieval(
    mailbox_credential_id: str,
    sender_contains: str = "",
    subject_contains: str = "",
    code_pattern: str = r"\b\d{6}\b",
    ttl_seconds: int = 300,
) -> dict[str, str]:
    """Snapshot a mailbox before the target triggers email MFA and return an opaque challenge ID."""

    if ttl_seconds < 30 or ttl_seconds > 900:
        raise ValueError("MFA challenge TTL must be between 30 and 900 seconds")
    store = _get_database_store()
    operation_id = _operation_id()
    active_task, target_record = _active_mfa_mailbox_task(store, operation_id, str(mailbox_credential_id))
    record = store.get_credential(str(mailbox_credential_id), include_payload=True)
    if record is None or record["credential_type"] != "email_login" or record["status"] not in {"unknown", "valid"}:
        raise ValueError("eligible email_login credential is required")
    try:
        pattern = re.compile(code_pattern)
    except re.error as error:
        raise ValueError("invalid email MFA code pattern") from error
    if pattern.groups:
        raise ValueError("email MFA code pattern must not contain capture groups")
    payload = record["payload"]
    mailbox = payload["mailbox"]
    client = None
    try:
        client = imaplib.IMAP4_SSL(mailbox["host"], int(mailbox["port"]))
        client.login(payload["email"], payload["password"])
        status, _ = client.select(mailbox.get("folder") or "INBOX", readonly=True)
        if status != "OK":
            raise ValueError("email MFA mailbox folder is unavailable")
        status, message_ids = client.uid("search", None, "ALL")
        if status != "OK":
            raise ValueError("email MFA mailbox search failed")
        uids = [int(value) for value in message_ids[0].split() if value.isdigit()]
        uidvalidity = (client.response("UIDVALIDITY")[1] or [b""])[0].decode("ascii", errors="ignore")
        challenge = store.create_mfa_challenge(
            operation_id,
            target_record["credential_id"],
            "email",
            (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
            {
                "code_pattern": code_pattern,
                "source": "imap",
                "mailbox_credential_id": record["credential_id"],
                "sender_contains": str(sender_contains or "")[:240],
                "subject_contains": str(subject_contains or "")[:240],
                "uidvalidity": uidvalidity,
                "highest_uid": max(uids, default=0),
            },
            task_uid=active_task.task_uid,
        )
        return {"challenge_id": challenge["challenge_id"], "expires_at": challenge["expires_at"]}
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


@tool(name="retrieve_email_mfa_code")
def retrieve_email_mfa_code(
    mailbox_credential_id: str,
    challenge_id: str = "",
    sender_contains: str = "",
    subject_contains: str = "",
    code_pattern: str = r"\b\d{6}\b",
    ttl_seconds: int = 300,
) -> str:
    """Retrieve one unique email MFA code through the configured TLS IMAP mailbox.

    The code is returned only to the active agent and is never persisted. A missing or ambiguous code is an error so
    callers can request an interactive MFA handoff instead of submitting an unrelated email code. The controller
    retains the resulting challenge metadata, but never the retrieved code.
    """

    if ttl_seconds < 30 or ttl_seconds > 900:
        raise ValueError("MFA challenge TTL must be between 30 and 900 seconds")
    store = _get_database_store()
    operation_id = _operation_id()
    active_task, target_record = _active_mfa_mailbox_task(store, operation_id, str(mailbox_credential_id))
    record = store.get_credential(str(mailbox_credential_id), include_payload=True)
    if record is None or record["credential_type"] != "email_login" or record["status"] not in {"unknown", "valid"}:
        raise ValueError("eligible email_login credential is required")
    payload = record["payload"]
    mailbox = payload["mailbox"]
    try:
        pattern = re.compile(code_pattern)
    except re.error as error:
        raise ValueError("invalid email MFA code pattern") from error
    if pattern.groups:
        raise ValueError("email MFA code pattern must not contain capture groups")
    if challenge_id:
        challenge = store.get_mfa_challenge(operation_id, challenge_id, task_uid=active_task.task_uid)
        metadata = challenge["metadata"]
        if challenge["method"] != "email" or metadata.get("mailbox_credential_id") != record["credential_id"]:
            raise ValueError("email MFA challenge does not match this mailbox")
        sender_contains = str(metadata.get("sender_contains") or "")
        subject_contains = str(metadata.get("subject_contains") or "")
        code_pattern = str(metadata.get("code_pattern") or code_pattern)
        pattern = re.compile(code_pattern)
    else:
        # Standalone compatibility: workflow callers must snapshot with begin_email_mfa_retrieval first.
        challenge = store.create_mfa_challenge(
            operation_id, target_record["credential_id"], "email", (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
            {"code_pattern": code_pattern, "source": "imap", "mailbox_credential_id": record["credential_id"],
             "sender_contains": str(sender_contains or "")[:240], "subject_contains": str(subject_contains or "")[:240]},
            task_uid=active_task.task_uid,
        )
        metadata = challenge["metadata"]
    client = None
    mailbox_login_attempted = False
    try:
        client = imaplib.IMAP4_SSL(mailbox["host"], int(mailbox["port"]))
        mailbox_login_attempted = True
        client.login(payload["email"], payload["password"])
        status, _ = client.select(mailbox.get("folder") or "INBOX", readonly=True)
        if status != "OK":
            raise ValueError("email MFA mailbox folder is unavailable")
        if challenge_id:
            current_uidvalidity = (client.response("UIDVALIDITY")[1] or [b""])[0].decode("ascii", errors="ignore")
            if current_uidvalidity != str(metadata.get("uidvalidity") or ""):
                raise ValueError("email MFA mailbox UIDVALIDITY changed")
            status, message_ids = client.uid("search", None, f"{int(metadata.get('highest_uid') or 0) + 1}:*")
        else:
            status, message_ids = client.search(None, "ALL")
        if status != "OK":
            raise ValueError("email MFA mailbox search failed")
        matches: set[str] = set()
        for message_id in list(message_ids[0].split())[-20:]:
            status, data = client.fetch(message_id, "(RFC822 INTERNALDATE)")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            if not challenge_id:
                received_at = _imap_internal_date(data[0][0])
                # Legacy standalone callers have no pre-trigger snapshot. Workflow callers must use challenge_id.
                if received_at is None or received_at < datetime.fromisoformat(challenge["created_at"]).astimezone(UTC) - timedelta(seconds=60):
                    continue
            message = email.message_from_bytes(data[0][1])
            sender = str(message.get("From") or "")
            subject = str(message.get("Subject") or "")
            if sender_contains and sender_contains.casefold() not in sender.casefold():
                continue
            if subject_contains and subject_contains.casefold() not in subject.casefold():
                continue
            parts = []
            if message.is_multipart():
                for part in message.walk():
                    if part.get_content_type() == "text/plain":
                        payload_bytes = part.get_payload(decode=True) or b""
                        parts.append(payload_bytes.decode(part.get_content_charset() or "utf-8", errors="replace"))
            else:
                payload_bytes = message.get_payload(decode=True) or b""
                parts.append(payload_bytes.decode(message.get_content_charset() or "utf-8", errors="replace"))
            matches.update(match.group(0) for match in pattern.finditer("\n".join(parts)))
        if len(matches) != 1:
            raise ValueError("email MFA code is missing or ambiguous")
        code = next(iter(matches))
        store.complete_mfa_challenge(operation_id, challenge["challenge_id"], task_uid=active_task.task_uid)
        store.record_credential_usage(
            operation_id,
            target_record["credential_id"],
            task_uid=active_task.task_uid,
            authentication_mode="mfa",
            outcome="used",
        )
        store.record_credential_usage(
            operation_id,
            record["credential_id"],
            task_uid=active_task.task_uid,
            authentication_mode="mfa",
            outcome="used",
        )
        return register_runtime_secret(code)
    except Exception:
        store.block_mfa_challenge(operation_id, challenge["challenge_id"], task_uid=active_task.task_uid)
        store.record_credential_usage(
            operation_id,
            target_record["credential_id"],
            task_uid=active_task.task_uid,
            authentication_mode="mfa",
            outcome="blocked",
        )
        if mailbox_login_attempted:
            store.record_credential_usage(
                operation_id,
                record["credential_id"],
                task_uid=active_task.task_uid,
                authentication_mode="mfa",
                outcome="blocked",
            )
        raise
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
