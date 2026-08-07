import json
import os

import pytest

from tests.helpers import memory_tasks
from tests.helpers.acceptance import task_proposal


def _task_create(memory, *args, **kwargs):
    del memory, args
    title = kwargs.pop("title")
    objective = kwargs.pop("objective")
    kwargs.pop("acceptance", None)
    kwargs.pop("phase", None)
    kwargs.pop("status", None)
    kwargs.pop("evidence", None)
    return task_proposal(title, objective, criterion_description=title)


def _initialize_filesystem_memory(memory, tmp_path, monkeypatch, operation_id="test-op-create-tasks"):
    # isolate global client/config for this test
    memory._MEMORY_CLIENT = None
    memory._MEMORY_CONFIG = None
    memory._DATABASE_STORE = None

    embedder_model = "mxbai-embed-large:latest"
    ollama_base_url = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("CYBER_MEMORY_MODE", "operation")
    monkeypatch.setenv("CYBER_OPERATION_ID", operation_id)
    monkeypatch.setenv("CYBER_AGENT_PROVIDER", "ollama")
    monkeypatch.setenv("CYBER_AGENT_EMBEDDING_MODEL", embedder_model)
    monkeypatch.setenv("OLLAMA_HOST", ollama_base_url)

    memory.initialize_memory_system(
        config={
            "embedding_provider": "ollama",
            "embedding_model": embedder_model,
            "embedding_dimensions": 1024,
            "ollama_base_url": ollama_base_url,
            "target_values": ["test-target"],
            "memory_mode": "operation",
            "output_dir": str(tmp_path),
        },
    )


def _activate_next_task_message(memory, phase):
    return memory_tasks.activate_next_task_message(memory, phase)


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
        memory_tasks.store_plan(memory, plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"

        raw = memory.create_tasks(
            [
                _task_create(memory,
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=None,
                    status="pending",
                ),
                _task_create(memory,
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                ),
            ]
        )

        assert isinstance(raw, str)
        assert not raw.startswith("Error:")

        assert json.loads(raw) == {"complete": True, "created_count": 2, "duplicate_count": 0}

        tasks = memory_tasks.list_uncompleted_tasks(memory)
        assert "task[2]" in tasks
        assert tasks.count(",1,") == 2
        assert tasks.count(",pending") == 2
        assert tasks.count(",active") == 0
        assert task_1_title+"," in tasks
        assert task_2_title+"," in tasks

        active_raw = _activate_next_task_message(memory, 1)
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
def test_create_tasks_filesystem_defaults_nonexistent_phase_to_active(tmp_path, monkeypatch):
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
        memory_tasks.store_plan(memory, plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"

        memory.create_tasks(
            [
                _task_create(memory,
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=2,
                    status="pending",
                ),
                _task_create(memory,
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=2,
                    status="pending",
                ),
            ]
        )

        tasks = memory_tasks.list_uncompleted_tasks(memory)
        assert "task[2]" in tasks
        assert task_1_title in tasks
        assert task_2_title in tasks
        stored_tasks = memory._DATABASE_STORE.get_tasks("test-op-fs")
        assert {task.phase for task in stored_tasks} == {1}

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_filesystem_assigns_proposals_to_active_phase(tmp_path, monkeypatch):
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
        memory_tasks.store_plan(memory, plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"

        raw = memory.create_tasks(
            [
                _task_create(memory,
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=2,
                    status="pending",
                ),
                _task_create(memory,
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=2,
                    status="pending",
                ),
            ]
        )

        assert isinstance(raw, str)
        assert not raw.startswith("Error:")

        assert json.loads(raw) == {"complete": True, "created_count": 2, "duplicate_count": 0}

        tasks = memory_tasks.list_uncompleted_tasks(memory)
        assert "task[2]" in tasks

        active_raw = _activate_next_task_message(memory, 1)
        assert "<active_task" in active_raw
        assert 'phase="1"' in active_raw
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
        memory_tasks.store_plan(memory, plan)

        task_1_title = "Enumerate login endpoints"
        task_1_evidence = ["outputs/OP_20260302/auth_analyzer3459734.json"]
        task_2_title = "Check GraphQL schema exposure"
        task_2_evidence = ["outputs/OP_20260302/graphql3497539745.json"]
        task_3_title = "Check for SQL injection"
        task_3_evidence = ["outputs/OP_20260302/advanced_payload_coord384758374.json"]

        create_raw = memory.create_tasks(
            [
                _task_create(memory,
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=None,
                    status="pending",
                    evidence=task_1_evidence,
                ),
                _task_create(memory,
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                    evidence=task_2_evidence,
                ),
            ]
        )

        assert json.loads(create_raw) == {"complete": True, "created_count": 2, "duplicate_count": 0}
        assert "<active_task" not in create_raw

        create_dup1 = memory.create_tasks(
            [
                _task_create(memory,
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=None,
                    status="pending",
                    evidence=task_1_evidence,
                ),
            ]
        )

        assert json.loads(create_dup1) == {"complete": True, "created_count": 0, "duplicate_count": 1}
        assert "task[2]" in memory_tasks.list_uncompleted_tasks(memory)

        create_dup2 = memory.create_tasks(
            [
                _task_create(memory,
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                    evidence=task_2_evidence,
                ),
                _task_create(memory,
                    title=task_2_title,
                    objective="Fuzz GraphQL endpoints",
                    phase=None,
                    status="pending",
                    # evidence=task_2_evidence,
                ),
            ]
        )

        assert json.loads(create_dup2) == {"complete": True, "created_count": 1, "duplicate_count": 1}
        assert "task[3]" in memory_tasks.list_uncompleted_tasks(memory)

        create_new2 = memory.create_tasks(
            [
                _task_create(memory,
                    title=task_3_title,
                    objective="Run sqlmap on endpoint",
                    phase=None,
                    status="pending",
                    evidence=task_3_evidence,
                ),
                _task_create(memory,
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=None,
                    status="pending",
                    evidence=task_2_evidence,
                ),
            ]
        )

        assert json.loads(create_new2) == {"complete": True, "created_count": 1, "duplicate_count": 1}
        assert "task[4]" in memory_tasks.list_uncompleted_tasks(memory)

        # Similar wording remains distinct without semantic guessing.
        create_fuzzy = memory.create_tasks(
            [
                _task_create(memory,
                    title="Enumerate login endpoint",
                    # slightly different title: "Enumerate login endpoints" vs "Enumerate login endpoint"
                    objective="Find authentication entry points.",  # slightly different objective: "." at the end
                    phase=None,
                    status="pending",
                ),
            ]
        )

        assert json.loads(create_fuzzy) == {"complete": True, "created_count": 1, "duplicate_count": 0}
        assert "task[5]" in memory_tasks.list_uncompleted_tasks(memory)

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
        memory_tasks.store_plan(memory, plan)

        # Retrieve plan
        retrieved_plan = memory.get_plan()
        assert retrieved_plan is not None
        assert "Initial Objective,1," in retrieved_plan

        # Verify it's in SQLite
        op_id = "test-op-persistence"
        sqlite_plan = memory._DATABASE_STORE.get_plan(op_id)
        assert sqlite_plan is not None
        assert sqlite_plan.objective == "Initial Objective"

        # Update plan
        plan["objective"] = "Updated Objective"
        memory_tasks.store_plan(memory, plan)

        # Retrieve updated
        updated_plan = memory.get_plan()
        assert "Updated Objective" in updated_plan

        # Verify update in SQLite
        updated_sqlite_plan = memory._DATABASE_STORE.get_plan(op_id)
        assert updated_sqlite_plan.objective == "Updated Objective"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_does_not_deduplicate_by_fuzzy_similarity(tmp_path, monkeypatch):
    """Keep distinct task text instead of guessing semantic equivalence."""
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
        memory_tasks.store_plan(memory, plan)

        # 1. Original task
        memory.create_tasks([
            _task_create(memory, title="Scan for open ports", objective="Identify services on the target", phase=1,
                              status="pending")
        ])
        assert "task[1]" in memory_tasks.list_uncompleted_tasks(memory)

        # 2. Case variation
        memory.create_tasks([
            _task_create(memory, title="SCAN FOR OPEN PORTS", objective="identify services on the target", phase=1,
                              status="pending")
        ])
        assert "task[2]" in memory_tasks.list_uncompleted_tasks(memory)

        # 3. Minor typo/difference (within 90% threshold)
        # "Scan for open ports" (19 chars)
        # "Scan for open port" (18 chars) -> ratio approx 97%
        memory.create_tasks([
            _task_create(memory, title="Scan for open port", objective="Identify service on the target", phase=1,
                              status="pending")
        ])
        assert "task[3]" in memory_tasks.list_uncompleted_tasks(memory)

        # 4. Significant difference
        memory.create_tasks([
            _task_create(memory, title="Exploit vulnerability", objective="Gain access to the system", phase=1,
                              status="pending")
        ])
        assert "task[4]" in memory_tasks.list_uncompleted_tasks(memory)

        # 5. Check SQLite task count for this operation
        op_id = "test-op-fuzzy-more"
        tasks = memory._DATABASE_STORE.get_tasks(op_id)
        assert len(tasks) == 4

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_memory_task_lifecycle(tmp_path, monkeypatch):
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
        memory_tasks.store_plan(memory, plan)

        task_1_title = "Enumerate login endpoints"
        task_2_title = "Check GraphQL schema exposure"
        task_3_title = "Check for SQL injection"

        memory.create_tasks(
            [
                _task_create(memory,
                    title=task_1_title,
                    objective="Find authentication entry points",
                    phase=1,
                    status="pending",
                ),
                _task_create(memory,
                    title=task_2_title,
                    objective="Inspect GraphQL attack surface",
                    phase=1,
                    status="pending",
                ),
                _task_create(memory,
                    title=task_3_title,
                    objective="Run sqlmap on endpoint",
                    phase=2,
                    status="pending",
                ),
            ]
        )
        assert len(_list_tasks()) == 3

        active_raw = _activate_next_task_message(memory, 1)
        assert isinstance(active_raw, str)
        assert "<active_task" in active_raw
        assert 'phase="1"' in active_raw
        assert 'status="active"' in active_raw
        assert len(_list_tasks()) == 3

        active_raw2 = memory_tasks.mark_task_done(memory, "done")
        assert isinstance(active_raw2, str)
        assert active_raw != active_raw2
        assert len(_list_tasks()) == 3

        active_none = memory_tasks.mark_task_done(memory, "blocked")
        assert isinstance(active_none, str)
        assert "<active_task" in active_none
        assert 'phase="1"' in active_none
        assert 'status="active"' in active_none
        assert len(_list_tasks()) == 3

        active_none = memory_tasks.mark_task_done(memory, "blocked")
        assert isinstance(active_none, str)
        assert "<active_task" in active_none
        assert 'phase="1"' in active_none
        assert 'status="none"' in active_none
        assert len(_list_tasks()) == 3

        plan["current_phase"] = 2
        plan["phases"][0]["status"] = "done"
        plan["phases"][1]["status"] = "active"
        memory_tasks.store_plan(memory, plan)

        active_raw3 = _activate_next_task_message(memory, 2)
        assert isinstance(active_raw3, str)
        assert "<active_task" in active_raw3
        assert 'phase="2"' in active_raw3
        assert 'status="none"' in active_raw3
        assert len(_list_tasks()) == 3

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
        memory_tasks.store_plan(memory, plan)

        # 1. Create a task with a URL
        url1 = "http://example.com/api/v1/user/details"
        memory.create_tasks([
            _task_create(memory,
                title=f"Check endpoint {url1}",
                objective=f"Verify access to {url1}",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a slightly different URL (non-numeric difference)
        url2 = "http://example.com/api/v1/user/profile"
        memory.create_tasks([
            _task_create(memory,
                title=f"Check endpoint {url2}",
                objective=f"Verify access to {url2}",
                phase=1,
                status="pending"
            )
        ])

        assert "task[2]" in memory_tasks.list_uncompleted_tasks(memory), "Expected new task for different URL"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_parameterized_urls(tmp_path, monkeypatch):
    """Verify that procedure tasks with different URL values remain distinct."""
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
        memory_tasks.store_plan(memory, plan)

        # 1. Create a task with a URL
        url1 = "https://example.com/api/v1/user?userId=1"
        memory.create_tasks([
            _task_create(memory,
                title=f"Check endpoint {url1}",
                objective=f"Test endpoint {url1} for web vulnerabilities",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a different parameter value
        url2 = "https://example.com/api/v1/user?userId=2"
        memory.create_tasks([
            _task_create(memory,
                title=f"Check endpoint {url2}",
                objective=f"Test endpoint {url2} for web vulnerabilities",
                phase=1,
                status="pending"
            )
        ])

        assert "task[2]" in memory_tasks.list_uncompleted_tasks(memory), "Expected a task for each parameter value"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_parameterized_url_paths(tmp_path, monkeypatch):
    """Verify that procedure tasks with different URL path values remain distinct."""
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
        memory_tasks.store_plan(memory, plan)

        # 1. Create a task with a URL
        url1 = "http://example.com/api/v1/user/1"
        memory.create_tasks([
            _task_create(memory,
                title=f"Check endpoint {url1}",
                objective=f"Verify access to {url1}",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a different path value
        url2 = "http://example.com/api/v1/user/2"
        memory.create_tasks([
            _task_create(memory,
                title=f"Check endpoint {url2}",
                objective=f"Verify access to {url2}",
                phase=1,
                status="pending"
            )
        ])

        assert "task[2]" in memory_tasks.list_uncompleted_tasks(memory), "Expected a task for each parameterized path"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None


@pytest.mark.ollama
def test_create_tasks_parameterized_urls_batched(tmp_path, monkeypatch):
    """Verify that batched procedure tasks with different parameter values remain distinct."""
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
        memory_tasks.store_plan(memory, plan)

        url1 = "https://example.com/api/v1/user?userId=1"
        url2 = "https://example.com/api/v1/user?userId=2"
        memory.create_tasks([
            _task_create(memory,
                title=f"Check endpoint {url1}",
                objective=f"Test endpoint {url1} for web vulnerabilities",
                phase=1,
                status="pending"
            ),
            _task_create(memory,
                title=f"Check endpoint {url2}",
                objective=f"Test endpoint {url2} for web vulnerabilities",
                phase=1,
                status="pending"
            )
        ])

        tasks = memory_tasks.list_uncompleted_tasks(memory)
        assert "task[2]" in tasks, "Expected both parameter-specific procedure tasks"
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
        memory_tasks.store_plan(memory, plan)

        # 1. Create a task with a path
        path1 = "/etc/passwd"
        memory.create_tasks([
            _task_create(memory,
                title=f"Read file {path1}",
                objective=f"Check permissions of {path1}",
                phase=1,
                status="pending"
            )
        ])

        # 2. Try to create a task with a slightly different path
        path2 = "/etc/shadow"
        memory.create_tasks([
            _task_create(memory,
                title=f"Read file {path2}",
                objective=f"Check permissions of {path2}",
                phase=1,
                status="pending"
            )
        ])

        tasks = memory_tasks.list_uncompleted_tasks(memory)
        assert "task[2]" in tasks, "Expected ADD for different path"

    finally:
        memory._MEMORY_CLIENT = None
        memory._MEMORY_CONFIG = None
