import json
import sqlite3
import stat

import pytest

from modules.tools.credentials import (
    canonicalize_credential_target,
    extract_config_credentials,
    extract_objective_credentials,
    generate_mfa_code,
    plan_access_control_comparisons,
    request_mfa_code,
    rotate_credential,
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


def test_react_credential_configuration_is_validated_without_returning_secrets_in_errors():
    drafts = extract_config_credentials(
        json.dumps(
            {
                "credentials": [
                    {
                        "credential_type": "api_key",
                        "role": "reader",
                        "values": {"api_key": "configured-secret", "placement": "header", "name": "X-API-Key"},
                    }
                ]
            }
        )
    )

    assert drafts[0]["credential_type"] == "api_key"
    assert drafts[0]["target"] is None
    with pytest.raises(ValueError, match="valid JSON") as error:
        extract_config_credentials("configured-secret")
    assert "configured-secret" not in str(error.value)


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


def test_credential_usage_requires_a_non_selection_event_for_authenticated_findings(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="user",
        values={"username": "alice", "password": "must-not-leak"},
    )

    store.record_credential_usage("op-1", credential["credential_id"], task_uid="task-1", outcome="selected")
    assert store.credential_ids_used_by_task("op-1", "task-1", [credential["credential_id"]]) == set()

    store.record_credential_usage("op-1", credential["credential_id"], task_uid="task-1", outcome="used")
    assert store.credential_ids_used_by_task("op-1", "task-1", [credential["credential_id"]]) == {
        credential["credential_id"]
    }


def test_access_control_comparisons_only_offer_distinct_account_role_or_tenant_pairs(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    member = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        account_label="alice",
        tenant_label="tenant-a",
        values={"username": "alice", "password": "must-not-leak"},
    )
    admin = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="admin",
        account_label="bob",
        tenant_label="tenant-b",
        values={"username": "bob", "password": "also-must-not-leak"},
    )

    result = json.loads(plan_access_control_comparisons("https://app.example.test"))

    assert {item["kind"] for item in result["comparisons"]} == {"account", "role", "tenant"}
    assert {member["credential_id"], admin["credential_id"]} == {
        result["comparisons"][0]["left_credential_id"], result["comparisons"][0]["right_credential_id"]
    }
    assert "must-not-leak" not in json.dumps(result)


def test_interactive_email_mfa_handoff_persists_only_challenge_metadata(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    events = []
    monkeypatch.setattr("modules.tools.credentials.emit_memory_event", events.append)
    monkeypatch.setattr("builtins.input", lambda: "123456")
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="user",
        values={
            "username": "alice",
            "password": "must-not-leak",
            "mfa": {"type": "email", "recipient": "alice@example.test", "mailbox_credential_id": "mailbox-1"},
        },
    )

    assert request_mfa_code(credential["credential_id"], prompt="Enter mail code") == "123456"
    assert events[0]["type"] == "user_handoff"
    assert events[0]["sensitive"] is True
    assert events[0]["handoff_kind"] == "mfa_code"

    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        row = connection.execute("SELECT status, metadata FROM mfa_challenges").fetchone()
    assert row is not None
    assert row[0] == "completed"
    assert "123456" not in row[1]
    assert "123456" not in json.dumps(store.list_credential_usage("op-1"))


def test_mfa_challenge_cannot_be_completed_after_expiry(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    credential = store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "reader",
            "payload": {"api_key": "not-logged", "placement": "header", "name": "X-API-Key", "prefix": ""},
            "origin": "provided",
            "management_policy": "user",
        },
    )
    challenge = store.create_mfa_challenge(
        "op-1", credential["credential_id"], "email", "2000-01-01T00:00:00+00:00", {"code_pattern": "\\d{6}"}
    )

    assert store.expire_mfa_challenges("op-1") == 1
    with pytest.raises(ValueError, match="not pending"):
        store.complete_mfa_challenge("op-1", challenge["challenge_id"])


def test_operation_managed_credential_rotation_preserves_retired_history(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    original = store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "reader",
            "payload": {"api_key": "old-key", "placement": "header", "name": "X-API-Key", "prefix": ""},
            "origin": "registered",
            "management_policy": "operation",
        },
    )

    result = json.loads(
        rotate_credential(
            original["credential_id"],
            {"api_key": "new-key", "placement": "header", "name": "X-API-Key"},
            "rotated after expiry",
        )
    )

    assert store.get_credential(original["credential_id"])["status"] == "retired"
    assert result["credential"]["supersedes_credential_id"] == original["credential_id"]
    assert store.get_credential(result["credential"]["credential_id"], include_payload=True)["payload"]["api_key"] == "new-key"


def test_rotation_rejects_user_provided_credentials(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://api.example.test",
        role="reader",
        values={"api_key": "user-key", "placement": "header", "name": "X-API-Key"},
    )

    with pytest.raises(ValueError, match="only be updated by the user"):
        rotate_credential(
            credential["credential_id"],
            {"api_key": "new-key", "placement": "header", "name": "X-API-Key"},
            "try agent rotation",
        )


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
