import base64
import json
import sqlite3
import stat
from datetime import UTC, datetime
from email.message import EmailMessage
from unittest.mock import MagicMock

import pytest
from strands import ToolContext

from modules.storage.credential_encryption import CredentialEncryptionError, CredentialPayloadCipher
from modules.tools.credentials import (
    build_checked_out_idor_login_contexts,
    canonicalize_credential_target,
    checkout_credential,
    exchange_oauth2_client_credentials,
    extract_config_credentials,
    extract_objective_credentials,
    generate_mfa_code,
    generate_password,
    mark_credential_status,
    plan_access_control_comparisons,
    plan_authenticated_coverage,
    prepare_api_key_authentication,
    prepare_login_form_authentication,
    query_credentials,
    record_checked_out_credential_usage,
    request_mfa_code,
    resolve_credential_target_for_operation,
    retrieve_email_mfa_code,
    rotate_credential,
    set_task_auth_context,
    store_credential,
    store_user_credential,
    validate_credential_payload,
)
from modules.tools.memory import OperationPlan, OperationTarget, PlanPhase, SQLiteApplicationStore, Task
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


def test_credential_storage_creates_an_initial_provenance_status_event(tmp_path):
    database_path = tmp_path / "credentials.db"
    store = SQLiteApplicationStore(str(database_path), "logical-target")
    provided = store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "reader",
            "payload": {"api_key": "provided-key", "placement": "header", "name": "X-API-Key"},
            "origin": "provided",
            "management_policy": "user",
        },
    )
    registered = store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "member",
            "payload": {"api_key": "registered-key", "placement": "header", "name": "X-API-Key"},
            "origin": "registered",
            "management_policy": "operation",
        },
    )

    with sqlite3.connect(database_path) as connection:
        events = connection.execute(
            "SELECT credential_id, status, actor, reason, evidence_refs "
            "FROM credential_status_events ORDER BY credential_id"
        ).fetchall()

    assert sorted(events) == sorted(
        [
            (provided["credential_id"], "unknown", "user", "Credential stored", "[]"),
            (registered["credential_id"], "unknown", "operation", "Credential stored", "[]"),
        ]
    )


def test_credential_payload_encryption_migrates_legacy_rows_and_fails_closed(tmp_path, monkeypatch):
    database_path = tmp_path / "credentials.db"
    plaintext_store = SQLiteApplicationStore(str(database_path), "logical-target")
    credential = plaintext_store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "reader",
            "payload": {"api_key": "legacy-secret", "placement": "header", "name": "X-API-Key"},
            "origin": "provided",
            "management_policy": "user",
        },
    )
    with sqlite3.connect(database_path) as connection:
        assert "legacy-secret" in connection.execute("SELECT payload FROM credential_records").fetchone()[0]

    key = base64.urlsafe_b64encode(b"a" * 32).decode("ascii")
    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_KEY", key)
    encrypted_store = SQLiteApplicationStore(str(database_path), "logical-target")
    encrypted = encrypted_store.get_credential(credential["credential_id"], include_payload=True)
    assert encrypted is not None
    assert encrypted["payload"]["api_key"] == "legacy-secret"
    with sqlite3.connect(database_path) as connection:
        stored_payload = connection.execute("SELECT payload FROM credential_records").fetchone()[0]
    assert stored_payload.startswith("enc:v1:")
    assert "legacy-secret" not in stored_payload

    newly_stored = encrypted_store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "writer",
            "payload": {"api_key": "new-secret", "placement": "header", "name": "X-API-Key"},
            "origin": "provided",
            "management_policy": "user",
        },
    )
    assert encrypted_store.get_credential(newly_stored["credential_id"], include_payload=True) is not None

    rotated_key = base64.urlsafe_b64encode(b"b" * 32).decode("ascii")
    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_KEY", rotated_key)
    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_PREVIOUS_KEYS", key)
    rotated_store = SQLiteApplicationStore(str(database_path), "logical-target")
    assert rotated_store.get_credential(credential["credential_id"], include_payload=True) is not None
    with sqlite3.connect(database_path) as connection:
        rotated_payload = connection.execute(
            "SELECT payload FROM credential_records WHERE credential_id = ?", (credential["credential_id"],)
        ).fetchone()[0]
    assert CredentialPayloadCipher(base64.urlsafe_b64decode(rotated_key)).decrypt(
        rotated_payload,
        logical_target="logical-target",
        credential_id=credential["credential_id"],
    )["api_key"] == "legacy-secret"

    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_KEY", key)
    monkeypatch.delenv("CYBER_CREDENTIAL_STORE_PREVIOUS_KEYS")
    with pytest.raises(CredentialEncryptionError, match="cannot be decrypted"):
        SQLiteApplicationStore(str(database_path), "logical-target")

    monkeypatch.delenv("CYBER_CREDENTIAL_STORE_KEY")
    unkeyed_store = SQLiteApplicationStore(str(database_path), "logical-target")
    with pytest.raises(CredentialEncryptionError, match="set CYBER_CREDENTIAL_STORE_KEY"):
        unkeyed_store.get_credential(credential["credential_id"], include_payload=True)


def test_credential_payload_encryption_rejects_invalid_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_KEY", "not a base64 key")
    with pytest.raises(CredentialEncryptionError, match="base64"):
        SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")

    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_KEY", base64.urlsafe_b64encode(b"short").decode("ascii"))
    with pytest.raises(CredentialEncryptionError, match="exactly 32 bytes"):
        SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")

    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_KEY", base64.urlsafe_b64encode(b"a" * 32).decode("ascii"))
    monkeypatch.setenv("CYBER_CREDENTIAL_STORE_PREVIOUS_KEYS", "not a base64 key")
    with pytest.raises(CredentialEncryptionError, match="PREVIOUS_KEYS.*base64"):
        SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")


def test_credential_payload_cipher_rejects_malformed_tampered_and_non_object_payloads():
    cipher = CredentialPayloadCipher(b"a" * 32)
    encrypted = cipher.encrypt({"api_key": "secret"}, logical_target="logical-target", credential_id="credential-1")

    assert cipher.decrypt(encrypted, logical_target="logical-target", credential_id="credential-1") == {"api_key": "secret"}
    with pytest.raises(CredentialEncryptionError, match="not encrypted"):
        cipher.decrypt("{}", logical_target="logical-target", credential_id="credential-1")
    with pytest.raises(CredentialEncryptionError, match="cannot be decrypted"):
        cipher.decrypt("enc:v1:", logical_target="logical-target", credential_id="credential-1")
    with pytest.raises(CredentialEncryptionError, match="cannot be decrypted"):
        cipher.decrypt(encrypted, logical_target="logical-target", credential_id="credential-2")

    nonce = b"0" * 12
    list_payload = cipher._cipher.encrypt(
        nonce,
        b"[]",
        cipher._associated_data("logical-target", "credential-1"),
    )
    non_object_envelope = "enc:v1:" + base64.urlsafe_b64encode(nonce + list_payload).decode("ascii")
    with pytest.raises(CredentialEncryptionError, match="must decode to an object"):
        cipher.decrypt(non_object_envelope, logical_target="logical-target", credential_id="credential-1")


def test_credential_payload_cipher_accepts_a_previous_rotation_key():
    old_cipher = CredentialPayloadCipher(b"a" * 32)
    encrypted = old_cipher.encrypt({"password": "secret"}, logical_target="logical-target", credential_id="credential-1")
    rotated_cipher = CredentialPayloadCipher(b"b" * 32, (b"a" * 32,))

    payload, key_index = rotated_cipher.decrypt_with_key_index(
        encrypted,
        logical_target="logical-target",
        credential_id="credential-1",
    )

    assert payload == {"password": "secret"}
    assert key_index == 1


def test_credential_listing_prefers_operation_scope_then_reusable_registered_accounts(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    provided = store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "reader",
            "payload": {"api_key": "provided-key", "placement": "header", "name": "X-API-Key"},
            "origin": "provided",
            "management_policy": "user",
        },
    )
    registered = store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "reader",
            "payload": {"api_key": "registered-key", "placement": "header", "name": "X-API-Key"},
            "origin": "registered",
            "management_policy": "operation",
        },
    )
    operation_scoped = store.store_credential(
        "op-2",
        {
            "credential_type": "api_key",
            "target": "https://api.example.test",
            "role": "reader",
            "operation_id": "op-2",
            "payload": {"api_key": "operation-key", "placement": "header", "name": "X-API-Key"},
            "origin": "found",
            "management_policy": "operation",
        },
    )

    assert [record["credential_id"] for record in store.list_credentials("op-3")] == [
        registered["credential_id"],
        provided["credential_id"],
    ]
    assert [record["credential_id"] for record in store.list_credentials("op-2")] == [
        operation_scoped["credential_id"],
        registered["credential_id"],
        provided["credential_id"],
    ]


def test_credential_storage_rejects_targets_outside_an_existing_operation_plan(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    _store_active_target_task(store)

    with pytest.raises(ValueError, match="exact resolved operation target"):
        store_user_credential(
            operation_id="op-1",
            credential_type="api_key",
            target="https://api.other.example.test",
            role="reader",
            values={"api_key": "must-not-store", "placement": "header", "name": "X-API-Key"},
        )


def test_credential_operation_scope_can_bind_to_the_current_importing_operation(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)

    credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://app.example.test",
        role="reader",
        operation_scope="current",
        values={"api_key": "scoped-secret", "placement": "header", "name": "X-API-Key"},
    )

    assert store.get_credential(credential["credential_id"])["operation_id"] == "op-1"
    assert store.list_credentials("op-2", target="https://app.example.test") == []
    with pytest.raises(ValueError, match="operation_scope"):
        store_user_credential(
            operation_id="op-1",
            credential_type="api_key",
            target="https://app.example.test",
            role="reader",
            operation_scope="op-2",
            values={"api_key": "must-not-store", "placement": "header", "name": "X-API-Key"},
        )


def test_configuration_credentials_require_exact_preflight_resolved_targets():
    targets = [OperationTarget(target_id="app", value="https://app.example.test", type="network")]

    assert (
        resolve_credential_target_for_operation("api_key", "HTTPS://APP.EXAMPLE.TEST:443", targets)
        == "https://app.example.test"
    )
    assert resolve_credential_target_for_operation("email_login", None, targets) is None
    with pytest.raises(ValueError, match="exact resolved operation target"):
        resolve_credential_target_for_operation("api_key", "https://other.example.test", targets)
    with pytest.raises(ValueError, match="must not specify a target"):
        resolve_credential_target_for_operation("email_login", "https://app.example.test", targets)


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
    sanitized, drafts = extract_objective_credentials(
        "Assess app. username=alice password=secret-value email=bob@example.test password=email-secret "
        "api_key=api-secret oauth2_client_id=client-id oauth2_client_secret=client-secret"
    )

    assert "secret-value" not in sanitized
    assert "email-secret" not in sanitized
    assert "api-secret" not in sanitized
    assert "client-secret" not in sanitized
    assert [draft["credential_type"] for draft in drafts] == [
        "username_password",
        "username_password",
        "api_key",
        "oauth2_client",
    ]
    assert drafts[1]["values"]["email"] == "bob@example.test"
    assert "token_url" not in drafts[-1]["values"]


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
    with pytest.raises(ValueError, match="credentials array"):
        extract_config_credentials(json.dumps({"credentials": {}}))
    with pytest.raises(ValueError, match="must be an object"):
        extract_config_credentials(json.dumps(["not-a-credential"]))
    with pytest.raises(ValueError, match="requires role"):
        extract_config_credentials(
            json.dumps([{"credential_type": "api_key", "values": {"api_key": "secret", "name": "X-Key"}}])
        )


def test_totp_generation_matches_rfc6238_vector(monkeypatch):
    monkeypatch.setattr("modules.tools.credentials.time.time", lambda: 59)

    code = generate_mfa_code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", digits=8, period=30, algorithm="SHA1")

    assert code == "94287082"


def test_totp_generation_uses_checked_out_credential_without_persisting_secret(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    monkeypatch.setattr("modules.tools.credentials.time.time", lambda: 59)
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        values={
            "username": "alice",
            "password": "secret",
            "mfa": {
                "type": "totp",
                "secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
                "digits": 8,
                "period": 30,
                "algorithm": "SHA1",
            },
        },
    )
    _store_active_target_task(store)
    checkout_credential(credential["credential_id"], "complete TOTP MFA")

    assert generate_mfa_code(credential_id=credential["credential_id"]) == "94287082"
    usage = store.list_credential_usage("op-1")

    assert any(record["authentication_mode"] == "mfa" and record["outcome"] == "used" for record in usage)
    assert "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ" not in json.dumps(usage)
    with pytest.raises(ValueError, match="cannot be combined"):
        generate_mfa_code("GEZDGNBV", credential_id=credential["credential_id"])
    with pytest.raises(ValueError, match="checked-out credential_id"):
        generate_mfa_code("GEZDGNBV", tool_context=MagicMock(spec=ToolContext))


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
    _store_active_target_task(store)
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
    coverage = json.loads(plan_authenticated_coverage("https://app.example.test"))

    assert {item["kind"] for item in result["comparisons"]} == {"account", "role", "tenant"}
    assert {member["credential_id"], admin["credential_id"]} == {
        result["comparisons"][0]["left_credential_id"], result["comparisons"][0]["right_credential_id"]
    }
    assert "must-not-leak" not in json.dumps(result)
    assert coverage["unauthenticated_required"] is True
    assert {item["credential_id"] for item in coverage["authenticated_contexts"]} == {
        member["credential_id"],
        admin["credential_id"],
    }
    assert {item["kind"] for item in coverage["comparisons"]} == {"account", "role", "tenant"}
    assert coverage["authenticated_coverage_gap"] is None
    assert coverage["comparison_coverage_gap"] is None
    assert "must-not-leak" not in json.dumps(coverage)


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
    _store_active_target_task(store)
    checkout_credential(credential["credential_id"], "complete email MFA")

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


def test_mfa_tools_reject_invalid_handoff_states_and_retrieve_unique_mail_code(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    email_mfa_credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        values={
            "username": "alice",
            "password": "secret",
            "mfa": {"type": "email", "recipient": "alice@example.test", "mailbox_credential_id": "mailbox-1"},
        },
    )
    totp_credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="reader",
        values={"username": "reader", "password": "secret", "mfa": {"type": "totp", "secret": "GEZDGNBV"}},
    )
    _store_active_target_task(store)
    checkout_credential(email_mfa_credential["credential_id"], "complete email MFA")
    with pytest.raises(ValueError, match="checked out"):
        generate_mfa_code(credential_id=totp_credential["credential_id"])
    checkout_credential(totp_credential["credential_id"], "complete TOTP MFA")
    with pytest.raises(ValueError, match="configured TOTP"):
        generate_mfa_code(credential_id=email_mfa_credential["credential_id"])
    with pytest.raises(ValueError, match="TTL"):
        request_mfa_code(email_mfa_credential["credential_id"], ttl_seconds=29)
    with pytest.raises(ValueError, match="capture"):
        request_mfa_code(email_mfa_credential["credential_id"], code_pattern=r"(\\d+)")
    with pytest.raises(ValueError, match="generate_mfa_code"):
        request_mfa_code(totp_credential["credential_id"])
    monkeypatch.setattr("builtins.input", lambda: "not-a-code")
    with pytest.raises(ValueError, match="did not match"):
        request_mfa_code(email_mfa_credential["credential_id"])
    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        assert connection.execute(
            "SELECT status FROM mfa_challenges ORDER BY created_at DESC LIMIT 1"
        ).fetchone() == ("blocked",)

    mailbox_credential = store_user_credential(
        operation_id="op-1",
        credential_type="email_login",
        target=None,
        role=None,
        values={
            "email": "mfa@example.test",
            "password": "mail-secret",
            "mailbox": {"host": "mail.example.test", "folder": "Codes"},
        },
    )
    mailbox_bound_credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        values={
            "username": "alice-mail",
            "password": "secret",
            "mfa": {
                "type": "email",
                "recipient": "alice@example.test",
                "mailbox_credential_id": mailbox_credential["credential_id"],
            },
        },
    )
    checkout_credential(mailbox_bound_credential["credential_id"], "retrieve email MFA")
    message = EmailMessage()
    message["From"] = "noreply@example.test"
    message["Subject"] = "Your code"
    message.set_content("Use 654321 to continue")

    class FakeImapClient:
        logged_out = False
        internaldate = datetime.now(UTC).strftime("%d-%b-%Y %H:%M:%S +0000")

        def __init__(self, host, port):
            assert (host, port) == ("mail.example.test", 993)

        def login(self, email_address, password):
            assert (email_address, password) == ("mfa@example.test", "mail-secret")

        def select(self, folder, readonly):
            assert (folder, readonly) == ("Codes", True)
            return "OK", [b""]

        def search(self, _charset, _query):
            return "OK", [b"1"]

        def fetch(self, message_id, _query):
            assert message_id == b"1"
            assert _query == "(RFC822 INTERNALDATE)"
            metadata = f'1 (INTERNALDATE "{self.internaldate}" RFC822'.encode("ascii")
            return "OK", [(metadata, message.as_bytes())]

        def logout(self):
            self.logged_out = True

    fake_client = FakeImapClient("mail.example.test", 993)
    monkeypatch.setattr("modules.tools.credentials.imaplib.IMAP4_SSL", lambda *_args: fake_client)

    assert retrieve_email_mfa_code(mailbox_credential["credential_id"], sender_contains="noreply") == "654321"
    assert fake_client.logged_out is True
    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        challenge = connection.execute(
            "SELECT credential_id, status, metadata FROM mfa_challenges "
            "WHERE credential_id = ? ORDER BY created_at DESC LIMIT 1",
            (mailbox_bound_credential["credential_id"],),
        ).fetchone()
    assert challenge is not None
    assert challenge[:2] == (mailbox_bound_credential["credential_id"], "completed")
    assert "654321" not in challenge[2]
    mfa_usage = [
        entry
        for entry in store.list_credential_usage("op-1")
        if entry["authentication_mode"] == "mfa" and entry["outcome"] == "used"
    ]
    assert {entry["credential_id"] for entry in mfa_usage} >= {
        mailbox_bound_credential["credential_id"],
        mailbox_credential["credential_id"],
    }
    monkeypatch.setattr(fake_client, "search", lambda *_args: ("NO", []))
    with pytest.raises(ValueError, match="search failed"):
        retrieve_email_mfa_code(mailbox_credential["credential_id"])
    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        assert connection.execute(
            "SELECT status FROM mfa_challenges WHERE credential_id = ? ORDER BY created_at DESC LIMIT 1",
            (mailbox_bound_credential["credential_id"],),
        ).fetchone() == ("blocked",)
    monkeypatch.setattr(fake_client, "search", lambda *_args: ("OK", [b"1"]))
    fake_client.internaldate = "01-Jan-2000 00:00:00 +0000"
    with pytest.raises(ValueError, match="missing or ambiguous"):
        retrieve_email_mfa_code(mailbox_credential["credential_id"])
    with pytest.raises(ValueError, match="capture"):
        retrieve_email_mfa_code(mailbox_credential["credential_id"], code_pattern=r"(\\d+)")
    with pytest.raises(ValueError, match="TTL"):
        retrieve_email_mfa_code(mailbox_credential["credential_id"], ttl_seconds=29)


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
    assert store.block_mfa_challenge("op-1", challenge["challenge_id"]) is False

    current = store.create_mfa_challenge(
        "op-1", credential["credential_id"], "email", "2099-01-01T00:00:00+00:00", {"code_pattern": "\\d{6}"}
    )
    assert store.block_mfa_challenge("op-1", current["challenge_id"]) is True
    with pytest.raises(ValueError, match="not pending"):
        store.complete_mfa_challenge("op-1", current["challenge_id"])


def test_operation_managed_credential_rotation_preserves_retired_history(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store, target="https://api.example.test")
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
    checkout_credential(original["credential_id"], "rotate expired API key")

    with pytest.raises(ValueError, match="durable evidence"):
        rotate_credential(
            original["credential_id"],
            {"api_key": "new-key", "placement": "header", "name": "X-API-Key"},
            "rotated after expiry",
        )
    result = json.loads(
        rotate_credential(
            original["credential_id"],
            {"api_key": "new-key", "placement": "header", "name": "X-API-Key"},
            "rotated after expiry",
            evidence_refs=["artifact:artifacts/credential-rotation.txt"],
        )
    )

    assert store.get_credential(original["credential_id"])["status"] == "retired"
    assert result["credential"]["supersedes_credential_id"] == original["credential_id"]
    assert store.get_credential(result["credential"]["credential_id"], include_payload=True)["payload"]["api_key"] == "new-key"
    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        events = connection.execute(
            "SELECT status, evidence_refs FROM credential_status_events WHERE credential_id IN (?, ?) ORDER BY created_at",
            (original["credential_id"], result["credential"]["credential_id"]),
        ).fetchall()
    assert ("retired", '["artifact:artifacts/credential-rotation.txt"]') in events
    assert ("unknown", '["artifact:artifacts/credential-rotation.txt"]') in events


def test_rotation_rejects_user_provided_credentials(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store, target="https://api.example.test")
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://api.example.test",
        role="reader",
        values={"api_key": "user-key", "placement": "header", "name": "X-API-Key"},
    )
    checkout_credential(credential["credential_id"], "validate rotation policy")

    with pytest.raises(ValueError, match="only be updated by the user"):
        rotate_credential(
            credential["credential_id"],
            {"api_key": "new-key", "placement": "header", "name": "X-API-Key"},
            "try agent rotation",
            evidence_refs=["artifact:artifacts/credential-rotation.txt"],
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


def _store_active_target_task(store, operation_id="op-1", target="https://app.example.test"):
    store.store_plan(
        operation_id,
        OperationPlan(
            objective="Assess application",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="Testing", status="active")],
            targets=[OperationTarget(target_id="app", value=target, type="network")],
        ),
    )
    task = Task(
        "task-1",
        "Authenticated testing",
        "Test the application",
        make_acceptance("task-1"),
        1,
        "active",
        target_scope="subset",
        target_ids=["app"],
    )
    store.store_task(operation_id, task)
    return task


def test_credential_payload_validation_supports_all_types_and_mfa_variants():
    username_password = validate_credential_payload(
        "username_password",
        {
            "username": "alice",
            "password": "not-logged",
            "email": "alice@example.test",
            "mfa": {"type": "totp", "secret": "GEZDGNBVGY3TQOJQ", "digits": 8, "algorithm": "sha256"},
        },
    )
    email_login = validate_credential_payload(
        "email_login",
        {
            "email": "mfa@example.test",
            "password": "not-logged",
            "mailbox": {"host": "mail.example.test", "folder": "Codes"},
            "mfa": {"type": "email", "recipient": "mfa@example.test", "mailbox_credential_id": "mailbox-1"},
        },
    )
    api_key = validate_credential_payload(
        "api_key", {"api_key": "not-logged", "placement": "query", "name": "key", "prefix": "Bearer "}
    )
    oauth = validate_credential_payload(
        "oauth2_client",
        {
            "client_id": "client",
            "client_secret": "not-logged",
            "token_url": "HTTPS://AUTH.EXAMPLE.TEST:443/token",
            "scopes": ["read", " ", "write"],
            "audience": "app",
        },
    )

    assert username_password["mfa"]["algorithm"] == "SHA256"
    assert email_login["mailbox"] == {"host": "mail.example.test", "port": 993, "tls": True, "folder": "Codes"}
    assert "token_url" not in validate_credential_payload(
        "oauth2_client", {"client_id": "client", "client_secret": "secret"}
    )
    assert api_key["placement"] == "query"
    assert oauth["token_url"] == "https://auth.example.test/token"
    assert oauth["scopes"] == ["read", "write"]

    with pytest.raises(ValueError, match="TOTP"):
        validate_credential_payload(
            "username_password",
            {"username": "alice", "password": "secret", "mfa": {"type": "totp", "secret": "a", "digits": 4}},
        )
    with pytest.raises(ValueError, match="placement"):
        validate_credential_payload("api_key", {"api_key": "secret", "placement": "cookie", "name": "key"})
    with pytest.raises(ValueError, match="mailbox"):
        validate_credential_payload("email_login", {"email": "a@example.test", "password": "secret"})
    with pytest.raises(ValueError, match="mfa must be an object"):
        validate_credential_payload("username_password", {"username": "alice", "password": "secret", "mfa": "totp"})
    with pytest.raises(ValueError, match="MFA type"):
        validate_credential_payload(
            "username_password", {"username": "alice", "password": "secret", "mfa": {"type": "push"}}
        )
    with pytest.raises(ValueError, match="values must be an object"):
        validate_credential_payload("api_key", None)
    with pytest.raises(ValueError, match="client_auth_method"):
        validate_credential_payload(
            "oauth2_client",
            {"client_id": "client", "client_secret": "secret", "client_auth_method": "private_key_jwt"},
        )


def test_oauth2_client_credentials_exchange_is_target_scoped_and_transient(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store, target="https://api.example.test")
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="oauth2_client",
        target="https://api.example.test",
        role="api_user",
        values={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_url": "https://api.example.test/oauth/token",
            "scopes": ["read", "write"],
            "audience": "api",
        },
    )
    checkout_credential(credential["credential_id"], "exchange OAuth client credentials")
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"access_token":"transient-token","token_type":"Bearer","expires_in":300}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr("modules.tools.credentials.urlrequest.urlopen", fake_urlopen)
    result = json.loads(exchange_oauth2_client_credentials(credential["credential_id"]))

    request, timeout = requests[0]
    assert timeout == 15
    assert request.full_url == "https://api.example.test/oauth/token"
    assert request.get_header("Authorization").startswith("Basic ")
    assert request.data == b"grant_type=client_credentials&scope=read+write&audience=api"
    assert result == {
        "credential_id": credential["credential_id"],
        "access_token": "transient-token",
        "token_type": "Bearer",
        "expires_in": 300,
    }
    usage = store.list_credential_usage("op-1")
    assert [entry["outcome"] for entry in usage] == ["selected", "succeeded"]
    assert "client-secret" not in json.dumps(usage)


def test_oauth2_client_credentials_exchange_rejects_unsafe_or_invalid_responses(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store, target="https://api.example.test")
    unsafe_credential = store_user_credential(
        operation_id="op-1",
        credential_type="oauth2_client",
        target="https://api.example.test",
        role="api_user",
        values={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_url": "https://identity.example.test/oauth/token",
        },
    )
    checkout_credential(unsafe_credential["credential_id"], "reject unsafe token endpoint")
    with pytest.raises(ValueError, match="target origin"):
        exchange_oauth2_client_credentials(unsafe_credential["credential_id"])
    with pytest.raises(ValueError, match="timeout"):
        exchange_oauth2_client_credentials(unsafe_credential["credential_id"], timeout_seconds=0)

    credential = store_user_credential(
        operation_id="op-1",
        credential_type="oauth2_client",
        target="https://api.example.test",
        role="api_user",
        values={
            "client_id": "second-client",
            "client_secret": "second-secret",
            "token_url": "https://api.example.test/oauth/token",
            "client_auth_method": "client_secret_post",
        },
    )
    checkout_credential(credential["credential_id"], "validate OAuth response")

    class MissingTokenResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(
        "modules.tools.credentials.urlrequest.urlopen", lambda *_args, **_kwargs: MissingTokenResponse()
    )
    with pytest.raises(ValueError, match="access_token"):
        exchange_oauth2_client_credentials(credential["credential_id"])
    assert store.get_credential(credential["credential_id"])["status"] == "unknown"
    assert [entry["outcome"] for entry in store.list_credential_usage("op-1")] == [
        "selected",
        "selected",
        "failed",
    ]


def test_api_key_request_material_uses_configured_target_placement(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store)
    header_credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://app.example.test",
        role="reader",
        values={"api_key": "header-key", "name": "X-API-Key", "prefix": "Bearer "},
    )
    query_credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://app.example.test",
        role="member",
        values={"api_key": "query-key", "placement": "query", "name": "api_key"},
    )
    password_credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="admin",
        values={"username": "alice", "password": "password"},
    )
    checkout_credential(header_credential["credential_id"], "prepare header API key")
    checkout_credential(query_credential["credential_id"], "prepare query API key")
    checkout_credential(password_credential["credential_id"], "reject non-API credential")

    header = json.loads(prepare_api_key_authentication(header_credential["credential_id"]))
    query = json.loads(prepare_api_key_authentication(query_credential["credential_id"]))

    assert header == {
        "credential_id": header_credential["credential_id"],
        "headers": {"X-API-Key": "Bearer header-key"},
        "query_params": {},
    }
    assert query == {
        "credential_id": query_credential["credential_id"],
        "headers": {},
        "query_params": {"api_key": "query-key"},
    }
    with pytest.raises(ValueError, match="api_key"):
        prepare_api_key_authentication(password_credential["credential_id"])


def test_login_form_material_requires_observed_distinct_fields_and_a_checked_out_login(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store)
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        values={"username": "alice", "password": "password", "email": "alice@example.test"},
    )
    api_credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://app.example.test",
        role="reader",
        values={"api_key": "key", "name": "X-API-Key"},
    )
    checkout_credential(credential["credential_id"], "prepare login form")
    checkout_credential(api_credential["credential_id"], "reject non-login credential")

    result = json.loads(
        prepare_login_form_authentication(
            credential["credential_id"],
            username_field="user[email]",
            password_field="password",
            email_field="contact_email",
        )
    )

    assert result == {
        "credential_id": credential["credential_id"],
        "form_fields": {
            "user[email]": "alice",
            "password": "password",
            "contact_email": "alice@example.test",
        },
    }
    with pytest.raises(ValueError, match="must differ"):
        prepare_login_form_authentication(credential["credential_id"], username_field="login", password_field="login")
    with pytest.raises(ValueError, match="invalid"):
        prepare_login_form_authentication(credential["credential_id"], username_field="[name*=user]")
    with pytest.raises(ValueError, match="username_password"):
        prepare_login_form_authentication(api_credential["credential_id"])


def test_credential_target_and_store_boundaries_cover_invalid_inputs_and_labels(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    invalid_targets = [
        ("", "required"),
        ("https://example.test:bad", "invalid port"),
        ("https://a:b@example.test", "userinfo"),
    ]
    for target, message in invalid_targets:
        with pytest.raises(ValueError, match=message):
            canonicalize_credential_target(target)
    assert canonicalize_credential_target("host.example.test///") == "host.example.test"

    with pytest.raises(ValueError, match="target is required"):
        store_user_credential(
            operation_id="op-1",
            credential_type="api_key",
            target=None,
            role="reader",
            values={"api_key": "secret", "name": "X-Key"},
        )
    with pytest.raises(ValueError, match="role is required"):
        store_user_credential(
            operation_id="op-1",
            credential_type="api_key",
            target="https://app.example.test",
            role=None,
            values={"api_key": "secret", "name": "X-Key"},
        )
    record = store_user_credential(
        operation_id="op-1",
        credential_type="email_login",
        target=None,
        role=None,
        account_label="mail-account",
        tenant_label="mail-tenant",
        values={"email": "mfa@example.test", "password": "secret", "mailbox": {"host": "mail.example.test"}},
    )

    payload = store.get_credential(record["credential_id"], include_payload=True)["payload"]
    assert payload["account_label"] == "mail-account"
    assert payload["tenant_label"] == "mail-tenant"


def test_agent_credential_store_query_and_status_tools_are_safe(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")

    with pytest.raises(ValueError, match="origin"):
        store_credential(
            "api_key",
            {"api_key": "secret", "name": "X-Key"},
            "https://app.example.test",
            "reader",
            origin="provided",
        )
    _store_active_target_task(store)
    with pytest.raises(ValueError, match="durable evidence"):
        store_credential(
            "api_key", {"api_key": "secret", "name": "X-Key"}, "https://app.example.test", "reader", origin="found"
        )
    with pytest.raises(ValueError, match="must be a list"):
        store_credential(
            "api_key",
            {"api_key": "secret", "name": "X-Key"},
            "https://app.example.test",
            "reader",
            origin="found",
            evidence_refs="artifact:artifacts/credential-discovery.txt",
        )
    with pytest.raises(ValueError, match="must use durable"):
        store_credential(
            "api_key",
            {"api_key": "secret", "name": "X-Key"},
            "https://app.example.test",
            "reader",
            origin="found",
            evidence_refs=["https://app.example.test/leak"],
        )
    stored = json.loads(
        store_credential(
            "api_key",
            {"api_key": "secret", "name": "X-Key"},
            "https://app.example.test",
            "reader",
            origin="found",
            evidence_refs=["artifact:artifacts/credential-discovery.txt", "artifact:artifacts/credential-discovery.txt"],
        )
    )
    registered = json.loads(
        store_credential(
            "api_key",
            {"api_key": "registered-secret", "name": "X-Registered-Key"},
            "https://app.example.test",
            "reader",
            origin="registered",
            evidence_refs=["memory:registration-result"],
        )
    )
    queried = json.loads(query_credentials("https://app.example.test", role="reader", credential_type="api_key"))
    checkout_credential(stored["credential"]["credential_id"], "validate API key")
    with pytest.raises(ValueError, match="status reason"):
        mark_credential_status(
            stored["credential"]["credential_id"],
            "valid",
            "",
            evidence_refs=["artifact:artifacts/authentication-result.txt"],
        )
    with pytest.raises(ValueError, match="durable evidence"):
        mark_credential_status(stored["credential"]["credential_id"], "valid", "login succeeded")
    with pytest.raises(ValueError, match="must use durable"):
        mark_credential_status(
            stored["credential"]["credential_id"],
            "valid",
            "login succeeded",
            evidence_refs=["https://app.example.test/login"],
        )
    status = json.loads(
        mark_credential_status(
            stored["credential"]["credential_id"],
            "valid",
            "login succeeded",
            evidence_refs=["artifact:artifacts/authentication-result.txt"],
        )
    )

    assert stored["stored"] is True
    assert registered["stored"] is True
    assert {record["credential_id"] for record in queried["credentials"]} == {
        stored["credential"]["credential_id"],
        registered["credential"]["credential_id"],
    }
    assert "secret" not in json.dumps(queried)
    assert status["credential"]["status"] == "valid"
    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        event = connection.execute(
            "SELECT evidence_refs FROM credential_status_events WHERE credential_id = ? ORDER BY created_at DESC LIMIT 1",
            (stored["credential"]["credential_id"],),
        ).fetchone()
    assert json.loads(event[0]) == ["artifact:artifacts/authentication-result.txt"]
    with pytest.raises(ValueError, match="unknown credential_type"):
        query_credentials(credential_type="bearer")
    with pytest.raises(ValueError, match="unknown credential status"):
        mark_credential_status(stored["credential"]["credential_id"], "broken", "no")


def test_agent_credential_tools_reject_access_outside_the_active_task_scope(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")

    with pytest.raises(ValueError, match="active task"):
        query_credentials()
    _store_active_target_task(store)
    empty_coverage = json.loads(plan_authenticated_coverage("https://app.example.test"))
    assert empty_coverage["authenticated_contexts"] == []
    assert empty_coverage["authenticated_coverage_gap"] == "No eligible credentials for authenticated testing."
    assert empty_coverage["comparison_coverage_gap"] == "No distinct eligible account, role, or tenant credential pair."
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://app.example.test",
        role="reader",
        values={"api_key": "secret", "name": "X-Key"},
    )
    with pytest.raises(ValueError, match="outside the active task"):
        query_credentials("https://other.example.test")
    with pytest.raises(ValueError, match="outside the active task"):
        plan_access_control_comparisons("https://other.example.test")
    with pytest.raises(ValueError, match="outside the active task"):
        plan_authenticated_coverage("https://other.example.test")
    with pytest.raises(ValueError, match="checked out"):
        mark_credential_status(
            credential["credential_id"],
            "invalid",
            "not tested",
            evidence_refs=["artifact:artifacts/authentication-result.txt"],
        )


def test_password_and_totp_tools_reject_bad_inputs_and_generate_compliant_password():
    password = generate_password(24)

    assert len(password) == 24
    assert any(character.islower() for character in password)
    assert any(character.isupper() for character in password)
    assert any(character.isdigit() for character in password)
    assert any(character in "!@#$%^&*-_" for character in password)
    with pytest.raises(ValueError, match="between"):
        generate_password(8)
    with pytest.raises(ValueError, match="digits"):
        generate_mfa_code("GEZDGNBV", digits=4)
    with pytest.raises(ValueError, match="algorithm"):
        generate_mfa_code("GEZDGNBV", algorithm="MD5")
    with pytest.raises(ValueError, match="provisioning"):
        generate_mfa_code("not base32!")
    with pytest.raises(ValueError, match="required"):
        generate_mfa_code()


def test_checkout_and_auth_context_reject_missing_task_scope_and_unavailable_credentials(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://app.example.test",
        role="reader",
        values={"api_key": "secret", "name": "X-Key"},
    )

    with pytest.raises(ValueError, match="at least one"):
        set_task_auth_context([])
    with pytest.raises(ValueError, match="active task"):
        set_task_auth_context([credential["credential_id"]])
    with pytest.raises(ValueError, match="purpose"):
        checkout_credential(credential["credential_id"], "")
    with pytest.raises(ValueError, match="unavailable"):
        checkout_credential("missing", "login")

    _store_active_target_task(store)
    foreign_operation_credential = store.store_credential(
        "op-1",
        {
            "credential_type": "api_key",
            "target": "https://app.example.test",
            "role": "reader",
            "operation_id": "other-operation",
            "payload": {"api_key": "secret", "name": "X-Key", "placement": "header", "prefix": ""},
            "origin": "provided",
            "management_policy": "user",
        },
    )
    with pytest.raises(ValueError, match="another operation"):
        checkout_credential(foreign_operation_credential["credential_id"], "login")
    store.record_credential_status("op-1", credential["credential_id"], "invalid", "operation", "failed", [])
    with pytest.raises(ValueError, match="unavailable"):
        checkout_credential(credential["credential_id"], "login")


def test_checkout_and_auth_context_bind_credentials_to_the_active_task_target(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    plan = OperationPlan(
        objective="Assess application",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Testing", status="active")],
        targets=[OperationTarget(target_id="app", value="https://app.example.test", type="network")],
    )
    store.store_plan("op-1", plan)
    task = Task(
        "task-1",
        "Authenticated testing",
        "Test the application",
        make_acceptance("task-1"),
        1,
        "active",
        target_scope="subset",
        target_ids=["app"],
    )
    store.store_task("op-1", task)
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        account_label="alice",
        tenant_label="tenant-a",
        values={"username": "alice", "password": "must-not-leak"},
    )

    with pytest.raises(ValueError, match="checked out"):
        set_task_auth_context([credential["credential_id"]])
    checkout_credential(credential["credential_id"], "authenticated comparison")
    result = json.loads(set_task_auth_context([credential["credential_id"]]))

    assert result["auth_context"] == {
        "mode": "authenticated",
        "credential_ids": [credential["credential_id"]],
        "roles": ["member"],
        "account_labels": ["alice"],
        "tenant_labels": ["tenant-a"],
    }
    assert "must-not-leak" not in json.dumps(result)


def test_idor_login_contexts_use_checked_out_task_bound_comparison_pair(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store)
    alice = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        account_label="alice",
        values={"username": "alice", "password": "alice-secret", "email": "alice@example.test"},
    )
    bob = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        account_label="bob",
        values={"username": "bob", "password": "bob-secret", "email": "bob@example.test"},
    )
    checkout_credential(alice["credential_id"], "authenticated IDOR comparison")
    checkout_credential(bob["credential_id"], "authenticated IDOR comparison")
    set_task_auth_context([alice["credential_id"], bob["credential_id"]])

    contexts = build_checked_out_idor_login_contexts(
        [alice["credential_id"], bob["credential_id"]],
        "https://app.example.test/api/accounts/1",
        "https://app.example.test/login",
        username_field="user[name]",
        password_field="pass",
        email_field="email",
        extra_form_fields={"csrf_token": "abc"},
    )

    assert [context["credential_id"] for context in contexts] == [alice["credential_id"], bob["credential_id"]]
    assert contexts[0]["form_fields"] == {
        "csrf_token": "abc",
        "user[name]": "alice",
        "pass": "alice-secret",
        "email": "alice@example.test",
    }
    assert "alice-secret" not in json.dumps(
        {"credential_id": contexts[0]["credential_id"], "credential_id_2": contexts[1]["credential_id"]}
    )


def test_idor_login_contexts_reject_unplanned_unbound_and_cross_origin_pairs(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store)
    left = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        values={"username": "left", "password": "secret"},
    )
    right = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        values={"username": "right", "password": "secret"},
    )
    checkout_credential(left["credential_id"], "authenticated IDOR comparison")
    checkout_credential(right["credential_id"], "authenticated IDOR comparison")
    with pytest.raises(ValueError, match="authenticated task context"):
        build_checked_out_idor_login_contexts(
            [left["credential_id"], right["credential_id"]],
            "https://app.example.test/api",
            "https://app.example.test/login",
        )

    set_task_auth_context([left["credential_id"], right["credential_id"]])
    with pytest.raises(ValueError, match="planned account, role, or tenant"):
        build_checked_out_idor_login_contexts(
            [left["credential_id"], right["credential_id"]],
            "https://app.example.test/api",
            "https://app.example.test/login",
        )
    with pytest.raises(ValueError, match="login_url must share"):
        build_checked_out_idor_login_contexts(
            [left["credential_id"], right["credential_id"]],
            "https://app.example.test/api",
            "https://other.example.test/login",
        )
    with pytest.raises(ValueError, match="must not override"):
        build_checked_out_idor_login_contexts(
            [left["credential_id"], right["credential_id"]],
            "https://app.example.test/api",
            "https://app.example.test/login",
            extra_form_fields={"username": "override"},
        )


def test_record_checked_out_credential_usage_records_idor_outcomes(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    _store_active_target_task(store)
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        account_label="alice",
        values={"username": "alice", "password": "secret"},
    )
    checkout_credential(credential["credential_id"], "authenticated IDOR comparison")

    record_checked_out_credential_usage([credential["credential_id"]], "succeeded")

    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        usage = connection.execute(
            "SELECT credential_id, task_uid, authentication_mode, outcome FROM credential_usage_records "
            "WHERE outcome = 'succeeded'"
        ).fetchall()

    assert usage == [(credential["credential_id"], "task-1", "authenticated", "succeeded")]
    with pytest.raises(ValueError, match="outcome"):
        record_checked_out_credential_usage([credential["credential_id"]], "unknown")


def test_checkout_rejects_a_credential_outside_the_active_task_target(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    store.store_plan(
        "op-1",
        OperationPlan(
            objective="Assess application",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="Testing", status="active")],
            targets=[OperationTarget(target_id="app", value="https://app.example.test", type="network")],
        ),
    )
    store.store_task(
        "op-1",
        Task(
            "task-1",
            "Testing",
            "Test the application",
            make_acceptance("task-1"),
            1,
            "active",
            target_scope="subset",
            target_ids=["app"],
        ),
    )
    with pytest.raises(ValueError, match="exact resolved operation target"):
        store_user_credential(
            operation_id="op-1",
            credential_type="api_key",
            target="https://api.other.example.test",
            role="reader",
            values={"api_key": "must-not-leak", "placement": "header", "name": "X-API-Key"},
        )


def test_mfa_tools_reject_missing_configuration_and_mailbox_errors(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "op-1")
    plain_credential = store_user_credential(
        operation_id="op-1",
        credential_type="username_password",
        target="https://app.example.test",
        role="member",
        values={"username": "alice", "password": "secret"},
    )
    with pytest.raises(ValueError, match="active task"):
        request_mfa_code("missing")
    _store_active_target_task(store)
    with pytest.raises(ValueError, match="checked out"):
        request_mfa_code(plain_credential["credential_id"])
    checkout_credential(plain_credential["credential_id"], "test MFA configuration")
    with pytest.raises(ValueError, match="configured MFA"):
        request_mfa_code(plain_credential["credential_id"])
    with pytest.raises(ValueError, match="configured TOTP"):
        generate_mfa_code(credential_id=plain_credential["credential_id"])
    with pytest.raises(ValueError, match="invalid MFA code pattern"):
        request_mfa_code(plain_credential["credential_id"], code_pattern="[")
    with pytest.raises(ValueError, match="not configured by an active"):
        retrieve_email_mfa_code(plain_credential["credential_id"])
