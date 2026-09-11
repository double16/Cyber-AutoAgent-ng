import json
import stat

import pytest

from modules.tools.credentials import (
    canonicalize_credential_target,
    extract_objective_credentials,
    generate_mfa_code,
    store_user_credential,
    validate_credential_payload,
)
from modules.tools.memory import SQLiteApplicationStore, Task
from tests.helpers.acceptance import make_acceptance


def test_credential_store_scopes_exact_targets_and_user_aliases(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)

    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="HTTPS://Example.TEST:443/app",
        role="member",
        values={"username": "alice", "password": "correct horse battery staple"},
    )

    assert credential["target"] == "https://example.test/app"
    assert "payload" not in credential
    assert store.list_credentials("op-2", target="https://example.test/app", role="member") == [credential]
    assert store.list_credentials("op-2", target="https://example.test/other", role="member") == []

    store.add_credential_target_alias("https://example.test/app", "https://login.example.test/app")
    assert store.list_credentials("op-2", target="https://login.example.test/app")[0]["credential_id"] == credential["credential_id"]
    assert stat.S_IMODE((tmp_path / "credentials.db").stat().st_mode) == 0o600


def test_invalid_credential_is_retained_but_not_selectable(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://api.example.test",
        role="reader",
        values={"api_key": "not-logged", "placement": "header", "name": "X-API-Key"},
    )

    record = store.record_credential_status(
        "op-1", credential["credential_id"], "invalid", "operation", "401 from login", ["artifact:auth.txt"]
    )

    assert record["status"] == "invalid"
    assert record["invalid_at"]
    assert store.get_credential(credential["credential_id"], include_payload=True)["payload"]["api_key"] == "not-logged"
    assert store.list_credentials("op-1", target="https://api.example.test") == []


def test_typed_payload_validation_rejects_incomplete_and_unsafe_mailbox_configuration():
    with pytest.raises(ValueError, match="password"):
        validate_credential_payload("username_password", {"username": "alice"})
    with pytest.raises(ValueError, match="TLS"):
        validate_credential_payload(
            "email_login",
            {"email": "mfa@example.test", "password": "password", "mailbox": {"host": "mail.example.test", "tls": False}},
        )
    with pytest.raises(ValueError, match="credential_type"):
        validate_credential_payload("bearer", {"token": "secret"})


def test_objective_credentials_are_extracted_and_sanitized_before_agent_use():
    sanitized, drafts = extract_objective_credentials("Assess app. username=alice password=secret-value api_key=api-secret")

    assert "secret-value" not in sanitized
    assert "api-secret" not in sanitized
    assert {draft["credential_type"] for draft in drafts} == {"username_password", "api_key"}


def test_totp_generation_matches_rfc6238_vector(monkeypatch):
    monkeypatch.setattr("modules.tools.credentials.time.time", lambda: 59)

    code = generate_mfa_code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", digits=8, period=30, algorithm="SHA1")

    assert code == "94287082"


def test_credential_usage_is_report_safe(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="user",
        values={"username": "alice", "password": "must-not-leak"},
    )
    store.record_credential_usage("op-1", credential["credential_id"], task_uid="task-1", outcome="used")

    serialized = json.dumps(store.list_credential_usage("op-1"))
    assert "must-not-leak" not in serialized
    assert credential["credential_id"] in serialized


def test_target_canonicalization_preserves_non_default_port_and_path():
    assert canonicalize_credential_target("HTTPS://EXAMPLE.TEST:8443/Login?next=/dashboard#fragment") == (
        "https://example.test:8443/Login?next=/dashboard"
    )


def test_task_authentication_context_requires_a_credential_for_authenticated_work():
    acceptance = make_acceptance("task")
    with pytest.raises(ValueError, match="credential_ids"):
        Task("task", "Auth", "Check auth", acceptance, 1, "pending", auth_context={"mode": "authenticated"})

    task = Task(
        "task",
        "Auth",
        "Check auth",
        acceptance,
        1,
        "pending",
        auth_context={"mode": "authenticated", "credential_ids": ["credential-1"], "roles": ["reader"]},
    )

    assert task.auth_context["credential_ids"] == ["credential-1"]
