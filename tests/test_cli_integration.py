#!/usr/bin/env python3

import argparse
import json
import os
import re
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from strands.types.exceptions import MaxTokensReachedException

# Add src to path for imports


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cyberautoagent
from modules.handlers.tool_recovery import ToolOutcomeJournal

ORIGINAL_RUN_TARGET_PREFLIGHT = cyberautoagent.run_target_preflight


@pytest.fixture(autouse=True)
def restore_working_directory_after_test():
    """Prevent operation-scoped CLI working directories from leaking between tests."""

    original_cwd = Path.cwd()
    yield
    os.chdir(original_cwd)


@pytest.fixture(autouse=True)
def bypass_live_target_preflight(monkeypatch):
    """Keep CLI integration tests deterministic and free of host-network dependencies."""

    def successful_preflight(*, logical_target, objective, operation_id, logger, **_kwargs):
        targets = cyberautoagent.resolve_operation_targets(logical_target, objective)
        results = [
            cyberautoagent.TargetValidationResult(
                target.target_id,
                target.value,
                target.type,
                "pass",
                ("test_bypass",),
            )
            for target in targets
        ]
        return targets, results

    monkeypatch.setattr(cyberautoagent, "run_target_preflight", successful_preflight)


@pytest.fixture(autouse=True)
def bypass_live_memory_client(monkeypatch):
    """Keep CLI workflow tests independent of local Qdrant/Ollama availability."""

    monkeypatch.setattr(cyberautoagent, "get_memory_client", Mock())


def test_restore_continuation_state_uses_persisted_objective_and_targets(tmp_path):
    from modules.tools.memory import (
        OperationPlan,
        OperationTarget,
        PlanPhase,
        SQLiteApplicationStore,
    )

    operation_id = "OP_20260812_120000"
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "logical")
    targets = [OperationTarget("target-1", "https://service.example:8443", "network")]
    store.store_plan(
        operation_id,
        OperationPlan(
            objective="Assess the authenticated service",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(1, "Recon", "active", "Map the service")],
            targets=targets,
        ),
    )

    objective, restored = cyberautoagent.restore_continuation_state(
        output_dir=str(tmp_path),
        logical_target="logical",
        operation_id=operation_id,
        incoming_objective="Perform web assessment",
        module="web",
        continuation_requested=True,
        logger=Mock(),
    )

    assert objective == "Assess the authenticated service"
    assert restored == targets


def test_restore_continuation_state_returns_current_objective_when_not_requested(tmp_path):
    logger = Mock()
    objective, restored = cyberautoagent.restore_continuation_state(
        output_dir=str(tmp_path),
        logical_target="logical",
        operation_id="OP",
        incoming_objective="current objective",
        module="web",
        continuation_requested=False,
        logger=logger,
    )
    assert objective == "current objective"
    assert restored is None
    logger.warning.assert_not_called()


def test_restore_continuation_state_warns_when_database_missing(tmp_path):
    logger = Mock()
    objective, restored = cyberautoagent.restore_continuation_state(
        output_dir=str(tmp_path),
        logical_target="logical",
        operation_id="OP",
        incoming_objective="current objective",
        module="web",
        continuation_requested=True,
        logger=logger,
    )
    assert (objective, restored) == ("current objective", None)
    logger.warning.assert_called_once()


def test_restore_continuation_state_handles_missing_plan_and_empty_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(cyberautoagent.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(cyberautoagent, "get_application_database_path", lambda _cfg: str(tmp_path / "db"))
    store = SimpleNamespace(get_plan=Mock(side_effect=[None, SimpleNamespace(objective="saved", targets=[])]))
    monkeypatch.setattr(cyberautoagent, "create_application_store", lambda *args, **kwargs: store)

    logger = Mock()
    assert cyberautoagent.restore_continuation_state(
        output_dir=str(tmp_path), logical_target="logical", operation_id="OP1",
        incoming_objective="current", module="web", continuation_requested=True, logger=logger,
    ) == ("current", None)
    assert cyberautoagent.restore_continuation_state(
        output_dir=str(tmp_path), logical_target="logical", operation_id="OP2",
        incoming_objective="Perform web assessment", module="web", continuation_requested=True, logger=logger,
    ) == ("saved", None)


@pytest.mark.parametrize("error, expected", [
    (FileNotFoundError("gone"), ("current", None)),
    (RuntimeError("broken"), ("current", None)),
])
def test_restore_continuation_state_handles_store_read_failures(monkeypatch, tmp_path, error, expected):
    monkeypatch.setattr(cyberautoagent.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(cyberautoagent, "get_application_database_path", lambda _cfg: str(tmp_path / "db"))
    monkeypatch.setattr(
        cyberautoagent,
        "create_application_store",
        lambda *args, **kwargs: SimpleNamespace(get_plan=Mock(side_effect=error)),
    )
    assert cyberautoagent.restore_continuation_state(
        output_dir=str(tmp_path), logical_target="logical", operation_id="OP",
        incoming_objective="current", module="web", continuation_requested=True, logger=Mock(),
    ) == expected


def test_restore_continuation_state_raises_for_invalid_persisted_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(cyberautoagent.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(cyberautoagent, "get_application_database_path", lambda _cfg: str(tmp_path / "db"))
    monkeypatch.setattr(
        cyberautoagent,
        "create_application_store",
        lambda *args, **kwargs: SimpleNamespace(get_plan=Mock(side_effect=ValueError("invalid"))),
    )
    with pytest.raises(RuntimeError, match="Persisted continuation plan is invalid"):
        cyberautoagent.restore_continuation_state(
            output_dir=str(tmp_path), logical_target="logical", operation_id="OP",
            incoming_objective="current", module="web", continuation_requested=True, logger=Mock(),
        )


def test_via_environment_resolves_before_placeholder_classification(monkeypatch):
    monkeypatch.setenv("CYBER_OBJECTIVE", "Perform web assessment")

    objective = cyberautoagent.resolve_objective_from_environment("via environment")

    assert objective == "Perform web assessment"
    assert cyberautoagent._is_continuation_objective_placeholder(objective, "web") is True


def test_via_environment_requires_a_non_empty_objective(monkeypatch):
    monkeypatch.delenv("CYBER_OBJECTIVE", raising=False)

    with pytest.raises(ValueError, match="CYBER_OBJECTIVE"):
        cyberautoagent.resolve_objective_from_environment("via environment")


def test_langfuse_and_deployment_mode_cover_docker_and_error_paths(monkeypatch):
    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: True)
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test")
    monkeypatch.setattr(cyberautoagent.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200))
    assert cyberautoagent.is_langfuse_available() is True
    assert cyberautoagent.detect_deployment_mode() == "compose"
    monkeypatch.setattr(cyberautoagent.requests, "get", Mock(side_effect=RuntimeError("offline")))
    assert cyberautoagent.is_langfuse_available() is False
    assert cyberautoagent.detect_deployment_mode() == "container"
    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: False)
    monkeypatch.setattr(cyberautoagent, "is_langfuse_available", lambda: False)
    assert cyberautoagent.detect_deployment_mode() == "cli"


def test_langfuse_health_non_200_is_unavailable(monkeypatch):
    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: False)
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test/")
    request = Mock(return_value=SimpleNamespace(status_code=503))
    monkeypatch.setattr(cyberautoagent.requests, "get", request)
    assert cyberautoagent.is_langfuse_available() is False
    request.assert_called_once_with("http://langfuse.test//api/public/health", timeout=2)


def test_langfuse_health_uses_docker_default_host(monkeypatch):
    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: True)
    request = Mock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(cyberautoagent.requests, "get", request)
    assert cyberautoagent.is_langfuse_available() is True
    request.assert_called_once_with("http://langfuse-web:3000/api/public/health", timeout=2)


def test_cli_policy_helpers_cover_tool_deltas_metrics_and_terminal_text_branches():
    handler = SimpleNamespace(tool_counts={"scan": 3, "ignored": 4})
    policy = cyberautoagent.AgentRunPolicy(
        min_tool_calls=2,
        required_tool_names={"scan"},
        ignored_terminal_tool_names={"ignored"},
        terminal_after_required_tools=True,
        allow_text_final_after_tools=True,
        max_actionless_after_tools=1,
    )

    assert cyberautoagent._tool_count_deltas(handler, {"scan": 5}) == {"scan": 0, "ignored": 4}
    assert cyberautoagent._required_tools_satisfied(handler, policy, {"scan": 2}) is False
    assert cyberautoagent._required_tools_satisfied(handler, policy) is True
    assert cyberautoagent._run_policy_allows_terminal_text(handler, policy, 1) is False
    assert cyberautoagent._run_policy_allows_terminal_text(handler, policy, 2) is True
    strict_policy = replace(policy, require_successful_required_tools=True)
    assert cyberautoagent._run_policy_allows_terminal_text(handler, strict_policy, 9) is False

    metrics_handler = SimpleNamespace(process_metrics=Mock())
    cyberautoagent.process_agent_metrics(
        metrics_handler,
        SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={"inputTokens": 4})),
    )
    assert metrics_handler.process_metrics.call_args.args[0].accumulated_usage == {"inputTokens": 4}
    cyberautoagent.process_agent_metrics(None, SimpleNamespace(metrics=None))


def test_cli_policy_helpers_cover_empty_and_ignored_tool_paths():
    policy = cyberautoagent.AgentRunPolicy(
        min_tool_calls=1,
        required_tool_names={"scan"},
        ignored_terminal_tool_names={"noise"},
        terminal_after_required_tools=True,
        allow_text_final_after_tools=False,
    )
    assert cyberautoagent._tool_count_deltas(None) == {}
    handler = SimpleNamespace(tool_counts={"noise": 4})
    assert cyberautoagent._required_tools_satisfied(handler, policy) is False
    assert cyberautoagent._run_policy_allows_terminal_text(handler, policy, 0) is False
    assert cyberautoagent._successful_required_tools_satisfied(SimpleNamespace(), policy, 0) is False

    class Journal:
        def since(self, _baseline):
            return [SimpleNamespace(tool_name="other", success=True)]

    assert cyberautoagent._successful_required_tools_satisfied(
        SimpleNamespace(tool_outcome_journal=Journal()), policy, 0
    ) is False


def test_cli_recoverable_errors_and_agent_invocation_signature_fallbacks():
    assert cyberautoagent.is_recoverable_agent_error(RuntimeError("network connection closed")) is True
    assert cyberautoagent.is_recoverable_agent_error(RuntimeError("unrelated")) is False

    def simple_agent(message):
        return message

    seen = {}

    def bounded_agent(message, **kwargs):
        seen.update(kwargs)
        return message

    assert cyberautoagent._invoke_agent_with_turn_limit(simple_agent, "hello", 3) == "hello"
    assert cyberautoagent._invoke_agent_with_turn_limit(bounded_agent, "hello", 3) == "hello"
    assert seen == {"limits": {"turns": 3}}
    for marker in ("read timed out", "readtimeouterror", "ratelimiterror", "serviceunavailableerror"):
        assert cyberautoagent.is_recoverable_agent_error(RuntimeError(marker)) is True


def test_cli_successful_required_tools_uses_journal_baseline_and_rejects_missing_journal():
    policy = cyberautoagent.AgentRunPolicy(required_tool_names={"scan", "verify"})
    handler = SimpleNamespace(
        tool_outcome_journal=SimpleNamespace(
            since=lambda baseline: [
                SimpleNamespace(tool_name="scan", success=True),
                SimpleNamespace(tool_name="verify", success=False),
            ]
        )
    )
    assert cyberautoagent._successful_required_tools_satisfied(handler, policy, 3) is False
    handler.tool_outcome_journal.since = lambda _baseline: [
        SimpleNamespace(tool_name="scan", success=True),
        SimpleNamespace(tool_name="verify", success=True),
    ]
    assert cyberautoagent._successful_required_tools_satisfied(handler, policy, 3) is True
    assert cyberautoagent._successful_required_tools_satisfied(SimpleNamespace(), policy, 0) is False


def test_agent_run_policy_normalizes_limits_and_rejects_unknown_actionless_modes():
    policy = cyberautoagent.AgentRunPolicy(max_actionless_calls=0, max_agent_calls=0, max_model_turns=0)
    assert policy.max_actionless_calls == 1
    assert policy.max_agent_calls == 1
    assert policy.max_model_turns == 1
    with pytest.raises(ValueError, match="actionless_mode"):
        cyberautoagent.AgentRunPolicy(actionless_mode="unsupported")


def test_cli_metrics_and_workflow_summary_cover_empty_state_and_task_failures(monkeypatch):
    callback = Mock()
    cyberautoagent.process_agent_metrics(callback, SimpleNamespace(metrics=SimpleNamespace(accumulated_usage=None)))
    assert callback.process_metrics.call_count == 0
    assert cyberautoagent.extract_last_assistant_text(42) == ""

    phase = SimpleNamespace(id=1, title="Recon", status="active")
    state = SimpleNamespace(list_tasks=Mock(side_effect=RuntimeError("store unavailable")))
    monkeypatch.setattr(cyberautoagent, "get_memory_client", lambda **_kwargs: state)
    assert cyberautoagent._workflow_coverage_summary(SimpleNamespace(phases=[phase])) == [
        {
            "phase_id": 1,
            "title": "Recon",
            "status": "active",
            "task_count": 0,
            "task_status_counts": {},
        }
    ]
    assert cyberautoagent._workflow_coverage_summary(None) == []
    assert cyberautoagent._workflow_coverage_summary(SimpleNamespace(phases="invalid")) == []


def test_recovery_guidance_returns_empty_or_delegates_shell_help(monkeypatch):
    assert cyberautoagent._recovery_guidance_with_failed_command_help(None, []) == ""
    unresolved = SimpleNamespace(
        unresolved=True,
        failed_executable="curl",
        recovery_guidance=Mock(return_value="use http_request"),
    )
    monkeypatch.setattr(cyberautoagent, "get_shell_command_help_context", lambda executable, tools: f"{executable}:{tools}")
    assert cyberautoagent._recovery_guidance_with_failed_command_help(unresolved, ["http_request"]) == "use http_request"
    unresolved.recovery_guidance.assert_called_once_with("curl:['http_request']")


def test_restore_continuation_state_preserves_explicit_objective(tmp_path):
    from modules.tools.memory import OperationPlan, PlanPhase, SQLiteApplicationStore

    operation_id = "OP_20260812_120001"
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "logical")
    store.store_plan(
        operation_id,
        OperationPlan(
            objective="Persisted objective",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(1, "Recon", "active", "Map the service")],
        ),
    )

    objective, restored = cyberautoagent.restore_continuation_state(
        output_dir=str(tmp_path),
        logical_target="logical",
        operation_id=operation_id,
        incoming_objective="Continue only the authentication checks",
        module="web",
        continuation_requested=True,
        logger=Mock(),
    )

    assert objective == "Continue only the authentication checks"
    assert restored is None


def test_run_target_preflight_validates_supplied_targets_without_resolving(monkeypatch):
    from modules.tools.memory import OperationTarget

    targets = [OperationTarget("target-1", "service.example", "network")]
    monkeypatch.setattr(
        cyberautoagent,
        "resolve_operation_targets",
        Mock(side_effect=AssertionError("continuation targets must not be re-resolved")),
    )
    expected = cyberautoagent.TargetValidationResult(
        "target-1", "service.example", "network", "pass", ("test",)
    )
    monkeypatch.setattr(cyberautoagent, "validate_operation_targets", Mock(return_value=[expected]))
    logger = Mock()

    resolved, results = ORIGINAL_RUN_TARGET_PREFLIGHT(
        logical_target="logical",
        objective="unused",
        operation_id="OP_test",
        logger=logger,
        emitter=Mock(),
        targets=targets,
    )

    assert resolved == targets
    assert len(results) == 1


def test_run_agent_until_terminal_state_orchestrates_terminal_rejection_and_retries(monkeypatch):
    """Drive the controller loop through its terminal contracts without an SDK runtime."""
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", lambda _agent: None)
    monkeypatch.setattr(cyberautoagent.time, "sleep", lambda _seconds: None)

    class Handler:
        def __init__(self):
            self.tool_counts = {}
            self.terminations = []
            self.stop = False
            self.tool_outcome_journal = SimpleNamespace(snapshot=lambda: 0)

        def should_stop(self):
            return self.stop

        def has_reached_limit(self):
            return False

        def emit_termination(self, *args):
            self.terminations.append(args)

    class Agent:
        def __init__(self, handler, steps):
            self.messages = []
            self._handler = handler
            self._steps = iter(steps)

        def __call__(self, _message, **_kwargs):
            step = next(self._steps)
            if isinstance(step, Exception):
                raise step
            if callable(step):
                step(self._handler)
            return step if not callable(step) else SimpleNamespace(state={}, stop_reason="", metrics=None)

    def result(*, state=None, stop_reason=""):
        return SimpleNamespace(state=state or {}, stop_reason=stop_reason, metrics=None)

    budget = cyberautoagent.BudgetConfig(max_duration_minutes=1)
    logger = Mock()
    rejected = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(Handler(), [result(state={cyberautoagent.TERMINAL_TOOL_REJECTED_STATE_KEY: {"error": "denied"}})]),
        callback_handler=Handler(), current_message="go", initial_prompt="go", budget_cfg=budget,
        operation_start=0, max_duration=None, logger=logger,
    )
    assert rejected.reason == "required_tool_rejected"

    repeated = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(Handler(), [result(state={cyberautoagent.REPEATED_TOOL_LOOP_STATE_KEY: {
            "tool_name": "scan", "repeat_count": 3, "cycle_length": 2,
            "tool_names": ["scan", "verify", "scan"], "result_reused": False,
        }})]),
        callback_handler=Handler(), current_message="go", initial_prompt="go", budget_cfg=budget,
        operation_start=0, max_duration=None, logger=logger,
    )
    assert repeated.reason == "repeated_tool_loop"
    assert "scan, verify" in repeated.message

    handler = Handler()
    stalled = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(handler, [result(), result()]), callback_handler=handler,
        current_message="go", initial_prompt="go", budget_cfg=budget, operation_start=0,
        max_duration=None, logger=logger,
        run_policy=cyberautoagent.AgentRunPolicy(max_actionless_calls=2, required_tool_names={"finish"}),
    )
    assert stalled.reason == "stalled"

    handler = Handler()
    recovered = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(handler, [RuntimeError("network connection closed"), result()]), callback_handler=handler,
        current_message="go", initial_prompt="go", budget_cfg=budget, operation_start=0,
        max_duration=None, logger=logger,
        run_policy=cyberautoagent.AgentRunPolicy(max_actionless_calls=1),
    )
    assert recovered.reason == "no_actions"


def test_run_agent_until_terminal_state_honors_sdk_limit_tool_cap_and_callback_stop(monkeypatch):
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", lambda _agent: None)

    class Handler:
        def __init__(self, stop=False):
            self.tool_counts = {}
            self.stop = stop
            self.tool_outcome_journal = SimpleNamespace(snapshot=lambda: 0)

        def should_stop(self):
            return self.stop

        def has_reached_limit(self):
            return False

        def emit_termination(self, *_args):
            return None

    class Agent:
        messages = []

        def __init__(self, handler, outcome):
            self.handler = handler
            self.outcome = outcome

        def __call__(self, _message, **_kwargs):
            if callable(self.outcome):
                self.outcome(self.handler)
                return SimpleNamespace(state={}, stop_reason="", metrics=None)
            return self.outcome

    budget = cyberautoagent.BudgetConfig(max_duration_minutes=1)
    limited = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(Handler(), SimpleNamespace(state={}, stop_reason="limit_turns", metrics=None)),
        callback_handler=Handler(), current_message="go", initial_prompt="go", budget_cfg=budget,
        operation_start=0, max_duration=None, logger=Mock(),
    )
    assert limited.reason == "stalled"

    tool_handler = Handler()
    capped = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(tool_handler, lambda handler: handler.tool_counts.update(scan=1)), callback_handler=tool_handler,
        current_message="go", initial_prompt="go", budget_cfg=budget, operation_start=0,
        max_duration=None, logger=Mock(), run_policy=cyberautoagent.AgentRunPolicy(max_tool_calls=1),
    )
    assert capped.reason == "agent_completed_required_tools"

    stopped_handler = Handler(stop=True)
    stopped = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(stopped_handler, SimpleNamespace(state={}, stop_reason="", metrics=None)),
        callback_handler=stopped_handler, current_message="go", initial_prompt="go", budget_cfg=budget,
        operation_start=0, max_duration=None, logger=Mock(),
    )
    assert stopped.reason == "callback_stop"


def test_workflow_max_token_recovery_orchestrates_reasoning_repair_and_token_escalation(monkeypatch):
    """Exercise controller-owned recovery orchestration without invoking a model provider."""
    import modules.config.models.agent_profiles as profiles

    class Registry:
        def __init__(self, reasoning_level):
            self.settings = SimpleNamespace(reasoning_level=reasoning_level)
            self.repairs = []
            self.boosts = []
            self.successes = []

        def get_settings(self, _role):
            return self.settings

        def apply_reasoning_repair(self, *args, **kwargs):
            self.repairs.append((args, kwargs))

        def record_token_recovery_success(self, *args, **kwargs):
            self.successes.append((args, kwargs))

        def boost_max_tokens_for_retry(self, *args, **kwargs):
            self.boosts.append((args, kwargs))

    class Journal:
        def entries(self):
            return [SimpleNamespace(tool_name="recon", success=True, output_summary="evidence")]

    callback = SimpleNamespace(tool_outcome_journal=Journal(), record_max_token_exhaustion=Mock())
    agent = SimpleNamespace(
        _cyber_agent_type="task_executor",
        _cyber_callback_handler=callback,
        model=SimpleNamespace(_output_tokens=128),
        messages=[],
    )
    policy = cyberautoagent.AgentRunPolicy(required_tool_names={"finish"}, recovery_objective="verify")
    classification = SimpleNamespace(
        kind="reasoning_loop", repetition_ratio=0.9, discarded_tokens=100, is_reasoning_induced=True
    )
    registry = Registry(profiles.ReasoningLevel.HIGH)
    max_error = MaxTokensReachedException("limit")
    recovered = cyberautoagent.AgentRunResult("complete", "done")

    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", Mock(side_effect=[max_error, recovered]))
    monkeypatch.setattr(cyberautoagent, "classify_and_discard_max_token_output", lambda *_args, **_kwargs: (classification, 1))
    monkeypatch.setattr(cyberautoagent, "is_repeated_max_token_pattern", lambda *_args: False)
    monkeypatch.setattr(cyberautoagent, "sanitize_sdk_error", lambda _error: None)
    monkeypatch.setattr(cyberautoagent, "reset_agent_conversation_for_recovery", Mock())
    monkeypatch.setattr(cyberautoagent, "build_task_executor_max_token_prompt", Mock(return_value="recover now"))
    monkeypatch.setattr(profiles, "get_agent_settings_registry", lambda: registry)
    monkeypatch.setattr(profiles, "mutate_agent_model_reasoning", Mock())
    monkeypatch.setattr(profiles, "mutate_agent_model_max_tokens", Mock())

    assert cyberautoagent.run_workflow_agent_with_max_token_recovery(
        agent=agent,
        prompt="initial",
        run_policy=policy,
        callback_handler=callback,
        initial_prompt="initial",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=1),
        operation_start=0,
        max_duration=None,
        logger=Mock(),
    ) == recovered
    assert registry.repairs[-1][1]["permanent"] is True
    callback.record_max_token_exhaustion.assert_called_once()

    token_registry = Registry(profiles.ReasoningLevel.NONE)
    token_error = MaxTokensReachedException("limit")
    token_classification = SimpleNamespace(
        kind="output_limit", repetition_ratio=0.0, discarded_tokens=10, is_reasoning_induced=False
    )
    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", Mock(side_effect=[token_error, recovered]))
    monkeypatch.setattr(cyberautoagent, "classify_and_discard_max_token_output", lambda *_args, **_kwargs: (token_classification, 0))
    monkeypatch.setattr(profiles, "get_agent_settings_registry", lambda: token_registry)

    assert cyberautoagent.run_workflow_agent_with_max_token_recovery(
        agent=agent,
        prompt="initial",
        run_policy=policy,
        callback_handler=callback,
        initial_prompt="initial",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=1),
        operation_start=0,
        max_duration=None,
        logger=Mock(),
    ) == recovered
    assert token_registry.boosts
    assert token_registry.successes


def test_finalization_and_cleanup_orchestrate_completion_health_and_memory_lifecycle(monkeypatch):
    """Cover terminal controller bookkeeping without report, browser, or network side effects."""
    plan = SimpleNamespace(assessment_complete=True)
    callback = SimpleNamespace(
        termination_reason="complete",
        termination_message="done",
        operation_health_snapshot=lambda: {"unresolved_task_count": 0, "incomplete_phase_ids": []},
        emit_operation_terminated=Mock(),
        ensure_report_generated=Mock(),
        trigger_evaluation_on_completion=Mock(),
        emit_operation_finalized=Mock(),
        _report_status="generated",
    )
    monkeypatch.setattr(cyberautoagent, "get_memory_client", lambda **_kwargs: SimpleNamespace(get_active_plan=lambda: plan))
    monkeypatch.setattr(cyberautoagent, "_workflow_coverage_summary", lambda _plan: [{"phase_id": 1}])
    cyberautoagent.finalize_report_and_evaluation(
        agent="agent", callback_handler=callback, target="target", objective="objective", module="web", logger=Mock()
    )
    callback.emit_operation_terminated.assert_called_once()
    callback.ensure_report_generated.assert_called_once()
    callback.trigger_evaluation_on_completion.assert_called_once()
    callback.emit_operation_finalized.assert_called_once_with(report_status="generated", evaluation_status="attempted")

    no_plan_status = cyberautoagent._build_report_completion_status(
        None, SimpleNamespace(termination_reason="budget", termination_message="stopped")
    )
    assert no_plan_status["assessment_complete"] is False
    assert "budget" in no_plan_status["incomplete_reason"]
    mismatched_status = cyberautoagent._build_report_completion_status(
        plan, SimpleNamespace(termination_reason="stalled", termination_message="stopped")
    )
    assert "assessment_complete=true" in mismatched_status["incomplete_reason"]

    async def no_op():
        return None

    cleaned = []
    finalized = []
    agent = SimpleNamespace(cleanup=Mock())
    args = SimpleNamespace(target="target", objective="objective", module="web", keep_memory=False)
    targets = [SimpleNamespace(value="https://target")]
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent.browser, "close_browser", Mock())
    monkeypatch.setattr(cyberautoagent, "channel_close_all", no_op)
    monkeypatch.setattr(cyberautoagent, "close_oast_providers", no_op)
    monkeypatch.setattr(cyberautoagent, "finalize_report_and_evaluation", lambda **kwargs: finalized.append(kwargs))
    monkeypatch.setattr(cyberautoagent, "clean_operation_memory", lambda operation_id, values: cleaned.append((operation_id, values)))
    monkeypatch.setattr(cyberautoagent, "flush_traces", Mock())
    monkeypatch.setattr(cyberautoagent, "close_log_outputs", Mock())
    cyberautoagent.cleanup_operation_resources(
        agent=agent, callback_handler=callback, args=args, operation_id="OP", operation_start=0,
        telemetry="telemetry", logger=Mock(), operation_targets=targets,
    )
    assert finalized and cleaned == [("OP", ["https://target"])]
    agent.cleanup.assert_called_once()

    args.keep_memory = True
    cyberautoagent.cleanup_operation_resources(
        agent=None, callback_handler=None, args=args, operation_id="OP2", operation_start=0,
        telemetry=None, logger=Mock(), operation_targets=targets,
    )
    assert cleaned == [("OP", ["https://target"])]


def test_run_agent_controller_covers_prompt_repair_successful_tools_duration_and_interrupt(monkeypatch):
    """Drive actionless recovery messages and boundary exits through the real controller loop."""
    monkeypatch.setattr(cyberautoagent, "print_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", lambda _agent: None)
    monkeypatch.setattr(cyberautoagent, "interrupted", False)

    class Journal:
        def snapshot(self):
            return 0

        def since(self, _baseline):
            return [SimpleNamespace(tool_name="finish", success=True)]

    class Handler:
        def __init__(self):
            self.tool_counts = {}
            self.tool_outcome_journal = Journal()

        def should_stop(self):
            return False

        def has_reached_limit(self):
            return False

        def emit_termination(self, *_args):
            return None

    class Agent:
        def __init__(self, handler, outcomes):
            self.messages = [
                {"content": []}, {"content": []}, {"content": []},
                {"content": [{"toolUse": {"name": "shell"}}]},
                {"content": []},
            ]
            self.outcomes = iter(outcomes)
            self.received = []
            self.handler = handler

        def __call__(self, message, **_kwargs):
            self.received.append(message)
            return next(self.outcomes)

    def result(state=None):
        return SimpleNamespace(state=state or {}, stop_reason="", metrics=None)
    monkeypatch.setattr(cyberautoagent, "_tool_count_deltas", lambda *_args, **_kwargs: {"prior_work": 1})
    handler = Handler()
    task_agent = Agent(handler, [result(), result(), result(), result()])
    task_result = cyberautoagent.run_agent_until_terminal_state(
        agent=task_agent, callback_handler=handler, current_message="start", initial_prompt="start",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=1), operation_start=time.time(),
        max_duration=None, logger=Mock(),
        run_policy=cyberautoagent.AgentRunPolicy(
            max_actionless_calls=4, required_tool_names={"finish"}, actionless_mode="task_progress"
        ),
    )
    assert task_result.reason == "stalled"
    assert "assigned task" in task_agent.received[1]
    assert len(task_agent.messages) == 4

    handler = Handler()
    strict_agent = Agent(handler, [result(), result(), result(), result()])
    strict_result = cyberautoagent.run_agent_until_terminal_state(
        agent=strict_agent, callback_handler=handler, current_message="start", initial_prompt="start",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=1), operation_start=time.time(),
        max_duration=None, logger=Mock(),
        run_policy=cyberautoagent.AgentRunPolicy(
            max_actionless_calls=4, required_tool_names={"finish"}, allow_text_final_after_tools=False
        ),
    )
    assert strict_result.reason == "stalled"
    assert "text-only response" in strict_agent.received[1]

    completed = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(Handler(), [result({cyberautoagent.TERMINAL_TOOL_COMPLETED_STATE_KEY: {"tool_name": "finish"}})]),
        callback_handler=Handler(), current_message="start", initial_prompt="start",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=1), operation_start=time.time(),
        max_duration=None, logger=Mock(),
        run_policy=cyberautoagent.AgentRunPolicy(require_successful_required_tools=True, required_tool_names={"finish"}),
    )
    assert completed.reason == "agent_completed_required_tools"

    with pytest.raises(cyberautoagent.BudgetLimitReached, match="Duration"):
        cyberautoagent.run_agent_until_terminal_state(
            agent=Agent(Handler(), [result()]), callback_handler=Handler(), current_message="start", initial_prompt="start",
            budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=1), operation_start=0,
            max_duration=1, logger=Mock(), run_policy=cyberautoagent.AgentRunPolicy(max_actionless_calls=4),
        )

    monkeypatch.setattr(cyberautoagent, "interrupted", True)
    interrupted = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent(Handler(), []), callback_handler=Handler(), current_message="start", initial_prompt="start",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=1), operation_start=time.time(),
        max_duration=None, logger=Mock(),
    )
    assert interrupted.reason == "interrupted"


def test_run_agent_controller_covers_unmet_terminal_tool_stopiteration_and_timeout_boundaries(monkeypatch):
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", lambda _agent: None)
    monkeypatch.setattr(cyberautoagent.time, "sleep", lambda _seconds: None)

    class Handler:
        tool_counts = {}
        tool_outcome_journal = SimpleNamespace(snapshot=lambda: 0)

        def __init__(self, reached=False):
            self.reached = reached
            self.terminations = []

        def should_stop(self):
            return False

        def has_reached_limit(self):
            return self.reached

        def emit_termination(self, *args):
            self.terminations.append(args)

    class Agent:
        messages = [
            {"content": []}, {"content": []}, {"content": []},
            {"content": [{"toolUse": {"name": "shell"}}]},
            {"content": ["non-mapping", {"text": "analysis"}]},
        ]

        def __init__(self, values):
            self.values = iter(values)

        def __call__(self, _message, **_kwargs):
            value = next(self.values)
            if isinstance(value, Exception):
                raise value
            return value

    def result(state=None):
        return SimpleNamespace(state=state or {}, stop_reason="", metrics=None)
    budget = cyberautoagent.BudgetConfig(max_duration_minutes=1)
    unmet = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([result({cyberautoagent.TERMINAL_TOOL_COMPLETED_STATE_KEY: {"tool_name": "other"}})]),
        callback_handler=Handler(), current_message="go", initial_prompt="go", budget_cfg=budget,
        operation_start=time.time(), max_duration=None, logger=Mock(),
        run_policy=cyberautoagent.AgentRunPolicy(max_actionless_calls=1, require_successful_required_tools=True,
                                                  required_tool_names={"finish"}),
    )
    assert unmet.reason == "stalled"

    retry_exhausted = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([result(), result(), result()]), callback_handler=Handler(), current_message="go",
        initial_prompt="go", budget_cfg=budget, operation_start=time.time(), max_duration=None, logger=Mock(),
        run_policy=cyberautoagent.AgentRunPolicy(max_actionless_calls=4),
    )
    assert retry_exhausted.reason == "no_actions"

    with pytest.raises(cyberautoagent.BudgetLimitReached, match="Step limit"):
        cyberautoagent.run_agent_until_terminal_state(
            agent=Agent([StopIteration("done")]), callback_handler=Handler(reached=True), current_message="go",
            initial_prompt="go", budget_cfg=budget, operation_start=time.time(), max_duration=None, logger=Mock(),
        )

    timeout = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([RuntimeError("network connection closed")]), callback_handler=Handler(), current_message="go",
        initial_prompt="go", budget_cfg=budget, operation_start=time.time(), max_duration=None, logger=Mock(),
        recoverable_retries=0,
    )
    assert timeout.reason == "network_timeout"


def test_run_agent_controller_handles_falsey_callback_without_optional_termination_events(monkeypatch):
    """Callback adapters can be intentionally falsey while still exposing controller state."""
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", lambda _agent: None)

    class FalseyHandler:
        tool_counts = {}
        tool_outcome_journal = SimpleNamespace(snapshot=lambda: 0)

        def __bool__(self):
            return False

        def should_stop(self):
            return False

        def has_reached_limit(self):
            return False

        def emit_termination(self, *_args):
            raise AssertionError("falsey callback must not emit")

    class Agent:
        messages = []

        def __init__(self, outcomes):
            self.outcomes = iter(outcomes)

        def __call__(self, _message, **_kwargs):
            value = next(self.outcomes)
            if isinstance(value, Exception):
                raise value
            return value

    budget = cyberautoagent.BudgetConfig(max_duration_minutes=1)
    limit = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([SimpleNamespace(state={}, stop_reason="limit_turns", metrics=None)]),
        callback_handler=FalseyHandler(), current_message="go", initial_prompt="go", budget_cfg=budget,
        operation_start=time.time(), max_duration=None, logger=Mock(),
    )
    assert limit.reason == "stalled"

    no_actions = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([SimpleNamespace(state={}, stop_reason="", metrics=None)]), callback_handler=FalseyHandler(),
        current_message="go", initial_prompt="go", budget_cfg=budget, operation_start=time.time(),
        max_duration=None, logger=Mock(),
        run_policy=cyberautoagent.AgentRunPolicy(max_actionless_calls=1, required_tool_names={"finish"}),
    )
    assert no_actions.reason == "stalled"

    max_calls = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([SimpleNamespace(state={}, stop_reason="", metrics=None)]), callback_handler=FalseyHandler(),
        current_message="go", initial_prompt="go", budget_cfg=budget, operation_start=time.time(),
        max_duration=None, logger=Mock(), run_policy=cyberautoagent.AgentRunPolicy(max_agent_calls=1, max_actionless_calls=4),
    )
    assert max_calls.reason == "stalled"

    recovered_after_stop = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([StopIteration("cycle"), SimpleNamespace(state={}, stop_reason="", metrics=None)]),
        callback_handler=FalseyHandler(), current_message="go", initial_prompt="go", budget_cfg=budget,
        operation_start=time.time(), max_duration=None, logger=Mock(),
        run_policy=cyberautoagent.AgentRunPolicy(max_actionless_calls=1),
    )
    assert recovered_after_stop.reason == "no_actions"

    timeout = cyberautoagent.run_agent_until_terminal_state(
        agent=Agent([RuntimeError("network connection closed")]), callback_handler=FalseyHandler(),
        current_message="go", initial_prompt="go", budget_cfg=budget, operation_start=time.time(),
        max_duration=None, logger=Mock(), recoverable_retries=0,
    )
    assert timeout.reason == "network_timeout"


class TestCLIArguments:
    """Test command-line argument parsing"""

    def test_invalid_memory_mode_environment_is_rejected(self, monkeypatch):
        monkeypatch.setenv("CYBER_MEMORY_MODE", "automatic")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cyberautoagent.py",
                "--target",
                "test.com",
                "--objective",
                "test objective",
            ],
        )

        with pytest.raises(SystemExit) as error:
            cyberautoagent.main()

        assert error.value.code == 2

    def test_required_arguments(self):
        """Test that required arguments are parsed correctly"""
        with patch(
            "sys.argv",
            [
                "cyberautoagent.py",
                "--target",
                "test.com",
                "--objective",
                "test objective",
            ],
        ):
            # Mock the setup and execution parts
            with (
                patch("cyberautoagent.setup_logging"),
                patch("cyberautoagent.auto_setup", return_value=[]),
                patch("cyberautoagent.create_agent", return_value=Mock()),
                patch("cyberautoagent.get_initial_prompt"),
                patch("cyberautoagent.print_banner"),
                patch("cyberautoagent.print_section"),
                patch("cyberautoagent.print_status"),
            ):
                # Parse arguments without executing main
                parser = argparse.ArgumentParser()
                parser.add_argument("--objective", type=str, required=True)
                parser.add_argument("--target", type=str, required=True)
                parser.add_argument("--max-duration", type=int, required=True)
                parser.add_argument("--verbose", action="store_true")
                parser.add_argument("--model", type=str)
                parser.add_argument("--region", type=str, default="us-east-1")
                parser.add_argument("--server", type=str, choices=["remote", "local"], default="remote")
                parser.add_argument("--confirmations", action="store_true")

                args = parser.parse_args(["--target", "test.com", "--objective", "test objective", "--max-duration", "300"])

                assert args.target == "test.com"
                assert args.objective == "test objective"
                assert args.server == "remote"  # default
                assert args.max_duration == 300
                assert not args.verbose  # default
                assert not args.confirmations  # default

    def test_server_argument_choices(self):
        """Test that --server argument accepts only valid choices"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--server", type=str, choices=["remote", "local"], default="remote")

        # Valid choices should work
        args = parser.parse_args(["--server", "local"])
        assert args.server == "local"

        args = parser.parse_args(["--server", "remote"])
        assert args.server == "remote"

        # Invalid choice should raise error
        with pytest.raises(SystemExit):
            parser.parse_args(["--server", "invalid"])

    def test_optional_arguments(self):
        """Test optional argument parsing"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--max-duration", type=int, required=True)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--model", type=str)
        parser.add_argument("--region", type=str, default="us-east-1")
        parser.add_argument("--server", type=str, choices=["remote", "local"], default="remote")
        parser.add_argument("--confirmations", action="store_true")

        args = parser.parse_args(
            [
                "--target",
                "test.com",
                "--objective",
                "test objective",
                "--server",
                "local",
                "--max-duration",
                "120",
                "--verbose",
                "--model",
                "custom-model",
                "--region",
                "us-west-2",
                "--confirmations",
            ]
        )

        assert args.target == "test.com"
        assert args.objective == "test objective"
        assert args.server == "local"
        assert args.max_duration == 120
        assert args.verbose is True
        assert args.model == "custom-model"
        assert args.region == "us-west-2"
        assert args.confirmations is True

    def test_new_output_arguments(self):
        """Test that new output configuration arguments are properly parsed"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--output-dir", type=str)
        parser.add_argument("--keep-memory", action="store_true", default=True)

        args = parser.parse_args(
            [
                "--target",
                "test.com",
                "--objective",
                "test objective",
                "--output-dir",
                "/custom/output",
            ]
        )

        assert args.target == "test.com"
        assert args.objective == "test objective"
        assert args.output_dir == "/custom/output"
        assert args.keep_memory is True  # Default is now True

    def test_budget_arguments(self):
        """Test that token and cost budget arguments are properly parsed"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--max-duration", type=int, required=True)
        parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
        parser.add_argument("--max-cost", dest="max_cost", type=float, default=None)

        args = parser.parse_args(
            [
                "--target",
                "test.com",
                "--objective",
                "test objective",
                "--max-duration",
                "60",
                "--max-tokens",
                "250000",
                "--max-cost",
                "12.34",
            ]
        )

        assert args.max_duration == 60
        assert args.max_tokens == 250000
        assert args.max_cost == 12.34

    @pytest.mark.parametrize(
        "flag,value",
        [
            ("--max-tokens", "not-an-int"),
            ("--max-cost", "not-a-float"),
        ],
    )
    def test_budget_arguments_reject_invalid_types(self, flag, value):
        """Test that token and cost budget arguments reject invalid values"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--max-duration", type=int, required=True)
        parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
        parser.add_argument("--max-cost", dest="max_cost", type=float, default=None)

        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "--target",
                    "test.com",
                    "--objective",
                    "test objective",
                    "--max-duration",
                    "60",
                    flag,
                    value,
                ]
            )


class TestMainFunction:
    """Test main function execution flow"""

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent_runtime_resources")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--max-duration",
            "60",
            "--provider",
            "bedrock",
        ],
    )
    def test_main_remote_flow(
        self,
        mock_print_status,
        mock_print_section,
        mock_print_banner,
        mock_get_prompt,
        mock_create_agent,
        mock_create_agent_runtime_resources,
        mock_auto_setup,
        mock_setup_logging,
    ):
        """Test main function execution with remote server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_actions": 0,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = mock_agent
        mock_create_agent_runtime_resources.return_value = SimpleNamespace(callback_handler=mock_handler)
        mock_auto_setup.return_value = ["nmap", "nikto"]
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return immediately
        mock_agent.return_value = "Agent response"

        # This should not raise any exceptions
        with patch("cyberautoagent.MultiAgentWorkflowController") as mock_workflow_controller:
            try:
                cyberautoagent.main()
            except SystemExit as e:
                # main() calls sys.exit(0) on success, which is expected
                assert e.code in [None, 0]

        mock_workflow_controller.return_value.run.assert_called_once()

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent_runtime_resources")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
        ],
    )
    def test_main_local_flow(
        self,
        mock_print_status,
        mock_print_section,
        mock_print_banner,
        mock_get_prompt,
        mock_create_agent,
        mock_create_agent_runtime_resources,
        mock_auto_setup,
        mock_setup_logging,
    ):
        """Test main function execution with local server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_actions": 0,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = mock_agent
        mock_create_agent_runtime_resources.return_value = SimpleNamespace(callback_handler=mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        with patch("cyberautoagent.MultiAgentWorkflowController") as mock_workflow_controller:
            try:
                cyberautoagent.main()
            except SystemExit as e:
                # main() calls sys.exit(0) on success, which is expected
                assert e.code in [None, 0]

        mock_workflow_controller.return_value.run.assert_called_once()

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent_runtime_resources")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
            "--continue",
        ],
    )
    @pytest.mark.skip(reason="Need more mocks")
    def test_main_local_flow_continue(
            self,
            mock_print_status,
            mock_print_section,
            mock_print_banner,
            mock_get_prompt,
            mock_create_agent,
            mock_create_agent_runtime_resources,
            mock_auto_setup,
            mock_setup_logging,
    ):
        """Test main function execution with local server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_actions": 0,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = mock_agent
        mock_create_agent_runtime_resources.return_value = SimpleNamespace(callback_handler=mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        with patch("cyberautoagent.MultiAgentWorkflowController") as mock_workflow_controller:
            try:
                cyberautoagent.main()
            except SystemExit as e:
                # main() calls sys.exit(0) on success, which is expected
                assert e.code in [None, 0]

        mock_workflow_controller.return_value.run.assert_called_once()

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent_runtime_resources")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
            "--report",
        ],
    )
    @pytest.mark.skip(reason="Need more mocks")
    def test_main_local_flow_report(
            self,
            mock_print_status,
            mock_print_section,
            mock_print_banner,
            mock_get_prompt,
            mock_create_agent,
            mock_create_agent_runtime_resources,
            mock_auto_setup,
            mock_setup_logging,
    ):
        """Test main function execution with local server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_actions": 0,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = mock_agent
        mock_create_agent_runtime_resources.return_value = SimpleNamespace(callback_handler=mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        with patch("cyberautoagent.MultiAgentWorkflowController") as mock_workflow_controller:
            try:
                cyberautoagent.main()
            except SystemExit as e:
                # main() calls sys.exit(0) on success, which is expected
                assert e.code in [None, 0]

        mock_workflow_controller.return_value.run.assert_called_once()

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent_runtime_resources")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        ["cyberautoagent.py", "--target", "test.com", "--objective", "test objective", "--max-duration", "60"],
    )
    def test_main_create_agent_failure(
        self,
        mock_print_status,
        mock_create_agent,
        mock_create_agent_runtime_resources,
        mock_auto_setup,
        mock_setup_logging,
    ):
        """Test main function when create_agent fails"""

        mock_create_agent.side_effect = Exception("Agent creation failed")
        mock_create_agent_runtime_resources.return_value = SimpleNamespace(callback_handler=Mock())
        mock_auto_setup.return_value = []

        with pytest.raises(SystemExit) as exc_info:
            cyberautoagent.main()

        assert exc_info.value.code == 1

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent_runtime_resources")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
            "--mcp-enabled",
            "--mcp-conns",
            """[{"id":"mcp1","transport":"streamable-http","server_url":"http://127.0.0.1:8000/mcp"}]""",
        ],
    )
    def test_main_local_mcp_flow(
            self,
            mock_print_status,
            mock_print_section,
            mock_print_banner,
            mock_get_prompt,
            mock_create_agent,
            mock_create_agent_runtime_resources,
            mock_auto_setup,
            mock_setup_logging,
    ):
        """Test main function execution with local server and an MCP"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_actions": 0,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = mock_agent
        mock_create_agent_runtime_resources.return_value = SimpleNamespace(callback_handler=mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        with patch("cyberautoagent.MultiAgentWorkflowController") as mock_workflow_controller:
            try:
                cyberautoagent.main()
            except SystemExit as e:
                # main() calls sys.exit(0) on success, which is expected
                assert e.code in [None, 0]

        mock_workflow_controller.return_value.run.assert_called_once()


class TestEnvironmentVariables:
    """Test environment variable handling"""

    @patch.dict(os.environ, {}, clear=True)
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test",
            "--confirmations",
        ],
    )
    def test_confirmations_flag_sets_env_var(self):
        """Test that --confirmations flag properly manages environment variables"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--confirmations", action="store_true")

        args = parser.parse_args(["--target", "test.com", "--objective", "test", "--confirmations"])

        # Simulate the environment variable logic from main()
        if not args.confirmations:
            os.environ["BYPASS_TOOL_CONSENT"] = "true"
        else:
            os.environ.pop("BYPASS_TOOL_CONSENT", None)

        # With --confirmations, the env var should not be set
        assert "BYPASS_TOOL_CONSENT" not in os.environ

    @patch.dict(os.environ, {}, clear=True)
    @patch("sys.argv", ["cyberautoagent.py", "--target", "test.com", "--objective", "test"])
    def test_no_confirmations_flag_sets_env_var(self):
        """Test that without --confirmations flag, environment variable is set"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--confirmations", action="store_true")

        args = parser.parse_args(["--target", "test.com", "--objective", "test"])

        # Simulate the environment variable logic from main()
        if not args.confirmations:
            os.environ["BYPASS_TOOL_CONSENT"] = "true"
        else:
            os.environ.pop("BYPASS_TOOL_CONSENT", None)

        # Without --confirmations, the env var should be set
        assert os.environ["BYPASS_TOOL_CONSENT"] == "true"



def test_cli_helpers_signal_and_workspace_markers(monkeypatch, tmp_path):
    logs = []
    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: False)
    monkeypatch.setattr(cyberautoagent.requests, "get", Mock(return_value=SimpleNamespace(status_code=200)))
    assert cyberautoagent.detect_deployment_mode() == "compose"

    logger = SimpleNamespace(info=lambda *args: logs.append(args), debug=lambda *args: logs.append(args))
    monkeypatch.setattr(cyberautoagent, "detect_deployment_mode", lambda: "cli")
    monkeypatch.setattr(cyberautoagent, "StrandsTelemetry", lambda: SimpleNamespace(setup_otlp_exporter=Mock()))
    telemetry = cyberautoagent.setup_telemetry(logger)
    assert telemetry is not None

    cyberautoagent.setup_langfuse_connection(logger, "cli")
    assert cyberautoagent.os.environ["OTEL_SERVICE_NAME"] == "cyber-autoagent"

    monkeypatch.setattr(cyberautoagent.traceback, "extract_stack", lambda: [SimpleNamespace(filename="swarm.py", name="run")])
    monkeypatch.setattr(cyberautoagent.threading, "Thread", lambda **_kwargs: SimpleNamespace(start=Mock()))
    with pytest.raises(KeyboardInterrupt):
        cyberautoagent.signal_handler(signal.SIGINT, None)


def test_recovery_guidance_with_failed_command_help_uses_catalog_context(monkeypatch):
    recovery_hook = SimpleNamespace(
        unresolved=True,
        failed_executable="feroxbuster",
        recovery_guidance=Mock(return_value="guidance with help"),
    )
    catalog_context = Mock(return_value="command: feroxbuster\nUsage: feroxbuster --help")
    monkeypatch.setattr(cyberautoagent, "get_shell_command_help_context", catalog_context)

    guidance = cyberautoagent._recovery_guidance_with_failed_command_help(recovery_hook, ["feroxbuster"])

    catalog_context.assert_called_once_with("feroxbuster", ["feroxbuster"])
    recovery_hook.recovery_guidance.assert_called_once_with("command: feroxbuster\nUsage: feroxbuster --help")
    assert guidance == "guidance with help"


def test_recovery_guidance_with_failed_command_help_omits_resolved_hook(monkeypatch):
    recovery_hook = SimpleNamespace(unresolved=False, failed_executable="feroxbuster")
    catalog_context = Mock()
    monkeypatch.setattr(cyberautoagent, "get_shell_command_help_context", catalog_context)

    assert cyberautoagent._recovery_guidance_with_failed_command_help(recovery_hook, ["feroxbuster"]) == ""
    catalog_context.assert_not_called()


def test_cli_deployment_telemetry_and_signal_variants(monkeypatch):
    logger = SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock())

    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: True)
    monkeypatch.setattr(cyberautoagent.requests, "get", Mock(side_effect=RuntimeError("down")))
    assert cyberautoagent.detect_deployment_mode() == "container"

    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: False)
    assert cyberautoagent.detect_deployment_mode() == "cli"

    telemetry = SimpleNamespace(setup_otlp_exporter=Mock())
    monkeypatch.setattr(cyberautoagent, "StrandsTelemetry", lambda: telemetry)
    monkeypatch.setattr(cyberautoagent, "detect_deployment_mode", lambda: "compose")
    monkeypatch.setattr(cyberautoagent, "is_langfuse_available", lambda: True)
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("CYBER_UI_MODE", "cli")
    assert cyberautoagent.setup_telemetry(logger) is telemetry
    telemetry.setup_otlp_exporter.assert_called_once()

    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: True)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    cyberautoagent.setup_langfuse_connection(logger, "container")
    assert cyberautoagent.os.environ["OTEL_EXPORTER_OTLP_HEADERS"].startswith("Authorization=Basic")

    monkeypatch.setattr(cyberautoagent.traceback, "extract_stack", lambda: [SimpleNamespace(filename="main.py", name="main")])
    thread = SimpleNamespace(start=Mock())
    monkeypatch.setattr(cyberautoagent.threading, "Thread", Mock(return_value=thread))
    with pytest.raises(KeyboardInterrupt):
        cyberautoagent.signal_handler(signal.SIGTERM, None)
    thread.start.assert_not_called()

    with pytest.raises(KeyboardInterrupt):
        cyberautoagent.signal_handler(999, None)


def test_setup_telemetry_falls_back_when_langfuse_is_unavailable(monkeypatch):
    logger = SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock())
    telemetry = SimpleNamespace(setup_otlp_exporter=Mock())
    monkeypatch.setattr(cyberautoagent, "StrandsTelemetry", lambda: telemetry)
    monkeypatch.setattr(cyberautoagent, "detect_deployment_mode", lambda: "compose")
    monkeypatch.setattr(cyberautoagent, "is_langfuse_available", lambda: False)
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("CYBER_UI_MODE", "cli")

    assert cyberautoagent.setup_telemetry(logger) is telemetry

    telemetry.setup_otlp_exporter.assert_not_called()
    logger.warning.assert_called_once_with(
        "Langfuse is unavailable; continuing with local telemetry only"
    )


def test_setup_telemetry_falls_back_when_exporter_setup_fails(monkeypatch):
    logger = SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock())
    error = RuntimeError("missing exporter")
    telemetry = SimpleNamespace(setup_otlp_exporter=Mock(side_effect=error))
    monkeypatch.setattr(cyberautoagent, "StrandsTelemetry", lambda: telemetry)
    monkeypatch.setattr(cyberautoagent, "detect_deployment_mode", lambda: "compose")
    monkeypatch.setattr(cyberautoagent, "is_langfuse_available", lambda: True)
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("CYBER_UI_MODE", "cli")

    assert cyberautoagent.setup_telemetry(logger) is telemetry

    logger.warning.assert_called_once_with(
        "Unable to configure OTLP exporter; continuing with local telemetry only: %s",
        error,
    )


def test_setup_telemetry_react_mode_observability_toggle(monkeypatch):
    logger = SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock())
    telemetry = SimpleNamespace(setup_otlp_exporter=Mock())
    monkeypatch.setattr(cyberautoagent, "StrandsTelemetry", lambda: telemetry)
    monkeypatch.setattr(cyberautoagent, "detect_deployment_mode", lambda: "cli")
    monkeypatch.setattr(cyberautoagent, "is_langfuse_available", lambda: True)
    monkeypatch.setattr(cyberautoagent, "setup_langfuse_connection", Mock())
    monkeypatch.setenv("CYBER_UI_MODE", "react")
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "false")
    cyberautoagent.setup_telemetry(logger)
    telemetry.setup_otlp_exporter.assert_not_called()

    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    cyberautoagent.setup_telemetry(logger)
    telemetry.setup_otlp_exporter.assert_called_once()


def test_cli_main_runs_mocked_react_operation(monkeypatch, tmp_path):
    class Provider:
        value = "ollama"

    server_config = SimpleNamespace(
        memory=SimpleNamespace(llm=SimpleNamespace(provider=Provider(), model_id="mem-llm")),
        embedding=SimpleNamespace(model_id="embed"),
        output=SimpleNamespace(base_dir=str(tmp_path)),
        llm=SimpleNamespace(model_id="llama", temperature=0.1, max_tokens=128, top_p=None),
    )
    config_manager = SimpleNamespace(
        get_server_config=Mock(return_value=server_config),
        get_mcp_config=Mock(return_value=SimpleNamespace(enabled=False, connections=[])),
        get_provider=Mock(return_value="ollama"),
        get_default_region=Mock(return_value="us-west-2"),
    )

    class FakeCallback:
        def __init__(self):
            self.tool_counts = {}
            self._emitted_any_reasoning = False
            self.termination_reason = None

        def process_metrics(self, metrics):
            self.metrics = metrics.accumulated_usage

        def should_stop(self):
            return hasattr(self, "metrics")

        def has_reached_limit(self):
            return False

        def get_summary(self):
            return {
                "duration": "1s",
                "tools_created": 0,
                "evidence_collected": 0,
                "memory_operations": 0,
                "capability_expansion": [],
            }

        def ensure_report_generated(self, *_args, **_kwargs):
            self.report_generated = True

        def trigger_evaluation_on_completion(self):
            self.evaluation_triggered = True

        def emit_termination(self, reason, message):
            self.termination_reason = reason
            self.termination_message = message

    callback = FakeCallback()

    class FakeAgent:
        def __init__(self):
            self.messages = []
            self.model = SimpleNamespace()
            self.cleanup = Mock()

        def __call__(self, message):
            self.last_message = message
            return SimpleNamespace(
                metrics=SimpleNamespace(accumulated_usage={"inputTokens": 1, "outputTokens": 2})
            )

    fake_agent = FakeAgent()

    monkeypatch.setenv("CYBER_UI_MODE", "react")
    monkeypatch.setenv("CYBERAGENT_NO_BANNER", "1")
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"])
    monkeypatch.setattr(cyberautoagent.signal, "signal", Mock())
    monkeypatch.setattr(cyberautoagent, "ensure_workspace_marker_files", Mock())
    monkeypatch.setattr(cyberautoagent, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(cyberautoagent, "get_output_path", lambda target, op_id, subdir, base_dir: str(tmp_path / target / op_id))
    monkeypatch.setattr(cyberautoagent, "setup_logging", lambda **_kwargs: SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock(), exception=Mock(), error=Mock()))
    monkeypatch.setattr(cyberautoagent, "setup_telemetry", Mock(return_value=SimpleNamespace()))
    monkeypatch.setattr("modules.config.system.logger.configure_sdk_logging", Mock())
    monkeypatch.setattr(cyberautoagent.atexit, "register", Mock())
    monkeypatch.setattr(cyberautoagent, "configure_model_rate_limits", Mock())
    monkeypatch.setattr(cyberautoagent, "auto_setup", Mock(return_value=["shell"]))
    workflow = Mock()
    workflow_controller = Mock(return_value=workflow)
    monkeypatch.setattr(cyberautoagent, "MultiAgentWorkflowController", workflow_controller)
    monkeypatch.setattr(cyberautoagent, "create_agent_runtime_resources", Mock(return_value=SimpleNamespace(callback_handler=callback)))
    monkeypatch.setattr(cyberautoagent, "create_agent", Mock(return_value=fake_agent))
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())
    monkeypatch.setattr(cyberautoagent.browser, "close_browser", Mock())
    monkeypatch.setattr(cyberautoagent, "channel_close_all", AsyncMock(return_value={"closed": 0}))
    monkeypatch.setattr(cyberautoagent, "close_oast_providers", AsyncMock(return_value=None))
    monkeypatch.setattr(cyberautoagent, "flush_traces", Mock())
    monkeypatch.setattr(cyberautoagent, "get_model_timeout", Mock(return_value=300))
    monkeypatch.setattr(cyberautoagent, "is_docker", Mock(return_value=False))
    monkeypatch.setattr(cyberautoagent, "interrupted", False)

    cyberautoagent.main()

    workflow.run.assert_called_once()
    assert callback.report_generated is True
    assert os.environ["CYBER_OPERATION_ID"].startswith("OP_")


def _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback):
    class Provider:
        value = "ollama"

    server_config = SimpleNamespace(
        memory=SimpleNamespace(llm=SimpleNamespace(provider=Provider(), model_id="mem-llm")),
        embedding=SimpleNamespace(model_id="embed"),
        output=SimpleNamespace(base_dir=str(tmp_path)),
        llm=SimpleNamespace(model_id="llama", temperature=0.1, max_tokens=128, top_p=None),
    )
    config_manager = SimpleNamespace(
        get_server_config=Mock(return_value=server_config),
        get_mcp_config=Mock(return_value=SimpleNamespace(enabled=False, connections=[])),
        get_provider=Mock(return_value="ollama"),
        get_default_region=Mock(return_value="us-west-2"),
    )
    monkeypatch.setenv("CYBER_UI_MODE", "react")
    monkeypatch.setenv("CYBERAGENT_NO_BANNER", "1")
    monkeypatch.setattr(cyberautoagent.signal, "signal", Mock())
    monkeypatch.setattr(cyberautoagent, "ensure_workspace_marker_files", Mock())
    monkeypatch.setattr(cyberautoagent, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(cyberautoagent, "get_output_path", lambda target, op_id, subdir, base_dir=None: str(tmp_path / target / op_id))
    monkeypatch.setattr(cyberautoagent, "setup_logging", lambda **_kwargs: SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock(), exception=Mock(), error=Mock()))
    monkeypatch.setattr(cyberautoagent, "setup_telemetry", Mock(return_value=SimpleNamespace()))
    monkeypatch.setattr("modules.config.system.logger.configure_sdk_logging", Mock())
    monkeypatch.setattr(cyberautoagent.atexit, "register", Mock())
    monkeypatch.setattr(cyberautoagent, "configure_model_rate_limits", Mock())
    monkeypatch.setattr(cyberautoagent, "auto_setup", Mock(return_value=["shell"]))
    workflow = Mock()
    workflow_controller = Mock(return_value=workflow)
    monkeypatch.setattr(cyberautoagent, "MultiAgentWorkflowController", workflow_controller)
    monkeypatch.setattr(cyberautoagent, "create_agent_runtime_resources", Mock(return_value=SimpleNamespace(callback_handler=callback)))
    monkeypatch.setattr(cyberautoagent, "create_agent", Mock(return_value=fake_agent))
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())
    monkeypatch.setattr(cyberautoagent.browser, "close_browser", Mock())
    monkeypatch.setattr(cyberautoagent, "channel_close_all", AsyncMock(return_value={"closed": 0}))
    monkeypatch.setattr(cyberautoagent, "close_oast_providers", AsyncMock(return_value=None))
    monkeypatch.setattr(cyberautoagent, "flush_traces", Mock())
    monkeypatch.setattr(cyberautoagent, "get_model_timeout", Mock(return_value=300))
    monkeypatch.setattr(cyberautoagent, "is_docker", Mock(return_value=False))
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    config_manager.workflow = workflow
    config_manager.workflow_controller = workflow_controller
    return config_manager


class CliCallback:
    def __init__(self):
        # Removed step-based control in budget-only model
        self.tool_counts = {}
        self.pending_progress_update = False
        self._emitted_any_reasoning = False
        self.termination_reason = None
        self.emit_model_usage_snapshot = Mock()
        self.ensure_report_generated = Mock()
        self.trigger_evaluation_on_completion = Mock()
        self.emit_assessment_complete = Mock()
        self.emit_termination = Mock()

    def process_metrics(self, _metrics):
        pass

    def should_stop(self):
        return True

    def has_reached_limit(self):
        return False

    def get_summary(self):
        return {
            "duration": "1s",
            "tools_created": 0,
            "evidence_collected": 0,
            "memory_operations": 0,
            "capability_expansion": [],
        }


class CallableCliAgent:
    def __init__(self):
        self.messages = []
        self.model = SimpleNamespace()
        self.cleanup = Mock()

    def __call__(self, _message):
        return SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={"inputTokens": 1}))


def _run_agent_helper(agent, callback, logger=None, **kwargs):
    return cyberautoagent.run_agent_until_terminal_state(
        agent=agent,
        callback_handler=callback,
        current_message=kwargs.pop("current_message", "initial"),
        initial_prompt=kwargs.pop("initial_prompt", "initial"),
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=60),
        operation_start=kwargs.pop("operation_start", cyberautoagent.time.time()),
        max_duration=kwargs.pop("max_duration", 60),
        logger=logger or SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock()),
        **kwargs,
    )


def test_run_agent_until_terminal_state_retries_recoverable_error(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)
    callback.has_reached_limit = Mock(return_value=False)
    callback.process_metrics = Mock()
    logger = SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock())

    class RetryAgent:
        def __init__(self):
            self.messages = []
            self.calls = 0

        def __call__(self, _message):
            self.calls += 1
            if self.calls == 1:
                raise cyberautoagent.RequestsReadTimeout("read timed out")
            callback.should_stop.return_value = True
            return SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={"inputTokens": 1}))

    agent = RetryAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent.time, "sleep", Mock())
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = cyberautoagent.run_agent_until_terminal_state(
        agent=agent,
        callback_handler=callback,
        current_message="initial",
        initial_prompt="initial",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=60),
        operation_start=cyberautoagent.time.time(),
        max_duration=60,
        logger=logger,
    )

    assert result.reason == "callback_stop"
    assert agent.calls == 2
    callback.process_metrics.assert_called_once()


def test_run_agent_until_terminal_state_callback_budget_limit(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=True)
    callback.has_reached_limit = Mock(return_value=True)
    agent = CallableCliAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    with pytest.raises(cyberautoagent.BudgetLimitReached):
        _run_agent_helper(agent, callback)


def test_run_agent_until_terminal_state_no_actions(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)

    class QuietAgent:
        messages = []

        def __call__(self, _message):
            return SimpleNamespace(metrics=None)

    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(QuietAgent(), callback)

    assert result.reason == "no_actions"


def test_retained_executor_cycle_stalls_with_sticky_reasoning_and_no_new_tools(monkeypatch):
    root_callback = CliCallback()
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)
    role_callback._emitted_any_reasoning = True
    role_callback.tool_counts = {"shell": 12}

    class TextOnlyAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            return SimpleNamespace(metrics=None)

    agent = TextOnlyAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(
            min_tool_calls=1,
            required_tool_names={"record_task_acceptance"},
            terminal_after_required_tools=True,
            require_successful_required_tools=True,
            allow_text_final_after_tools=False,
            actionless_mode="task_progress",
            max_actionless_calls=3,
        ),
    )

    assert result.reason == "stalled"
    assert result.message == "No actions taken after 3 attempts"
    assert len(agent.calls) == 3
    assert "Call the next registered tool needed" in agent.calls[1]
    assert "required completion tool(s) (record_task_acceptance) as completion conditions" in agent.calls[1]
    assert "only after their prerequisite work and evidence are complete" in agent.calls[1]
    assert "call the next registered tool" in agent.calls[2]
    assert "required completion tool(s) (record_task_acceptance) only when their prerequisites" in agent.calls[2]
    assert "final answer" not in agent.calls[2]
    role_callback.emit_termination.assert_called_once_with("stalled", result.message)


def test_run_agent_until_terminal_state_has_absolute_agent_call_bound(monkeypatch):
    root_callback = CliCallback()
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)

    class AlwaysActingAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            role_callback.tool_counts["shell"] = len(self.calls)
            return SimpleNamespace(metrics=None)

    agent = AlwaysActingAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(max_agent_calls=4),
    )

    assert result.reason == "stalled"
    assert result.message == "Stopped after 4 agent calls without reaching the role contract"
    assert len(agent.calls) == 4
    role_callback.emit_termination.assert_called_once_with("stalled", result.message)


def test_run_agent_until_terminal_state_passes_sdk_turn_limit(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)

    class LimitedAgent:
        def __init__(self):
            self.messages = []
            self.calls = []

        def __call__(self, message, **kwargs):
            self.calls.append((message, kwargs))
            return SimpleNamespace(metrics=None, stop_reason="limit_turns")

    agent = LimitedAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        callback,
        run_policy=cyberautoagent.AgentRunPolicy(max_model_turns=7),
    )

    assert result.reason == "stalled"
    assert result.message == "Agent stopped at its configured SDK limit: limit_turns"
    assert agent.calls == [("initial", {"limits": {"turns": 7}})]
    callback.emit_termination.assert_called_once_with("stalled", result.message)


def test_run_agent_until_terminal_state_uses_agent_callback_for_role_tool_calls(monkeypatch):
    root_callback = CliCallback()
    root_callback.should_stop = Mock(return_value=False)
    role_callback = CliCallback()
    role_callback.should_stop = Mock(side_effect=[False, True])
    role_callback.process_metrics = Mock()

    class TaskCreatorAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            role_callback.tool_counts["create_tasks"] = role_callback.tool_counts.get("create_tasks", 0) + 1
            return SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={"inputTokens": 1}))

    agent = TaskCreatorAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(agent, root_callback)

    assert result.reason == "callback_stop"
    assert agent.calls == ["initial", ""]
    assert all("MANDATORY ACTION" not in message for message in agent.calls)
    assert root_callback.tool_counts == {}
    assert role_callback.tool_counts == {"create_tasks": 2}
    role_callback.process_metrics.assert_called()


def test_run_agent_until_terminal_state_policy_stops_after_required_tool(monkeypatch):
    root_callback = CliCallback()
    root_callback.should_stop = Mock(return_value=False)
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)
    logger = SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock())

    class PolicyAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            if len(self.calls) == 1:
                role_callback.tool_counts["required_tool"] = 1
            return SimpleNamespace(metrics=None)

    agent = PolicyAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        logger=logger,
        run_policy=cyberautoagent.AgentRunPolicy(
            required_tool_names={"required_tool"},
            terminal_after_required_tools=True,
            terminal_reason="required_tool_done",
            terminal_message="Required tool completed",
        ),
    )

    assert result.reason == "required_tool_done"
    assert result.message == "Required tool completed"
    assert agent.calls == ["initial", ""]
    assert root_callback.tool_counts == {}
    assert role_callback.tool_counts == {"required_tool": 1}
    assert all("MANDATORY ACTION" not in message for message in agent.calls)
    assert not logger.warning.called


def test_run_agent_until_terminal_state_policy_stops_at_max_tool_calls(monkeypatch):
    root_callback = CliCallback()
    root_callback.should_stop = Mock(return_value=False)
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)
    logger = SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock())

    class MaxToolAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            role_callback.tool_counts["shell"] = len(self.calls)
            return SimpleNamespace(metrics=None)

    agent = MaxToolAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        logger=logger,
        run_policy=cyberautoagent.AgentRunPolicy(
            max_tool_calls=1,
            terminal_reason="max_tools",
            terminal_message="Stopped after max tools",
        ),
    )

    assert result.reason == "max_tools"
    assert result.message == "Stopped after max tools"
    assert agent.calls == ["initial"]


def test_run_agent_until_terminal_state_stops_repeated_tool_loop_without_error(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)
    logger = SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock())

    class RepeatedToolAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = callback

        def __call__(self, message):
            self.calls.append(message)
            callback.tool_counts["shell"] = 4
            return SimpleNamespace(
                metrics=None,
                state={
                    "repeated_tool_loop": {
                        "repeat_count": 4,
                        "tool_name": "shell",
                    }
                },
            )

    agent = RepeatedToolAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(agent, callback, logger=logger)

    assert result.reason == "repeated_tool_loop"
    assert "latest completed result was reused" in result.message
    assert agent.calls == ["initial"]
    assert logger.warning.called


def test_run_agent_until_terminal_state_describes_uncacheable_repeated_tool_loop(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)
    logger = SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock())

    class RepeatedToolAgent:
        def __init__(self):
            self.messages = []
            self._cyber_callback_handler = callback

        def __call__(self, message):
            del message
            return SimpleNamespace(
                metrics=None,
                state={
                    "repeated_tool_loop": {
                        "repeat_count": 3,
                        "tool_name": "shell",
                        "result_reused": False,
                    }
                },
            )

    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(RepeatedToolAgent(), callback, logger=logger)

    assert result.reason == "repeated_tool_loop"
    assert "no reusable completed result was available" in result.message


def test_run_agent_until_terminal_state_describes_multi_call_tool_cycle(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)
    logger = SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock())

    class RepeatedToolAgent:
        def __init__(self):
            self.messages = []
            self._cyber_callback_handler = callback

        def __call__(self, message):
            del message
            callback.tool_counts["shell"] = 7
            return SimpleNamespace(
                metrics=None,
                state={
                    "repeated_tool_loop": {
                        "cycle_length": 2,
                        "repeat_count": 3,
                        "tool_name": "shell",
                        "tool_names": ["shell", "shell"],
                    }
                },
            )

    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(RepeatedToolAgent(), callback, logger=logger)

    assert result.reason == "repeated_tool_loop"
    assert result.message == (
        "Stopped agent after 3 repetitions of a 2-call tool cycle involving shell; "
        "matching completed results were reused."
    )


def test_run_agent_policy_can_stop_immediately_after_required_tool(monkeypatch):
    root_callback = CliCallback()
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)

    class PolicyAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            role_callback.tool_counts["create_tasks"] = 1
            return SimpleNamespace(metrics=None)

    agent = PolicyAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(
            required_tool_names={"create_tasks"},
            terminal_after_required_tools=True,
            allow_text_final_after_tools=False,
            terminal_reason="task_creator_done",
        ),
    )

    assert result.reason == "task_creator_done"
    assert agent.calls == ["initial"]


def test_run_agent_policy_requires_success_marker_when_configured(monkeypatch):
    root_callback = CliCallback()
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)

    class PolicyAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            role_callback.tool_counts["record_task_acceptance"] = len(self.calls)
            state = {}
            if len(self.calls) == 2:
                state["terminal_tool_completed"] = {"tool_name": "record_task_acceptance"}
            return SimpleNamespace(metrics=None, state=state)

    agent = PolicyAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(
            required_tool_names={"record_task_acceptance"},
            terminal_after_required_tools=True,
            require_successful_required_tools=True,
            allow_text_final_after_tools=False,
            terminal_reason="task_executor_done",
        ),
    )

    assert result.reason == "task_executor_done"
    assert len(agent.calls) == 2


def test_run_agent_policy_waits_for_all_successful_required_tool_outcomes(monkeypatch):
    root_callback = CliCallback()
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)
    role_callback.tool_outcome_journal = ToolOutcomeJournal()

    class PolicyAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            tool_name = "record_task_acceptance" if len(self.calls) == 1 else "record_finding_validation"
            role_callback.tool_counts[tool_name] = 1
            role_callback.tool_outcome_journal.append(
                tool_use_id=f"tool-{len(self.calls)}",
                tool_name=tool_name,
                success=True,
                correctable=False,
                tool_input={},
                output="complete",
            )
            return SimpleNamespace(metrics=None, state={})

    agent = PolicyAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(
            required_tool_names={"record_task_acceptance", "record_finding_validation"},
            terminal_after_required_tools=True,
            require_successful_required_tools=True,
            allow_text_final_after_tools=False,
            terminal_reason="task_executor_done",
        ),
    )

    assert result.reason == "task_executor_done"
    assert len(agent.calls) == 2


def test_run_agent_policy_requires_new_tool_calls_for_reused_agent_pass(monkeypatch):
    root_callback = CliCallback()
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)
    role_callback.tool_counts = {"shell": 1}

    class ReusedAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            if len(self.calls) == 2:
                role_callback.tool_counts["shell"] += 1
            return SimpleNamespace(metrics=None)

    agent = ReusedAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(
            min_tool_calls=1,
            terminal_after_required_tools=True,
        ),
    )

    assert result.reason == "agent_completed_required_tools"
    assert len(agent.calls) == 3
    assert role_callback.tool_counts == {"shell": 2}
    assert "MANDATORY ACTION" in agent.calls[1]


def test_run_agent_returns_rejected_required_tool_error(monkeypatch):
    root_callback = CliCallback()
    role_callback = CliCallback()
    role_callback.should_stop = Mock(return_value=False)

    class ExhaustedAgent:
        def __init__(self):
            self.messages = []
            self._cyber_callback_handler = role_callback

        def __call__(self, _message):
            return SimpleNamespace(
                metrics=None,
                state={
                    "terminal_tool_rejected": {
                        "tool_name": "create_tasks",
                        "error": "limits field required",
                    }
                },
            )

    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        ExhaustedAgent(),
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(required_tool_names={"create_tasks"}),
    )

    assert result.reason == "required_tool_rejected"
    assert result.message == "limits field required"


def test_agent_run_policy_rejects_unknown_actionless_mode():
    with pytest.raises(ValueError, match="actionless_mode must be"):
        cyberautoagent.AgentRunPolicy(actionless_mode="guess")


def test_extract_last_assistant_text_skips_tool_use_messages():
    messages = [
        {"role": "assistant", "content": [{"text": "older"}]},
        {"role": "assistant", "content": [{"toolUse": {"name": "shell"}}, {"text": "tool turn"}]},
        {"role": "user", "content": [{"text": "tool result"}]},
        {"role": "assistant", "content": [{"text": "final worker summary"}]},
    ]

    assert cyberautoagent.extract_last_assistant_text(messages) == "final worker summary"
    assert cyberautoagent.extract_last_assistant_text([messages[1]]) == ""
    assert cyberautoagent.extract_last_assistant_text(None) == ""


def test_objective_placeholder_and_assistant_text_edge_cases():
    assert cyberautoagent._is_continuation_objective_placeholder("", "web_scan") is True
    assert cyberautoagent._is_continuation_objective_placeholder(" perform web scan assessment ", "web_scan") is True
    assert cyberautoagent._is_continuation_objective_placeholder(
        "comprehensive web scan security assessment", "web_scan"
    ) is True
    assert cyberautoagent._is_continuation_objective_placeholder("custom objective", "web_scan") is False

    messages = [
        {"role": "assistant", "content": "not a list"},
        {"role": "assistant", "content": [{"tool_use": {"name": "shell"}}]},
        {"role": "assistant", "content": [{"text": ""}, {"text": " final "}]},
    ]
    assert cyberautoagent.extract_last_assistant_text(messages) == "final"
    assert cyberautoagent.extract_last_assistant_text(
        [{"role": "assistant", "content": [{"image": "data"}]}]
    ) == ""
    assert cyberautoagent.extract_last_assistant_text(
        [{"role": "user", "content": [{"text": "ignored"}]}]
    ) == ""


def test_run_agent_until_terminal_state_policy_requires_all_tools(monkeypatch):
    root_callback = CliCallback()
    root_callback.should_stop = Mock(return_value=False)
    role_callback = CliCallback()
    role_callback.should_stop = Mock(side_effect=[False, False, True])

    class PartialPolicyAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            if len(self.calls) == 1:
                role_callback.tool_counts["first_tool"] = 1
            return SimpleNamespace(metrics=None)

    agent = PartialPolicyAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(
            required_tool_names={"first_tool", "second_tool"},
            terminal_after_required_tools=True,
        ),
    )

    assert result.reason == "callback_stop"
    assert role_callback.tool_counts == {"first_tool": 1}
    assert any("MANDATORY ACTION" in message for message in agent.calls)


def test_run_agent_until_terminal_state_policy_ignores_configured_tools(monkeypatch):
    root_callback = CliCallback()
    root_callback.should_stop = Mock(return_value=False)
    role_callback = CliCallback()
    role_callback.should_stop = Mock(side_effect=[False, False, True])

    class CaptureOnlyAgent:
        def __init__(self):
            self.messages = []
            self.calls = []
            self._cyber_callback_handler = role_callback

        def __call__(self, message):
            self.calls.append(message)
            if len(self.calls) == 1:
                role_callback.tool_counts["create_tasks"] = 1
            return SimpleNamespace(metrics=None)

    agent = CaptureOnlyAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(
        agent,
        root_callback,
        run_policy=cyberautoagent.AgentRunPolicy(
            min_tool_calls=1,
            terminal_after_required_tools=True,
            ignored_terminal_tool_names={"create_tasks"},
        ),
    )

    assert result.reason == "callback_stop"
    assert role_callback.tool_counts == {"create_tasks": 1}
    assert any("MANDATORY ACTION" in message for message in agent.calls)


def test_run_agent_until_terminal_state_duration_budget(monkeypatch):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)
    callback._emitted_any_reasoning = True
    agent = CallableCliAgent()
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    with pytest.raises(cyberautoagent.BudgetLimitReached):
        _run_agent_helper(agent, callback, operation_start=cyberautoagent.time.time() - 120, max_duration=1)


def test_run_agent_until_terminal_state_recoverable_error_exhausted(monkeypatch):
    callback = CliCallback()

    class TimeoutAgent:
        messages = []

        def __call__(self, _message):
            raise cyberautoagent.RequestsReadTimeout("read timed out")

    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    result = _run_agent_helper(TimeoutAgent(), callback, recoverable_retries=0)

    assert result.reason == "network_timeout"
    callback.emit_termination.assert_called_once()


def test_run_agent_until_terminal_state_event_loop_stop_is_not_swallowed(monkeypatch):
    callback = CliCallback()

    class EventStopAgent:
        messages = []

        def __call__(self, _message):
            raise RuntimeError("event loop cycle stop requested\nReason: done")

    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    with pytest.raises(RuntimeError, match="event loop cycle stop requested"):
        _run_agent_helper(EventStopAgent(), callback)


def test_run_agent_until_terminal_state_propagates_max_tokens(monkeypatch):
    callback = CliCallback()
    logger = SimpleNamespace(debug=Mock(), warning=Mock(), info=Mock())

    class TokenAgent:
        messages = []

        def __call__(self, _message):
            raise MaxTokensReachedException("max_tokens")

    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())

    with pytest.raises(MaxTokensReachedException):
        cyberautoagent.run_agent_until_terminal_state(
            agent=TokenAgent(),
            callback_handler=callback,
            current_message="initial",
            initial_prompt="initial",
            budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=60),
            operation_start=cyberautoagent.time.time(),
            max_duration=60,
            logger=logger,
        )


def test_workflow_task_executor_recovers_once_from_reasoning_loop(monkeypatch):
    journal = ToolOutcomeJournal()
    journal.append(
        tool_use_id="tool-1",
        tool_name="shell",
        success=True,
        correctable=False,
        tool_input={"command": "curl target"},
        output="200 OK",
    )
    callback = SimpleNamespace(tool_outcome_journal=journal, record_max_token_exhaustion=Mock())
    agent = SimpleNamespace(
        _cyber_agent_type="task_executor",
        _cyber_callback_handler=callback,
        model=SimpleNamespace(_output_tokens=6000),
        messages=[{"role": "user", "content": [{"text": "assigned task"}]}],
    )
    calls = []
    policies = []
    repeated = "I should repeat this analysis rather than call the required tool.\n" * 60

    def run_agent(**kwargs):
        calls.append(kwargs["current_message"])
        policies.append(kwargs["run_policy"])
        if len(calls) == 1:
            agent.messages.append({"role": "assistant", "content": [{"text": repeated}]})
            raise MaxTokensReachedException("max_tokens")
        return cyberautoagent.AgentRunResult("task_executor_done", "done")

    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", run_agent)
    logger = SimpleNamespace(warning=Mock())

    result = cyberautoagent.run_workflow_agent_with_max_token_recovery(
        agent=agent,
        prompt="original task prompt",
        run_policy=cyberautoagent.AgentRunPolicy(
            required_tool_names={"record_task_acceptance"},
            terminal_after_required_tools=True,
            recovery_objective="Map authentication behavior for the assigned /login route.",
            recovery_next_action="Call record_task_acceptance with canonical durable evidence references.",
        ),
        callback_handler=callback,
        initial_prompt="initial",
        budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=60),
        operation_start=cyberautoagent.time.time(),
        max_duration=60,
        logger=logger,
    )

    assert result.reason == "task_executor_done"
    assert len(calls) == 2
    assert "repetitive reasoning was detected" in calls[1]
    assert "Task objective: Map authentication behavior for the assigned /login route." in calls[1]
    assert "Latest tool outcome: shell: 200 OK" in calls[1]
    assert "Next required action: Call record_task_acceptance" in calls[1]
    assert repeated not in calls[1]
    assert "Controller-observed successful outcomes" not in calls[1]
    assert "Do not repeat any tool call already represented above" not in calls[1]
    assert policies[1].max_agent_calls == 1
    assert policies[1].max_model_turns == 1
    assert policies[1].max_tool_calls == 1
    assert agent.messages == []
    callback.record_max_token_exhaustion.assert_called_once_with(
        role="task_executor",
        classification="reasoning_loop",
        exhaustion_ordinal=1,
        agent=agent,
    )


def test_workflow_task_executor_propagates_second_max_tokens(monkeypatch):
    callback = SimpleNamespace(tool_outcome_journal=ToolOutcomeJournal(), record_max_token_exhaustion=Mock())
    agent = SimpleNamespace(
        _cyber_agent_type="task_executor",
        _cyber_callback_handler=callback,
        model=SimpleNamespace(_output_tokens=6000),
        messages=[],
    )
    calls = []
    repeated = "The same reasoning loop continues without a tool call.\n" * 60

    def run_agent(**kwargs):
        calls.append(kwargs["current_message"])
        agent.messages.append({"role": "assistant", "content": [{"text": repeated}]})
        raise MaxTokensReachedException("max_tokens")

    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", run_agent)

    with pytest.raises(MaxTokensReachedException) as exc_info:
        cyberautoagent.run_workflow_agent_with_max_token_recovery(
            agent=agent,
            prompt="original task prompt",
            run_policy=cyberautoagent.AgentRunPolicy(required_tool_names={"record_task_acceptance"}),
            callback_handler=callback,
            initial_prompt="initial",
            budget_cfg=cyberautoagent.BudgetConfig(max_duration_minutes=60),
            operation_start=cyberautoagent.time.time(),
            max_duration=60,
            logger=SimpleNamespace(warning=Mock()),
        )

    assert len(calls) == 2
    assert exc_info.value.max_token_classification.kind == "reasoning_loop"
    assert agent.messages == []
    assert callback.record_max_token_exhaustion.call_count == 2


def test_finalize_report_and_evaluation_runs_once(monkeypatch):
    callback = CliCallback()
    callback.termination_reason = "complete"
    monkeypatch.setattr(
        cyberautoagent,
        "get_memory_client",
        Mock(return_value=SimpleNamespace(get_active_plan=lambda: SimpleNamespace(assessment_complete=True))),
    )
    lifecycle = Mock()
    callback.emit_model_usage_snapshot = lifecycle.emit_model_usage_snapshot
    callback.ensure_report_generated = lifecycle.ensure_report_generated
    callback.trigger_evaluation_on_completion = lifecycle.trigger_evaluation_on_completion
    callback.emit_assessment_complete = lifecycle.emit_assessment_complete
    logger = SimpleNamespace(info=Mock(), warning=Mock())
    agent = SimpleNamespace(model=SimpleNamespace())
    monkeypatch.setenv("ENABLE_AUTO_EVALUATION", "true")
    monkeypatch.setattr(cyberautoagent, "get_model_timeout", Mock(return_value=450))

    cyberautoagent.finalize_report_and_evaluation(
        agent=agent,
        callback_handler=callback,
        target="example.com",
        objective="test",
        module="web",
        logger=logger,
    )

    callback.ensure_report_generated.assert_called_once()
    report_call = callback.ensure_report_generated.call_args
    assert report_call.args == (agent, "example.com", "test", "web")
    assert report_call.kwargs["completion_status"] == {
        "assessment_complete": True,
        "workflow_complete": True,
        "termination_reason": "complete",
        "termination_message": None,
        "incomplete_reason": None,
        "unresolved_task_count": None,
        "incomplete_phase_ids": [],
        "workflow_coverage_summary": [],
    }
    callback.trigger_evaluation_on_completion.assert_called_once()
    callback.emit_assessment_complete.assert_called_once()
    assert [call[0] for call in lifecycle.method_calls] == [
        "emit_model_usage_snapshot",
        "ensure_report_generated",
        "trigger_evaluation_on_completion",
        "emit_assessment_complete",
    ]


def test_finalize_report_and_evaluation_allows_missing_agent_for_report_mode(monkeypatch):
    callback = CliCallback()
    callback.termination_reason = "complete"
    monkeypatch.setattr(
        cyberautoagent,
        "get_memory_client",
        Mock(return_value=SimpleNamespace(get_active_plan=lambda: SimpleNamespace(assessment_complete=True))),
    )
    logger = SimpleNamespace(info=Mock(), warning=Mock())
    monkeypatch.setenv("ENABLE_AUTO_EVALUATION", "true")

    cyberautoagent.finalize_report_and_evaluation(
        agent=None,
        callback_handler=callback,
        target="example.com",
        objective="test",
        module="web",
        logger=logger,
    )

    callback.ensure_report_generated.assert_called_once()
    report_call = callback.ensure_report_generated.call_args
    assert report_call.args == (None, "example.com", "test", "web")
    assert report_call.kwargs["completion_status"]["assessment_complete"] is True
    callback.emit_assessment_complete.assert_called_once()


def test_finalize_report_and_evaluation_continues_when_model_usage_snapshot_fails(monkeypatch):
    callback = CliCallback()
    callback.termination_reason = "complete"
    snapshot_error = RuntimeError("snapshot unavailable")
    callback.emit_model_usage_snapshot.side_effect = snapshot_error
    monkeypatch.setattr(
        cyberautoagent,
        "get_memory_client",
        Mock(return_value=SimpleNamespace(get_active_plan=lambda: SimpleNamespace(assessment_complete=True))),
    )
    logger = SimpleNamespace(info=Mock(), warning=Mock())

    cyberautoagent.finalize_report_and_evaluation(
        agent=SimpleNamespace(model=SimpleNamespace()),
        callback_handler=callback,
        target="example.com",
        objective="test",
        module="web",
        logger=logger,
    )

    callback.ensure_report_generated.assert_called_once()
    callback.trigger_evaluation_on_completion.assert_called_once()
    logger.warning.assert_called_with(
        "Unable to persist model usage before report generation: %s", snapshot_error
    )


def test_finalize_report_and_evaluation_completes_when_evaluation_is_disabled(monkeypatch):
    callback = CliCallback()
    callback.termination_reason = "complete"
    monkeypatch.setattr(
        cyberautoagent,
        "get_memory_client",
        Mock(return_value=SimpleNamespace(get_active_plan=lambda: SimpleNamespace(assessment_complete=True))),
    )
    monkeypatch.setenv("ENABLE_AUTO_EVALUATION", "false")

    cyberautoagent.finalize_report_and_evaluation(
        agent=SimpleNamespace(model=SimpleNamespace()),
        callback_handler=callback,
        target="example.com",
        objective="test",
        module="web",
        logger=SimpleNamespace(info=Mock(), warning=Mock()),
    )

    callback.trigger_evaluation_on_completion.assert_called_once()
    callback.emit_assessment_complete.assert_called_once()


def test_finalize_report_and_evaluation_handles_missing_handler_and_errors(monkeypatch):
    logger = SimpleNamespace(info=Mock(), warning=Mock())

    cyberautoagent.finalize_report_and_evaluation(
        agent=SimpleNamespace(model=SimpleNamespace()),
        callback_handler=None,
        target="example.com",
        objective="test",
        module="web",
        logger=logger,
    )
    logger.warning.assert_called_with("No callback_handler available for evaluation trigger")

    callback = CliCallback()
    callback.ensure_report_generated.side_effect = RuntimeError("report failed")
    callback.termination_reason = "error"
    monkeypatch.setattr(
        cyberautoagent,
        "get_memory_client",
        Mock(return_value=SimpleNamespace(get_active_plan=lambda: SimpleNamespace(assessment_complete=False))),
    )
    cyberautoagent.finalize_report_and_evaluation(
        agent=SimpleNamespace(model=SimpleNamespace()),
        callback_handler=callback,
        target="example.com",
        objective="test",
        module="web",
        logger=logger,
    )

    assert logger.warning.call_args.args[0] == "Error in final report/evaluation: %s"
    report_call = callback.ensure_report_generated.call_args
    assert report_call.kwargs["completion_status"]["assessment_complete"] is False
    assert report_call.kwargs["completion_status"]["workflow_complete"] is False
    assert report_call.kwargs["completion_status"]["termination_reason"] == "error"
    callback.trigger_evaluation_on_completion.assert_not_called()
    callback.emit_assessment_complete.assert_not_called()

    callback = CliCallback()
    callback.emit_assessment_complete.side_effect = RuntimeError("event failed")
    callback.termination_reason = "complete"
    monkeypatch.setattr(
        cyberautoagent,
        "get_memory_client",
        Mock(return_value=SimpleNamespace(get_active_plan=lambda: SimpleNamespace(assessment_complete=True))),
    )
    logger = SimpleNamespace(info=Mock(), warning=Mock())
    cyberautoagent.finalize_report_and_evaluation(
        agent=SimpleNamespace(model=SimpleNamespace()),
        callback_handler=callback,
        target="example.com",
        objective="test",
        module="web",
        logger=logger,
    )

    logger.warning.assert_called_with(
        "Unable to emit operation finalization: %s",
        callback.emit_assessment_complete.side_effect,
    )


def test_cli_service_mode_idle_interrupt_returns(monkeypatch):
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--service-mode"])
    monkeypatch.setattr(cyberautoagent.signal, "signal", Mock())
    monkeypatch.setattr(cyberautoagent, "ensure_workspace_marker_files", Mock())
    monkeypatch.setattr(cyberautoagent.time, "sleep", Mock(side_effect=KeyboardInterrupt))

    cyberautoagent.main()

    assert cyberautoagent.ensure_workspace_marker_files.call_count >= 1


def test_cli_main_report_mode_uses_latest_operation(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = SimpleNamespace(messages=[], model=SimpleNamespace(), cleanup=Mock())
    _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    monkeypatch.setenv("CYBER_BUG_BOUNTY_HEADERS", '{"X-Env":"yes"}')
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "report", "--max-duration", "60", "--provider", "ollama", "--report", "--bug-bounty-header", "X-Test=1"])
    monkeypatch.setattr(cyberautoagent, "get_default_base_dir", lambda: str(tmp_path))

    class DirEntry:
        def __init__(self, name):
            self.name = name

        def is_dir(self):
            return True

    from modules.tools.memory import create_application_store

    store = create_application_store(
        str(tmp_path / "cyber_autoagent.db"),
        logical_target="example.com",
    )
    store.ensure_operation("OP_20260102_000000")
    original_scandir = os.scandir
    operation_dir = os.path.join(str(tmp_path), cyberautoagent.sanitize_target_name("example.com"))

    def scandir(path):
        if path == operation_dir:
            return [DirEntry("OP_20260101_000000"), DirEntry("OP_20260102_000000")]
        return original_scandir(path)

    monkeypatch.setattr(
        cyberautoagent.os,
        "scandir",
        scandir,
    )

    cyberautoagent.main()

    assert os.environ["CYBER_OPERATION_ID"] == "OP_20260102_000000"
    assert json.loads(os.environ["CYBER_BUG_BOUNTY_HEADERS"]) == {"X-Test": "1"}
    callback.ensure_report_generated.assert_called_once()
    report_call = callback.ensure_report_generated.call_args
    assert report_call.args == (None, "example.com", "report", "web")
    assert report_call.kwargs["completion_status"]["assessment_complete"] is False
    fake_agent.cleanup.assert_not_called()


def test_cli_main_service_mode_with_params_auto_runs(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = CallableCliAgent()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        ["cyberautoagent", "--service-mode", "--target", "example.com", "--objective", "run", "--max-duration", "60", "--provider", "ollama"],
    )

    cyberautoagent.main()

    config_manager.workflow.run.assert_called_once()
    fake_agent.cleanup.assert_not_called()


def test_cli_main_passes_token_and_cost_budgets_to_agent_config(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = CallableCliAgent()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        [
            "cyberautoagent",
            "--target",
            "example.com",
            "--objective",
            "run",
            "--max-duration",
            "60",
            "--max-tokens",
            "250000",
            "--max-cost",
            "12.34",
            "--provider",
            "ollama",
        ],
    )

    cyberautoagent.main()

    config = cyberautoagent.create_agent_runtime_resources.call_args.kwargs["config"]
    assert config.budget.max_duration_minutes == 60
    assert config.budget.max_tokens == 250000
    assert config.budget.max_cost == 12.34
    config_manager.workflow.run.assert_called_once()
    fake_agent.cleanup.assert_not_called()


def test_cli_main_handles_max_tokens_exception(monkeypatch, tmp_path):
    callback = CliCallback()

    class TokenAgent:
        messages = []
        model = SimpleNamespace()
        cleanup = Mock()

        def __call__(self, _message):
            raise MaxTokensReachedException("max_tokens")

    agent = TokenAgent()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, agent, callback)
    config_manager.workflow.run.side_effect = MaxTokensReachedException("max_tokens")
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"])

    cyberautoagent.main()

    callback.emit_termination.assert_called_with("max_tokens", "Model token limit reached. Switching to final report.")
    callback.ensure_report_generated.assert_called()
    agent.cleanup.assert_not_called()


@pytest.mark.parametrize("has_callback", [True, False])
def test_cli_main_emits_workflow_invariant_message_as_termination_reason(monkeypatch, tmp_path, has_callback):
    callback = CliCallback() if has_callback else None
    agent = CallableCliAgent()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, agent, callback)
    message = "Workflow iteration limit reached"
    config_manager.workflow.run.side_effect = cyberautoagent.WorkflowInvariantError(message)
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        [
            "cyberautoagent",
            "--target",
            "example.com",
            "--objective",
            "test",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cyberautoagent.main()

    assert exc_info.value.code == 1
    if callback:
        callback.emit_termination.assert_called_once_with("error", message)


def test_cli_main_preserves_generic_workflow_error_termination(monkeypatch, tmp_path):
    callback = CliCallback()
    agent = CallableCliAgent()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, agent, callback)
    message = "Unexpected workflow failure"
    config_manager.workflow.run.side_effect = RuntimeError(message)
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        [
            "cyberautoagent",
            "--target",
            "example.com",
            "--objective",
            "test",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cyberautoagent.main()

    assert exc_info.value.code == 1
    callback.emit_termination.assert_called_with("error", message)


def test_cli_main_runs_workflow_controller(monkeypatch, tmp_path):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)
    callback.has_reached_limit = Mock(return_value=False)

    class QuietAgent:
        def __init__(self):
            self.messages = [
                {"role": "user", "content": [{"text": "initial"}]},
                {"role": "assistant", "content": [{"text": "thinking"}]},
                {"role": "assistant", "content": [{"text": "still thinking"}]},
                {"role": "assistant", "content": [{"text": "done"}]},
            ]
            self.model = SimpleNamespace()
            self.cleanup = Mock()
            self.calls = []

        def __call__(self, message):
            self.calls.append(message)
            return SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={}))

    agent = QuietAgent()
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"])
    config_manager = _patch_cli_common(monkeypatch, tmp_path, agent, callback)
    cyberautoagent.main()

    config_manager.workflow.run.assert_called_once()
    agent.cleanup.assert_not_called()
    expected_cwd = tmp_path / "example.com" / os.environ["CYBER_OPERATION_ID"]
    assert Path.cwd() == expected_cwd


def test_cli_preflight_persists_without_initializing_qdrant(monkeypatch, tmp_path):
    callback = CliCallback()
    plan_store = Mock()
    plan_store_cls = Mock(return_value=plan_store)
    agent = CallableCliAgent()
    _patch_cli_common(monkeypatch, tmp_path, agent, callback)
    monkeypatch.setattr(cyberautoagent, "create_application_store", plan_store_cls)
    monkeypatch.setattr(cyberautoagent, "get_application_database_path", Mock(return_value="/tmp/preflight.db"))
    semantic_memory = Mock()
    monkeypatch.setattr(cyberautoagent, "get_memory_client", semantic_memory)
    plan_store.store_preflight_results.side_effect = lambda *_args: semantic_memory.assert_not_called()
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        [
            "cyberautoagent",
            "--target",
            "example.com",
            "--objective",
            "test",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
        ],
    )

    cyberautoagent.main()

    plan_store_cls.assert_called_once_with("/tmp/preflight.db", logical_target="example.com")
    plan_store.store_preflight_results.assert_called_once()


def test_cli_main_preflight_failure_stops_before_environment_or_workflow(monkeypatch, tmp_path):
    callback = CliCallback()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, CallableCliAgent(), callback)
    failure = cyberautoagent.TargetValidationResult(
        "target-1",
        "missing.test",
        "network",
        "fail",
        ("resolve",),
        "name not found",
    )
    monkeypatch.setattr(
        cyberautoagent,
        "run_target_preflight",
        Mock(return_value=([cyberautoagent.OperationTarget("target-1", "missing.test", "network")], [failure])),
    )
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        [
            "cyberautoagent",
            "--target",
            "missing.test",
            "--objective",
            "test",
            "--max-duration",
            "60",
            "--provider",
            "ollama",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cyberautoagent.main()

    assert exc_info.value.code == 2
    cyberautoagent.auto_setup.assert_not_called()
    config_manager.workflow.run.assert_not_called()


def test_cli_main_workflow_runner_creates_role_agent(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = CallableCliAgent()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    runner_result = cyberautoagent.AgentRunResult("callback_stop", "worker finished")
    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", Mock(return_value=runner_result))
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"])

    def run_workflow():
        work_runner = config_manager.workflow_controller.call_args.kwargs["work_runner"]
        policy = cyberautoagent.AgentRunPolicy(required_tool_names={"shell"}, terminal_after_required_tools=True)
        result = work_runner("task_executor", "do the task", ["shell"], "role system", policy)
        assert result == "worker finished"

    config_manager.workflow.run.side_effect = run_workflow

    cyberautoagent.main()

    cyberautoagent.create_agent.assert_called_once()
    agent_kwargs = cyberautoagent.create_agent.call_args.kwargs
    assert agent_kwargs["runtime_resources"] is cyberautoagent.create_agent_runtime_resources.return_value
    assert agent_kwargs["system_prompt"] == "role system"
    assert agent_kwargs["tools"] == ["shell"]
    assert re.match(r"Cyber-AutoAgent OP_\d{8}_\d{6} task_executor", agent_kwargs["name"])
    assert agent_kwargs["agent_type"] == "task_executor"
    assert agent_kwargs["include_tool_catalog"] is True
    cyberautoagent.run_agent_until_terminal_state.assert_called_once()
    run_kwargs = cyberautoagent.run_agent_until_terminal_state.call_args.kwargs
    assert run_kwargs["agent"] is fake_agent
    assert run_kwargs["callback_handler"] is callback
    assert run_kwargs["current_message"] == "do the task"
    assert run_kwargs["run_policy"].required_tool_names == frozenset({"shell"})
    fake_agent.cleanup.assert_called_once()


@pytest.mark.parametrize(("role", "include_tool_catalog"), [("task_executor", True), ("task_creator", False)])
def test_cli_main_worker_session_reuses_and_cleans_role_agent(
    monkeypatch,
    tmp_path,
    role,
    include_tool_catalog,
):
    callback = CliCallback()
    fake_agent = CallableCliAgent()
    journal = ToolOutcomeJournal()
    journal.append(
        tool_use_id="prior-tool",
        tool_name="shell",
        success=True,
        correctable=False,
        tool_input={"command": "true"},
        output="complete",
    )
    fake_agent._cyber_callback_handler = SimpleNamespace(tool_outcome_journal=journal)
    config_manager = _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    runner_result = cyberautoagent.AgentRunResult("task_executor_done", "worker finished")
    run_count = 0

    def run_agent(**kwargs):
        nonlocal run_count
        run_count += 1
        if run_count == 1:
            fake_agent.messages.append({"role": "assistant", "content": [{"text": "first summary"}]})
        return runner_result

    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", Mock(side_effect=run_agent))
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"],
    )

    def run_workflow():
        session_factory = config_manager.workflow_controller.call_args.kwargs["executor_session_factory"]
        policy = cyberautoagent.AgentRunPolicy(min_tool_calls=1, terminal_after_required_tools=True)
        with session_factory(role, ["shell"], "role system") as run_executor:
            assert callable(run_executor.live_outcomes)
            assert [outcome.tool_use_id for outcome in run_executor.live_outcomes()] == ["prior-tool"]
            first_result = run_executor("first pass", policy)
            second_result = run_executor("critic guidance", policy)
            assert first_result.text == "first summary"
            assert first_result.outcomes == []
            assert first_result.recovery_required is False
            assert second_result.text == "worker finished"
        fake_agent.cleanup.assert_called_once()

    config_manager.workflow.run.side_effect = run_workflow

    cyberautoagent.main()

    cyberautoagent.create_agent.assert_called_once()
    create_kwargs = cyberautoagent.create_agent.call_args.kwargs
    assert create_kwargs["agent_type"] == role
    assert create_kwargs["include_tool_catalog"] is include_tool_catalog
    assert cyberautoagent.run_agent_until_terminal_state.call_count == 2
    assert [
        call.kwargs["current_message"]
        for call in cyberautoagent.run_agent_until_terminal_state.call_args_list
    ] == ["first pass", "critic guidance"]
    fake_agent.cleanup.assert_called_once()


def test_cli_main_retained_executor_preserves_repeated_tool_loop_metadata(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = CallableCliAgent()
    config_manager = _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    loop_details = {
        "cycle_signature": "loop-signature",
        "cycle_length": 1,
        "repeat_count": 4,
        "tool_name": "shell",
        "tool_names": ["shell"],
    }
    runner_result = cyberautoagent.AgentRunResult(
        "repeated_tool_loop",
        "Stopped agent after repeated shell calls",
        details=loop_details,
    )
    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", Mock(return_value=runner_result))
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"],
    )

    def run_workflow():
        session_factory = config_manager.workflow_controller.call_args.kwargs["executor_session_factory"]
        with session_factory("task_executor", ["shell"], "role system") as run_executor:
            result = run_executor("perform task", cyberautoagent.AgentRunPolicy())
        assert result.text == runner_result.message
        assert result.repeat_loop_detected is True
        assert result.repeat_loop_signature == "loop-signature"
        assert result.repeat_loop_reason == runner_result.message

    config_manager.workflow.run.side_effect = run_workflow

    cyberautoagent.main()


def test_cli_main_workflow_runner_prefers_agent_final_text_over_policy_message(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = CallableCliAgent()
    fake_agent.messages = [{"role": "assistant", "content": [{"toolUse": {"name": "shell"}}]}]
    config_manager = _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    policy = cyberautoagent.AgentRunPolicy(
        required_tool_names={"shell"},
        terminal_after_required_tools=True,
        terminal_reason="task_executor_done",
        terminal_message="Task executor completed after tool use",
    )
    runner_result = cyberautoagent.AgentRunResult(policy.terminal_reason, policy.terminal_message)
    def run_agent(**kwargs):
        fake_agent.messages.append({"role": "assistant", "content": [{"text": "real task summary"}]})
        return runner_result

    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", Mock(side_effect=run_agent))
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"],
    )

    def run_workflow():
        work_runner = config_manager.workflow_controller.call_args.kwargs["work_runner"]
        assert work_runner("task_executor", "do the task", ["shell"], "role system", policy) == "real task summary"

    config_manager.workflow.run.side_effect = run_workflow

    cyberautoagent.main()
    fake_agent.cleanup.assert_called_once()


def test_cli_main_workflow_runner_omits_policy_message_when_no_agent_final_text(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = CallableCliAgent()
    fake_agent.messages = [{"role": "assistant", "content": [{"toolUse": {"name": "shell"}}]}]
    config_manager = _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    policy = cyberautoagent.AgentRunPolicy(
        required_tool_names={"shell"},
        terminal_after_required_tools=True,
        terminal_reason="task_executor_done",
        terminal_message="Task executor completed after tool use",
    )
    runner_result = cyberautoagent.AgentRunResult(policy.terminal_reason, policy.terminal_message)
    monkeypatch.setattr(cyberautoagent, "run_agent_until_terminal_state", Mock(return_value=runner_result))
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        ["cyberautoagent", "--target", "example.com", "--objective", "test", "--max-duration", "60", "--provider", "ollama"],
    )

    def run_workflow():
        work_runner = config_manager.workflow_controller.call_args.kwargs["work_runner"]
        assert work_runner("task_executor", "do the task", ["shell"], "role system", policy) == ""

    config_manager.workflow.run.side_effect = run_workflow

    cyberautoagent.main()
    fake_agent.cleanup.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
