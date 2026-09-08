import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.modules.handlers.utils import get_tool_spec
from src.modules.tools import memory as mod
from src.modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    EvidenceRequirement,
    Task,
    TaskProposalRepairGuard,
    finalize_finding_validation,
    finalize_objective_validation,
    record_finding_validation,
    record_objective_validation,
    store_finding,
    store_knowledge,
    store_objective_candidate,
    store_observation,
)


def test_plan_phase_normalizes_finding_dependency_to_creation_mode():
    phase = mod.PlanPhase.from_obj({
        "id": 1,
        "title": "Candidate follow-up",
        "status": "pending",
        "produces_hypotheses": False,
        "requires_finding_candidates": True,
    })

    assert phase.task_creation_mode == "finding_dependent"
    assert phase.to_dict()["task_creation_mode"] == "finding_dependent"


def test_plan_phase_rejects_missing_hypothesis_metadata():
    with pytest.raises(ValueError, match="produces_hypotheses is required"):
        mod.PlanPhase.from_obj({"id": 1, "title": "Discovery", "status": "pending"})


def test_plan_phase_rejects_unknown_task_creation_mode():
    with pytest.raises(ValueError, match="task_creation_mode must be one of"):
        mod.PlanPhase(id=1, title="Invalid", status="pending", task_creation_mode="invented")


def test_plan_phase_accepts_hypothesis_dependent_task_creation_mode():
    phase = mod.PlanPhase(
        id=1,
        title="Comprehensive testing",
        status="pending",
        task_creation_mode="hypothesis_dependent",
    )

    assert phase.task_creation_mode == "hypothesis_dependent"


@pytest.mark.parametrize(
    ("current_phase", "phase_modes", "expected_phase"),
    [
        (1, ["standard", "finding_validation", "finding_dependent"], 2),
        (1, ["standard", "finding_dependent", "finding_validation"], 1),
        (2, ["standard", "finding_validation", "finding_dependent"], 2),
        (3, ["standard", "finding_validation", "finding_dependent"], 3),
        (2, ["standard", "finding_validation", "standard", "finding_dependent"], 2),
        (2, ["finding_validation", "standard", "finding_validation"], 2),
        (1, ["standard", "finding_dependent"], 1),
    ],
)
def test_finding_validation_task_phase_uses_planned_validation_owner(
    current_phase, phase_modes, expected_phase
):
    phases = [
        mod.PlanPhase(id=index, title=f"Phase {index}", status="pending", task_creation_mode=mode)
        for index, mode in enumerate(phase_modes, start=1)
    ]
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=current_phase,
        total_phases=len(phases),
        phases=phases,
    )

    assert mod._finding_validation_task_phase(plan, current_phase) == expected_phase


def test_finding_validation_task_phase_keeps_current_phase_without_matching_plan_context():
    assert mod._finding_validation_task_phase(None, 3) == 3

    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Finding validation", status="active")],
    )

    assert mod._finding_validation_task_phase(plan, 99) == 99


def test_task_proposal_repair_guard_restores_individually_valid_proposals():
    guard = TaskProposalRepairGuard()
    guard.capture([
        {
            "title": "Valid mapping",
            "objective": "Map one bounded target",
            "methods": ["crawl"],
            "limits": {"max_requests": 5},
            "criteria": [{"description": "Store a finite inventory"}],
        },
        {"title": "Broken"},
    ])
    rewritten = mod.TaskProposal.model_validate({
        "title": "Rewritten mapping",
        "objective": "Changed objective",
        "methods": ["crawl"],
        "limits": {"max_requests": 9},
        "criteria": [{"description": "Changed criterion"}],
    })
    repaired = mod.TaskProposal.model_validate({
        "title": "Fixed proposal",
        "objective": "Perform bounded work",
        "methods": ["crawl"],
        "limits": {"max_requests": 5},
        "criteria": [{"description": "Store finite evidence"}],
    })

    restored = guard.restore([rewritten, repaired])

    assert restored[0].title == "Valid mapping"
    assert restored[1].title == "Fixed proposal"


def test_task_proposal_repair_guard_keeps_prior_valid_slots_across_captures():
    guard = TaskProposalRepairGuard()
    original = {
        "title": "Original valid proposal",
        "objective": "Map one bounded target",
        "methods": ["crawl"],
        "limits": {"max_requests": 5},
        "criteria": [{"description": "Store a finite inventory"}],
    }
    repaired = {
        "title": "Repaired proposal",
        "objective": "Perform bounded work",
        "methods": ["crawl"],
        "limits": {"max_requests": 5},
        "criteria": [{"description": "Store finite evidence"}],
    }
    guard.capture([original, "invalid proposal"])
    guard.capture([{**original, "title": "Unwanted rewrite"}, repaired])

    restored = guard.restore([
        mod.TaskProposal.model_validate({**original, "title": "Another rewrite"}),
        mod.TaskProposal.model_validate({**repaired, "title": "Later rewrite"}),
    ])

    assert [proposal.title for proposal in restored] == ["Original valid proposal", "Repaired proposal"]


def test_task_proposal_repair_guard_rejects_count_changes():
    guard = TaskProposalRepairGuard()
    guard.capture([
        {
            "title": "Valid proposal",
            "objective": "Map one bounded target",
            "methods": ["crawl"],
            "limits": {"max_requests": 5},
            "criteria": [{"description": "Store a finite inventory"}],
        },
        "invalid proposal",
    ])

    with pytest.raises(ValueError, match="preserve the original proposal count and order"):
        guard.restore([guard.baseline[0]])


def test_task_proposal_repair_guard_ignores_malformed_capture_without_valid_slots():
    guard = TaskProposalRepairGuard()
    proposal = mod.TaskProposal.model_validate({
        "title": "Valid proposal",
        "objective": "Map one bounded target",
        "methods": ["crawl"],
        "limits": {"max_requests": 5},
        "criteria": [{"description": "Store a finite inventory"}],
    })

    guard.capture({"tasks": []})
    guard.capture(["invalid proposal"])

    assert guard.restore([proposal]) == [proposal]
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


def test_new_memory_write_emits_durable_memory_event(memory_client, operation_ids):
    events = []
    mod.set_memory_event_emitter(events.append)

    try:
        store_observation("A newly stored observation")
    finally:
        mod.set_memory_event_emitter(None)

    assert events == [
        {
            "type": "memory_added",
            "memory_id": "m-new",
            "memory_ref": "memory:m-new",
            "category": "observation",
            "content_preview": "A newly stored observation",
            "content_length": len("A newly stored observation"),
        }
    ]


def test_memory_added_event_bounds_long_content_preview(memory_client, operation_ids):
    events = []
    content = "x" * 241
    mod.set_memory_event_emitter(events.append)

    try:
        store_observation(content)
    finally:
        mod.set_memory_event_emitter(None)

    assert events[0]["content_preview"] == ("x" * 240) + "..."
    assert events[0]["content_length"] == 241


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


def test_duplicate_memory_write_does_not_emit_memory_added_event(memory_client, operation_ids):
    events = []
    memory_client.search.return_value = [{"id": "m-existing", "memory": "Existing observation"}]
    mod.set_memory_event_emitter(events.append)

    try:
        store_observation("Existing observation")
    finally:
        mod.set_memory_event_emitter(None)

    assert events == []


def test_failed_memory_write_does_not_emit_memory_added_event(memory_client, operation_ids):
    events = []
    memory_client.store_memory.side_effect = RuntimeError("backend unavailable")
    mod.set_memory_event_emitter(events.append)

    try:
        with pytest.raises(RuntimeError, match="backend unavailable"):
            store_observation("Unavailable observation")
    finally:
        mod.set_memory_event_emitter(None)

    assert events == []


def test_memory_event_callback_failure_does_not_interrupt_storage(memory_client, operation_ids):
    def fail_event(_event):
        raise RuntimeError("event transport unavailable")

    mod.set_memory_event_emitter(fail_event)
    try:
        result = json.loads(store_observation("Stored despite event transport failure"))
    finally:
        mod.set_memory_event_emitter(None)

    assert result["memory_ref"] == "memory:m-new"


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


def test_store_finding_routes_task_to_future_validation_phase(memory_client, operation_ids, tmp_path: Path):
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
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=3,
        total_phases=4,
        phases=[
            mod.PlanPhase(id=1, title="Discovery", status="done"),
            mod.PlanPhase(id=2, title="Testing", status="done"),
            mod.PlanPhase(id=3, title="Candidate discovery", status="active"),
            mod.PlanPhase(
                id=4,
                title="Finding validation",
                status="pending",
                task_creation_mode="finding_validation",
            ),
        ],
        targets=[mod.OperationTarget(target_id="target-1", value="https://target", type="network")],
    )
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_active_plan", return_value=plan),
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
                [{"artifact": str(artifact), "marker": "admin data"}],
            )
        )

    assert result["status"] == "pending_validation"
    store_entry.assert_called_once()
    task = memory_client.store_task.call_args.kwargs["task"]
    assert task.phase == 4
    assert task.kind == "finding_validation"
    assert task.reference_id == result["finding_uid"]
    assert result["finding_ref"] == f"finding:{result['finding_uid']}"
    assert result["verification_task_ref"] == f"task:{result['verification_task_uid']}"
    assert task.status == "pending"
    assert task.target_scope == "subset"
    assert task.target_ids == ["target-1"]
    candidate = plan_store.store_finding_candidate.call_args.args[3]
    assert "admin data" not in task.objective
    assert candidate["verification_packet"]["observed_result"] == "Admin data was returned"
    assert candidate["source_task_uids"] == [source_task.task_uid]
    assert candidate["artifact_fingerprints"] == {
        "artifact:admin-response.txt": hashlib.sha256(artifact.read_bytes()).hexdigest()
    }
    assert candidate["source_task_receipts"] == [{
        "task_uid": source_task.task_uid,
        "finding_uid": result["finding_uid"],
        "status": "persisted",
        "evidence_refs": ["artifact:admin-response.txt", f"finding:{result['finding_uid']}"],
    }]
    assert candidate["verification_packet"] == {
        "version": 1,
        "finding_uid": result["finding_uid"],
        "confirmation_guard_catalog_version": 1,
        "confirmation_requirements": [],
        "source_task": {
            "task_uid": "source-task",
            "title": "Assess admin",
            "objective": "Assess admin authorization",
        },
        "target": "https://target/admin",
        "target_scope": "subset",
        "target_ids": ["target-1"],
        "claim": "An unauthenticated user can access admin data",
        "technique": "auth_bypass",
        "expected_result": "Unauthenticated request is denied",
        "observed_result": "Admin data was returned",
        "reproduction_steps": ["Send an unauthenticated request", "Compare the response"],
        "evidence_assertions": [{
            "artifact": "artifact:admin-response.txt",
            "type": "literal_text",
            "value": "admin data",
            "marker": "admin data",
        }],
        "artifacts": ["artifact:admin-response.txt"],
        "artifact_fingerprints": candidate["artifact_fingerprints"],
    }


def test_store_finding_persists_internal_task_bound_evidence_receipts(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("HTTP 200 admin data", encoding="utf-8")
    task = Task(
        task_uid="source-task",
        title="Assess admin",
        objective="Assess admin authorization",
        acceptance=make_acceptance("source").to_dict(),
        phase=1,
        status="active",
    )
    plan_store = MagicMock()
    plan_store.get_tasks.return_value = [task]
    plan_store.get_finding_by_fingerprint.return_value = None
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_plan_current_phase", return_value=1),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        store_finding(
            "Authorization bypass", "Admin data exposed", "HIGH", "https://target/admin", "auth_bypass",
            "Denied", "Admin data returned", ["Request target"], [str(artifact)],
            [{"artifact": str(artifact), "marker": "admin data"}],
        )

    candidate = plan_store.store_finding_candidate.call_args.args[3]
    assert candidate["evidence_assertions"] == [{
        "artifact": "artifact:response.txt",
        "type": "literal_text",
        "value": "admin data",
        "marker": "admin data",
    }]
    assert candidate["evidence_receipts"][0].startswith("finding_evidence:")
    stored = plan_store.store_finding_evidence_receipt.call_args.args
    assert stored[2:5] == (task.task_uid, "artifact:response.txt", "admin data")


def test_finding_fingerprint_ignores_model_authored_title_variants():
    first = mod._finding_fingerprint(
        "Exposed connection string",
        "A connection string is exposed by /api/config",
        "https://target.test/api/config",
        "credential_exposure",
    )
    second = mod._finding_fingerprint(
        "PostgreSQL Connection String Exposure",
        "A connection string is exposed by /api/config",
        "https://target.test/api/config",
        "credential_exposure",
    )
    distinct = mod._finding_fingerprint(
        "Google Maps key exposure",
        "An API key is exposed by /api/config",
        "https://target.test/api/config",
        "credential_exposure",
    )

    assert first == second
    assert first != distinct


def test_finding_fingerprint_merges_equivalent_multi_secret_exposure_claims():
    first = mod._finding_fingerprint(
        "Sensitive credentials exposed",
        "The endpoint exposes a PostgreSQL connection string and a Google Maps API key.",
        "https://target.test/api/config",
        "credential_exposure",
    )
    second = mod._finding_fingerprint(
        "Configuration disclosure",
        "A Google Maps API key and PostgreSQL connection string are disclosed by the endpoint.",
        "https://target.test/api/config",
        "credential_exposure",
    )

    assert first == second


def test_typed_evidence_assertions_validate_binary_and_json_artifacts(tmp_path: Path):
    binary = tmp_path / "capture.bin"
    binary.write_bytes(b"prefix\x00\xffproofsuffix")
    structured = tmp_path / "result.json"
    structured.write_text('{"result":{"roles":["user","admin"]}}', encoding="utf-8")

    with patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        references = mod._validated_artifact_paths([str(binary), str(structured)])
        assertions = mod._validated_evidence_assertions(
            [
                {
                    "artifact": str(binary),
                    "type": "byte_sequence",
                    "encoding": "hex",
                    "value": "00ff70726f6f66",
                },
                {
                    "artifact": str(structured),
                    "type": "json_value",
                    "pointer": "/result/roles",
                    "operator": "contains",
                    "expected": "admin",
                },
            ],
            references,
            require_one=True,
        )

    assert [assertion["type"] for assertion in assertions] == ["byte_sequence", "json_value"]


def test_typed_evidence_assertion_rejects_unsatisfied_predicate(tmp_path: Path):
    artifact = tmp_path / "result.json"
    artifact.write_text('{"result":"denied"}', encoding="utf-8")

    with (
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match="was not satisfied"),
    ):
        references = mod._validated_artifact_paths([str(artifact)])
        mod._validated_evidence_assertions(
            [{
                "artifact": str(artifact),
                "type": "json_value",
                "pointer": "/result",
                "operator": "equals",
                "expected": "allowed",
            }],
            references,
            require_one=True,
        )


def test_store_finding_canonicalizes_service_boundary_and_retains_route_query(
    memory_client, operation_ids, tmp_path: Path
):
    artifact = tmp_path / "xss-response.html"
    artifact.write_text("<pre>Hello <script>alert(1)</script></pre>", encoding="utf-8")
    plan = mod.OperationPlan(
        objective="Assess DVWA",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Validation", status="active")],
        targets=[
            mod.OperationTarget(
                target_id="target-1",
                type="network",
                value="http://host.docker.internal:4280",
            )
        ],
    )
    source_task = Task(
        task_uid="source-task",
        title="Assess XSS",
        objective="Assess reflected XSS",
        acceptance=make_acceptance("source").to_dict(),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    plan_store = MagicMock()
    plan_store.get_tasks.return_value = [source_task]
    plan_store.get_finding_by_fingerprint.return_value = None

    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_active_plan", return_value=plan),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
    ):
        store_finding(
            "Reflected XSS",
            "The name parameter reflects executable script content.",
            "HIGH",
            "http://host.docker-internal:4280/vulnerabilities/xss_r/?name=test",
            "reflected_xss",
            "Input is encoded before rendering.",
            "The response contained the submitted script tag without encoding.",
            ["Send the script payload to name", "Inspect the response body"],
            [str(artifact)],
            [{"artifact": str(artifact), "marker": "<script>alert(1)</script>"}],
        )

    candidate = plan_store.store_finding_candidate.call_args.args[3]
    assert candidate["target"] == "http://host.docker.internal:4280/vulnerabilities/xss_r/?name=test"
    stored_task = memory_client.store_task.call_args.kwargs["task"]
    assert stored_task.target_scope == "subset"
    assert stored_task.target_ids == ["target-1"]
    assert "http://host.docker.internal:4280/vulnerabilities/xss_r/?name=test" in stored_task.objective
    assert "host.docker-internal" not in store_entry.call_args.args[0]


def test_store_finding_rejects_ambiguous_multi_service_target(memory_client, operation_ids, tmp_path: Path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("response", encoding="utf-8")
    plan = mod.OperationPlan(
        objective="Assess services",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[
            mod.OperationTarget(target_id="target-1", type="network", value="http://one.test:8080"),
            mod.OperationTarget(target_id="target-2", type="network", value="http://two.test:8080"),
        ],
    )
    source_task = Task(
        task_uid="source-task",
        title="Assess services",
        objective="Assess services",
        acceptance=make_acceptance("source").to_dict(),
        phase=1,
        status="active",
    )
    plan_store = MagicMock()
    plan_store.get_tasks.return_value = [source_task]

    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_active_plan", return_value=plan),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
        pytest.raises(ValueError, match="exactly one assigned target"),
    ):
        store_finding(
            "Reflected XSS",
            "Script content was reflected.",
            "MEDIUM",
            "http://typo.test:8080/xss",
            "reflected_xss",
            "Input is encoded.",
            "The response reflected the script tag.",
            ["Send payload"],
            [str(artifact)],
            [{"artifact": str(artifact), "marker": "response"}],
        )

    store_entry.assert_not_called()
    plan_store.store_finding_candidate.assert_not_called()
    memory_client.store_task.assert_not_called()


def test_finding_target_resolver_handles_network_range_and_filesystem_locations(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_file = source_root / "app.py"
    source_file.write_text("print('safe')", encoding="utf-8")
    plan = mod.OperationPlan(
        objective="Assess assigned targets",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[
            mod.OperationTarget(target_id="network-1", type="network_range", value="10.20.0.0/24"),
            mod.OperationTarget(target_id="service-1", type="network", value="10.30.0.4:22"),
            mod.OperationTarget(target_id="source-1", type="filesystem", value=str(source_root)),
        ],
    )

    network_task = Task(
        task_uid="network-task",
        title="Assess host",
        objective="Assess host",
        acceptance=make_acceptance("network").to_dict(),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["network-1"],
    )
    source_task = Task(
        task_uid="source-task",
        title="Assess source",
        objective="Assess source",
        acceptance=make_acceptance("source").to_dict(),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["source-1"],
    )
    service_task = Task(
        task_uid="service-task",
        title="Assess service",
        objective="Assess service",
        acceptance=make_acceptance("service").to_dict(),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["service-1"],
    )

    assert mod._canonicalize_finding_target("10.20.0.7", plan, network_task) == (
        "10.20.0.7",
        ["network-1"],
    )
    assert mod._canonicalize_finding_target("app.py", plan, source_task) == (
        str(source_file.resolve()),
        ["source-1"],
    )
    assert mod._canonicalize_finding_target("10.30.0.4:22", plan, service_task) == (
        "10.30.0.4:22",
        ["service-1"],
    )
    with pytest.raises(ValueError, match="allowed targets"):
        mod._canonicalize_finding_target("10.21.0.7", plan, network_task)
    with pytest.raises(ValueError, match="ambiguous|allowed targets"):
        mod._canonicalize_finding_target("../outside.py", plan, source_task)


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
        patch("src.modules.tools.memory._ensure_memory_client", return_value=MagicMock()),
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
                [{"artifact": str(artifact), "marker": "Access"}],
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


def test_store_finding_records_source_task_persistence_receipt(tmp_path: Path, operation_ids):
    artifact = tmp_path / "proof.txt"
    artifact.write_text("proof", encoding="utf-8")
    source_task = Task(
        task_uid="source-task",
        title="Assess XSS",
        objective="Assess XSS",
        acceptance=make_acceptance("source").to_dict(),
        phase=1,
        status="active",
    )
    candidate = {
        "title": "Reflected XSS",
        "observed_result": "The response reflected the submitted script.",
        "artifacts": ["artifact:proof.txt"],
    }

    mod._record_source_task_finding_receipt(source_task, candidate, "finding-1")

    assert candidate["source_task_receipts"] == [{
        "task_uid": "source-task",
        "finding_uid": "finding-1",
        "status": "persisted",
        "evidence_refs": ["artifact:proof.txt", "finding:finding-1"],
    }]


def test_store_finding_schema_requires_artifacts():
    schema = get_tool_spec(store_finding)["inputSchema"]["json"]

    assert "artifacts" in schema["required"]
    assert schema["properties"]["artifacts"] == {"type": "array", "items": {"type": "string"}, "minItems": 1}
    assert str(store_finding._tool_func.__annotations__["artifacts"]) == "typing.Annotated[list[str], Len(min_length=1, max_length=None)]"
    assert str(store_observation._tool_func.__annotations__["artifacts"]) == "typing.Optional[typing.List[str]]"
    assertion_schema = schema["properties"]["evidence_assertions"]["items"]
    assert assertion_schema["properties"]["type"]["enum"] == [
        "literal_text",
        "byte_sequence",
        "json_value",
        "secret_exposure",
    ]
    assert assertion_schema["properties"]["encoding"]["enum"] == ["hex", "base64"]
    assert assertion_schema["properties"]["operator"]["enum"] == ["exists", "equals", "contains"]
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
            [{"artifact": str(artifact), "marker": "Stored XSS proof"}],
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


def test_store_finding_verification_task_keeps_payload_urls_out_of_objective(
    memory_client, operation_ids, tmp_path: Path
):
    artifact = tmp_path / "config.json"
    artifact.write_text(
        '{"database": "postgres://bc:bc@db:5432/bc", '
        '"bucket": "https://neuralegion-open-bucket.s3.amazonaws.com", '
        '"cache": "mongodb://db:27017/config"}',
        encoding="utf-8",
    )
    plan_store = MagicMock()
    plan_store.get_finding_by_fingerprint.return_value = None
    plan_store.get_tasks.return_value = []
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._get_plan_current_phase", return_value=1),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._store_memory_entry"),
    ):
        store_finding(
            "Unauthenticated configuration exposure",
            "The endpoint exposes sensitive service configuration",
            "HIGH",
            "http://192.168.253.101:3001/api/config",
            "credential_exposure",
            "No authentication required",
            artifact.read_text(encoding="utf-8"),
            ["Request /api/config"],
            [str(artifact)],
            [
                {"artifact": str(artifact), "marker": "postgres://bc:bc@db:5432/bc"},
                {"artifact": str(artifact), "marker": "https://neuralegion-open-bucket.s3.amazonaws.com"},
                {"artifact": str(artifact), "marker": "mongodb://db:27017/config"},
            ],
        )

    task = memory_client.store_task.call_args.kwargs["task"]
    assert task.kind == "finding_validation"
    assert task.objective == (
        f"Independently verify finding candidate {task.reference_id} against "
        "http://192.168.253.101:3001/api/config. Re-test the target to reproduce the reported finding "
        "behavior, capture required evidence in fresh direct or differential artifacts, call "
        "record_finding_validation with the outcome, and stop."
    )
    assert "postgres://" not in task.objective
    assert "s3.amazonaws.com" not in task.objective
    assert "mongodb://" not in task.objective
    candidate = plan_store.store_finding_candidate.call_args.args[3]
    assert candidate["verification_packet"]["observed_result"] == artifact.read_text(encoding="utf-8")
    assert [item["marker"] for item in candidate["verification_packet"]["evidence_assertions"]] == [
        "postgres://bc:bc@db:5432/bc",
        "https://neuralegion-open-bucket.s3.amazonaws.com",
        "mongodb://db:27017/config",
    ]


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
    plan_store.get_finding.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {"evidence_assertions": [{"artifact": "artifact:response.txt", "marker": "HTTP 200"}]},
    }
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
            "finding-1", "confirmed", "Confirmed", ["Request target"], "direct", [str(artifact)], None,
            [{"artifact": str(artifact), "marker": "HTTP 200"}],
        )

    payload = json.loads(result)
    assert payload["complete"] is True
    assert payload["acceptance"]["complete"] is True
    validation = plan_store.store_finding_validation.call_args.args[2]
    assert validation["outcome"] == "confirmed"
    assert validation["evidence_artifacts"] == ["artifact:response.txt"]
    assert acceptance_results[0].disposition == "existing_finding"


def test_record_finding_validation_canonicalizes_bare_artifact_references(tmp_path: Path, operation_ids, memory_client):
    artifact = tmp_path / "artifacts" / "response.txt"
    artifact.parent.mkdir()
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
    plan_store.get_finding.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {
            "evidence_assertions": [{"artifact": "artifact:artifacts/response.txt", "marker": "HTTP 200"}],
        },
    }
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
        record_finding_validation(
            "finding-1", "confirmed", "Confirmed", ["Request target"], "direct", ["response.txt"]
        )

    validation = plan_store.store_finding_validation.call_args.args[2]
    assert validation["evidence_artifacts"] == ["artifact:artifacts/response.txt"]


@pytest.mark.parametrize(
    ("reproduction_steps", "evidence_artifacts", "expected_error"),
    [
        ("Replay request", [], "reproduction_steps must be an array of strings"),
        (["Replay request"], {}, "evidence_artifacts must be an artifact reference string or array of strings"),
        (
            ["Replay request"],
            ["artifact:response.txt", {}],
            "evidence_artifacts must be an artifact reference string or array of strings",
        ),
    ],
)
def test_record_finding_validation_rejects_malformed_payload_before_lookup(
    reproduction_steps,
    evidence_artifacts,
    expected_error,
    operation_ids,
):
    plan_store = MagicMock()
    with patch("src.modules.tools.memory._get_database_store", return_value=plan_store), pytest.raises(
        ValueError, match=expected_error
    ):
        record_finding_validation(
            "finding-1",
            "confirmed",
            "Confirmed",
            reproduction_steps,
            "direct",
            evidence_artifacts,
        )

    plan_store.get_finding.assert_not_called()


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


def test_confirmed_enumeration_and_rate_limit_require_resolved_manifest(tmp_path: Path, operation_ids, memory_client):
    existing = tmp_path / "existing.txt"
    nonexistent = tmp_path / "nonexistent.txt"
    existing.write_text("HTTP/1.1 200 OK\n\npositive-marker existing user", encoding="utf-8")
    nonexistent.write_text("HTTP/1.1 200 OK\n\npositive-marker unknown user", encoding="utf-8")
    manifest = tmp_path / "validation.json"
    manifest.write_text(json.dumps({
        "checks": {
            "user_enumeration": {
                "known_existing_artifact": str(existing),
                "known_nonexistent_artifact": str(nonexistent),
            },
            "lack_of_rate_limiting": {
                "attempts": [
                    {"sequence": index, "response_artifact": str(existing)}
                    for index in range(1, 11)
                ]
            },
        },
    }), encoding="utf-8")
    acceptance = AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(kind="snapshot", description="Finding", source_refs=["finding:finding-1"]),
        criteria=[AcceptanceCriterion(
            id="verify-finding:finding-1",
            description="Verify finding",
            evidence_requirements=[EvidenceRequirement(kind="artifact")],
        )],
    )
    task = Task("task-1", "Verify", "Verify", acceptance, 1, "active",
                kind="finding_validation", reference_id="finding-1")
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {
            "title": "User Enumeration and Lack of Rate Limiting",
            "claim": "User enumeration and lack of rate limiting",
            "technique": "Authentication testing",
            "evidence_assertions": [{"artifact": "artifact:existing.txt", "marker": "positive-marker"}],
        },
    }
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
        payload = json.loads(record_finding_validation(
            "finding-1", "confirmed", "Confirmed", ["Replay requests"], "direct", [str(existing)], None,
            None, str(manifest),
        ))

    assert payload["outcome"] == "confirmed"
    assert payload["validation_manifest"] == "artifact:validation.json"
    validation = plan_store.store_finding_validation.call_args.args[2]
    assert validation["validation_manifest_attestation"]["derived"]["lack_of_rate_limiting"]["attempt_count"] == 10


def test_secret_exposure_manifest_error_repeats_required_schema(tmp_path: Path, operation_ids):
    manifest = tmp_path / "validation.json"
    manifest.write_text(json.dumps({"checks": {"secret_exposure": {}}}), encoding="utf-8")
    candidate = {
        "title": "Exposed API key",
        "claim": "The target has a secret exposure.",
        "technique": "secret exposure",
        "evidence_assertions": [{"type": "secret_exposure", "kind": "api_key", "digest": "digest"}],
    }

    with (
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError) as error,
    ):
        mod._validate_confirmation_manifest(candidate, str(manifest))

    message = str(error.value)
    assert "reexposure_artifact" in message
    assert "Expected validation_manifest JSON shape" in message
    assert '"version"' not in message


def test_secret_exposure_manifest_error_reports_canonical_artifact_and_safe_predicates(tmp_path, operation_ids):
    fresh = tmp_path / "fresh-response.json"
    fresh.write_text('{"api_key":"different-secret"}', encoding="utf-8")
    manifest = tmp_path / "validation.json"
    manifest.write_text(
        json.dumps({"checks": {"secret_exposure": {"reexposure_artifact": str(fresh)}}}),
        encoding="utf-8",
    )
    candidate = {
        "title": "Exposed API key",
        "claim": "The target has a secret exposure.",
        "technique": "secret exposure",
        "evidence_assertions": [{
            "type": "secret_exposure",
            "kind": "provider_api_key",
            "digest": "missing-digest",
        }],
    }

    with (
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError) as error,
    ):
        mod._validate_confirmation_manifest(candidate, str(manifest))

    message = str(error.value)
    assert "canonical reexposure_artifact=artifact:fresh-response.json" in message
    assert "required predicates=" in message
    assert "available predicates=" in message


@pytest.mark.parametrize(
    ("candidate", "manifest_payload", "missing_field"),
    [
        (
            {"title": "User Enumeration", "claim": "User enumeration", "technique": "authentication"},
            {"checks": {"user_enumeration": {}}},
            "known_existing_artifact",
        ),
        (
            {"title": "Lack of rate limiting", "claim": "No rate limiting", "technique": "HTTP"},
            {"checks": {"lack_of_rate_limiting": {"attempts": []}}},
            "at least 10 recorded attempts",
        ),
    ],
)
def test_confirmation_manifest_schema_errors_repeat_the_required_shape(
    tmp_path: Path,
    operation_ids,
    candidate,
    manifest_payload,
    missing_field,
):
    manifest = tmp_path / "validation.json"
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")

    with (
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError) as error,
    ):
        mod._validate_confirmation_manifest(candidate, str(manifest))

    message = str(error.value)
    assert missing_field in message
    assert "Expected validation_manifest JSON shape" in message
    assert '"checks"' in message


@pytest.mark.parametrize(
    ("candidate", "manifest_payload", "manifest_name", "expected_reason"),
    [
        (
            {"title": "User Enumeration", "claim": "User enumeration", "technique": "authentication"},
            None,
            "missing-validation.json",
            "Artifact does not exist",
        ),
        (
            {"title": "User Enumeration", "claim": "User enumeration", "technique": "authentication"},
            {
                "checks": {
                    "user_enumeration": {
                        "known_existing_artifact": "missing-response.txt",
                        "known_nonexistent_artifact": "missing-control.txt",
                    }
                }
            },
            "validation.json",
            "Artifact does not exist: missing-response.txt",
        ),
        (
            {"title": "Lack of rate limiting", "claim": "No rate limiting", "technique": "HTTP"},
            {
                "checks": {
                    "lack_of_rate_limiting": {
                        "attempts": [
                            {"sequence": index, "response_artifact": "missing-attempt.txt"}
                            for index in range(1, 11)
                        ]
                    }
                }
            },
            "validation.json",
            "Artifact does not exist: missing-attempt.txt",
        ),
        (
            {
                "title": "Exposed API key",
                "claim": "The target has a secret exposure.",
                "technique": "secret exposure",
                "evidence_assertions": [{"type": "secret_exposure", "kind": "api_key", "digest": "digest"}],
            },
            {"checks": {"secret_exposure": {"reexposure_artifact": "missing-fresh-response.txt"}}},
            "validation.json",
            "Artifact does not exist: missing-fresh-response.txt",
        ),
    ],
    ids=("missing_manifest", "invalid_response_comparison", "invalid_rate_limit_attempt", "invalid_reexposure"),
)
def test_confirmation_manifest_invalid_references_repeat_reason_and_required_shape(
    tmp_path: Path,
    operation_ids,
    candidate,
    manifest_payload,
    manifest_name,
    expected_reason,
):
    manifest = tmp_path / manifest_name
    if manifest_payload is not None:
        manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")

    with (
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError) as error,
    ):
        mod._validate_confirmation_manifest(candidate, str(manifest))

    message = str(error.value)
    assert expected_reason in message
    assert "Expected validation_manifest JSON shape" in message
    assert '"checks"' in message


def test_confirmation_manifest_unreadable_json_repeats_reason_and_required_shape(
    tmp_path: Path, operation_ids
):
    manifest = tmp_path / "validation.json"
    manifest.write_text("not valid JSON", encoding="utf-8")
    candidate = {"title": "User Enumeration", "claim": "User enumeration", "technique": "authentication"}

    with (
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError) as error,
    ):
        mod._validate_confirmation_manifest(candidate, str(manifest))

    message = str(error.value)
    assert "Expecting value" in message
    assert "Expected validation_manifest JSON shape" in message
    assert '"checks"' in message


def test_finding_validation_manifest_schema_documents_every_supported_check_shape():
    schema = mod.finding_validation_manifest_schema([
        {"id": "user_enumeration", "kind": "response_comparison"},
        {"id": "lack_of_rate_limiting", "kind": "rate_limit_probe"},
        {"id": "secret_exposure", "kind": "secret_exposure_revalidation"},
    ])

    assert schema == {
        "checks": {
            "user_enumeration": {
                "known_existing_artifact": "artifact:<operation-local response artifact>",
                "known_nonexistent_artifact": "artifact:<operation-local response artifact>",
            },
            "lack_of_rate_limiting": {
                "attempts": [
                    {
                        "sequence": 1,
                        "response_artifact": "artifact:<operation-local response artifact>",
                    }
                ],
            },
            "secret_exposure": {
                "reexposure_artifact": "artifact:<fresh operation-local exposure artifact>",
            },
        }
    }


def test_secret_exposure_manifest_accepts_versionless_fresh_reexposure_artifact(
    tmp_path: Path, operation_ids
):
    fresh_artifact = tmp_path / "fresh-response.json"
    fresh_artifact.write_text('{"api_key":"fresh-secret"}', encoding="utf-8")
    manifest = tmp_path / "validation.json"
    manifest.write_text(json.dumps({
        "checks": {"secret_exposure": {"reexposure_artifact": str(fresh_artifact)}},
    }), encoding="utf-8")
    candidate = {
        "title": "Exposed API key",
        "claim": "The target has a secret exposure.",
        "technique": "secret exposure",
        "evidence_assertions": [{"type": "secret_exposure", "kind": "api_key", "digest": "digest"}],
    }

    with (
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._assertion_matches_artifact", return_value=True),
    ):
        attestation = mod._validate_confirmation_manifest(candidate, str(manifest))

    assert attestation["derived"]["secret_exposure"] == {
        "reexposure_artifact": "artifact:fresh-response.json"
    }


def test_confirmed_enumeration_rejects_identical_response_signatures(tmp_path: Path, operation_ids):
    response = tmp_path / "response.txt"
    response.write_text("HTTP/1.1 200 OK\n\npositive-marker identical", encoding="utf-8")
    manifest = tmp_path / "validation.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "checks": {"user_enumeration": {
            "known_existing_artifact": str(response),
            "known_nonexistent_artifact": str(response),
        }},
    }), encoding="utf-8")
    task = Task("task-1", "Verify", "Verify", make_acceptance().to_dict(), 1, "active",
                kind="finding_validation", reference_id="finding-1")
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {
            "title": "User Enumeration",
            "claim": "User enumeration",
            "technique": "Authentication testing",
            "evidence_assertions": [{"artifact": "artifact:response.txt", "marker": "positive-marker"}],
        },
    }
    plan_store.get_tasks.return_value = [task]
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match="materially different"),
    ):
        record_finding_validation(
            "finding-1", "confirmed", "Confirmed", ["Replay requests"], "direct", [str(response)], None,
            None, str(manifest),
        )

    plan_store.store_finding_validation.assert_not_called()


def test_confirmed_lfi_validation_rejects_cited_open_failure(tmp_path: Path, operation_ids):
    artifact = tmp_path / "lfi.txt"
    artifact.write_text(
        "Warning: include(../../../../etc/passwd): Failed to open stream\n"
        "Warning: include(): Failed opening '../../../../etc/passwd'\n",
        encoding="utf-8",
    )
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
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {
            "title": "LFI",
            "claim": "Local file inclusion",
            "technique": "LFI",
            "evidence_assertions": [{"artifact": "artifact:lfi.txt", "marker": "Failed to open stream"}],
        },
    }
    plan_store.get_tasks.return_value = [task]
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="local_file_inclusion_open_failure"):
            record_finding_validation(
                "finding-1", "confirmed", "Claim reproduced", ["Replay request"], "direct", [str(artifact)], None,
                [{"artifact": str(artifact), "marker": "Failed to open stream"}],
            )

    plan_store.store_finding_validation.assert_not_called()


@pytest.mark.parametrize(
    ("artifact_content", "should_reject"),
    [
        ('{"user":"Leaf"}', True),
        ('{"user":{"id":1,"details":{"role":"admin"}}}', False),
    ],
)
def test_confirmed_nested_json_validation_requires_nested_response(
    tmp_path: Path,
    operation_ids,
    artifact_content: str,
    should_reject: bool,
):
    artifact = tmp_path / "response.json"
    artifact.write_text(artifact_content, encoding="utf-8")
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
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {
            "title": "Endpoint returns nested JSON structure",
            "claim": "The endpoint returns a nested JSON object containing user details.",
            "technique": "Information Disclosure",
            "evidence_assertions": [{"artifact": "artifact:response.json", "marker": '"user"'}],
        },
    }
    plan_store.get_tasks.return_value = [task]
    memory_client = MagicMock()
    memory_client.store_memory.return_value = {"results": [{"id": "memory-validation"}]}
    def invocation():
        return record_finding_validation(
            "finding-1", "confirmed", "Claim reproduced", ["Replay request"], "direct", [str(artifact)], None,
            [{"artifact": str(artifact), "marker": '"user"'}],
        )

    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        patch("src.modules.tools.memory._ensure_memory_client", return_value=memory_client),
        patch("src.modules.tools.memory._record_task_acceptance", return_value='{"complete": true}'),
    ):
        if should_reject:
            with pytest.raises(ValueError, match="nested_json_claim_flat_response"):
                invocation()
        else:
            invocation()

    if should_reject:
        plan_store.store_finding_validation.assert_not_called()
    else:
        plan_store.store_finding_validation.assert_called_once()


def test_confirmed_validation_rejects_missing_positive_evidence_marker(tmp_path: Path, operation_ids):
    artifact = tmp_path / "not-found.html"
    artifact.write_text("<title>404 Not Found</title>", encoding="utf-8")
    task = Task(
        "task-1", "Verify", "Verify", make_acceptance().to_dict(), 1, "active",
        kind="finding_validation", reference_id="finding-1",
    )
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "verification_task_uid": "task-1",
        "candidate_data": {
            "evidence_assertions": [{"artifact": "artifact:not-found.html", "marker": "<script>alert(1)</script>"}]
        },
    }
    plan_store.get_tasks.return_value = [task]

    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match="did not reproduce candidate assertion"),
    ):
        record_finding_validation(
            "finding-1",
            "confirmed",
            "Claim reproduced",
            ["Replay request"],
            "direct",
            [str(artifact)],
            None,
            [{"artifact": str(artifact), "marker": "<script>alert(1)</script>"}],
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
    assert schema["properties"]["validation_manifest"]["type"] == "string"
    assert "evidence_assertions" not in schema["properties"]
    assert "evidence_assertions" not in schema["required"]


def test_bound_finding_validation_tool_hides_the_controller_owned_finding_identifier(monkeypatch):
    task = Task(
        task_uid="verify-task",
        title="Verify finding",
        objective="Verify finding",
        acceptance=make_acceptance("verify").to_dict(),
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )

    store = MagicMock()
    store.get_finding.return_value = {
        "verification_task_uid": "verify-task",
        "candidate_data": {"finding_uid": "finding-1", "title": "Open redirect"},
    }
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    schema = get_tool_spec(mod.build_record_finding_validation_tool(task))["inputSchema"]["json"]

    assert "finding_uid" not in schema["properties"]
    assert schema["properties"]["validation_manifest"]["type"] == "string"
    assert schema["required"] == ["outcome", "summary", "reproduction_steps"]


def test_bound_finding_validation_tool_requires_manifest_for_confirmed_secret_exposure(monkeypatch):
    task = Task(
        task_uid="verify-task",
        title="Verify exposed connection string",
        objective="Verify finding",
        acceptance=make_acceptance("verify").to_dict(),
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    store = MagicMock()
    store.get_finding.return_value = {
        "verification_task_uid": "verify-task",
        "candidate_data": {
            "finding_uid": "finding-1",
            "title": "Exposed connection string",
            "claim": "A connection string is exposed.",
            "technique": "Direct response inspection",
        }
    }
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    schema = get_tool_spec(mod.build_record_finding_validation_tool(task))["inputSchema"]["json"]

    assert schema["allOf"] == [{
        "if": {"properties": {"outcome": {"const": "confirmed"}}, "required": ["outcome"]},
        "then": {"required": ["validation_manifest"]},
    }]
    assert "artifact reference, never inline JSON" in schema["properties"]["validation_manifest"]["description"]
    assert "reexposure_artifact" in schema["properties"]["validation_manifest"]["description"]
    assert '"version"' not in schema["properties"]["validation_manifest"]["description"]


def test_bound_finding_validation_uses_frozen_requirements_per_candidate(monkeypatch):
    store = MagicMock()
    records = {
        "open-redirect": {
            "verification_task_uid": "verify-open",
            "candidate_data": {
                "finding_uid": "open-redirect",
                "title": "Open redirect",
                "claim": "Redirects to the supplied URL.",
                "technique": "open redirect",
                "verification_packet": {"confirmation_requirements": []},
            },
        },
        "secret": {
            "verification_task_uid": "verify-secret",
            "candidate_data": {
                "finding_uid": "secret",
                "title": "Exposed API key",
                "claim": "A secret exposure was reported.",
                "technique": "secret exposure",
                "verification_packet": {
                    "confirmation_requirements": [
                        {"id": "secret_exposure", "kind": "secret_exposure_revalidation"}
                    ]
                },
            },
        },
    }
    store.get_finding.side_effect = lambda _operation_id, finding_uid: records[finding_uid]
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    open_task = Task("verify-open", "Verify redirect", "Verify", make_acceptance("open").to_dict(), 1, "active",
                     kind="finding_validation", reference_id="open-redirect")
    secret_task = Task("verify-secret", "Verify secret", "Verify", make_acceptance("secret").to_dict(), 1, "active",
                       kind="finding_validation", reference_id="secret")

    open_schema = get_tool_spec(mod.build_record_finding_validation_tool(open_task))["inputSchema"]["json"]
    secret_schema = get_tool_spec(mod.build_record_finding_validation_tool(secret_task))["inputSchema"]["json"]

    assert "allOf" not in open_schema
    assert secret_schema["allOf"][0]["then"] == {"required": ["validation_manifest"]}


def test_bound_finding_validation_rejects_mismatched_verification_task(monkeypatch):
    task = Task("verify-open", "Verify redirect", "Verify", make_acceptance("open").to_dict(), 1, "active",
                kind="finding_validation", reference_id="open-redirect")
    store = MagicMock()
    store.get_finding.return_value = {
        "verification_task_uid": "verify-secret",
        "candidate_data": {"finding_uid": "open-redirect", "title": "Open redirect"},
    }
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    with pytest.raises(ValueError, match="Finding validation binding mismatch"):
        mod.build_record_finding_validation_tool(task)


def test_bound_finding_validation_rejects_unknown_candidate(monkeypatch):
    task = Task("verify-open", "Verify redirect", "Verify", make_acceptance("open").to_dict(), 1, "active",
                kind="finding_validation", reference_id="open-redirect")
    store = MagicMock()
    store.get_finding.return_value = None
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    with pytest.raises(ValueError, match="Unknown finding_uid"):
        mod._load_finding_validation_binding(store, "test_op", "open-redirect", task.task_uid)


def test_bound_finding_validation_rejects_mismatched_candidate_packet(monkeypatch):
    task = Task("verify-open", "Verify redirect", "Verify", make_acceptance("open").to_dict(), 1, "active",
                kind="finding_validation", reference_id="open-redirect")
    store = MagicMock()
    store.get_finding.return_value = {
        "verification_task_uid": "verify-open",
        "candidate_data": {
            "finding_uid": "secret",
            "verification_packet": {"finding_uid": "secret"},
        },
    }
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    with pytest.raises(ValueError, match="stored_finding_uid=secret"):
        mod.build_record_finding_validation_tool(task)


def test_bound_objective_validation_tool_hides_controller_owned_candidate_identifier(monkeypatch):
    task = Task("verify-objective", "Validate flag", "Validate", make_acceptance("objective").to_dict(), 1, "active",
                kind="objective_validation", reference_id="candidate-1")
    store = MagicMock()
    store.get_objective_candidate.return_value = {"verification_task_uid": "verify-objective"}
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    schema = get_tool_spec(mod.build_record_objective_validation_tool(task))["inputSchema"]["json"]

    assert "candidate_uid" not in schema["properties"]
    assert schema["required"] == ["outcome", "confidence", "summary", "evidence_artifacts", "validator"]


def test_bound_objective_validation_tool_rejects_invalid_or_mismatched_task(monkeypatch):
    invalid_task = Task("verify-objective", "Validate", "Validate", make_acceptance("objective").to_dict(), 1,
                        "active", kind="recon", reference_id="candidate-1")
    with pytest.raises(ValueError, match="bound objective-validation task"):
        mod.build_record_objective_validation_tool(invalid_task)

    task = Task("verify-objective", "Validate", "Validate", make_acceptance("objective").to_dict(), 1, "active",
                kind="objective_validation", reference_id="candidate-1")
    store = MagicMock()
    store.get_objective_candidate.return_value = {"verification_task_uid": "different-task"}
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")

    with pytest.raises(ValueError, match="Objective validation binding mismatch"):
        mod.build_record_objective_validation_tool(task)


def test_bound_objective_validation_tool_forwards_only_its_owned_candidate(monkeypatch):
    task = Task("verify-objective", "Validate", "Validate", make_acceptance("objective").to_dict(), 1, "active",
                kind="objective_validation", reference_id="candidate-1")
    store = MagicMock()
    store.get_objective_candidate.return_value = {"verification_task_uid": "verify-objective"}
    recorded = MagicMock(return_value='{"status":"recorded"}')
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "test_op")
    monkeypatch.setattr(mod, "record_objective_validation", recorded)

    bound_tool = mod.build_record_objective_validation_tool(task)
    bound_tool("confirmed", 90, "Validated", ["artifact:proof.json"], "evaluator")

    assert recorded.call_args.args[0] == "candidate-1"
    assert recorded.call_args.args[1:] == (
        "confirmed", 90, "Validated", ["artifact:proof.json"], "evaluator"
    )


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


def test_finalize_finding_validation_promotes_only_approved_confirmation(operation_ids, tmp_path: Path):
    artifact = tmp_path / "proof.txt"
    artifact.write_text("positive proof", encoding="utf-8")
    artifact_ref = "artifact:proof.txt"
    task = Task(
        "task-1", "Verify", "Verify", make_acceptance().to_dict(), 1, "active",
        kind="finding_validation", reference_id="finding-1"
    )
    plan_store = MagicMock()
    plan_store.get_finding.return_value = {
        "candidate_data": {
            "claim": "Confirmed claim",
            "severity": "HIGH",
            "evidence_assertions": [{"artifact": artifact_ref, "marker": "positive proof"}],
            "taxonomy": {"cwe": [{"id": "CWE-79"}], "mitre_attack": [], "provenance": {}},
        },
        "validation_data": {
            "outcome": "confirmed",
            "evidence_artifacts": [artifact_ref],
            "control_artifacts": [],
            "evidence_strategy": "direct",
            "evidence_assertions": [{"artifact": artifact_ref, "marker": "positive proof"}],
            "evidence_artifact_fingerprints": {artifact_ref: hashlib.sha256(artifact.read_bytes()).hexdigest()},
        },
        "resolution": None,
    }
    with (
        patch("src.modules.tools.memory._get_database_store", return_value=plan_store),
        patch("src.modules.tools.memory._store_memory_entry") as store_entry,
        patch("src.modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
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
