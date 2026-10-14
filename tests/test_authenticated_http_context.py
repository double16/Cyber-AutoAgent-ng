"""Tests for operation-local authenticated request contexts."""

from __future__ import annotations

import asyncio
import json
import time
from base64 import urlsafe_b64encode
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.handlers.utils import get_tool_spec
from modules.tools import authentication, credentials
from modules.tools.credentials import checkout_credential, set_task_auth_context
from modules.tools.memory import OperationPlan, OperationTarget, PlanPhase, SQLiteApplicationStore, Task
from modules.utils.redaction import REDACTED, redact_text
from tests.fixtures.authentication_app import running_authentication_app
from tests.helpers.acceptance import make_acceptance


@pytest.fixture
def assume_context_proven(monkeypatch):
    """Isolate proof only in unit tests focused on other context behavior."""

    monkeypatch.setattr(authentication, "_prove_authenticated_context", lambda _context: True)


def _activate_task(store: SQLiteApplicationStore, target: str) -> None:
    store.store_plan(
        "OP_AUTH",
        OperationPlan(
            objective="Authenticate",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="Authentication", status="active")],
            targets=[OperationTarget(target_id="target", value=target, type="network")],
        ),
    )
    store.store_task("OP_AUTH", Task("auth-task", "Auth", "Authenticate", make_acceptance("auth-task"), 1, "active"))


def _jwt_with_exp(expiration: int) -> str:
    def segment(value: dict[str, object]) -> str:
        return urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{segment({'alg': 'HS256', 'typ': 'JWT'})}.{segment({'exp': expiration})}.signature"


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("https://target.test/app/api/me", True),
        ("https://target.test/app", True),
        ("https://target.test/app-admin", False),
        ("https://target.test.evil.test/app/api/me", False),
        ("http://target.test/app/api/me", False),
        ("https://target.test:444/app/api/me", False),
    ],
)
def test_credential_target_url_scope_uses_parsed_origin_and_path(candidate, expected):
    assert authentication._credential_target_url_contains("https://target.test/app", candidate, label="URL") is expected


@pytest.mark.parametrize("anonymous_status,anonymous_body,marker,expected", [
    (401, "denied", "", True),
    (200, "public", "", False),
    (200, "public", "account:alice", True),
    (200, "account:alice", "account:alice", False),
])
def test_context_proof_requires_a_credential_dependent_response(
    monkeypatch, anonymous_status, anonymous_body, marker, expected,
):
    target = "https://target.test"
    anonymous_session = Mock()
    anonymous_session.get.return_value = SimpleNamespace(
        status_code=anonymous_status, headers={}, text=anonymous_body, url=f"{target}/private",
    )
    credential_session = Mock()
    credential_session.get.return_value = SimpleNamespace(
        status_code=200, headers={}, text="account:alice", url=f"{target}/private",
    )
    monkeypatch.setattr(authentication.requests, "Session", lambda: anonymous_session)
    context = authentication._AuthenticationContext(
        target, "credential", credential_session, {"X-Session": "opaque"}, {}, f"{target}/private",
        validation_marker=marker,
    )

    assert authentication._prove_authenticated_context(context) is expected


def test_scoped_authenticated_redirect_rejects_a_foreign_origin():
    session = Mock()
    session.get.return_value = SimpleNamespace(
        status_code=302, headers={"Location": "https://elsewhere.test/private"}, text="",
    )

    with pytest.raises(ValueError, match="outside the credential target boundary"):
        authentication._scoped_request(session, "https://target.test", "GET", "https://target.test/private")
    session.get.assert_called_once()


def test_context_proof_accepts_credential_dependent_same_target_redirect(monkeypatch):
    target = "https://target.test"
    anonymous = Mock()
    anonymous.get.return_value = SimpleNamespace(status_code=200, headers={}, text="login", url=f"{target}/login")
    authenticated = Mock()
    authenticated.get.return_value = SimpleNamespace(
        status_code=200, headers={}, text="dashboard", url=f"{target}/dashboard",
    )
    monkeypatch.setattr(authentication.requests, "Session", lambda: anonymous)
    context = authentication._AuthenticationContext(
        target, "credential", authenticated, {"Tauser": "opaque"}, {}, f"{target}/private",
    )

    assert authentication._prove_authenticated_context(context)


def test_request_header_names_accept_arbitrary_valid_names_and_reject_duplicates():
    assert authentication._normalize_request_header_names(["X-Session", "Tauser"]) == ["X-Session", "Tauser"]
    with pytest.raises(ValueError, match="distinct"):
        authentication._normalize_request_header_names(["Tauser", "tauser"])
    with pytest.raises(ValueError, match="valid HTTP header"):
        authentication._normalize_request_header_names(["Bad Header"])


def test_authenticated_http_request_can_disable_redirects_for_controller(monkeypatch, assume_context_proven):
    target = "https://target.test"
    credential_id = "credential-1"
    session = Mock()
    session.headers = {}
    session.cookies = []
    session.get.return_value = SimpleNamespace(status_code=302, headers={"Location": "/login"}, text="")
    context = authentication._AuthenticationContext(
        target, credential_id, session, {"Authorization": "Bearer hidden"}, {}, f"{target}/private",
    )
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    monkeypatch.setattr(authentication, "_get_database_store", lambda: SimpleNamespace(
        record_credential_usage=Mock(),
    ))
    monkeypatch.setattr(authentication, "_active_record", lambda *_args, **_kwargs: (
        SimpleNamespace(task_uid="coverage-task"), {"target": target}, credential_id,
    ))
    monkeypatch.setattr(authentication, "_validate", lambda _context: True)
    monkeypatch.setitem(authentication._CONTEXTS, ("OP_AUTH", target, credential_id), context)

    response = json.loads(authentication.authenticated_http_request(
        "GET", f"{target}/private", credential_id, allow_redirects=False,
    ))

    assert response["status_code"] == 302
    assert session.get.call_args.kwargs["allow_redirects"] is False


def test_authenticated_request_protects_arbitrary_credential_header(monkeypatch):
    target = "https://target.test"
    credential_id = "credential"
    context = authentication._AuthenticationContext(
        target, credential_id, Mock(headers={}, cookies=[]), {"Tauser": "opaque"}, {}, f"{target}/private",
    )
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    monkeypatch.setattr(authentication, "_active_record", lambda *_args, **_kwargs: (
        SimpleNamespace(task_uid="task"), {"target": target}, credential_id,
    ))
    monkeypatch.setitem(authentication._CONTEXTS, ("OP_AUTH", target, credential_id), context)

    with pytest.raises(ValueError, match="cannot be overridden"):
        authentication.authenticated_http_request("GET", f"{target}/private", headers={"tauser": "caller"})


def test_browser_login_receipt_marks_only_mapped_401_as_credential_rejected(monkeypatch):
    monkeypatch.setattr(
        "modules.tools.browser.latest_browser_interaction_receipts",
        lambda: [
            {"url": "https://target.test/other", "status": 401},
            {"url": "https://target.test/login", "status": 401, "evidence_ref": "artifact://login.har"},
        ],
    )

    result = authentication.record_browser_authentication_attempt(
        "OP_AUTH", "https://target.test", "credential-1", "https://target.test/login"
    )

    assert result == {
        "outcome": "credential_rejected",
        "transport": "browser",
        "login_url": "https://target.test/login",
        "status": 401,
        "evidence_refs": ["artifact://login.har"],
    }
    assert authentication.authentication_attempt_result("OP_AUTH", "https://target.test", "credential-1") == result


def test_browser_login_receipt_does_not_invalidate_forbidden_response(monkeypatch):
    monkeypatch.setattr(
        "modules.tools.browser.latest_browser_interaction_receipts",
        lambda: [{"url": "https://target.test/login", "status": 403}],
    )

    result = authentication.record_browser_authentication_attempt(
        "OP_AUTH", "https://target.test", "credential-2", "https://target.test/login"
    )

    assert result is not None
    assert result["outcome"] == "completed"


def test_browser_login_receipt_ignores_unrelated_and_missing_status(monkeypatch):
    monkeypatch.setattr(
        "modules.tools.browser.latest_browser_interaction_receipts",
        lambda: [
            {"url": "https://target.test/login", "status": "401"},
            {"url": "https://target.test/unrelated", "status": 401},
        ],
    )

    assert authentication.record_browser_authentication_attempt(
        "OP_AUTH", "https://target.test", "credential-3", "https://target.test/login"
    ) is None


def test_checkout_registers_credential_payload_values_for_runtime_redaction(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    credential = store.store_credential(
        "OP_AUTH",
        {
            "credential_type": "username_password",
            "target": target,
            "role": "member",
            "payload": {"username": "alice", "password": "checkout-secret-value"},
            "origin": "provided",
            "management_policy": "user",
            "status": "unknown",
        },
    )
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_AUTH")

    checkout_credential(credential["credential_id"], "authentication setup")

    assert redact_text("password checkout-secret-value") == f"password {REDACTED}"


def test_authentication_flow_records_are_target_scoped_and_secret_free(tmp_path):
    target = "https://target.test/app"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    stored = store.upsert_authentication_flow(
        "OP_AUTH",
        {
            "target": target,
            "purpose": "authentication",
            "kind": "browser_form",
            "login_url": f"{target}/login",
            "validation_url": f"{target}/api/me",
            "allowed_origins": ["https://target.test"],
            "evidence_refs": ["artifact://auth-flow"],
        },
    )

    flows = store.list_authentication_flows(target)

    assert stored["status"] == "discovered"
    assert len(flows) == 1
    assert flows[0]["descriptor"]["login_url"] == f"{target}/login"
    assert flows[0]["descriptor"]["evidence_refs"] == ["artifact://auth-flow"]
    assert store.list_authentication_flows("https://target.test/other") == []


def test_current_authentication_flow_record_requires_observed_discovery_provenance(tmp_path):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)

    with pytest.raises(ValueError, match="observed worker provenance"):
        store.upsert_authentication_flow(
            "OP_AUTH",
            {
                "target": target,
                "purpose": "authentication",
                "kind": "browser_form",
                "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
                "login_url": f"{target}/login",
                "validation_url": f"{target}/account",
            },
        )


def test_registration_flow_recording_reuses_active_target_without_a_credential(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")

    result = json.loads(
        authentication.record_authentication_flow(
            purpose="registration",
            kind="browser_registration",
            login_url=f"{target}/register",
            roles=["member"],
            identity_fields=["email", "password"],
            required_fields=["firstName", "lastName"],
            optional_fields=["company"],
        )
    )

    assert result["recorded"] is True
    descriptor = store.list_authentication_flows(target, purpose="registration")[0]["descriptor"]
    assert descriptor["url"] == f"{target}/register"
    assert descriptor["flow_version"] == authentication.REGISTRATION_FLOW_VERSION
    assert descriptor["roles"] == ["member"]
    assert descriptor["identity_fields"] == ["email", "password"]
    assert descriptor["required_fields"] == ["firstName", "lastName"]
    assert descriptor["optional_fields"] == ["company"]
    with pytest.raises(ValueError, match="query values"):
        authentication.record_authentication_flow(
            purpose="registration",
            kind="browser_registration",
            login_url=f"{target}/register?token=must-not-persist",
        )


def test_authentication_flow_discovery_records_active_target_without_a_credential(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")

    result = json.loads(
        authentication.record_authentication_flow(
            target=target,
            purpose="authentication",
            kind="browser_form",
            login_url=f"{target}/login",
            validation_url=f"{target}/protected",
            request_format="json",
        )
    )

    assert result["recorded"] is True
    assert result["credential_id"] == ""
    assert store.list_authentication_flows(target)[0]["descriptor"]["login_url"] == f"{target}/login"


def test_authentication_enum_schemas_advertise_canonical_values_and_accept_synonyms(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")

    flow_schema = get_tool_spec(authentication.record_authentication_flow)["inputSchema"]["json"]
    properties = flow_schema["properties"]
    assert properties["purpose"]["enum"] == ["authentication", "registration"]
    assert properties["kind"]["enum"] == sorted(
        authentication.AUTHENTICATION_FLOW_KINDS | authentication.REGISTRATION_FLOW_KINDS
    )
    assert properties["request_format"]["enum"] == ["", "json", "form"]
    runtime_input = authentication.record_authentication_flow._metadata.validate_input(
        {"target": target, "purpose": "login", "kind": "web form", "request_format": "application/json"}
    )
    assert runtime_input["purpose"] == "login"
    assert runtime_input["kind"] == "web form"

    result = json.loads(
        authentication.record_authentication_flow(
            target=target,
            purpose="login",
            kind="web form",
            login_url=f"{target}/login",
            validation_url=f"{target}/protected",
            request_format="application/json",
        )
    )
    descriptor = store.list_authentication_flows(target, purpose="authentication")[0]["descriptor"]
    assert result["recorded"] is True
    assert descriptor["purpose"] == "authentication"
    assert descriptor["kind"] == "browser_form"
    assert descriptor["request_format"] == "json"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("purpose", "enrollment"), ("kind", "magic_link"), ("request_format", "xml")],
)
def test_authentication_enum_validation_rejects_unknown_values(field_name, value):
    with pytest.raises(ValueError, match=field_name):
        authentication._normalize_authentication_enum(value, field_name=field_name)


def test_browser_flow_discovery_allows_provisional_validation_url(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")

    result = json.loads(
        authentication.record_authentication_flow(
            target=target,
            purpose="authentication",
            kind="browser_form",
            login_url=f"{target}/login",
            storage_header_bindings=[
                {"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"}
            ],
        )
    )

    assert result["recorded"] is True
    descriptor = store.list_authentication_flows(target)[0]["descriptor"]
    assert descriptor["validation_url"] == ""


def test_browser_flow_requires_final_validation_url_for_non_provisional_record(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")

    with pytest.raises(ValueError, match="validation_url is required"):
        authentication.record_authentication_flow(
            target=target,
            purpose="authentication",
            kind="api_key",
            login_url=f"{target}/login",
        )


def test_bound_authentication_flow_recorder_omits_controller_owned_inputs(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    recorder = authentication.build_record_authentication_flow_tool(
        target=target,
        purpose="authentication",
        kind="browser_form",
        allowed_origins=(target,),
        name="record_browser_form_authentication_flow",
    )

    schema = get_tool_spec(recorder)["inputSchema"]["json"]

    assert set(schema["properties"]) == {
        "login_url", "validation_url", "authorization_storage_key", "storage_header_bindings",
        "request_header_names", "request_format", "steps"
    }
    assert schema["required"] == ["login_url"]
    result = json.loads(recorder(
        login_url=f"{target}/login",
        validation_url=f"{target}/protected",
        authorization_storage_key="token",
        request_format="form",
    ))
    assert result["recorded"] is True
    descriptor = store.list_authentication_flows(target, purpose="authentication")[0]["descriptor"]
    assert descriptor["target"] == target
    assert descriptor["purpose"] == "authentication"
    assert descriptor["kind"] == "browser_form"
    assert descriptor["authorization_storage_key"] == "token"
    assert descriptor["storage_header_bindings"] == [{
        "header_name": "Authorization", "storage_key": "token", "value_template": "Bearer {value}"
    }]


@pytest.mark.parametrize("purpose,kind", [
    ("authentication", "browser_form"), ("registration", "browser_registration"),
])
@pytest.mark.parametrize("request_format", [None, "", "form"])
def test_bound_flow_recorder_stages_validated_candidates_without_persistence(
    tmp_path, monkeypatch, purpose, kind, request_format,
):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    candidates = []
    recorder = authentication.build_record_authentication_flow_tool(
        target=target, purpose=purpose, kind=kind, roles=("member",), candidate_sink=candidates.append,
        task_uid="auth-task", operation_id="OP_AUTH",
    )
    assert "candidate_sink" not in get_tool_spec(recorder)["inputSchema"]["json"]["properties"]
    schema = get_tool_spec(recorder)["inputSchema"]["json"]
    assert "request_format" not in schema["required"]
    assert schema["properties"]["request_format"]["enum"] == ["", "json", "form"]
    assert "unknown for a browser flow" in schema["properties"]["request_format"]["description"]
    fields = {} if request_format is None else {"request_format": request_format}
    result = json.loads(recorder(login_url=f"{target}/start", **fields))
    assert result["staged"] is True
    assert candidates[0]["target"] == target
    assert candidates[0]["purpose"] == purpose
    assert candidates[0]["request_format"] == (request_format or "")
    expected_version = (
        authentication.AUTHENTICATION_FLOW_VERSION if purpose == "authentication"
        else authentication.REGISTRATION_FLOW_VERSION
    )
    assert candidates[0]["flow_version"] == expected_version
    assert not store.list_authentication_flows(target, purpose=purpose)
    with pytest.raises(ValueError):
        recorder(login_url="https://unrelated.test/start")
    assert len(candidates) == 1


def test_authentication_flow_rejects_storage_values_and_invalid_key_names(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")

    with pytest.raises(ValueError, match="storage key"):
        authentication.record_authentication_flow(
            target=target,
            purpose="authentication",
            kind="browser_form",
            login_url=f"{target}/login",
            validation_url=f"{target}/protected",
            authorization_storage_key="Bearer secret-value",
        )
    with pytest.raises(ValueError, match="value_template"):
        authentication.record_authentication_flow(
            target=target,
            purpose="authentication",
            kind="browser_form",
            login_url=f"{target}/login",
            validation_url=f"{target}/protected",
            storage_header_bindings=[
                {"header_name": "X-Authentication", "storage_key": "token", "value_template": "token"}
            ],
        )


@pytest.mark.parametrize("status_code, expected", [(204, True), (401, False), (403, False), (500, False)])
def test_context_validation_requires_successful_response(status_code, expected, caplog):
    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            return type("Response", (), {"status_code": status_code})()

    context = authentication._AuthenticationContext(
        "https://target.test", "credential", Session(), {}, {}, "https://target.test/protected"
    )

    with caplog.at_level("INFO", logger=authentication.__name__):
        assert authentication._validate(context) is expected

    if expected:
        assert "Authenticated context validation failed" not in caplog.text
        assert "Authenticated context validation succeeded" in caplog.text
        assert f"status_code={status_code}" in caplog.text
        assert "method=GET" in caplog.text
        assert "elapsed_ms=" in caplog.text
    else:
        assert "reason=non_success_status" in caplog.text
        assert f"status_code={status_code}" in caplog.text
        assert "method=GET" in caplog.text
        assert "url=https://target.test/protected" in caplog.text
        assert "elapsed_ms=" in caplog.text


def test_context_validation_failure_logs_redacted_sent_headers_and_cookies(caplog):
    request = authentication.requests.Request(
        "GET",
        "https://target.test/protected?token=query-secret#fragment-secret",
        headers={
            "Authorization": "Bearer header-secret",
            "X-Debug-Mode": "visible-diagnostic",
            "Cookie": "sessionid=cookie-secret; theme=light",
        },
    ).prepare()

    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            return type("Response", (), {"status_code": 401, "request": request})()

    context = authentication._AuthenticationContext(
        "https://target.test",
        "credential",
        Session(),
        {},
        {},
        "https://target.test/protected?token=query-secret#fragment-secret",
    )

    with caplog.at_level("WARNING", logger=authentication.__name__):
        assert authentication._validate(context) is False

    assert "Authorization" in caplog.text
    assert "[REDACTED]" in caplog.text
    assert "visible-diagnostic" in caplog.text
    assert '"name": "sessionid"' in caplog.text
    assert '"name": "theme"' in caplog.text
    assert "header-secret" not in caplog.text
    assert "cookie-secret" not in caplog.text
    assert "query-secret" not in caplog.text
    assert "fragment-secret" not in caplog.text
    assert "url=https://target.test/protected" in caplog.text


@pytest.mark.parametrize("placement", ["header", "cookie_header", "cookie_jar", "query"])
def test_expiring_jwt_still_validates_http(placement, monkeypatch):
    now = int(time.time())
    token = _jwt_with_exp(now + 599)
    session = authentication.requests.Session()
    calls = []
    def get(*_args, **_kwargs):
        calls.append("requested")
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    headers = {}
    params = {}
    if placement == "header":
        headers["X-Session-Assertion"] = f"Token {token}"
    elif placement == "cookie_header":
        headers["Cookie"] = f"sid={token}"
    elif placement == "cookie_jar":
        session.cookies.set("sid", token, domain="target.test", path="/")
    else:
        params["access_token"] = token
    context = authentication._AuthenticationContext(
        "https://target.test", "credential", session, headers, params, "https://target.test/protected"
    )

    assert authentication._validate(context) is True
    assert calls == ["requested"]


def test_jwt_at_ten_minute_boundary_still_validates_http(monkeypatch):
    now = int(time.time())
    token = _jwt_with_exp(now + 600)
    session = authentication.requests.Session()
    calls = []

    def get(*_args, **_kwargs):
        calls.append("requested")
        return type("Response", (), {"status_code": 204})()

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    context = authentication._AuthenticationContext(
        "https://target.test", "credential", session, {"Authorization": token}, {}, "https://target.test/protected"
    )

    assert authentication._validate(context) is True
    assert calls == ["requested"]


@pytest.mark.parametrize("remaining, request_succeeds", [(104, False), (599, False), (600, True), (601, True),
                                                        (0, False), (-1, False)])
def test_reuse_and_requests_enforce_expiry_safety_window(
    monkeypatch, remaining, request_succeeds, assume_context_proven,
):
    now = int(time.time())
    target = "https://target.test"
    credential_id = "credential-1"
    session = Mock()
    session.headers = {}
    session.cookies = []
    session.get.return_value = SimpleNamespace(status_code=204, headers={}, text="")
    session.request.return_value = SimpleNamespace(status_code=200, headers={}, text="ok")
    context = authentication._AuthenticationContext(
        target, credential_id, session, {"Authorization": _jwt_with_exp(now + remaining)}, {},
        f"{target}/protected",
    )
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    monkeypatch.setattr(authentication, "_get_database_store", lambda: SimpleNamespace(
        record_credential_usage=Mock(),
    ))
    monkeypatch.setattr(authentication, "_active_record", lambda *_args, **_kwargs: (
        SimpleNamespace(task_uid="coverage-task"), {"target": target}, credential_id,
    ))
    monkeypatch.setitem(authentication._CONTEXTS, ("OP_AUTH", target, credential_id), context)

    assert authentication._validate(context) is True
    assert authentication.authentication_context_is_valid("OP_AUTH", target, credential_id) is request_succeeds
    if request_succeeds:
        assert json.loads(authentication.authenticated_http_request("GET", f"{target}/protected"))["status_code"] == 204
        assert session.get.call_count >= 2
    else:
        with pytest.raises(ValueError, match="AUTH_CONTEXT_REFRESH_REQUIRED"):
            authentication.authenticated_http_request("GET", f"{target}/protected")
        assert session.get.call_count >= 1




@pytest.mark.parametrize("placement", ["header", "query", "cookie_jwt", "cookie_expiry"])
@pytest.mark.parametrize("remaining", [-1, 0, 104, 599])
def test_known_expiring_material_requires_refresh_after_validation(placement, remaining, monkeypatch):
    now = int(time.time())
    target = "https://target.test"
    session = authentication.requests.Session()
    session.get = Mock(return_value=SimpleNamespace(status_code=204))
    session.request = Mock(return_value=SimpleNamespace(status_code=200, headers={}, text="ok"))
    headers = {}
    params = {}
    if placement == "header":
        headers["Tauser"] = _jwt_with_exp(now + remaining)
    elif placement == "query":
        params["access_token"] = _jwt_with_exp(now + remaining)
    elif placement == "cookie_jwt":
        session.cookies.set("sid", _jwt_with_exp(now + remaining), domain="target.test", path="/")
    else:
        session.cookies.set("sid", "opaque", domain="target.test", path="/", expires=now + remaining)
    context = authentication._AuthenticationContext(
        target, "credential-1", session, headers, params, f"{target}/protected",
    )
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    monkeypatch.setattr(authentication, "_get_database_store", lambda: SimpleNamespace(
        record_credential_usage=Mock(),
    ))
    monkeypatch.setattr(authentication, "_active_record", lambda *_args, **_kwargs: (
        SimpleNamespace(task_uid="coverage-task"), {"target": target}, "credential-1",
    ))
    monkeypatch.setitem(authentication._CONTEXTS, ("OP_AUTH", target, "credential-1"), context)

    assert authentication.authentication_context_is_valid("OP_AUTH", target, "credential-1") is False
    with pytest.raises(ValueError, match="AUTH_CONTEXT_REFRESH_REQUIRED"):
        authentication.authenticated_http_request("GET", f"{target}/protected", "credential-1")

    assert session.get.call_count == 2
    session.request.assert_not_called()


def test_unreadable_jwt_uses_http_validation(monkeypatch):
    session = authentication.requests.Session()
    calls = []

    def get(*_args, **_kwargs):
        calls.append("requested")
        return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(session, "get", get)
    context = authentication._AuthenticationContext(
        "https://target.test", "credential", session,
        {"X-Session-Assertion": "Bearer opaque.not-json.signature"}, {}, "https://target.test/protected"
    )

    assert authentication._validate(context) is True
    assert calls == ["requested"]


@pytest.mark.parametrize("claim", [{}, {"exp": "soon"}, {"exp": True}])
def test_jwt_without_numeric_expiration_uses_http_validation(claim, monkeypatch):
    header = urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    payload = urlsafe_b64encode(json.dumps(claim).encode()).rstrip(b"=").decode()
    session = authentication.requests.Session()
    calls = []

    def get(*_args, **_kwargs):
        calls.append("requested")
        return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(session, "get", get)
    context = authentication._AuthenticationContext(
        "https://target.test", "credential", session,
        {"X-Session": f"{header}.{payload}.signature"}, {}, "https://target.test/protected"
    )

    assert authentication._validate(context) is True
    assert calls == ["requested"]


def test_jwt_with_invalid_structure_or_unrepresentable_expiration_is_ignored():
    def encode(value):
        return urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    for header, payload in [
        ([], {"exp": 1}),
        ({"typ": "JWT"}, {"exp": 1}),
        ({"alg": "HS256"}, []),
        ({"alg": "HS256"}, {"exp": 10**1000}),
        ({"alg": "HS256"}, {"exp": float("inf")}),
    ]:
        assert list(authentication._jwt_expirations(f"{encode(header)}.{encode(payload)}.signature")) == []


def test_long_lived_cookie_and_jwt_continue_to_http_validation(monkeypatch):
    now = int(time.time())
    session = authentication.requests.Session()
    session.cookies.set("sid", _jwt_with_exp(now + 1800), domain="target.test", path="/", expires=now + 1200)
    calls = []

    def get(*_args, **_kwargs):
        calls.append("requested")
        return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    context = authentication._AuthenticationContext(
        "https://target.test", "credential", session, {}, {}, "https://target.test/protected"
    )

    assert authentication._validate(context) is True
    assert authentication._expiring_authentication_material(context, now) is None
    assert calls == ["requested"]


@pytest.mark.parametrize(
    ("domain", "secure", "validation_url"),
    [
        ("elsewhere.test", False, "https://target.test/protected"),
        ("target.test", True, "http://target.test/protected"),
    ],
)
def test_expiring_cookie_outside_domain_or_scheme_is_ignored(domain, secure, validation_url, monkeypatch):
    now = int(time.time())
    session = authentication.requests.Session()
    session.cookies.set("sid", "opaque", domain=domain, path="/", secure=secure, expires=now + 1)
    calls = []

    def get(*_args, **_kwargs):
        calls.append("requested")
        return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    context = authentication._AuthenticationContext(
        validation_url.removesuffix("/protected"), "credential", session, {}, {}, validation_url
    )

    assert authentication._validate(context) is True
    assert authentication._expiring_authentication_material(context, now) is None
    assert calls == ["requested"]


def test_expiring_applicable_cookie_still_validates_http(monkeypatch):
    now = int(time.time())
    session = authentication.requests.Session()
    session.cookies.set("session", "opaque", domain="target.test", path="/", expires=now + 599)
    session.cookies.set("other_path", "opaque", domain="target.test", path="/other", expires=now + 1)
    calls = []
    def get(*_args, **_kwargs):
        calls.append("requested")
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    context = authentication._AuthenticationContext(
        "https://target.test", "credential", session, {}, {}, "https://target.test/protected"
    )

    assert authentication._validate(context) is True
    assert calls == ["requested"]


def test_expiring_cookie_outside_validation_path_does_not_skip_http(monkeypatch):
    now = int(time.time())
    session = authentication.requests.Session()
    session.cookies.set("other_path", "opaque", domain="target.test", path="/other", expires=now + 1)
    calls = []

    def get(*_args, **_kwargs):
        calls.append("requested")
        return type("Response", (), {"status_code": 204})()

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(authentication.time, "time", lambda: now)
    context = authentication._AuthenticationContext(
        "https://target.test", "credential", session, {}, {}, "https://target.test/protected"
    )

    assert authentication._validate(context) is True
    assert authentication._expiring_authentication_material(context, now) is None
    assert calls == ["requested"]


def test_har_set_cookie_expiry_uses_max_age_and_latest_matching_response():
    target = "https://target.test"
    issued_at = int(time.time())
    cookie = {"name": "sid", "value": "current-value", "domain": "target.test", "path": "/"}

    class Response:
        def __init__(self, headers):
            self.headers = headers

        async def header_values(self, name):
            assert name == "set-cookie"
            return self.headers

    class Request:
        def __init__(self, url, when, headers):
            self.url = url
            self.timing = {"startTime": when * 1000}
            self.result = Response(headers)

        async def response(self):
            return self.result

    browser = type("Browser", (), {"recent_requests": [
        Request(f"{target}/login", issued_at, [
            "sid=current-value; Path=/; Max-Age=60; Expires=Wed, 21 Oct 2037 07:28:00 GMT",
            "unrelated=other; Path=/; Max-Age=1",
        ]),
        Request("https://other.test/login", issued_at + 10, ["sid=current-value; Path=/; Max-Age=1"]),
        Request(f"{target}/login", issued_at + 20, ["sid=old-value; Path=/; Max-Age=1"]),
        Request(f"{target}/login", issued_at + 30, ["sid=current-value; Path=/; Max-Age=3600"]),
    ]})()

    observed = asyncio.run(authentication._observed_cookie_expirations(browser, target, [cookie]))

    assert observed == {("sid", "target.test", "/"): issued_at + 3630}


def test_har_set_cookie_expires_and_session_replacement():
    target = "https://target.test"
    cookie = {"name": "sid", "value": "current-value", "domain": "target.test", "path": "/"}

    class Response:
        def __init__(self, header):
            self.header = header

        async def all_headers(self):
            return {"set-cookie": self.header}

    class Request:
        url = f"{target}/login"

        def __init__(self, header):
            self.header = header

        async def response(self):
            return Response(self.header)

    first_request = Request("sid=current-value; Path=/; Expires=Wed, 21 Oct 2037 07:28:00 GMT")
    browser = type("Browser", (), {"recent_requests": [first_request]})()

    assert asyncio.run(authentication._observed_cookie_expirations(browser, target, [cookie])) == {
        ("sid", "target.test", "/"): datetime(2037, 10, 21, 7, 28, tzinfo=UTC).timestamp()
    }
    browser.recent_requests.append(Request("sid=current-value; Path=/"))

    assert asyncio.run(authentication._observed_cookie_expirations(browser, target, [cookie])) == {
        ("sid", "target.test", "/"): None
    }


def test_har_max_age_without_response_time_does_not_use_expires():
    parsed_cookie = authentication.SimpleCookie()
    parsed_cookie.load("sid=value; Max-Age=60; Expires=Wed, 21 Oct 2037 07:28:00 GMT")

    assert authentication._set_cookie_expiration(parsed_cookie["sid"], None) is None


@pytest.mark.parametrize(
    ("header", "issued_at", "expected"),
    [
        ("sid=value; Max-Age=bogus; Expires=Wed, 21 Oct 2037 07:28:00 GMT", 100, "expires"),
        ("sid=value; Expires=not-a-date", 100, None),
        ("sid=value; Max-Age=999999999999999999999999999999999999999", 100, "huge"),
        ("sid=value; Max-Age=60", 100, 160),
    ],
)
def test_set_cookie_expiration_handles_invalid_and_large_values(header, issued_at, expected):
    parsed_cookie = authentication.SimpleCookie()
    parsed_cookie.load(header)
    actual = authentication._set_cookie_expiration(parsed_cookie["sid"], issued_at)
    if expected == "expires":
        assert actual == datetime(2037, 10, 21, 7, 28, tzinfo=UTC).timestamp()
    elif expected == "huge":
        assert actual is not None and actual > 10**30
    else:
        assert actual == expected


def test_har_cookie_expiry_ignores_unavailable_responses_and_mismatched_scope():
    target = "https://target.test"
    cookie = {"name": "sid", "value": "current", "domain": "target.test", "path": "/"}

    class Request:
        def __init__(self, url, response):
            self.url = url
            self.result = response

        async def response(self):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    class Response:
        async def header_values(self, _name):
            return [
                "sid=current; Domain=other.test; Path=/; Max-Age=1",
                "sid=current; Path=/other; Max-Age=1",
                "sid=previous; Path=/; Max-Age=1",
            ]

    browser = type("Browser", (), {"recent_requests": [
        Request("not-a-url", Response()),
        Request("https://other.test/login", Response()),
        Request(f"{target}/login", None),
        Request(f"{target}/login", RuntimeError("no response")),
        Request(f"{target}/login", Response()),
    ]})()

    assert asyncio.run(authentication._observed_cookie_expirations(browser, target, [cookie])) == {}


def test_validation_url_diagnostic_preserves_ipv6_without_sensitive_parts():
    assert authentication._diagnostic_validation_url(
        "https://user:secret@[::1]:8443/protected?token=secret#fragment"
    ) == "https://[::1]:8443/protected"


@pytest.mark.parametrize(("browser_expiry_seconds", "expected_seconds"), [(None, 900), (3600, 3600)])
@pytest.mark.parametrize("worker_access", [False, True])
def test_browser_capture_preserves_cookie_expiry_from_browser_or_har(
    browser_expiry_seconds, expected_seconds, monkeypatch, worker_access
):
    target = "https://target.test"
    validation_url = f"{target}/protected"
    now = int(time.time())
    cookie = {
        "name": "sid", "value": "opaque-session", "domain": "target.test", "path": "/",
        "expires": -1 if browser_expiry_seconds is None else now + browser_expiry_seconds,
    }

    class LoginRequest:
        url = f"{target}/login"
        timing = {"startTime": now * 1000}

        async def response(self):
            return type("Response", (), {
                "status": 302,
                "header_values": staticmethod(lambda _name: _set_cookie_headers()),
            })()

    class ValidationRequest:
        url = validation_url

        async def all_headers(self):
            return {"Cookie": "sid=opaque-session"}

        async def response(self):
            return type("Response", (), {"status": 204})()

    async def _set_cookie_headers():
        return ["sid=opaque-session; Path=/; Max-Age=900"]

    async def _cookies(_urls):
        return [cookie]

    class Browser:
        recent_requests = [LoginRequest(), ValidationRequest()]
        context = type("Context", (), {"cookies": staticmethod(_cookies)})()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @asynccontextmanager
        async def timeout(self):
            yield

        async def run_in_browser_loop(self, function):
            return await function()

    captured = {}

    def store_context(_task, _record, _credential_id, session, _headers, _params, _url, _marker=""):
        captured["expires"] = next(iter(session.cookies)).expires
        return "captured"

    monkeypatch.setattr(authentication, "_active_record", lambda _id: (SimpleNamespace(task_uid="task"), {"target": target}, "credential"))
    monkeypatch.setattr(authentication, "_store_context", store_context)
    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())

    if worker_access:
        monkeypatch.setattr(authentication, "_active_record", Mock(side_effect=AssertionError("No checkout")))
        result = authentication._capture_browser_authenticated_context(
            "credential", [], validation_url,
            credential_resolver=lambda: (SimpleNamespace(task_uid="task"), {"target": target}, "credential"),
        )
    else:
        result = authentication.capture_browser_authenticated_context("credential", [], validation_url)
    assert asyncio.run(result) == "captured"
    assert captured["expires"] == now + expected_seconds


@pytest.mark.parametrize("failure", ["wrong_origin", "missing_header", "invalid_storage"])
def test_browser_capture_rejects_invalid_validation_evidence(failure, monkeypatch):
    target = "https://target.test"
    validation_url = f"{target}/protected"
    class Request:
        url = validation_url

        async def all_headers(self):
            return {} if failure == "missing_header" else {"Authorization": "Bearer observed"}

        async def response(self):
            return type("Response", (), {"status": 200})()

    async def _cookies(_urls):
        return []

    async def _evaluate(_script, _keys):
        return []

    class Browser:
        recent_requests = [Request()]
        context = type("Context", (), {"cookies": staticmethod(_cookies)})()
        page = type("Page", (), {"evaluate": staticmethod(_evaluate)})()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @asynccontextmanager
        async def timeout(self):
            yield

        async def run_in_browser_loop(self, function):
            return await function()

    monkeypatch.setattr(authentication, "_active_record", lambda _id: (SimpleNamespace(task_uid="task"), {"target": target}, "credential"))
    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())

    if failure == "wrong_origin":
        with pytest.raises(ValueError, match="share the credential target origin"):
            asyncio.run(authentication.capture_browser_authenticated_context(
                "credential", [], "https://other.test/protected"
            ))
    elif failure == "missing_header":
        with pytest.raises(ValueError, match="required credentials"):
            asyncio.run(authentication.capture_browser_authenticated_context(
                "credential",
                [{"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"}],
                validation_url,
            ))
    else:
        with pytest.raises(ValueError, match="invalid result"):
            asyncio.run(authentication.capture_browser_authenticated_context(
                "credential",
                [{"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"}],
                validation_url,
            ))


def test_context_validation_logs_request_exception_type(caplog):
    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            raise authentication.requests.Timeout("sensitive request details")

    context = authentication._AuthenticationContext(
        "https://target.test", "credential", Session(), {}, {}, "https://target.test/protected"
    )

    with caplog.at_level("WARNING", logger=authentication.__name__):
        assert authentication._validate(context) is False

    assert "reason=request_exception" in caplog.text
    assert "exception_type=Timeout" in caplog.text
    assert "request_source=intended_context" in caplog.text
    assert "method=GET" in caplog.text
    assert "url=https://target.test/protected" in caplog.text
    assert "request_headers={}" in caplog.text
    assert "sensitive request details" not in caplog.text


def test_missing_authenticated_context_logs_reason(caplog):
    with caplog.at_level("INFO", logger=authentication.__name__):
        assert authentication.authentication_context_is_valid(
            "OP_MISSING_CONTEXT", "https://target.test", "missing-credential"
        ) is False

    assert "reason=context_missing" in caplog.text






def test_capture_browser_context_uses_named_storage_token_for_validation(tmp_path, monkeypatch):
    with running_authentication_app() as (target, state):
        state.sessions["session-alice"] = "alice"
        store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
        _activate_task(store, target)
        credential = store.store_credential(
            "OP_AUTH",
            {
                "credential_type": "username_password",
                "target": target,
                "role": "member",
                "payload": {"username": "alice", "password": "alice-password"},
                "origin": "provided",
                "management_policy": "user",
                "status": "unknown",
            },
        )
        monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
        monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
        monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
        monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_AUTH")
        authentication._CONTEXTS.clear()
        checkout_credential(credential["credential_id"], "authenticated coverage")
        set_task_auth_context([credential["credential_id"]])
        validation_url = f"{target}/tenants/tenant-a/records/1"
        store.upsert_authentication_flow(
            "OP_AUTH",
            {
                "target": target,
                "purpose": "authentication",
                "kind": "browser_form",
                "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
                "login_url": f"{target}/login",
                "validation_url": "",
                "storage_header_bindings": [
                    {"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"}
                ],
                "provenance": "observed_worker_flow",
            },
            status="discovered",
        )

        async def _cookies():
            return [{"name": "session", "value": "browser-session", "domain": "127.0.0.1", "path": "/"}]

        async def _token():
            return {"token": "session-alice"}

        class Request:
            url = validation_url

            async def all_headers(self):
                return {"Authorization": "session-alice"}

            async def response(self):
                return type("Response", (), {"status": 200})()

        class Browser:
            recent_requests = [Request()]
            context = type("Context", (), {"cookies": staticmethod(lambda _urls: _cookies())})()
            page = type("Page", (), {"evaluate": staticmethod(lambda _script, _key: _token())})()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            @asynccontextmanager
            async def timeout(self):
                yield

            async def run_in_browser_loop(self, function):
                return await function()

        monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())

        ready = json.loads(
            asyncio.run(
                authentication.capture_browser_authenticated_context(
                    credential["credential_id"],
                    [{"header_name": "Authorization", "storage_key": "token", "value_template": "Bearer {value}"}],
                    validation_url,
                )
            )
        )

        assert ready["authentication_ready"] is True
        assert authentication.authentication_context_is_valid("OP_AUTH", target, credential["credential_id"])


def test_capture_browser_context_prefers_observed_raw_authorization_header(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    credential = store.store_credential(
        "OP_AUTH",
        {
            "credential_type": "username_password",
            "target": target,
            "role": "member",
            "payload": {"username": "alice", "password": "alice-password"},
            "origin": "provided",
            "management_policy": "user",
            "status": "unknown",
        },
    )
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_AUTH")
    checkout_credential(credential["credential_id"], "authenticated coverage")
    set_task_auth_context([credential["credential_id"]])
    store.upsert_authentication_flow(
        "OP_AUTH",
        {
            "target": target,
            "purpose": "authentication",
            "kind": "browser_form",
            "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
            "login_url": f"{target}/login",
            "validation_url": "",
            "storage_header_bindings": [
                {"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"},
                {"header_name": "X-Session", "storage_key": "session_id", "value_template": "{value}"},
            ],
            "provenance": "observed_worker_flow",
        },
        status="discovered",
    )

    class Request:
        url = f"{target}/api/session"

        async def all_headers(self):
            return {"Authorization": "raw-jwt-value", "X-Session": "observed-session"}

        async def response(self):
            return type("Response", (), {"status": 200})()

    async def _storage_values():
        return {"token": "stored-jwt", "session_id": "stored-session"}

    class Browser:
        recent_requests = [Request()]
        context = type("Context", (), {"cookies": staticmethod(lambda _urls: _empty_cookies())})()
        page = type("Page", (), {"evaluate": staticmethod(lambda _script, _keys: _storage_values())})()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @asynccontextmanager
        async def timeout(self):
            yield

        async def run_in_browser_loop(self, function):
            return await function()

    async def _empty_cookies():
        return []

    captured: dict[str, str] = {}

    def store_context(_task, _record, _credential_id, _session, headers, _params, _validation_url, _marker=""):
        captured.update(headers)
        return "captured"

    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())
    monkeypatch.setattr(authentication, "_store_context", store_context)

    result = asyncio.run(
        authentication.capture_browser_authenticated_context(
            credential["credential_id"],
            [{"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"},
             {"header_name": "X-Session", "storage_key": "session_id", "value_template": "{value}"}],
            f"{target}/api/session",
        )
    )

    assert result == "captured"
    assert captured == {"Authorization": "raw-jwt-value", "X-Session": "observed-session"}




def test_capture_browser_context_runtime_schema_requires_controller_flow():
    schema = get_tool_spec(authentication.capture_browser_authenticated_context)["inputSchema"]["json"]

    assert schema["required"] == ["credential_id", "storage_header_bindings"]
    assert set(schema["properties"]) == {
        "credential_id", "storage_header_bindings", "request_header_names", "validation_url", "validation_marker"
    }
    authentication.capture_browser_authenticated_context._metadata.validate_input({
        "credential_id": "credential", "storage_header_bindings": [],
    })


def test_capture_browser_context_rejects_403_har_validation(monkeypatch):
    target = "https://target.test"

    class Task:
        task_uid = "task-auth"

    monkeypatch.setattr(
        authentication,
        "_active_record",
        lambda _credential_id: (Task(), {"target": target}, "credential"),
    )

    class Request:
        url = f"{target}/api/protected"

        async def all_headers(self):
            return {"Authorization": "raw-jwt-value"}

        async def response(self):
            return type("Response", (), {"status": 403})()

    class Browser:
        recent_requests = [Request()]
        class Context:
            @staticmethod
            async def cookies(_urls):
                return []

        context = Context()
        page = type("Page", (), {})()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @asynccontextmanager
        async def timeout(self):
            yield

        async def run_in_browser_loop(self, function):
            return await function()

    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())

    with pytest.raises(ValueError, match="successful same-target browser GET request"):
        asyncio.run(
            authentication.capture_browser_authenticated_context(
                "credential",
                [],
                f"{target}/api/protected",
            )
        )


@pytest.mark.parametrize(
    ("request_url", "status", "cookie_header", "accepted"),
    [
        ("https://target.test/api/protected", 200, "session=browser-session", True),
        (None, 200, "session=browser-session", False),
        ("https://target.test/api/other", 200, "session=browser-session", False),
        ("https://other.test/api/protected", 200, "session=browser-session", False),
        ("https://target.test/api/protected", 403, "session=browser-session", False),
        ("https://target.test/api/protected", 200, "session=wrong", False),
        ("https://target.test/api/protected", 200, "", False),
    ],
)
def test_cookie_only_browser_capture_requires_successful_request_with_session_cookie(
    monkeypatch, request_url, status, cookie_header, accepted
):
    target = "https://target.test"

    class Task:
        task_uid = "task-auth"

    class Request:
        url = request_url

        async def all_headers(self):
            return {"Cookie": cookie_header}

        async def response(self):
            return type("Response", (), {"status": status})()

    class Context:
        @staticmethod
        async def cookies(_urls):
            return [{"name": "session", "value": "browser-session", "domain": "target.test", "path": "/"}]

    class Browser:
        recent_requests = [Request()] if request_url else []
        context = Context()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @asynccontextmanager
        async def timeout(self):
            yield

        async def run_in_browser_loop(self, function):
            return await function()

    monkeypatch.setattr(authentication, "_active_record", lambda _id: (Task(), {"target": target}, "credential"))
    monkeypatch.setattr(authentication, "_store_context", lambda *_args: "captured")
    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())

    if accepted:
        assert asyncio.run(
            authentication.capture_browser_authenticated_context(
                "credential", [], f"{target}/api/protected"
            )
        ) == "captured"
    else:
        with pytest.raises(ValueError, match="successful same-target browser GET request"):
            asyncio.run(
                authentication.capture_browser_authenticated_context(
                    "credential", [], f"{target}/api/protected"
                )
            )


def test_bound_authentication_flow_recorder_rejects_controller_input_overrides(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    recorder = authentication.build_record_authentication_flow_tool(
        target=target,
        purpose="authentication",
        kind="browser_form",
    )

    with pytest.raises(TypeError):
        recorder(
            login_url=f"{target}/login",
            validation_url=f"{target}/protected",
            kind="api_form",
        )


def test_registration_flow_records_same_target_success_redirect_only(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")

    authentication.record_authentication_flow(
        purpose="registration",
        kind="browser_registration",
        login_url=f"{target}/register",
        success_redirect_url=f"{target}/login",
    )

    descriptor = store.list_authentication_flows(target, purpose="registration")[0]["descriptor"]
    assert descriptor["success_redirect_url"] == f"{target}/login"
    assert descriptor["flow_version"] == authentication.REGISTRATION_FLOW_VERSION
    with pytest.raises(ValueError, match="credential target boundary"):
        authentication.record_authentication_flow(
            purpose="registration",
            kind="browser_registration",
            login_url=f"{target}/register",
            success_redirect_url="https://idp.target.test/login",
        )




def test_authenticated_http_request_reports_missing_controller_authorization(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    credential = store.store_credential(
        "OP_AUTH",
        {
            "credential_type": "username_password",
            "target": target,
            "role": "member",
            "payload": {"username": "alice", "password": "alice-password"},
            "origin": "provided",
            "management_policy": "user",
            "status": "unknown",
        },
    )
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_AUTH")

    with pytest.raises(ValueError, match="AUTH_CONTEXT_UNAVAILABLE") as error:
        authentication.authenticated_http_request("GET", f"{target}/account", credential["credential_id"])

    assert "stop authenticated attempts" in str(error.value)
    assert "checkout_credential" not in str(error.value)
    with pytest.raises(ValueError, match="AUTH_CONTEXT_UNAVAILABLE"):
        authentication.authenticated_http_request("GET", f"{target}/account")






def test_authenticated_http_request_uses_hidden_api_key_context(tmp_path, monkeypatch):
    with running_authentication_app() as (target, _state):
        store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
        _activate_task(store, target)
        credential = store.store_credential(
            "OP_AUTH",
            {
                "credential_type": "api_key",
                "target": target,
                "role": "api_user",
                "payload": {"api_key": "fixture-api-key", "placement": "header", "name": "x-api-key"},
                "origin": "provided",
                "management_policy": "user",
                "status": "unknown",
            },
        )
        monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
        monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
        monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
        monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_AUTH")
        authentication._CONTEXTS.clear()
        checkout_credential(credential["credential_id"], "authenticated coverage")
        set_task_auth_context([credential["credential_id"]])

        ready = json.loads(
            authentication.establish_credential_authenticated_context(
                validation_url=f"{target}/api-key"
            )
        )
        response = json.loads(authentication.authenticated_http_request("GET", f"{target}/api-key"))

        assert ready["authentication_ready"] is True
        assert response["status_code"] == 200
        assert response["body"] == '{"authorized":true}'

        context = authentication._CONTEXTS[("OP_AUTH", target, credential["credential_id"])]
        context.validation_url = f"{target}/openapi.json"
        assert not authentication.authentication_context_is_valid("OP_AUTH", target, credential["credential_id"])
        with pytest.raises(ValueError, match="AUTH_CONTEXT_REFRESH_REQUIRED"):
            authentication.authenticated_http_request("GET", f"{target}/api-key")


def test_authenticated_http_request_exchanges_hidden_oauth_context(tmp_path, monkeypatch):
    with running_authentication_app() as (target, _state):
        store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
        _activate_task(store, target)
        credential = store.store_credential(
            "OP_AUTH",
            {
                "credential_type": "oauth2_client",
                "target": target,
                "role": "api_user",
                "payload": {
                    "client_id": "fixture-client",
                    "client_secret": "fixture-secret",
                    "client_auth_method": "client_secret_basic",
                    "scopes": [],
                    "audience": "",
                    "token_url": f"{target}/oauth/token",
                },
                "origin": "provided",
                "management_policy": "user",
                "status": "unknown",
            },
        )
        monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
        monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
        monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
        monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_AUTH")
        authentication._CONTEXTS.clear()
        checkout_credential(credential["credential_id"], "authenticated coverage")
        set_task_auth_context([credential["credential_id"]])

        authentication.establish_credential_authenticated_context(validation_url=f"{target}/oauth-protected")
        response = json.loads(authentication.authenticated_http_request("GET", f"{target}/oauth-protected"))

        assert response["status_code"] == 200
        assert response["body"] == '{"authorized":true}'


@pytest.mark.parametrize("purpose,kind,expected_version", [
    ("authentication", "browser_form", authentication.AUTHENTICATION_FLOW_VERSION),
    ("registration", "browser_registration", authentication.REGISTRATION_FLOW_VERSION),
])
def test_flow_recording_uses_independent_purpose_versions(tmp_path, monkeypatch, purpose, kind, expected_version):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "versions.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    authentication.record_authentication_flow(target=target, purpose=purpose, kind=kind, login_url=f"{target}/start")
    descriptor = store.list_authentication_flows(target, purpose=purpose)[0]["descriptor"]
    assert descriptor["flow_version"] == expected_version


@pytest.mark.parametrize("version,expected", [
    (authentication.AUTHENTICATION_FLOW_VERSION, True), (9, False), (8, False),
    (7, False), (6, False), (2, False), (1, False), (None, False),
])
def test_authentication_flow_version_rejects_legacy_and_registration_versions(version, expected):
    assert authentication._is_current_authentication_flow({"flow_version": version}) is expected


@pytest.mark.parametrize("purpose,kind", [
    ("authentication", "browser_form"), ("registration", "browser_registration"),
])
def test_discovery_recorder_uses_explicit_child_with_active_consumer(tmp_path, monkeypatch, purpose, kind):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    child = Task("discovery", "Discovery", "Map flow", make_acceptance("discovery"), 1, "active")
    store.store_task("OP_AUTH", child)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    candidates = []
    recorder = authentication.build_record_authentication_flow_tool(
        target=target, purpose=purpose, kind=kind, candidate_sink=candidates.append,
        task_uid=child.task_uid, operation_id="OP_AUTH",
    )
    schema = get_tool_spec(recorder)["inputSchema"]["json"]["properties"]
    assert not {"task_uid", "operation_id", "candidate_sink"} & schema.keys()
    assert json.loads(recorder(login_url=f"{target}/start"))["staged"]
    assert len(candidates) == 1
    assert not store.list_authentication_flows(target, purpose=purpose)
    store.store_task("OP_AUTH", Task(
        "discovery", "Discovery", "Map flow", make_acceptance("discovery"), 1, "done",
    ))
    with pytest.raises(ValueError, match="missing or inactive"):
        recorder(login_url=f"{target}/start")
    assert len(candidates) == 1
    with pytest.raises(ValueError, match="multiple active tasks"):
        store.store_task("OP_AUTH", child)
        authentication.record_authentication_flow(
            target=target, purpose=purpose, kind=kind, login_url=f"{target}/start",
        )


@pytest.mark.parametrize("overrides,message", [
    ({"task_uid": ""}, "requires controller"),
    ({"operation_id": ""}, "requires controller"),
    ({"credential_id": "secret-id"}, "no credential_id"),
    ({"task_uid": "missing"}, "missing or inactive"),
    ({"operation_id": "OP_OTHER"}, "operation mismatch"),
    ({"target": "https://other.test"}, "outside assigned task scope"),
])
def test_discovery_recorder_rejects_invalid_binding(tmp_path, monkeypatch, overrides, message):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    arguments = dict(
        target=target, purpose="authentication", kind="browser_form", candidate_sink=Mock(),
        task_uid="auth-task", operation_id="OP_AUTH",
    )
    arguments.update(overrides)
    with pytest.raises(ValueError, match=message):
        authentication.build_record_authentication_flow_tool(**arguments)(login_url=f"{target}/login")


def test_discovery_binding_requires_staging_sink():
    with pytest.raises(ValueError, match="only for worker staging"):
        authentication.build_record_authentication_flow_tool(
            target="https://target.test", purpose="authentication", kind="browser_form",
            task_uid="task", operation_id="OP_AUTH",
        )


def test_registration_discovery_requires_single_assigned_target(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    plan = store.get_plan("OP_AUTH")
    plan.targets.append(OperationTarget(target_id="other", value="https://other.test", type="network"))
    store.store_plan("OP_AUTH", plan)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    with pytest.raises(ValueError, match="one assigned task target"):
        authentication.build_record_authentication_flow_tool(
            target=target, purpose="registration", kind="browser_registration",
            task_uid="auth-task", operation_id="OP_AUTH", candidate_sink=Mock(),
        )(login_url=f"{target}/signup")



def test_credential_guard_reports_missing_active_task(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    store.store_task("OP_AUTH", Task("auth-task", "Auth", "Auth", make_acceptance("auth-task"), 1, "done"))
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    with pytest.raises(ValueError, match="no active task was found"):
        authentication.record_authentication_flow(
            target=target, purpose="authentication", kind="browser_form", login_url=f"{target}/login",
        )


@pytest.mark.parametrize("labels", [
    ["Email Address", "🔐 Password", "名前", "e\u0301", "a/b? [field]", "value@example.test", "  literal label  "],
    ["", "👩🏽‍💻", "line\nbreak", "x" * 256, "same label", "same label"],
])
def test_flow_labels_preserve_unicode_through_runtime_schema_and_storage(labels, tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    recorder = authentication.build_record_authentication_flow_tool(
        target=target, purpose="registration", kind="browser_registration",
    )
    args = {
        "login_url": f"{target}/signup",
        "identity_fields": labels, "required_fields": labels, "optional_fields": labels,
        "steps": [
            {"url": f"{target}/signup", "action": "navigation"},
            {"url": f"{target}/signup", "action": "form_submission", "field_names": labels},
            {"url": f"{target}/confirm", "action": "verification", "field_names": ["✅ Code"]},
        ],
    }
    validated = recorder._metadata.validate_input(args)
    recorder(**validated)
    flow = store.list_authentication_flows(target, purpose="registration")[0]["descriptor"]
    assert flow["steps"][1]["field_names"] == labels
    assert flow["steps"][2]["field_names"] == ["✅ Code"]
    for name in ("identity_fields", "required_fields", "optional_fields"):
        assert flow[name] == labels


@pytest.mark.parametrize("values", ["label", [1], [None], {"label": "value"}])
def test_flow_labels_reject_non_string_structure(values):
    with pytest.raises(ValueError, match="observed label strings"):
        authentication._normalize_flow_labels(values, label="identity_fields")


def test_flow_steps_reject_submitted_value_properties():
    with pytest.raises(ValueError, match="Extra inputs"):
        authentication._normalize_flow_steps("https://target.test", [{
            "url": "https://target.test/signup", "action": "form_submission",
            "field_names": ["🔐 Password"], "values": {"password": "secret"},
        }], [])
    assert authentication._normalize_flow_labels(None, label="fields") == []


@pytest.mark.parametrize("mode", [
    "header", "cookie", "missing", "explicit", "post", "foreign", "403", "fallback", "limit", "unexpected",
])
def test_browser_capture_selects_latest_authenticated_get_without_har_search(mode, monkeypatch):
    target = "https://target.test"
    cookies = [{"name": "session", "value": "sensitive", "domain": "target.test", "path": "/", "expires": 9999999999}]
    bindings = [] if mode == "cookie" else [
        {"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"},
    ]

    class Request:
        def __init__(self, url, method="GET", status=200, headers=None):
            self.url, self.method, self.status = url, method, status
            self.headers = headers or {}

        async def all_headers(self):
            return self.headers

        async def response(self):
            return SimpleNamespace(status=self.status)

    headers = {"cookie": "session=sensitive"} if mode == "cookie" else {"authorization": "sensitive"}
    candidate = Request(f"{target}/private?view=recent", headers=headers)
    if mode == "missing":
        candidate.headers = {}
    if mode == "post":
        candidate.method = "POST"
    if mode == "foreign":
        candidate.url = "https://other.test/private"
    if mode == "403":
        candidate.status = 403

    class Browser:
        recent_requests = [candidate, Request(f"{target}/login", "POST", headers=headers)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @asynccontextmanager
        async def timeout(self):
            yield

        async def run_in_browser_loop(self, function):
            return await function()

    async def read_cookies(_urls):
        return cookies

    async def read_storage(_script, _keys):
        return {"token": "sensitive"}

    browser = Browser()
    browser.context = SimpleNamespace(cookies=read_cookies)
    browser.page = SimpleNamespace(evaluate=read_storage)
    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: browser)
    monkeypatch.setattr(authentication, "_active_record", lambda *_args: (SimpleNamespace(task_uid="task"), {"target": target}, "credential"))
    capture = Mock(return_value="captured")
    monkeypatch.setattr(authentication, "_AUTHENTICATION_ATTEMPTS", {})
    if mode == "fallback":
        browser.recent_requests.insert(1, Request(f"{target}/public", headers=headers))
        capture.side_effect = [ValueError("authentication validation failed"), "captured"]
    elif mode == "limit":
        browser.recent_requests = [
            Request(f"{target}/resource/{index}", headers=headers) for index in range(12)
        ]
        browser.recent_requests.append(browser.recent_requests[-1])
        capture.side_effect = ValueError("authenticated response was not distinguished from the anonymous response")
    elif mode == "unexpected":
        capture.side_effect = ValueError("invalid session metadata")
    monkeypatch.setattr(authentication, "_store_context", capture)
    url = candidate.url if mode == "explicit" else ""
    if mode in {"missing", "post", "foreign", "403"}:
        with pytest.raises(ValueError, match="normal signed-in browser action") as error:
            asyncio.run(authentication.capture_browser_authenticated_context("credential", bindings, url))
        assert "sensitive" not in str(error.value)
        capture.assert_not_called()
    elif mode in {"limit", "unexpected"}:
        with pytest.raises(ValueError, match="AUTH_CAPTURE_PROOF_FAILED" if mode == "limit" else "invalid session"):
            asyncio.run(authentication.capture_browser_authenticated_context("credential", bindings, url))
        assert capture.call_count == (8 if mode == "limit" else 1)
    else:
        assert asyncio.run(authentication.capture_browser_authenticated_context("credential", bindings, url)) == "captured"
        assert capture.call_args.args[-2] == candidate.url
        if mode != "cookie":
            assert capture.call_args.args[4] == {"Authorization": "sensitive"}


@pytest.mark.parametrize("header_name", ["X-Session", "Tauser"])
def test_browser_capture_uses_observed_named_header_without_storage_key(header_name, monkeypatch):
    target = "https://target.test"

    class Request:
        url = f"{target}/private"
        method = "GET"

        async def all_headers(self):
            return {header_name.lower(): "opaque-session"}

        async def response(self):
            return SimpleNamespace(status=200)

    class Browser:
        recent_requests = [Request()]
        context = SimpleNamespace(cookies=AsyncMock(return_value=[]))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @asynccontextmanager
        async def timeout(self):
            yield

        async def run_in_browser_loop(self, function):
            return await function()

    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())
    monkeypatch.setattr(authentication, "_active_record", lambda *_args: (
        SimpleNamespace(task_uid="task"), {"target": target}, "credential",
    ))
    captured = Mock(return_value="captured")
    monkeypatch.setattr(authentication, "_store_context", captured)

    assert asyncio.run(authentication.capture_browser_authenticated_context(
        "credential", [], request_header_names=[header_name],
    )) == "captured"
    assert captured.call_args.args[4] == {header_name: "opaque-session"}


@pytest.mark.parametrize("reusable", [True, False])
def test_context_keeps_exact_runtime_url_and_only_validated_query_free_mapping(reusable, monkeypatch):
    target = "https://target.test"
    exact = f"{target}/private?view=required"
    store = SimpleNamespace(record_credential_usage=Mock())
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_QUERY")
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_validate", lambda context: context.validation_url == exact or reusable)
    monkeypatch.setattr(authentication, "_prove_authenticated_context", lambda context: context.validation_url == exact or reusable)
    result = authentication._store_context(
        SimpleNamespace(task_uid="task"), {"target": target}, "credential", authentication.requests.Session(),
        {"Authorization": "secret"}, {}, exact,
    )
    assert "view=required" not in result
    context = authentication._CONTEXTS[("OP_QUERY", target, "credential")]
    assert context.validation_url == exact
    assert authentication.authentication_context_validation_url("OP_QUERY", target, "credential") == (
        f"{target}/private" if reusable else ""
    )
    authentication._CONTEXTS.pop(("OP_QUERY", target, "credential"))


@pytest.mark.parametrize("url,expected", [
    ("https://target.test/private", "https://target.test/private"),
    ("https://target.test/private?secret=value", ""),
    ("https://target.test/private#fragment", ""),
])
def test_unclassified_context_never_exposes_query_in_flow_metadata(url, expected, monkeypatch):
    monkeypatch.setitem(authentication._CONTEXTS, ("OP_META", "https://target.test", "credential"),
                        authentication._AuthenticationContext(
                            "https://target.test", "credential", authentication.requests.Session(), {}, {}, url,
                        ))
    assert authentication.authentication_context_validation_url("OP_META", "https://target.test", "credential") == expected
    assert authentication.authentication_context_validation_url("OP_META", "https://target.test", "missing") == ""


@pytest.mark.parametrize("header_name", ["X-Session", "Tauser"])
def test_establish_context_rejects_public_validation_response(tmp_path, monkeypatch, header_name):
    with running_authentication_app() as (target, _state):
        store = SQLiteApplicationStore(str(tmp_path / "public.db"), target)
        _activate_task(store, target)
        credential = store.store_credential("OP_AUTH", {
            "credential_type": "api_key", "target": target, "role": "member",
            "payload": {"api_key": "opaque-fixture-value", "placement": "header", "name": header_name},
            "origin": "provided", "management_policy": "user", "status": "unknown",
        })
        for module in (authentication, credentials):
            monkeypatch.setattr(module, "_get_database_store", lambda: store)
            monkeypatch.setattr(module, "_operation_id", lambda: "OP_AUTH")
        authentication._CONTEXTS.clear()
        checkout_credential(credential["credential_id"], "validate credential")
        set_task_auth_context([credential["credential_id"]])
        with pytest.raises(ValueError, match="not distinguished from the anonymous response"):
            authentication.establish_credential_authenticated_context(validation_url=f"{target}/openapi.json")
        assert ("OP_AUTH", target, credential["credential_id"]) not in authentication._CONTEXTS


@pytest.mark.parametrize("transport", ["cookie", "X-Session", "Tauser", "combined"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("automatic", [False, True])
def test_browser_capture_proves_data_request_after_public_spa(
    tmp_path, monkeypatch, transport, explicit, automatic,
):
    with running_authentication_app() as (target, _state):
        header_name = "X-Session" if transport == "combined" else transport
        use_headers = transport != "cookie"
        use_cookies = transport in {"cookie", "combined"}
        request_headers = {header_name.lower(): "opaque-session"} if use_headers else {}
        if use_cookies:
            request_headers["cookie"] = "session=opaque-session"
        cookies = [{
            "name": "session", "value": "opaque-session", "domain": "127.0.0.1",
            "path": "/", "expires": 9999999999,
        }] if use_cookies else []

        class Request:
            method = "GET"

            def __init__(self, path):
                self.url = target + path

            async def all_headers(self):
                return request_headers

            async def response(self):
                return SimpleNamespace(status=200)

        class Browser:
            recent_requests = [Request("/session-data"), Request("/spa")]
            context = SimpleNamespace(cookies=AsyncMock(return_value=cookies))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            @asynccontextmanager
            async def timeout(self):
                yield

            async def run_in_browser_loop(self, function):
                return await function()

        browser = Browser()
        monkeypatch.setattr("modules.tools.browser.get_browser", lambda: browser)
        monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_CAPTURE")
        monkeypatch.setattr(authentication, "_operation_output_root", lambda: str(tmp_path))
        monkeypatch.setattr(authentication, "_AUTHENTICATION_ATTEMPTS", {})
        monkeypatch.setattr(authentication, "_CONTEXTS", {})
        monkeypatch.setattr(authentication, "_active_record", lambda *_args: (
            SimpleNamespace(task_uid="task"), {"target": target}, "credential",
        ))
        monkeypatch.setattr(authentication, "_get_database_store", lambda: SimpleNamespace(
            record_credential_usage=Mock(),
        ))
        names = [header_name] if use_headers else []
        if explicit:
            with pytest.raises(ValueError, match="AUTH_CAPTURE_PROOF_FAILED"):
                asyncio.run(authentication.capture_browser_authenticated_context(
                    "credential", [], target + "/spa", request_header_names=names,
                ))
            with pytest.raises(ValueError, match="AUTH_CAPTURE_NEW_EVIDENCE_REQUIRED"):
                asyncio.run(authentication.capture_browser_authenticated_context(
                    "credential", [], target + "/spa", request_header_names=names,
                ))
            # A newly observed request permits a new attempt, but still cannot prove the public shell.
            browser.recent_requests.append(Request("/spa"))
            with pytest.raises(ValueError, match="AUTH_CAPTURE_PROOF_FAILED"):
                asyncio.run(authentication.capture_browser_authenticated_context(
                    "credential", [], target + "/spa", request_header_names=names,
                ))
        async def capture():
            return await authentication.capture_browser_authenticated_context(
                "credential", [], request_header_names=names,
            )

        if automatic:
            from strands import tool

            from modules.agents.multi_agent_workflow import MultiAgentWorkflowController

            captured = []

            async def capture_completion():
                captured.append(await capture())

            @tool
            async def browser_perform_action(action: str) -> str:
                """Perform the final login action."""
                return "Signed-in page displayed"

            bound = MultiAgentWorkflowController._authentication_browser_capture_tool(
                browser_perform_action, capture_completion,
            )
            assert asyncio.run(bound(action="submit login")) == "Signed-in page displayed"
            result = json.loads(captured[0])
        else:
            result = json.loads(asyncio.run(capture()))
        assert result["status"] == "valid"
        assert result["validation_url"] == target + "/session-data"
        assert result["credential_header_names"] == names
        proof = json.loads(Path(result["evidence_refs"][0].removeprefix("artifact:")).read_text())
        assert proof["proof_method"] == "anonymous_access_denied"
        assert proof["anonymous_status"] == 401
        assert proof["authenticated_status"] == 200
        assert proof["credential_header_names"] == names
        assert proof["cookie_names"] == (["session"] if use_cookies else [])
        assert "opaque-session" not in json.dumps(proof)


@pytest.mark.parametrize("case", [
    "authorized", "worker", "operation", "foreign_credential_operation", "inactive", "unauthorized", "other_task",
    "missing", "invalid", "target", "mailbox",
])
def test_worker_resolver_requires_bound_authorization_without_checkout(tmp_path, monkeypatch, case):
    target = "https://worker.test"
    store = SQLiteApplicationStore(str(tmp_path / "worker-resolver.db"), target)
    _activate_task(store, target)
    credential = store.store_credential("OP_AUTH", {
        "credential_type": "email_login" if case == "mailbox" else "username_password",
        "target": "https://other.test" if case == "target" else target,
        "role": "member", "payload": {"username": "worker", "password": "worker-resolver-secret"},
        "origin": "provided", "management_policy": "user", "status": "unknown",
        "operation_id": "OTHER" if case == "foreign_credential_operation" else None,
    })
    identifier = credential["credential_id"]
    if case != "unauthorized":
        store.authorize_credential_for_task(
            "OP_AUTH", identifier, "auth-task",
            "authentication_worker" if case == "worker" else "reused_authenticated_context",
        )
    if case == "other_task":
        store.store_task("OP_AUTH", Task(
            "other-task", "Other auth", "Authenticate", make_acceptance("other-task"), 1, "active",
        ))
    if case == "missing":
        monkeypatch.setattr(store, "get_credential", lambda *_args, **_kwargs: None)
    if case == "invalid":
        store.record_credential_status("OP_AUTH", identifier, "invalid", "user", "invalid", [])
    if case == "inactive":
        store.patch_task("OP_AUTH", "auth-task", status="done")
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)
    monkeypatch.setattr(authentication, "_operation_id", lambda: "OP_AUTH")
    assert store.credential_ids_selected_by_task("OP_AUTH", "auth-task") == set()
    if case in {"authorized", "worker"}:
        task, record, resolved = authentication.resolve_worker_credential("OP_AUTH", "auth-task", target, identifier)
        assert task.task_uid == "auth-task" and resolved == identifier
        assert record["payload"]["password"] == "worker-resolver-secret"
        assert "worker-resolver-secret" not in redact_text("worker-resolver-secret")
        assert store.credential_ids_selected_by_task("OP_AUTH", "auth-task") == set()
        if case == "worker":
            with pytest.raises(ValueError, match="AUTH_CONTEXT_UNAVAILABLE"):
                credentials._active_credential_for_access(store, "OP_AUTH", identifier, "context")
            with pytest.raises(ValueError, match="checked out"):
                credentials._active_credential_for_access(store, "OP_AUTH", identifier, "checkout")
    else:
        with pytest.raises(ValueError):
            authentication.resolve_worker_credential("OTHER" if case == "operation" else "OP_AUTH",
                                                     "other-task" if case == "other_task" else "auth-task",
                                                     target, identifier)
