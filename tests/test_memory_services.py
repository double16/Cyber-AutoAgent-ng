import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from jsonschema import Draft202012Validator
import pytest
from pydantic import TypeAdapter, ValidationError

import modules.tools as tools_module
from modules.handlers.utils import get_tool_spec
from modules.tools import memory as mod
from tests.helpers import memory_tasks
from tests.helpers.acceptance import acceptance_dict, make_acceptance, task_proposal


@pytest.mark.parametrize(
    ("adapter_type", "alias", "canonical"),
    [
        (mod.NormalizedAcceptanceResultStatus, "Completed", "satisfied"),
        (mod.NormalizedAcceptanceDisposition, "no-finding", "no_vulnerability"),
        (mod.NormalizedFindingValidationOutcome, "not verified", "not_confirmed"),
        (mod.NormalizedEvidenceStrategy, "negative-control", "differential"),
        (mod.NormalizedTaskStatus, "in progress", "active"),
        (mod.NormalizedPlanStatus, "N-A", "not_applicable"),
    ],
)
def test_shared_semantic_enum_normalization(adapter_type, alias, canonical):
    assert TypeAdapter(adapter_type).validate_python(alias) == canonical


@pytest.mark.parametrize(
    "adapter_type",
    [
        mod.NormalizedAcceptanceResultStatus,
        mod.NormalizedAcceptanceDisposition,
        mod.NormalizedFindingValidationOutcome,
        mod.NormalizedEvidenceStrategy,
        mod.NormalizedTaskStatus,
        mod.NormalizedPlanStatus,
    ],
)
def test_shared_semantic_enum_normalization_keeps_unknown_values_invalid(adapter_type):
    with pytest.raises(ValidationError):
        TypeAdapter(adapter_type).validate_python("invented-state")


class FakePlanStore:
    def __init__(self):
        self.plan = None
        self.tasks = []
        self.acceptance_results = {}
        self.findings = {}
        self.acceptance_memory_publications = {}

    def store_plan(self, _operation_id, plan):
        self.plan = plan

    def get_plan(self, _operation_id):
        return self.plan

    def store_task(self, _operation_id, task):
        self.tasks = [t for t in self.tasks if t.task_uid != task.task_uid]
        self.tasks.append(task)

    def get_tasks(self, _operation_id):
        return list(self.tasks)

    def store_acceptance_results(self, _operation_id, task_uid, results):
        current = {result.criterion_id: result for result in self.acceptance_results.get(task_uid, [])}
        current.update({result.criterion_id: result for result in results})
        self.acceptance_results[task_uid] = list(current.values())

    def get_acceptance_results(self, _operation_id, task_uid):
        return list(self.acceptance_results.get(task_uid, []))

    def has_acceptance_memory_publication(self, operation_id, task_uid, publication_key):
        return self.acceptance_memory_publications.get((operation_id, task_uid)) == publication_key

    def mark_acceptance_memory_published(self, operation_id, task_uid, publication_key):
        self.acceptance_memory_publications[(operation_id, task_uid)] = publication_key

    def get_finding(self, _operation_id, finding_uid):
        return self.findings.get(finding_uid)

    def list_findings(self, _operation_id):
        return [
            {"finding_uid": finding_uid, **record}
            for finding_uid, record in self.findings.items()
        ]

    def link_finding_source_task(self, _operation_id, finding_uid, task_uid):
        candidate_data = self.findings[finding_uid].setdefault("candidate_data", {})
        source_task_uids = candidate_data.setdefault("source_task_uids", [])
        if task_uid not in source_task_uids:
            source_task_uids.append(task_uid)


class FakeMem0:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []
        self.get_all_calls = []
        self.get_calls = []

    def add(self, **kwargs):
        self.add_calls.append(kwargs)
        return {"id": "m1"}

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return {"results": [{"memory": "finding one", "metadata": {"category": "finding", "active": True}}]}

    def get_all(self, **kwargs):
        self.get_all_calls.append(kwargs)
        if "page" in kwargs:
            raise TypeError("page unsupported")
        return {
            "results": [
                {"memory": "active", "metadata": {"category": "finding", "active": True}, "created_at": "2"},
                {"memory": "inactive", "metadata": {"active": False}},
                "plain text",
                None,
            ]
        }

    def get(self, memory_id):
        self.get_calls.append(memory_id)
        if memory_id == "m1":
            return {
                "id": "m1",
                "memory": "direct memory",
                "metadata": {"active": True, "category": "observation", "operation_id": "op1"},
            }
        if memory_id == "inactive":
            return {"id": "inactive", "memory": "hidden", "metadata": {"active": False}}
        return None


@pytest.fixture
def fake_memory_client(monkeypatch, tmp_path):
    store = FakePlanStore()
    client = mod.Mem0ServiceClient.__new__(mod.Mem0ServiceClient)
    client.mem0 = FakeMem0()
    client.has_existing_memories = True
    client.silent = True
    client.config = {}
    client.region = None

    monkeypatch.setattr(mod, "_MEMORY_CLIENT", client)
    monkeypatch.setattr(mod, "_PLAN_STORE", store)
    monkeypatch.setattr(
        mod,
        "_MEMORY_CONFIG",
        {
            "user_id": "u1",
            "operation_id": "op1",
            "output_dir": str(tmp_path),
            "target_name": "target",
        },
    )
    monkeypatch.setattr(mod, "_get_plan_store", lambda: store)
    monkeypatch.setenv("CYBER_OPERATION_ID", "op1")
    return client, store


def _write_inventory_manifest() -> Path:
    root = Path(mod._operation_output_root())
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "inventory.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "id": "endpoint-1",
                        "target_id": "target-1",
                        "kind": "endpoint",
                        "value": "http://target.test/login",
                        "attributes": {},
                    }
                ],
                "unassessed_gaps": [],
            }
        )
    )
    return manifest


def _coverage_acceptance(manifest: Path) -> dict:
    return {
        "mode": "coverage",
        "basis": {
            "kind": "snapshot",
            "description": "Frozen endpoint inventory",
            "source_refs": [f"artifact:{manifest}"],
        },
        "criteria": [
            {
                "id": "test-inventory",
                "description": "Assess every item ID in the frozen inventory",
                "evidence_requirements": [{"kind": "artifact", "min_count": 1}],
            }
        ],
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"schema_version": 2, "items": [], "unassessed_gaps": []}, "schema_version"),
        ({"schema_version": 1, "items": [], "unassessed_gaps": []}, "non-empty list"),
        ({"schema_version": 1, "items": [{}], "unassessed_gaps": None}, "must be a list"),
        ({"schema_version": 1, "items": ["item"], "unassessed_gaps": []}, "must be objects"),
        (
            {
                "schema_version": 1,
                "items": [{"id": "x", "target_id": "target-1", "kind": "bad", "value": "x"}],
                "unassessed_gaps": [],
            },
            "requires id",
        ),
        (
            {
                "schema_version": 1,
                "items": [
                    {"id": "x", "target_id": "target-1", "kind": "endpoint", "value": "x", "attributes": []}
                ],
                "unassessed_gaps": [],
            },
            "attributes",
        ),
        (
            {
                "schema_version": 1,
                "items": [
                    {"id": "x", "target_id": "target-1", "kind": "endpoint", "value": "x"},
                    {"id": "x", "target_id": "target-1", "kind": "endpoint", "value": "y"},
                ],
                "unassessed_gaps": [],
            },
            "ids must be unique",
        ),
    ],
)
def test_inventory_manifest_rejects_malformed_shapes(fake_memory_client, payload, message):
    del fake_memory_client
    path = Path(mod._operation_output_root()) / "invalid-inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        mod._load_inventory_manifest(f"artifact:{path}")


def test_memory_dataclasses_validation_and_formatting():
    task = mod.Task.from_obj(
        {
            "task_uid": "t1",
            "title": "Check,/admin",
            "objective": "Test\npath",
            "acceptance": acceptance_dict(),
            "evidence": [{"url": "/admin"}, None, " log "],
            "phase": 1,
            "status": "active",
            "status_reason": None,
        }
    )
    assert "Check;/admin" in task.to_toon()
    assert mod.Task.list_to_toon([task]).startswith("task[1]")
    assert task.to_dict()["evidence"][0] == '{"url": "/admin"}'
    with pytest.raises(ValueError):
        mod.Task.from_obj("bad")


def test_strict_acceptance_components_cover_positive_and_negative_shapes():
    requirement = mod.EvidenceRequirement(kind="artifact")
    assert mod.EvidenceRequirement.from_obj(requirement) is requirement
    assert requirement.to_dict() == {"kind": "artifact", "min_count": 1}
    with pytest.raises(ValueError, match="unsupported"):
        mod.EvidenceRequirement(kind="text")
    with pytest.raises(ValueError, match="positive int"):
        mod.EvidenceRequirement(kind="artifact", min_count=0)
    with pytest.raises(ValueError, match="object/dict"):
        mod.EvidenceRequirement.from_obj("artifact")

    criterion = mod.AcceptanceCriterion(
        id=" Result ",
        description="Assess the finite basis",
        evidence_requirements=[requirement],
    )
    assert mod.AcceptanceCriterion.from_obj(criterion) is criterion
    with pytest.raises(ValueError, match="id required"):
        mod.AcceptanceCriterion(id="", description="Result", evidence_requirements=[requirement])
    with pytest.raises(ValueError, match="description required"):
        mod.AcceptanceCriterion(id="result", description="", evidence_requirements=[requirement])
    with pytest.raises(ValueError, match="moving scope"):
        mod.AcceptanceCriterion(
            id="moving",
            description="Assess all reachable endpoints",
            evidence_requirements=[requirement],
        )
    with pytest.raises(ValueError, match="evidence_requirements required"):
        mod.AcceptanceCriterion(id="result", description="Result", evidence_requirements=[])
    with pytest.raises(ValueError, match="object/dict"):
        mod.AcceptanceCriterion.from_obj("criterion")

    procedure = mod.DiscoveryProcedure(
        methods=["crawl"],
        limits={"max_requests": 10},
        stop_condition="first_limit_reached",
        gap_policy="record_unassessed",
        output_kind="inventory_manifest",
    )
    assert mod.DiscoveryProcedure.from_obj(procedure) is procedure
    with pytest.raises(ValueError, match="limits required"):
        mod.DiscoveryProcedure(["crawl"], {}, "first_limit_reached", "record_unassessed", "inventory_manifest")
    with pytest.raises(ValueError, match="unsupported"):
        mod.DiscoveryProcedure(
            ["crawl"], {"unknown": 1}, "first_limit_reached", "record_unassessed", "inventory_manifest"
        )
    with pytest.raises(ValueError, match="positive integers"):
        mod.DiscoveryProcedure(
            ["crawl"], {"max_requests": 0}, "first_limit_reached", "record_unassessed", "inventory_manifest"
        )
    with pytest.raises(ValueError, match="stop_condition"):
        mod.DiscoveryProcedure(["crawl"], {"max_requests": 1}, "never", "record_unassessed", "inventory_manifest")
    with pytest.raises(ValueError, match="gap_policy"):
        mod.DiscoveryProcedure(["crawl"], {"max_requests": 1}, "first_limit_reached", "ignore", "inventory_manifest")
    with pytest.raises(ValueError, match="output_kind"):
        mod.DiscoveryProcedure(["crawl"], {"max_requests": 1}, "first_limit_reached", "record_unassessed", "other")
    with pytest.raises(ValueError, match="object/dict"):
        mod.DiscoveryProcedure.from_obj("procedure")

    with pytest.raises(ValueError, match="description required"):
        mod.AcceptanceBasis(kind="snapshot", description="", source_refs=["memory:m1"])
    with pytest.raises(ValueError, match="kind"):
        mod.AcceptanceBasis(kind="moving", description="Basis", source_refs=["memory:m1"])
    with pytest.raises(ValueError, match="requires procedure"):
        mod.AcceptanceBasis(kind="procedure", description="Basis", source_refs=["target:target-1"])
    with pytest.raises(ValueError, match="only target"):
        mod.AcceptanceBasis(
            kind="procedure",
            description="Basis",
            source_refs=["memory:m1"],
            procedure=procedure,
        )
    with pytest.raises(ValueError, match="must not contain"):
        mod.AcceptanceBasis(
            kind="snapshot",
            description="Basis",
            source_refs=["memory:m1"],
            procedure=procedure,
        )
    with pytest.raises(ValueError, match="object/dict"):
        mod.AcceptanceBasis.from_obj("basis")

    coverage = mod.CoverageResult(
        item_id="item",
        status="satisfied",
        evidence_refs=["artifact:evidence.txt"],
    )
    assert mod.CoverageResult.from_obj(coverage) is coverage
    with pytest.raises(ValueError, match="item_id required"):
        mod.CoverageResult(item_id="", status="satisfied", evidence_refs=["artifact:evidence.txt"])
    with pytest.raises(ValueError, match="terminal"):
        mod.CoverageResult(item_id="item", status="pending", evidence_refs=["artifact:evidence.txt"])
    with pytest.raises(ValueError, match="object/dict"):
        mod.CoverageResult.from_obj("coverage")
    with pytest.raises(ValueError, match="must be unique"):
        mod.AcceptanceResult(
            criterion_id="result",
            status="satisfied",
            disposition="observation",
            summary="Done",
            evidence_refs=["artifact:evidence.txt"],
            coverage=[coverage, coverage],
        )

    with pytest.raises(ValueError, match="must be a list"):
        mod._normalize_non_empty_strings("value", "field")
    with pytest.raises(ValueError, match="non-empty"):
        mod._normalize_non_empty_strings([""], "field")
    assert mod._normalize_non_empty_strings(["one", "one"], "field") == ("one",)


def test_procedure_output_kind_enforces_matching_evidence_requirements():
    def contract(output_kind, requirements):
        return mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="procedure",
                description="Bounded output procedure",
                source_refs=["target:target-1"],
                procedure={
                    "methods": ["inspect"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": output_kind,
                },
            ),
            criteria=[
                mod.AcceptanceCriterion(
                    id="output",
                    description="Store the bounded procedure output",
                    evidence_requirements=requirements,
                )
            ],
        )

    artifact_contract = contract("artifact", [mod.EvidenceRequirement(kind="artifact")])
    assert artifact_contract.basis.procedure.output_kind == "artifact"
    with pytest.raises(ValueError, match="must not require inventory_manifest"):
        contract("artifact", [mod.EvidenceRequirement(kind="inventory_manifest")])
    with pytest.raises(ValueError, match="requires inventory_manifest evidence"):
        contract("inventory_manifest", [mod.EvidenceRequirement(kind="artifact")])
    with pytest.raises(ValueError, match="requires artifact evidence"):
        contract("artifact", [mod.EvidenceRequirement(kind="observation")])


def test_acceptance_contract_normalizes_and_hashes_finite_manifest():
    contract = mod.AcceptanceContract.from_obj(
        {
            "mode": "coverage",
            "basis": {
                "kind": "snapshot",
                "description": "Endpoint inventory",
                "source_refs": ["artifact:artifacts/inventory.json"],
            },
            "criteria": [
                {
                    "id": " Endpoint:/Login.php ",
                    "description": "Map login parameters",
                    "evidence_requirements": [{"kind": "artifact", "min_count": 1}],
                },
                {
                    "id": "endpoint:/security.php",
                    "description": "Map security parameters",
                    "evidence_requirements": [{"kind": "observation", "min_count": 1}],
                },
            ],
        }
    )

    assert [criterion.id for criterion in contract.criteria] == [
        "endpoint:/login.php",
        "endpoint:/security.php",
    ]
    assert len(contract.manifest_hash) == 64
    assert contract.to_dict()["frozen_at"]
    assert contract.basis.description == "Endpoint inventory"
    assert contract.basis.source_refs == ("artifact:artifacts/inventory.json",)
    with pytest.raises(AttributeError):
        contract.criteria.append(
            mod.AcceptanceCriterion(
                id="later",
                description="Moving scope",
                evidence_requirements=[mod.EvidenceRequirement(kind="memory")],
            )
        )

    with pytest.raises(ValueError, match="unique"):
        mod.AcceptanceContract.from_obj(
            {
                "mode": "coverage",
                "basis": {
                    "kind": "snapshot",
                    "description": "Inventory",
                    "source_refs": ["artifact:inventory.json"],
                },
                "criteria": [
                    {
                        "id": "same",
                        "description": "One",
                        "evidence_requirements": [{"kind": "artifact"}],
                    },
                    {
                        "id": "same",
                        "description": "Two",
                        "evidence_requirements": [{"kind": "artifact"}],
                    },
                ],
            }
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "object/dict"),
        (
            {
                "mode": "invalid",
                "basis": {
                    "kind": "snapshot",
                    "description": "Inventory",
                    "source_refs": ["memory:inventory"],
                },
                "criteria": [{"id": "criterion", "description": "Check it", "evidence_requirements": [{"kind": "memory"}]}],
            },
            "mode",
        ),
        ({"mode": "outcome", "basis": "inventory"}, "basis must"),
        (
            {
                "mode": "outcome",
                "basis": {"kind": "snapshot", "description": "Inventory", "source_refs": []},
                "criteria": [{"id": "criterion", "description": "Check it", "evidence_requirements": [{"kind": "memory"}]}],
            },
            "source_refs required",
        ),
        (
            {
                "mode": "outcome",
                "basis": {"kind": "snapshot", "description": "Inventory", "source_refs": ["memory:inventory"]},
                "criteria": [],
            },
            "criteria required",
        ),
        (
            {
                "mode": "outcome",
                "basis": {"kind": "snapshot", "description": "Inventory", "source_refs": ["endpoints"]},
                "criteria": [{"id": "criterion", "description": "Check it", "evidence_requirements": [{"kind": "memory"}]}],
            },
            "must use",
        ),
    ],
)
def test_acceptance_contract_rejects_unbounded_or_malformed_manifests(payload, message):
    with pytest.raises(ValueError, match=message):
        mod.AcceptanceContract.from_obj(payload)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"criterion_id": "", "status": "satisfied", "summary": "Result", "evidence_refs": ["memory:x"]},
        {"criterion_id": "criterion", "status": "unknown", "summary": "Result", "evidence_refs": ["memory:x"]},
        {"criterion_id": "criterion", "status": "satisfied", "summary": "", "evidence_refs": ["memory:x"]},
        {"criterion_id": "criterion", "status": "satisfied", "summary": "Result", "evidence_refs": []},
    ],
)
def test_acceptance_result_rejects_non_terminal_or_evidenceless_results(payload):
    with pytest.raises(ValueError):
        mod.AcceptanceResult.from_obj(payload)


def test_task_proposal_accepts_concise_procedure_shape():
    proposal = mod.TaskProposal.model_validate(task_proposal("Check", "Check target"))

    assert proposal.inferred_basis_kind == "procedure"
    assert proposal.limits.max_items == 1
    assert proposal.target_ids == []


def test_task_proposal_defaults_basis_description_to_objective():
    payload = task_proposal("Check", "Check target")
    payload.pop("basis_description")

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.effective_basis_description == "Check target"


def test_task_proposal_defaults_procedure_output_to_artifact():
    payload = task_proposal("Check", "Check target")
    payload.pop("output_kind")
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )

    contract = mod._proposal_acceptance_contract(proposal, plan)

    assert contract.basis.procedure.output_kind == "artifact"
    assert contract.criteria[0].evidence_requirements[0].kind == "artifact"


@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        ("title", "title required"),
        ("objective", "objective required"),
        ("basis_description", "basis_description must be non-empty"),
    ],
)
def test_task_proposal_rejects_whitespace_required_fields(field_name, message):
    payload = task_proposal("Check", "Check target")
    payload[field_name] = "   "

    with pytest.raises(ValueError, match=message):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_rejects_model_supplied_criterion_id():
    payload = task_proposal("Check", "Check target")
    payload["criteria"][0]["id"] = "model-owned-id"

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_compiler_generates_readable_criterion_id():
    payload = task_proposal("Check", "Check target", "Store résumé & route inventory!")
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )

    contract = mod._proposal_acceptance_contract(proposal, plan)

    assert [criterion.id for criterion in contract.criteria] == ["store-resume-route-inventory"]


def test_task_proposal_criterion_id_uses_bounded_ordinal_fallback():
    payload = task_proposal("Check", "Check target", "測試")
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )

    contract = mod._proposal_acceptance_contract(proposal, plan)

    assert contract.criteria[0].id == "criterion-1"


def test_task_proposal_rejects_multiple_criteria():
    payload = task_proposal("Check", "Check target")
    payload["criteria"].append({"description": "Second result"})

    with pytest.raises(ValueError, match="at most 1 item"):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_criterion_ids_are_scoped_to_each_task():
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )
    first = mod.TaskProposal.model_validate(task_proposal("First", "First task", "Store result"))
    second = mod.TaskProposal.model_validate(task_proposal("Second", "Second task", "Store result"))

    first_contract = mod._proposal_acceptance_contract(first, plan)
    second_contract = mod._proposal_acceptance_contract(second, plan)

    assert first_contract.criteria[0].id == "store-result"
    assert second_contract.criteria[0].id == "store-result"


def test_task_proposal_limits_require_positive_value():
    payload = task_proposal("Check", "Check target")
    payload["limits"] = {}

    with pytest.raises(ValueError, match="requires at least one discovery procedure limit"):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_requires_limits_field():
    payload = task_proposal("Check", "Check target")
    payload.pop("limits")

    with pytest.raises(ValueError, match="Field required"):
        mod.TaskProposal.model_validate(payload)


def test_snapshot_task_proposal_requires_limits_field_before_discarding_it():
    payload = task_proposal("Review", "Review stored evidence", evidence_kind="memory")
    payload.update({"methods": [], "snapshot_refs": ["memory:m1"]})
    payload.pop("limits")

    with pytest.raises(ValueError, match="Field required"):
        mod.TaskProposal.model_validate(payload)


@pytest.mark.parametrize("field_name", ["methods", "snapshot_refs"])
def test_task_proposal_requires_explicit_basis_arrays(field_name):
    payload = task_proposal("Check", "Check target")
    payload.pop(field_name)

    with pytest.raises(ValueError, match="Field required"):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_list_reports_all_missing_fields_compactly():
    proposals = [
        {"title": "First", "objective": "Assess first", "criteria": [{"description": "Store first"}]},
        {
            "title": "Second",
            "objective": "Assess second",
            "limits": {},
            "criteria": [{"description": "Store second"}],
        },
    ]

    with pytest.raises(ValueError) as raised:
        mod._create_tasks_from_proposals(proposals, prompt_token_limit=48_000)

    message = str(raised.value)
    assert "proposal[0].methods" in message
    assert "proposal[0].limits" in message
    assert "proposal[0].snapshot_refs" in message
    assert "proposal[1].methods" in message
    assert "proposal[1].snapshot_refs" in message
    assert "errors.pydantic.dev" not in message
    assert "input_value" not in message


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"methods": []}, "requires procedure methods"),
        ({"limits": None}, "valid dictionary or instance"),
        ({"snapshot_refs": ["memory:m1"]}, "must not mix"),
        ({"coverage": True}, "Extra inputs are not permitted"),
    ],
)
def test_task_proposal_rejects_invalid_procedure_combinations(updates, message):
    payload = task_proposal("Check", "Check target")
    payload.update(updates)

    with pytest.raises(ValueError, match=message):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_rejects_snapshot_procedure_fields():
    payload = task_proposal("Check", "Check target")
    payload.update({"snapshot_refs": ["memory:m1"]})

    with pytest.raises(ValueError, match="must not mix"):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_rejects_removed_basis_kind():
    payload = task_proposal("Check", "Check target")
    payload["basis_kind"] = "procedure"

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        mod.TaskProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"snapshot_refs": []}, "requires procedure methods or snapshot_refs"),
        ({"methods": ["inspect"]}, "must not mix"),
    ],
)
def test_task_proposal_rejects_invalid_snapshot_combinations(updates, message):
    payload = task_proposal("Check", "Check target")
    payload.update({"methods": [], "limits": {}, "snapshot_refs": ["memory:m1"]})
    payload.update(updates)

    with pytest.raises(ValueError, match=message):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_compiler_builds_snapshot_outcome_contract():
    payload = task_proposal("Review", "Review stored evidence", evidence_kind="memory")
    payload.update({"methods": [], "limits": {"max_items": 7}, "snapshot_refs": ["memory:m1"], "output_kind": "artifact"})
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Review", status="active")],
    )

    contract = mod._proposal_acceptance_contract(proposal, plan)

    assert contract.mode == "outcome"
    assert contract.basis.kind == "snapshot"
    assert contract.basis.source_refs == ("memory:m1",)
    assert contract.basis.procedure is None
    assert mod._normalize_task_proposal(proposal).limits is None


def test_task_proposal_normalizes_inapplicable_snapshot_fields():
    payload = task_proposal("Review", "Review stored evidence", evidence_kind="memory")
    payload.pop("limits")
    payload.update({
        "methods": [],
        "limit": {"max_items": 7},
        "snapshot_refs": ["memory:m1"],
        "output_kind": "inventory_manifest",
    })

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.limits.model_dump(exclude_none=True) == {}
    assert proposal.output_kind == "artifact"


def test_task_proposal_rejects_inventory_wide_procedure_without_snapshot():
    payload = task_proposal(
        "SQL injection testing",
        "Test all endpoints and parameters from the initial endpoint inventory for SQL injection",
        evidence_kind="artifact",
    )

    with pytest.raises(ValueError, match="use canonical snapshot_refs"):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_rejects_model_supplied_evidence_contract():
    payload = task_proposal("Check", "Check target")
    payload["criteria"][0]["evidence"] = [{"kind": "memory"}]

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        mod.TaskProposal.model_validate(payload)


def test_resolve_operation_targets_prefers_objective_literals_over_logical_target():
    targets = mod.resolve_operation_targets(
        "dvwa",
        "Assess http://dvwa.local/login.php and 192.168.56.0/24",
    )

    assert [target.value for target in targets] == ["http://dvwa.local/login.php", "192.168.56.0/24"]
    assert [target.type for target in targets] == ["network", "network_range"]
    assert all(target.source == "objective" for target in targets)


def test_resolve_operation_targets_falls_back_to_logical_bare_target():
    targets = mod.resolve_operation_targets("easypicking.htb", "Security assessment")

    assert len(targets) == 1
    assert targets[0].value == "easypicking.htb"
    assert targets[0].source == "logical_target_fallback"


def test_create_tasks_tool_schema_is_flat_and_controller_owned():
    tool_spec = get_tool_spec(mod.create_tasks)
    task_schema = tool_spec["inputSchema"]["json"]["$defs"]["TaskProposal"]
    criterion_schema = tool_spec["inputSchema"]["json"]["$defs"]["TaskProposalCriterion"]

    assert task_schema["required"] == [
        "title",
        "objective",
        "methods",
        "limits",
        "snapshot_refs",
        "criteria",
    ]
    assert set(task_schema["properties"]) == {
        "title",
        "objective",
        "basis_description",
        "methods",
        "limits",
        "snapshot_refs",
        "output_kind",
        "criteria",
        "target_ids",
    }
    schema_text = json.dumps(task_schema["properties"])
    for removed in ("acceptance", "phase", "status", "target_scope", "gap_policy", "stop_condition", "basis_kind"):
        assert removed not in schema_text
    assert criterion_schema["required"] == ["description"]
    assert set(criterion_schema["properties"]) == {"description"}


def test_inventory_manifest_filters_out_of_scope_items_before_validation(fake_memory_client, monkeypatch):
    _client, store = fake_memory_client
    logger_info = Mock()
    monkeypatch.setattr(mod.logger, "info", logger_info)
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test:8080",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test:8080", type="network")],
    )
    manifest = _write_inventory_manifest()
    payload = json.loads(manifest.read_text())
    payload["items"] = [
        {
            "id": "filesystem-route",
            "target_id": "target-1",
            "kind": "endpoint",
            "value": "http://target.test:8080/var/www/html/index.php",
        },
        {
            "id": "filesystem-route",
            "target_id": "target-1",
            "kind": "endpoint",
            "value": "http://target.test:9090/login.php",
            "attributes": [],
        },
        {
            "id": "external-parameter",
            "target_id": "target-1",
            "kind": "parameter",
            "value": "https://outside.test/search?q=test",
        },
    ]
    manifest.write_text(json.dumps(payload))

    loaded, digest = mod._load_inventory_manifest(f"artifact:{manifest}")

    assert [item["value"] for item in loaded["items"]] == [
        "http://target.test:8080/var/www/html/index.php"
    ]
    persisted = json.loads(manifest.read_text())
    assert persisted == loaded
    canonical = json.dumps(persisted, sort_keys=True, separators=(",", ":"))
    assert digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    logger_info.assert_any_call(
        "Removed out-of-scope inventory items reference=%s removed_count=%d",
        "artifact:inventory.json",
        2,
    )


def test_inventory_manifest_rejects_when_scope_filter_removes_every_item(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test:8080",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test:8080", type="network")],
    )
    manifest = _write_inventory_manifest()
    payload = json.loads(manifest.read_text())
    payload["items"] = [{
        "id": "external",
        "target_id": "target-1",
        "kind": "endpoint",
        "value": "https://outside.test/login",
    }]
    manifest.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="items must be a non-empty list"):
        mod._load_inventory_manifest(f"artifact:{manifest}")

    assert json.loads(manifest.read_text())["items"] == []


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (
            {"id": "malformed", "target_id": "target-1", "kind": "endpoint", "value": "/relative"},
            "absolute HTTP",
        ),
        (
            {"id": "unknown", "target_id": "target-2", "kind": "endpoint", "value": "https://outside.test"},
            "unknown target IDs",
        ),
    ],
)
def test_inventory_manifest_does_not_filter_ambiguous_invalid_items(fake_memory_client, item, message):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    payload = json.loads(manifest.read_text())
    payload["items"] = [item]
    manifest.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        mod._load_inventory_manifest(f"artifact:{manifest}")

    assert json.loads(manifest.read_text())["items"] == [item]


def test_create_tasks_generated_schema_accepts_proposal_and_rejects_legacy_contract():
    schema = get_tool_spec(mod.create_tasks)["inputSchema"]["json"]
    validator = Draft202012Validator(schema)
    task = task_proposal(
        "Generate bounded surface inventory",
        "Run the phase-one discovery procedure against the exact assigned target.",
        "surface-inventory",
        target_ids=["target-1"],
    )

    validator.validate({"tasks": [task]})

    legacy_task = dict(task)
    legacy_task["acceptance"] = acceptance_dict()
    errors = list(validator.iter_errors({"tasks": [legacy_task]}))

    assert any(error.validator == "additionalProperties" for error in errors)

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        mod.TaskProposal.model_validate({**task, "phase": 1})


def test_create_tasks_tool_description_contains_exact_required_shape():
    tool_spec = get_tool_spec(mod.create_tasks)
    description = tool_spec["description"]
    normalized_description = " ".join(description.split())

    assert '"output_kind": "inventory_manifest"' in normalized_description
    assert '"criteria": [{"description": "Store the finite inventory"}]' in normalized_description
    assert '"snapshot_refs": ["artifact:artifacts/inventory.json"]' in normalized_description
    assert "Python infers the basis kind" in description
    assert "route-scoped coverage tasks" in normalized_description


def test_create_tasks_rejects_task_without_required_title():
    with pytest.raises(ValueError, match="title"):
        mod.create_tasks([{"objective": "Enumerate reachable endpoints"}])


def test_create_tasks_rejects_empty_proposal_list():
    with pytest.raises(ValueError, match="must have at least one task"):
        mod.create_tasks([])


def test_create_tasks_compiles_bounded_procedure_proposal(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )

    result = mod.create_tasks(
        [
            task_proposal(
                "Generate bounded surface inventory",
                "Run the bounded phase-one discovery procedure against the assigned target.",
                "surface-inventory",
                target_ids=["target-1"],
            )
        ]
    )

    assert json.loads(result) == {"complete": True, "created_count": 1, "duplicate_count": 0}
    task = store.tasks[0]
    assert task.acceptance.basis.source_refs == ("target:target-1", "plan:phase-1")
    assert task.acceptance.basis.procedure.stop_condition == "first_limit_reached"
    assert task.acceptance.basis.procedure.gap_policy == "record_unassessed"
    assert task.target_scope == "subset"
    assert task.phase == 1
    assert task.status == "pending"


def test_create_tasks_uses_exact_contract_deduplication_and_allows_failed_retry(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    proposal = task_proposal("Inspect target", "Inspect the assigned target", "Store exact result")

    first = json.loads(mod.create_tasks([proposal]))
    store.tasks[0] = replace(store.tasks[0], status="done")
    duplicate = json.loads(mod.create_tasks([proposal]))
    store.tasks[0] = replace(store.tasks[0], status="partial_failure")
    retry = json.loads(mod.create_tasks([proposal]))

    assert first["created_count"] == 1
    assert duplicate == {"complete": True, "created_count": 0, "duplicate_count": 1}
    assert retry["created_count"] == 1


def test_create_tasks_does_not_guess_semantic_duplicates(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
    )
    first = task_proposal(
        "Inspect authenticated admin route",
        "Inspect the authenticated admin route for access behavior",
        "Store access behavior alpha",
    )
    second = task_proposal(
        "Inspect authenticated admin route",
        "Inspect the authenticated admin route for access behaviors",
        "Store access behavior beta",
    )

    result = json.loads(mod.create_tasks([first, second]))

    assert result == {"complete": True, "created_count": 2, "duplicate_count": 0}
    assert len(store.tasks) == 2


def test_create_tasks_accepts_mixed_procedure_and_snapshot_proposals(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    snapshot = {
        "title": "Assess frozen route",
        "objective": "Assess the frozen route",
        "methods": [],
        "limits": {"max_items": 20},
        "snapshot_refs": [f"artifact:{manifest}"],
        "output_kind": "inventory_manifest",
        "criteria": [{"description": "Store route result"}],
        "target_ids": ["target-1"],
    }
    procedure = task_proposal(
        "Inspect headers",
        "Inspect a bounded set of response headers",
        "Store header result",
        target_ids=["target-1"],
    )

    result = json.loads(mod.create_tasks([snapshot, procedure]))

    assert result == {"complete": True, "created_count": 2, "duplicate_count": 0}
    assert {task.acceptance.mode for task in store.tasks} == {"coverage", "outcome"}


def test_create_tasks_infers_all_targets_and_compiles_single_criterion(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess two targets",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[
            mod.OperationTarget(target_id="target-1", value="http://one.test", type="network"),
            mod.OperationTarget(target_id="target-2", value="http://two.test", type="network"),
        ],
    )
    proposal = task_proposal("Map targets", "Map both assigned targets", "inventory")
    mod.create_tasks([proposal])

    task = store.tasks[0]
    assert task.target_scope == "all"
    assert task.target_ids == []
    assert task.acceptance.basis.source_refs == ("target:target-1", "target:target-2", "plan:phase-1")
    assert [criterion.id for criterion in task.acceptance.criteria] == ["inventory"]


def test_create_tasks_accepts_inventory_and_workflow_artifact_procedures(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Mapping", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )

    def task(title, evidence_kind, artifact_name):
        proposal = task_proposal(
            title,
            f"Produce {artifact_name} for http://target.test",
            artifact_name,
            evidence_kind=evidence_kind,
            target_ids=["target-1"],
        )
        proposal["methods"] = ["inspect"]
        proposal["limits"] = {"max_requests": 10}
        return proposal

    result = mod.create_tasks(
        [
            task("Discovery Inventory", "inventory_manifest", "discovery_inventory.json"),
            task("Workflow Mapping", "artifact", "workflow_map.json"),
        ]
    )

    assert json.loads(result)["created_count"] == 2
    output_kinds = {stored.title: stored.acceptance.basis.procedure.output_kind for stored in store.tasks}
    assert output_kinds == {"Discovery Inventory": "inventory_manifest", "Workflow Mapping": "artifact"}


def test_create_tasks_freezes_valid_coverage_manifest(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess inventory",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Coverage", status="active")],
    )
    manifest = _write_inventory_manifest()

    result = mod.create_tasks(
        [
            {
                "title": "Test frozen inventory",
                "objective": "Assess the finite inventory",
                "basis_description": "Frozen endpoint inventory",
                "methods": [],
                "limits": {},
                "snapshot_refs": [f"artifact:{manifest}"],
                "criteria": [
                    {"description": "Assess every item ID in the frozen inventory"}
                ],
            }
        ]
    )

    assert json.loads(result)["created_count"] == 1
    assert len(store.tasks[0].acceptance.basis.snapshot_hash) == 64
    assert store.tasks[0].acceptance.basis.item_ids == ("endpoint-1",)
    assert store.tasks[0].acceptance.criteria[0].evidence_requirements[0].kind == "durable_evidence"


def test_create_tasks_groups_endpoint_parameter_and_trailing_slash_variants(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess inventory",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Coverage", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = Path(mod._operation_output_root()) / "route-inventory.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [
            {
                "id": "login-route",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": "HTTP://TARGET.TEST/login/",
            },
            {
                "id": "login-username",
                "target_id": "target-1",
                "kind": "parameter",
                "value": "http://target.test/login?username=test",
            },
            {
                "id": "login-csrf",
                "target_id": "target-1",
                "kind": "parameter",
                "value": "csrf_token",
                "attributes": {"endpoint_id": "login-route"},
            },
            {
                "id": "security-route",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": "http://target.test/security",
            },
            {
                "id": "login-workflow",
                "target_id": "target-1",
                "kind": "workflow",
                "value": "login then logout",
            },
        ],
        "unassessed_gaps": [],
    }))

    mod.create_tasks([{
        "title": "Assess inventory",
        "objective": "Assess the frozen inventory",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Record the assigned result"}],
    }])

    assert [task.acceptance.basis.item_ids for task in store.tasks] == [
        ("login-route", "login-username", "login-csrf"),
        ("security-route",),
        ("login-workflow",),
    ]
    assert store.tasks[0].title == "Assess endpoint http://target.test/login [target-1]"
    assert store.tasks[1].title == "Assess endpoint http://target.test/security [target-1]"
    assert "login then logout" in store.tasks[2].title
    assert all(task.objective.startswith("Assess the frozen inventory") for task in store.tasks)
    assert all(task.target_scope == "subset" and task.target_ids == ["target-1"] for task in store.tasks)


def test_normalized_route_preserves_service_scheme_and_drops_query():
    http_route = mod._normalized_route("http://TARGET.test/login/?username=test")
    https_route = mod._normalized_route("https://target.test/login")

    assert http_route == ("http://target.test/login", "http://target.test/login")
    assert https_route == ("https://target.test/login", "https://target.test/login")


def test_inventory_url_normalization_preserves_boundary_and_repairs_common_route_errors():
    value = mod._canonical_inventory_url(
        "http://host.docker.internal:4280/vulnerabilities/sqli/vulnerabilities/sqli/?id=1&&",
        "http://host.docker.internal:4280",
    )

    assert value == "http://host.docker.internal:4280/vulnerabilities/sqli/?id=1"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("http://host.docker.internal:4220/login", "registered target"),
        ("/relative/path", "absolute HTTP"),
        ("file:///etc/passwd", "absolute HTTP"),
    ],
)
def test_inventory_url_normalization_rejects_wrong_boundary_and_malformed_routes(value, message):
    with pytest.raises(ValueError, match=message):
        mod._canonical_inventory_url(value, "http://host.docker.internal:4280")


def test_inventory_url_normalization_accepts_target_bound_filesystem_looking_route():
    assert mod._canonical_inventory_url(
        "http://host.docker.internal:4280/etc/passwd",
        "http://host.docker.internal:4280",
    ) == "http://host.docker.internal:4280/etc/passwd"


def test_bound_executable_target_corrects_model_copied_port(monkeypatch):
    plan = mod.OperationPlan(
        objective="Test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assess", status="active")],
        targets=[mod.OperationTarget(
            target_id="target-1",
            value="http://host.docker.internal:4280",
            type="network",
        )],
    )
    task = mod.Task(
        task_uid="task-1",
        title="Assess",
        objective="Assess target",
        acceptance=make_acceptance(),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    store = Mock()
    store.get_tasks.return_value = [task]
    monkeypatch.setattr(mod, "_get_active_plan", lambda: plan)
    monkeypatch.setattr(mod, "_get_plan_store", lambda: store)
    monkeypatch.setattr(mod, "_operation_id", lambda: "operation")

    assert (
        mod.resolve_bound_executable_target("http://host.docker.internal:4220")
        == "http://host.docker.internal:4280"
    )


def test_coverage_route_grouping_does_not_expand_with_context_window():
    manifest = {
        "items": [
            {
                "id": f"endpoint-{index}",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": f"http://target.test/{index}",
            }
            for index in range(20)
        ]
    }

    at_48k = mod._coverage_route_groups(manifest, prompt_token_limit=48_000)
    at_200k = mod._coverage_route_groups(manifest, prompt_token_limit=200_000)

    assert at_48k == at_200k
    assert len(at_48k) == 20


def test_create_tasks_binds_canonical_manifest_per_route_independent_of_context(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess inventory",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Coverage", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = Path(mod._operation_output_root()) / "large-inventory.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [
            {
                "id": f"endpoint-{index}",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": f"http://target.test/{index}",
                "attributes": {},
            }
            for index in range(25)
        ],
        "unassessed_gaps": [],
    }))
    producer = mod.Task(
        task_uid="inventory-task",
        title="Inventory",
        objective="Produce inventory",
        acceptance=make_acceptance("produce-inventory"),
        evidence=[str(manifest)],
        phase=1,
        status="done",
    )
    store.store_task("op1", producer)

    result = mod.build_create_tasks_tool(prompt_token_limit=48_000)(tasks=[{
        "title": "Assess inventory",
        "objective": "Assess every frozen inventory item",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Record a terminal disposition for every assigned item"}],
    }])

    assert json.loads(result)["created_count"] == 25
    route_tasks = [task for task in store.tasks if task.task_uid != "inventory-task"]
    assert all(len(task.acceptance.basis.item_ids) == 1 for task in route_tasks)
    assert route_tasks[0].title == "Assess endpoint http://target.test/0 [target-1]"
    assert route_tasks[-1].title == "Assess endpoint http://target.test/24 [target-1]"
    canonical_manifest = mod.canonical_artifact_reference(str(manifest))
    assert all(task.acceptance.basis.source_refs == (canonical_manifest,) for task in route_tasks)


def test_create_tasks_coverage_retry_excludes_previously_dispositioned_items(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess inventory",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Coverage", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = Path(mod._operation_output_root()) / "retry-inventory.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [
            {
                "id": f"endpoint-{index}",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": f"http://target.test/{index}",
                "attributes": {},
            }
            for index in range(3)
        ],
        "unassessed_gaps": [],
    }))
    first_payload = {
        "title": "Assess inventory",
        "objective": "Assess every frozen inventory item",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Record every assigned disposition"}],
    }
    mod.create_tasks([first_payload])
    created = list(store.tasks)
    first = replace(created[0], status="partial_failure")
    for task in created:
        store.store_task("op1", replace(task, status="partial_failure"))
    store.acceptance_results[first.task_uid] = [mod.AcceptanceResult(
        criterion_id=first.acceptance.criteria[0].id,
        status="satisfied",
        disposition="observation",
        summary="One item assessed",
        evidence_refs=(f"artifact:{manifest}",),
        coverage=(mod.CoverageResult(
            item_id="endpoint-0",
            status="assessed_negative",
            evidence_refs=(f"artifact:{manifest}",),
        ),),
    )]

    result = mod.create_tasks([first_payload])

    assert json.loads(result)["created_count"] == 2
    assert [task.acceptance.basis.item_ids for task in store.tasks[-2:]] == [
        ("endpoint-1",),
        ("endpoint-2",),
    ]


def test_create_tasks_coverage_deduplication_is_scoped_to_active_phase(fake_memory_client):
    _client, store = fake_memory_client
    manifest = Path(mod._operation_output_root()) / "cross-phase-inventory.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [{
            "id": "endpoint-login",
            "target_id": "target-1",
            "kind": "endpoint",
            "value": "http://target.test/login",
            "attributes": {},
        }],
        "unassessed_gaps": [],
    }))
    phases = [
        mod.PlanPhase(id=1, title="Mapping", status="active"),
        mod.PlanPhase(id=2, title="Assessment", status="pending"),
    ]
    store.plan = mod.OperationPlan(
        objective="Map and assess inventory",
        current_phase=1,
        total_phases=2,
        phases=phases,
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    payload = {
        "title": "Assess inventory",
        "objective": "Assess every frozen inventory item",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Record every assigned disposition"}],
    }

    assert json.loads(mod.create_tasks([payload]))["created_count"] == 1
    phase_one_task = replace(store.tasks[0], status="done")
    store.store_task("op1", phase_one_task)
    store.acceptance_results[phase_one_task.task_uid] = [mod.AcceptanceResult(
        criterion_id=phase_one_task.acceptance.criteria[0].id,
        status="assessed_negative",
        disposition="no_vulnerability",
        summary="Mapped in phase one",
        evidence_refs=(f"artifact:{manifest}",),
        coverage=(mod.CoverageResult(
            item_id="endpoint-login",
            status="assessed_negative",
            evidence_refs=(f"artifact:{manifest}",),
        ),),
    )]
    store.plan = replace(
        store.plan,
        current_phase=2,
        phases=[
            replace(phases[0], status="done"),
            replace(phases[1], status="active"),
        ],
    )

    result = json.loads(mod.create_tasks([payload]))

    assert result["created_count"] == 1
    assert store.tasks[-1].phase == 2
    assert store.tasks[-1].acceptance.basis.item_ids == ("endpoint-login",)


def test_bound_create_tasks_tool_keeps_duplicate_only_call_correctable(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
    )
    duplicate = task_proposal("Existing task", "Inspect target", "existing")
    mod.create_tasks([duplicate])
    create_tool = mod.build_create_tasks_tool()

    assert json.loads(create_tool(tasks=[duplicate]))["created_count"] == 0
    distinct = task_proposal("Distinct task", "Inspect another behavior", "distinct")
    assert json.loads(create_tool(tasks=[distinct]))["created_count"] == 1
    with pytest.raises(ValueError, match="already completed"):
        create_tool(tasks=[distinct])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("complete", "satisfied"),
        ("completed", "satisfied"),
        ("success", "satisfied"),
        ("successful", "satisfied"),
        ("negative", "assessed_negative"),
        ("no-finding", "assessed_negative"),
        ("no vulnerability", "assessed_negative"),
        ("not_vulnerable", "assessed_negative"),
        ("unreachable", "inaccessible"),
        ("not accessible", "inaccessible"),
        ("out-of-scope", "excluded"),
        ("duplicated", "duplicate"),
    ],
)
def test_acceptance_status_aliases_are_normalized(value, expected):
    assert mod._normalize_acceptance_status_alias(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("assessed_negative", "no_vulnerability"),
        ("negative", "no_vulnerability"),
        ("no finding", "no_vulnerability"),
        ("no-vuln", "no_vulnerability"),
        ("not_vulnerable", "no_vulnerability"),
        ("informational", "observation"),
        ("finding", "finding_candidate"),
        ("candidate", "finding_candidate"),
        ("existing", "existing_finding"),
        ("duplicate finding", "existing_finding"),
    ],
)
def test_acceptance_disposition_aliases_are_normalized(value, expected):
    assert mod._normalize_acceptance_disposition_alias(value) == expected


def test_bound_acceptance_tool_normalizes_aliases_before_validation(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="alias-acceptance",
        title="Test aliases",
        objective="Record a negative assessment",
        acceptance=make_acceptance("alias-outcome"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    result = mod.build_record_task_acceptance_tool(task.task_uid)(
        status="no-finding",
        disposition="assessed-negative",
        summary="No vulnerability was demonstrated",
        evidence_refs=[f"artifact:{manifest}"],
    )

    assert json.loads(result)["complete"] is True
    recorded = store.get_acceptance_results("op1", task.task_uid)[0]
    assert recorded.status == "assessed_negative"
    assert recorded.disposition == "no_vulnerability"


def test_acceptance_alias_normalizers_leave_unknown_values_for_strict_validation():
    assert mod._normalize_acceptance_status_alias("mystery") == "mystery"
    assert mod._normalize_acceptance_disposition_alias("mystery") == "mystery"


def test_bound_acceptance_tool_rejects_unknown_aliases(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="unknown-alias",
        title="Reject aliases",
        objective="Reject unknown aliases",
        acceptance=make_acceptance("unknown-alias"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    acceptance_tool = mod.build_record_task_acceptance_tool(task.task_uid)

    with pytest.raises(ValueError, match="status must be one of"):
        acceptance_tool(
            status="mystery",
            disposition="observation",
            summary="Unknown status",
            evidence_refs=[f"artifact:{manifest}"],
        )
    with pytest.raises(ValueError, match="disposition is invalid"):
        acceptance_tool(
            status="satisfied",
            disposition="mystery",
            summary="Unknown disposition",
            evidence_refs=[f"artifact:{manifest}"],
        )


def test_bound_create_tasks_tool_allows_correction_then_only_one_success(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
    )
    create_tool = mod.build_create_tasks_tool()
    with pytest.raises(ValueError, match="title"):
        create_tool(tasks=[{"objective": "Invalid"}])

    result = create_tool(tasks=[task_proposal("Valid inventory task", "Run bounded inventory work", "inventory")])

    assert json.loads(result)["complete"] is True
    assert len(store.tasks) == 1
    with pytest.raises(ValueError, match="already completed"):
        create_tool(
            tasks=[
                {
                    **task_proposal("Duplicate successful call", "Must not be stored", "duplicate"),
                }
            ]
        )
    assert len(store.tasks) == 1


def test_bound_create_tasks_tool_exposes_strict_controller_owned_schema(fake_memory_client):
    del fake_memory_client
    schema = mod.build_create_tasks_tool().tool_spec["inputSchema"]["json"]
    schema_text = json.dumps(schema, sort_keys=True)

    assert '"additionalProperties": false' in schema_text
    assert '"max_requests"' in schema_text
    assert '"max_items"' in schema_text
    assert '"max_tests"' not in schema_text
    task_schema = schema["$defs"]["TaskProposal"]
    assert "limits" in task_schema["required"]
    assert set(task_schema["properties"]) == {
        "title",
        "objective",
        "basis_description",
        "methods",
        "limits",
        "snapshot_refs",
        "output_kind",
        "criteria",
        "target_ids",
    }


def test_task_proposal_limits_remove_nullable_unused_values():
    proposal = task_proposal("Valid inventory task", "Run bounded discovery", "inventory")
    proposal["limits"].update({"max_duration_minutes": None, "max_requests": 500, "max_depth": 3})

    validated = mod.TaskProposal.model_validate(proposal)

    assert validated.limits.model_dump(exclude_none=True) == {
        "max_items": 1,
        "max_requests": 500,
        "max_depth": 3,
    }


def test_create_tasks_rejects_missing_or_incomplete_coverage_basis_atomically(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
    )
    missing = Path(mod._operation_output_root()) / "missing.json"

    with pytest.raises(ValueError, match="Artifact does not exist"):
        mod.create_tasks(
            [
                task_proposal("Valid prerequisite", "Create an inventory", "create-inventory"),
                {
                    "title": "Invalid dependent coverage",
                    "objective": "Consume a missing inventory",
                    "basis_description": "Missing inventory",
                    "methods": [],
                    "limits": {},
                    "snapshot_refs": [f"artifact:{missing}"],
                    "criteria": [
                        {"description": "Assess every item in the missing inventory"}
                    ],
                },
            ]
        )


def test_acceptance_basis_reference_resolution_rejects_invalid_sources(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    def snapshot(*references):
        return mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="snapshot",
                description="Existing evidence",
                source_refs=list(references),
            ),
            criteria=[
                mod.AcceptanceCriterion(
                    id="result",
                    description="Review the finite evidence",
                    evidence_requirements=[mod.EvidenceRequirement(kind="memory")],
                )
            ],
        )

    with pytest.raises(ValueError, match="task is missing or not done"):
        mod._freeze_and_validate_acceptance(snapshot("task:missing"), [])
    with pytest.raises(ValueError, match="memory does not exist"):
        mod._freeze_and_validate_acceptance(snapshot("memory:missing"), [])
    with pytest.raises(ValueError, match="finding does not exist"):
        mod._freeze_and_validate_acceptance(snapshot("finding:missing"), [])
    with pytest.raises(ValueError, match="may reference only"):
        mod._freeze_and_validate_acceptance(snapshot("target:target-1"), [])

    done = mod.Task(
        task_uid="done-source",
        title="Done",
        objective="Done",
        acceptance=make_acceptance("done"),
        phase=1,
        status="done",
    )
    assert mod._freeze_and_validate_acceptance(snapshot("task:done-source"), [done]).basis.kind == "snapshot"
    assert mod._freeze_and_validate_acceptance(snapshot("memory:m1"), []).basis.kind == "snapshot"
    store.findings["finding-1"] = {"resolution": "verified"}
    assert mod._freeze_and_validate_acceptance(snapshot("finding:finding-1"), []).basis.kind == "snapshot"

    outside = Path(mod._operation_output_root()).parent / "outside.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("outside")
    with pytest.raises(ValueError, match="outside"):
        mod._artifact_path_from_ref(f"artifact:{outside}")

    assert store.tasks == []

    manifest = _write_inventory_manifest()
    producer = mod.Task(
        task_uid="producer",
        title="Inventory producer",
        objective="Produce inventory",
        acceptance=make_acceptance("produce"),
        evidence=[str(manifest)],
        phase=1,
        status="partial_failure",
    )
    store.store_task("op1", producer)

    with pytest.raises(ValueError, match="task is missing or not done"):
        mod.create_tasks(
            [
                {
                    "title": "Coverage with failed producer",
                    "objective": "Consume inventory",
                    "basis_description": "Frozen inventory",
                    "methods": [],
                    "limits": {},
                    "snapshot_refs": ["task:producer"],
                    "criteria": [
                        {"description": "Assess every frozen inventory item"}
                    ],
                }
            ]
        )


def test_bound_acceptance_validates_coverage_ledger_and_manifest_hash(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    contract = mod._freeze_and_validate_acceptance(
        mod.AcceptanceContract.from_obj(_coverage_acceptance(manifest)),
        [],
    )
    task = mod.Task(
        task_uid="coverage-task",
        title="Coverage",
        objective="Assess inventory",
        acceptance=contract,
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    acceptance_tool = mod.build_record_task_acceptance_tool(task.task_uid)

    result = acceptance_tool(
        status="assessed_negative",
        disposition="no_vulnerability",
        summary="Inventory assessed",
        evidence_refs=[f"artifact:{manifest}"],
    )

    assert json.loads(result)["complete"] is True
    assert store.get_acceptance_results("op1", task.task_uid)[0].coverage[0].item_id == "endpoint-1"
    assert store.get_acceptance_results("op1", task.task_uid)[0].coverage[0].status == "assessed_negative"
    assert store.get_tasks("op1")[0].evidence == [mod.canonical_artifact_reference(str(manifest))]


def test_bound_acceptance_rejects_changed_snapshot_and_wrong_evidence_kind(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    contract = mod._freeze_and_validate_acceptance(
        mod.AcceptanceContract.from_obj(_coverage_acceptance(manifest)),
        [],
    )
    task = mod.Task(
        task_uid="changed-snapshot",
        title="Coverage",
        objective="Assess inventory",
        acceptance=contract,
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "id": "endpoint-1",
                        "target_id": "target-1",
                        "kind": "endpoint",
                        "value": "http://target.test/changed",
                        "attributes": {},
                    }
                ],
                "unassessed_gaps": [],
            }
        )
    )

    with pytest.raises(ValueError, match="changed after task creation"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Changed basis",
            evidence_refs=[f"artifact:{manifest}"],
        )

    observation_contract = mod.AcceptanceContract(
        mode="outcome",
        basis=mod.AcceptanceBasis(
            kind="snapshot",
            description="Stored observation",
            source_refs=["memory:m1"],
        ),
        criteria=[
            mod.AcceptanceCriterion(
                id="observation",
                description="Record the observed result",
                evidence_requirements=[mod.EvidenceRequirement(kind="observation")],
            )
        ],
    )
    observation_task = mod.Task(
        task_uid="observation-task",
        title="Observation",
        objective="Record observation",
        acceptance=observation_contract,
        phase=1,
        status="active",
    )
    store.store_task("op1", observation_task)

    with pytest.raises(ValueError, match="requires 1 observation"):
        mod.build_record_task_acceptance_tool(observation_task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Wrong evidence kind",
            evidence_refs=[f"artifact:{manifest}"],
        )


def test_store_observation_reference_satisfies_bound_acceptance(fake_memory_client):
    _client, store = fake_memory_client
    task = mod.Task(
        task_uid="observation-round-trip",
        title="Identify technology",
        objective="Record the identified technology",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="snapshot",
                description="Stored technology observation",
                source_refs=("memory:m1",),
            ),
            criteria=[
                mod.AcceptanceCriterion(
                    id="technology-identification",
                    description="Record the identified technology",
                    evidence_requirements=[mod.EvidenceRequirement(kind="observation")],
                )
            ],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    observation = json.loads(mod.store_observation("The target identifies as PHP 8"))
    result = json.loads(
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="The response identifies PHP 8",
            evidence_refs=[observation["memory_ref"]],
        )
    )

    assert observation["memory_ref"] == "memory:m1"
    assert result["complete"] is True


def test_evidence_reference_kind_validates_memory_findings_and_prefixes(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()

    assert mod._evidence_reference_kind(f"artifact:{manifest}", "artifact") is True
    assert mod._evidence_reference_kind(f"artifact:{manifest}", "inventory_manifest") is True
    assert mod._evidence_reference_kind(f"artifact:{manifest}", "observation") is False
    assert mod._evidence_reference_kind("memory:m1", "memory") is True
    assert mod._evidence_reference_kind("memory:m1", "observation") is True
    with pytest.raises(ValueError, match="does not exist"):
        mod._evidence_reference_kind("memory:missing", "memory")

    store.findings["candidate"] = {"resolution": None}
    store.findings["verified"] = {"resolution": "verified"}
    assert mod._evidence_reference_kind("finding:candidate", "finding_candidate") is True
    assert mod._evidence_reference_kind("finding:candidate", "verified_finding") is False
    assert mod._evidence_reference_kind("finding:verified", "verified_finding") is True
    with pytest.raises(ValueError, match="does not exist"):
        mod._evidence_reference_kind("finding:missing", "finding_candidate")
    with pytest.raises(ValueError, match="must use"):
        mod._evidence_reference_kind("target:target-1", "memory")

    outcome_task = mod.Task(
        task_uid="outcome",
        title="Outcome",
        objective="Outcome",
        acceptance=make_acceptance("outcome"),
        phase=1,
        status="active",
    )
    result = mod.AcceptanceResult(
        criterion_id="outcome",
        status="satisfied",
        disposition="observation",
        summary="Done",
        evidence_refs=[f"artifact:{manifest}"],
        coverage=[
            mod.CoverageResult(
                item_id="endpoint-1",
                status="satisfied",
                evidence_refs=[f"artifact:{manifest}"],
            )
        ],
    )
    with pytest.raises(ValueError, match="only for coverage"):
        mod._validate_acceptance_result_evidence(
            outcome_task,
            outcome_task.acceptance.criteria[0],
            result,
        )


def test_inventory_requirement_ignores_non_manifest_when_valid_manifest_is_also_referenced(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    manifest_payload = json.loads(manifest.read_text())
    manifest_payload["items"].append({
        "id": "external-link",
        "target_id": "target-1",
        "kind": "endpoint",
        "value": "https://outside.test/reference",
        "attributes": {},
    })
    manifest.write_text(json.dumps(manifest_payload))
    workflow_map = Path(mod._operation_output_root()) / "workflow_map.json"
    workflow_map.write_text(json.dumps({"workflows": [{"name": "login"}]}))
    contract = mod.AcceptanceContract(
        mode="outcome",
        basis=mod.AcceptanceBasis(
            kind="procedure",
            description="Produce a bounded inventory with supporting artifacts",
            source_refs=["target:target-1"],
            procedure={
                "methods": ["crawl"],
                "limits": {"max_items": 10},
                "stop_condition": "first_limit_reached",
                "gap_policy": "record_unassessed",
                "output_kind": "inventory_manifest",
            },
        ),
        criteria=[
            mod.AcceptanceCriterion(
                id="inventory",
                description="Store the finite inventory and supporting workflow artifact",
                evidence_requirements=[
                    mod.EvidenceRequirement(kind="inventory_manifest"),
                    mod.EvidenceRequirement(kind="artifact"),
                ],
            )
        ],
    )
    task = mod.Task(
        task_uid="mixed-artifacts",
        title="Mixed artifact evidence",
        objective="Record inventory and workflow artifacts",
        acceptance=contract,
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    result = mod.build_record_task_acceptance_tool(task.task_uid)(
        status="satisfied",
        disposition="observation",
        summary="Inventory and workflow evidence stored",
        evidence_refs=[f"artifact:{workflow_map}", f"artifact:{manifest}"],
    )

    assert json.loads(result)["complete"] is True
    assert [item["id"] for item in json.loads(manifest.read_text())["items"]] == ["endpoint-1"]


def test_inventory_requirement_ignores_generic_artifact_without_manifest_candidate(fake_memory_client):
    _client, store = fake_memory_client
    workflow_map = Path(mod._operation_output_root()) / "workflow_map.json"
    workflow_map.parent.mkdir(parents=True, exist_ok=True)
    workflow_map.write_text(json.dumps({"workflows": [{"name": "login"}]}))
    task = mod.Task(
        task_uid="malformed-inventory",
        title="Malformed inventory",
        objective="Reject a workflow map used as inventory",
        acceptance=make_acceptance("inventory"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    with pytest.raises(ValueError, match="received 0.*generic artifact evidence"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Wrong artifact type",
            evidence_refs=[f"artifact:{workflow_map}"],
        )
    with pytest.raises(ValueError, match="received 0.*Required root fields"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="No artifact candidate",
            evidence_refs=["memory:m1"],
        )


def test_inventory_acceptance_allows_target_bound_filesystem_looking_route(fake_memory_client):
    _client, store = fake_memory_client
    root = Path(mod._operation_output_root())
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "inventory_manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [{
            "id": "bad-phpinfo",
            "target_id": "target-1",
            "kind": "endpoint",
            "value": "http://target.test/var/www/html/phpinfo.php",
            "attributes": {},
        }],
        "unassessed_gaps": [],
    }))
    task = mod.Task(
        task_uid="invalid-item-inventory",
        title="Invalid item inventory",
        objective="Store a bounded inventory",
        acceptance=make_acceptance("inventory"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    tool = mod.build_record_task_acceptance_tool(task.task_uid)
    result = json.loads(tool(
        status="satisfied",
        disposition="observation",
        summary="Inventory stored",
        evidence_refs=[str(manifest)],
    ))

    assert result["complete"] is True
    recorded = store.get_acceptance_results("op1", task.task_uid)[0]
    assert recorded.evidence_refs == ("artifact:inventory_manifest.json",)


def test_store_plan_does_not_complete_terminal_phases_with_actionable_tasks(fake_memory_client):
    client, store = fake_memory_client
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assess", status="partial_failure")],
    )
    store.store_task("op1", mod.Task(
        task_uid="remaining",
        title="Remaining",
        objective="Assess remaining endpoint",
        acceptance=make_acceptance(),
        phase=1,
        status="pending",
    ))

    result = client.store_plan(plan=plan, operation_id="op1")

    assert plan.assessment_complete is False
    assert "_reminder" not in result


def test_create_tasks_rejects_unknown_target_ids(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        targets=[
            mod.OperationTarget(
                target_id="target-1",
                value="http://target.test",
                type="network",
            )
        ],
    )

    with pytest.raises(ValueError, match="unknown operation target IDs"):
        mod.create_tasks(
            [
                task_proposal(
                    "Check other target",
                    "Check http://other.test",
                    "other-target",
                    target_ids=["target-99"],
                )
            ]
        )

    with pytest.raises(ValueError, match="concrete operation target IDs"):
        mod.create_tasks(
            [task_proposal("Check placeholder", "Check target", "placeholder", target_ids=["target-id"])]
        )


def test_bound_record_task_acceptance_validates_active_frozen_manifest(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="task-1",
        title="Map parameters",
        objective="Map the frozen endpoint inventory",
        acceptance=make_acceptance("endpoint:/login.php"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    acceptance_tool = mod.build_record_task_acceptance_tool("task-1")
    tool_spec = get_tool_spec(acceptance_tool)
    tool_schema = tool_spec["inputSchema"]["json"]
    store.store_task(
        "op1",
        mod.Task(
            task_uid="other-active",
            title="Other active task",
            objective="Do not receive the bound task's results",
            acceptance=make_acceptance("other"),
            phase=1,
            status="active",
        ),
    )

    result = acceptance_tool(
        status="satisfied",
        disposition="observation",
        summary="Login form mapped",
        evidence_refs=[f"artifact:{manifest}"],
    )

    assert json.loads(result) == {
        "complete": True,
        "memory_created": True,
        "memory_published": True,
        "recorded_count": 1,
        "replayed": False,
        "required_count": 1,
    }
    assert len(_client.mem0.add_calls) == 1
    published = _client.mem0.add_calls[0]
    assert published["messages"][0]["content"].startswith('Task acceptance for "Map parameters".')
    assert "Criterion endpoint:/login.php [satisfied; observation]: Login form mapped" in published["messages"][0]["content"]
    assert mod.canonical_artifact_reference(str(manifest)) in published["messages"][0]["content"]
    assert published["metadata"]["category"] == "observation"
    assert published["metadata"]["source"] == "task_acceptance"
    assert published["metadata"]["task_uid"] == "task-1"
    assert store.get_acceptance_results("op1", "task-1")[0].summary == "Login form mapped"
    assert store.get_acceptance_results("op1", "other-active") == []
    assert set(tool_schema["required"]) == {"status", "disposition", "summary", "evidence_refs"}
    assert "task_uid" not in tool_schema["properties"]
    assert '"schema_version": 1' in tool_spec["description"]
    assert "Workflow maps, reports, and arbitrary JSON outputs are artifact evidence" in tool_spec["description"]
    replay = acceptance_tool(
        status="satisfied",
        disposition="observation",
        summary="Replay",
        evidence_refs=[f"artifact:{manifest}"],
    )
    replay_payload = json.loads(replay)
    assert replay_payload["replayed"] is True
    assert replay_payload["memory_published"] is True
    assert replay_payload["memory_created"] is False
    assert len(_client.mem0.add_calls) == 1

    with pytest.raises(ValueError, match="evidence_refs required"):
        acceptance_tool(
            status="satisfied",
            disposition="observation",
            summary="Other mapped",
            evidence_refs=[],
        )

    with pytest.raises(ValueError, match="task_uid required when binding"):
        mod.build_record_task_acceptance_tool("")
    with pytest.raises(ValueError, match="Unknown task_uid"):
        mod.build_record_task_acceptance_tool("missing")

    pending = mod.Task(
        task_uid="task-pending",
        title="Pending",
        objective="Wait",
        acceptance=make_acceptance("pending"),
        phase=1,
        status="pending",
    )
    store.store_task("op1", pending)
    with pytest.raises(ValueError, match="active task"):
        mod.build_record_task_acceptance_tool("task-pending")(
            status="excluded",
            disposition="no_vulnerability",
            summary="Not active",
            evidence_refs=["memory:pending"],
        )


def test_record_task_acceptance_warns_and_preserves_ledger_when_memory_publication_fails(
    fake_memory_client,
    monkeypatch,
):
    client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="publish-failure",
        title="Map target",
        objective="Map the target surface",
        acceptance=make_acceptance("inventory"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    original_store_memory = client.store_memory
    monkeypatch.setattr(client, "store_memory", Mock(side_effect=RuntimeError("memory backend unavailable")))

    result = mod.build_record_task_acceptance_tool(task.task_uid)(
        status="satisfied",
        disposition="observation",
        summary="Mapped the target surface",
        evidence_refs=[f"artifact:{manifest}"],
    )

    payload = json.loads(result)
    assert payload["complete"] is True
    assert payload["memory_published"] is False
    assert payload["memory_created"] is False
    assert "memory backend unavailable" in payload["memory_warning"]
    assert store.get_acceptance_results("op1", task.task_uid)[0].summary == "Mapped the target surface"
    assert store.acceptance_memory_publications == {}

    monkeypatch.setattr(client, "store_memory", original_store_memory)
    replay = json.loads(
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Ignored immutable replay input",
            evidence_refs=[f"artifact:{manifest}"],
        )
    )

    assert replay["replayed"] is True
    assert replay["memory_published"] is True
    assert replay["memory_created"] is True


def test_task_acceptance_memory_is_bounded_and_preserves_terminal_statuses():
    task = mod.Task(
        task_uid="bounded-memory",
        title="Coverage task",
        objective="Assess the frozen inventory",
        acceptance=make_acceptance("coverage"),
        phase=2,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    statuses = list(mod.TERMINAL_ACCEPTANCE_STATUSES)
    results = []
    for index, status in enumerate(statuses):
        coverage = []
        if index == 0:
            coverage = [
                mod.CoverageResult(
                    item_id=f"item-{item_index}",
                    status="assessed_negative" if item_index % 2 else "satisfied",
                    evidence_refs=[f"artifact:/tmp/item-{item_index}.json"],
                )
                for item_index in range(100)
            ]
        results.append(
            mod.AcceptanceResult(
                criterion_id=f"criterion-{index}",
                status=status,
                disposition="observation",
                summary=(f"Concrete {status} result " * 100),
                evidence_refs=[f"artifact:/tmp/evidence-{index}-{ref_index}.json" for ref_index in range(10)],
                coverage=coverage,
            )
        )

    content, metadata, publication_key = mod._task_acceptance_memory_payload(task, results)

    assert len(content) <= mod.TASK_ACCEPTANCE_MEMORY_MAX_CHARS
    assert "Coverage: assessed_negative=50, satisfied=50." in content
    for status in statuses:
        assert f"[{status}; observation]" in content
    assert "item-99" not in content
    assert "Additional evidence references omitted: 30." in content
    assert metadata["source"] == "task_acceptance"
    assert metadata["target_ids"] == "target-1"
    assert metadata["publication_key"] == publication_key


def test_removed_plan_task_tools_are_not_exported_from_tools_module():
    removed = {
        "get_active_task",
        "list_uncompleted_tasks",
        "record_task_acceptance",
        "store_plan",
        "task_done",
    }

    for name in removed:
        assert not hasattr(tools_module, name)
        assert name not in tools_module.__all__
        assert not hasattr(mod, name)

    assert not hasattr(tools_module, "get_plan")
    assert "get_plan" not in tools_module.__all__

    assert hasattr(tools_module, "create_tasks")
    with pytest.raises(ValueError):
        mod.Task(
            task_uid="",
            title="x",
            objective="y",
            acceptance=make_acceptance(),
            phase=1,
            status="pending",
        )

    phase = mod.PlanPhase.from_obj({"id": 1, "title": "Recon", "status": "active", "criteria": None})
    plan = mod.OperationPlan.from_obj(
        {
            "objective": "Assess target",
            "constraints": ["Read-only checks", "Keep evidence in artifacts"],
            "current_phase": 1,
            "phases": [phase.to_dict(), {"id": 2, "title": "Exploit", "status": "pending"}],
        }
    )
    assert "plan_overview[1]" in plan.to_toon()
    assert "plan_constraints[2]{constraint}:" in plan.to_toon()
    assert "Read-only checks" in plan.to_toon()
    assert plan.constraints_to_toon() == (
        "plan_constraints[2]{constraint}:\n  Read-only checks\n  Keep evidence in artifacts"
    )
    assert plan.to_dict()["constraints"] == ["Read-only checks", "Keep evidence in artifacts"]
    assert plan.total_phases == 2
    assert mod.OperationPlan.from_obj(plan) is plan
    legacy_plan = mod.OperationPlan.from_obj(
        {
            "objective": "Legacy",
            "current_phase": 1,
            "phases": [{"id": 1, "title": "Recon", "status": "active"}],
        }
    )
    assert legacy_plan.constraints == []
    assert "plan_constraints[0]{constraint}:" in legacy_plan.to_toon()
    with pytest.raises(ValueError):
        mod.PlanPhase(id=-1, title="bad", status="pending")
    with pytest.raises(ValueError):
        mod.OperationPlan(objective="x", current_phase=1, total_phases=1, phases=[])
    scalar_plan = mod.OperationPlan.from_obj(
        {
            "objective": "Scalar",
            "constraints": "  read-only  ",
            "current_phase": 1,
            "phases": [{"id": 1, "title": "Recon", "status": "active"}],
        }
    )
    assert scalar_plan.constraints == ["read-only"]
    tuple_plan = mod.OperationPlan(
        objective="Tuple",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        constraints=("  first  ", 2),
    )
    assert tuple_plan.constraints == ["first", "2"]
    null_plan = mod.OperationPlan(
        objective="Null",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        constraints=None,
    )
    assert null_plan.constraints == []
    with pytest.raises(ValueError, match="string, list, tuple, or null"):
        mod.OperationPlan(
            objective="Bad",
            current_phase=1,
            total_phases=1,
            phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
            constraints={"constraint": "read-only"},
        )
    with pytest.raises(ValueError, match="coercible to non-empty strings"):
        mod.OperationPlan(
            objective="Bad",
            current_phase=1,
            total_phases=1,
            phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
            constraints=[""],
        )
    with pytest.raises(ValueError, match="coercible to non-empty strings"):
        mod.OperationPlan(
            objective="Bad nested shape",
            current_phase=1,
            total_phases=1,
            phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
            constraints=[["nested"]],
        )

    class UnstringifiableConstraint:
        def __str__(self):
            raise TypeError("cannot stringify")

    with pytest.raises(ValueError, match="coercible to non-empty strings"):
        mod.OperationPlan(
            objective="Bad conversion",
            current_phase=1,
            total_phases=1,
            phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
            constraints=[UnstringifiableConstraint()],
        )


def test_memory_helpers_and_tool_wrappers(fake_memory_client, monkeypatch, tmp_path):
    client, store = fake_memory_client
    proof = tmp_path / "proof.txt"
    proof.write_text("proof")

    assert mod._normalize_evidence({"a": 1}) == ['{"a": 1}']
    assert mod.sanitize_toon_value("a,b\nc") == "a;b c"
    assert mod.sanitize_toon_value("\ta,  b\r\n c") == "a; b c"
    assert mod._normalize_id("https://x.test/users/123?id=456").count(":id") == 1
    assert "/admin/:id" in mod._extract_sensitive_patterns("see /admin/123 and ./file.txt")
    assert mod._has_valid_proof_pack({"proof_pack": {"artifacts": [str(proof)]}}) is True
    assert memory_tasks.active_task_message(mod, None, current_phase=2).startswith("<active_task")
    assert mod.memory_create_time({"metadata": {"created_at": "1"}}) == "1"
    monkeypatch.setenv("MEMORY_ISOLATION", "shared")
    assert mod.memory_is_cross_operation() is True
    monkeypatch.setenv("MEMORY_ISOLATION", "operation")

    stored = mod.store_observation(
        "[OBSERVATION] confirmed issue",
        metadata={
            "severity": "HIGH",
            "confidence": "95%",
        },
    )
    assert json.loads(stored) == {
        "stored": True,
        "created": True,
        "memory_ref": "memory:m1",
    }
    metadata = client.mem0.add_calls[0]["metadata"]
    assert metadata["category"] == "observation"
    assert metadata["severity"] == "HIGH"

    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Done", status="done")],
    )
    assert "All phases complete" in memory_tasks.store_plan(mod, plan)
    assert "plan_overview[1]" in mod.get_plan()

    created = mod.create_tasks(
        [
            task_proposal("First", "Do first", "first"),
            task_proposal("Second", "Do second", "second"),
        ]
    )
    assert json.loads(created)["complete"] is True
    assert "task[" in memory_tasks.list_uncompleted_tasks(mod)
    assert "<active_task" in memory_tasks.mark_task_done(mod, "done")

    assert "- active" in mod.mem0_list()
    assert "- finding one" in mod.mem0_retrieve("finding", {"category": "finding"})


def test_create_tasks_assigns_every_proposal_to_active_phase(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess",
        current_phase=3,
        total_phases=4,
        phases=[
            mod.PlanPhase(id=1, title="One", status="done"),
            mod.PlanPhase(id=2, title="Two", status="done"),
            mod.PlanPhase(id=3, title="Three", status="active"),
            mod.PlanPhase(id=4, title="Four", status="pending"),
        ],
    )

    result = mod.create_tasks(
        [
            task_proposal("First", "First task", "first"),
            task_proposal("Second", "Second task", "second"),
        ]
    )

    assert json.loads(result)["created_count"] == 2
    phases_by_title = {task.title: task.phase for task in store.tasks}
    assert phases_by_title == {"First": 3, "Second": 3}
    assert {task.status for task in store.tasks} == {"pending"}


def test_create_tasks_requires_active_plan(fake_memory_client):
    del fake_memory_client

    with pytest.raises(ValueError, match="no_active_plan"):
        mod.create_tasks([task_proposal("No plan", "Cannot compile without a plan", "no-plan")])


def test_inventory_reconciliation_adds_in_scope_html_routes(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    artifacts_dir = Path(mod._operation_output_root()) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "root.artifact.log").write_text(
        '<html><a href="/new-route/?id=1">New</a><a href="https://outside.test/nope">Outside</a></html>'
    )

    reconciled, _digest = mod._load_inventory_manifest(f"artifact:{manifest}", reconcile=True)

    added = [item for item in reconciled["items"] if item["id"].startswith("auto-endpoint-")]
    assert [item["value"] for item in added] == ["http://target.test/new-route/?id=1"]
    assert added[0]["attributes"]["query_parameters"] == ["id"]
    assert reconciled["extraction"] == {
        "source_artifact_count": 1,
        "candidate_count": 1,
        "added_count": 1,
    }


def test_artifact_id_resolves_and_canonicalizes_within_operation(fake_memory_client):
    _client, _store = fake_memory_client
    artifacts_dir = Path(mod._operation_output_root()) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact = artifacts_dir / "response.artifact.log"
    artifact.write_text("evidence")

    assert mod.canonical_artifact_reference(f"artifact_id:{artifact.name}") == (
        "artifact:artifacts/response.artifact.log"
    )
    with pytest.raises(ValueError, match="one artifact filename"):
        mod.canonical_artifact_reference("artifact_id:../response.artifact.log")


def test_inventory_typed_fanout_and_unsupported_kind(fake_memory_client):
    _client, _store = fake_memory_client
    _store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    payload = json.loads(manifest.read_text())
    payload["items"].append(
        {
            "id": "technology-1",
            "target_id": "target-1",
            "kind": "technology",
            "value": "Example Server 1.0",
            "attributes": {},
        }
    )
    manifest.write_text(json.dumps(payload))

    result = json.loads(mod.create_tasks([{
        "title": "Assess inventory",
        "objective": "Assess the frozen inventory",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Assess the assigned inventory unit"}],
        "target_ids": ["target-1"],
    }]))

    assert result["created_count"] == 2
    titles = [task.title for task in _store.tasks]
    assert any(title.startswith("Assess endpoint ") for title in titles)
    assert "Validate technology Example Server 1.0 [target-1]" in titles

    payload["items"][-1]["kind"] = "other"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="supported kind"):
        mod._load_inventory_manifest(f"artifact:{manifest}")


def test_inventory_snapshot_fanout_deduplicates_staged_routes(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    proposal = {
        "title": "Assess inventory",
        "objective": "Assess the frozen inventory",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Assess the assigned inventory unit"}],
        "target_ids": ["target-1"],
    }

    result = json.loads(mod.create_tasks([proposal, {**proposal, "title": "Review the same inventory"}]))

    assert result == {"complete": True, "created_count": 1, "duplicate_count": 1}
    assert len(store.tasks) == 1


def test_inventory_snapshot_fanout_preserves_distinct_objectives(fake_memory_client, monkeypatch):
    _client, store = fake_memory_client
    logger_info = Mock()
    monkeypatch.setattr(mod.logger, "info", logger_info)
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    common = {
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "target_ids": ["target-1"],
    }
    authentication = {
        **common,
        "title": "Authentication mapping",
        "objective": "Map authentication and session management behavior",
        "criteria": [{"description": "Map authentication entry points and session persistence"}],
    }
    authorization = {
        **common,
        "title": "Authorization mapping",
        "objective": "Map authorization and role boundary behavior",
        "criteria": [{"description": "Map roles and access-control boundaries"}],
    }

    result = json.loads(mod.create_tasks([authentication, authorization]))

    assert result == {"complete": True, "created_count": 2, "duplicate_count": 0}
    assert len(store.tasks) == 2
    assert store.tasks[0].acceptance.basis.item_ids == store.tasks[1].acceptance.basis.item_ids
    assert "authentication and session" in store.tasks[0].objective.lower()
    assert "authorization and role" in store.tasks[1].objective.lower()
    fanout_calls = [call for call in logger_info.call_args_list if "Task proposal fan-out" in call.args[0]]
    assert len(fanout_calls) == 2
    assert fanout_calls[0].args[1:] == (0, "Authentication mapping", authentication["objective"], 1, 1, 0)
    assert fanout_calls[1].args[1:] == (1, "Authorization mapping", authorization["objective"], 1, 1, 0)


def test_inventory_snapshot_fanout_preserves_distinct_acceptance_intent(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    proposal = {
        "title": "Map trust boundaries",
        "objective": "Map the assigned endpoint's trust boundaries",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Record session transition boundaries"}],
        "target_ids": ["target-1"],
    }
    role_intent = {
        **proposal,
        "criteria": [{"description": "Record role-based access boundaries"}],
    }

    result = json.loads(mod.create_tasks([proposal, role_intent]))

    assert result == {"complete": True, "created_count": 2, "duplicate_count": 0}
    descriptions = [task.acceptance.criteria[0].description for task in store.tasks]
    assert any("session transition boundaries" in description for description in descriptions)
    assert any("role-based access boundaries" in description for description in descriptions)


def test_exhausted_snapshot_with_gap_creates_inventory_refinement(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Assessment", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = _write_inventory_manifest()
    payload = json.loads(manifest.read_text())
    payload["unassessed_gaps"] = ["Check links revealed after authentication"]
    manifest.write_text(json.dumps(payload))
    proposal = {
        "title": "Assess inventory",
        "objective": "Assess the frozen inventory",
        "methods": [],
        "limits": {},
        "snapshot_refs": [f"artifact:{manifest}"],
        "criteria": [{"description": "Assess the assigned inventory unit"}],
        "target_ids": ["target-1"],
    }
    mod.create_tasks([proposal])
    first_task = store.tasks[0]
    store.acceptance_results[first_task.task_uid] = [mod.AcceptanceResult(
        criterion_id=first_task.acceptance.criteria[0].id,
        status="assessed_negative",
        disposition="no_vulnerability",
        summary="Endpoint assessed",
        evidence_refs=(f"artifact:{manifest}",),
        coverage=(mod.CoverageResult(
            item_id="endpoint-1",
            status="assessed_negative",
            evidence_refs=(f"artifact:{manifest}",),
        ),),
    )]

    result = json.loads(mod.create_tasks([proposal]))

    assert result["snapshot_exhausted"] is True
    assert result["unresolved_gaps"] == ["Check links revealed after authentication"]
    assert store.tasks[-1].title == "Refine exhausted inventory"
    assert store.tasks[-1].acceptance.basis.procedure.output_kind == "inventory_manifest"


def test_acceptance_disposition_requires_finding_for_confirmed_behavior(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="finding-disposition",
        title="Test behavior",
        objective="Test one behavior",
        acceptance=make_acceptance("outcome"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    acceptance_tool = mod.build_record_task_acceptance_tool(task.task_uid)

    with pytest.raises(ValueError, match="require finding_candidate"):
        acceptance_tool(
            status="satisfied",
            disposition="observation",
            summary="Confirmed an exploitable injection vulnerability",
            evidence_refs=[f"artifact:{manifest}"],
        )

    store.findings["candidate-1"] = {
        "candidate_data": {"source_task_uids": [task.task_uid]},
        "resolution": None,
    }
    result = acceptance_tool(
        status="satisfied",
        disposition="finding_candidate",
        summary="Confirmed an exploitable injection vulnerability",
        evidence_refs=[f"artifact:{manifest}", "finding:placeholder-from-model"],
    )
    assert json.loads(result)["complete"] is True
    assert store.get_acceptance_results("op1", task.task_uid)[0].evidence_refs[-1] == "finding:candidate-1"


def test_acceptance_finding_auto_binding_rejects_missing_and_ambiguous_candidates(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="finding-binding",
        title="Test behavior",
        objective="Test one behavior",
        acceptance=make_acceptance("outcome"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    acceptance_tool = mod.build_record_task_acceptance_tool(task.task_uid)

    with pytest.raises(ValueError, match="finding created by this task"):
        acceptance_tool(
            status="satisfied",
            disposition="finding_candidate",
            summary="Confirmed an exploitable injection vulnerability",
            evidence_refs=[f"artifact:{manifest}"],
        )

    for finding_uid in ("candidate-1", "candidate-2"):
        store.findings[finding_uid] = {
            "candidate_data": {"source_task_uids": [task.task_uid]},
            "resolution": None,
        }
    with pytest.raises(ValueError, match="ambiguous.*finding:candidate-1.*finding:candidate-2"):
        acceptance_tool(
            status="satisfied",
            disposition="finding_candidate",
            summary="Confirmed an exploitable injection vulnerability",
            evidence_refs=[f"artifact:{manifest}"],
        )


def test_mem0_service_client_methods_and_fallbacks(fake_memory_client, monkeypatch):
    client, store = fake_memory_client

    assert mod.Mem0ServiceClient._remove_inactive(None) == []
    assert mod.Mem0ServiceClient._coerce_entry(["a"])["memory"] == '["a"]'
    assert mod.Mem0ServiceClient._normalise_results_list({"data": ["x"]}) == [{"memory": "x", "metadata": {}}]

    client.store_memory("content", user_id="u1", metadata={"category": "finding"})
    assert client.mem0.add_calls[-1]["run_id"] == "op1"

    listed = client.list_memories(user_id="u1", limit=3, run_id="op1")
    assert [entry["memory"] for entry in listed] == ["active", "plain text", ""]

    memory = client.get_memory_by_id("m1", user_id="u1")
    assert memory == {
        "id": "m1",
        "memory": "direct memory",
        "metadata": {"active": True, "category": "observation", "operation_id": "op1"},
    }
    assert client.mem0.get_calls == ["m1"]
    assert client.get_memory_by_id("inactive", user_id="u1") is None
    assert client.get_memory_by_id("", user_id="u1") is None

    found = client.search(query="finding", filters={"category": "finding"}, limit=5, user_id="u1", run_id="op1")
    assert found[0]["memory"] == "finding one"
    client.mem0 = SimpleNamespace()
    client.list_memories = Mock(
        return_value=[
            {"memory": "alpha beta", "metadata": {"category": "finding", "operation_id": "op1"}},
            {"memory": "alpha", "metadata": {"category": "observation", "operation_id": "op1"}},
            {"memory": "other", "metadata": {"category": "finding", "operation_id": "other"}},
        ]
    )
    fallback = client.search(query="alpha beta", filters={"category": "finding"}, limit=2, user_id="u1", run_id="op1")
    assert fallback == [{"memory": "alpha beta", "metadata": {"category": "finding", "operation_id": "op1"}}]

    prev = mod.OperationPlan(
        objective="Old",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Old", status="done")],
        assessment_complete=True,
    )
    store.plan = prev
    expanded = mod.OperationPlan(
        objective="New",
        current_phase=1,
        total_phases=2,
        phases=[
            mod.PlanPhase(id=1, title="Old", status="done"),
            mod.PlanPhase(id=2, title="New", status="pending"),
        ],
    )
    result = mod.Mem0ServiceClient.store_plan(client, expanded, operation_id="op1")
    assert result["status"] == "success"
    assert "_reminder" in result

    store.tasks = [
        mod.Task(
            task_uid="a", title="A", objective="A", acceptance=make_acceptance("a"),
            phase=1, status="active", created_at="1"
        ),
        mod.Task(
            task_uid="b", title="B", objective="B", acceptance=make_acceptance("b"),
            phase=1, status="pending", created_at="2"
        ),
    ]
    updated, next_active = client.advance_task_in_phase(user_id="u1", phase=1, new_status="done", task_uid="a")
    assert updated.status == "done"
    assert next_active.status == "active"
    active, activated = client.get_or_activate_next_task_in_phase(user_id="u1", phase=1)
    assert active.status == "active"
    assert activated is False
    assert len(client.list_tasks(user_id="u1", phase=1, status=["active", "pending"])) >= 1

    client.list_memories = Mock(return_value=[{"memory": "F" * 120, "metadata": {"category": "finding"}, "created_at": "3"}])
    overview = client.get_memory_overview(user_id="u1")
    assert overview["has_memories"] is True


def test_mem0_platform_store_waits_for_durable_memory_id(fake_memory_client, monkeypatch):
    client, _store = fake_memory_client

    class FakePlatformMemoryClient:
        def __init__(self):
            self.add_calls = []

        def add(self, **kwargs):
            self.add_calls.append(kwargs)
            return {"results": [{"id": "platform-memory"}]}

    platform = FakePlatformMemoryClient()
    client.mem0 = platform
    monkeypatch.setattr(mod, "MemoryClient", FakePlatformMemoryClient)

    result = client.store_memory("content", user_id="u1", metadata={"category": "observation"})

    assert result["results"][0]["id"] == "platform-memory"
    assert platform.add_calls[0]["async_mode"] is False
