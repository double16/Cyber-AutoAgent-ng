import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from jsonschema import Draft202012Validator
import pytest

import modules.tools as tools_module
from modules.handlers.utils import get_tool_spec
from modules.tools import memory as mod
from tests.helpers import memory_tasks
from tests.helpers.acceptance import acceptance_dict, make_acceptance, task_proposal


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

    assert proposal.basis_kind == "procedure"
    assert proposal.limits.max_items == 1
    assert proposal.target_ids == []


@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        ("title", "title required"),
        ("objective", "objective required"),
        ("basis_description", "basis_description required"),
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


def test_task_proposal_compiler_generates_readable_unique_criterion_ids():
    payload = task_proposal("Check", "Check target", "Store résumé & route inventory!")
    payload["criteria"].extend(
        [
            {**payload["criteria"][0]},
            {
                "description": "Store resume route inventory",
                "evidence": [{"kind": "inventory_manifest", "min_count": 1}],
            },
        ]
    )
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )

    contract = mod._proposal_acceptance_contract(proposal, plan)

    assert [criterion.id for criterion in contract.criteria] == [
        "store-resume-route-inventory",
        "store-resume-route-inventory-2",
        "store-resume-route-inventory-3",
    ]


def test_task_proposal_criterion_id_uses_bounded_ordinal_fallback():
    payload = task_proposal("Check", "Check target", "測試")
    payload["criteria"].append(
        {
            "description": "A" * 100,
            "evidence": [{"kind": "inventory_manifest", "min_count": 1}],
        }
    )
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )

    contract = mod._proposal_acceptance_contract(proposal, plan)

    assert contract.criteria[0].id == "criterion-1"
    assert contract.criteria[1].id == "a" * mod.TASK_PROPOSAL_CRITERION_ID_MAX_CHARS


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

    with pytest.raises(ValueError, match="at least one discovery procedure limit"):
        mod.TaskProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"methods": []}, "requires methods"),
        ({"limits": None}, "requires limits"),
        ({"snapshot_refs": ["memory:m1"]}, "must not include snapshot_refs"),
        ({"coverage": True}, "must not enable coverage"),
    ],
)
def test_task_proposal_rejects_invalid_procedure_combinations(updates, message):
    payload = task_proposal("Check", "Check target")
    payload.update(updates)

    with pytest.raises(ValueError, match=message):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_rejects_snapshot_procedure_fields():
    payload = task_proposal("Check", "Check target")
    payload.update({"basis_kind": "snapshot", "snapshot_refs": ["memory:m1"]})

    with pytest.raises(ValueError, match="must not include methods"):
        mod.TaskProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"snapshot_refs": []}, "requires snapshot_refs"),
        ({"limits": {"max_items": 1}}, "must not include limits"),
    ],
)
def test_task_proposal_rejects_invalid_snapshot_combinations(updates, message):
    payload = task_proposal("Check", "Check target")
    payload.update({"basis_kind": "snapshot", "methods": [], "limits": None, "snapshot_refs": ["memory:m1"]})
    payload.update(updates)

    with pytest.raises(ValueError, match=message):
        mod.TaskProposal.model_validate(payload)


def test_task_proposal_compiler_builds_snapshot_outcome_contract():
    payload = task_proposal("Review", "Review stored evidence", evidence_kind="memory")
    payload.update({"basis_kind": "snapshot", "methods": [], "limits": None, "snapshot_refs": ["memory:m1"]})
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


@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        ([{"kind": "artifact"}, {"kind": "inventory_manifest"}], "must not mix"),
        ([{"kind": "memory"}], "requires artifact or inventory_manifest"),
    ],
)
def test_task_proposal_compiler_rejects_invalid_procedure_outputs(evidence, message):
    payload = task_proposal("Check", "Check target")
    payload["criteria"][0]["evidence"] = evidence
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )

    with pytest.raises(ValueError, match=message):
        mod._proposal_acceptance_contract(proposal, plan)


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

    assert task_schema["required"] == ["title", "objective", "basis_kind", "basis_description", "criteria"]
    assert set(task_schema["properties"]) == {
        "title",
        "objective",
        "basis_kind",
        "basis_description",
        "methods",
        "limits",
        "snapshot_refs",
        "coverage",
        "criteria",
        "target_ids",
    }
    schema_text = json.dumps(task_schema["properties"])
    for removed in ("acceptance", "phase", "status", "target_scope", "output_kind", "gap_policy", "stop_condition"):
        assert removed not in schema_text
    assert criterion_schema["required"] == ["description", "evidence"]
    assert set(criterion_schema["properties"]) == {"description", "evidence"}


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

    assert '"basis_kind": "procedure"' in normalized_description
    assert '"evidence": [{"kind": "inventory_manifest"' in normalized_description
    assert "Python assigns the active phase and pending status" in description
    assert "Criterion IDs, procedure stop conditions, gap policy, output kind, and source references are deterministic" in normalized_description


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


def test_create_tasks_infers_all_targets_and_compiles_multiple_criteria(fake_memory_client):
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
    proposal["criteria"].append(
        {
            "description": "coverage notes",
            "evidence": [
                {"kind": "inventory_manifest", "min_count": 1},
                {"kind": "observation", "min_count": 1},
            ],
        }
    )

    mod.create_tasks([proposal])

    task = store.tasks[0]
    assert task.target_scope == "all"
    assert task.target_ids == []
    assert task.acceptance.basis.source_refs == ("target:target-1", "target:target-2", "plan:phase-1")
    assert [criterion.id for criterion in task.acceptance.criteria] == ["inventory", "coverage-notes"]


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
                "basis_kind": "snapshot",
                "basis_description": "Frozen endpoint inventory",
                "snapshot_refs": [f"artifact:{manifest}"],
                "coverage": True,
                "criteria": [
                    {
                        "description": "Assess every item ID in the frozen inventory",
                        "evidence": [{"kind": "artifact", "min_count": 1}],
                    }
                ],
            }
        ]
    )

    assert json.loads(result)["created_count"] == 1
    assert len(store.tasks[0].acceptance.basis.snapshot_hash) == 64


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
    assert set(task_schema["properties"]) == {
        "title",
        "objective",
        "basis_kind",
        "basis_description",
        "methods",
        "limits",
        "snapshot_refs",
        "coverage",
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
                    "basis_kind": "snapshot",
                    "basis_description": "Missing inventory",
                    "snapshot_refs": [f"artifact:{missing}"],
                    "coverage": True,
                    "criteria": [
                        {
                            "description": "Assess every item in the missing inventory",
                            "evidence": [{"kind": "artifact", "min_count": 1}],
                        }
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

    with pytest.raises(ValueError, match="producer task is not done"):
        mod.create_tasks(
            [
                {
                    "title": "Coverage with failed producer",
                    "objective": "Consume inventory",
                    "basis_kind": "snapshot",
                    "basis_description": "Frozen inventory",
                    "snapshot_refs": [f"artifact:{manifest}"],
                    "coverage": True,
                    "criteria": [
                        {
                            "description": "Assess every frozen inventory item",
                            "evidence": [{"kind": "artifact", "min_count": 1}],
                        }
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

    with pytest.raises(ValueError, match="Coverage ledger mismatch"):
        acceptance_tool(
            results=[
                {
                    "criterion_id": "test-inventory",
                    "status": "satisfied",
                    "summary": "Incomplete ledger",
                    "evidence_refs": [f"artifact:{manifest}"],
                    "coverage": [],
                }
            ]
        )

    result = acceptance_tool(
        results=[
            {
                "criterion_id": "test-inventory",
                "status": "satisfied",
                "summary": "Inventory assessed",
                "evidence_refs": [f"artifact:{manifest}"],
                "coverage": [
                    {
                        "item_id": "endpoint-1",
                        "status": "assessed_negative",
                        "evidence_refs": [f"artifact:{manifest}"],
                    }
                ],
            }
        ]
    )

    assert json.loads(result)["complete"] is True
    assert store.get_acceptance_results("op1", task.task_uid)[0].coverage[0].item_id == "endpoint-1"
    assert store.get_tasks("op1")[0].evidence == [f"artifact:{manifest}"]


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
            results=[
                {
                    "criterion_id": "test-inventory",
                    "status": "satisfied",
                    "summary": "Changed basis",
                    "evidence_refs": [f"artifact:{manifest}"],
                    "coverage": [
                        {
                            "item_id": "endpoint-1",
                            "status": "satisfied",
                            "evidence_refs": [f"artifact:{manifest}"],
                        }
                    ],
                }
            ]
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
            results=[
                {
                    "criterion_id": "observation",
                    "status": "satisfied",
                    "summary": "Wrong evidence kind",
                    "evidence_refs": [f"artifact:{manifest}"],
                }
            ]
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
            results=[
                {
                    "criterion_id": "technology-identification",
                    "status": "satisfied",
                    "summary": "The response identifies PHP 8",
                    "evidence_refs": [observation["memory_ref"]],
                }
            ]
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
    manifest = _write_inventory_manifest()
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
        results=[
            {
                "criterion_id": "inventory",
                "status": "satisfied",
                "summary": "Inventory and workflow evidence stored",
                "evidence_refs": [f"artifact:{workflow_map}", f"artifact:{manifest}"],
            }
        ]
    )

    assert json.loads(result)["complete"] is True


def test_inventory_requirement_reports_contract_for_malformed_only_candidate(fake_memory_client):
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

    with pytest.raises(ValueError, match="Expected contract:.*schema_version"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            results=[
                {
                    "criterion_id": "inventory",
                    "status": "satisfied",
                    "summary": "Wrong artifact type",
                    "evidence_refs": [f"artifact:{workflow_map}"],
                }
            ]
        )
    with pytest.raises(ValueError, match="received 0.*Expected contract"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            results=[
                {
                    "criterion_id": "inventory",
                    "status": "satisfied",
                    "summary": "No artifact candidate",
                    "evidence_refs": ["memory:m1"],
                }
            ]
        )


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
        results=[
            {
                "criterion_id": "endpoint:/login.php",
                "status": "satisfied",
                "summary": "Login form mapped",
                "evidence_refs": [f"artifact:{manifest}"],
            }
        ],
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
    assert "Criterion endpoint:/login.php [satisfied]: Login form mapped" in published["messages"][0]["content"]
    assert f"artifact:{manifest}" in published["messages"][0]["content"]
    assert published["metadata"]["category"] == "observation"
    assert published["metadata"]["source"] == "task_acceptance"
    assert published["metadata"]["task_uid"] == "task-1"
    assert store.get_acceptance_results("op1", "task-1")[0].summary == "Login form mapped"
    assert store.get_acceptance_results("op1", "other-active") == []
    assert tool_schema["required"] == ["results"]
    assert "task_uid" not in tool_schema["properties"]
    assert '"schema_version": 1' in tool_spec["description"]
    assert "Workflow maps, reports, and arbitrary JSON outputs are artifact evidence" in tool_spec["description"]
    replay = acceptance_tool(
        results=[
            {
                "criterion_id": "endpoint:/login.php",
                "status": "satisfied",
                "summary": "Replay",
                "evidence_refs": [f"artifact:{manifest}"],
            }
        ]
    )
    replay_payload = json.loads(replay)
    assert replay_payload["replayed"] is True
    assert replay_payload["memory_published"] is True
    assert replay_payload["memory_created"] is False
    assert len(_client.mem0.add_calls) == 1

    with pytest.raises(ValueError, match="exactly match frozen criteria"):
        acceptance_tool(
            results=[
                {
                    "criterion_id": "endpoint:/other.php",
                    "status": "satisfied",
                    "summary": "Other mapped",
                    "evidence_refs": ["memory:other"],
                }
            ],
        )

    with pytest.raises(ValueError, match="task_uid required when binding"):
        mod.build_record_task_acceptance_tool("")
    with pytest.raises(ValueError, match="at least one"):
        acceptance_tool(results=[])
    with pytest.raises(ValueError, match="Unknown task_uid"):
        mod.build_record_task_acceptance_tool("missing")(
            results=[store.get_acceptance_results("op1", "task-1")[0]],
        )

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
            results=[
                {
                    "criterion_id": "pending",
                    "status": "excluded",
                    "summary": "Not active",
                    "evidence_refs": ["memory:pending"],
                }
            ],
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
        results=[
            {
                "criterion_id": "inventory",
                "status": "satisfied",
                "summary": "Mapped the target surface",
                "evidence_refs": [f"artifact:{manifest}"],
            }
        ]
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
            results=[
                {
                    "criterion_id": "inventory",
                    "status": "satisfied",
                    "summary": "Ignored immutable replay input",
                    "evidence_refs": [f"artifact:{manifest}"],
                }
            ]
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
                summary=(f"Concrete {status} result " * 100),
                evidence_refs=[f"artifact:/tmp/evidence-{index}-{ref_index}.json" for ref_index in range(10)],
                coverage=coverage,
            )
        )

    content, metadata, publication_key = mod._task_acceptance_memory_payload(task, results)

    assert len(content) <= mod.TASK_ACCEPTANCE_MEMORY_MAX_CHARS
    assert "Coverage: assessed_negative=50, satisfied=50." in content
    for status in statuses:
        assert f"[{status}]" in content
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
