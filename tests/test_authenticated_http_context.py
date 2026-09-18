"""Tests for operation-local authenticated request contexts."""

from __future__ import annotations

import json

import pytest

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
        )
    )

    assert result["recorded"] is True
    descriptor = store.list_authentication_flows(target, purpose="registration")[0]["descriptor"]
    assert descriptor["url"] == f"{target}/register"
    assert descriptor["roles"] == ["member"]
    with pytest.raises(ValueError, match="query values"):
        authentication.record_authentication_flow(
            purpose="registration",
            kind="browser_registration",
            login_url=f"{target}/register?token=must-not-persist",
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
        ready = json.loads(
            authentication.ensure_authenticated_context(
                "",
                login_url=f"{target}/login",
                validation_url=f"{target}/tenants/tenant-a/records/1",
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
        with pytest.raises(ValueError, match="managed"):
            authentication.authenticated_http_request(
                "GET", f"{target}/tenants/tenant-a/records/2", credential["credential_id"],
                headers={"Authorization": "Bearer caller-supplied"},
            )
        assert authentication.authentication_context_is_valid("OP_AUTH", f"{target}/other", credential["credential_id"]) is False


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
