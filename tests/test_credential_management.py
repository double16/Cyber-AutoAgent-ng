import json

import pytest

from modules.tools.credential_management import execute_credential_management
from modules.tools.memory import SQLiteApplicationStore


def _operation_credential(store):
    return store.store_credential(
        "OP_SOURCE",
        {
            "credential_type": "api_key",
            "target": "https://app.example.test",
            "role": "reader",
            "payload": {"api_key": "secret-value", "placement": "header", "name": "X-API-Key"},
            "origin": "registered",
            "management_policy": "operation",
            "operation_id": "OP_SOURCE",
        },
    )


def test_management_inventory_is_secret_safe_and_includes_history(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "https://app.example.test")
    credential = _operation_credential(store)
    store.record_credential_status(
        "OP_SOURCE", credential["credential_id"], "valid", "operation", "verified", ["artifact:auth"]
    )

    result = execute_credential_management(
        ["--output-dir", str(tmp_path), "--target", "https://app.example.test", "list"]
    )

    assert result["credentials"][0]["credential_id"] == credential["credential_id"]
    assert result["credentials"][0]["history"][-1]["status"] == "valid"
    assert "secret-value" not in json.dumps(result)
    assert "payload" not in result["credentials"][0]


def test_management_queues_and_cancels_only_eligible_operation_credentials(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "https://app.example.test")
    credential = _operation_credential(store)
    queued = execute_credential_management(
        [
            "--output-dir", str(tmp_path), "--target", "https://app.example.test", "queue-rotation",
            "--credential-id", credential["credential_id"], "--reason", "Scheduled rotation",
        ]
    )

    request = queued["request"]
    assert request["status"] == "queued"
    assert request["maintenance_operation_id"].startswith("OP_CREDENTIAL_ROTATION_")
    cancelled = execute_credential_management(
        [
            "--output-dir", str(tmp_path), "--target", "https://app.example.test", "cancel-rotation",
            "--request-id", request["request_id"], "--reason", "No longer needed",
        ]
    )
    assert cancelled["request"]["status"] == "cancelled"
    with pytest.raises(ValueError, match="only queued"):
        execute_credential_management(
            [
                "--output-dir", str(tmp_path), "--target", "https://app.example.test", "cancel-rotation",
                "--request-id", request["request_id"], "--reason", "Again",
            ]
        )


def test_management_rejects_user_managed_or_unknown_credentials(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "https://app.example.test")
    user_credential = store.store_credential(
        "OP_SOURCE",
        {
            "credential_type": "api_key",
            "target": "https://app.example.test",
            "role": "reader",
            "payload": {"api_key": "user-secret", "placement": "header", "name": "X-API-Key"},
            "origin": "provided",
            "management_policy": "user",
        },
    )
    with pytest.raises(ValueError, match="only be updated by the user"):
        execute_credential_management(
            [
                "--output-dir", str(tmp_path), "--target", "https://app.example.test", "queue-rotation",
                "--credential-id", user_credential["credential_id"], "--reason", "Wrong policy",
            ]
        )
    with pytest.raises(ValueError, match="unavailable"):
        execute_credential_management(
            [
                "--output-dir", str(tmp_path), "--target", "https://app.example.test", "queue-rotation",
                "--credential-id", "missing", "--reason", "Missing",
            ]
        )
