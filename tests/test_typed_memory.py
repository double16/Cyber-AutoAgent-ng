import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.modules.handlers.utils import get_tool_spec
from src.modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    EvidenceRequirement,
    Task,
    finalize_finding_validation,
    record_finding_validation,
    store_finding,
    store_knowledge,
    store_observation,
)
from tests.helpers.acceptance import make_acceptance


@pytest.fixture
def memory_client():
    with patch("src.modules.tools.memory._ensure_memory_client") as ensure:
        client = MagicMock()
        client.mem0.search.return_value = {"results": []}
        client.store_memory.return_value = {"results": [{"id": "m-new"}]}
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

    assert json.loads(result) == {
        "stored": True,
        "created": True,
        "memory_ref": "memory:m-new",
    }
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
        "results": [{"id": "m-existing", "memory": "Line one Line two", "score": 0.01}]
    }

    result = json.loads(store_observation("Line one\nLine two"))

    assert result == {
        "stored": True,
        "created": False,
        "memory_ref": "memory:m-existing",
    }
    memory_client.store_memory.assert_not_called()


def test_duplicate_without_id_creates_referenceable_memory(memory_client, operation_ids):
    memory_client.mem0.search.return_value = {"results": [{"memory": "Same observation"}]}

    result = json.loads(store_observation("Same observation"))

    assert result["created"] is True
    assert result["memory_ref"] == "memory:m-new"
    memory_client.store_memory.assert_called_once()


def test_store_memory_entry_accepts_direct_id_envelope(memory_client, operation_ids):
    memory_client.store_memory.return_value = {"id": "m-direct"}

    result = json.loads(store_observation("Direct response"))

    assert result["memory_ref"] == "memory:m-direct"


def test_store_memory_entry_recovers_missing_write_id(memory_client, operation_ids):
    memory_client.store_memory.return_value = {"results": []}
    memory_client.mem0.search.side_effect = [
        {"results": []},
        {"results": [{"id": "m-recovered", "memory": "Recover this observation"}]},
    ]

    result = json.loads(store_observation("Recover this observation"))

    assert result["memory_ref"] == "memory:m-recovered"


def test_store_memory_entry_rejects_unrecoverable_id(memory_client, operation_ids):
    memory_client.store_memory.return_value = {"results": []}

    with pytest.raises(RuntimeError, match="did not return a durable ID"):
        store_observation("Unreferenceable observation")


def test_store_memory_entry_reports_recovery_search_failure(memory_client, operation_ids):
    memory_client.store_memory.return_value = {"results": []}
    memory_client.mem0.search.side_effect = [{"results": []}, RuntimeError("search unavailable")]

    with pytest.raises(RuntimeError, match="durable ID could not be recovered"):
        store_observation("Recovery search failure")


def test_store_observation_reports_current_root_for_outside_artifact(memory_client, operation_ids, tmp_path):
    operation_root = tmp_path / "current-operation"
    operation_root.mkdir()
    outside_artifact = tmp_path / "previous-operation" / "result.txt"
    outside_artifact.parent.mkdir()
    outside_artifact.write_text("result", encoding="utf-8")

    with patch("src.modules.tools.memory._operation_output_root", return_value=str(operation_root)):
        with pytest.raises(ValueError, match=f"current operation output {operation_root}") as error:
            store_observation("Outside artifact", artifacts=[str(outside_artifact)])

    assert "Use a path relative to the current operation output" in str(error.value)
    memory_client.store_memory.assert_not_called()


def test_store_finding_creates_one_linked_same_phase_task(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "admin-response.txt"
    artifact.write_text("HTTP 200 admin data", encoding="utf-8")
    plan_store = MagicMock()
    plan_store.get_finding_by_fingerprint.return_value = None
    source_task = Task(
        task_uid="source-task",
        title="Assess admin",
        objective="Assess admin authorization",
        acceptance=make_acceptance("source").to_dict(),
        phase=3,
        status="active",
    )
    plan_store.get_tasks.return_value = [source_task]
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_plan_current_phase", return_value=3),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
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
                [str(artifact)],
            )
        )

    assert result["status"] == "pending_validation"
    store_entry.assert_called_once()
    task = memory_client.store_task.call_args.kwargs["task"]
    assert task.phase == 3
    assert task.kind == "finding_validation"
    assert task.reference_id == result["finding_uid"]
    assert result["finding_ref"] == f"finding:{result['finding_uid']}"
    assert result["verification_task_ref"] == f"task:{result['verification_task_uid']}"
    assert task.status == "pending"
    assert task.target_scope == "all"
    candidate = plan_store.store_finding_candidate.call_args.args[3]
    assert candidate["source_task_uids"] == [source_task.task_uid]


def test_store_finding_is_idempotent(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("Access", encoding="utf-8")
    plan_store = MagicMock()
    plan_store.get_finding_by_fingerprint.return_value = {
        "finding_uid": "finding-1",
        "verification_task_uid": "task-1",
        "resolution": None,
    }
    source_task = Task(
        task_uid="source-task",
        title="Assess endpoint",
        objective="Assess endpoint behavior",
        acceptance=make_acceptance("source").to_dict(),
        phase=1,
        status="active",
    )
    plan_store.get_tasks.return_value = [source_task]
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        result = json.loads(
            store_finding(
                "X",
                "Claim",
                "LOW",
                "/x",
                "test",
                "No access",
                "Access",
                ["Request /x"],
                [str(artifact)],
            )
        )

    assert result == {
        "finding_ref": "finding:finding-1",
        "finding_uid": "finding-1",
        "status": "pending_validation",
        "verification_task_ref": "task:task-1",
        "verification_task_uid": "task-1",
    }
    plan_store.link_finding_source_task.assert_called_once_with("test_op", "finding-1", source_task.task_uid)
    memory_client.store_task.assert_not_called()


def test_store_finding_schema_requires_artifacts():
    schema = get_tool_spec(store_finding)["inputSchema"]["json"]

    assert "artifacts" in schema["required"]


def test_store_finding_rejects_missing_artifact(memory_client, operation_ids, tmp_path: Path):
    with patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError, match="At least one existing artifact is required"):
            store_finding("X", "Claim", "LOW", "/x", "test", "No access", "Access", ["Request /x"], [])
        with pytest.raises(ValueError, match="Artifact does not exist"):
            store_finding("X", "Claim", "LOW", "/x", "test", "No access", "Access", ["Request /x"], ["missing.txt"])


def test_store_finding_rejects_assumed_observed_result(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("Access", encoding="utf-8")
    with patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError, match="concrete observed evidence"):
            store_finding(
                "X",
                "Claim",
                "LOW",
                "/x",
                "test",
                "No access",
                "Likely access based on route name",
                ["Request /x"],
                [str(artifact)],
            )


def test_record_finding_validation_requires_linked_active_task(tmp_path: Path, operation_ids):
    artifact = tmp_path / "response.txt"
    artifact.write_text("HTTP 200", encoding="utf-8")
    validation_acceptance = AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(kind="snapshot", description="Finding", source_refs=["finding:finding-1"]),
        criteria=[AcceptanceCriterion(
            id="verify-finding:finding-1",
            description="Verify finding",
            evidence_requirements=[EvidenceRequirement(kind="artifact")],
        )],
    )
    task = Task(
        "task-1", "Verify", "Verify claim", validation_acceptance, 1, "active",
        kind="finding_validation", reference_id="finding-1"
    )
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {"verification_task_uid": "task-1"}
    plan_store.get_tasks.return_value = [task]
    acceptance_results = []
    plan_store.get_acceptance_results.side_effect = lambda *_args: list(acceptance_results)
    plan_store.store_acceptance_results.side_effect = (
        lambda _op_id, _task_uid, results: acceptance_results.extend(results)
    )
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        result = record_finding_validation(
            "finding-1", "confirmed", "Confirmed", ["Request target"], "direct", [str(artifact)]
        )

    payload = json.loads(result)
    assert payload["complete"] is True
    assert payload["acceptance"]["complete"] is True
    validation = plan_store.store_finding_validation.call_args.args[2]
    assert validation["outcome"] == "confirmed"
    assert validation["evidence_artifacts"] == ["artifact:response.txt"]
    assert acceptance_results[0].disposition == "existing_finding"


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


def test_not_confirmed_validation_materializes_negative_acceptance(tmp_path: Path, operation_ids):
    artifact = tmp_path / "negative-control.txt"
    artifact.write_text("Behavior not reproduced", encoding="utf-8")
    acceptance = AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(kind="snapshot", description="Finding", source_refs=["finding:finding-1"]),
        criteria=[AcceptanceCriterion(
            id="verify-finding:finding-1",
            description="Verify finding",
            evidence_requirements=[EvidenceRequirement(kind="artifact")],
        )],
    )
    task = Task(
        "task-1", "Verify", "Verify claim", acceptance, 1, "active",
        kind="finding_validation", reference_id="finding-1",
    )
    stored_results = []
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {"verification_task_uid": "task-1"}
    plan_store.get_tasks.return_value = [task]
    plan_store.get_acceptance_results.side_effect = lambda *_args: list(stored_results)
    plan_store.store_acceptance_results.side_effect = (
        lambda _op_id, _task_uid, results: stored_results.extend(results)
    )
    with (
        patch("src.modules.tools.memory._get_plan_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        payload = json.loads(record_finding_validation(
            "finding-1",
            "not verified",
            "Could not reproduce",
            ["Replay request"],
            "direct",
            [str(artifact)],
        ))

    assert payload["outcome"] == "not_confirmed"
    assert stored_results[0].status == "assessed_negative"
    assert stored_results[0].disposition == "no_vulnerability"


def test_finding_validation_schema_advertises_only_canonical_enum_values():
    schema = get_tool_spec(record_finding_validation)["inputSchema"]["json"]

    assert schema["properties"]["outcome"]["enum"] == ["confirmed", "not_confirmed"]
    assert schema["properties"]["evidence_strategy"]["enum"] == ["direct", "differential"]


def test_finding_validation_runtime_schema_accepts_aliases():
    validated = record_finding_validation._metadata.validate_input({
        "finding_uid": "finding-1",
        "outcome": "verified",
        "summary": "Confirmed",
        "reproduction_steps": ["Replay request"],
        "evidence_strategy": "negative-control",
    })

    assert validated["outcome"] == "verified"
    assert validated["evidence_strategy"] == "negative-control"


def test_finalize_finding_validation_promotes_only_approved_confirmation(operation_ids):
    task = Task(
        "task-1", "Verify", "Verify", make_acceptance().to_dict(), 1, "active",
        kind="finding_validation", reference_id="finding-1"
    )
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
    task = Task(
        "task-1", "Verify", "Verify", make_acceptance().to_dict(), 1, "active",
        kind="finding_validation", reference_id="finding-1"
    )
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
