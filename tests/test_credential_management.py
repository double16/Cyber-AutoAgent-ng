import json

import pytest

from modules.tools.credential_management import execute_credential_management
from modules.tools.credential_management import main as credential_management_main
from modules.tools.credentials import complete_credential_rotation, stage_credential_rotation
from modules.tools.memory import SQLiteApplicationStore, Task, create_tasks
from tests.helpers.acceptance import make_acceptance


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


def test_management_rejects_output_directory_without_database(tmp_path):
    with pytest.raises(ValueError, match="database does not exist"):
        execute_credential_management(
            ["--output-dir", str(tmp_path), "--target", "https://app.example.test", "list"]
        )


def test_management_cli_converts_storage_errors_to_exit_message(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["credential_management", "--output-dir", "/missing", "--target", "example.test", "list"],
    )
    with pytest.raises(SystemExit, match="credential database does not exist"):
        credential_management_main()


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


def test_management_start_claims_once_and_returns_constrained_objective(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "https://app.example.test")
    credential = _operation_credential(store)
    queued = execute_credential_management(
        [
            "--output-dir", str(tmp_path), "--target", "https://app.example.test", "queue-rotation",
            "--credential-id", credential["credential_id"], "--reason", "expiry",
        ]
    )
    started = execute_credential_management(
        [
            "--output-dir", str(tmp_path), "--target", "https://app.example.test", "start-rotation",
            "--request-id", queued["request"]["request_id"],
        ]
    )
    assert started["request"]["status"] == "claimed"
    assert started["request"]["claimed_task_uid"] == f"credential-rotation:{queued['request']['request_id']}"
    task = store.get_tasks(started["request"]["maintenance_operation_id"])[0]
    assert task.kind == "credential_rotation"
    assert task.reference_id == queued["request"]["request_id"]
    assert started["request"]["maintenance_operation_id"] in started["maintenance_objective"]
    assert queued["request"]["request_id"] in started["maintenance_objective"]
    with pytest.raises(ValueError, match="only queued"):
        execute_credential_management(
            [
                "--output-dir", str(tmp_path), "--target", "https://app.example.test", "start-rotation",
                "--request-id", queued["request"]["request_id"],
            ]
        )
    failed = execute_credential_management(
        [
            "--output-dir", str(tmp_path), "--target", "https://app.example.test", "fail-rotation",
            "--request-id", queued["request"]["request_id"], "--reason", "maintenance failed",
        ]
    )
    assert failed["request"]["status"] == "failed"


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


def test_rotation_lifecycle_stages_before_retiring_and_preserves_failure(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "https://app.example.test")
    original = _operation_credential(store)
    request = store.create_credential_rotation_request("OP_UI", original["credential_id"], "expiry", "OP_ROTATE")

    claimed = store.claim_credential_rotation_request(request["request_id"], "rotation-task")
    assert claimed["status"] == "claimed"
    with pytest.raises(ValueError, match="staged"):
        store.complete_credential_rotation_request(request["request_id"], ["artifact:verified"])

    replacement = store.store_credential(
        "OP_ROTATE",
        {
            "credential_type": "api_key",
            "target": "https://app.example.test",
            "role": "reader",
            "payload": {"api_key": "replacement", "placement": "header", "name": "X-API-Key"},
            "origin": "registered",
            "management_policy": "operation",
            "status": "pending",
        },
    )
    staged = store.stage_credential_rotation_request(request["request_id"], replacement["credential_id"])
    assert staged["staged_credential_id"] == replacement["credential_id"]
    assert store.get_credential(original["credential_id"])["status"] != "retired"
    completed = store.complete_credential_rotation_request(request["request_id"], ["artifact:verified"])
    assert completed["status"] == "succeeded"
    assert completed["completed_at"]
    assert store.get_credential(original["credential_id"])["status"] == "retired"
    assert store.get_credential(replacement["credential_id"])["status"] == "valid"

    another = _operation_credential(store)
    failed_request = store.create_credential_rotation_request("OP_UI", another["credential_id"], "test", "OP_FAIL")
    store.claim_credential_rotation_request(failed_request["request_id"], "rotation-task")
    failed = store.fail_credential_rotation_request(failed_request["request_id"], "target rejected replacement")
    assert failed["status"] == "failed"
    assert store.get_credential(another["credential_id"])["status"] != "retired"


def test_request_scoped_rotation_tools_reject_other_operations(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "https://app.example.test")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_ROTATE")
    original = _operation_credential(store)
    request = store.create_credential_rotation_request("OP_UI", original["credential_id"], "expiry", "OP_ROTATE")
    store.claim_credential_rotation_request(request["request_id"], "rotation-task")
    store.store_task(
        "OP_ROTATE",
        Task("rotation-task", "Rotate", "Rotate the request", make_acceptance("rotation-task"), 1, "active"),
    )
    replacement = store.store_credential(
        "OP_ROTATE",
        {
            "credential_type": "api_key", "target": "https://app.example.test", "role": "reader",
            "payload": {"api_key": "replacement", "placement": "header", "name": "X-API-Key"},
            "origin": "registered", "management_policy": "operation", "status": "pending",
        },
    )
    monkeypatch.setenv("CYBER_CREDENTIAL_ROTATION_REQUEST", request["request_id"])
    assert "staged_credential_id" in stage_credential_rotation(request["request_id"], replacement["credential_id"])
    assert "succeeded" in complete_credential_rotation(request["request_id"], ["artifact:verified"])
    with pytest.raises(ValueError, match="unavailable"):
        stage_credential_rotation("another-request", replacement["credential_id"])
    monkeypatch.setattr("modules.tools.credentials._operation_id", lambda: "OP_WRONG")
    with pytest.raises(ValueError, match="does not match"):
        stage_credential_rotation(request["request_id"], replacement["credential_id"])


def test_maintenance_operation_rejects_planner_task_fan_out(monkeypatch):
    monkeypatch.setenv("CYBER_CREDENTIAL_ROTATION_REQUEST", "request-1")
    with pytest.raises(ValueError, match="controller-created"):
        create_tasks({"tasks": []})
