import json
import pytest
from unittest.mock import MagicMock, patch
from strands import ToolContext
from modules.tools.memory import store_plan, OperationPlan, Task


def test_store_plan_with_operation_plan_object():
    plan_data = {"objective": "Test", "current_phase": 1, "total_phases": 1,
                 "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "c1"}]}
    plan_obj = OperationPlan.from_obj(plan_data)
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui, patch("modules.tools.memory._operation_id") as mopid:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mopid.return_value = "OP_TEST"
        mock_client.get_active_plan.return_value = None
        mock_client.store_plan.return_value = {"status": "success", "plan": plan_obj.to_toon()}
        result = store_plan(plan_obj)
        mock_client.store_plan.assert_called_once_with(plan=plan_obj, user_id="user", operation_id="OP_TEST")
        assert "plan_overview[1]" in result


def test_store_plan_with_dict():
    plan_dict = {"objective": "Test", "current_phase": 1, "total_phases": 1,
                 "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "c1"}]}
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = None
        mock_client.store_plan.return_value = {"status": "success", "plan": OperationPlan.from_obj(plan_dict).to_toon()}
        result = store_plan(plan_dict)
        args, kwargs = mock_client.store_plan.call_args
        assert isinstance(kwargs["plan"], OperationPlan)
        assert "plan_overview[1]" in result


def test_store_plan_with_json_string():
    plan_dict = {"objective": "Test", "current_phase": 1, "total_phases": 1,
                 "phases": [{"id": 1, "title": "P1", "status": "active", "criteria": "c1"}]}
    plan_json = json.dumps(plan_dict)
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = None
        mock_client.store_plan.return_value = {"status": "success"}
        store_plan(plan_json)
        assert mock_client.store_plan.called

    plan_json_extra = plan_json + "}"
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = None
        mock_client.store_plan.return_value = {"status": "success"}
        store_plan(plan_json_extra)
        assert mock_client.store_plan.called


def test_store_plan_invalid_input():
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = None
        mock_client.store_plan.return_value = {"status": "success"}

        with pytest.raises(ValueError, match="store_plan content must be object/dict or JSON string"):
            store_plan(123)
        with pytest.raises(ValueError, match="Got string that is not valid JSON"):
            store_plan("not a json")


def test_store_plan_phase_change_validation_refusal():
    prev_plan_data = {
        "objective": "Test",
        "current_phase": 1,
        "total_phases": 2,
        "phases": [
            {"id": 1, "title": "P1", "status": "active", "criteria": "c1"},
            {"id": 2, "title": "P2", "status": "pending", "criteria": "c2"}
        ]
    }
    prev_plan = OperationPlan.from_obj(prev_plan_data)
    new_plan_data = prev_plan_data.copy()
    new_plan_data["current_phase"] = 2
    new_plan = OperationPlan.from_obj(new_plan_data)
    mock_tool_context = MagicMock(spec=ToolContext)
    mock_agent = MagicMock()
    mock_tool_context.agent = mock_agent
    mock_agent.callback_handler.current_step = 1
    mock_agent.callback_handler.max_steps = 100
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch(
            "modules.tools.memory._user_id") as mui, patch("modules.tools.memory.active_task_message") as mock_msg:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = prev_plan
        active_task = Task(task_uid="uuid", title="T1", objective="O1", phase=1, status="active")
        mock_client.get_or_activate_next_task_in_phase.return_value = (active_task, False)
        mock_msg.return_value = "msg"
        with pytest.raises(ValueError, match="Cannot advance phase due to activate tasks remaining"):
            store_plan(new_plan, tool_context=mock_tool_context)


def test_store_plan_phase_change_allowed_no_tasks():
    prev_plan_data = {
        "objective": "Test",
        "current_phase": 1,
        "total_phases": 2,
        "phases": [
            {"id": 1, "title": "P1", "status": "active", "criteria": "c1"},
            {"id": 2, "title": "P2", "status": "pending", "criteria": "c2"}
        ]
    }
    prev_plan = OperationPlan.from_obj(prev_plan_data)
    new_plan_data = prev_plan_data.copy()
    new_plan_data["current_phase"] = 2
    new_plan = OperationPlan.from_obj(new_plan_data)
    mock_tool_context = MagicMock(spec=ToolContext)
    mock_agent = MagicMock()
    mock_tool_context.agent = mock_agent
    mock_agent.callback_handler.current_step = 1
    mock_agent.callback_handler.max_steps = 100
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = prev_plan
        mock_client.get_or_activate_next_task_in_phase.return_value = (None, False)
        mock_client.store_plan.return_value = {"status": "success", "plan": new_plan.to_toon()}
        result = store_plan(new_plan, tool_context=mock_tool_context)
        assert "plan_overview[1]" in result
        assert "Test,2,2" in result


def test_store_plan_phase_change_allowed_budget_exhausted():
    prev_plan_data = {
        "objective": "Test",
        "current_phase": 1,
        "total_phases": 2,
        "phases": [
            {"id": 1, "title": "P1", "status": "active", "criteria": "c1"},
            {"id": 2, "title": "P2", "status": "pending", "criteria": "c2"}
        ]
    }
    prev_plan = OperationPlan.from_obj(prev_plan_data)
    new_plan_data = prev_plan_data.copy()
    new_plan_data["current_phase"] = 2
    new_plan = OperationPlan.from_obj(new_plan_data)
    mock_tool_context = MagicMock(spec=ToolContext)
    mock_agent = MagicMock()
    mock_tool_context.agent = mock_agent
    mock_agent.callback_handler.current_step = 46
    mock_agent.callback_handler.max_steps = 100
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = prev_plan
        # Active task remaining but budget is exhausted (current_step > phase_step_start * 0.9)
        # phase_step_start = 100 * (2-1) // 2 = 50. 46 > 50 * 0.9 = 45.
        active_task = Task(task_uid="uuid", title="T1", objective="O1", phase=1, status="active")
        mock_client.get_or_activate_next_task_in_phase.return_value = (active_task, False)
        mock_client.store_plan.return_value = {"status": "success", "plan": new_plan.to_toon()}
        result = store_plan(new_plan, tool_context=mock_tool_context)
        assert "plan_overview[1]" in result
        assert "Test,2,2" in result


def test_store_plan_assessment_complete_reminder():
    """Test store_plan adds a reminder when all phases are done but assessment wasn't marked complete."""
    plan_data = {
        "objective": "Test",
        "current_phase": 1,
        "total_phases": 1,
        "phases": [{"id": 1, "title": "P1", "status": "done", "criteria": "c1"}],
        "assessment_complete": False
    }
    plan_obj = OperationPlan.from_obj(plan_data)
    with patch("modules.tools.memory._ensure_memory_client") as mc, patch("modules.tools.memory._user_id") as mui:
        mock_client = MagicMock()
        mc.return_value = mock_client
        mui.return_value = "user"
        mock_client.get_active_plan.return_value = None

        # We'll simulate what Mem0ServiceClient.store_plan does
        def side_effect(plan, user_id=None, operation_id=None):
            if all(p.status == "done" for p in plan.phases) and not plan.assessment_complete:
                # Need to use object.__setattr__ because OperationPlan is frozen=True
                object.__setattr__(plan, "assessment_complete", True)
                return {
                    "status": "success",
                    "plan": plan.to_toon(),
                    "_reminder": "All phases complete. Call stop('Assessment complete: X phases done, Y findings')"
                }
            return {"status": "success", "plan": plan.to_toon()}

        mock_client.store_plan.side_effect = side_effect

        result = store_plan(plan_obj)

        assert "plan_overview[1]" in result
        assert "All phases complete" in result
        args, kwargs = mock_client.store_plan.call_args
        assert kwargs["plan"].assessment_complete is True
