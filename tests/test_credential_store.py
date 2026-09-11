import json
import sqlite3
import stat
from email.message import EmailMessage

import pytest

from modules.tools.credentials import (
    canonicalize_credential_target,
    checkout_credential,
    extract_config_credentials,
    extract_objective_credentials,
    generate_mfa_code,
    generate_password,
    mark_credential_status,
    plan_access_control_comparisons,
    query_credentials,
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
    checkout_credential(totp_credential["credential_id"], "complete TOTP MFA")
    with pytest.raises(ValueError, match="TTL"):
        request_mfa_code(email_mfa_credential["credential_id"], ttl_seconds=29)
    with pytest.raises(ValueError, match="capture"):
        request_mfa_code(email_mfa_credential["credential_id"], code_pattern=r"(\\d+)")
    with pytest.raises(ValueError, match="generate_mfa_code"):
        request_mfa_code(totp_credential["credential_id"])
    monkeypatch.setattr("builtins.input", lambda: "not-a-code")
    with pytest.raises(ValueError, match="did not match"):
        request_mfa_code(email_mfa_credential["credential_id"])

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
            return "OK", [(b"RFC822", message.as_bytes())]

        def logout(self):
            self.logged_out = True

    fake_client = FakeImapClient("mail.example.test", 993)
    monkeypatch.setattr("modules.tools.credentials.imaplib.IMAP4_SSL", lambda *_args: fake_client)

    assert retrieve_email_mfa_code(mailbox_credential["credential_id"], sender_contains="noreply") == "654321"
    assert fake_client.logged_out is True
    with pytest.raises(ValueError, match="capture"):
        retrieve_email_mfa_code(mailbox_credential["credential_id"], code_pattern=r"(\\d+)")


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
    stored = json.loads(
        store_credential(
            "api_key", {"api_key": "secret", "name": "X-Key"}, "https://app.example.test", "reader", origin="found"
        )
    )
    queried = json.loads(query_credentials("https://app.example.test", role="reader", credential_type="api_key"))
    checkout_credential(stored["credential"]["credential_id"], "validate API key")
    status = json.loads(mark_credential_status(stored["credential"]["credential_id"], "valid", "login succeeded"))

    assert stored["stored"] is True
    assert queried["credentials"][0]["credential_id"] == stored["credential"]["credential_id"]
    assert "secret" not in json.dumps(queried)
    assert status["credential"]["status"] == "valid"
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
    with pytest.raises(ValueError, match="checked out"):
        mark_credential_status(credential["credential_id"], "invalid", "not tested")


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
    with pytest.raises(ValueError, match="invalid MFA code pattern"):
        request_mfa_code(plain_credential["credential_id"], code_pattern="[")
    with pytest.raises(ValueError, match="not configured by an active"):
        retrieve_email_mfa_code(plain_credential["credential_id"])
