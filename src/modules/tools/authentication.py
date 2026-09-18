"""Operation-local, secret-safe authenticated HTTP contexts."""

from __future__ import annotations

import json
from base64 import b64encode
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests
from strands import tool

from modules.tools.credentials import _active_checked_out_credential, _active_task_target_values
from modules.tools.memory import _get_database_store, _operation_id, active_credential_task
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
    try:
        response = context.session.get(
            context.validation_url, headers=context.headers, params=context.params, timeout=15
        )
    except requests.RequestException:
        return False
    return response.status_code not in {401, 403, 404}


def authentication_context_is_valid(operation_id: str, target: str, credential_id: str) -> bool:
    """Check an opaque operation-memory context without exposing session material."""

    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    return context is not None and _validate(context)


def _active_record(credential_id: str) -> tuple[Any, dict[str, Any], str]:
    """Resolve an active task's checked-out, controller-bound credential."""

    store = _get_database_store()
    operation_id = _operation_id()
    active_task = active_credential_task(store, operation_id)
    if active_task is None:
        raise ValueError("authenticated request requires an active task")
    resolved_credential_id = _bound_credential_id(active_task, credential_id)
    task, record = _active_checked_out_credential(store, operation_id, resolved_credential_id)
    return task, record, resolved_credential_id


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


@tool(name="record_authentication_flow")
def record_authentication_flow(
    credential_id: str = "",
    kind: str = "",
    login_url: str = "",
    validation_url: str = "",
    request_format: str = "",
    allowed_origins: list[str] | None = None,
    purpose: str = "authentication",
    roles: list[str] | None = None,
    success_redirect_url: str = "",
) -> str:
    """Persist a secret-free observed authentication or registration flow for later same-target setup."""

    if purpose not in {"authentication", "registration"}:
        raise ValueError("purpose must be authentication or registration")
    if purpose == "authentication":
        _task, record, credential_id = _active_record(credential_id)
        target = str(record["target"]).rstrip("/")
    else:
        store = _get_database_store()
        _task, target_values = _active_task_target_values(store, _operation_id())
        if credential_id:
            raise ValueError("registration flow recording does not accept a credential_id")
        if len(target_values) != 1:
            raise ValueError("registration flow recording requires one active task target")
        target = next(iter(target_values)).rstrip("/")
        credential_id = ""
    allowed_kinds = (
        {"api_form", "browser_form", "browser_redirect", "browser_mfa", "api_key", "oauth2_client"}
        if purpose == "authentication"
        else {"browser_registration", "api_registration"}
    )
    if kind not in allowed_kinds:
        raise ValueError("unknown flow kind")
    effective_login_url = login_url or (validation_url if kind in {"api_key", "oauth2_client"} else "")
    effective_login_url = _safe_flow_descriptor_url(target, effective_login_url, label="login_url")
    if purpose == "authentication":
        validation_url = _safe_flow_descriptor_url(target, validation_url, label="validation_url")
    elif validation_url:
        raise ValueError("registration flow recording does not accept validation_url")
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
        "login_url": effective_login_url,
        "validation_url": validation_url if purpose == "authentication" else "",
        "url": effective_login_url if purpose == "registration" else "",
        "request_format": request_format if request_format in {"", "json", "form"} else "",
        "allowed_origins": list(dict.fromkeys([f"{urlsplit(target).scheme}://{urlsplit(target).netloc}", *normalized_origins])),
        "evidence_refs": [],
    }
    if purpose == "registration":
        descriptor["roles"] = sorted({str(role).strip() for role in roles or ["user"] if str(role).strip()})
        descriptor["success_redirect_url"] = success_redirect_url
    stored = _get_database_store().upsert_authentication_flow(_operation_id(), descriptor)
    return json.dumps({"recorded": True, "credential_id": credential_id, "flow": stored}, sort_keys=True)


@tool(name="ensure_authenticated_context")
def ensure_authenticated_context(
    credential_id: str,
    login_url: str = "",
    validation_url: str = "",
    request_format: str = "json",
    additional_fields: dict[str, str] | None = None,
) -> str:
    """Ensure one checked-out credential has an operation-local authenticated HTTP context.

    Existing contexts are validated before reuse. When absent or invalid, this controller-owned adapter performs the
    mapped same-origin username/password login. It retains cookies and tokens only in memory and never returns them.
    """

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
        raise ValueError("mapped same-origin login_url and validation_url are required to establish authentication")
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
        raise ValueError("authentication request failed") from error
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


@tool(name="capture_browser_authenticated_context")
async def capture_browser_authenticated_context(
    credential_id: str,
    validation_url: str,
    authorization_storage_key: str = "",
) -> str:
    """Capture an authentication agent's browser session into an opaque HTTP context.

    This tool is intentionally available only to the authentication agent. It copies same-origin cookies and, when a
    named local/session-storage key is supplied, a bearer token directly into operation memory. Neither value is
    returned to the agent or persisted to workflow state.
    """

    from modules.tools.browser import get_browser

    task, record, credential_id = _active_record(credential_id)
    target = str(record["target"]).rstrip("/")
    if not _credential_target_url_contains(target, validation_url, label="validation_url"):
        raise ValueError("validation URL must share the credential target origin")
    session = requests.Session()
    headers: dict[str, str] = {}
    async with get_browser() as browser:
        async def capture() -> tuple[list[dict[str, Any]], str]:
            async with browser.timeout():
                cookies = await browser.context.cookies([target])
                token = ""
                if authorization_storage_key:
                    token = await browser.page.evaluate(
                        "(key) => localStorage.getItem(key) || sessionStorage.getItem(key) || ''",
                        authorization_storage_key,
                    )
                return cookies, token

        cookies, token = await browser.run_in_browser_loop(capture)
    for cookie in cookies:
        session.cookies.set(
            str(cookie["name"]),
            str(cookie["value"]),
            domain=str(cookie.get("domain") or ""),
            path=str(cookie.get("path") or "/"),
        )
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
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
    task, record, credential_id = _active_record(credential_id)
    target = str(record["target"]).rstrip("/")
    if not _credential_target_url_contains(target, url, label="request URL"):
        raise ValueError("request URL is outside the credential target origin")
    context = _CONTEXTS.get(_context_key(operation_id, target, credential_id))
    if context is None or not _validate(context):
        raise ValueError("no valid authenticated context; establish one before requesting")
    merged_headers = {**context.headers, **normalized_headers}
    try:
        response = context.session.request(
            method.upper(), url, data=body, headers=merged_headers, params=context.params, timeout=20
        )
    except requests.RequestException as error:
        raise ValueError("authenticated request failed") from error
    store.record_credential_usage(operation_id, credential_id, task_uid=task.task_uid, outcome="succeeded")
    return json.dumps(redact({"status_code": response.status_code, "headers": dict(response.headers), "body": response.text}))
