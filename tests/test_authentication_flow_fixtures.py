import json

import requests

from modules.tools.credentials import (
    checkout_credential,
    exchange_oauth2_client_credentials,
    prepare_api_key_authentication,
    retrieve_email_mfa_code,
    store_user_credential,
)
from modules.tools.idor_specialist import idor_specialist
from modules.tools.memory import OperationPlan, OperationTarget, PlanPhase, SQLiteApplicationStore, Task
from tests.fixtures.authentication_app import FixtureImapClient, running_authentication_app
from tests.helpers.acceptance import make_acceptance


def _active_task(store, target):
    store.store_plan(
        "OP_E2E",
        OperationPlan(
            objective="Test fixture authentication",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="Authentication", status="active")],
            targets=[OperationTarget(target_id="fixture", value=target, type="network")],
        ),
    )
    store.store_task("OP_E2E", Task("task-e2e", "Authentication", "Authenticate", make_acceptance("task-e2e"), 1, "active"))


def test_fixture_registration_login_email_mfa_and_multi_tenant_idor():
    with running_authentication_app() as (base_url, state):
        registration_payload = {
            "username": "new-user",
            "password": "new-password",
            "email": "new@example.test",
            "tenant": "tenant-a",
        }
        registration = requests.post(
            f"{base_url}/register",
            json=registration_payload,
            timeout=3,
        )
        assert registration.status_code == 200
        assert requests.post(f"{base_url}/register", json=registration_payload, timeout=3).status_code == 409

        alice = requests.post(f"{base_url}/login", json={"username": "alice", "password": "alice-password"}, timeout=3)
        assert alice.status_code == 200
        assert requests.post(f"{base_url}/login", json={"username": "alice", "password": "wrong"}, timeout=3).status_code == 401
        assert requests.post(f"{base_url}/mfa/request", json={"username": "alice"}, timeout=3).json()["challenge"] == "email"
        assert "246810" in state.mailboxes["alice@example.test"][-1]
        assert requests.post(f"{base_url}/mfa/verify", json={"username": "alice", "code": "246810"}, timeout=3).status_code == 200
        assert requests.post(f"{base_url}/mfa/verify", json={"username": "alice", "code": "000000"}, timeout=3).status_code == 401

        idor = requests.get(
            f"{base_url}/tenants/tenant-b/records/bob-record",
            headers={"Authorization": f"Bearer {alice.json()['access_token']}"},
            timeout=3,
        )
        assert idor.status_code == 200
        assert idor.json()["tenant"] == "tenant-b"
        specialist_result = json.loads(
            idor_specialist(
                target_url=f"{base_url}/tenants/tenant-b/records/bob-record",
                test_type="authz_replay",
                login_url=f"{base_url}/login",
                multi_credentials=json.dumps(
                    [
                        {"username": "alice", "password": "alice-password"},
                        {"username": "bob", "password": "bob-password"},
                    ]
                ),
                auth_type="oauth",
            )
        )
        assert specialist_result["target"].endswith("/tenant-b/records/bob-record")
        assert not specialist_result["errors"]


def test_fixture_exercises_production_oauth_and_api_key_material(tmp_path, monkeypatch):
    with running_authentication_app() as (base_url, _state):
        store = SQLiteApplicationStore(str(tmp_path / "fixture.db"), base_url)
        _active_task(store, base_url)
        monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
        monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_E2E")
        oauth = store_user_credential(
            operation_id="OP_E2E",
            credential_type="oauth2_client",
            target=base_url,
            role="api_user",
            values={"client_id": "fixture-client", "client_secret": "fixture-secret", "token_url": f"{base_url}/oauth/token"},
        )
        api_key = store_user_credential(
            operation_id="OP_E2E",
            credential_type="api_key",
            target=base_url,
            role="api_user",
            values={"api_key": "fixture-api-key", "name": "X-API-Key"},
        )
        checkout_credential(oauth["credential_id"], "fixture OAuth")
        checkout_credential(api_key["credential_id"], "fixture API key")
        token = json.loads(exchange_oauth2_client_credentials(oauth["credential_id"]))
        assert token["access_token"] == "fixture-access-token"
        material = json.loads(prepare_api_key_authentication(api_key["credential_id"]))
        assert requests.get(f"{base_url}/api-key", headers=material["headers"], timeout=3).status_code == 200
        assert requests.get(f"{base_url}/api-key", timeout=3).status_code == 401


def test_fixture_email_mfa_uses_the_production_mailbox_adapter(tmp_path, monkeypatch):
    with running_authentication_app() as (base_url, state):
        store = SQLiteApplicationStore(str(tmp_path / "fixture.db"), base_url)
        _active_task(store, base_url)
        monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
        monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_E2E")
        mailbox = store_user_credential(
            operation_id="OP_E2E",
            credential_type="email_login",
            target=None,
            role="mailbox",
            values={
                "email": "alice@example.test",
                "password": "mailbox-password",
                "mailbox": {"host": "fixture-mail.example.test", "port": 993},
            },
        )
        target = store_user_credential(
            operation_id="OP_E2E",
            credential_type="username_password",
            target=base_url,
            role="member",
            values={
                "username": "alice",
                "password": "alice-password",
                "mfa": {"type": "email", "recipient": "alice@example.test", "mailbox_credential_id": mailbox["credential_id"]},
            },
        )
        checkout_credential(target["credential_id"], "fixture email MFA")
        assert requests.post(f"{base_url}/mfa/request", json={"username": "alice"}, timeout=3).status_code == 200
        monkeypatch.setattr("modules.tools.credentials.imaplib.IMAP4_SSL", lambda *_args: FixtureImapClient(state))

        assert retrieve_email_mfa_code(mailbox["credential_id"], subject_contains="MFA code") == "246810"
