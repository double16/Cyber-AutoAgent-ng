from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.tools import memory as mod


class FakePlanStore:
    def __init__(self):
        self.plan = None
        self.tasks = []

    def store_plan(self, _operation_id, plan):
        self.plan = plan

    def get_plan(self, _operation_id):
        return self.plan

    def store_task(self, _operation_id, task):
        self.tasks = [t for t in self.tasks if t.task_uid != task.task_uid]
        self.tasks.append(task)

    def get_tasks(self, _operation_id):
        return list(self.tasks)


class FakeMem0:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []
        self.get_all_calls = []

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


@pytest.fixture
def fake_memory_client(monkeypatch):
    store = FakePlanStore()
    client = mod.Mem0ServiceClient.__new__(mod.Mem0ServiceClient)
    client.mem0 = FakeMem0()
    client.has_existing_memories = True
    client.silent = True
    client.config = {}
    client.region = None

    monkeypatch.setattr(mod, "_MEMORY_CLIENT", client)
    monkeypatch.setattr(mod, "_PLAN_STORE", store)
    monkeypatch.setattr(mod, "_MEMORY_CONFIG", {"user_id": "u1", "operation_id": "op1"})
    monkeypatch.setattr(mod, "_get_plan_store", lambda: store)
    monkeypatch.setenv("CYBER_OPERATION_ID", "op1")
    return client, store


def test_memory_dataclasses_validation_and_formatting():
    task = mod.Task.from_obj(
        {
            "task_uid": "t1",
            "title": "Check,/admin",
            "objective": "Test\npath",
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
    with pytest.raises(ValueError):
        mod.Task(task_uid="", title="x", objective="y", phase=1, status="pending")

    phase = mod.PlanPhase.from_obj({"id": 1, "title": "Recon", "status": "active", "criteria": None})
    plan = mod.OperationPlan.from_obj(
        {
            "objective": "Assess target",
            "current_phase": 1,
            "phases": [phase.to_dict(), {"id": 2, "title": "Exploit", "status": "pending"}],
        }
    )
    assert "plan_overview[1]" in plan.to_toon()
    assert plan.total_phases == 2
    assert mod.OperationPlan.from_obj(plan) is plan
    with pytest.raises(ValueError):
        mod.PlanPhase(id=-1, title="bad", status="pending")
    with pytest.raises(ValueError):
        mod.OperationPlan(objective="x", current_phase=1, total_phases=1, phases=[])


def test_memory_helpers_and_tool_wrappers(fake_memory_client, monkeypatch, tmp_path):
    client, store = fake_memory_client
    proof = tmp_path / "proof.txt"
    proof.write_text("proof")

    assert mod._normalize_evidence({"a": 1}) == ['{"a": 1}']
    assert mod._sanitize_toon_value("a,b\nc") == "a;b c"
    assert mod._normalize_id("https://x.test/users/123?id=456").count(":id") == 1
    assert "/admin/:id" in mod._extract_sensitive_patterns("see /admin/123 and ./file.txt")
    assert mod._has_valid_proof_pack({"proof_pack": {"artifacts": [str(proof)]}}) is True
    assert mod.active_task_message(None, current_phase=2).startswith("<active_task")
    assert mod.memory_create_time({"metadata": {"created_at": "1"}}) == "1"
    monkeypatch.setenv("MEMORY_ISOLATION", "shared")
    assert mod.memory_is_cross_operation() is True
    monkeypatch.setenv("MEMORY_ISOLATION", "operation")

    stored = mod.mem0_store(
        "[OBSERVATION] confirmed issue",
        {
            "category": "observation",
            "severity": "HIGH",
            "status": "solved",
            "validation_status": "hypothesis",
            "confidence": "95%",
            "proof_pack": {"artifacts": [str(proof)]},
        },
    )
    assert stored == "Memory stored."
    metadata = client.mem0.add_calls[0]["metadata"]
    assert metadata["category"] == "finding"
    assert metadata["status"] == "hypothesis"
    assert metadata["validation_status"] == "hypothesis"

    plan = mod.OperationPlan(
        objective="Assess",
        current_phase=1,
        total_phases=1,
        phases=[mod.PlanPhase(id=1, title="Done", status="done")],
    )
    assert "All phases complete" in mod.store_plan(plan)
    assert "plan_overview[1]" in mod.get_plan()

    created = mod.create_tasks(
        [
            {"title": "First", "objective": "Do first", "phase": 1, "status": "pending"},
            mod.TaskCreate(title="Second", objective="Do second", phase=None, status="active", evidence="e"),
        ]
    )
    assert "Tasks created." in created
    assert "task[" in mod.list_uncompleted_tasks()
    assert "active_task" in mod.get_active_task()
    assert "closed" in mod.task_done("done")

    assert "- active" in mod.mem0_list()
    assert "- finding one" in mod.mem0_retrieve("finding", {"category": "finding"})


def test_mem0_service_client_methods_and_fallbacks(fake_memory_client, monkeypatch):
    client, store = fake_memory_client

    assert mod.Mem0ServiceClient._remove_inactive(None) == []
    assert mod.Mem0ServiceClient._coerce_entry(["a"])["memory"] == '["a"]'
    assert mod.Mem0ServiceClient._normalise_results_list({"data": ["x"]}) == [{"memory": "x", "metadata": {}}]

    client.store_memory("content", user_id="u1", metadata={"category": "finding"})
    assert client.mem0.add_calls[-1]["run_id"] == "op1"

    listed = client.list_memories(user_id="u1", limit=3, run_id="op1")
    assert [entry["memory"] for entry in listed] == ["active", "plain text", ""]

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
        mod.Task(task_uid="a", title="A", objective="A", phase=1, status="active", created_at="1"),
        mod.Task(task_uid="b", title="B", objective="B", phase=1, status="pending", created_at="2"),
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
