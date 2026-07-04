#!/usr/bin/env python3
"""Unit tests for budget-progress prompt rebuilding."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from modules.handlers.prompt_rebuild_hook import PromptRebuildHook


@pytest.fixture
def mock_callback_handler():
    handler = MagicMock()
    handler.get_budget_progress = MagicMock(return_value=0)
    handler.emitter = MagicMock()
    return handler


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.search = MagicMock(return_value=[])
    return memory


@pytest.fixture
def mock_config(tmp_path):
    config = MagicMock()
    config.output_dir = str(tmp_path / "outputs")
    config.provider = "ollama"
    config.target = "test-target"
    config.module = "web"
    config.budget = MagicMock()
    return config


@pytest.fixture
def setup_operation_folder(tmp_path, mock_config):
    from modules.handlers.utils import sanitize_target_name

    output_dir = Path(mock_config.output_dir)
    target_name = sanitize_target_name("test-target")
    operation_folder = output_dir / target_name / "OP_TEST123"
    operation_folder.mkdir(parents=True, exist_ok=True)
    (operation_folder / "execution_prompt_optimized.txt").write_text("Original execution prompt content")
    return operation_folder


def _hook(mock_callback_handler, mock_memory, mock_config, **kwargs):
    return PromptRebuildHook(
        callback_handler=mock_callback_handler,
        memory_instance=mock_memory,
        config=mock_config,
        target="test-target",
        objective="test objective",
        operation_id="OP_TEST123",
        module="web",
        **kwargs,
    )


def _event():
    mock_event = MagicMock()
    mock_event.agent = MagicMock()
    mock_event.agent.system_prompt = "original prompt"
    return mock_event


def test_prompt_rebuild_hook_initialization(mock_callback_handler, mock_memory, mock_config):
    hook = _hook(mock_callback_handler, mock_memory, mock_config, rebuild_interval=20)

    assert hook.target == "test-target"
    assert hook.objective == "test objective"
    assert hook.operation_id == "OP_TEST123"
    assert hook.module == "web"
    assert hook.rebuild_interval == 20
    assert hook.last_rebuild_progress == 0
    assert hook.force_rebuild is False
    assert hook.last_phase is None


def test_prompt_rebuild_hook_register_hooks(mock_callback_handler, mock_memory, mock_config):
    hook = _hook(mock_callback_handler, mock_memory, mock_config)
    mock_registry = MagicMock()

    hook.register_hooks(mock_registry)

    mock_registry.add_callback.assert_called_once()
    assert mock_registry.add_callback.call_args[0][1] == hook.check_if_rebuild_needed


def test_prompt_rebuild_not_triggered_before_interval(
    mock_callback_handler, mock_memory, mock_config, setup_operation_folder
):
    hook = _hook(mock_callback_handler, mock_memory, mock_config, rebuild_interval=20)
    mock_callback_handler.get_budget_progress.return_value = 10
    event = _event()

    hook.check_if_rebuild_needed(event)

    assert event.agent.system_prompt == "original prompt"


def test_prompt_rebuild_triggered_at_interval(
    mock_callback_handler, mock_memory, mock_config, setup_operation_folder
):
    hook = _hook(mock_callback_handler, mock_memory, mock_config, rebuild_interval=20)
    mock_callback_handler.get_budget_progress.return_value = 20
    event = _event()

    with patch("modules.prompts.get_system_prompt", return_value="rebuilt prompt"):
        hook.check_if_rebuild_needed(event)

    assert event.agent.system_prompt == (
        "rebuilt prompt\n\n## MODULE EXECUTION GUIDANCE\nOriginal execution prompt content"
    )
    assert hook.last_rebuild_progress == 20


def test_prompt_rebuild_triggered_by_force_flag(
    mock_callback_handler, mock_memory, mock_config, setup_operation_folder
):
    hook = _hook(mock_callback_handler, mock_memory, mock_config, rebuild_interval=20)
    mock_callback_handler.get_budget_progress.return_value = 5
    hook.set_force_rebuild()
    event = _event()

    with patch("modules.prompts.get_system_prompt", return_value="rebuilt prompt"):
        hook.check_if_rebuild_needed(event)

    assert event.agent.system_prompt == (
        "rebuilt prompt\n\n## MODULE EXECUTION GUIDANCE\nOriginal execution prompt content"
    )
    assert hook.force_rebuild is False
    assert hook.last_rebuild_progress == 5


def test_phase_change_detection(mock_callback_handler, mock_memory, mock_config):
    from modules.tools.memory import OperationPlan, PlanPhase

    hook = _hook(mock_callback_handler, mock_memory, mock_config)
    plan1 = OperationPlan(
        objective="test objective",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Phase 1", status="active", criteria="Criteria 1"),
            PlanPhase(id=2, title="Phase 2", status="pending", criteria="Criteria 2"),
        ],
    )
    plan2 = OperationPlan(
        objective="test objective",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Phase 1", status="done", criteria="Criteria 1"),
            PlanPhase(id=2, title="Phase 2", status="active", criteria="Criteria 2"),
        ],
    )

    mock_memory.get_active_plan.return_value = plan1
    assert hook._phase_changed() is False
    assert hook.last_phase == 1
    assert hook._phase_changed() is False

    mock_memory.get_active_plan.return_value = plan2
    assert hook._phase_changed() is True
    assert hook.last_phase == 2


def test_execution_prompt_modification_detection(
    mock_callback_handler, mock_memory, mock_config, setup_operation_folder
):
    hook = _hook(mock_callback_handler, mock_memory, mock_config)
    exec_prompt_path = hook.exec_prompt_path
    assert hook._execution_prompt_modified() is False

    modified_content = "Modified execution prompt content" + " " * 100
    exec_prompt_path.write_text(modified_content)
    current_mtime = exec_prompt_path.stat().st_mtime
    import os

    os.utime(exec_prompt_path, (current_mtime + 10, current_mtime + 10))
    mock_callback_handler.get_budget_progress.return_value = 3
    event = _event()

    with patch("modules.prompts.get_system_prompt", return_value="rebuilt due to prompt change"):
        hook.check_if_rebuild_needed(event)

    assert event.agent.system_prompt == (
        f"rebuilt due to prompt change\n\n## MODULE EXECUTION GUIDANCE\n{modified_content.strip()}"
    )
    assert hook.last_rebuild_progress == 3


def test_query_memory_overview(mock_callback_handler, mock_memory, mock_config):
    hook = _hook(mock_callback_handler, mock_memory, mock_config)
    mock_memory.list_memories.return_value = [
        {"memory": "Critical finding", "metadata": {"severity": "critical"}},
        {"memory": "High finding 1", "metadata": {"severity": "high"}},
        {"memory": "High finding 2", "metadata": {"severity": "high"}},
        {"memory": "Medium finding", "metadata": {"severity": "medium"}},
    ]

    overview = hook._query_memory_overview()

    assert overview["total_count"] == 4
    assert len(overview["sample"]) == 3
    assert overview["recent_summary"] is not None


def test_rebuild_with_memory_and_plan_context(
    mock_callback_handler, mock_memory, mock_config, setup_operation_folder
):
    from modules.tools.memory import OperationPlan, PlanPhase

    hook = _hook(mock_callback_handler, mock_memory, mock_config, rebuild_interval=20)
    mock_memory.list_memories.return_value = [
        {"memory": "Critical finding", "metadata": {"severity": "critical"}},
    ]
    mock_memory.get_active_plan.return_value = OperationPlan(
        objective="test objective",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Phase 1", status="active", criteria="Criteria 1"),
            PlanPhase(id=2, title="Phase 2", status="pending", criteria="Criteria 2"),
        ],
    )
    mock_callback_handler.get_budget_progress.return_value = 20
    event = _event()

    with patch("modules.prompts.get_system_prompt", return_value="rebuilt prompt") as mock_get_prompt:
        hook.check_if_rebuild_needed(event)

    call_kwargs = mock_get_prompt.call_args[1]
    assert call_kwargs["target"] == "test-target"
    assert call_kwargs["objective"] == "test objective"
    assert call_kwargs["operation_id"] == "OP_TEST123"
    assert call_kwargs["progress_percent"] == 20
    assert "current_step" not in call_kwargs
    assert "max_steps" not in call_kwargs
    assert call_kwargs["memory_overview"] is not None
    assert call_kwargs["plan_snapshot"] is not None


def test_rebuild_handles_errors_gracefully(
    mock_callback_handler, mock_memory, mock_config, setup_operation_folder
):
    hook = _hook(mock_callback_handler, mock_memory, mock_config, rebuild_interval=20)
    mock_callback_handler.get_budget_progress.return_value = 20
    event = _event()

    with patch("modules.prompts.get_system_prompt", side_effect=Exception("Rebuild failed")):
        hook.check_if_rebuild_needed(event)

    assert event.agent.system_prompt == "original prompt"


def test_default_rebuild_interval_percent(mock_callback_handler, mock_memory, mock_config):
    hook = _hook(mock_callback_handler, mock_memory, mock_config)

    assert hook.rebuild_interval == 20


def test_compute_rebuild_interval_percent():
    assert PromptRebuildHook.compute_rebuild_interval_percent() == 20
