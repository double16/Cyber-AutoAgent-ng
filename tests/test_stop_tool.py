import pytest
from unittest.mock import MagicMock, patch
from modules.tools.stop import stop, TOOL_SPEC
from modules.tools.memory import OperationPlan, Task


def test_tool_spec():
    assert TOOL_SPEC["name"] == "stop"
    assert "reason" in TOOL_SPEC["inputSchema"]["json"]["properties"]


def test_stop_success_no_plan():
    tool_use = {
        "toolUseId": "test_id",
        "input": {"reason": "Test reason"}
    }
    request_state = {}

    with patch("modules.tools.stop.get_memory_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = None

        result = stop(tool_use, request_state=request_state)

        assert result["status"] == "success"
        assert result["toolUseId"] == "test_id"
        assert "Test reason" in result["content"][0]["text"]
        assert request_state["stop_event_loop"] is True


def test_stop_success_default_reason():
    tool_use = {
        "toolUseId": "test_id",
        "input": {}
    }
    request_state = {}

    with patch("modules.tools.stop.get_memory_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = None

        result = stop(tool_use, request_state=request_state)

        assert result["status"] == "success"
        assert "No reason provided" in result["content"][0]["text"]
        assert request_state["stop_event_loop"] is True


def test_stop_success_plan_complete():
    tool_use = {"toolUseId": "test_id", "input": {}}
    request_state = {}
    plan = MagicMock(spec=OperationPlan)
    plan.assessment_complete = True

    with patch("modules.tools.stop.get_memory_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = plan

        result = stop(tool_use, request_state=request_state)
        assert result["status"] == "success"
        assert request_state["stop_event_loop"] is True


def test_stop_success_last_phase():
    tool_use = {"toolUseId": "test_id", "input": {}}
    request_state = {}
    plan = MagicMock(spec=OperationPlan)
    plan.assessment_complete = False
    plan.current_phase = 3
    plan.total_phases = 3

    with patch("modules.tools.stop.get_memory_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = plan

        result = stop(tool_use, request_state=request_state)
        assert result["status"] == "success"
        assert request_state["stop_event_loop"] is True


def test_stop_error_active_task_remains():
    tool_use = {"toolUseId": "test_id", "input": {}}
    request_state = {}

    plan = MagicMock(spec=OperationPlan)
    plan.assessment_complete = False
    plan.current_phase = 1
    plan.total_phases = 2

    agent = MagicMock()
    agent.callback_handler.current_step = 5
    agent.callback_handler.max_steps = 100

    active_task = MagicMock(spec=Task)

    plan.total_phases = 3
    plan.current_phase = 2
    # phase_step_start = 100 * (2 - 1) // 3 = 33
    # 0.9 * 33 = 29.7
    agent.callback_handler.current_step = 20  # 20 < 29.7 is True

    with patch("modules.tools.stop.get_memory_client") as mock_get_client, \
            patch("modules.tools.stop.active_task_message") as mock_msg:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = plan
        mock_client.get_or_activate_next_task_in_phase.return_value = (active_task, False)
        mock_msg.return_value = "Run Task 1"

        result = stop(tool_use, request_state=request_state, agent=agent)

        assert result["status"] == "error"
        assert "MANDATORY ACTION" in result["content"][0]["text"]
        assert "Run Task 1" in result["content"][0]["text"]
        assert "stop_event_loop" not in request_state


def test_stop_error_no_active_task_but_plan_incomplete():
    tool_use = {"toolUseId": "test_id", "input": {}}
    request_state = {}

    plan = MagicMock(spec=OperationPlan)
    plan.assessment_complete = False
    plan.current_phase = 1
    plan.total_phases = 3

    agent = MagicMock()
    agent.callback_handler.current_step = 10
    agent.callback_handler.max_steps = 100

    with patch("modules.tools.stop.get_memory_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = plan
        mock_client.get_or_activate_next_task_in_phase.return_value = (None, False)

        result = stop(tool_use, request_state=request_state, agent=agent)

        assert result["status"] == "error"
        assert "move to phase 2" in result["content"][0]["text"]
        assert "stop_event_loop" not in request_state


def test_stop_success_above_threshold():
    tool_use = {"toolUseId": "test_id", "input": {}}
    request_state = {}

    plan = MagicMock(spec=OperationPlan)
    plan.assessment_complete = False
    plan.current_phase = 2
    plan.total_phases = 3

    agent = MagicMock()
    agent.callback_handler.max_steps = 100
    # phase_step_start = 100 * (2 - 1) // 3 = 33
    # 0.9 * 33 = 29.7
    agent.callback_handler.current_step = 35  # 35 < 29.7 is False

    active_task = MagicMock(spec=Task)

    with patch("modules.tools.stop.get_memory_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = plan
        mock_client.get_or_activate_next_task_in_phase.return_value = (active_task, False)

        result = stop(tool_use, request_state=request_state, agent=agent)

        assert result["status"] == "success"
        assert request_state["stop_event_loop"] is True


def test_stop_success_no_agent_or_callback():
    tool_use = {"toolUseId": "test_id", "input": {}}
    request_state = {}

    plan = MagicMock(spec=OperationPlan)
    plan.assessment_complete = False
    plan.current_phase = 1
    plan.total_phases = 3

    with patch("modules.tools.stop.get_memory_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_active_plan.return_value = plan

        # Case 1: No agent
        result = stop(tool_use, request_state=request_state)
        assert result["status"] == "success"
        assert request_state["stop_event_loop"] is True

        # Case 2: Agent has no callback_handler
        request_state = {}

        # Using a regular object for Case 2 and 3 to avoid MagicMock's automatic attribute creation
        # or properly configuring MagicMock.
        class SimpleAgent:
            pass

        agent = SimpleAgent()
        result = stop(tool_use, request_state=request_state, agent=agent)
        assert result["status"] == "success"
        assert request_state["stop_event_loop"] is True

        # Case 3: Callback handler missing current_step or max_steps
        request_state = {}

        class AgentWithEmptyHandler:
            def __init__(self):
                self.callback_handler = SimpleAgent()

        agent = AgentWithEmptyHandler()
        result = stop(tool_use, request_state=request_state, agent=agent)
        assert result["status"] == "success"
        assert request_state["stop_event_loop"] is True
