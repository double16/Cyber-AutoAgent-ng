"""Operation-local, secret-safe authenticated HTTP contexts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from base64 import b64encode, urlsafe_b64decode
from binascii import Error as BinasciiError
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

import requests
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from strands import tool

from modules.tools.credentials import (
    AUTH_CONTEXT_UNAVAILABLE_ERROR,
    CredentialAccessMode,
    _active_credential_for_access,
    _active_task_target_values,
    _register_credential_payload_secrets,
    _task_resolved_targets,
)
from modules.tools.memory import _get_database_store, _operation_id, _operation_output_root, active_credential_task
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
    flow_validation_url: str | None = None
    validation_marker: str = ""
    validation_proof: dict[str, Any] = field(default_factory=dict)
    validation_evidence_ref: str = ""


_CONTEXTS: dict[tuple[str, str, str], _AuthenticationContext] = {}
_AUTHENTICATION_ATTEMPTS: dict[tuple[str, str, str], dict[str, Any]] = {}

AUTHENTICATION_FLOW_KINDS = frozenset({
    "browser_form",
    "browser_redirect",
    "browser_mfa",
    "api_key",
    "oauth2_client",
})
REGISTRATION_FLOW_KINDS = frozenset({"browser_registration", "api_registration"})
AUTHENTICATION_FLOW_VERSION = 10
REGISTRATION_FLOW_VERSION = 4
_AUTHORIZATION_STORAGE_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{2,4096}\.[A-Za-z0-9_-]{2,16384}\.[A-Za-z0-9_-]{0,8192})"
    r"(?![A-Za-z0-9_-])"
)
_AUTH_EXPIRY_WINDOW_SECONDS = 600
_BROWSER_VALIDATION_CANDIDATE_LIMIT = 8
AUTH_CONTEXT_REFRESH_REQUIRED_ERROR = (
    "AUTH_CONTEXT_REFRESH_REQUIRED: authenticated context is stale; request controller refresh for this "
    "credential before retrying the request"
)
_LOGGER = logging.getLogger(__name__)
_PURPOSE_ADAPTER = TypeAdapter(Literal["authentication", "registration"])
_FLOW_KIND_ADAPTER = TypeAdapter(
    Literal[
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
            "login_form": "browser_form",
            "form_login": "browser_form",
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


def _scoped_request(
    session: requests.Session, target: str, method: str, url: str,
    *, follow_redirects: bool = True, **kwargs: Any,
) -> requests.Response:
    """Apply the credential target boundary before every network hop."""

    if not _credential_target_url_contains(target, url, label="request URL"):
        raise ValueError("authenticated request is outside the credential target boundary")
    for _ in range(6):
        if method.upper() == "GET":
            response = session.get(url, allow_redirects=False, **kwargs)
        elif method.upper() == "POST":
            response = session.post(url, allow_redirects=False, **kwargs)
        else:
            response = session.request(method, url, allow_redirects=False, **kwargs)
        if not follow_redirects or response.status_code not in {301, 302, 303, 307, 308} or not response.headers.get("Location"):
            return response
        next_url = urljoin(url, response.headers["Location"])
        if not _credential_target_url_contains(target, next_url, label="redirect URL"):
            raise ValueError("authenticated redirect is outside the credential target boundary")
        if response.status_code == 303 or (response.status_code in {301, 302} and method.upper() == "POST"):
            method = "GET"
            kwargs.pop("data", None)
            kwargs.pop("json", None)
        url = next_url
    raise ValueError("authenticated redirect limit reached")


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


def _normalize_request_header_names(names: list[str] | None) -> list[str]:
    """Keep only explicit, valid, distinct credential header names."""

    if names is None or names == "":
        return []
    if not isinstance(names, list):
        raise ValueError("request_header_names must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not _HEADER_NAME_PATTERN.fullmatch(name):
            raise ValueError("request_header_names must contain valid HTTP header names")
        normalized = name.lower()
        if normalized in seen or normalized == "cookie":
            raise ValueError("request_header_names must be distinct and cannot contain Cookie")
        seen.add(normalized)
        result.append(name)
    return result


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
            "validation_url": context.flow_validation_url or "",
            "credential_header_names": sorted(context.headers),
            "evidence_refs": [context.validation_evidence_ref] if context.validation_evidence_ref else [],
        },
        sort_keys=True,
    )


def _validate(context: _AuthenticationContext) -> bool:
    """Probe the protected route regardless of the remaining material lifetime."""

    started_at = time.perf_counter()
    try:
        response = _scoped_request(
            context.session, context.target, "GET", context.validation_url,
            headers=context.headers, params=context.params, timeout=15,
        )
    except (requests.RequestException, ValueError) as error:
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


def _jwt_expirations(value: str) -> Iterable[float]:
    """Yield readable JWT expiration claims without treating the token as verified."""

    for match in _JWT_PATTERN.finditer(unquote(str(value))):
        header_segment, payload_segment, _signature_segment = match.group(1).split(".")
        try:
            header = json.loads(urlsafe_b64decode(header_segment + "=" * (-len(header_segment) % 4)))
            payload = json.loads(urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4)))
        except (BinasciiError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(header, dict) or not isinstance(header.get("alg"), str) or not isinstance(payload, dict):
            continue
        expiration = _finite_timestamp(payload.get("exp"))
        if expiration is not None:
            yield expiration


def _finite_timestamp(value: Any) -> float | None:
    """Accept finite numeric times without allowing booleans or oversized integers."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _cookie_applies_to_url(cookie: Any, url: str) -> bool:
    """Check domain, path, and secure scope even if the cookie is already expired."""

    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    domain = str(cookie.domain or "").lstrip(".").lower()
    if domain and host != domain and not (cookie.domain_initial_dot and host.endswith(f".{domain}")):
        return False
    if cookie.secure and parsed.scheme.lower() != "https":
        return False
    path = str(cookie.path or "/")
    request_path = parsed.path or "/"
    return request_path == path or (
        request_path.startswith(path) and (path.endswith("/") or request_path[len(path):].startswith("/"))
    )


def _expiring_authentication_material(
    context: _AuthenticationContext, now: float, *, expired_only: bool = False
) -> tuple[str, str, float] | None:
    """Find known JWT or cookie expiry inside the task window or already expired."""

    def matches(remaining: float) -> bool:
        return remaining <= 0 if expired_only else remaining < _AUTH_EXPIRY_WINDOW_SECONDS

    for source, values in (
        ("header", {**getattr(context.session, "headers", {}), **context.headers}),
        ("query", context.params),
    ):
        for name, value in values.items():
            for expiration in _jwt_expirations(str(value)):
                remaining = expiration - now
                if matches(remaining):
                    return "jwt", f"{source}:{name}", remaining
    for cookie in getattr(context.session, "cookies", ()):
        if not _cookie_applies_to_url(cookie, context.validation_url):
            continue
        for expiration in _jwt_expirations(cookie.value):
            remaining = expiration - now
            if matches(remaining):
                return "jwt", f"cookie:{cookie.name}", remaining
        cookie_expiry = _finite_timestamp(cookie.expires)
        if cookie_expiry is not None:
            remaining = cookie_expiry - now
            if matches(remaining):
                return "cookie", f"cookie:{cookie.name}", remaining
    return None


def _set_cookie_expiration(morsel: Any, issued_at: float | None) -> float | None:
    """Resolve RFC cookie lifetime, preferring Max-Age to Expires."""

    max_age = str(morsel["max-age"] or "").strip()
    if max_age:
        try:
            seconds = int(max_age)
        except ValueError:
            pass
        else:
            if issued_at is None:
                return None
            try:
                return _finite_timestamp(issued_at + seconds)
            except OverflowError:
                return None
    expires = str(morsel["expires"] or "").strip()
    if expires:
        try:
            parsed = parsedate_to_datetime(expires)
            return parsed.replace(tzinfo=UTC).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def _matches_captured_cookie(captured: dict[str, Any], morsel: Any, request_url: str) -> bool:
    """Bind a Set-Cookie observation to the current cookie value and scope."""

    if str(captured.get("name")) != morsel.key or str(captured.get("value")) != morsel.value:
        return False
    parsed = urlsplit(request_url)
    domain = str(morsel["domain"] or parsed.hostname or "").lstrip(".").lower()
    captured_domain = str(captured.get("domain") or "").lstrip(".").lower()
    if domain != captured_domain:
        return False
    request_path = parsed.path or "/"
    default_path = request_path.rpartition("/")[0] or "/"
    path = str(morsel["path"] or default_path)
    return path == str(captured.get("path") or "/")


async def _observed_cookie_expirations(
    browser: Any,
    target: str,
    cookies: list[dict[str, Any]],
) -> dict[tuple[str, str, str], float | None]:
    """Use current-session HAR source responses to recover matching cookie lifetimes."""

    observations: dict[tuple[str, str, str], float | None] = {}
    for request in tuple(getattr(browser, "recent_requests", ())):
        request_url = str(getattr(request, "url", ""))
        try:
            in_scope = _credential_target_url_contains(target, request_url, label="browser request URL")
        except ValueError:
            continue
        if not in_scope:
            continue
        try:
            response = await asyncio.wait_for(request.response(), timeout=0.25)
            if response is None:
                continue
            header_values = getattr(response, "header_values", None)
            if callable(header_values):
                set_cookie_lines = await asyncio.wait_for(header_values("set-cookie"), timeout=0.25)
            else:
                all_headers = getattr(response, "all_headers", None)
                response_headers = await asyncio.wait_for(all_headers(), timeout=0.25) if callable(all_headers) else {}
                set_cookie_lines = [
                    value for name, value in response_headers.items() if str(name).lower() == "set-cookie"
                ]
        except Exception:
            continue
        timing = getattr(request, "timing", {})
        started_ms = timing.get("startTime") if isinstance(timing, dict) else None
        finite_start = _finite_timestamp(started_ms)
        issued_at = finite_start / 1000 if finite_start is not None else None
        for header_value in set_cookie_lines or ():
            for line in str(header_value).splitlines():
                parsed_cookies = SimpleCookie()
                parsed_cookies.load(line)
                for morsel in parsed_cookies.values():
                    for cookie in cookies:
                        if _matches_captured_cookie(cookie, morsel, request_url):
                            key = (
                                str(cookie["name"]),
                                str(cookie.get("domain") or ""),
                                str(cookie.get("path") or "/"),
                            )
                            observations[key] = _set_cookie_expiration(morsel, issued_at)
    return observations


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
    """Check whether a validated context has enough lifetime to start a task."""

    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    if context is None:
        _LOGGER.info(
            "Authenticated context unavailable credential_id=%s reason=context_missing",
            credential_id,
        )
        return False
    validated = _validate(context)
    expiring_material = _expiring_authentication_material(context, time.time(), expired_only=False)
    if expiring_material is not None:
        kind, source, remaining_seconds = expiring_material
        _LOGGER.info(
            "Authenticated context not ready for task credential_id=%s reason=auth_material_expiring "
            "material_kind=%s material_source=%s remaining_seconds=%.1f",
            credential_id, kind, redact(source), remaining_seconds,
        )
        return False
    return validated and _prove_authenticated_context(context)


def authentication_context_validation_url(operation_id: str, target: str, credential_id: str) -> str:
    """Return the secret-free route used to validate an opaque context."""

    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    if context is None:
        return ""
    if context.flow_validation_url is not None:
        return context.flow_validation_url
    parsed = urlsplit(context.validation_url)
    return context.validation_url if not parsed.query and not parsed.fragment else ""


def authentication_attempt_result(operation_id: str, target: str, credential_id: str) -> dict[str, Any] | None:
    """Return the controller-readable, secret-free result of a login attempt."""

    result = _AUTHENTICATION_ATTEMPTS.get(_context_key(operation_id, target, credential_id))
    return {key: value for key, value in result.items() if key != "capture_failure"} if result is not None else None


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
        _AUTHENTICATION_ATTEMPTS.setdefault(_context_key(operation_id, target, credential_id), {}).update(result)
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


def resolve_worker_credential(
    operation_id: str, task_uid: str, target: str, credential_id: str,
) -> tuple[Any, dict[str, Any], str]:
    """Resolve authorized worker material without granting executor checkout access."""
    _bound_flow_task_target_values(task_uid, operation_id, target, "authentication")
    store = _get_database_store()
    if credential_id not in store.credential_ids_authorized_for_worker(operation_id, task_uid):
        raise ValueError("Authentication worker credential is not authorized for the assigned task")
    record = store.get_credential(credential_id, include_payload=True)
    if record is None or record["status"] not in {"unknown", "valid"}:
        raise ValueError("Eligible credential is required for authentication worker")
    if record.get("operation_id") and record["operation_id"] != operation_id:
        raise ValueError("Authentication worker credential is scoped to another operation")
    if record["target"] != target or record["credential_type"] == "email_login":
        raise ValueError("Authentication worker credential does not match its assigned target")
    task = next(item for item in store.get_tasks(operation_id) if item.task_uid == task_uid)
    _register_credential_payload_secrets(record)
    return task, record, credential_id


def _store_context(
    task: Any,
    record: dict[str, Any],
    credential_id: str,
    session: requests.Session,
    headers: dict[str, str],
    params: dict[str, str],
    validation_url: str,
    validation_marker: str = "",
) -> str:
    """Validate and retain sensitive session state without returning it to an agent."""

    target = str(record["target"]).rstrip("/")
    context = _AuthenticationContext(
        target, credential_id, session, headers, params, validation_url,
        validation_marker=validation_marker,
    )
    if not session.cookies and not headers and not params:
        raise ValueError("authentication did not establish a session or authorization token")
    for value in headers.values():
        register_runtime_secret(value)
    for value in params.values():
        register_runtime_secret(value)
    for cookie in session.cookies:
        register_runtime_secret(cookie.value)
    if not _validate(context):
        raise ValueError("authentication validation failed")
    if not _prove_authenticated_context(context):
        raise ValueError("authenticated response was not distinguished from the anonymous response")
    parsed = urlsplit(validation_url)
    descriptor_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    replay = replace(context, validation_url=descriptor_url)
    context.flow_validation_url = (
        descriptor_url if descriptor_url == validation_url or _prove_authenticated_context(replay) else ""
    )
    operation_id = _operation_id()
    if context.validation_proof:
        proof_path = Path(_operation_output_root()) / "artifacts" / f"authentication_validation_{time.time_ns()}.json"
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        proof = {
            **context.validation_proof,
            "validation_url": descriptor_url,
            "reusable_validation_url": context.flow_validation_url,
            "credential_header_names": sorted(headers),
            "cookie_names": sorted(cookie.name for cookie in session.cookies),
        }
        proof_path.write_text(
            json.dumps({key: redact(value) for key, value in proof.items()}, sort_keys=True), encoding="utf-8",
        )
        context.validation_evidence_ref = f"artifact:{proof_path}"
    _CONTEXTS[_context_key(operation_id, target, credential_id)] = context
    _get_database_store().record_credential_usage(
        operation_id, credential_id, task_uid=task.task_uid, outcome="succeeded"
    )
    return _safe_context(context, "valid")


def _prove_authenticated_context(context: _AuthenticationContext) -> bool:
    """Require an observable difference attributable to the credential at the validation URL."""

    try:
        anonymous = _scoped_request(requests.Session(), context.target, "GET", context.validation_url, timeout=15)
        authenticated = _scoped_request(
            context.session, context.target, "GET", context.validation_url,
            headers=context.headers, params=context.params, timeout=15,
        )
    except (requests.RequestException, ValueError):
        return False
    if not 200 <= authenticated.status_code < 300:
        return False
    context.validation_proof = {
        "anonymous_status": anonymous.status_code,
        "authenticated_status": authenticated.status_code,
    }
    if anonymous.status_code in {401, 403}:
        context.validation_proof["proof_method"] = "anonymous_access_denied"
        return True
    anonymous_url = str(getattr(anonymous, "url", "") or "")
    authenticated_url = str(getattr(authenticated, "url", "") or "")
    if anonymous_url and authenticated_url and anonymous_url != authenticated_url:
        supported = _credential_target_url_contains(context.target, authenticated_url, label="authenticated response URL")
        if supported:
            context.validation_proof["proof_method"] = "different_response_destination"
        return supported
    marker = context.validation_marker
    supported = bool(marker and marker in authenticated.text and marker not in anonymous.text)
    if supported:
        context.validation_proof["proof_method"] = "authenticated_only_marker"
    return supported


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
                "request_header_names": {"type": "array", "items": {"type": "string"}},
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
    request_header_names: list[str] | None = None,
    request_format: str = "",
    allowed_origins: list[str] | None = None,
    purpose: str = "authentication",
    roles: list[str] | None = None,
    success_redirect_url: str = "",
    identity_fields: list[str] | None = None,
    required_fields: list[str] | None = None,
    optional_fields: list[str] | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> str:
    """Persist a secret-free observed authentication or registration flow for later same-target setup."""

    return _record_authentication_flow(**locals())


def _bound_flow_task_target_values(task_uid: str, operation_id: str, target: str, purpose: str) -> set[str]:
    """Validate controller-owned worker identity independently of other active tasks."""

    diagnostic = "Flow worker controller task context is invalid; changing tool arguments cannot repair it"
    if operation_id != _operation_id():
        raise ValueError(f"{diagnostic}: operation mismatch")
    store = _get_database_store()
    task = next((item for item in store.get_tasks(operation_id) if item.task_uid == task_uid), None)
    if task is None or task.status != "active":
        raise ValueError(f"{diagnostic}: assigned task is missing or inactive")
    targets = set(_task_resolved_targets(store, operation_id, task))
    if target.rstrip("/") not in {value.rstrip("/") for value in targets}:
        raise ValueError(f"{diagnostic}: target is outside assigned task scope")
    if purpose == "registration" and len(targets) != 1:
        raise ValueError(f"{diagnostic}: registration requires one assigned task target")
    return targets


class FlowStep(BaseModel):
    """Secret-free metadata for one observed step, never replay inputs."""

    model_config = ConfigDict(extra="forbid")
    url: str
    action: Literal["navigation", "form_submission", "redirect", "verification"]
    field_names: list[str] = Field(
        default_factory=list, description="Observed Unicode control labels or names, never submitted values.",
    )


def _normalize_flow_labels(values: list[str] | None, *, label: str) -> list[str]:
    """Preserve observed Unicode labels; values belong to a different schema property."""

    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"{label} must be a list of observed label strings")
    return list(values)


def _normalize_flow_steps(
    target: str,
    steps: list[dict[str, Any]] | None,
    allowed_origins: list[str],
) -> list[dict[str, Any]]:
    normalized = []
    aliases = {"navigate": "navigation", "submit": "form_submission", "verify": "verification"}
    for raw in steps or []:
        value = dict(raw)
        action = str(value.get("action") or "").strip().lower()
        value["action"] = aliases.get(action, action)
        step = FlowStep.model_validate(value)
        scope = target
        if not _credential_target_url_contains(target, step.url, label="step url"):
            scope = next(
                (
                    origin
                    for origin in allowed_origins
                    if not _credential_target_url_contains(origin, target, label="step target")
                    and _credential_target_url_contains(origin, step.url, label="step url")
                ),
                target,
            )
        step.url = _safe_flow_descriptor_url(scope, step.url, label="step url")
        step.field_names = _normalize_flow_labels(step.field_names, label="step field_names")
        normalized.append(step.model_dump())
    return normalized


_FLOW_STEPS_SCHEMA = {
    "type": "array",
    "description": "Ordered observed steps; clean URLs and Unicode control labels, never submitted values or secrets.",
    "items": FlowStep.model_json_schema(),
}


def _record_authentication_flow(
    credential_id: str = "",
    target: str = "",
    kind: str = "",
    login_url: str = "",
    validation_url: str = "",
    authorization_storage_key: str = "",
    storage_header_bindings: list[dict[str, str]] | None = None,
    request_header_names: list[str] | None = None,
    request_format: str = "",
    allowed_origins: list[str] | None = None,
    purpose: str = "authentication",
    roles: list[str] | None = None,
    success_redirect_url: str = "",
    identity_fields: list[str] | None = None,
    required_fields: list[str] | None = None,
    optional_fields: list[str] | None = None,
    steps: list[dict[str, Any]] | None = None,
    candidate_sink: Callable[[dict[str, Any]], None] | None = None,
    task_uid: str = "",
    operation_id: str = "",
) -> str:
    """Validate a flow, then stage it for review or persist it for legacy callers."""

    purpose = _normalize_authentication_enum(purpose, field_name="purpose")
    kind = _normalize_authentication_enum(kind, field_name="kind")
    request_format = _normalize_authentication_enum(request_format, field_name="request_format")
    if candidate_sink is not None:
        if not task_uid or not operation_id or credential_id:
            raise ValueError("Worker staging requires controller task and operation bindings and no credential_id")
        _bound_flow_task_target_values(task_uid, operation_id, target, purpose)
    elif purpose == "authentication":
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
    elif purpose == "registration":
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
        request_header_names = _normalize_request_header_names(request_header_names)
    elif validation_url:
        raise ValueError("registration flow recording does not accept validation_url")
    elif authorization_storage_key:
        raise ValueError("registration flow recording does not accept an authorization storage key")
    elif storage_header_bindings:
        raise ValueError("registration flow recording does not accept storage header bindings")
    elif request_header_names:
        raise ValueError("registration flow recording does not accept request header names")
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
        "flow_version": AUTHENTICATION_FLOW_VERSION if purpose == "authentication" else REGISTRATION_FLOW_VERSION,
        "login_url": effective_login_url,
        "validation_url": validation_url if purpose == "authentication" else "",
        "authorization_storage_key": authorization_storage_key if purpose == "authentication" else "",
        "storage_header_bindings": storage_header_bindings if purpose == "authentication" else [],
        "request_header_names": request_header_names if purpose == "authentication" else [],
        "url": effective_login_url if purpose == "registration" else "",
        "request_format": request_format,
        "allowed_origins": list(dict.fromkeys([f"{urlsplit(target).scheme}://{urlsplit(target).netloc}", *normalized_origins])),
        "evidence_refs": [],
        "steps": _normalize_flow_steps(target, steps, normalized_origins),
    }
    if purpose == "authentication":
        descriptor["provenance"] = "observed_worker_flow"
    if purpose == "registration":
        descriptor["roles"] = sorted({str(role).strip() for role in roles or ["user"] if str(role).strip()})
        descriptor["success_redirect_url"] = success_redirect_url
        for key, values in {
            "identity_fields": identity_fields,
            "required_fields": required_fields,
            "optional_fields": optional_fields,
        }.items():
            normalized_fields = _normalize_flow_labels(values, label=key)
            if normalized_fields:
                descriptor[key] = normalized_fields
    if candidate_sink is not None:
        descriptor["provenance"] = "observed_worker_flow"
        candidate_sink(descriptor)
        return json.dumps({"recorded": True, "staged": True, "flow": descriptor}, sort_keys=True)
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
    candidate_sink: Callable[[dict[str, Any]], None] | None = None,
    task_uid: str = "",
    operation_id: str = "",
) -> Any:
    """Bind observed fields; an optional controller sink stages the descriptor without persistence."""

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

    if candidate_sink is not None:
        if not task_uid or not operation_id or normalized_credential_id:
            raise ValueError("Worker staging requires controller task and operation bindings and no credential_id")
    elif task_uid or operation_id:
        raise ValueError("Controller task bindings are supported only for worker staging")

    def record_bound_authentication_flow(
        login_url: str,
        validation_url: str = "",
        authorization_storage_key: str = "",
        storage_header_bindings: list[dict[str, str]] | None = None,
        request_header_names: list[str] | None = None,
        request_format: str = "",
        success_redirect_url: str = "",
        identity_fields: list[str] | None = None,
        required_fields: list[str] | None = None,
        optional_fields: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> str:
        """Persist one observed flow using controller-bound authentication context."""

        return _record_authentication_flow(
            credential_id=normalized_credential_id,
            target=normalized_target,
            kind=normalized_kind,
            login_url=login_url,
            validation_url=validation_url,
            authorization_storage_key=authorization_storage_key,
            storage_header_bindings=storage_header_bindings,
            request_header_names=request_header_names,
            request_format=request_format,
            allowed_origins=list(normalized_origins),
            purpose=normalized_purpose,
            roles=list(normalized_roles),
            success_redirect_url=success_redirect_url,
            identity_fields=identity_fields,
            required_fields=required_fields,
            optional_fields=optional_fields,
            steps=steps,
            candidate_sink=candidate_sink,
            task_uid=task_uid,
            operation_id=operation_id,
        )

    if normalized_purpose == "authentication":
        required = ["login_url"] if normalized_kind in {"browser_form", "browser_redirect", "browser_mfa"} else [
            "login_url", "validation_url"
        ]
        properties = {
            "login_url": {"type": "string", "description": "Observed same-target login URL."},
            "validation_url": {
                "type": "string",
                "description": "Observed same-target validation URL; empty until browser login yields a validated request.",
            },
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
            "request_header_names": {
                "type": "array", "items": {"type": "string"},
                "description": "Names of credential headers observed on a successful browser request; never values.",
            },
            "request_format": {
                "type": "string", "enum": ["", "json", "form"],
                "description": "Observed transport format; omit or leave empty when unknown for a browser flow.",
            },
        }
    else:
        required = ["login_url"]
        properties = {
            "login_url": {"type": "string", "description": "Observed same-target registration URL."},
            "request_format": {
                "type": "string", "enum": ["", "json", "form"],
                "description": "Observed transport format; omit or leave empty when unknown for a browser flow.",
            },
            "success_redirect_url": {"type": "string", "description": "Observed same-target success redirect URL."},
            "identity_fields": {"type": "array", "items": {"type": "string"}},
            "required_fields": {
                "type": "array", "items": {"type": "string"},
                "description": "Fields proven required by rendered constraints or observed validation; unknowns omitted.",
            },
            "optional_fields": {
                "type": "array", "items": {"type": "string"},
                "description": "Fields proven optional; absence of required markers alone does not prove optionality.",
            },
        }
    properties["steps"] = _FLOW_STEPS_SCHEMA
    record_bound_authentication_flow.__name__ = str(name).strip()
    record_bound_authentication_flow.__doc__ = (
        ("Stage one observed flow for controller review. " if candidate_sink else "Persist one observed flow. ")
        + "The controller bound target="
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
    name="capture_browser_authenticated_context",
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "credential_id": {"type": "string"},
                "storage_header_bindings": {"type": "array", "items": {"type": "object"}},
                "request_header_names": {"type": "array", "items": {"type": "string"}},
                "validation_url": {"type": "string"},
                "validation_marker": {"type": "string"},
            },
            "required": ["credential_id", "storage_header_bindings"],
            "additionalProperties": False,
        }
    },
)
async def capture_browser_authenticated_context(
    credential_id: str,
    storage_header_bindings: list[dict[str, str]],
    validation_url: str = "",
    validation_marker: str = "",
    request_header_names: list[str] | None = None,
) -> str:
    """Capture browser authentication under the active task checkout boundary."""
    return await _capture_browser_authenticated_context(
        credential_id, storage_header_bindings, validation_url, validation_marker, request_header_names,
    )


async def _capture_browser_authenticated_context(
    credential_id: str,
    storage_header_bindings: list[dict[str, str]],
    validation_url: str = "",
    validation_marker: str = "",
    request_header_names: list[str] | None = None,
    credential_resolver: Callable[[], tuple[Any, dict[str, Any], str]] | None = None,
) -> str:
    """Capture an authentication agent's browser session into an opaque HTTP context.

    Python binds staged header metadata for the authentication agent. It copies same-origin cookies plus
    the flow's declared browser-storage-derived headers into operation memory. An omitted URL selects the latest
    successful same-target GET carrying those credentials. Observed request headers take precedence for matching declared names. Neither values nor cookies are returned to the agent or persisted to
    workflow state.
    """

    from modules.tools.browser import get_browser

    task, record, credential_id = (
        credential_resolver() if credential_resolver is not None else _active_record(credential_id)
    )
    target = str(record["target"]).rstrip("/")
    if validation_url and not _credential_target_url_contains(target, validation_url, label="validation_url"):
        raise ValueError("validation URL must share the credential target origin")
    bindings = _normalize_storage_header_bindings(storage_header_bindings)
    observed_names = _normalize_request_header_names(request_header_names)
    session = requests.Session()
    headers: dict[str, str] = {}
    requested_url = validation_url
    candidates: list[tuple[str, dict[str, str]]] = []
    candidate_requests: list[int] = []
    async with get_browser() as browser:
        async def capture() -> tuple[
            list[dict[str, Any]],
            dict[str, str],
            dict[str, str],
            dict[tuple[str, str, str], float | None],
            str,
        ]:
            async with browser.timeout():
                cookies = await browser.context.cookies([target])
                observed_headers: dict[str, str] = {}
                wanted_headers = {str(binding["header_name"]).lower() for binding in bindings}
                wanted_headers.update(name.lower() for name in observed_names)
                matching_request_found = False
                selected_url = ""
                for request in reversed(tuple(getattr(browser, "recent_requests", ()))):
                    if not _credential_target_url_contains(target, str(request.url), label="browser request URL"):
                        continue
                    if validation_url and str(request.url).split("#", 1)[0] != validation_url:
                        continue
                    if str(getattr(request, "method", "GET")).upper() != "GET":
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
                    candidate_requests.append(id(request))
                    selected_url = str(request.url).split("#", 1)[0]
                    observed_headers = {
                        name: normalized_headers[name]
                        for name in wanted_headers
                        if name in normalized_headers
                    }
                    if not any(url == selected_url for url, _ in candidates):
                        candidates.append((selected_url, observed_headers))
                    if len(candidates) >= _BROWSER_VALIDATION_CANDIDATE_LIMIT:
                        break
                if not matching_request_found:
                    raise ValueError(
                        "AUTH_CAPTURE_REQUEST_MISSING: No successful same-target browser GET request matched "
                        "the validation URL and required "
                        "credentials. Perform a normal signed-in browser action, then capture without a URL. "
                        f"Required header names: {', '.join(sorted(wanted_headers)) or 'none configured'}. "
                        "Record observed credential headers if cookies are insufficient."
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
                needs_cookie_expiry = any(
                    (expiry := _finite_timestamp(cookie.get("expires"))) is None or expiry <= 0
                    for cookie in cookies
                )
                observed_expirations = (
                    await _observed_cookie_expirations(browser, target, cookies) if needs_cookie_expiry else {}
                )
                return cookies, observed_headers, storage_values, observed_expirations, selected_url

        cookies, observed_headers, storage_values, observed_expirations, validation_url = (
            await browser.run_in_browser_loop(capture)
        )
    for cookie in cookies:
        expiry = _finite_timestamp(cookie.get("expires"))
        if expiry is None or expiry <= 0:
            key = (
                str(cookie["name"]),
                str(cookie.get("domain") or ""),
                str(cookie.get("path") or "/"),
            )
            expiry = observed_expirations.get(key)
        session.cookies.set(
            str(cookie["name"]),
            str(cookie["value"]),
            domain=str(cookie.get("domain") or ""),
            path=str(cookie.get("path") or "/"),
            expires=int(expiry) if _finite_timestamp(expiry) is not None else None,
        )
    fingerprint = hashlib.sha256(json.dumps({
        "task": task.task_uid, "url": requested_url, "marker": validation_marker, "candidates": candidates,
        "cookies": [(cookie.name, cookie.value, cookie.domain, cookie.path, cookie.expires) for cookie in session.cookies],
        "storage": storage_values, "bindings": bindings,
        "requests": candidate_requests,
    }, sort_keys=True).encode()).hexdigest()
    failures = _AUTHENTICATION_ATTEMPTS.setdefault(
        _context_key(_operation_id(), target, credential_id), {},
    )
    failure_key = "capture_failure"
    if failures.get(failure_key) == fingerprint:
        raise ValueError(
            "AUTH_CAPTURE_NEW_EVIDENCE_REQUIRED: this capture already failed with the same requests and "
            "session material. Perform a new signed-in action that requests protected data, or correct "
            "the validation URL, marker, or recorded credential header names before retrying."
        )
    failures[failure_key] = fingerprint
    session.close()
    last_error = None
    for selected_url, observed_headers in candidates:
        headers = {}
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
        for name in observed_names:
            headers[name] = observed_headers[name.lower()]
        candidate_session = requests.Session()
        candidate_session.cookies.update(session.cookies)
        try:
            result = _store_context(
                task, record, credential_id, candidate_session, headers, {}, selected_url, validation_marker,
            )
        except ValueError as error:
            candidate_session.close()
            if str(error) not in {
                "authentication validation failed",
                "authenticated response was not distinguished from the anonymous response",
            }:
                raise
            last_error = error
            continue
        failures.pop(failure_key, None)
        return result
    failure_code = (
        "AUTH_CAPTURE_REPLAY_FAILED" if str(last_error) == "authentication validation failed"
        else "AUTH_CAPTURE_PROOF_FAILED"
    )
    raise ValueError(
        f"{failure_code}: {last_error}. Tried {len(candidates)} distinct observed requests. "
        "Rendered SPA pages may return identical anonymous HTML; use an observed protected data endpoint "
        "and the application's recorded credential headers, or an authenticated-only response marker."
    )



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
        if "headers" in payload:
            headers.update(payload["headers"])
        else:
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
            response = _scoped_request(
                session, target, "POST", token_url, data=form_values, headers=request_headers, timeout=15,
            )
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
    allow_redirects: bool = True,
) -> str:
    """Make one request through a validated operation-local authenticated context.

    Cookie and Authorization headers are controller-owned and cannot be supplied by the task.
    """

    normalized_headers = dict(headers or {})
    store = _get_database_store()
    operation_id = _operation_id()
    task, record, credential_id = _active_record(credential_id, access_mode="context")
    target = str(record["target"]).rstrip("/")
    if not _credential_target_url_contains(target, url, label="request URL"):
        raise ValueError("request URL is outside the credential target origin")
    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    if context is None:
        raise ValueError(AUTH_CONTEXT_REFRESH_REQUIRED_ERROR)
    owned_headers = {name.lower() for name in context.headers} | {"cookie"}
    if any(name.lower() in owned_headers for name in normalized_headers):
        raise ValueError("controller-owned credential headers cannot be overridden")
    owned_params = {name.lower() for name in context.params}
    if any(name.lower() in owned_params for name, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
        raise ValueError("controller-owned credential query parameters cannot be overridden")
    validated = _validate(context) and _prove_authenticated_context(context)
    if not validated or _expiring_authentication_material(context, time.time(), expired_only=False):
        raise ValueError(AUTH_CONTEXT_REFRESH_REQUIRED_ERROR)
    merged_headers = {**context.headers, **normalized_headers}
    try:
        response = _scoped_request(
            context.session, target, method.upper(), url, data=body, headers=merged_headers,
            params=context.params, timeout=20, follow_redirects=allow_redirects,
        )
    except (requests.RequestException, ValueError) as error:
        raise ValueError("authenticated request failed") from error
    store.record_credential_usage(operation_id, credential_id, task_uid=task.task_uid, outcome="succeeded")
    return json.dumps(redact({"status_code": response.status_code, "headers": dict(response.headers), "body": response.text}))
