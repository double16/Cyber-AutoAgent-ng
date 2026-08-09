import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.modules.tools import memory as mod
from src.modules.handlers.utils import get_tool_spec
from src.modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    EvidenceRequirement,
    Task,
    finalize_finding_validation,
    finalize_objective_validation,
    record_finding_validation,
    record_objective_validation,
    store_finding,
    store_knowledge,
    store_observation,
    store_objective_candidate,
)
from tests.helpers.acceptance import make_acceptance


@pytest.fixture
def memory_client():
    with patch("src.modules.tools.memory._ensure_memory_client") as ensure:
        client = MagicMock()
        client.search.return_value = []
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
    memory_client.search.return_value = [
        {"id": "m-existing", "memory": "Line one Line two", "score": 0.01}
    ]

    result = json.loads(store_observation("Line one\nLine two"))

    assert result == {
        "stored": True,
        "created": False,
        "memory_ref": "memory:m-existing",
    }
    memory_client.store_memory.assert_not_called()


def test_duplicate_without_id_creates_referenceable_memory(memory_client, operation_ids):
    memory_client.search.return_value = [{"memory": "Same observation"}]

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
    memory_client.search.side_effect = [
        [],
        [{"id": "m-recovered", "memory": "Recover this observation"}],
    ]

    result = json.loads(store_observation("Recover this observation"))

    assert result["memory_ref"] == "memory:m-recovered"


def test_store_memory_entry_rejects_unrecoverable_id(memory_client, operation_ids):
    memory_client.store_memory.return_value = {"results": []}

    with pytest.raises(RuntimeError, match="did not return a durable ID"):
        store_observation("Unreferenceable observation")


def test_store_memory_entry_reports_recovery_search_failure(memory_client, operation_ids):
    memory_client.store_memory.return_value = {"results": []}
    memory_client.search.side_effect = [[], RuntimeError("search unavailable")]

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
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
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
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
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
    assert "cwe_mappings" not in schema["properties"]
    assert "mitre_attack_mappings" not in schema["properties"]


def test_store_finding_leaves_taxonomy_annotation_to_the_workflow(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "proof.txt"
    artifact.write_text("Stored XSS proof", encoding="utf-8")
    plan_store = MagicMock()
    plan_store.get_finding_by_fingerprint.return_value = None
    plan_store.get_tasks.return_value = []
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_plan_current_phase", return_value=1),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        store_finding(
            "Stored XSS",
            "A script executes for another user",
            "HIGH",
            "https://target/comments",
            "stored_xss",
            "Input is encoded",
            "The stored payload executed",
            ["Submit payload", "Load the comment"],
            [str(artifact)],
        )

    candidate = plan_store.store_finding_candidate.call_args.args[3]
    assert "taxonomy" not in candidate
    assert candidate["artifacts"] == ["artifact:proof.txt"]


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


def test_record_finding_validation_requires_linked_active_task(tmp_path: Path, operation_ids, memory_client):
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
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
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
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="negative-control"):
            record_finding_validation(
                "finding-1", "confirmed", "Confirmed", ["Test"], "differential", [str(artifact)]
            )


def test_not_confirmed_validation_materializes_negative_acceptance(tmp_path: Path, operation_ids, memory_client):
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
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
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


def test_store_objective_candidate_creates_separate_validation_task(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("FLAG{abc}", encoding="utf-8")
    plan_store = MagicMock()
    plan_store.get_objective_candidate_by_fingerprint.return_value = None
    plan_store.get_plan.return_value = SimpleNamespace(
        objective="Find the flag. Flag format is: FLAG{...} and has length 9."
    )
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_plan_current_phase", return_value=5),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        result = json.loads(store_objective_candidate(
            "flag",
            "FLAG{abc}",
            "Retrieved through command injection",
            ["Inject cat /FLAG.txt"],
            [str(artifact)],
        ))

    task = memory_client.store_task.call_args.kwargs["task"]
    candidate = plan_store.store_objective_candidate.call_args.args[3]
    assert result["candidate_ref"].startswith("objective_candidate:")
    assert task.kind == "objective_validation"
    assert task.phase == 5
    assert candidate["validation_type"] == "objective"
    assert candidate["constraints"] == {"exact_length": 9, "format_template": "FLAG{...}"}
    assert store_entry.call_args.args[1] == "objective_candidate"


def test_store_objective_candidate_validates_inputs_and_is_idempotent(
    memory_client,
    operation_ids,
    tmp_path: Path,
):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("FLAG{abc}", encoding="utf-8")
    existing = {
        "candidate_uid": "candidate-1",
        "verification_task_uid": "task-1",
        "resolution": "objective_rejected",
    }
    plan_store = MagicMock()
    plan_store.get_objective_candidate_by_fingerprint.return_value = existing
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        result = json.loads(store_objective_candidate(
            "flag", "FLAG{abc}", "Retrieved", ["Read response"], [str(artifact)]
        ))
        with pytest.raises(ValueError, match="objective_type"):
            store_objective_candidate("token", "abc", "Retrieved", ["Read"], [str(artifact)])
        with pytest.raises(ValueError, match="reproduction_steps"):
            store_objective_candidate("flag", "FLAG{abc}", "Retrieved", [], [str(artifact)])

    assert result["candidate_uid"] == "candidate-1"
    assert result["status"] == "objective_rejected"
    memory_client.store_task.assert_not_called()


def test_store_objective_candidate_rejects_unproven_or_constraint_violating_values(
    memory_client,
    operation_ids,
    tmp_path: Path,
):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("server response: FLAG{abc}", encoding="utf-8")
    plan_store = MagicMock()
    plan_store.get_objective_candidate_by_fingerprint.return_value = None
    plan_store.get_plan.return_value = SimpleNamespace(
        objective="Find the flag. Flag format is: FLAG{...} and has length 9."
    )
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="evidence_artifacts"):
            store_objective_candidate(
                "flag", "FLAG{xyz}", "Retrieved", ["Read response"], [str(artifact)]
            )
        with pytest.raises(ValueError, match="objective constraints"):
            store_objective_candidate("flag", "HTB{abcd}", "Retrieved", ["Read response"], [str(artifact)])

    memory_client.store_task.assert_not_called()
    plan_store.store_objective_candidate.assert_not_called()


def test_objective_constraint_helpers_cover_optional_constraints(operation_ids):
    plan_store = MagicMock()
    plan_store.get_plan.return_value = SimpleNamespace(objective="Capture a flag without a prescribed shape")
    with patch("src.modules.tools.memory._get_database_store", return_value=plan_store):
        assert mod._objective_constraints("flag") == {}
        assert mod._objective_constraints("custom") == {}

    assert mod._objective_constraint_failures("anything", {}) == []
    assert mod._objective_constraint_failures("FLAG{x}", {"exact_length": 70}) == [
        "candidate length 7 does not equal required length 70"
    ]


def test_objective_validation_rejects_format_mismatch_without_changing_finding(
    memory_client,
    operation_ids,
    tmp_path: Path,
):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("flag{abc}", encoding="utf-8")
    task = Task(
        "objective-task",
        "Validate flag",
        "Validate objective candidate",
        AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Flag candidate",
                source_refs=["artifact:flag.txt"],
            ),
            criteria=[AcceptanceCriterion(
                id="validate-objective:c1",
                description="Validate flag",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )],
        ),
        5,
        "active",
        kind="objective_validation",
        reference_id="c1",
    )
    stored_results = []
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = {
        "verification_task_uid": task.task_uid,
        "candidate_data": {
            "candidate_uid": "c1",
            "objective_type": "flag",
            "candidate_value": "flag{abc}",
            "constraints": {"exact_length": 9, "format_template": "FLAG{...}"},
        },
    }
    plan_store.get_tasks.return_value = [task]
    plan_store.get_acceptance_results.side_effect = lambda *_args: list(stored_results)
    plan_store.store_acceptance_results.side_effect = (
        lambda _op_id, _task_uid, results: stored_results.extend(results)
    )
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        result = json.loads(record_objective_validation(
            "c1",
            "confirmed",
            95,
            "Candidate appeared in the response",
            [str(artifact)],
            "task_evaluator",
        ))

    assert result["requested_outcome"] == "confirmed"
    assert result["outcome"] == "rejected"
    validation = plan_store.store_objective_validation.call_args.args[2]
    assert validation["validation_type"] == "objective"
    assert validation["constraint_failures"] == ["candidate does not match required format FLAG{...}"]
    assert stored_results[0].status == "assessed_negative"
    plan_store.store_finding_validation.assert_not_called()


def test_objective_validation_requires_eighty_percent_confidence(
    memory_client,
    operation_ids,
    tmp_path: Path,
):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("FLAG{abc}", encoding="utf-8")
    task = Task(
        "objective-task",
        "Validate flag",
        "Validate objective candidate",
        AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Flag candidate",
                source_refs=["artifact:flag.txt"],
            ),
            criteria=[AcceptanceCriterion(
                id="validate-objective:c1",
                description="Validate flag",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )],
        ),
        5,
        "active",
        kind="objective_validation",
        reference_id="c1",
    )
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = {
        "verification_task_uid": task.task_uid,
        "candidate_data": {
            "candidate_uid": "c1",
            "objective_type": "flag",
            "candidate_value": "FLAG{abc}",
            "constraints": {"exact_length": 9, "format_template": "FLAG{...}"},
        },
    }
    plan_store.get_tasks.return_value = [task]
    plan_store.get_acceptance_results.return_value = []
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        result = json.loads(record_objective_validation(
            "c1", "confirmed", 79, "Low-confidence candidate", [str(artifact)], "task_evaluator"
        ))

    assert result["outcome"] == "rejected"
    assert "confidence must be at least 80" in plan_store.store_objective_validation.call_args.args[2]["summary"]


def test_objective_validation_rejects_candidate_absent_from_evidence(
    memory_client,
    operation_ids,
    tmp_path: Path,
):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("response did not include the flag", encoding="utf-8")
    task = Task(
        "objective-task",
        "Validate flag",
        "Validate objective candidate",
        AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(kind="snapshot", description="Flag", source_refs=["artifact:flag.txt"]),
            criteria=[AcceptanceCriterion(
                id="validate-objective:c1",
                description="Validate flag",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )],
        ),
        5,
        "active",
        kind="objective_validation",
        reference_id="c1",
    )
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = {
        "verification_task_uid": task.task_uid,
        "candidate_data": {
            "candidate_uid": "c1",
            "objective_type": "flag",
            "candidate_value": "FLAG{abc}",
            "constraints": {"exact_length": 9, "format_template": "FLAG{...}"},
        },
    }
    plan_store.get_tasks.return_value = [task]
    plan_store.get_acceptance_results.return_value = []
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        result = json.loads(record_objective_validation(
            "c1", "confirmed", 95, "Flag verified", [str(artifact)], "task_evaluator"
        ))

    assert result["outcome"] == "rejected"
    validation = plan_store.store_objective_validation.call_args.args[2]
    assert validation["constraint_failures"] == ["candidate value does not appear in the supplied evidence artifacts"]


def test_objective_validation_accepts_valid_candidate(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("FLAG{abc}", encoding="utf-8")
    task = Task(
        "objective-task",
        "Validate flag",
        "Validate objective candidate",
        AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(kind="snapshot", description="Flag", source_refs=["artifact:flag.txt"]),
            criteria=[AcceptanceCriterion(
                id="validate-objective:c1",
                description="Validate flag",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )],
        ),
        5,
        "active",
        kind="objective_validation",
        reference_id="c1",
    )
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = {
        "verification_task_uid": task.task_uid,
        "candidate_data": {
            "candidate_uid": "c1",
            "objective_type": "flag",
            "candidate_value": "FLAG{abc}",
            "constraints": {"exact_length": 9, "format_template": "FLAG{...}"},
        },
    }
    plan_store.get_tasks.return_value = [task]
    plan_store.get_acceptance_results.return_value = []
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        result = json.loads(record_objective_validation(
            "c1", "verified", 80, "Valid flag", [str(artifact)], "task_evaluator"
        ))

    assert result["outcome"] == "confirmed"
    assert plan_store.store_objective_validation.call_args.args[2]["constraint_failures"] == []


def test_objective_validation_rejects_unknown_candidate_wrong_owner_and_bad_values(
    operation_ids,
    tmp_path: Path,
):
    artifact = tmp_path / "flag.txt"
    artifact.write_text("FLAG{abc}", encoding="utf-8")
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = None
    with patch("src.modules.tools.memory._get_database_store", return_value=plan_store):
        with pytest.raises(ValueError, match="Unknown objective candidate"):
            record_objective_validation("missing", "confirmed", 90, "Valid", [str(artifact)], "validator")

    plan_store.get_objective_candidate.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {"candidate_value": "FLAG{abc}", "constraints": {}, "objective_type": "flag"},
    }
    plan_store.get_tasks.return_value = [SimpleNamespace(status="active", task_uid="other-task")]
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="active verification task"):
            record_objective_validation("c1", "confirmed", 90, "Valid", [str(artifact)], "validator")

    plan_store.get_tasks.return_value = [SimpleNamespace(status="active", task_uid="task-1")]
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="outcome must"):
            record_objective_validation("c1", "invented", 90, "Valid", [str(artifact)], "validator")
        with pytest.raises(ValueError, match="confidence"):
            record_objective_validation("c1", "confirmed", 101, "Valid", [str(artifact)], "validator")


def test_objective_validation_runtime_schema_advertises_canonical_values():
    candidate_schema = get_tool_spec(store_objective_candidate)["inputSchema"]["json"]
    validation_schema = get_tool_spec(record_objective_validation)["inputSchema"]["json"]

    assert candidate_schema["properties"]["objective_type"]["enum"] == ["flag"]
    assert validation_schema["properties"]["outcome"]["enum"] == [
        "confirmed",
        "rejected",
        "inconclusive",
    ]
    assert mod._normalize_objective_validation_outcome("not confirmed") == "rejected"
    assert mod._normalize_objective_validation_outcome("invented-state") == "invented_state"



def test_finalize_objective_validation_uses_objective_category(operation_ids):
    task = Task(
        "objective-task",
        "Validate flag",
        "Validate objective candidate",
        make_acceptance().to_dict(),
        5,
        "active",
        kind="objective_validation",
        reference_id="c1",
    )
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = {
        "candidate_data": {"candidate_value": "FLAG{abc}", "objective_type": "flag"},
        "validation_data": {"outcome": "confirmed", "confidence": 95},
        "resolution": None,
    }
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        resolution = finalize_objective_validation(task, "done", "Objective confirmed")

    assert resolution == "objective_verified"
    assert store_entry.call_args.args[1] == "objective_result"
    assert store_entry.call_args.args[2]["validation_type"] == "objective"
    plan_store.resolve_finding.assert_not_called()


def test_rejected_objective_validation_creates_one_actionable_follow_up(
    memory_client,
    operation_ids,
):
    task = Task(
        "objective-task",
        "Validate flag",
        "Validate objective candidate",
        make_acceptance().to_dict(),
        5,
        "active",
        kind="objective_validation",
        reference_id="c1",
    )
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = {
        "candidate_data": {
            "candidate_value": "flag{wrong}",
            "objective_type": "flag",
            "artifacts": ["artifact:flag.txt"],
        },
        "validation_data": {
            "outcome": "rejected",
            "confidence": 95,
            "evidence_artifacts": ["artifact:flag.txt"],
        },
        "resolution": None,
    }
    plan_store.get_tasks.return_value = [task]
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        resolution = finalize_objective_validation(task, "done", "Format mismatch")

    follow_up = memory_client.store_task.call_args.kwargs["task"]
    assert resolution == "objective_rejected"
    assert follow_up.status == "pending"
    assert follow_up.phase == 5
    assert follow_up.reference_id == "objective-search:flag"
    assert "different flag candidate" in follow_up.objective


def test_objective_validation_helpers_and_existing_resolution_are_idempotent(operation_ids):
    standard = Task("standard", "Task", "Work", make_acceptance().to_dict(), 1, "active")
    assert mod.objective_validation_submitted(standard) is True
    assert mod.objective_validation_outcome(standard) is None
    assert finalize_objective_validation(standard, "done", "Done") is None

    validation_task = Task(
        "objective-task",
        "Validate",
        "Validate",
        make_acceptance().to_dict(),
        1,
        "active",
        kind="objective_validation",
        reference_id="c1",
    )
    plan_store = MagicMock()
    plan_store.get_objective_candidate.return_value = {
        "validation_data": {"outcome": "inconclusive"},
        "resolution": "objective_rejected",
    }
    with patch("src.modules.tools.memory._get_database_store", return_value=plan_store):
        assert mod.objective_validation_submitted(validation_task) is True
        assert mod.objective_validation_outcome(validation_task) == "inconclusive"
        assert finalize_objective_validation(validation_task, "done", "Done") == "objective_rejected"


def test_finalize_finding_validation_promotes_only_approved_confirmation(operation_ids):
    task = Task(
        "task-1", "Verify", "Verify", make_acceptance().to_dict(), 1, "active",
        kind="finding_validation", reference_id="finding-1"
    )
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "candidate_data": {
            "claim": "Confirmed claim",
            "severity": "HIGH",
            "taxonomy": {"cwe": [{"id": "CWE-79"}], "mitre_attack": [], "provenance": {}},
        },
        "validation_data": {
            "outcome": "confirmed",
            "evidence_artifacts": ["/artifact"],
            "control_artifacts": [],
            "evidence_strategy": "direct",
        },
        "resolution": None,
    }
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        resolution = finalize_finding_validation(task, "done", "Evidence approved")

    assert resolution == "verified"
    assert store_entry.call_args.args[2]["taxonomy"]["cwe"][0]["id"] == "CWE-79"
    assert store_entry.call_args.args[1] == "finding"
    assert store_entry.call_args.args[2]["validation_status"] == "verified"


def test_finalize_rejected_confirmation_becomes_validation_failure(operation_ids):
    task = Task(
        "task-1", "Verify", "Verify", make_acceptance().to_dict(), 1, "active",
        kind="finding_validation", reference_id="finding-1"
    )
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "candidate_data": {
            "claim": "Unsupported claim",
            "severity": "CRITICAL",
            "taxonomy": {"cwe": [{"id": "CWE-79"}], "mitre_attack": [], "provenance": {}},
        },
        "validation_data": {"outcome": "confirmed"},
        "resolution": None,
    }
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        resolution = finalize_finding_validation(task, "partial_failure", "Artifact did not support the claim")

    assert resolution == "validation_failure"
    assert store_entry.call_args.args[2]["taxonomy"]["cwe"][0]["id"] == "CWE-79"
    metadata = store_entry.call_args.args[2]
    assert metadata["claimed_severity"] == "CRITICAL"
    assert metadata["validation_reason"] == "Artifact did not support the claim"
