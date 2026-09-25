"""Operation-local, secret-safe authenticated HTTP contexts."""

from __future__ import annotations

import json
import logging
import re
import time
from base64 import b64encode
from collections.abc import Iterable
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import requests
from pydantic import TypeAdapter, ValidationError
from strands import tool

from modules.tools.credentials import (
    AUTH_CONTEXT_UNAVAILABLE_ERROR,
    CredentialAccessMode,
    _active_credential_for_access,
    _active_task_target_values,
)
from modules.tools.memory import _get_database_store, _operation_id, active_credential_task
from modules.tools.semantic_enum import normalize_semantic_enum
from modules.utils.redaction import redact, register_runtime_secret


@dataclass
class _AuthenticationContext:
    target: str
    credential_id: str
    session: requests.Session
    headers: dict[str, str]
    params: dict[str, str]
    validation_url: str


_CONTEXTS: dict[tuple[str, str, str], _AuthenticationContext] = {}
_AUTHENTICATION_ATTEMPTS: dict[tuple[str, str, str], dict[str, Any]] = {}

AUTHENTICATION_FLOW_KINDS = frozenset({
    "api_form",
    "browser_form",
    "browser_redirect",
    "browser_mfa",
    "api_key",
    "oauth2_client",
})
REGISTRATION_FLOW_KINDS = frozenset({"browser_registration", "api_registration"})
AUTHENTICATION_FLOW_VERSION = 6
_AUTHORIZATION_STORAGE_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_LOGGER = logging.getLogger(__name__)
_PURPOSE_ADAPTER = TypeAdapter(Literal["authentication", "registration"])
_FLOW_KIND_ADAPTER = TypeAdapter(
    Literal[
        "api_form",
        "browser_form",
        "browser_redirect",
        "browser_mfa",
        "api_key",
        "oauth2_client",
        "browser_registration",
        "api_registration",
    ]
)
_REQUEST_FORMAT_ADAPTER = TypeAdapter(Literal["", "json", "form"])


def _normalize_authentication_enum(value: str, *, field_name: str) -> str:
    """Normalize common model phrasing, then validate a fixed authentication value with Pydantic."""

    aliases = {
        "purpose": {
            "auth": "authentication",
            "login": "authentication",
            "sign_in": "authentication",
            "signin": "authentication",
            "register": "registration",
            "signup": "registration",
            "sign_up": "registration",
            "create_account": "registration",
        },
        "kind": {
            "login_form": "api_form",
            "form_login": "api_form",
            "web_form": "browser_form",
            "browser_login": "browser_form",
            "redirect_login": "browser_redirect",
            "mfa": "browser_mfa",
            "api_key_auth": "api_key",
            "oauth": "oauth2_client",
            "oauth2": "oauth2_client",
            "signup_form": "browser_registration",
            "browser_signup": "browser_registration",
            "api_signup": "api_registration",
        },
        "request_format": {
            "application_json": "json",
            "application/json": "json",
            "form_encoded": "form",
            "application/x-www-form-urlencoded": "form",
            "urlencoded": "form",
            "url_encoded": "form",
        },
    }
    normalized = normalize_semantic_enum(
        value,
        aliases=aliases[field_name],
        field_name=field_name,
        logger=_LOGGER,
    )
    adapter = {
        "purpose": _PURPOSE_ADAPTER,
        "kind": _FLOW_KIND_ADAPTER,
        "request_format": _REQUEST_FORMAT_ADAPTER,
    }[field_name]
    try:
        return adapter.validate_python(normalized)
    except ValidationError as error:
        choices = {
            "purpose": "authentication or registration",
            "kind": ", ".join(sorted(AUTHENTICATION_FLOW_KINDS | REGISTRATION_FLOW_KINDS)),
            "request_format": "empty, json, or form",
        }[field_name]
        raise ValueError(f"{field_name} must be one of: {choices}") from error


def _credential_target_url_contains(target: str, candidate: str, *, label: str) -> bool:
    """Return whether a URL is on the credential target origin and path boundary."""

    try:
        target_url = urlsplit(target)
        candidate_url = urlsplit(candidate)
        target_port = target_url.port or (443 if target_url.scheme.lower() == "https" else 80)
        candidate_port = candidate_url.port or (443 if candidate_url.scheme.lower() == "https" else 80)
    except ValueError as error:
        raise ValueError(f"{label} has an invalid URL") from error
    if (
        target_url.scheme.lower() != candidate_url.scheme.lower()
        or (target_url.hostname or "").encode("idna").decode("ascii").lower()
        != (candidate_url.hostname or "").encode("idna").decode("ascii").lower()
        or target_port != candidate_port
    ):
        return False
    target_path = target_url.path.rstrip("/") or "/"
    candidate_path = candidate_url.path.rstrip("/") or "/"
    return target_path == "/" or candidate_path == target_path or candidate_path.startswith(f"{target_path}/")


def _safe_flow_descriptor_url(target: str, value: str, *, label: str) -> str:
    """Validate a target-bound URL before storing it as a secret-free flow descriptor."""

    if not _credential_target_url_contains(target, value, label=label):
        raise ValueError(f"{label} must share the credential target boundary")
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain user info, query values, or fragments")
    return value


def _safe_authorization_storage_key(value: str) -> str:
    """Validate a secret-free browser storage key name for a flow descriptor."""

    normalized = str(value or "").strip()
    if normalized and not _AUTHORIZATION_STORAGE_KEY_PATTERN.fullmatch(normalized):
        raise ValueError("authorization storage key must be a simple browser storage key name")
    return normalized


def _normalize_storage_header_bindings(
    bindings: list[dict[str, str]] | None,
    authorization_storage_key: str = "",
) -> list[dict[str, str]]:
    """Validate secret-free storage-to-header instructions for a browser auth flow."""

    normalized: list[dict[str, str]] = []
    seen_header_names: set[str] = set()
    for binding in bindings or []:
        if not isinstance(binding, dict):
            raise ValueError("storage header bindings must be objects")
        header_name = str(binding.get("header_name") or "").strip()
        storage_key = _safe_authorization_storage_key(str(binding.get("storage_key") or ""))
        value_template = str(binding.get("value_template") or "").strip()
        if not _HEADER_NAME_PATTERN.fullmatch(header_name):
            raise ValueError("storage header binding header_name must be a valid HTTP header name")
        if not storage_key:
            raise ValueError("storage header binding storage_key is required")
        if (
            value_template.count("{value}") != 1
            or "{" in value_template.replace("{value}", "")
            or "}" in value_template.replace("{value}", "")
        ):
            raise ValueError("storage header binding value_template must contain exactly one {value} placeholder")
        normalized_name = header_name.lower()
        if normalized_name in seen_header_names:
            raise ValueError("storage header bindings must not repeat a header name")
        seen_header_names.add(normalized_name)
        normalized.append(
            {
                "header_name": header_name,
                "storage_key": storage_key,
                "value_template": value_template,
            }
        )

    legacy_key = _safe_authorization_storage_key(authorization_storage_key)
    if legacy_key and "authorization" not in seen_header_names:
        normalized.append(
            {
                "header_name": "Authorization",
                "storage_key": legacy_key,
                "value_template": "Bearer {value}",
            }
        )
    return normalized


def _is_current_authentication_flow(descriptor: dict[str, Any]) -> bool:
    """Return whether a persisted flow uses the current descriptor contract."""

    return descriptor.get("flow_version") == AUTHENTICATION_FLOW_VERSION


def _context_key(operation_id: str, target: str, credential_id: str) -> tuple[str, str, str]:
    return operation_id, target.rstrip("/"), credential_id


def _bound_credential_id(task: Any, credential_id: str) -> str:
    """Use the sole controller-bound credential when an executor supplies no ID."""

    if credential_id:
        return credential_id
    context = task.auth_context if isinstance(task.auth_context, dict) else {}
    identifiers = [str(value) for value in context.get("credential_ids", []) if str(value).strip()]
    if len(identifiers) != 1:
        raise ValueError("authenticated request requires one controller-bound credential")
    return identifiers[0]


def _safe_context(context: _AuthenticationContext, status: str) -> str:
    return json.dumps(
        {
            "credential_id": context.credential_id,
            "target": context.target,
            "status": status,
            "authentication_ready": status == "valid",
        },
        sort_keys=True,
    )


def _validate(context: _AuthenticationContext) -> bool:
    started_at = time.perf_counter()
    try:
        response = context.session.get(
            context.validation_url, headers=context.headers, params=context.params, timeout=15
        )
    except requests.RequestException as error:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        request = getattr(error, "request", None)
        request_source = "exception_request" if request is not None else "intended_context"
        diagnostics = _validation_request_diagnostics(context, request)
        _LOGGER.warning(
            "Authenticated context validation failed credential_id=%s method=GET url=%s "
            "reason=request_exception exception_type=%s elapsed_ms=%.1f request_source=%s "
            "request_headers=%s cookies=%s",
            context.credential_id,
            redact(_diagnostic_validation_url(context.validation_url)),
            type(error).__name__,
            elapsed_ms,
            request_source,
            json.dumps(diagnostics["headers"], sort_keys=True),
            json.dumps(diagnostics["cookies"], sort_keys=True),
        )
        return False
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    if 200 <= response.status_code < 300:
        _LOGGER.info(
            "Authenticated context validation succeeded credential_id=%s method=GET url=%s "
            "status_code=%s elapsed_ms=%.1f",
            context.credential_id,
            redact(_diagnostic_validation_url(context.validation_url)),
            response.status_code,
            elapsed_ms,
        )
        return True
    request = getattr(response, "request", None)
    diagnostics = _validation_request_diagnostics(context, request)
    request_source = "response_request" if request is not None else "intended_context"
    _LOGGER.warning(
        "Authenticated context validation failed credential_id=%s method=GET url=%s "
        "reason=non_success_status status_code=%s elapsed_ms=%.1f request_source=%s "
        "request_headers=%s cookies=%s",
        context.credential_id,
        redact(_diagnostic_validation_url(context.validation_url)),
        response.status_code,
        elapsed_ms,
        request_source,
        json.dumps(diagnostics["headers"], sort_keys=True),
        json.dumps(diagnostics["cookies"], sort_keys=True),
    )
    return False


def _diagnostic_validation_url(value: str) -> str:
    """Drop URL user info, query, and fragment before writing a validation URL to logs."""

    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _validation_request_diagnostics(
    context: _AuthenticationContext,
    request: Any = None,
) -> dict[str, Any]:
    """Return redacted headers and cookie metadata for a completed or intended request."""

    if request is None:
        try:
            request = context.session.prepare_request(
                requests.Request(
                    "GET",
                    context.validation_url,
                    headers=context.headers,
                    params=context.params,
                )
            )
        except (AttributeError, requests.RequestException, TypeError, ValueError):
            request = None

    request_headers = getattr(request, "headers", None) or context.headers
    cookie_header = str(request_headers.get("Cookie", request_headers.get("cookie", "")) or "")
    cookies = SimpleCookie()
    cookies.load(cookie_header)
    cookie_metadata = [
        {"name": name, "value": redact({"cookie": morsel.value})["cookie"]}
        for name, morsel in sorted(cookies.items())
    ]
    headers_without_cookie = {
        name: value
        for name, value in request_headers.items()
        if str(name).lower() != "cookie"
    }
    return {
        "headers": redact(headers_without_cookie),
        "cookies": cookie_metadata,
    }


def authentication_context_is_valid(operation_id: str, target: str, credential_id: str) -> bool:
    """Check an opaque operation-memory context without exposing session material."""

    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    if context is None:
        _LOGGER.info(
            "Authenticated context unavailable credential_id=%s reason=context_missing",
            credential_id,
        )
        return False
    return _validate(context)


def authentication_context_validation_url(operation_id: str, target: str, credential_id: str) -> str:
    """Return the secret-free route used to validate an opaque context."""

    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    return context.validation_url if context is not None else ""


def authentication_attempt_result(operation_id: str, target: str, credential_id: str) -> dict[str, Any] | None:
    """Return the controller-readable, secret-free result of a login attempt."""

    result = _AUTHENTICATION_ATTEMPTS.get(_context_key(operation_id, target, credential_id))
    return dict(result) if result is not None else None


def record_browser_authentication_attempt(
    operation_id: str,
    target: str,
    credential_id: str,
    login_url: str,
) -> dict[str, Any] | None:
    """Classify the latest mapped browser login request without trusting agent prose."""

    from modules.tools.browser import latest_browser_interaction_receipts

    expected = urlsplit(login_url)
    expected_url = f"{expected.scheme}://{expected.netloc}{expected.path}"
    for receipt in reversed(latest_browser_interaction_receipts()):
        if receipt.get("url") != expected_url:
            continue
        status = receipt.get("status")
        if not isinstance(status, int):
            continue
        result = {
            "outcome": "credential_rejected" if status == 401 else "completed",
            "transport": "browser",
            "login_url": expected_url,
            "status": status,
            "evidence_refs": [receipt["evidence_ref"]] if receipt.get("evidence_ref") else [],
        }
        _AUTHENTICATION_ATTEMPTS[_context_key(operation_id, target, credential_id)] = result
        return dict(result)
    return None


def _active_record(
    credential_id: str, *, access_mode: CredentialAccessMode = "checkout"
) -> tuple[Any, dict[str, Any], str]:
    """Resolve an active task's credential under the requested access mode."""

    store = _get_database_store()
    operation_id = _operation_id()
    active_task = active_credential_task(store, operation_id)
    if active_task is None:
        if access_mode == "context":
            raise ValueError(AUTH_CONTEXT_UNAVAILABLE_ERROR)
        raise ValueError("authenticated request requires an active task")
    try:
        resolved_credential_id = _bound_credential_id(active_task, credential_id)
    except ValueError as error:
        if access_mode == "context":
            raise ValueError(AUTH_CONTEXT_UNAVAILABLE_ERROR) from error
        raise
    task, record = _active_credential_for_access(store, operation_id, resolved_credential_id, access_mode)
    return task, record, resolved_credential_id


def _stored_api_form_flow(target: str, flow_id: str) -> dict[str, Any]:
    """Return the controller-selected API-form flow for one exact credential target."""

    candidates = []
    for stored in _get_database_store().list_authentication_flows(
        target, purpose="authentication", statuses=("discovered", "validated")
    ):
        if str(stored.get("flow_id") or "") != flow_id:
            continue
        descriptor = stored.get("descriptor") if isinstance(stored.get("descriptor"), dict) else {}
        if not _is_current_authentication_flow(descriptor):
            continue
        if str(descriptor.get("kind") or "") != "api_form":
            continue
        login_url = str(descriptor.get("login_url") or "").strip()
        validation_url = str(descriptor.get("validation_url") or "").strip()
        if not login_url or not validation_url:
            continue
        if not _credential_target_url_contains(target, login_url, label="stored login_url"):
            continue
        if not _credential_target_url_contains(target, validation_url, label="stored validation_url"):
            continue
        candidates.append((str(stored.get("status") or ""), descriptor))
    if len(candidates) != 1:
        raise ValueError("the selected api_form authentication flow is unavailable for this credential target")
    return candidates[0][1]


def _stored_browser_flow(target: str, flow_id: str) -> dict[str, Any]:
    """Return the controller-selected browser flow for one exact credential target."""

    candidates = []
    for stored in _get_database_store().list_authentication_flows(
        target, purpose="authentication", statuses=("discovered", "validated")
    ):
        if str(stored.get("flow_id") or "") != flow_id:
            continue
        descriptor = stored.get("descriptor") if isinstance(stored.get("descriptor"), dict) else {}
        if not _is_current_authentication_flow(descriptor):
            continue
        if str(descriptor.get("kind") or "") not in {"browser_form", "browser_redirect", "browser_mfa"}:
            continue
        login_url = str(descriptor.get("login_url") or "").strip()
        if not login_url or not _credential_target_url_contains(target, login_url, label="stored login_url"):
            continue
        candidates.append(descriptor)
    if len(candidates) != 1:
        raise ValueError("the selected browser authentication flow is unavailable for this credential target")
    return candidates[0]


def _capture_storage_header_bindings(target: str, flow_id: str) -> list[dict[str, str]]:
    """Resolve canonical browser capture bindings from the selected flow."""

    flow = _stored_browser_flow(target, flow_id)
    return _normalize_storage_header_bindings(
        flow.get("storage_header_bindings") if isinstance(flow.get("storage_header_bindings"), list) else [],
        str(flow.get("authorization_storage_key") or ""),
    )


def _store_context(
    task: Any,
    record: dict[str, Any],
    credential_id: str,
    session: requests.Session,
    headers: dict[str, str],
    params: dict[str, str],
    validation_url: str,
) -> str:
    """Validate and retain sensitive session state without returning it to an agent."""

    target = str(record["target"]).rstrip("/")
    context = _AuthenticationContext(target, credential_id, session, headers, params, validation_url)
    if not session.cookies and not headers and not params:
        raise ValueError("authentication did not establish a session or authorization token")
    for value in headers.values():
        register_runtime_secret(value)
    for cookie in session.cookies:
        register_runtime_secret(cookie.value)
    if not _validate(context):
        raise ValueError("authentication validation failed")
    operation_id = _operation_id()
    _CONTEXTS[_context_key(operation_id, target, credential_id)] = context
    _get_database_store().record_credential_usage(
        operation_id, credential_id, task_uid=task.task_uid, outcome="succeeded"
    )
    return _safe_context(context, "valid")


@tool(
    name="record_authentication_flow",
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "credential_id": {"type": "string"},
                "target": {"type": "string"},
                "kind": {"type": "string", "enum": sorted(AUTHENTICATION_FLOW_KINDS | REGISTRATION_FLOW_KINDS)},
                "login_url": {"type": "string"},
                "validation_url": {"type": "string"},
                "authorization_storage_key": {"type": "string"},
                "storage_header_bindings": {"type": "array", "items": {"type": "object"}},
                "request_format": {"type": "string", "enum": ["", "json", "form"]},
                "allowed_origins": {"type": "array", "items": {"type": "string"}},
                "purpose": {"type": "string", "enum": ["authentication", "registration"]},
                "roles": {"type": "array", "items": {"type": "string"}},
                "success_redirect_url": {"type": "string"},
                "identity_fields": {"type": "array", "items": {"type": "string"}},
                "required_fields": {"type": "array", "items": {"type": "string"}},
                "optional_fields": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        }
    },
)
def record_authentication_flow(
    credential_id: str = "",
    target: str = "",
    kind: str = "",
    login_url: str = "",
    validation_url: str = "",
    authorization_storage_key: str = "",
    storage_header_bindings: list[dict[str, str]] | None = None,
    request_format: str = "",
    allowed_origins: list[str] | None = None,
    purpose: str = "authentication",
    roles: list[str] | None = None,
    success_redirect_url: str = "",
    identity_fields: list[str] | None = None,
    required_fields: list[str] | None = None,
    optional_fields: list[str] | None = None,
) -> str:
    """Persist a secret-free observed authentication or registration flow for later same-target setup."""

    purpose = _normalize_authentication_enum(purpose, field_name="purpose")
    kind = _normalize_authentication_enum(kind, field_name="kind")
    request_format = _normalize_authentication_enum(request_format, field_name="request_format")
    if purpose == "authentication":
        if credential_id:
            _task, record, credential_id = _active_record(credential_id)
            target = str(record["target"]).rstrip("/")
        else:
            store = _get_database_store()
            _task, target_values = _active_task_target_values(store, _operation_id())
            normalized_target = str(target).rstrip("/")
            if normalized_target not in {value.rstrip("/") for value in target_values}:
                raise ValueError("authentication flow recording requires an active task target")
            target = normalized_target
    else:
        store = _get_database_store()
        _task, target_values = _active_task_target_values(store, _operation_id())
        if credential_id:
            raise ValueError("registration flow recording does not accept a credential_id")
        if len(target_values) != 1:
            raise ValueError("registration flow recording requires one active task target")
        target = next(iter(target_values)).rstrip("/")
        credential_id = ""
    allowed_kinds = AUTHENTICATION_FLOW_KINDS if purpose == "authentication" else REGISTRATION_FLOW_KINDS
    if kind not in allowed_kinds:
        raise ValueError("unknown flow kind")
    effective_login_url = login_url or (validation_url if kind in {"api_key", "oauth2_client"} else "")
    effective_login_url = _safe_flow_descriptor_url(target, effective_login_url, label="login_url")
    if purpose == "authentication":
        if validation_url:
            validation_url = _safe_flow_descriptor_url(target, validation_url, label="validation_url")
        elif kind not in {"browser_form", "browser_redirect", "browser_mfa"}:
            raise ValueError("validation_url is required for non-browser authentication flows")
        authorization_storage_key = _safe_authorization_storage_key(authorization_storage_key)
        storage_header_bindings = _normalize_storage_header_bindings(
            storage_header_bindings, authorization_storage_key
        )
    elif validation_url:
        raise ValueError("registration flow recording does not accept validation_url")
    elif authorization_storage_key:
        raise ValueError("registration flow recording does not accept an authorization storage key")
    elif storage_header_bindings:
        raise ValueError("registration flow recording does not accept storage header bindings")
    if purpose == "authentication" and success_redirect_url:
        raise ValueError("authentication flow recording does not accept success_redirect_url")
    if purpose == "registration" and success_redirect_url:
        success_redirect_url = _safe_flow_descriptor_url(
            target, success_redirect_url, label="success_redirect_url"
        )
    normalized_origins = []
    for origin in allowed_origins or []:
        parsed = urlsplit(str(origin))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("allowed_origins must contain HTTP(S) origins")
        normalized_origins.append(f"{parsed.scheme}://{parsed.netloc}")
    descriptor = {
        "target": target,
        "purpose": purpose,
        "kind": kind,
        "flow_version": AUTHENTICATION_FLOW_VERSION,
        "login_url": effective_login_url,
        "validation_url": validation_url if purpose == "authentication" else "",
        "authorization_storage_key": authorization_storage_key if purpose == "authentication" else "",
        "storage_header_bindings": storage_header_bindings if purpose == "authentication" else [],
        "url": effective_login_url if purpose == "registration" else "",
        "request_format": request_format,
        "allowed_origins": list(dict.fromkeys([f"{urlsplit(target).scheme}://{urlsplit(target).netloc}", *normalized_origins])),
        "evidence_refs": [],
    }
    if purpose == "authentication":
        descriptor["provenance"] = "observed_flow_discovery"
    if purpose == "registration":
        descriptor["roles"] = sorted({str(role).strip() for role in roles or ["user"] if str(role).strip()})
        descriptor["success_redirect_url"] = success_redirect_url
        for key, values in {
            "identity_fields": identity_fields,
            "required_fields": required_fields,
            "optional_fields": optional_fields,
        }.items():
            normalized_fields = sorted({str(value).strip() for value in values or [] if str(value).strip()})
            if normalized_fields:
                descriptor[key] = normalized_fields
    stored = _get_database_store().upsert_authentication_flow(_operation_id(), descriptor)
    return json.dumps({"recorded": True, "credential_id": credential_id, "flow": stored}, sort_keys=True)


def build_record_authentication_flow_tool(
    *,
    target: str,
    purpose: str,
    kind: str,
    allowed_origins: Iterable[str] = (),
    credential_id: str = "",
    roles: Iterable[str] = (),
    name: str = "record_authentication_flow",
) -> Any:
    """Build a controller-bound recorder that exposes only observed flow fields to an agent."""

    normalized_target = str(target).rstrip("/")
    normalized_purpose = _normalize_authentication_enum(str(purpose).strip(), field_name="purpose")
    normalized_kind = _normalize_authentication_enum(str(kind).strip(), field_name="kind")
    normalized_credential_id = str(credential_id).strip()
    normalized_origins = tuple(
        str(origin).strip() for origin in allowed_origins if str(origin).strip()
    )
    normalized_roles = tuple(str(role).strip() for role in roles if str(role).strip())
    allowed_kinds = AUTHENTICATION_FLOW_KINDS if normalized_purpose == "authentication" else REGISTRATION_FLOW_KINDS
    if normalized_purpose not in {"authentication", "registration"}:
        raise ValueError("purpose must be authentication or registration")
    if normalized_kind not in allowed_kinds:
        raise ValueError("bound authentication flow kind is incompatible with its purpose")
    if normalized_purpose == "registration" and normalized_credential_id:
        raise ValueError("registration flow recorder cannot bind a credential_id")
    if not normalized_target:
        raise ValueError("target required when binding authentication flow recorder")
    if not str(name).strip():
        raise ValueError("name required when binding authentication flow recorder")

    def record_bound_authentication_flow(
        login_url: str,
        validation_url: str = "",
        authorization_storage_key: str = "",
        storage_header_bindings: list[dict[str, str]] | None = None,
        request_format: str = "",
        success_redirect_url: str = "",
        identity_fields: list[str] | None = None,
        required_fields: list[str] | None = None,
        optional_fields: list[str] | None = None,
    ) -> str:
        """Persist one observed flow using controller-bound authentication context."""

        return record_authentication_flow(
            credential_id=normalized_credential_id,
            target=normalized_target,
            kind=normalized_kind,
            login_url=login_url,
            validation_url=validation_url,
            authorization_storage_key=authorization_storage_key,
            storage_header_bindings=storage_header_bindings,
            request_format=request_format,
            allowed_origins=list(normalized_origins),
            purpose=normalized_purpose,
            roles=list(normalized_roles),
            success_redirect_url=success_redirect_url,
            identity_fields=identity_fields,
            required_fields=required_fields,
            optional_fields=optional_fields,
        )

    if normalized_purpose == "authentication":
        required = ["login_url"] if normalized_kind in {"browser_form", "browser_redirect", "browser_mfa"} else [
            "login_url", "validation_url"
        ]
        properties = {
            "login_url": {"type": "string", "description": "Observed same-target login URL."},
            "validation_url": {"type": "string", "description": "Observed same-target protected validation URL."},
            "authorization_storage_key": {
                "type": "string",
                "description": "Legacy observed bearer-token storage key; never a token value.",
            },
            "storage_header_bindings": {
                "type": "array",
                "description": "Observed browser-storage-to-header mappings; never include header values.",
                "items": {
                    "type": "object",
                    "properties": {
                        "header_name": {"type": "string"},
                        "storage_key": {"type": "string"},
                        "value_template": {"type": "string"},
                    },
                    "required": ["header_name", "storage_key", "value_template"],
                    "additionalProperties": False,
                },
            },
            "request_format": {"type": "string", "enum": ["", "json", "form"]},
        }
    else:
        required = ["login_url"]
        properties = {
            "login_url": {"type": "string", "description": "Observed same-target registration URL."},
            "request_format": {"type": "string", "enum": ["", "json", "form"]},
            "success_redirect_url": {"type": "string", "description": "Observed same-target success redirect URL."},
            "identity_fields": {"type": "array", "items": {"type": "string"}},
            "required_fields": {"type": "array", "items": {"type": "string"}},
            "optional_fields": {"type": "array", "items": {"type": "string"}},
        }
    record_bound_authentication_flow.__name__ = str(name).strip()
    record_bound_authentication_flow.__doc__ = (
        "Persist one observed authentication flow. The controller bound target="
        f"{normalized_target}, purpose={normalized_purpose}, and kind={normalized_kind}. "
        "Supply only the observed fields in this tool schema."
    )
    return tool(
        record_bound_authentication_flow,
        name=str(name).strip(),
        inputSchema={
            "json": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
        },
    )


@tool(
    name="ensure_authenticated_context",
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "credential_id": {"type": "string"},
                "flow_id": {"type": "string"},
                "login_url": {"type": "string"},
                "validation_url": {"type": "string"},
                "request_format": {"type": "string", "enum": ["", "json", "form"]},
                "additional_fields": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "required": ["credential_id"],
            "additionalProperties": False,
        }
    },
)
def ensure_authenticated_context(
    credential_id: str,
    flow_id: str = "",
    login_url: str = "",
    validation_url: str = "",
    request_format: str = "",
    additional_fields: dict[str, str] | None = None,
) -> str:
    """Ensure one checked-out credential has an operation-local authenticated HTTP context.

    Existing contexts are validated before reuse. When absent or invalid, this controller-owned adapter performs the
    mapped same-origin username/password login. It retains cookies and tokens only in memory and never returns them.
    """

    request_format = _normalize_authentication_enum(request_format, field_name="request_format")
    operation_id = _operation_id()
    task, record, credential_id = _active_record(credential_id)
    target = str(record["target"]).rstrip("/")
    key = _context_key(operation_id, target, credential_id)
    existing = _CONTEXTS.get(key)
    if existing and authentication_context_is_valid(operation_id, target, credential_id):
        return _safe_context(existing, "valid")
    _CONTEXTS.pop(key, None)

    if record["credential_type"] != "username_password":
        raise ValueError("authentication context login currently requires a username_password credential")
    if not login_url or not validation_url:
        if not flow_id:
            raise ValueError("authentication context login requires a controller-selected flow_id")
        flow = _stored_api_form_flow(target, flow_id)
        login_url = login_url or str(flow["login_url"])
        validation_url = validation_url or str(flow["validation_url"])
        request_format = request_format or str(flow.get("request_format") or "json")
    request_format = request_format or "json"
    if not _credential_target_url_contains(target, login_url, label="login_url") or not _credential_target_url_contains(
        target, validation_url, label="validation_url"
    ):
        raise ValueError("authentication URLs must share the credential target origin")
    if request_format not in {"json", "form"}:
        raise ValueError("request_format must be json or form")

    payload = record["payload"]
    credentials = {"username": payload["username"], "password": payload["password"]}
    if additional_fields:
        credentials.update({str(name): str(value) for name, value in additional_fields.items()})
    session = requests.Session()
    try:
        if request_format == "json":
            response = session.post(login_url, json=credentials, timeout=15)
        else:
            response = session.post(login_url, data=credentials, timeout=15)
    except requests.RequestException as error:
        _AUTHENTICATION_ATTEMPTS[key] = {
            "outcome": "request_failed",
            "transport": "api",
            "login_url": login_url,
            "evidence_refs": [],
        }
        raise ValueError("authentication request failed") from error
    _AUTHENTICATION_ATTEMPTS[key] = {
        "outcome": "credential_rejected" if response.status_code == 401 else "completed",
        "transport": "api",
        "login_url": login_url,
        "status": response.status_code,
        "evidence_refs": [],
    }
    if response.status_code >= 400:
        raise ValueError("authentication was rejected")

    headers: dict[str, str] = {}
    authorization = response.headers.get("Authorization")
    if authorization:
        headers["Authorization"] = authorization
    else:
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = {}
        if isinstance(response_body, dict) and isinstance(response_body.get("access_token"), str):
            headers["Authorization"] = f"{response_body.get('token_type') or 'Bearer'} {response_body['access_token']}"
    return _store_context(task, record, credential_id, session, headers, {}, validation_url)


@tool(
    name="capture_browser_authenticated_context",
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "credential_id": {"type": "string"},
                "flow_id": {"type": "string"},
                "validation_url": {"type": "string"},
            },
            "required": ["credential_id", "flow_id", "validation_url"],
            "additionalProperties": False,
        }
    },
)
async def capture_browser_authenticated_context(
    credential_id: str,
    flow_id: str,
    validation_url: str,
) -> str:
    """Capture an authentication agent's browser session into an opaque HTTP context.

    This tool is intentionally available only to the authentication agent. The required flow ID selects the
    controller-recorded browser flow; unauthenticated tasks never invoke this tool. It copies same-origin cookies plus
    the flow's declared browser-storage-derived headers into operation memory. Observed same-origin request headers
    take precedence for matching declared names. Neither values nor cookies are returned to the agent or persisted to
    workflow state.
    """

    from modules.tools.browser import get_browser

    task, record, credential_id = _active_record(credential_id)
    target = str(record["target"]).rstrip("/")
    if not _credential_target_url_contains(target, validation_url, label="validation_url"):
        raise ValueError("validation URL must share the credential target origin")
    bindings = _capture_storage_header_bindings(target, str(flow_id or "").strip())
    session = requests.Session()
    headers: dict[str, str] = {}
    async with get_browser() as browser:
        async def capture() -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
            async with browser.timeout():
                cookies = await browser.context.cookies([target])
                observed_headers: dict[str, str] = {}
                wanted_headers = {str(binding["header_name"]).lower() for binding in bindings}
                matching_request_found = False
                for request in reversed(tuple(getattr(browser, "recent_requests", ()))):
                    if not _credential_target_url_contains(target, str(request.url), label="browser request URL"):
                        continue
                    if str(request.url).split("#", 1)[0] != validation_url:
                        continue
                    request_headers = await request.all_headers()
                    normalized_headers = {
                        str(name).lower(): str(value).strip()
                        for name, value in request_headers.items()
                        if str(value).strip()
                    }
                    if wanted_headers and not wanted_headers.issubset(normalized_headers):
                        continue
                    if not wanted_headers:
                        request_cookies = SimpleCookie()
                        request_cookies.load(normalized_headers.get("cookie", ""))
                        if not any(
                            cookie.get("name") in request_cookies
                            and request_cookies[cookie["name"]].value == cookie.get("value")
                            for cookie in cookies
                        ):
                            continue
                    response = await request.response() if hasattr(request, "response") else None
                    status_code = getattr(response, "status", None)
                    if not isinstance(status_code, int) or not 200 <= status_code < 300:
                        continue
                    matching_request_found = True
                    observed_headers = {
                        name: normalized_headers[name]
                        for name in wanted_headers
                        if name in normalized_headers
                    }
                    break
                if not matching_request_found:
                    raise ValueError(
                        "validation URL did not produce a successful browser request with the required credentials"
                    )
                storage_values: dict[str, str] = {}
                storage_keys = sorted({binding["storage_key"] for binding in bindings})
                if storage_keys:
                    storage_values = await browser.page.evaluate(
                        """(keys) => Object.fromEntries(keys.map((key) => [
                            key, localStorage.getItem(key) || sessionStorage.getItem(key) || ''
                        ]))""",
                        storage_keys,
                    )
                    if not isinstance(storage_values, dict):
                        raise ValueError("browser storage lookup returned an invalid result")
                return cookies, observed_headers, storage_values

        cookies, observed_headers, storage_values = await browser.run_in_browser_loop(capture)
    for cookie in cookies:
        session.cookies.set(
            str(cookie["name"]),
            str(cookie["value"]),
            domain=str(cookie.get("domain") or ""),
            path=str(cookie.get("path") or "/"),
        )
    for binding in bindings:
        header_name = binding["header_name"]
        observed_value = observed_headers.get(header_name.lower(), "")
        if observed_value:
            headers[header_name] = observed_value
            continue
        storage_value = str(storage_values.get(binding["storage_key"], "")).strip()
        if not storage_value:
            raise ValueError(f"browser storage key for {header_name} was not available after authentication")
        headers[header_name] = binding["value_template"].replace("{value}", storage_value)
    if "Authorization" not in headers and observed_headers.get("authorization"):
        headers["Authorization"] = observed_headers["authorization"]
    return _store_context(task, record, credential_id, session, headers, {}, validation_url)


@tool(name="establish_credential_authenticated_context")
def establish_credential_authenticated_context(credential_id: str = "", validation_url: str = "") -> str:
    """Establish an opaque API-key or OAuth2-client authentication context without returning request material."""

    task, record, credential_id = _active_record(credential_id)
    target = str(record["target"]).rstrip("/")
    if not validation_url or not _credential_target_url_contains(target, validation_url, label="validation_url"):
        raise ValueError("a mapped same-origin validation URL is required")
    session = requests.Session()
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    payload = record["payload"]
    if record["credential_type"] == "api_key":
        value = f"{payload.get('prefix') or ''}{payload['api_key']}"
        if payload.get("placement", "header") == "header":
            headers[str(payload["name"])] = value
        else:
            params[str(payload["name"])] = value
    elif record["credential_type"] == "oauth2_client":
        token_url = str(payload.get("token_url") or "").strip()
        if not token_url or not _credential_target_url_contains(target, token_url, label="token_url"):
            raise ValueError("OAuth token URL must share the credential target origin")
        form_values = {"grant_type": "client_credentials"}
        if payload.get("scopes"):
            form_values["scope"] = " ".join(str(scope) for scope in payload["scopes"])
        if payload.get("audience"):
            form_values["audience"] = str(payload["audience"])
        request_headers = {"Accept": "application/json"}
        if payload["client_auth_method"] == "client_secret_basic":
            basic = b64encode(f"{payload['client_id']}:{payload['client_secret']}".encode()).decode()
            request_headers["Authorization"] = f"Basic {basic}"
        else:
            form_values.update({"client_id": payload["client_id"], "client_secret": payload["client_secret"]})
        try:
            response = session.post(token_url, data=form_values, headers=request_headers, timeout=15)
            response.raise_for_status()
            token_payload: Any = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ValueError("OAuth token exchange failed") from error
        token = token_payload.get("access_token") if isinstance(token_payload, dict) else ""
        if not isinstance(token, str) or not token:
            raise ValueError("OAuth token response did not contain an access token")
        headers["Authorization"] = f"{token_payload.get('token_type') or 'Bearer'} {token}"
    else:
        raise ValueError("credential context requires an api_key or oauth2_client credential")
    return _store_context(task, record, credential_id, session, headers, params, validation_url)


@tool(name="authenticated_http_request")
def authenticated_http_request(
    method: str,
    url: str,
    credential_id: str = "",
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """Make one request through a validated operation-local authenticated context.

    Cookie and Authorization headers are controller-owned and cannot be supplied by the task.
    """

    normalized_headers = dict(headers or {})
    if any(name.lower() in {"cookie", "authorization"} for name in normalized_headers):
        raise ValueError("Cookie and Authorization headers are managed by the authenticated context")
    store = _get_database_store()
    operation_id = _operation_id()
    task, record, credential_id = _active_record(credential_id, access_mode="context")
    target = str(record["target"]).rstrip("/")
    if not _credential_target_url_contains(target, url, label="request URL"):
        raise ValueError("request URL is outside the credential target origin")
    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    if context is None or not _validate(context):
        raise ValueError(AUTH_CONTEXT_UNAVAILABLE_ERROR)
    merged_headers = {**context.headers, **normalized_headers}
    try:
        response = context.session.request(
            method.upper(), url, data=body, headers=merged_headers, params=context.params, timeout=20
        )
    except requests.RequestException as error:
        raise ValueError("authenticated request failed") from error
    store.record_credential_usage(operation_id, credential_id, task_uid=task.task_uid, outcome="succeeded")
    return json.dumps(redact({"status_code": response.status_code, "headers": dict(response.headers), "body": response.text}))
