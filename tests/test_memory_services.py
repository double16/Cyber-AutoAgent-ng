import hashlib
import json
import os
import sqlite3
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
        (mod.NormalizedTaskStatus, "replaced", "superseded"),
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


def test_task_service_scope_violations_enforces_assigned_scheme_host_and_port_only():
    plan = mod.OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        targets=[
            mod.OperationTarget(
                target_id="target-1", type="network", value="custom-scheme://service.example:4280"
            ),
            mod.OperationTarget(target_id="target-2", type="network", value="other.example:8443"),
        ],
    )
    task = mod.Task(
        task_uid="task-1",
        title="Assess service",
        objective="Collect bounded evidence",
        acceptance=make_acceptance("service-boundary"),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )

    assert mod.task_service_scope_violations(
        plan,
        task,
        "Request custom-scheme://service.example:4280/api?mode=read and retain artifact:/tmp/evidence.json.",
    ) == []
    assert mod.task_service_scope_violations(plan, task, "Check service.example:4280/status.") == []

    violations = mod.task_service_scope_violations(
        plan,
        task,
        "Do not use https://service.example:4280 or custom-scheme://service.example:4200.",
    )

    assert len(violations) == 2
    assert "https://service.example:4280" in violations[0]
    assert "custom-scheme://service.example:4200" in violations[1]
    assert "target-1=custom-scheme://service.example:4280" in violations[0]

    invalid_port = mod.task_service_scope_violations(plan, task, "Check service.example:99999.")

    assert len(invalid_port) == 1
    assert "invalid service reference" in invalid_port[0]

    host_only_plan = mod.OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", type="network", value="service.example")],
    )
    assert mod.task_service_scope_violations(
        host_only_plan, task, "Check https://unrelated.example:443."
    ) == []


def test_task_scope_ignores_non_network_tokens_but_detects_numeric_hostname_references():
    plan = mod.OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", type="network", value="10.0.0.5:4280")],
    )
    task = mod.Task(
        task_uid="task-1",
        title="Assess service",
        objective="Collect bounded evidence",
        acceptance=make_acceptance("service-boundary"),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )

    assert mod.task_service_scope_violations(plan, task, "Started at 2026-08-08T20:15.") == []
    assert mod.task_service_scope_violations(plan, task, "Use image 12invalid.example:4280 locally.") == []
    assert mod.task_service_scope_violations(plan, task, "Use python:3.12 for the local helper.") == []
    assert mod.task_service_scope_violations(plan, task, "Probe 10.0.0.5:4280.") == []
    assert mod.task_service_scope_violations(plan, task, "Mention 10.0.0.6:4280 as data.") == []
    assert len(mod.task_service_scope_violations(plan, task, "Probe http://10.0.0.6:4280/")) == 1


def test_technology_task_scope_does_not_treat_version_as_host_port():
    plan = mod.OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Hypotheses", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", type="network", value="http://service.example:4280")],
    )
    acceptance = make_acceptance("validate-the-assigned-technology")
    task = mod.Task(
        task_uid="technology",
        title="Validate technology apache http server:2.4.68",
        objective="Validate technology apache http server:2.4.68",
        acceptance=acceptance,
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )

    assert mod.task_service_scope_violations(plan, task, task.title) == []
    assert len(mod.task_service_scope_violations(plan, task, "Use http://other.example:4280.")) == 1


class FakeApplicationStore:
    def __init__(self):
        self.plan = None
        self.tasks = []
        self.acceptance_results = {}
        self.findings = {}
        self.acceptance_memory_publications = {}
        self.read_operation_ids = []

    def store_plan(self, _operation_id, plan):
        self.plan = plan

    def get_plan(self, _operation_id):
        return self.plan

    def store_task(self, _operation_id, task):
        self.tasks = [t for t in self.tasks if t.task_uid != task.task_uid]
        self.tasks.append(task)

    def get_tasks(self, _operation_id):
        self.read_operation_ids.append(("tasks", _operation_id))
        return list(self.tasks)

    def store_acceptance_results(self, _operation_id, task_uid, results):
        current = {result.criterion_id: result for result in self.acceptance_results.get(task_uid, [])}
        current.update({result.criterion_id: result for result in results})
        self.acceptance_results[task_uid] = list(current.values())

    def get_acceptance_results(self, _operation_id, task_uid):
        self.read_operation_ids.append(("acceptance", _operation_id))
        return list(self.acceptance_results.get(task_uid, []))

    def has_acceptance_memory_publication(self, operation_id, task_uid, publication_key):
        return self.acceptance_memory_publications.get((operation_id, task_uid)) == publication_key

    def mark_acceptance_memory_published(self, operation_id, task_uid, publication_key):
        self.acceptance_memory_publications[(operation_id, task_uid)] = publication_key

    def get_finding(self, _operation_id, finding_uid):
        return self.findings.get(finding_uid)

    def list_findings(self, _operation_id):
        self.read_operation_ids.append(("findings", _operation_id))
        return [
            {"finding_uid": finding_uid, **record}
            for finding_uid, record in self.findings.items()
        ]

    def link_finding_source_task(self, _operation_id, finding_uid, task_uid):
        candidate_data = self.findings[finding_uid].setdefault("candidate_data", {})
        source_task_uids = candidate_data.setdefault("source_task_uids", [])
        if task_uid not in source_task_uids:
            source_task_uids.append(task_uid)


class FakeMemoryBackend:
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
    store = FakeApplicationStore()
    backend = FakeMemoryBackend()
    client = mod.QdrantMemoryClient.__new__(mod.QdrantMemoryClient)
    client._fake_backend = backend
    client.store_memory = lambda content, user_id=None, agent_id=None, metadata=None: backend.add(
        messages=[{"role": "user", "content": content}],
        user_id=user_id,
        agent_id=agent_id,
        metadata=metadata,
        run_id=mod._operation_id(),
    )
    client.search = lambda query, filters=None, limit=100, **kwargs: backend.search(
        query=query, filters=filters, limit=limit, **kwargs
    )["results"]
    client.list_memories = lambda *_args, **kwargs: [
        item for item in backend.get_all(**kwargs)["results"] if isinstance(item, dict) and item.get("metadata", {}).get("active", True)
    ]
    client.get_memory_by_id = lambda memory_id, **_kwargs: backend.get(memory_id)
    client.has_existing_memories = True
    client.silent = True
    client.config = {}
    client.region = None

    monkeypatch.setattr(mod, "_MEMORY_CLIENT", client)
    monkeypatch.setattr(mod, "_DATABASE_STORE", store)
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
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
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


def test_operation_scoped_reads_ignore_stale_memory_context(fake_memory_client):
    client, store = fake_memory_client
    store.list_preflight_results = Mock(return_value=[])

    client.list_tasks(operation_id="op2")
    client.list_task_acceptance_results("task-1", operation_id="op2")
    client.list_finding_records(operation_id="op2")
    client.list_preflight_results(operation_id="op2")

    assert store.read_operation_ids == [
        ("tasks", "op2"),
        ("acceptance", "op2"),
        ("findings", "op2"),
    ]
    store.list_preflight_results.assert_called_once_with("op2")


def test_initialize_memory_system_synchronizes_operation_environment(monkeypatch):
    monkeypatch.setattr(mod, "QdrantMemoryClient", Mock())
    monkeypatch.setattr(mod, "_MEMORY_CONFIG", {"operation_id": "old-op"})
    monkeypatch.setattr(mod, "_MEMORY_CLIENT", None)
    monkeypatch.setenv("CYBER_OPERATION_ID", "old-op")

    mod.initialize_memory_system(operation_id="new-op", target_name="target")

    assert mod._MEMORY_CONFIG["operation_id"] == "new-op"
    assert os.environ["CYBER_OPERATION_ID"] == "new-op"


def test_get_memory_client_reinitializes_when_environment_operation_changes(fake_memory_client, monkeypatch):
    _client, _store = fake_memory_client
    replacement = Mock()
    constructor = Mock(return_value=replacement)
    monkeypatch.setattr(mod, "QdrantMemoryClient", constructor)
    monkeypatch.setenv("CYBER_OPERATION_ID", "op2")

    assert mod.get_memory_client(silent=True) is replacement
    constructor.assert_called_once()
    assert constructor.call_args.args[0]["operation_id"] == "op2"
    assert mod._MEMORY_CONFIG["operation_id"] == "op2"


def test_database_store_is_shared_when_operation_context_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mod,
        "_MEMORY_CONFIG",
        {"output_dir": str(tmp_path), "target_name": "target", "operation_id": "op1"},
    )
    first = mod._get_database_store()
    assert first.db_path == str(tmp_path / "cyber_autoagent.db")

    monkeypatch.setattr(
        mod,
        "_MEMORY_CONFIG",
        {"output_dir": str(tmp_path), "target_name": "target", "operation_id": "op2"},
    )
    second = mod._get_database_store()

    assert second is first
    assert second.db_path == str(tmp_path / "cyber_autoagent.db")


def test_sqlite_store_initialization_recovers_corrupt_database_before_operation(tmp_path, monkeypatch):
    database = tmp_path / "cyber_autoagent.db"
    database.write_bytes(b"not a sqlite database")
    recovered = []

    def recover(store):
        recovered.append(store.db_path)
        store._replace_with_fresh_database()
        return True

    monkeypatch.setattr(mod.SQLiteApplicationStore, "_recover_database", recover)

    store = mod.SQLiteApplicationStore(str(database), logical_target="target")

    assert recovered == [str(database)]
    assert store._sqlite_integrity_check(str(database)).lower() == "ok"
    assert len(list(tmp_path.glob("cyber_autoagent.corrupt-*.db"))) == 1


def test_sqlite_store_initialization_replaces_database_when_recovery_fails(tmp_path, monkeypatch):
    database = tmp_path / "cyber_autoagent.db"
    database.write_bytes(b"not a sqlite database")
    monkeypatch.setattr(mod.SQLiteApplicationStore, "_recover_database", lambda _store: False)

    store = mod.SQLiteApplicationStore(str(database), logical_target="target")

    assert store._sqlite_integrity_check(str(database)).lower() == "ok"
    assert len(list(tmp_path.glob("cyber_autoagent.corrupt-*.db"))) == 1


def test_sqlite_store_recovers_one_runtime_operation_and_retries(tmp_path, monkeypatch):
    store = mod.SQLiteApplicationStore(str(tmp_path / "runtime.db"), logical_target="target")
    original_connect = store._connect
    attempts = {"connect": 0, "recovery": 0}

    def flaky_connect():
        attempts["connect"] += 1
        if attempts["connect"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return original_connect()

    def recover(_error):
        attempts["recovery"] += 1
        return True

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store, "_recover_runtime_database", recover)

    assert store.get_plan("operation") is None
    assert attempts == {"connect": 2, "recovery": 1}


def test_sqlite_store_fails_closed_when_runtime_recovery_fails(tmp_path, monkeypatch):
    store = mod.SQLiteApplicationStore(str(tmp_path / "runtime-fail.db"), logical_target="target")
    attempts = {"recovery": 0}

    def fail_connect():
        raise sqlite3.OperationalError("disk I/O error")

    def fail_recovery(_error):
        attempts["recovery"] += 1
        return False

    monkeypatch.setattr(store, "_connect", fail_connect)
    monkeypatch.setattr(store, "_recover_runtime_database", fail_recovery)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store.get_plan("operation")
    assert attempts == {"recovery": 1}


def test_read_only_plan_store_does_not_create_missing_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mod,
        "_MEMORY_CONFIG",
        {
            "output_dir": str(tmp_path),
            "target_name": "target",
            "operation_id": "missing-op",
            "read_only": True,
        },
    )

    with pytest.raises(FileNotFoundError, match="Application database does not exist"):
        mod._get_database_store()

    assert not (tmp_path / "cyber_autoagent.db").exists()


def test_read_only_plan_store_preserves_existing_database(tmp_path, monkeypatch):
    config = {"output_dir": str(tmp_path), "target_name": "target", "operation_id": "op1"}
    monkeypatch.setattr(mod, "_MEMORY_CONFIG", config)
    writable = mod._get_database_store()
    writable.store_plan(
        "op1",
        mod.OperationPlan(
            objective="test",
            current_phase=1,
            total_phases=1,
            phases=[mod.PlanPhase(id=1, title="Test", status="pending")],
        ),
    )

    monkeypatch.setattr(mod, "_MEMORY_CONFIG", {**config, "read_only": True})
    readonly = mod._get_database_store()

    assert readonly.read_only is True
    assert readonly.get_plan("op1").objective == "test"


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


def test_inventory_manifest_infers_missing_schema_version_for_compatible_shape(fake_memory_client, monkeypatch):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    logger_info = Mock()
    monkeypatch.setattr(mod.logger, "info", logger_info)
    manifest = _write_inventory_manifest()
    payload = json.loads(manifest.read_text())
    del payload["schema_version"]
    manifest.write_text(json.dumps(payload))

    loaded, _digest = mod._load_inventory_manifest(f"artifact:{manifest}")

    assert loaded["schema_version"] == 1
    assert json.loads(manifest.read_text())["schema_version"] == 1
    logger_info.assert_any_call(
        "Inferred inventory manifest schema_version=%s reference=%s",
        1,
        "artifact:inventory.json",
    )


def test_inventory_manifest_missing_schema_version_keeps_structural_errors(fake_memory_client):
    del fake_memory_client
    path = Path(mod._operation_output_root()) / "missing-schema-invalid.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": [], "unassessed_gaps": []}
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="items must be a non-empty list") as error:
        mod._load_inventory_manifest(f"artifact:{path}")

    assert "schema_version" not in str(error.value)
    assert "schema_version" not in json.loads(path.read_text())


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


@pytest.mark.parametrize("description", ["Check target", "Different target"])
def test_task_proposal_objective_takes_precedence_over_description(description):
    payload = task_proposal("Check", "Check target")
    payload["description"] = description

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.objective == "Check target"


def test_task_proposal_description_alias_becomes_objective():
    payload = task_proposal("Check", "Check target")
    payload["description"] = payload.pop("objective")

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.objective == "Check target"


def test_task_proposal_defaults_basis_description_to_objective():
    payload = task_proposal("Check", "Check target")
    payload.pop("basis_description")

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.effective_basis_description == "Check target"


def test_create_tasks_rejects_accidental_multi_route_http_proposal_atomically(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Testing", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    proposal = task_proposal(
        "NoSQL injection assessment",
        "Test POST /api/auth/login and GET /api/products/:id for NoSQL injection.",
        "Record NoSQL injection results for both routes.",
        target_ids=["target-1"],
    )

    with pytest.raises(ValueError, match="multiple distinct endpoint routes: /api/auth/login, /api/products/:id"):
        mod._create_tasks_from_proposals([proposal], prompt_token_limit=48_000)

    assert store.tasks == []


def test_create_tasks_accepts_single_http_route_method_and_query_variants(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Testing", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    proposal = task_proposal(
        "Login injection assessment",
        "Test POST /api/auth/login?mode=baseline and GET http://target.test/api/auth/login/.",
        "Record evidence for the login endpoint.",
        target_ids=["target-1"],
    )

    result = mod._create_tasks_from_proposals([proposal], prompt_token_limit=48_000)

    assert json.loads(result)["created_count"] == 1
    assert len(store.tasks) == 1


def test_create_tasks_accepts_declared_multi_route_workflow(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Testing", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    proposal = task_proposal(
        "Authenticated purchase workflow assessment",
        "Test POST /api/auth/login followed by POST /api/orders as one authenticated workflow.",
        "Record the ordered workflow outcome.",
        target_ids=["target-1"],
    )

    result = mod._create_tasks_from_proposals([proposal], prompt_token_limit=48_000)

    assert json.loads(result)["created_count"] == 1
    assert len(store.tasks) == 1


def test_create_tasks_does_not_apply_http_route_atomicity_to_filesystem_targets(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess source",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Testing", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="/repo", type="filesystem")],
    )
    proposal = task_proposal(
        "Source analysis",
        "Inspect /repo/app.py and /repo/config.py for unsafe input handling.",
        "Store source-analysis evidence.",
        target_ids=["target-1"],
    )

    result = mod._create_tasks_from_proposals([proposal], prompt_token_limit=48_000)

    assert json.loads(result)["created_count"] == 1
    assert len(store.tasks) == 1


@pytest.mark.parametrize("reference", ["shell:curl https://target.test", "tooluse_123", "https://target.test"])
def test_canonical_evidence_reference_rejects_non_durable_references(reference):
    with pytest.raises(ValueError, match="Acceptance evidence references must use"):
        mod._canonical_evidence_reference(reference)


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


def test_task_proposal_compiler_generates_short_task_scoped_criterion_id():
    payload = task_proposal("Check", "Check target", "Store résumé & route inventory!")
    proposal = mod.TaskProposal.model_validate(payload)
    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )

    contract = mod._proposal_acceptance_contract(proposal, plan)

    assert [criterion.id for criterion in contract.criteria] == ["criterion-1"]


def test_task_proposal_criterion_id_is_independent_of_description_characters():
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


def test_task_proposal_criterion_ids_are_short_ordinals_for_multiple_criteria():
    criteria = [
        mod.TaskProposalCriterion(description="A deliberately long criterion description " * 8),
        mod.TaskProposalCriterion(description="測試"),
        mod.TaskProposalCriterion(description="Punctuation! does not affect the identifier."),
    ]

    assert mod._task_proposal_criterion_ids(criteria) == ["criterion-1", "criterion-2", "criterion-3"]


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

    assert first_contract.criteria[0].id == "criterion-1"
    assert second_contract.criteria[0].id == "criterion-1"


def test_task_proposal_empty_limits_use_bounded_defaults():
    payload = task_proposal("Check", "Check target")
    payload["limits"] = {}

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.limits.max_requests == 50
    assert proposal.limits.max_duration_minutes == 10


def test_task_proposal_defaults_omitted_limits():
    payload = task_proposal("Check", "Check target")
    payload.pop("limits")

    proposal = mod.TaskProposal.model_validate(payload)
    assert proposal.limits.max_requests == 50
    assert proposal.limits.max_duration_minutes == 10


def test_task_proposal_explicit_limits_override_defaults():
    payload = task_proposal("Check", "Check target")
    payload["limits"] = {"max_requests": 7}

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.limits.max_requests == 7
    assert proposal.limits.max_duration_minutes is None


def test_task_proposal_scalar_positive_limit_normalizes_to_max_requests():
    payload = task_proposal("Check", "Check target")
    payload["limits"] = 7

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.limits.max_requests == 7
    assert proposal.limits.max_duration_minutes is None


def test_create_tasks_tool_accepts_scalar_positive_limits(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Check", status="active")],
    )
    proposal = task_proposal("Check", "Check target")
    proposal["limits"] = 7

    result = json.loads(mod.build_create_tasks_tool()(tasks=[proposal]))

    assert result["created_count"] == 1
    assert store.tasks[0].acceptance.basis.procedure.limits["max_requests"] == 7


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "7"])
def test_task_proposal_rejects_invalid_scalar_limit_values(value):
    payload = task_proposal("Check", "Check target")
    payload["limits"] = value

    with pytest.raises(ValueError):
        mod.TaskProposal.model_validate(payload)


def test_snapshot_task_proposal_discards_scalar_limits():
    payload = task_proposal("Review", "Review stored evidence", evidence_kind="memory")
    payload.update({"methods": [], "limits": 7, "snapshot_refs": ["memory:m1"]})

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.limits.model_dump(exclude_none=True) == {}


def test_snapshot_task_proposal_discards_default_limits():
    payload = task_proposal("Review", "Review stored evidence", evidence_kind="memory")
    payload.update({"methods": [], "snapshot_refs": ["memory:m1"]})
    payload.pop("limits")

    proposal = mod.TaskProposal.model_validate(payload)
    assert proposal.limits.max_requests is None
    assert proposal.limits.max_duration_minutes is None


@pytest.mark.parametrize("field_name", ["methods", "snapshot_refs"])
def test_task_proposal_defaults_omitted_basis_arrays(field_name):
    payload = task_proposal("Check", "Check target")
    if field_name == "methods":
        payload["snapshot_refs"] = ["memory:m1"]
    payload.pop(field_name)

    proposal = mod.TaskProposal.model_validate(payload)
    assert proposal.methods == [] if field_name == "methods" else proposal.methods == ["test-fixture"]
    assert proposal.snapshot_refs == ["memory:m1"] if field_name == "methods" else proposal.snapshot_refs == []


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
    assert "procedure methods or snapshot_refs" in message
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


def test_task_proposal_normalizes_common_structural_and_output_aliases():
    payload = task_proposal("Assess", "Assess the assigned route")
    payload["method"] = payload.pop("methods")
    payload["limit"] = payload.pop("limits")
    payload["criterion"] = payload.pop("criteria")
    payload["target_id"] = payload.pop("target_ids")
    payload["output_kind"] = "vulnerability_report"

    proposal = mod.TaskProposal.model_validate(payload)

    assert proposal.methods == ["test-fixture"]
    assert proposal.limits.max_items == 1
    assert proposal.criteria[0].description == "test-outcome"
    assert proposal.target_ids == []
    assert proposal.output_kind == "artifact"


@pytest.mark.parametrize("output_kind", ["report", "evidence", "vulnerability_report"])
def test_task_proposal_rejects_unknown_output_kind_alias(output_kind):
    payload = task_proposal("Assess", "Assess the assigned route")
    payload["output_kind"] = output_kind
    assert mod.TaskProposal.model_validate(payload).output_kind == "artifact"


def test_task_proposal_keeps_unknown_output_kind_invalid():
    payload = task_proposal("Assess", "Assess the assigned route")
    payload["output_kind"] = "finding_report_bundle"

    with pytest.raises(ValueError, match="Input should be 'artifact' or 'inventory_manifest'"):
        mod.TaskProposal.model_validate(payload)


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


def test_resolve_operation_targets_excludes_remote_path_hints_and_prose_from_ctf_objective():
    objective = (
        "Find and report the flag on the target services exposed at: host.docker.internal:32770. "
        "Potential files on the target that contain the flag (retrieve contents via exploit): /FLAG.txt."
    )

    targets = mod.resolve_operation_targets("ctf", objective)

    assert [(target.value, target.type) for target in targets] == [
        ("host.docker.internal:32770", "network"),
    ]


def test_resolve_operation_targets_accepts_contextual_fqdn_objective_target():
    targets = mod.resolve_operation_targets("assessment", "Assess demo.local for the authorized exercise.")

    assert [(target.value, target.type) for target in targets] == [("demo.local", "network")]


def test_resolve_operation_targets_falls_back_to_logical_bare_target():
    targets = mod.resolve_operation_targets("easypicking.htb", "Security assessment")

    assert len(targets) == 1
    assert targets[0].value == "easypicking.htb"
    assert targets[0].source == "logical_target_fallback"


def test_resolve_operation_targets_keeps_missing_path_as_filesystem(tmp_path):
    missing = tmp_path / "missing-target"

    targets = mod.resolve_operation_targets(str(missing), "Security assessment")

    assert len(targets) == 1
    assert targets[0].value == str(missing)
    assert targets[0].type == "filesystem"


def test_resolve_operation_targets_preserves_explicit_ip_service_boundary():
    targets = mod.resolve_operation_targets("assessment", "Assess 192.0.2.10:8443")

    assert [target.value for target in targets] == ["192.0.2.10:8443"]
    assert [target.type for target in targets] == ["network"]


def test_resolve_operation_targets_extracts_ipv6_host_and_network():
    targets = mod.resolve_operation_targets("assessment", "Assess 2001:db8::10 and 2001:db8:1::/64")

    assert [target.value for target in targets] == ["2001:db8::10", "2001:db8:1::/64"]
    assert [target.type for target in targets] == ["network", "network_range"]


def test_create_tasks_tool_schema_is_flat_and_controller_owned():
    tool_spec = get_tool_spec(mod.create_tasks)
    task_schema = tool_spec["inputSchema"]["json"]["$defs"]["TaskProposal"]
    criterion_schema = tool_spec["inputSchema"]["json"]["$defs"]["TaskProposalCriterion"]

    assert task_schema["required"] == ["title", "objective", "criteria"]
    assert set(task_schema["properties"]) == {
        "title",
        "objective",
        "basis_description",
        "methods",
        "limits",
        "snapshot_refs",
        "finding_refs",
        "output_kind",
        "criteria",
        "target_ids",
        "replacement_of",
        "supersedes_criteria",
        "workstream",
        "task_role",
        "depends_on_workstreams",
        "inapplicability_reason",
    }
    schema_text = json.dumps(task_schema["properties"])
    for removed in ("acceptance", "phase", "status", "target_scope", "gap_policy", "stop_condition", "basis_kind"):
        assert removed not in schema_text
    assert criterion_schema["required"] == ["description"]
    assert set(criterion_schema["properties"]) == {"description"}
    assert task_schema["examples"][0]["methods"] == []
    assert task_schema["examples"][0]["limits"] == {}
    assert task_schema["examples"][1]["limits"]["max_requests"] == 500


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
    del payload["schema_version"]
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
    assert persisted["schema_version"] == 1
    canonical = json.dumps(persisted, sort_keys=True, separators=(",", ":"))
    assert digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    logger_info.assert_any_call(
        "Removed out-of-scope inventory items reference=%s removed_count=%d",
        "artifact:inventory.json",
        2,
    )
    logger_info.assert_any_call(
        "Inferred inventory manifest schema_version=%s reference=%s",
        1,
        "artifact:inventory.json",
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


def test_inventory_manifest_normalizes_protocol_neutral_interaction_and_raw_target_id(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Inspect /workspace/application",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="/workspace/application", type="filesystem")],
    )
    manifest = _write_inventory_manifest()
    payload = json.loads(manifest.read_text())
    payload["items"] = [{
        "id": "repository-1",
        "target_id": "/workspace/application",
        "kind": "service",
        "value": "/workspace/application",
        "attributes": {
            "interaction": {
                "interface": " filesystem ",
                "operations": ["read", "read", "trace-data-flow"],
                "inputs": [{"name": " path ", "location": " argument "}],
                "success_signals": ["source located"],
                "failure_signals": ["path absent"],
            }
        },
    }]
    manifest.write_text(json.dumps(payload))

    loaded, _digest = mod._load_inventory_manifest(f"artifact:{manifest}")

    item = loaded["items"][0]
    assert item["target_id"] == "target-1"
    assert item["attributes"]["interaction"] == {
        "interface": "filesystem",
        "operations": ["read", "trace-data-flow"],
        "inputs": [{"name": "path", "location": "argument"}],
        "success_signals": ["source located"],
        "failure_signals": ["path absent"],
    }


@pytest.mark.parametrize(
    ("interaction", "message"),
    [
        ({"operations": ["read"], "transport": "local"}, "unsupported fields"),
        ({"inputs": [{"location": "argument"}]}, "requires a name"),
    ],
)
def test_inventory_manifest_rejects_invalid_interaction_metadata(fake_memory_client, interaction, message):
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
    payload["items"][0]["attributes"] = {"interaction": interaction}
    manifest.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        mod._load_inventory_manifest(f"artifact:{manifest}")


def test_deterministic_inventory_candidates_preserve_html_form_operation(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    artifact_dir = Path(mod._operation_output_root()) / "artifacts"
    artifact_dir.mkdir(parents=True)
    form = artifact_dir / "ping.html"
    form.write_text(
        '<form action="/ping" method="post"><input name="host" type="text"><button>Ping</button></form>'
    )

    candidates, source_count = mod._deterministic_inventory_candidates()

    ping = next(item for item in candidates if item["value"] == "http://target.test/ping")
    assert source_count == 1
    assert ping["attributes"]["interaction"]["operations"] == ["POST"]
    assert ping["attributes"]["interaction"]["inputs"] == [
        {"name": "host", "location": "body", "type": "text"}
    ]


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


def test_create_tasks_preserves_explicit_replacement_lineage(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Testing", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    parent = mod.Task(
        task_uid="parent-task",
        title="Combined test",
        objective="Test two related behaviors",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="procedure",
                description="bounded",
                source_refs=["target:target-1"],
                procedure={
                    "methods": ["test"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "inventory_manifest",
                },
            ),
            criteria=[mod.AcceptanceCriterion(
                id="criterion-1",
                description="Resolve the combined test",
                evidence_requirements=[mod.EvidenceRequirement(kind="inventory_manifest")],
            )],
        ),
        phase=1,
        status="partial_failure",
    )
    store.tasks.append(parent)

    result = mod.create_tasks([{
        "title": "Cookie follow-up",
        "objective": "Resolve the cookie portion of the failed combined test",
        "methods": ["test"],
        "criteria": [{"description": "Record evidence for the parent intent"}],
        "target_ids": ["target-1"],
        "replacement_of": "parent-task",
        "supersedes_criteria": ["criterion-1"],
    }])

    assert json.loads(result)["created_count"] == 1
    replacement = next(task for task in store.tasks if task.task_uid != "parent-task")
    assert replacement.replacement_of == "parent-task"
    assert replacement.supersedes_criteria == ["criterion-1"]

    with pytest.raises(ValueError, match=r"allowed criterion IDs: criterion-1"):
        mod.create_tasks([{
            "title": "Invalid replacement",
            "objective": "Attempt to resolve the failed combined test",
            "methods": ["test"],
            "criteria": [{"description": "Record evidence for the parent intent"}],
            "target_ids": ["target-1"],
            "replacement_of": "parent-task",
            "supersedes_criteria": ["unknown-criterion"],
        }])


def test_create_tasks_rejects_unknown_replacement_parent(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess http://target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Testing", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )

    with pytest.raises(ValueError, match="unknown task"):
        mod.create_tasks([{
            "title": "Replacement",
            "objective": "Resolve missing work",
            "criteria": [{"description": "Record evidence"}],
            "methods": ["test"],
            "replacement_of": "missing-parent",
            "supersedes_criteria": ["criterion-1"],
        }])


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


@pytest.mark.parametrize(
    "objective",
    [
        "Enumerate ports 80, 443, and 8080-8090 on the assigned service",
        "Run nmap -sV -p 80 against the assigned service",
        "Run nmap -p- against the assigned service",
        "Run an all-port scan against the assigned service",
        "Run nmap with HTTP title headers (-p) against the assigned service",
    ],
)
def test_create_tasks_rejects_ports_outside_explicit_service_target(fake_memory_client, objective):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess host.docker.internal:32769",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[
            mod.OperationTarget(
                target_id="target-1",
                value="host.docker.internal:32769",
                type="network",
            )
        ],
    )

    with pytest.raises(ValueError, match="explicit service target|exact port selector|allowed ports"):
        mod.create_tasks(
            [
                task_proposal(
                    "Probe assigned service",
                    objective,
                    "invalid-service-scope",
                    target_ids=["target-1"],
                )
            ]
        )

    assert store.tasks == []


def test_create_tasks_accepts_exact_explicit_service_port_and_unrelated_limits(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess host.docker.internal:32769",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[
            mod.OperationTarget(
                target_id="target-1",
                value="host.docker.internal:32769",
                type="network",
            )
        ],
    )

    result = mod.create_tasks(
        [
            task_proposal(
                "Probe assigned service",
                "Run nmap -sV -p 32769 against host.docker.internal and collect at most 50 requests",
                "valid-service-scope",
                target_ids=["target-1"],
            )
        ]
    )

    assert json.loads(result)["created_count"] == 1
    assert len(store.tasks) == 1


@pytest.mark.parametrize("field", ["criteria", "basis_description"])
def test_create_tasks_checks_all_scope_text_fields_for_explicit_service_ports(fake_memory_client, field):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess host.docker.internal:32769",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="host.docker.internal:32769", type="network")],
    )
    payload = task_proposal(
        "Probe assigned service",
        "Probe only the assigned service port 32769",
        "Inspect the assigned service",
        target_ids=["target-1"],
    )
    if field == "criteria":
        payload["criteria"] = [{"description": "Inspect port 80 instead"}]
    else:
        payload["basis_description"] = "Bounded procedure for port 80"

    with pytest.raises(ValueError, match="allowed ports"):
        mod.create_tasks([payload])
    assert store.tasks == []


def test_create_tasks_allows_multiple_selected_exact_service_ports(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess two services",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[
            mod.OperationTarget(target_id="target-1", value="first.test:32769", type="network"),
            mod.OperationTarget(target_id="target-2", value="second.test:32770", type="network"),
        ],
    )

    result = mod.create_tasks(
        [
            task_proposal(
                "Probe selected services",
                "Probe ports 32769 and 32770 on the selected services",
                "selected-service-scope",
                target_ids=["target-1", "target-2"],
            )
        ]
    )

    assert json.loads(result)["created_count"] == 1


def test_create_tasks_keeps_bare_host_target_behavior(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target.test",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="target.test", type="network")],
    )

    result = mod.create_tasks(
        [
            task_proposal(
                "Enumerate selected host ports",
                "Probe ports 80 and 443 on the selected bare host",
                "bare-host-scope",
                target_ids=["target-1"],
            )
        ]
    )

    assert json.loads(result)["created_count"] == 1


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
    assert [criterion.id for criterion in task.acceptance.criteria] == ["criterion-1"]


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
    assert [task.acceptance.criteria[0].id for task in store.tasks] == [
        "assess-the-assigned-endpoint",
        "assess-the-assigned-endpoint",
        "assess-the-assigned-workflow",
    ]
    assert all(task.objective.startswith("Assess the frozen inventory") for task in store.tasks)
    assert all(task.target_scope == "subset" and task.target_ids == ["target-1"] for task in store.tasks)


def test_normalized_route_preserves_service_scheme_and_drops_query():
    http_route = mod._normalized_route("http://TARGET.test/login/?username=test")
    https_route = mod._normalized_route("https://target.test/login")

    assert http_route == ("http://target.test/login", "http://target.test/login")
    assert https_route == ("https://target.test/login", "https://target.test/login")


@pytest.mark.parametrize(
    ("group_kind", "criterion_id"),
    [
        ("endpoint", "assess-the-assigned-endpoint"),
        ("workflow", "assess-the-assigned-workflow"),
        ("service", "assess-the-assigned-service"),
        ("technology", "validate-the-assigned-technology"),
        ("source_file", "inspect-the-assigned-resource"),
    ],
)
def test_coverage_acceptance_criterion_matches_compiled_work_kind(group_kind, criterion_id):
    criterion = mod._coverage_acceptance_criterion(group_kind, "Map authentication boundaries")

    assert criterion.id == criterion_id
    assert "Map authentication boundaries" in criterion.description
    assert criterion.evidence_requirements[0].kind == "durable_evidence"


def test_route_scoped_phase_objective_removes_inventory_wide_wording():
    scoped = mod._route_scoped_phase_objective(
        "Map authentication and authorization boundaries across the baseline inventory."
    )

    assert scoped == "Map authentication and authorization boundaries"
    assert "inventory" not in scoped.lower()


def test_route_scoped_phase_objective_removes_dangling_inventory_scope_preposition():
    scoped = mod._route_scoped_phase_objective(
        "Map all endpoints across the baseline inventory for authorization boundaries."
    )

    assert scoped == "Map authorization boundaries"


def test_route_scoped_phase_objective_removes_key_workflows():
    scoped = mod._route_scoped_phase_objective(
        "Generate testable hypotheses across key workflows in the inventory."
    )

    assert scoped == "Generate testable hypotheses in the inventory"
    assert "key workflows" not in scoped.lower()

    criterion = mod._phase_specific_coverage_criterion(
        "endpoint",
        "Document route hypotheses",
        "Attack Hypothesis Generation",
        "Generate testable hypotheses across key workflows in the inventory.",
        "http://target.test/login",
        ["endpoint-1"],
    )

    assert "key workflows" not in criterion.description.lower()


def test_phase_specific_coverage_criterion_binds_route_and_frozen_items():
    criterion = mod._phase_specific_coverage_criterion(
        "endpoint",
        "Map route authentication behavior",
        "Authentication and Authorization Mapping",
        "Map authentication and authorization boundaries across the baseline inventory.",
        "http://target.test/login",
        ["endpoint-1", "parameter-1"],
    )

    assert "Authentication and Authorization Mapping" in criterion.description
    assert "assigned route http://target.test/login" in criterion.description
    assert "endpoint-1, parameter-1" in criterion.description
    assert "Map authentication and authorization boundaries" in criterion.description
    assert "baseline inventory" not in criterion.description.lower()
    assert "this assigned route only" in criterion.description


def test_acceptance_evidence_error_advertises_canonical_reference_syntax():
    with pytest.raises(ValueError) as exc_info:
        mod._canonical_evidence_reference("https://target.test/login")

    message = str(exc_info.value)
    assert "artifact:artifacts/<file>" in message
    assert "artifact_id:<id>" in message
    assert "memory:<id>" in message
    assert "finding:<id>" in message
    assert "Raw URLs" in message


def test_inventory_url_normalization_preserves_boundary_and_repeated_route_segments():
    value = mod._canonical_inventory_url(
        "http://host.docker.internal:4280/vulnerabilities/sqli/vulnerabilities/sqli/?id=1&&",
        "http://host.docker.internal:4280",
    )

    assert value == (
        "http://host.docker.internal:4280/vulnerabilities/sqli/vulnerabilities/sqli/?id=1"
    )


def test_inventory_url_normalization_removes_serialized_quote_artifacts():
    assert mod._canonical_inventory_url(
        r'\"http://host.docker.internal:4280/\"instructions.php\"\"',
        "http://host.docker.internal:4280",
    ) == "http://host.docker.internal:4280/instructions.php"
    assert mod._canonical_inventory_url(
        r'http://host.docker.internal:4280/\".\"',
        "http://host.docker.internal:4280",
    ) == "http://host.docker.internal:4280/"


@pytest.mark.parametrize("prefix", ["charset=utf-8", "charset=UTF-8"])
def test_inventory_url_normalization_removes_leading_charset_contamination(prefix):
    assert mod._canonical_inventory_url(
        f"{prefix} http://host.docker.internal:4280/api/config",
        "http://host.docker.internal:4280",
    ) == "http://host.docker.internal:4280/api/config"


def test_inventory_url_normalization_rejects_non_charset_prefix_contamination():
    with pytest.raises(ValueError, match="absolute HTTP"):
        mod._canonical_inventory_url(
            "content-type: application/json http://host.docker.internal:4280/api/config",
            "http://host.docker.internal:4280",
        )


def test_inventory_manifest_persists_normalized_charset_url(fake_memory_client):
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
    payload["items"][0]["value"] = "charset=UTF-8 http://target.test/login"
    manifest.write_text(json.dumps(payload))

    loaded, _digest = mod._load_inventory_manifest(f"artifact:{manifest}")

    assert loaded["items"][0]["value"] == "http://target.test/login"
    assert json.loads(manifest.read_text())["items"][0]["value"] == "http://target.test/login"


def test_inventory_manifest_deduplicates_normalized_endpoints(fake_memory_client):
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
    payload["items"].append({
        "id": "endpoint-duplicate",
        "target_id": "target-1",
        "kind": "endpoint",
        "value": r'http://target.test/\"login\"',
        "attributes": {},
    })
    manifest.write_text(json.dumps(payload))

    loaded, _digest = mod._load_inventory_manifest(f"artifact:{manifest}")

    assert [item["id"] for item in loaded["items"] if item["kind"] == "endpoint"] == ["endpoint-1"]
    persisted = json.loads(manifest.read_text())
    assert len(persisted["items"]) == 1


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
    monkeypatch.setattr(mod, "_get_database_store", lambda: store)
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


def test_coverage_route_grouping_keeps_orphan_parameters_as_inventory_only():
    manifest = {
        "items": [
            {
                "id": "endpoint-1",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": "https://target.test/search",
            },
            {
                "id": "parameter-1",
                "target_id": "target-1",
                "kind": "parameter",
                "value": "query",
                "attributes": {"endpoint_id": "endpoint-1"},
            },
            {
                "id": "parameter-2",
                "target_id": "target-1",
                "kind": "parameter",
                "value": "orphaned_parameter",
            },
            {
                "id": "parameter-3",
                "target_id": "target-1",
                "kind": "parameter",
                "value": "https://target.test/looks-like-a-route",
            },
        ]
    }

    groups = mod._coverage_route_groups(manifest, prompt_token_limit=48_000)

    assert groups == [
        (
            "target-1",
            "endpoint",
            "https://target.test/search",
            ["endpoint-1", "parameter-1"],
        )
    ]


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


def test_bound_create_tasks_tool_limits_snapshot_fanout_to_assigned_batch(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess inventory",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Coverage", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = Path(mod._operation_output_root()) / "batched-inventory.json"
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
    canonical_manifest = mod.canonical_artifact_reference(str(manifest))
    create_tool = mod.build_create_tasks_tool(
        prompt_token_limit=48_000,
        coverage_item_ids={"endpoint-0", "endpoint-1"},
        expected_snapshot_ref=canonical_manifest,
        phase_title="Trust Boundary & Workflow Mapping",
        phase_objective=(
            "Map authentication mechanisms and authorization boundaries across key workflows "
            "in the baseline inventory."
        ),
    )

    result = create_tool(tasks=[{
        "title": "Assess inventory batch",
        "objective": "Assess every assigned frozen inventory item",
        "methods": [],
        "limits": {},
        "snapshot_refs": [canonical_manifest],
        "criteria": [{"description": "Record a terminal disposition for every assigned item"}],
    }])

    assert json.loads(result)["created_count"] == 2
    assert {
        item_id
        for task in store.tasks
        for item_id in task.acceptance.basis.item_ids
    } == {"endpoint-0", "endpoint-1"}
    assert all(task.title.startswith("Trust Boundary & Workflow Mapping: Assess endpoint") for task in store.tasks)
    assert all("Map authentication mechanisms" in task.objective for task in store.tasks)
    assert all("Trust Boundary & Workflow Mapping" in task.acceptance.criteria[0].description for task in store.tasks)


def test_bound_create_tasks_tool_rejects_preflight_batch_before_persisting(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )

    def reject_unavailable_capability(_proposals):
        raise ValueError("task_preflight:execution_capability: no available runtime capability for crawl")

    create_tool = mod.build_create_tasks_tool(
        proposal_preflight_validator=reject_unavailable_capability,
        reject_duplicate_proposals=True,
    )

    with pytest.raises(ValueError, match="task_preflight:execution_capability"):
        create_tool(tasks=[{
            "title": "Crawl target",
            "objective": "Perform bounded crawling",
            "methods": ["crawl"],
            "limits": {"max_requests": 5},
            "criteria": [{"description": "Store bounded crawl evidence"}],
            "target_ids": ["target-1"],
        }])

    assert store.tasks == []


def test_bound_create_tasks_tool_rejects_duplicate_preflight_batch(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    proposal = {
        "title": "Request target",
        "objective": "Perform one bounded request",
        "methods": ["request"],
        "limits": {"max_requests": 1},
        "criteria": [{"description": "Store the response artifact"}],
        "target_ids": ["target-1"],
    }

    mod.build_create_tasks_tool()(tasks=[proposal])

    with pytest.raises(ValueError, match="task_preflight:duplicate_task"):
        mod.build_create_tasks_tool(reject_duplicate_proposals=True)(tasks=[proposal])

    assert len(store.tasks) == 1
    assert all("baseline inventory" not in task.objective.lower() for task in store.tasks)
    assert all("baseline inventory" not in task.acceptance.criteria[0].description.lower() for task in store.tasks)
    assert all("key workflows" not in task.acceptance.criteria[0].description.lower() for task in store.tasks)


def test_bound_create_tasks_tool_rejects_multiple_snapshot_proposals_atomically(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess inventory",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Coverage", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = Path(mod._operation_output_root()) / "single-proposal-batch.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [
            {
                "id": "endpoint-0",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": "http://target.test/one",
                "attributes": {},
            },
            {
                "id": "endpoint-1",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": "http://target.test/two",
                "attributes": {},
            },
        ],
        "unassessed_gaps": [],
    }))
    canonical_manifest = mod.canonical_artifact_reference(str(manifest))
    proposal = {
        "title": "Assess inventory batch",
        "objective": "Assess every assigned frozen inventory item",
        "methods": [],
        "limits": {},
        "snapshot_refs": [canonical_manifest],
        "criteria": [{"description": "Record a terminal disposition for every assigned item"}],
    }
    create_tool = mod.build_create_tasks_tool(
        coverage_item_ids={"endpoint-0", "endpoint-1"},
        expected_snapshot_ref=canonical_manifest,
        phase_title="Trust Boundary & Workflow Mapping",
        phase_objective="Map authentication mechanisms, authorization boundaries, and critical workflows",
    )

    generic_proposal = {
        **proposal,
        "objective": "Assess the assigned endpoint",
        "criteria": [{"description": "Assess the assigned endpoint"}],
    }
    with pytest.raises(ValueError, match="generic endpoint assessment"):
        create_tool(tasks=[generic_proposal])

    with pytest.raises(ValueError, match="requires exactly one snapshot proposal"):
        create_tool(tasks=[proposal, {**proposal, "title": "Second category"}])

    assert store.tasks == []


def test_bound_create_tasks_tool_rejects_wrong_snapshot_and_split_route(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess inventory",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Coverage", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    manifest = Path(mod._operation_output_root()) / "atomic-route-inventory.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [
            {
                "id": "endpoint-0",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": "http://target.test/login.php",
                "attributes": {},
            },
            {
                "id": "parameter-0",
                "target_id": "target-1",
                "kind": "parameter",
                "value": "username",
                "attributes": {"endpoint_id": "endpoint-0"},
            },
        ],
        "unassessed_gaps": [],
    }))
    canonical_manifest = mod.canonical_artifact_reference(str(manifest))
    proposal = {
        "title": "Assess inventory batch",
        "objective": "Assess every assigned frozen inventory item",
        "methods": [],
        "limits": {},
        "snapshot_refs": [canonical_manifest],
        "criteria": [{"description": "Record a terminal disposition for every assigned item"}],
    }

    wrong_snapshot_tool = mod.build_create_tasks_tool(
        coverage_item_ids={"endpoint-0", "parameter-0"},
        expected_snapshot_ref="artifact:artifacts/other.json",
    )
    with pytest.raises(ValueError, match="requires snapshot reference"):
        wrong_snapshot_tool(tasks=[proposal])

    split_route_tool = mod.build_create_tasks_tool(
        coverage_item_ids={"endpoint-0"},
        expected_snapshot_ref=canonical_manifest,
    )
    with pytest.raises(ValueError, match="split an atomic inventory route group"):
        split_route_tool(tasks=[proposal])


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


def test_create_tasks_rejects_semantic_cross_phase_duplicate(fake_memory_client):
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

    assert result["created_count"] == 0
    assert result["duplicate_count"] == 1
    assert len(store.tasks) == 1


def test_cross_phase_identity_ignores_controller_owned_phase_reference():
    phase_one = make_acceptance()
    phase_two = mod.AcceptanceContract(
        mode=phase_one.mode,
        basis=replace(phase_one.basis, source_refs=["target:target-1", "plan:phase-2"]),
        criteria=phase_one.criteria,
    )

    first = mod._semantic_cross_phase_task_identity(
        "Run bounded discovery",
        "Discover the same surface",
        phase_one,
        "subset",
        ["target-1"],
    )
    second = mod._semantic_cross_phase_task_identity(
        "Run bounded discovery",
        "Discover the same surface",
        phase_two,
        "subset",
        ["target-1"],
    )

    assert first == second


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


def test_detect_secret_exposures_returns_only_redaction_safe_fingerprints(fake_memory_client):
    artifact = Path(mod._operation_output_root()) / "artifacts" / "target-config.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    secret = "postgres://user:password@db.example.test:5432/app"
    artifact.write_text(f"HTTP/1.1 200 OK\n\n{{\"database\":\"{secret}\"}}", encoding="utf-8")

    exposures = mod.detect_secret_exposures("artifact:artifacts/target-config.txt")

    assert any(exposure.kind == "connection_string" for exposure in exposures)
    assert secret not in str(exposures)
    assert all(len(exposure.digest) == 64 for exposure in exposures)


def test_detect_secret_exposures_ignores_redacted_and_short_placeholder_values(fake_memory_client):
    artifact = Path(mod._operation_output_root()) / "artifacts" / "redacted-config.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('api_key="redacted"\npassword=example\ntoken=short', encoding="utf-8")

    assert mod.detect_secret_exposures("artifact:artifacts/redacted-config.txt") == []


def test_secret_exposure_assertion_validates_without_persisting_raw_value(fake_memory_client):
    artifact = Path(mod._operation_output_root()) / "artifacts" / "key-config.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    secret = "AIzaSyD2wIxpYCuNI0Zjt8kChs2hLTS5abVQfRQ"
    artifact.write_text(f'{{"googlemaps":"{secret}"}}', encoding="utf-8")
    exposure = mod.detect_secret_exposures("artifact:artifacts/key-config.txt")[0]

    assertions = mod._validated_evidence_assertions(
        [{
            "artifact": "artifact:artifacts/key-config.txt",
            "type": "secret_exposure",
            "kind": exposure.kind,
            "digest": exposure.digest,
        }],
        ["artifact:artifacts/key-config.txt"],
        require_one=True,
    )

    assert assertions[0]["digest"] == exposure.digest
    assert secret not in str(assertions)


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


def test_acceptance_artifact_is_snapshotted_to_task_owned_storage(fake_memory_client, monkeypatch):
    _client, store = fake_memory_client
    snapshot_logs = []
    monkeypatch.setattr(mod.logger, "info", lambda message, *args: snapshot_logs.append((message, args)))
    artifact = Path(mod._operation_output_root()) / "artifacts" / "shared-result.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("first result")
    task = mod.Task(
        task_uid="owned-artifact",
        title="Record immutable result",
        objective="Record the result",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="snapshot",
                description="one artifact",
                source_refs=["target:target-1"],
            ),
            criteria=[mod.AcceptanceCriterion(
                id="criterion-1",
                description="Retain one result",
                evidence_requirements=[mod.EvidenceRequirement(kind="artifact", min_count=1)],
            )],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    mod.build_record_task_acceptance_tool(task.task_uid)(
        status="satisfied",
        disposition="observation",
        summary="Result retained",
        evidence_refs=["artifact:artifacts/shared-result.txt"],
    )

    recorded = store.get_acceptance_results("op1", task.task_uid)[0]
    assert recorded.evidence_refs[0].startswith("artifact:task_evidence/")
    assert Path(mod._artifact_path_from_ref(recorded.evidence_refs[0])).read_text() == "first result"
    assert any(
        "operation_root=%s" in message
        and str(mod._operation_output_root()) in str(args)
        and "task_evidence" in str(args[-1])
        for message, args in snapshot_logs
    )
    artifact.write_text("later task replacement")
    assert Path(mod._artifact_path_from_ref(recorded.evidence_refs[0])).read_text() == "first result"


def test_acceptance_rejects_unverifiable_task_evidence_snapshot(fake_memory_client, monkeypatch):
    _client, store = fake_memory_client
    artifact = Path(mod._operation_output_root()) / "artifacts" / "shared-result.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("expected result")
    task = mod.Task(
        task_uid="corrupt-owned-artifact",
        title="Record immutable result",
        objective="Record the result",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="snapshot",
                description="one artifact",
                source_refs=["target:target-1"],
            ),
            criteria=[mod.AcceptanceCriterion(
                id="criterion-1",
                description="Retain one result",
                evidence_requirements=[mod.EvidenceRequirement(kind="artifact", min_count=1)],
            )],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    def write_corrupt_snapshot(_source, destination):
        Path(destination).write_text("corrupted result")
        return str(destination)

    monkeypatch.setattr(mod.shutil, "copy2", write_corrupt_snapshot)

    with pytest.raises(mod.TaskEvidenceSnapshotVerificationError, match="digest mismatch"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Result retained",
            evidence_refs=["artifact:artifacts/shared-result.txt"],
        )

    assert store.get_acceptance_results("op1", task.task_uid) == []


def test_acceptance_classifies_missing_source_artifact_for_controller_repair(fake_memory_client):
    _client, store = fake_memory_client
    task = mod.Task(
        task_uid="missing-owned-artifact",
        title="Record immutable result",
        objective="Record the result",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="snapshot",
                description="one artifact",
                source_refs=["target:target-1"],
            ),
            criteria=[mod.AcceptanceCriterion(
                id="criterion-1",
                description="Retain one result",
                evidence_requirements=[mod.EvidenceRequirement(kind="artifact", min_count=1)],
            )],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    with pytest.raises(mod.TaskEvidenceSnapshotVerificationError, match="SOURCE_UNAVAILABLE"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Result retained",
            evidence_refs=["artifact:artifacts/missing-result.txt"],
        )

    assert store.get_acceptance_results("op1", task.task_uid) == []


@pytest.mark.parametrize(
    ("failure", "expected_message"),
    [
        ("copy", "unable to copy snapshot"),
        ("not_file", "not a regular file"),
        ("source", "unable to prepare source snapshot"),
        ("read", "unable to read copied snapshot"),
        ("canonical", "copied snapshot is unavailable"),
    ],
)
def test_task_evidence_snapshot_wraps_io_and_canonicalization_failures(tmp_path, monkeypatch, failure, expected_message):
    operation_root = tmp_path / "operation"
    source = operation_root / "artifacts" / "result.txt"
    source.parent.mkdir(parents=True)
    source.write_text("expected result")
    task = SimpleNamespace(task_uid="snapshot-failure")
    monkeypatch.setattr(mod, "_operation_output_root", lambda: str(operation_root))
    monkeypatch.setattr(mod, "_artifact_path_from_ref", lambda _reference: str(source))

    if failure == "copy":
        def fail_copy(_source, _destination):
            raise OSError("copy failed")

        monkeypatch.setattr(mod.shutil, "copy2", fail_copy)
    elif failure == "not_file":
        monkeypatch.setattr(mod.shutil, "copy2", lambda _source, _destination: None)
    elif failure == "read":
        original_read_bytes = Path.read_bytes

        def fail_destination_read(path):
            if "task_evidence" in path.parts:
                raise OSError("read failed")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", fail_destination_read)
    elif failure == "source":
        monkeypatch.setattr(Path, "read_bytes", lambda _path: (_ for _ in ()).throw(OSError("read failed")))
    else:
        monkeypatch.setattr(
            mod,
            "canonical_artifact_reference",
            lambda _reference: (_ for _ in ()).throw(ValueError("snapshot disappeared")),
        )

    with pytest.raises(mod.TaskEvidenceSnapshotVerificationError, match=expected_message):
        mod._snapshot_task_artifact_reference(task, "artifact:artifacts/result.txt")


def test_endpoint_coverage_acceptance_rejects_manifest_as_subject_evidence(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="endpoint-evidence",
        title="Assess inventory endpoint",
        objective="Assess the frozen endpoint",
        acceptance=mod.AcceptanceContract(
            mode="coverage",
            basis=mod.AcceptanceBasis(
                kind="snapshot",
                description="one endpoint",
                source_refs=[f"artifact:{manifest}"],
                item_ids=["endpoint-1"],
            ),
            criteria=[mod.AcceptanceCriterion(
                id="criterion-1",
                description="Assess the selected endpoint",
                evidence_requirements=[mod.EvidenceRequirement(kind="artifact", min_count=1)],
            )],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    with pytest.raises(ValueError, match="cannot prove a single endpoint assessment"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Assessment complete",
            evidence_refs=[f"artifact:{manifest}"],
        )

    assert store.get_acceptance_results("op1", task.task_uid) == []


def test_controller_execution_evidence_is_required_before_acceptance(fake_memory_client):
    _client, store = fake_memory_client
    artifact = _write_inventory_manifest()
    execution_artifact = Path(mod._operation_output_root()) / "artifacts" / "execution-result.txt"
    execution_artifact.parent.mkdir(parents=True, exist_ok=True)
    execution_artifact.write_text("controller-validated execution")
    task = mod.Task(
        task_uid="execution-receipt",
        title="Execute one bounded check",
        objective="Perform the assigned check",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(kind="snapshot", description="bounded", source_refs=["target:target-1"]),
            criteria=[mod.AcceptanceCriterion(
                id="criterion-1",
                description="Preserve a terminal result",
                evidence_requirements=[mod.EvidenceRequirement(kind="artifact", min_count=1)],
                execution_requirements=[mod.ExecutionRequirement(
                    id="criterion-1-execution-1",
                    description="Execute the assigned check against /api/example.",
                    subject_ref="/api/example",
                )],
            )],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    with pytest.raises(ValueError, match="Execution evidence is required before acceptance"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="assessed_negative",
            disposition="no_vulnerability",
            summary="No vulnerability demonstrated",
            evidence_refs=[f"artifact:{artifact}"],
        )

    resolver_calls = []

    def resolve(current_task, criterion):
        resolver_calls.append((current_task.task_uid, criterion.id))
        return {"criterion-1-execution-1": [f"artifact:{execution_artifact}"]}

    result = mod.build_record_task_acceptance_tool(
        task.task_uid,
        execution_evidence_resolver=resolve,
    )(
        status="assessed_negative",
        disposition="no_vulnerability",
        summary="No vulnerability demonstrated",
        evidence_refs=[f"artifact:{artifact}"],
    )
    assert json.loads(result)["complete"] is True
    assert resolver_calls == [(task.task_uid, "criterion-1")]
    recorded = store.get_acceptance_results("op1", task.task_uid)[0]
    assert len(recorded.evidence_refs) == 2
    assert {
        Path(mod._artifact_path_from_ref(reference)).read_text()
        for reference in recorded.evidence_refs
    } == {artifact.read_text(), "controller-validated execution"}


def test_acceptance_payload_is_retained_when_live_execution_proof_is_not_yet_visible(fake_memory_client):
    _client, store = fake_memory_client
    artifact = _write_inventory_manifest()
    task = mod.Task(
        task_uid="pending-controller-proof",
        title="Execute one bounded check",
        objective="Perform the assigned check",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(kind="snapshot", description="bounded", source_refs=["target:target-1"]),
            criteria=[mod.AcceptanceCriterion(
                id="criterion-1",
                description="Preserve a terminal result",
                evidence_requirements=[mod.EvidenceRequirement(kind="artifact", min_count=1)],
                execution_requirements=[mod.ExecutionRequirement(
                    id="criterion-1-execution-1",
                    description="Execute the assigned check",
                    subject_ref="target:target-1",
                )],
            )],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    with pytest.raises(
        ValueError,
        match="Acceptance is incomplete: execution evidence is required before acceptance",
    ):
        mod.build_record_task_acceptance_tool(
            task.task_uid,
            execution_evidence_resolver=lambda _task, _criterion: {},
        )(
            status="assessed_negative",
            disposition="no_vulnerability",
            summary="No vulnerability demonstrated",
            evidence_refs=[f"artifact:{artifact}"],
        )

    persisted = next(item for item in store.get_tasks("op1") if item.task_uid == task.task_uid)
    assert persisted.recovery_context["pending_controller_acceptance"]["evidence_refs"] == [f"artifact:{artifact}"]
    assert persisted.recovery_context["pending_controller_acceptance"]["missing_requirement_ids"] == [
        "criterion-1-execution-1"
    ]
    assert store.get_acceptance_results("op1", task.task_uid) == []


def test_bound_acceptance_tool_runtime_schema_accepts_aliases_before_function_validation(fake_memory_client):
    _client, store = fake_memory_client
    task = mod.Task(
        task_uid="runtime-alias-acceptance",
        title="Runtime aliases",
        objective="Record an assessment",
        acceptance=make_acceptance("runtime-alias-outcome"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)
    acceptance_tool = mod.build_record_task_acceptance_tool(task.task_uid)

    validated = acceptance_tool._metadata.validate_input({
        "status": "completed",
        "disposition": "assessed-negative",
        "summary": "No vulnerability was demonstrated",
        "evidence_refs": ["artifact:artifacts/result.txt"],
    })

    assert validated["status"] == "completed"
    assert validated["disposition"] == "assessed-negative"
    schema = get_tool_spec(acceptance_tool)["inputSchema"]["json"]
    assert schema["properties"]["status"]["enum"] == [
        "satisfied",
        "assessed_negative",
        "inaccessible",
        "excluded",
        "duplicate",
    ]
    assert schema["properties"]["disposition"]["enum"] == [
        "no_vulnerability",
        "observation",
        "finding_candidate",
        "existing_finding",
    ]


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
    assert "limits" not in task_schema["required"]
    assert set(task_schema["properties"]) == {
        "title",
        "objective",
        "basis_description",
        "methods",
        "limits",
        "snapshot_refs",
        "finding_refs",
        "output_kind",
        "criteria",
        "target_ids",
        "replacement_of",
        "supersedes_criteria",
        "workstream",
        "task_role",
        "depends_on_workstreams",
        "inapplicability_reason",
    }


def test_bound_create_tasks_tool_reports_rejection_to_observer(fake_memory_client):
    del fake_memory_client
    observed = []
    create_tool = mod.build_create_tasks_tool(
        invocation_observer=lambda tool_input, result, error: observed.append((tool_input, result, error))
    )

    with pytest.raises(ValueError, match="title"):
        create_tool(tasks=[{"objective": "Invalid"}])

    assert observed[0][0] == {"tasks": [{"objective": "Invalid"}]}
    assert observed[0][1] is None
    assert isinstance(observed[0][2], ValueError)


def test_bound_create_tasks_tool_reports_success_to_observer(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Inventory", status="active")],
    )
    observed = []
    create_tool = mod.build_create_tasks_tool(
        invocation_observer=lambda tool_input, result, error: observed.append((tool_input, result, error))
    )
    proposal = task_proposal("Inventory", "Run bounded inventory work", "inventory")

    result = create_tool(tasks=[proposal])

    assert json.loads(result)["complete"] is True
    assert observed == [({"tasks": [proposal]}, result, None)]
    assert len(store.tasks) == 1


def test_bound_create_tasks_tool_requires_and_persists_candidate_source_refs(fake_memory_client):
    _client, store = fake_memory_client
    store.plan = mod.OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Impact Demonstration", status="active")],
    )
    proposal = task_proposal("Demonstrate impact", "Demonstrate the assigned finding", "impact")

    result = mod.build_create_tasks_tool(required_finding_refs={"finding:candidate-1"})(tasks=[proposal])

    assert json.loads(result)["created_count"] == 1
    assert store.tasks[-1].evidence == ["finding:candidate-1"]

    proposal = task_proposal("Demonstrate impact", "Demonstrate the assigned finding", "impact")
    proposal["finding_refs"] = ["finding:unknown"]
    with pytest.raises(ValueError, match="includes unavailable finding_refs"):
        mod.build_create_tasks_tool(required_finding_refs={"finding:candidate-1"})(tasks=[proposal])

    proposal = task_proposal(
        "Demonstrate assigned finding explicitly",
        "Demonstrate the assigned finding with an explicit reference",
        "impact",
    )
    proposal["finding_refs"] = ["finding:candidate-1"]
    result = mod.build_create_tasks_tool(required_finding_refs={"finding:candidate-1"})(tasks=[proposal])

    assert json.loads(result)["created_count"] == 1
    assert store.tasks[-1].evidence == ["finding:candidate-1"]


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
    assert store.get_tasks("op1")[0].evidence[0].startswith("artifact:task_evidence/")


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


def test_store_knowledge_returns_memory_reference_for_acceptance(fake_memory_client):
    _client, store = fake_memory_client
    task = mod.Task(
        task_uid="knowledge-round-trip",
        title="Generate hypotheses",
        objective="Record hypotheses",
        acceptance=mod.AcceptanceContract(
            mode="outcome",
            basis=mod.AcceptanceBasis(
                kind="snapshot",
                description="Stored hypothesis knowledge",
                source_refs=("memory:m1",),
            ),
            criteria=[
                mod.AcceptanceCriterion(
                    id="hypotheses",
                    description="Record hypotheses",
                    evidence_requirements=[mod.EvidenceRequirement(kind="durable_evidence")],
                )
            ],
        ),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    knowledge = json.loads(mod.store_knowledge("Hypotheses recorded"))
    accepted = json.loads(
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Hypotheses recorded",
            evidence_refs=[knowledge["memory_ref"]],
        )
    )

    assert knowledge["stored"] is True
    assert knowledge["created"] is True
    assert knowledge["memory_ref"] == "memory:m1"
    assert accepted["complete"] is True


def test_store_observation_uses_active_task_target_and_rejects_conflicting_location(
    fake_memory_client, monkeypatch
):
    client, store = fake_memory_client
    plan = mod.OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Recon", status="active")],
        targets=[mod.OperationTarget(target_id="target-1", type="network", value="http://target.test:4280")],
    )
    task = mod.Task(
        task_uid="active-observation",
        title="Observe target",
        objective="Observe target",
        acceptance=make_acceptance("observation"),
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    store.store_task("op1", task)
    monkeypatch.setattr(mod, "_get_active_plan", lambda: plan)

    mod.store_observation("Observed response", metadata={"location": "http://target.test:4280/path"})

    assert client._fake_backend.add_calls[-1]["metadata"]["target_id"] == "target-1"
    assert client._fake_backend.add_calls[-1]["metadata"]["target"] == "http://target.test:4280"
    with pytest.raises(ValueError, match="assigned target boundary"):
        mod.store_observation("Wrong target", metadata={"target": "http://other.test:4280"})


def test_evidence_reference_kind_validates_memory_findings_and_prefixes(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()

    assert mod._evidence_reference_kind(f"artifact:{manifest}", "artifact") is True
    assert mod._evidence_reference_kind(f"artifact:{manifest}", "inventory_manifest") is True
    assert mod._evidence_reference_kind(f"artifact:{manifest}", "observation") is False
    assert mod._evidence_reference_kind("memory:m1", "memory") is True
    assert mod._evidence_reference_kind("memory:m1", "observation") is True
    with pytest.raises(
        ValueError,
        match=(
            r"Memory evidence is operation-scoped.*store_observation.*returned memory_ref.*"
            r"artifact:artifacts/<file>"
        ),
    ):
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
    assert recorded.evidence_refs[0].startswith("artifact:task_evidence/")


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
    assert len(_client._fake_backend.add_calls) == 1
    published = _client._fake_backend.add_calls[0]
    assert published["messages"][0]["content"].startswith('Task acceptance for "Map parameters".')
    assert "Criterion endpoint:/login.php [satisfied; observation]: Login form mapped" in published["messages"][0]["content"]
    assert "artifact:task_evidence/" in published["messages"][0]["content"]
    assert published["metadata"]["category"] == "observation"
    assert published["metadata"]["source"] == "task_acceptance"
    assert published["metadata"]["task_uid"] == "task-1"
    assert store.get_acceptance_results("op1", "task-1")[0].summary == "Login form mapped"
    assert store.get_acceptance_results("op1", "other-active") == []
    assert set(tool_schema["required"]) == {"status", "disposition", "summary", "evidence_refs"}
    assert "task_uid" not in tool_schema["properties"]
    assert '"schema_version": 1' in tool_spec["description"]
    assert "Workflow maps, reports, and arbitrary JSON outputs are artifact evidence" in tool_spec["description"]
    assert "Frozen criterion: endpoint:/login.php" in tool_spec["description"]
    assert "Required evidence: inventory_manifest>=1" in tool_spec["description"]
    assert "Concrete observed result for" in tool_schema["properties"]["summary"]["description"]
    assert "raw commands, URLs, tool IDs" in tool_schema["properties"]["evidence_refs"]["description"]
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
    assert len(_client._fake_backend.add_calls) == 1

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
    proof = Path(mod._operation_output_root()) / "proof.txt"
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_text("proof")

    assert mod._normalize_evidence({"a": 1}) == ['{"a": 1}']
    assert mod.sanitize_toon_value("a,b\nc") == "a;b c"
    assert mod.sanitize_toon_value("\ta,  b\r\n c") == "a; b c"
    assert mod._normalize_id("https://x.test/users/123?id=456").count(":id") == 1
    assert "/admin/:id" in mod._extract_sensitive_patterns("see /admin/123 and ./file.txt")
    assert mod._has_valid_proof_pack({"proof_pack": {"artifacts": [str(proof)]}}) is True
    assert memory_tasks.active_task_message(mod, None, current_phase=2).startswith("<active_task")
    assert mod.memory_create_time({"metadata": {"created_at": "1"}}) == "1"
    mod._MEMORY_CONFIG["memory_mode"] = "shared"
    assert mod.memory_is_cross_operation() is True
    mod._MEMORY_CONFIG["memory_mode"] = "operation"

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
    metadata = client._fake_backend.add_calls[0]["metadata"]
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

    assert "- active" in mod.memory_list()
    assert "- finding one" in mod.memory_retrieve("finding", {"category": "finding"})


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


def test_bound_acceptance_tool_lists_current_task_evidence_when_refs_are_missing(fake_memory_client):
    _client, store = fake_memory_client
    manifest = _write_inventory_manifest()
    task = mod.Task(
        task_uid="acceptance-evidence-context",
        title="Evidence context",
        objective="Record a negative result",
        acceptance=make_acceptance("outcome"),
        evidence=[f"artifact:{manifest}"],
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    with pytest.raises(ValueError, match=r"eligible_evidence_refs=.*artifact:inventory\.json"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="no_vulnerability",
            summary="No vulnerability was demonstrated",
            evidence_refs=[],
        )


def test_inventory_acceptance_missing_artifact_has_prerequisite_repair_error(fake_memory_client):
    _client, store = fake_memory_client
    task = mod.Task(
        task_uid="missing-manifest",
        title="Inventory",
        objective="Create inventory",
        acceptance=make_acceptance("outcome"),
        phase=1,
        status="active",
    )
    store.store_task("op1", task)

    with pytest.raises(ValueError, match="submitted manifest file does not exist.*Do not retry acceptance"):
        mod.build_record_task_acceptance_tool(task.task_uid)(
            status="satisfied",
            disposition="observation",
            summary="Inventory produced",
            evidence_refs=["artifact:artifacts/missing-inventory.json"],
        )


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

    with pytest.raises(
        ValueError,
        match=r"finding created by this task.*Call store_finding first.*finding:<id>.*record_task_acceptance",
    ):
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


def test_qdrant_memory_client_scope_and_workflow_methods(fake_memory_client, monkeypatch):
    client, store = fake_memory_client
    client.target_values = ["https://target.test"]
    client.memory_mode = "operation"
    client.operation_id = "OP-1"
    scope = mod.QdrantMemoryClient._scope_filter(client, {"category": ["finding", "observation"]})
    assert scope.must[0].key == "target_values"
    assert scope.must[1].key == "operation_id"
    assert scope.must[2].key == "metadata.category"

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
    result = mod.QdrantMemoryClient.store_plan(client, expanded, operation_id="op1")
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


def test_qdrant_shared_retrieval_filters_results_to_overlapping_target_values():
    class FakeQdrant:
        def __init__(self):
            self.last_scroll_filter = None
            self.last_query_filter = None
            self.points = [
                SimpleNamespace(
                    id="same-target",
                    payload={
                        "memory": "prior operation target context",
                        "target_values": ["https://target.test"],
                        "operation_id": "OP-prior",
                        "active": True,
                    },
                ),
                SimpleNamespace(
                    id="other-target",
                    payload={
                        "memory": "unrelated target context",
                        "target_values": ["https://other.test"],
                        "operation_id": "OP-prior",
                        "active": True,
                    },
                ),
            ]

        def scroll(self, **kwargs):
            self.last_scroll_filter = kwargs["scroll_filter"]
            return self.points, None

        def query_points(self, **kwargs):
            self.last_query_filter = kwargs["query_filter"]
            return SimpleNamespace(points=self.points)

        def retrieve(self, **_kwargs):
            return [self.points[1]]

    client = mod.QdrantMemoryClient.__new__(mod.QdrantMemoryClient)
    client.target_values = ["https://target.test"]
    client.memory_mode = "shared"
    client.operation_id = "OP-current"
    client.collection_name = "test_memories"
    client.qdrant = FakeQdrant()
    client.embeddings = SimpleNamespace(embed_query=lambda _query: [0.1])

    listed = mod.QdrantMemoryClient.list_memories(client)
    searched = mod.QdrantMemoryClient.search(client, "prior context")

    assert [memory["id"] for memory in listed] == ["same-target"]
    assert [memory["id"] for memory in searched] == ["same-target"]
    assert mod.QdrantMemoryClient.get_memory_by_id(client, "other-target") is None
    assert client.qdrant.last_scroll_filter.must[0].key == "target_values"
    assert len(client.qdrant.last_scroll_filter.must) == 1
    assert client.qdrant.last_query_filter.must[0].match.any == ["https://target.test"]


def test_qdrant_memory_rejects_invalid_mode_and_missing_target():
    assert mod.QdrantMemoryClient._memory_mode("shared") == "shared"
    assert mod.QdrantMemoryClient._memory_mode("operation") == "operation"
    with pytest.raises(ValueError, match="operation, shared"):
        mod.QdrantMemoryClient._memory_mode("fresh")
    with pytest.raises(ValueError, match="OperationTarget"):
        mod.QdrantMemoryClient._target_values({})

    client = mod.QdrantMemoryClient.__new__(mod.QdrantMemoryClient)
    client.target_values = []
    client.memory_mode = "shared"
    client.operation_id = "OP-current"
    with pytest.raises(ValueError, match="canonical OperationTarget"):
        mod.QdrantMemoryClient._scope_filter(client)
