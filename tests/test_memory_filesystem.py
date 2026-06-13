import json
import os

import pytest


def _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-create-tasks"):
    faiss_path = tmp_path / "mem0_faiss"

    # isolate global client/config for this test
    memory._MEMORY_CLIENT = None
    memory._MEMORY_CONFIG = None
    memory._PLAN_STORE = None

    embedder_model = "mxbai-embed-large:latest"
    ollama_base_url = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("MEMORY_ISOLATION", "operation")
    monkeypatch.setenv("CYBER_OPERATION_ID", operation_id)
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
def test_create_tasks_filesystem(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-fs")

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
        memory.store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"

        raw = memory.create_tasks(
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

        assert "tasks created" in raw.lower()

        tasks = memory.list_uncompleted_tasks()
        assert "task[2]" in tasks
        assert tasks.count(",1,") == 2
        assert tasks.count(",pending") == 1
        assert tasks.count(",active") == 1
        assert task_1_title+"," in tasks
        assert task_2_title+"," in tasks

        active_raw = memory.get_active_task()
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
def test_create_tasks_filesystem_invalid_phase(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-fs")

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
        memory.store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"

        memory.create_tasks(
            [
                memory.TaskCreate(
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=2,
                    status="pending",
                ),
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=2,
                    status="pending",
                ),
            ]
        )

        tasks = memory.list_uncompleted_tasks()
        assert "task[0]" in tasks, "tasks were created in current phase"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_filesystem_future_phase(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-fs")

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
        memory.store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"

        raw = memory.create_tasks(
            [
                memory.TaskCreate(
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=2,
                    status="pending",
                ),
                memory.TaskCreate(
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=2,
                    status="pending",
                ),
            ]
        )

        assert isinstance(raw, str)
        assert not raw.startswith("Error:")

        assert "tasks created" in raw.lower()

        tasks = memory.list_uncompleted_tasks()
        assert "task[0]" in tasks

        active_raw = memory.get_active_task()
        assert "<active_task" in active_raw
        assert 'phase="1"' in active_raw
        assert 'status="none"' in active_raw

        plan["current_phase"] = 2
        memory.store_plan(plan)

        tasks = memory.list_uncompleted_tasks()
        assert "task[2]" in tasks

        active_raw = memory.get_active_task()
        assert "<active_task" in active_raw
        assert 'phase="2"' in active_raw
        assert 'status="active"' in active_raw

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_duplicates(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-duplicates")

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
        memory.store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_1_evidence = ["outputs/OP_20260302/auth_analyzer3459734.json"]
        task_2_title = "Check GraphQL schema exposure"
        task_2_evidence = ["outputs/OP_20260302/graphql3497539745.json"]
        task_3_title = "Check for SQL injection"
        task_3_evidence = ["outputs/OP_20260302/advanced_payload_coord384758374.json"]

        create_raw = memory.create_tasks(
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
        )

        assert "tasks created" in create_raw.lower()
        assert "<active_task" in create_raw

        create_dup1 = memory.create_tasks(
            [
                memory.TaskCreate(
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=None,
                    status="pending",
                    evidence=task_1_evidence,
                ),
            ]
        )

        assert "tasks created" in create_dup1.lower()
        assert "task[2]" in memory.list_uncompleted_tasks()

        create_dup2 = memory.create_tasks(
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
        )

        assert "tasks created" in create_dup2.lower()
        assert "task[3]" in memory.list_uncompleted_tasks()

        create_new2 = memory.create_tasks(
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
        )

        assert "tasks created" in create_new2.lower()
        assert "task[4]" in memory.list_uncompleted_tasks()

        # Fuzzy duplicate check
        create_fuzzy = memory.create_tasks(
            [
                memory.TaskCreate(
                    title="Enumerate login endpoint",
                    # slightly different title: "Enumerate login endpoints" vs "Enumerate login endpoint"
                    objective="Find authentication entry points.",  # slightly different objective: "." at the end
                    phase=None,
                    status="pending",
                ),
            ]
        )

        assert "tasks created" in create_fuzzy.lower()
        assert "task[4]" in memory.list_uncompleted_tasks()

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_store_plan_persistence(tmp_path, monkeypatch):
    """Verify that store_plan and get_plan use SQLite correctly."""
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-persistence")

    try:
        plan = {
            "objective": "Initial Objective",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [
                {
                    "id": 1,
                    "title": "Phase 1",
                    "status": "active",
                    "criteria": "Criteria 1",
                }
            ],
            "assessment_complete": False,
        }

        # Store plan
        memory.store_plan(plan)

        # Retrieve plan
        retrieved_plan = memory.get_plan()
        assert retrieved_plan is not None
        assert "Initial Objective,1," in retrieved_plan

        # Verify it's in SQLite
        op_id = "test-op-persistence"
        sqlite_plan = memory._PLAN_STORE.get_plan(op_id)
        assert sqlite_plan is not None
        assert sqlite_plan.objective == "Initial Objective"

        # Update plan
        plan["objective"] = "Updated Objective"
        memory.store_plan(plan)

        # Retrieve updated
        updated_plan = memory.get_plan()
        assert "Updated Objective" in updated_plan

        # Verify update in SQLite
        updated_sqlite_plan = memory._PLAN_STORE.get_plan(op_id)
        assert updated_sqlite_plan.objective == "Updated Objective"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_more_fuzzy(tmp_path, monkeypatch):
    """Test more fuzzy matching cases for task creation."""
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-fuzzy-more")

    try:
        # Need a plan first
        plan = {
            "objective": "Test fuzzy",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "C1"}],
            "assessment_complete": False,
        }
        memory.store_plan(plan)

        # 1. Original task
        memory.create_tasks([
            memory.TaskCreate(title="Scan for open ports", objective="Identify services on the target", phase=1,
                              status="pending")
        ])
        assert "task[1]" in memory.list_uncompleted_tasks()

        # 2. Case variation
        memory.create_tasks([
            memory.TaskCreate(title="SCAN FOR OPEN PORTS", objective="identify services on the target", phase=1,
                              status="pending")
        ])
        assert "task[1]" in memory.list_uncompleted_tasks()

        # 3. Minor typo/difference (within 90% threshold)
        # "Scan for open ports" (19 chars)
        # "Scan for open port" (18 chars) -> ratio approx 97%
        memory.create_tasks([
            memory.TaskCreate(title="Scan for open port", objective="Identify service on the target", phase=1,
                              status="pending")
        ])
        assert "task[1]" in memory.list_uncompleted_tasks()

        # 4. Significant difference
        memory.create_tasks([
            memory.TaskCreate(title="Exploit vulnerability", objective="Gain access to the system", phase=1,
                              status="pending")
        ])
        assert "task[2]" in memory.list_uncompleted_tasks()

        # 5. Check SQLite task count for this operation
        op_id = "test-op-fuzzy-more"
        tasks = memory._PLAN_STORE.get_tasks(op_id)
        assert len(tasks) == 2  # One original, one "Significant difference"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_mem0_task_lifecycle(tmp_path, monkeypatch):
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-lifecycle")

    def _list_tasks():
        op_id = os.getenv("CYBER_OPERATION_ID", "test-op-lifecycle")
        result = memory._MEMORY_CLIENT._list_tasks_latest(user_id=memory._MEMORY_CONFIG.get("user_id"), run_id=op_id)
        return result

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
        memory.store_plan(plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"
        task_3_title = "Check for SQL injection"

        memory.create_tasks(
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
        assert len(_list_tasks()) == 3

        active_raw = memory.get_active_task()
        assert isinstance(active_raw, str)
        assert "<active_task" in active_raw
        assert 'phase="1"' in active_raw
        assert 'status="active"' in active_raw
        assert len(_list_tasks()) == 3

        active_raw2 = memory.task_done("done")
        assert isinstance(active_raw2, str)
        assert active_raw != active_raw2
        assert len(_list_tasks()) == 3

        active_none = memory.task_done("blocked")
        assert isinstance(active_none, str)
        assert "<active_task" in active_none
        assert 'phase="1"' in active_none
        assert 'status="none"' in active_none
        assert len(_list_tasks()) == 3

        plan["current_phase"] = 2
        plan["phases"][0]["status"] = "done"
        plan["phases"][1]["status"] = "active"
        memory.store_plan(plan)

        active_raw3 = memory.get_active_task()
        assert isinstance(active_raw3, str)
        assert "<active_task" in active_raw3
        assert 'phase="2"' in active_raw3
        assert 'status="active"' in active_raw3
        assert len(_list_tasks()) == 3

        active_none2 = memory.task_done("blocked")
        assert isinstance(active_none2, str)
        assert "<active_task" in active_none2
        assert 'phase="2"' in active_none2
        assert 'status="none"' in active_none2

        task_memories = _list_tasks()
        assert len(task_memories) == 3
        assert set([task.status for task in task_memories]) == {"blocked", "done"}

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_sensitive_urls(tmp_path, monkeypatch):
    """Verify that tasks with different URLs are not considered duplicates, even if similar."""
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-urls")

    try:
        plan = {
            "objective": "Test sensitive URLs",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "C1"}],
            "assessment_complete": False,
        }
        memory.store_plan(plan)

        # 1. Create a task with a URL
        url1 = "http://example.com/api/v1/user/details"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Check endpoint {url1}",
                objective=f"Verify access to {url1}",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a slightly different URL (non-numeric difference)
        url2 = "http://example.com/api/v1/user/profile"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Check endpoint {url2}",
                objective=f"Verify access to {url2}",
                phase=1,
                status="pending"
            )
        ])

        assert "task[2]" in memory.list_uncompleted_tasks(), f"Expected new task for different URL"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_parameterized_urls(tmp_path, monkeypatch):
    """Verify that tasks with different parameters in URLs are considered duplicates."""
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-urls")

    try:
        plan = {
            "objective": "Test parameterized URLs",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "C1"}],
            "assessment_complete": False,
        }
        memory.store_plan(plan)

        # 1. Create a task with a URL
        url1 = "https://example.com/api/v1/user?userId=1"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Check endpoint {url1}",
                objective=f"Test endpoint {url1} for web vulnerabilities",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a different parameter value
        url2 = "https://example.com/api/v1/user?userId=2"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Check endpoint {url2}",
                objective=f"Test endpoint {url2} for web vulnerabilities",
                phase=1,
                status="pending"
            )
        ])

        assert "task[1]" in memory.list_uncompleted_tasks(), f"Expected duplicate task for different parameter"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_parameterized_url_paths(tmp_path, monkeypatch):
    """Verify that tasks with different parameters in URL paths are considered duplicates."""
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-urls")

    try:
        plan = {
            "objective": "Test parameterized URL paths",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "C1"}],
            "assessment_complete": False,
        }
        memory.store_plan(plan)

        # 1. Create a task with a URL
        url1 = "http://example.com/api/v1/user/1"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Check endpoint {url1}",
                objective=f"Verify access to {url1}",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a different path value
        url2 = "http://example.com/api/v1/user/2"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Check endpoint {url2}",
                objective=f"Verify access to {url2}",
                phase=1,
                status="pending"
            )
        ])

        assert "task[1]" in memory.list_uncompleted_tasks(), f"Expected duplicate task for different parameterized path"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_parameterized_urls_batched(tmp_path, monkeypatch):
    """Verify that tasks with different parameters are considered duplicates submitted in the same tool call."""
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-urls")

    try:
        plan = {
            "objective": "Test parameterized URLs",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "C1"}],
            "assessment_complete": False,
        }
        memory.store_plan(plan)

        url1 = "https://example.com/api/v1/user?userId=1"
        url2 = "https://example.com/api/v1/user?userId=2"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Check endpoint {url1}",
                objective=f"Test endpoint {url1} for web vulnerabilities",
                phase=1,
                status="pending"
            ),
            memory.TaskCreate(
                title=f"Check endpoint {url2}",
                objective=f"Test endpoint {url2} for web vulnerabilities",
                phase=1,
                status="pending"
            )
        ])

        tasks = memory.list_uncompleted_tasks()
        assert "task[1]" in tasks, "Expected ADD for first task, and DUPLICATE for different parameter"
        assert url1 in tasks, "Expected ADD for first task"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_sensitive_paths(tmp_path, monkeypatch):
    """Verify that tasks with different file paths are not considered duplicates, even if similar."""
    from modules.tools import memory

    _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-paths")

    try:
        plan = {
            "objective": "Test sensitive paths",
            "current_phase": 1,
            "total_phases": 1,
            "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "C1"}],
            "assessment_complete": False,
        }
        memory.store_plan(plan)

        # 1. Create a task with a path
        path1 = "/etc/passwd"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Read file {path1}",
                objective=f"Check permissions of {path1}",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a slightly different path
        path2 = "/etc/shadow"
        memory.create_tasks([
            memory.TaskCreate(
                title=f"Read file {path2}",
                objective=f"Check permissions of {path2}",
                phase=1,
                status="pending"
            )
        ])

        tasks = memory.list_uncompleted_tasks()
        assert "task[2]" in tasks, "Expected ADD for different path"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None
