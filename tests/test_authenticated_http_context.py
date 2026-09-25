"""Tests for operation-local authenticated request contexts."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from modules.handlers.utils import get_tool_spec
from modules.tools import authentication
from modules.tools.credentials import checkout_credential, set_task_auth_context
from modules.tools.memory import OperationPlan, OperationTarget, PlanPhase, SQLiteApplicationStore, Task
from modules.utils.redaction import REDACTED, redact_text
from tests.fixtures.authentication_app import running_authentication_app
from tests.helpers.acceptance import make_acceptance


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

    with pytest.raises(ValueError, match="observed discovery provenance"):
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
    assert descriptor["flow_version"] == authentication.AUTHENTICATION_FLOW_VERSION
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
            kind="api_form",
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
    ensure_schema = get_tool_spec(authentication.ensure_authenticated_context)["inputSchema"]["json"]
    assert ensure_schema["properties"]["request_format"]["enum"] == ["", "json", "form"]
    runtime_input = authentication.record_authentication_flow._metadata.validate_input(
        {"target": target, "purpose": "login", "kind": "API form", "request_format": "application/json"}
    )
    assert runtime_input["purpose"] == "login"
    assert runtime_input["kind"] == "API form"

    result = json.loads(
        authentication.record_authentication_flow(
            target=target,
            purpose="login",
            kind="API form",
            login_url=f"{target}/login",
            validation_url=f"{target}/protected",
            request_format="application/json",
        )
    )
    descriptor = store.list_authentication_flows(target, purpose="authentication")[0]["descriptor"]
    assert result["recorded"] is True
    assert descriptor["purpose"] == "authentication"
    assert descriptor["kind"] == "api_form"
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
            kind="api_form",
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
        "login_url", "validation_url", "authorization_storage_key", "storage_header_bindings", "request_format"
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


def test_stale_api_form_flow_is_not_reused(monkeypatch):
    monkeypatch.setattr(
        authentication,
        "_get_database_store",
        lambda: type("Store", (), {
            "list_authentication_flows": lambda _self, _target, purpose="authentication", **_kwargs: [{
                "status": "validated",
                "descriptor": {
                    "kind": "api_form",
                    "flow_version": authentication.AUTHENTICATION_FLOW_VERSION - 1,
                    "login_url": "https://target.test/login",
                    "validation_url": "https://target.test/account",
                },
            }]
        })(),
    )

    with pytest.raises(ValueError, match="selected api_form"):
        authentication._stored_api_form_flow("https://target.test", "stale-flow")


def test_selected_api_form_flow_does_not_query_sibling_descriptors(monkeypatch):
    selected = {
        "kind": "api_form",
        "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
        "login_url": "https://target.test/login-v2",
        "validation_url": "https://target.test/account",
    }
    monkeypatch.setattr(
        authentication,
        "_get_database_store",
        lambda: type("Store", (), {
            "list_authentication_flows": lambda _self, *_args, **_kwargs: [
                {
                    "flow_id": "old-flow",
                    "status": "validated",
                    "descriptor": {**selected, "login_url": "https://target.test/login-v1"},
                },
                {"flow_id": "selected-flow", "status": "discovered", "descriptor": selected},
            ]
        })(),
    )

    assert authentication._stored_api_form_flow("https://target.test", "selected-flow") == selected


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
        flow = store.upsert_authentication_flow(
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
                "provenance": "observed_flow_discovery",
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
                    credential["credential_id"], flow["flow_id"], validation_url
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
    flow = store.upsert_authentication_flow(
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
            "provenance": "observed_flow_discovery",
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

    def store_context(_task, _record, _credential_id, _session, headers, _params, _validation_url):
        captured.update(headers)
        return "captured"

    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())
    monkeypatch.setattr(authentication, "_store_context", store_context)

    result = asyncio.run(
        authentication.capture_browser_authenticated_context(
            credential["credential_id"],
            flow["flow_id"],
            f"{target}/api/session",
        )
    )

    assert result == "captured"
    assert captured == {"Authorization": "raw-jwt-value", "X-Session": "observed-session"}


def test_capture_browser_context_resolves_bindings_from_selected_flow(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
    _activate_task(store, target)
    flow = store.upsert_authentication_flow(
        "OP_AUTH",
        {
            "target": target,
            "purpose": "authentication",
            "kind": "browser_form",
            "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
            "login_url": f"{target}/login",
            "validation_url": "",
            "storage_header_bindings": [
                {"header_name": "Authorization", "storage_key": "token", "value_template": "Bearer {value}"}
            ],
            "provenance": "observed_flow_discovery",
        },
        status="discovered",
    )
    monkeypatch.setattr(authentication, "_get_database_store", lambda: store)

    assert authentication._capture_storage_header_bindings(target, flow["flow_id"]) == [
        {"header_name": "Authorization", "storage_key": "token", "value_template": "Bearer {value}"}
    ]
    with pytest.raises(ValueError, match="unavailable"):
        authentication._capture_storage_header_bindings(target, "missing-flow")


def test_capture_browser_context_runtime_schema_requires_controller_flow():
    schema = get_tool_spec(authentication.capture_browser_authenticated_context)["inputSchema"]["json"]

    assert schema["required"] == ["credential_id", "flow_id", "validation_url"]
    assert set(schema["properties"]) == {"credential_id", "flow_id", "validation_url"}


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
    monkeypatch.setattr(
        authentication,
        "_stored_browser_flow",
        lambda *_args: {
            "storage_header_bindings": [
                {"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"}
            ],
            "authorization_storage_key": "",
        },
    )

    with pytest.raises(ValueError, match="successful browser request"):
        asyncio.run(
            authentication.capture_browser_authenticated_context(
                "credential",
                "flow-1",
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
    monkeypatch.setattr(authentication, "_capture_storage_header_bindings", lambda *_args: [])
    monkeypatch.setattr(authentication, "_store_context", lambda *_args: "captured")
    monkeypatch.setattr("modules.tools.browser.get_browser", lambda: Browser())

    if accepted:
        assert asyncio.run(
            authentication.capture_browser_authenticated_context(
                "credential", "flow-1", f"{target}/api/protected"
            )
        ) == "captured"
    else:
        with pytest.raises(ValueError, match="successful browser request"):
            asyncio.run(
                authentication.capture_browser_authenticated_context(
                    "credential", "flow-1", f"{target}/api/protected"
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
    assert descriptor["flow_version"] == authentication.AUTHENTICATION_FLOW_VERSION
    with pytest.raises(ValueError, match="credential target boundary"):
        authentication.record_authentication_flow(
            purpose="registration",
            kind="browser_registration",
            login_url=f"{target}/register",
            success_redirect_url="https://idp.target.test/login",
        )


def test_authenticated_http_request_reuses_hidden_bearer_context(tmp_path, monkeypatch):
    with running_authentication_app() as (target, _state):
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
        with pytest.raises(ValueError, match="AUTH_CONTEXT_UNAVAILABLE") as error:
            authentication.authenticated_http_request(
                "GET", f"{target}/tenants/tenant-a/records/2", credential["credential_id"]
            )
        assert "stop authenticated attempts" in str(error.value)
        assert "ensure_authenticated_context" not in str(error.value)
        stored_flow = store.upsert_authentication_flow(
            "OP_AUTH",
            {
                "target": target,
                "purpose": "authentication",
                "kind": "api_form",
                "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
                "login_url": f"{target}/login",
                "validation_url": f"{target}/tenants/tenant-a/records/1",
                "request_format": "json",
                "provenance": "observed_flow_discovery",
            },
            status="validated",
        )
        ready = json.loads(
            authentication.ensure_authenticated_context(
                "",
                flow_id=stored_flow["flow_id"],
            )
        )

        assert ready["authentication_ready"] is True
        assert authentication.authentication_context_is_valid("OP_AUTH", target, credential["credential_id"])
        response = json.loads(
            authentication.authenticated_http_request(
                "GET", f"{target}/tenants/tenant-a/records/2"
            )
        )
        assert response["status_code"] == 200
        assert response["body"] == '{"tenant":"tenant-a","record_id":"2","requested_by":"alice"}'
        monkeypatch.setattr(authentication, "_validate", lambda _context: False)
        with pytest.raises(ValueError, match="AUTH_CONTEXT_UNAVAILABLE"):
            authentication.authenticated_http_request(
                "GET", f"{target}/tenants/tenant-a/records/2", credential["credential_id"]
            )
        with pytest.raises(ValueError, match="managed"):
            authentication.authenticated_http_request(
                "GET", f"{target}/tenants/tenant-a/records/2", credential["credential_id"],
                headers={"Authorization": "Bearer caller-supplied"},
            )
        assert authentication.authentication_context_is_valid("OP_AUTH", f"{target}/other", credential["credential_id"]) is False


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


def test_authenticated_http_request_accepts_controller_authorization_for_reused_context(tmp_path, monkeypatch):
    with running_authentication_app() as (target, _state):
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
        stored_flow = store.upsert_authentication_flow(
            "OP_AUTH",
            {
                "target": target,
                "purpose": "authentication",
                "kind": "api_form",
                "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
                "login_url": f"{target}/login",
                "validation_url": f"{target}/tenants/tenant-a/records/1",
                "request_format": "json",
                "provenance": "observed_flow_discovery",
            },
            status="validated",
        )
        authentication.ensure_authenticated_context("", flow_id=stored_flow["flow_id"])
        store.patch_task("OP_AUTH", "auth-task", status="done")
        store.store_task(
            "OP_AUTH",
            Task("reused-auth-task", "Reused auth", "Use the validated context", make_acceptance("reused-auth-task"), 1, "active"),
        )
        store.authorize_credential_for_task(
            "OP_AUTH",
            credential["credential_id"],
            "reused-auth-task",
            "reused_authenticated_context",
        )

        with pytest.raises(ValueError, match="checked out by the active task"):
            authentication.ensure_authenticated_context(
                credential["credential_id"], flow_id=stored_flow["flow_id"]
            )
        with pytest.raises(ValueError, match="checked out by the active task"):
            authentication.establish_credential_authenticated_context(
                credential["credential_id"], f"{target}/tenants/tenant-a/records/1"
            )

        get_credential = store.get_credential
        payload_reads = []

        def recorded_get_credential(credential_id, *, include_payload=False):
            payload_reads.append(include_payload)
            return get_credential(credential_id, include_payload=include_payload)

        monkeypatch.setattr(store, "get_credential", recorded_get_credential)
        response = json.loads(
            authentication.authenticated_http_request(
                "GET", f"{target}/tenants/tenant-a/records/2", credential["credential_id"]
            )
        )

        assert response["status_code"] == 200
        assert payload_reads and not any(payload_reads)
        assert store.credential_ids_selected_by_task("OP_AUTH", "reused-auth-task") == set()
        assert store.credential_ids_authorized_by_task("OP_AUTH", "reused-auth-task") == {
            credential["credential_id"]
        }


def test_authenticated_context_rejects_unsuccessful_login(tmp_path, monkeypatch):
    with running_authentication_app() as (target, _state):
        store = SQLiteApplicationStore(str(tmp_path / "auth.db"), target)
        _activate_task(store, target)
        credential = store.store_credential(
            "OP_AUTH",
            {
                "credential_type": "username_password",
                "target": target,
                "role": "member",
                "payload": {"username": "alice", "password": "wrong"},
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

        with pytest.raises(ValueError, match="rejected"):
            authentication.ensure_authenticated_context(
                credential["credential_id"],
                login_url=f"{target}/login",
                validation_url=f"{target}/tenants/tenant-a/records/1",
            )
        attempt = authentication.authentication_attempt_result("OP_AUTH", target, credential["credential_id"])
        assert attempt is not None
        assert attempt["outcome"] == "credential_rejected"
        assert attempt["status"] == 401


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
