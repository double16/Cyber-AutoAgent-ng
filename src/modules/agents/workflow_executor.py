"""Shared production workflow executor sessions and outcome/recovery capture."""

from contextlib import contextmanager
from typing import Any

from strands.types.exceptions import MaxTokensReachedException

from modules.agents.multi_agent_workflow import TaskExecutorCycleResult, WorkflowInvariantError
from modules.agents.run_policy import AgentRunPolicy
from modules.utils.sdk_error_sanitization import sanitize_sdk_error


@contextmanager
def workflow_executor_session(*, runtime, role: str, tools: list[Any], system_prompt: str,
                              create_agent, invoke, recovery_guidance, logger, config=None,
                              target=None, objective=None, operation_id=None):
    """Open the same bounded executor session for CLI execution and integration continuation."""
    config = runtime.config if config is None else config
    agent = create_agent(
        target=config.target if target is None else target,
        objective=config.objective if objective is None else objective,
        config=config,
        runtime_resources=runtime,
        system_prompt=system_prompt,
        tools=tools,
        name=f"Cyber-AutoAgent {operation_id or runtime.operation_id} {role}",
        agent_type=role,
        include_tool_catalog=role != "task_creator",
    )
    try:
        callback = getattr(agent, "_cyber_callback_handler", None)
        recovery_hook = getattr(agent, "_cyber_failure_recovery_hook", None)
        journal = getattr(callback, "tool_outcome_journal", None)
        if role == "authentication_agent":
            recorder = getattr(agent, "_cyber_tool_outcome_recording_hook", None)
            if recorder is None or journal is None or recorder.journal is not journal:
                raise WorkflowInvariantError("Worker requires its agent's outcome recorder")


        def run_retained_executor(
            prompt: str,
            run_policy: AgentRunPolicy | None = None,
        ) -> TaskExecutorCycleResult:
            snapshot = journal.snapshot() if journal is not None else 0
            try:
                run_result, result_message = invoke(agent, prompt, run_policy)
            except MaxTokensReachedException as error:
                sanitize_sdk_error(error)
                classification = getattr(error, "max_token_classification", None)
                kind = getattr(classification, "kind", "output_truncation")
                outcomes = journal.since(snapshot) if journal is not None else []
                role_label = "Task creator" if role == "task_creator" else "Task executor"
                return TaskExecutorCycleResult(
                    text="",
                    outcomes=outcomes,
                    max_tokens_exhausted=True,
                    max_tokens_classification=kind,
                    max_token_efficiency_accounted=bool(
                        getattr(error, "_max_token_efficiency_recorded", False)
                    ),
                    max_tokens_reason=(
                        f"{role_label} repeated the same reasoning loop after its bounded recovery."
                        if kind == "reasoning_loop"
                        else f"{role_label} reached its output-token limit after its bounded recovery."
                    ),
                )
            outcomes = journal.since(snapshot) if journal is not None else []
            repeat_loop = (
                run_result.details
                if run_result.reason == "repeated_tool_loop"
                else None
            )
            return TaskExecutorCycleResult(
                text=result_message,
                outcomes=outcomes,
                reason=run_result.reason,
                message=run_result.message,
                details=run_result.details,
                artifact_limit_exhausted=bool(
                    getattr(getattr(agent, "_cyber_evaluator_artifact_read_limit_hook", None),
                            "exhausted", False)
                ),
                artifact_limit_events=tuple(
                    getattr(getattr(agent, "_cyber_evaluator_artifact_read_limit_hook", None),
                            "limit_events", ())
                ),
                recovery_required=bool(recovery_hook and recovery_hook.unresolved),
                recovery_exhausted=bool(recovery_hook and recovery_hook.exhausted),
                recovery_guidance=recovery_guidance(recovery_hook),
                repeat_loop_detected=isinstance(repeat_loop, dict),
                repeat_loop_signature=(
                    str(repeat_loop.get("cycle_signature", ""))
                    if isinstance(repeat_loop, dict)
                    else ""
                ),
                repeat_loop_reason=(
                    result_message
                    if isinstance(repeat_loop, dict)
                    else ""
                ),
            )

        run_retained_executor.live_outcomes = (
            lambda: journal.entries() if journal is not None else []
        )
        yield run_retained_executor
    finally:
        try:
            agent.cleanup()
        except Exception as error:
            logger.warning("Unable to clean up role agent %s: %s", role, error)
