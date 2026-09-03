import pytest

from modules.agents.run_policy import AgentRunPolicy


def test_agent_run_policy_normalizes_limits_and_collection_inputs():
    policy = AgentRunPolicy(
        max_actionless_calls=0,
        max_agent_calls=-2,
        max_model_turns="0",
        required_tool_names=["shell", "shell", "artifact"],
        ignored_terminal_tool_names=["create_tasks"],
        recovery_allowed_tool_names=["record_task_acceptance"],
        recovery_objective=None,
        recovery_next_action=0,
    )

    assert policy.max_actionless_calls == 1
    assert policy.max_agent_calls == 1
    assert policy.max_model_turns == 1
    assert policy.required_tool_names == frozenset({"shell", "artifact"})
    assert policy.ignored_terminal_tool_names == frozenset({"create_tasks"})
    assert policy.recovery_allowed_tool_names == frozenset({"record_task_acceptance"})
    assert policy.recovery_objective == ""
    assert policy.recovery_next_action == ""


@pytest.mark.parametrize("mode", ["unexpected", "", None])
def test_agent_run_policy_rejects_unknown_actionless_modes(mode):
    with pytest.raises(ValueError, match="actionless_mode"):
        AgentRunPolicy(actionless_mode=mode)
