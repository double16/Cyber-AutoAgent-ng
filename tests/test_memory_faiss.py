import json
import os

import pytest


def _initialize_faiss_memory(memory, tmp_path, monkeypatch):
    faiss_path = tmp_path / "mem0_faiss"

    # isolate global client/config for this test
    memory._MEMORY_CLIENT = None
    memory._MEMORY_CONFIG = None

    embedder_model = "mxbai-embed-large:latest"
    ollama_base_url = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("MEMORY_ISOLATION", "operation")
    monkeypatch.setenv("CYBER_OPERATION_ID", "test-op-create-tasks")
    monkeypatch.setenv("CYBER_AGENT_PROVIDER", "ollama")
    monkeypatch.setenv("CYBER_AGENT_EMBEDDING_MODEL", embedder_model)
    monkeypatch.setenv("OLLAMA_HOST", ollama_base_url)

    memory.initialize_memory_system(
        config={
            "embedder": {
                "provider": "ollama",
                "config": {
                    "model": embedder_model,
                    "ollama_base_url": ollama_base_url,
                },
            },
            "llm": {
                "provider": "ollama",
                "config": {
                    "model": "llama3.2:3b",
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "ollama_base_url": ollama_base_url,
                },
            },
            "vector_store": {
                "provider": "faiss",
                "config": {
                    "path": str(faiss_path),
                    "embedding_model_dims": 1024,
                },
            },
        },
    )


@pytest.mark.ollama
def test_mem0_create_tasks_faiss_filesystem(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_faiss_memory(memory, tmp_path, monkeypatch)

    try:
        plan = {
            "objective": "Test task creation",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [
                {
                    "id": 1,
                    "title": "Phase 1",
                    "status": "active",
                    "criteria": "Create tasks",
                }
            ],
            "assessment_complete": False,
        }
        memory.mem0_store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"

        raw = memory.mem0_create_tasks(
            [
                memory.TaskCreate(
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=None,
                    status="pending",
                ),
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                ),
            ]
        )

        assert isinstance(raw, str)
        assert not raw.startswith("Error:")

        payload = json.loads(raw)
        assert isinstance(payload, dict)

        tasks = memory.mem0_list_uncompleted_tasks()
        assert len(tasks) == 2
        assert all(task.phase == 1 for task in tasks)
        assert all(task.status == "pending" for task in tasks)
        assert {task.title for task in tasks} == {task_1_title, task_2_title}

        active_raw = memory.mem0_get_active_task()
        assert isinstance(active_raw, str)
        assert "<active_task" in active_raw
        assert 'phase="1"' in active_raw
        assert 'status="active"' in active_raw

        start = active_raw.index("{")
        end = active_raw.rindex("}") + 1
        active_payload = json.loads(active_raw[start:end])

        active_task = active_payload["task"]
        assert active_task is not None
        assert active_task["phase"] == 1
        assert active_task["status"] == "active"
        assert active_task["title"] in {task_1_title, task_2_title}

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_mem0_create_tasks_faiss_duplicates(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_faiss_memory(memory, tmp_path, monkeypatch)

    try:
        plan = {
            "objective": "Test task creation",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [
                {
                    "id": 1,
                    "title": "Phase 1",
                    "status": "active",
                    "criteria": "Create tasks",
                }
            ],
            "assessment_complete": False,
        }
        memory.mem0_store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_1_evidence = ["outputs/OP_20260302/auth_analyzer3459734.json"]
        task_2_title = "Check GraphQL schema exposure"
        task_2_evidence = ["outputs/OP_20260302/graphql3497539745.json"]
        task_3_title = "Check for SQL injection"
        task_3_evidence = ["outputs/OP_20260302/advanced_payload_coord384758374.json"]

        create_raw = json.loads(memory.mem0_create_tasks(
            [
                memory.TaskCreate(
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=None,
                    status="pending",
                    evidence=task_1_evidence,
                ),
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                    evidence=task_2_evidence,
                ),
            ]
        ))

        assert len(create_raw["results"]) == 2
        assert create_raw["results"][0]["event"] == "ADD"
        assert create_raw["results"][1]["event"] == "ADD"

        create_dup1 = json.loads(memory.mem0_create_tasks(
            [
                memory.TaskCreate(
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=None,
                    status="pending",
                    evidence=task_1_evidence,
                ),
            ]
        ))

        assert len(create_dup1["results"]) == 1
        assert create_dup1["results"][0]["event"] == "DUPLICATE"
        assert create_dup1["results"][0]["id"] == create_raw["results"][0]["id"]

        create_dup2 = json.loads(memory.mem0_create_tasks(
            [
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                    evidence=task_2_evidence,
                ),
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Fuzz GraphQL endpoints",
                    phase=None,
                    status="pending",
                    # evidence=task_2_evidence,
                ),
            ]
        ))

        assert len(create_dup2["results"]) == 2
        assert create_dup2["results"][0]["event"] == "DUPLICATE"
        assert create_dup2["results"][0]["id"] == create_raw["results"][1]["id"]
        assert create_dup2["results"][1]["event"] == "DUPLICATE"
        assert create_dup2["results"][1]["id"] == create_raw["results"][1]["id"]

        create_new2 = json.loads(memory.mem0_create_tasks(
            [
                memory.TaskCreate(
                    title=task_3_title,
                    objective="Run sqlmap on endpoint",
                    phase=None,
                    status="pending",
                    evidence=task_3_evidence,
                ),
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                    evidence=task_2_evidence,
                ),
            ]
        ))

        assert len(create_new2["results"]) == 2
        assert create_new2["results"][0]["event"] == "ADD"
        assert create_new2["results"][1]["event"] == "DUPLICATE"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_mem0_task_lifecycle(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_faiss_memory(memory, tmp_path, monkeypatch)

    try:
        plan = {
            "objective": "Test task creation",
            "current_phase": 1,
            "total_phases": 2,
            "phases": [
                {
                    "id": 1,
                    "title": "Phase 1",
                    "status": "active",
                    "criteria": "Create tasks",
                },
                {
                    "id": 2,
                    "title": "Phase 2",
                    "status": "pending",
                    "criteria": "Resolve tasks",
                }
            ],
            "assessment_complete": False,
        }
        memory.mem0_store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"
        task_3_title = "Check for SQL injection"

        memory.mem0_create_tasks(
            [
                memory.TaskCreate(
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=1,
                    status="pending",
                ),
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=1,
                    status="pending",
                ),
                memory.TaskCreate(
                    title=task_3_title,
                    objective="Run sqlmap on endpoint",
                    phase=2,
                    status="pending",
                ),
            ]
        )

        active_raw = memory.mem0_get_active_task()
        assert isinstance(active_raw, str)
        assert "<active_task" in active_raw
        assert 'phase="1"' in active_raw
        assert 'status="active"' in active_raw

        active_raw2 = memory.mem0_task_done("done")
        assert isinstance(active_raw2, str)
        assert active_raw != active_raw2

        active_none = memory.mem0_task_done("blocked")
        assert isinstance(active_none, str)
        assert "<active_task" in active_none
        assert 'phase="1"' in active_none
        assert 'status="none"' in active_none

        plan["current_phase"] = 2
        plan["phases"][0]["status"] = "done"
        plan["phases"][1]["status"] = "active"
        memory.mem0_store_plan(plan)

        active_raw3 = memory.mem0_get_active_task()
        assert isinstance(active_raw3, str)
        assert "<active_task" in active_raw3
        assert 'phase="2"' in active_raw3
        assert 'status="active"' in active_raw3

        active_none2 = memory.mem0_task_done("blocked")
        assert isinstance(active_none2, str)
        assert "<active_task" in active_none2
        assert 'phase="2"' in active_none2
        assert 'status="none"' in active_none2

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None
