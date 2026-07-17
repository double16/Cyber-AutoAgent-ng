import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.modules.tools.memory import (
    Task,
    finalize_finding_validation,
    record_finding_validation,
    store_finding,
    store_knowledge,
    store_observation,
)


@pytest.fixture
def memory_client():
    with patch("src.modules.tools.memory._ensure_memory_client") as ensure:
        client = MagicMock()
        client.mem0.search.return_value = {"results": []}
        ensure.return_value = client
        yield client


@pytest.fixture
def operation_ids():
    with (
        patch("src.modules.tools.memory._user_id", return_value="test_user"),
        patch("src.modules.tools.memory._operation_id", return_value="test_op"),
    ):
        yield


def test_store_observation_fixes_category_and_never_promotes(memory_client, operation_ids):
    result = store_observation("High priority fact", metadata={"category": "finding", "severity": "HIGH"})

    assert result == "Observation stored."
    metadata = memory_client.store_memory.call_args.args[3]
    assert metadata["category"] == "observation"
    assert metadata["severity"] == "HIGH"


def test_store_knowledge_is_internal_category(memory_client, operation_ids):
    store_knowledge("Use a test/control pair", {"knowledge_type": "technique"})

    metadata = memory_client.store_memory.call_args.args[3]
    assert metadata["category"] == "knowledge"
    assert metadata["knowledge_type"] == "technique"


def test_typed_memory_cleaning_and_duplicates(memory_client, operation_ids):
    memory_client.mem0.search.return_value = {
        "results": [{"memory": "Line one Line two", "score": 0.01}]
    }

    store_observation("Line one\nLine two")

    memory_client.store_memory.assert_not_called()


def test_store_finding_creates_one_linked_same_phase_task(memory_client, operation_ids):
    plan_store = MagicMock()
    plan_store.get_finding_by_fingerprint.return_value = None
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_plan_current_phase", return_value=3),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        result = json.loads(
            store_finding(
                "Authorization bypass",
                "An unauthenticated user can access admin data",
                "HIGH",
                "https://target/admin",
                "auth_bypass",
                "Unauthenticated request is denied",
                "Admin data was returned",
                ["Send an unauthenticated request", "Compare the response"],
            )
        )

    assert result["status"] == "pending_validation"
    store_entry.assert_called_once()
    task = memory_client.store_task.call_args.kwargs["task"]
    assert task.phase == 3
    assert task.kind == "finding_validation"
    assert task.reference_id == result["finding_uid"]
    assert task.status == "pending"


def test_store_finding_is_idempotent(memory_client, operation_ids):
    plan_store = MagicMock()
    plan_store.get_finding_by_fingerprint.return_value = {
        "finding_uid": "finding-1",
        "verification_task_uid": "task-1",
        "resolution": None,
    }
    with patch("src.modules.tools.memory._get_plan_store", return_value=plan_store):
        result = json.loads(
            store_finding("X", "Claim", "LOW", "/x", "test", "No access", "Access", ["Request /x"])
        )

    assert result == {
        "finding_uid": "finding-1",
        "status": "pending_validation",
        "verification_task_uid": "task-1",
    }
    memory_client.store_task.assert_not_called()


def test_record_finding_validation_requires_linked_active_task(tmp_path: Path, operation_ids):
    artifact = tmp_path / "response.txt"
    artifact.write_text("HTTP 200", encoding="utf-8")
    task = Task("task-1", "Verify", "Verify claim", 1, "active", kind="finding_validation", reference_id="finding-1")
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {"verification_task_uid": "task-1"}
    plan_store.get_tasks.return_value = [task]
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        result = record_finding_validation(
            "finding-1", "confirmed", "Confirmed", ["Request target"], "direct", [str(artifact)]
        )

    assert "evaluator review" in result
    validation = plan_store.store_finding_validation.call_args.args[2]
    assert validation["outcome"] == "confirmed"
    assert validation["evidence_artifacts"] == [str(artifact)]


def test_differential_confirmation_requires_control(tmp_path: Path, operation_ids):
    artifact = tmp_path / "response.txt"
    artifact.write_text("changed", encoding="utf-8")
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {"verification_task_uid": "task-1"}
    plan_store.get_tasks.return_value = [SimpleNamespace(status="active", task_uid="task-1")]
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="negative-control"):
            record_finding_validation(
                "finding-1", "confirmed", "Confirmed", ["Test"], "differential", [str(artifact)]
            )


def test_finalize_finding_validation_promotes_only_approved_confirmation(operation_ids):
    task = Task("task-1", "Verify", "Verify", 1, "active", kind="finding_validation", reference_id="finding-1")
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "candidate_data": {"claim": "Confirmed claim", "severity": "HIGH"},
        "validation_data": {
            "outcome": "confirmed",
            "evidence_artifacts": ["/artifact"],
            "control_artifacts": [],
            "evidence_strategy": "direct",
        },
        "resolution": None,
    }
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        resolution = finalize_finding_validation(task, "done", "Evidence approved")

    assert resolution == "verified"
    assert store_entry.call_args.args[1] == "finding"
    assert store_entry.call_args.args[2]["validation_status"] == "verified"


def test_finalize_rejected_confirmation_becomes_validation_failure(operation_ids):
    task = Task("task-1", "Verify", "Verify", 1, "active", kind="finding_validation", reference_id="finding-1")
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "candidate_data": {"claim": "Unsupported claim", "severity": "CRITICAL"},
        "validation_data": {"outcome": "confirmed"},
        "resolution": None,
    }
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        resolution = finalize_finding_validation(task, "partial_failure", "Artifact did not support the claim")

    assert resolution == "validation_failure"
    metadata = store_entry.call_args.args[2]
    assert metadata["claimed_severity"] == "CRITICAL"
    assert metadata["validation_reason"] == "Artifact did not support the claim"
