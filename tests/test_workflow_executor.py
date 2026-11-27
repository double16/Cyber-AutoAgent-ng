"""Shared production executor session lifecycle, evidence and terminal failures."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands.types.exceptions import MaxTokensReachedException

from modules.agents.workflow_executor import workflow_executor_session
from modules.handlers.tool_recovery import ToolOutcomeJournal


@pytest.mark.parametrize("subject", ["https://fixture.test/path", "tcp://fixture.test:1234", "/fixture/source"])
@pytest.mark.parametrize("reason", ["completed", "repeated_tool_loop"])
def test_executor_captures_incremental_outcomes_recovery_and_cleanup(subject, reason):
    journal = ToolOutcomeJournal()
    agent = SimpleNamespace(
        _cyber_callback_handler=SimpleNamespace(tool_outcome_journal=journal),
        _cyber_failure_recovery_hook=SimpleNamespace(unresolved=True, exhausted=True),
        _cyber_evaluator_artifact_read_limit_hook=SimpleNamespace(exhausted=True, limit_events=[{"limit": "read"}]),
        cleanup=Mock(),
    )
    runtime = SimpleNamespace(config=SimpleNamespace(target=subject, objective="Assigned work"), operation_id="OP")
    factory = Mock(return_value=agent)

    def invoke(_agent, _prompt, _policy):
        journal.append(tool_use_id=str(journal.snapshot()), tool_name="fixture", success=True, correctable=False,
                       tool_input={}, output="retained")
        return SimpleNamespace(reason=reason, message="message", details={"cycle_signature": "cycle"}), "result"

    with workflow_executor_session(runtime=runtime, role="task_executor", tools=[], system_prompt="system",
                                   create_agent=factory, invoke=invoke, recovery_guidance=lambda _: "Recover",
                                   logger=Mock()) as runner:
        assert runner.live_outcomes() == []
        first = runner("first")
        second = runner("second")
        assert len(first.outcomes) == len(second.outcomes) == 1
        assert len(runner.live_outcomes()) == 2
        assert first.recovery_required and first.recovery_exhausted and first.artifact_limit_exhausted
        assert first.artifact_limit_events == ({"limit": "read"},)
        assert first.recovery_guidance == "Recover"
        assert first.repeat_loop_detected == (reason == "repeated_tool_loop")
        assert first.repeat_loop_signature == ("cycle" if reason == "repeated_tool_loop" else "")
    agent.cleanup.assert_called_once()
    assert factory.call_args.kwargs["target"] == subject


@pytest.mark.parametrize("role", ["task_executor", "task_creator"])
@pytest.mark.parametrize("classification", [None, "reasoning_loop"])
def test_executor_preserves_max_token_failure_with_or_without_journal(role, classification):
    agent = SimpleNamespace(cleanup=Mock())
    error = MaxTokensReachedException("Bounded failure")
    if classification:
        error.max_token_classification = SimpleNamespace(kind=classification)
        error._max_token_efficiency_recorded = True

    def invoke(*_):
        raise error

    runtime = SimpleNamespace(config=SimpleNamespace(target="fixture", objective="work"), operation_id="OP")
    with workflow_executor_session(runtime=runtime, role=role, tools=[], system_prompt="system",
                                   create_agent=lambda **_: agent, invoke=invoke,
                                   recovery_guidance=lambda _: "", logger=Mock()) as runner:
        result = runner("request")
        assert result.max_tokens_exhausted and result.outcomes == []
        assert result.max_tokens_classification == (classification or "output_truncation")
        assert result.max_token_efficiency_accounted == bool(classification)
        assert ("repeated the same reasoning loop" in result.max_tokens_reason) == bool(classification)
        assert ("Task creator" in result.max_tokens_reason) == (role == "task_creator")
        assert runner.live_outcomes() == []
    agent.cleanup.assert_called_once()


@pytest.mark.parametrize("valid", [False, True])
def test_authentication_requires_its_own_outcome_recorder(valid):
    journal = ToolOutcomeJournal()
    agent = SimpleNamespace(cleanup=Mock(), _cyber_callback_handler=SimpleNamespace(tool_outcome_journal=journal))
    if valid:
        agent._cyber_tool_outcome_recording_hook = SimpleNamespace(journal=journal)
    runtime = SimpleNamespace(config=SimpleNamespace(target="fixture", objective="work"), operation_id="OP")
    def invoke(*_):
        return SimpleNamespace(reason="done", message="", details=None), "done"
    if valid:
        with workflow_executor_session(runtime=runtime, role="authentication_agent", tools=[], system_prompt="",
                                       create_agent=lambda **_: agent, invoke=invoke,
                                       recovery_guidance=lambda _: "", logger=Mock()) as runner:
            assert runner("request").text == "done"
    else:
        with pytest.raises(RuntimeError, match="outcome recorder"):
            with workflow_executor_session(runtime=runtime, role="authentication_agent", tools=[], system_prompt="",
                                           create_agent=lambda **_: agent, invoke=invoke,
                                           recovery_guidance=lambda _: "", logger=Mock()):
                pass
    agent.cleanup.assert_called_once()


def test_cleanup_failure_is_logged_without_discarding_result():
    agent = SimpleNamespace(cleanup=Mock(side_effect=OSError("cleanup unavailable")))
    runtime = SimpleNamespace(config=SimpleNamespace(target="fixture", objective="work"), operation_id="OP")
    logger = Mock()
    with workflow_executor_session(runtime=runtime, role="task_executor", tools=[], system_prompt="",
                                   create_agent=lambda **_: agent,
                                   invoke=lambda *_: (SimpleNamespace(reason="done", message="", details=None), "done"),
                                   recovery_guidance=lambda _: "", logger=logger) as runner:
        assert runner("request").text == "done"
    logger.warning.assert_called_once()
