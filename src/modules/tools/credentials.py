"""Typed credential-store tools backed by the application SQLite database.

Credential payloads are intentionally retained only in the application database
and are never included in list or status responses.  The database is currently
plaintext by product choice, so callers must not write returned secret values to
artifacts, logs, reports, or tool descriptions.
"""

from __future__ import annotations

import base64
import email
import hashlib
import hmac
import imaplib
import json
import re
import secrets
import string
import struct
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from strands import tool

from modules.tools.memory import (
    _get_database_store,
    _operation_id,
    active_credential_task,
    emit_memory_event,
)

_CREDENTIAL_TYPES = frozenset({"username_password", "email_login", "api_key", "oauth2_client"})
_STATUSES = frozenset({"unknown", "pending", "valid", "invalid", "expired", "revoked", "retired"})
_OBJECTIVE_LOGIN_PATTERN = re.compile(
    r"(?is)\b(?:username|user)\s*[:=]\s*(?P<username>[^\s,;]+).*?\b(?:password|pass)\s*[:=]\s*(?P<password>[^\s,;]+)"
)
_OBJECTIVE_API_KEY_PATTERN = re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?key)\s*[:=]\s*(?P<api_key>[^\s,;]+)")


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
    return {
        "client_id": _require_string(values, "client_id"),
        "client_secret": _require_string(values, "client_secret"),
        "token_url": canonicalize_credential_target(_require_string(values, "token_url")),
        "scopes": [str(scope).strip() for scope in values.get("scopes", []) if str(scope).strip()],
        "audience": str(values.get("audience") or "").strip(),
        "client_auth_method": str(values.get("client_auth_method") or "client_secret_basic").strip(),
    }


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
    return _get_database_store().store_credential(
        operation_id,
        {
            "credential_type": kind,
            "target": canonicalize_credential_target(target) if target else None,
            "role": role,
            "operation_id": operation_scope,
            "payload": payload,
            "origin": origin,
            "management_policy": management_policy,
            "status": "unknown",
        },
    )


@tool(name="store_credential")
def store_credential(
    credential_type: str,
    values: dict[str, Any],
    target: str | None = None,
    role: str | None = None,
    operation_scope: str | None = None,
    origin: str = "found",
    account_label: str | None = None,
    tenant_label: str | None = None,
) -> str:
    """Store a credential found in a target or created by authorized self-registration.

    Use `registered` for a successful self-registration and `found` for a credential recovered from target-owned
    evidence. Never store user-provided credentials with this tool. Passwords and other secret values are not echoed.
    """

    normalized_origin = str(origin or "").strip().lower()
    if normalized_origin not in {"found", "registered"}:
        raise ValueError("agent credential origin must be found or registered")
    record = store_user_credential(
        operation_id=_operation_id(),
        credential_type=credential_type,
        target=target,
        role=role,
        values=values,
        operation_scope=operation_scope,
        account_label=account_label,
        tenant_label=tenant_label,
        origin=normalized_origin,
        management_policy="operation",
    )
    return json.dumps({"stored": True, "credential": record})


@tool(name="query_credentials")
def query_credentials(
    target: str | None = None,
    role: str | None = None,
    credential_type: str | None = None,
) -> str:
    """List eligible credential metadata for the current operation without returning secret values."""

    normalized_type = str(credential_type or "").strip().lower() or None
    if normalized_type is not None and normalized_type not in _CREDENTIAL_TYPES:
        raise ValueError("unknown credential_type")
    records = _get_database_store().list_credentials(
        _operation_id(),
        target=canonicalize_credential_target(target) if target else None,
        role=str(role).strip() if role else None,
        credential_type=normalized_type,
    )
    return json.dumps({"credentials": records})


@tool(name="plan_access_control_comparisons")
def plan_access_control_comparisons(target: str) -> str:
    """List safe, credential-ID-only account, role, and tenant comparison pairs for authorized IDOR testing.

    Use the returned IDs to check out two distinct credentials and test only the assigned target. No pair is invented:
    an empty comparison list is a coverage gap that must be reported rather than bypassed.
    """

    normalized_target = canonicalize_credential_target(target)
    records = _get_database_store().list_credentials(_operation_id(), target=normalized_target)
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
    unique_comparisons = list({(item["kind"], item["left_credential_id"], item["right_credential_id"]): item for item in comparisons}.values())
    return json.dumps(
        {
            "target": normalized_target,
            "comparisons": unique_comparisons,
            "coverage_gap": None if unique_comparisons else "No distinct eligible account, role, or tenant credential pair.",
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
    active_task = active_credential_task(store, operation_id)
    if active_task is None:
        raise ValueError("an active task is required to set an authentication context")
    selected_ids = store.credential_ids_selected_by_task(operation_id, active_task.task_uid)
    missing_selection = sorted(set(normalized_ids) - selected_ids)
    if missing_selection:
        raise ValueError("authentication context credentials must be checked out by the active task")
    target_values = _task_resolved_targets(store, operation_id, active_task)
    if not target_values:
        raise ValueError("active task does not have a resolved target scope")
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
    active_task = active_credential_task(store, _operation_id())
    if active_task is None:
        raise ValueError("an active task is required to check out a credential")
    target_values = _task_resolved_targets(store, _operation_id(), active_task)
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
    return json.dumps({"credential_id": record["credential_id"], "credential_type": record["credential_type"], "values": record["payload"]})


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
    record = _get_database_store().record_credential_status(
        _operation_id(), str(credential_id), normalized_status, "operation", str(reason or ""), evidence_refs or []
    )
    return json.dumps({"credential": record})


@tool(name="rotate_credential")
def rotate_credential(credential_id: str, values: dict[str, Any], reason: str) -> str:
    """Replace an operation-managed credential while retaining the retired credential's audit history.

    This tool is limited to credentials created by an operation. User-provided credentials must be changed through the
    React configuration or environment import path. The old record is retained with status `retired`.
    """

    if not str(reason or "").strip():
        raise ValueError("credential rotation reason is required")
    store = _get_database_store()
    previous = store.get_credential(str(credential_id), include_payload=True)
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
        _operation_id(),
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
        },
    )
    store.record_credential_status(
        _operation_id(),
        previous["credential_id"],
        "retired",
        "operation",
        str(reason).strip(),
        [],
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
    return "".join(characters)


@tool(name="generate_mfa_code")
def generate_mfa_code(provisioning_secret: str, digits: int = 6, period: int = 30, algorithm: str = "SHA1") -> str:
    """Generate a current TOTP code. The code is never persisted by this tool."""

    if digits < 6 or digits > 10 or period <= 0:
        raise ValueError("invalid TOTP digits or period")
    normalized_algorithm = str(algorithm or "").upper()
    if normalized_algorithm not in {"SHA1", "SHA256", "SHA512"}:
        raise ValueError("invalid TOTP algorithm")
    try:
        secret = base64.b32decode(provisioning_secret.strip().replace(" ", "").upper() + "=" * (-len(provisioning_secret.strip().replace(" ", "")) % 8))
    except Exception as error:
        raise ValueError("invalid TOTP provisioning secret") from error
    counter = int(time.time() // period)
    digest = hmac.new(secret, struct.pack(">Q", counter), getattr(hashlib, normalized_algorithm.lower())).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


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
    record = store.get_credential(str(credential_id), include_payload=True)
    if record is None or record["status"] not in {"unknown", "valid"}:
        raise ValueError("eligible credential is required for MFA")
    payload = record["payload"]
    mfa = payload.get("mfa") if isinstance(payload, dict) else None
    if not isinstance(mfa, dict) or str(mfa.get("type") or "").lower() not in {"totp", "email"}:
        raise ValueError("credential does not have a configured MFA method")
    if str(mfa.get("type")).lower() == "totp":
        raise ValueError("configured TOTP credentials must use generate_mfa_code")
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
    challenge = store.create_mfa_challenge(
        _operation_id(),
        record["credential_id"],
        "email",
        expires_at,
        {"code_pattern": code_pattern, "prompt": str(prompt or "")[:240]},
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
        raise ValueError("MFA code handoff is unavailable") from error
    if not pattern.fullmatch(code):
        raise ValueError("MFA code did not match the requested format")
    store.complete_mfa_challenge(_operation_id(), challenge["challenge_id"])
    active_task = active_credential_task(store, _operation_id())
    store.record_credential_usage(
        _operation_id(),
        record["credential_id"],
        task_uid=active_task.task_uid if active_task is not None else None,
        authentication_mode="mfa",
        outcome="used",
    )
    return code


@tool(name="retrieve_email_mfa_code")
def retrieve_email_mfa_code(
    mailbox_credential_id: str,
    sender_contains: str = "",
    subject_contains: str = "",
    code_pattern: str = r"\b\d{6}\b",
) -> str:
    """Retrieve one unique email MFA code through the configured TLS IMAP mailbox.

    The code is returned only to the active agent and is never persisted. A missing or ambiguous code is an error so
    callers can request an interactive MFA handoff instead of submitting an unrelated email code.
    """

    store = _get_database_store()
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
    client = imaplib.IMAP4_SSL(mailbox["host"], int(mailbox["port"]))
    try:
        client.login(payload["email"], payload["password"])
        status, _ = client.select(mailbox.get("folder") or "INBOX", readonly=True)
        if status != "OK":
            raise ValueError("email MFA mailbox folder is unavailable")
        status, message_ids = client.search(None, "ALL")
        if status != "OK":
            raise ValueError("email MFA mailbox search failed")
        matches: set[str] = set()
        for message_id in list(message_ids[0].split())[-20:]:
            status, data = client.fetch(message_id, "(RFC822)")
            if status != "OK" or not data or not isinstance(data[0], tuple):
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
        active_task = active_credential_task(store, _operation_id())
        store.record_credential_usage(
            _operation_id(),
            record["credential_id"],
            task_uid=active_task.task_uid if active_task is not None else None,
            authentication_mode="mfa",
            outcome="used",
        )
        return code
    finally:
        try:
            client.logout()
        except Exception:
            pass
