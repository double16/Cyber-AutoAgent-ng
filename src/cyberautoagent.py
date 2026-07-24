#!/usr/bin/env python3
"""
Cyber-AutoAgent - Autonomous Cybersecurity Assessment Tool
=========================================================

An autonomous cybersecurity agent powered by Strands framework.
Conducts authorized penetration testing with intelligent tool selection and
evidence collection capabilities.

EXPERIMENTAL SOFTWARE - USE ONLY IN AUTHORIZED, SAFE, SANDBOXED ENVIRONMENTS

For educational and authorized security testing purposes only.
Ensure you have explicit permission before testing any targets.

Author: Patrick Double
Original Author: Aaron Brown
License: MIT
"""

import argparse
import asyncio
import atexit
import base64
import importlib
import inspect
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import litellm
import requests
from botocore.exceptions import (
    ConnectTimeoutError as BotoConnectTimeoutError,
)
from botocore.exceptions import (
    EndpointConnectionError as BotoEndpointConnectionError,
)
from botocore.exceptions import (
    ReadTimeoutError as BotoReadTimeoutError,
)
from dotenv import load_dotenv
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout as RequestsReadTimeout
from strands.telemetry.config import StrandsTelemetry
from strands.types.exceptions import MaxTokensReachedException

from modules.agents.cyber_autoagent import (
    AgentConfig,
    _ensure_prompt_within_budget,
    create_agent,
    create_agent_runtime_resources,
)
from modules.agents.multi_agent_workflow import (
    MultiAgentWorkflowController,
    TaskExecutorCycleResult,
    WorkflowInvariantError,
)
from modules.agents.run_policy import AgentRunPolicy
from modules.config.manager import get_config_manager
from modules.config.models.factory import (  # noqa: F401
    configure_model_rate_limits,
    get_model_timeout,
)
from modules.config.system.environment import (
    auto_setup,
    clean_operation_memory,
    setup_logging,
)
from modules.config.types import (
    DEFAULT_MAX_DURATION,
    BudgetConfig,
    get_default_base_dir,
)
from modules.handlers.base import BudgetLimitReached, is_docker
from modules.handlers.max_token_recovery import (
    build_task_executor_max_token_prompt,
    classify_and_discard_max_token_output,
    is_repeated_max_token_pattern,
)
from modules.handlers.react import AgentEventHandler
from modules.handlers.tool_repeat_guard import REPEATED_TOOL_LOOP_STATE_KEY
from modules.handlers.utils import (
    Colors,
    dumpstacks,
    get_output_path,
    get_terminal_width,
    print_banner,
    print_section,
    print_status,
    sanitize_target_name,
    update_latest_output_pointer,
)
from modules.handlers.terminal_tool import (
    TERMINAL_TOOL_COMPLETED_STATE_KEY,
    TERMINAL_TOOL_REJECTED_STATE_KEY,
)
from modules.tools import browser, channel_close_all
from modules.tools.memory import get_memory_client
from modules.tools.oast import close_oast_providers
from modules.tools.tool_catalog import get_shell_command_help_context
from modules.utils.telemetry import flush_traces

load_dotenv()

warnings.filterwarnings("ignore", category=DeprecationWarning)


# Backward-compatibility: provide a placeholder symbol so tests can patch it
# The real value is set later during runtime execution.
def get_initial_prompt():  # noqa: D401
    """Placeholder function; patched in tests and set at runtime."""
    return ""


def _recovery_guidance_with_failed_command_help(recovery_hook: Any, available_tools: list[str]) -> str:
    if not recovery_hook or not recovery_hook.unresolved:
        return ""
    return recovery_hook.recovery_guidance(
        get_shell_command_help_context(
            recovery_hook.failed_executable,
            available_tools,
        )
    )


def is_langfuse_available() -> bool:
    """Return whether the configured Langfuse health endpoint is reachable."""

    try:
        if is_docker():
            langfuse_host = os.getenv("LANGFUSE_HOST", "http://langfuse-web:3000")
        else:
            langfuse_host = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
        response = requests.get(f"{langfuse_host}/api/public/health", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def detect_deployment_mode():
    """
    Detect deployment mode for appropriate observability defaults.

    Returns:
        str: 'cli' (Python CLI), 'container' (single container), or 'compose' (full stack)
    """

    if is_docker():
        if is_langfuse_available():
            return "compose"  # Full Docker Compose stack
        else:
            return "container"  # Single container mode
    else:
        if is_langfuse_available():
            return "compose"  # Local development with Langfuse
        else:
            return "cli"  # Pure Python CLI mode


def setup_telemetry(logger):
    """
    Setup telemetry system with separated concerns:
    1. Local telemetry (always enabled) - for token counting, cost tracking, metrics
    2. Remote observability (deployment-aware) - for Langfuse trace export

    Local telemetry provides essential metrics for UI display regardless of deployment mode.
    Remote observability is only enabled when Langfuse infrastructure is available.
    """
    deployment_mode = detect_deployment_mode()

    # Set smart defaults based on deployment mode
    if deployment_mode == "compose":
        default_observability = "true"
        logger.info(
            "Detected full-stack deployment mode - observability enabled by default"
        )
    else:
        default_observability = "false"
        logger.info(
            "Detected %s deployment mode - observability disabled by default",
            deployment_mode,
        )
        logger.info(
            "To enable observability, set ENABLE_OBSERVABILITY=true and ensure Langfuse is running"
        )

    # Always initialize Strands telemetry for local metrics (token counting, cost tracking)
    # This sets up the global tracer provider that the Agent will use
    telemetry = StrandsTelemetry()
    logger.info("Strands telemetry initialized - token counting enabled")

    # Check if remote observability (Langfuse export) is enabled
    # Keep it simple: in React UI mode, the app is the source of truth; otherwise fall back to previous default
    ui_mode = os.getenv("CYBER_UI_MODE", "").lower()
    if ui_mode == "react":
        observability_enabled = (
            os.getenv("ENABLE_OBSERVABILITY", "false").lower() == "true"
        )
        logger.info(
            "React UI mode: observability %s by application",
            "enabled" if observability_enabled else "disabled",
        )
    else:
        observability_enabled = (
            os.getenv("ENABLE_OBSERVABILITY", default_observability).lower() == "true"
        )
        logger.info(
            "Non-UI/CLI mode: observability %s (fallback defaults)",
            "enabled" if observability_enabled else "disabled",
        )

    if observability_enabled:
        logger.info("Remote observability enabled - configuring Langfuse export")
        if not is_langfuse_available():
            logger.warning(
                "Langfuse is unavailable; continuing with local telemetry only"
            )
        else:
            try:
                setup_langfuse_connection(logger, deployment_mode)
                telemetry.setup_otlp_exporter()
            except Exception as error:
                logger.warning(
                    "Unable to configure OTLP exporter; continuing with local telemetry only: %s",
                    error,
                )
            else:
                logger.info(
                    "OTLP exporter configured - traces will be exported to Langfuse"
                )
    else:
        logger.info("Remote observability disabled - metrics available locally only")
        logger.debug("Token counting and cost tracking enabled via local telemetry")

    return telemetry


def setup_langfuse_connection(logger, deployment_mode):
    """Setup Langfuse connection parameters for remote observability."""

    # Use langfuse-web:3000 when in Docker, localhost:3000 otherwise
    default_host = (
        "http://langfuse-web:3000" if is_docker() else "http://localhost:3000"
    )
    host = os.getenv("LANGFUSE_HOST", default_host)
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "cyber-public")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "cyber-secret")

    # Create auth token for Langfuse
    auth_token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    # Set OpenTelemetry environment variables that Strands SDK will use
    os.environ["OTEL_SERVICE_NAME"] = "cyber-autoagent"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{host}/api/public/otel"
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Basic {auth_token}"

    logger.info("Langfuse connection configured at %s", host)
    logger.info("OTLP endpoint: %s", os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])
    logger.info("View traces at %s (login: admin@cyber-autoagent.com/changeme)", host)


# Global flag for interrupt handling
interrupted = False


def signal_handler(signum, frame):  # pylint: disable=unused-argument
    """Handle interrupt signals gracefully"""
    global interrupted
    interrupted = True

    # Determine signal type for appropriate message
    if signum == signal.SIGINT:
        signal_name = "SIGINT (Ctrl+C)"
    elif signum == signal.SIGTSTP:
        signal_name = "SIGTSTP (Ctrl+Z)"
    elif signum == signal.SIGTERM:
        signal_name = "SIGTERM (ESC Kill Switch)"
    else:
        signal_name = f"Signal {signum}"

    print(f"\n\033[93m[!] {signal_name} received. Stopping agent gracefully...\033[0m")

    # For swarm operations, we need to be more forceful
    # Check if we're in a swarm operation by looking at the call stack
    stack = traceback.extract_stack()
    in_swarm = any(
        "swarm" in str(frame_info.filename).lower()
        or "swarm" in str(frame_info.name).lower()
        for frame_info in stack
    )

    if in_swarm:
        print(
            "\033[91m[!] Swarm operation detected - forcing immediate termination\033[0m"
        )

        # Force exit after a short delay to allow cleanup
        def force_exit():
            time.sleep(2)
            print("\033[91m[!] Force terminating swarm operation\033[0m")
            os._exit(1)

        threading.Thread(target=force_exit, daemon=True).start()

    # Raise KeyboardInterrupt to interrupt current operation
    raise KeyboardInterrupt("User interrupted operation")


@dataclass
class AgentRunResult:
    """Terminal state from one agent run loop."""

    reason: str
    message: str = ""


RECOVERABLE_AGENT_ERRORS = (
    RequestsReadTimeout,
    RequestsConnectionError,
    BotoReadTimeoutError,
    BotoEndpointConnectionError,
    BotoConnectTimeoutError,
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
)


def is_recoverable_agent_error(error: Exception) -> bool:
    """Return True when the agent can be retried after a provider/network interruption."""
    error_str = str(error).lower()
    return isinstance(error, RECOVERABLE_AGENT_ERRORS) or any(
        marker in error_str
        for marker in [
            "read timed out",
            "readtimeouterror",
            "network connection",
            "ratelimiterror",
            "serviceunavailableerror",
        ]
    )


def process_agent_metrics(callback_handler: Any, result: Any) -> None:
    """Forward Strands result usage metrics to the operation callback handler."""
    if not callback_handler or not hasattr(result, "metrics") or not result.metrics:
        return
    if not hasattr(result.metrics, "accumulated_usage") or not result.metrics.accumulated_usage:
        return

    class MetricsObject:
        def __init__(self, accumulated_usage):
            self.accumulated_usage = accumulated_usage

    callback_handler.process_metrics(MetricsObject(result.metrics.accumulated_usage))


def extract_last_assistant_text(messages: Any) -> str:
    """Return text from the most recent assistant message without tool calls."""

    try:
        reversed_messages = reversed(list(messages or []))
    except TypeError:
        return ""
    for message in reversed_messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content", []) or []
        if not isinstance(content, list):
            continue
        has_tool_use = any(isinstance(block, dict) and ("toolUse" in block or "tool_use" in block) for block in content)
        if has_tool_use:
            continue
        parts = [block.get("text", "") for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
        text = "\n".join(part for part in parts if part).strip()
        if text:
            return text
    return ""


def _tool_count_deltas(callback_handler: Any, baseline: Optional[dict[str, int]] = None) -> dict[str, int]:
    """Return non-negative tool-call counts observed after a run-pass baseline."""

    tool_counts = getattr(callback_handler, "tool_counts", {}) or {}
    baseline = baseline or {}
    return {
        name: max(0, int(count or 0) - int(baseline.get(name, 0) or 0))
        for name, count in tool_counts.items()
    }


def _required_tools_satisfied(
    callback_handler: Any,
    run_policy: AgentRunPolicy,
    baseline: Optional[dict[str, int]] = None,
) -> bool:
    """Return true when this run pass has observed enough required tool calls."""

    tool_counts = _tool_count_deltas(callback_handler, baseline)
    tool_total_count = sum(
        count
        for name, count in tool_counts.items()
        if name not in run_policy.ignored_terminal_tool_names
    )
    if tool_total_count < run_policy.min_tool_calls:
        return False
    return all(int(tool_counts.get(name, 0) or 0) > 0 for name in run_policy.required_tool_names)


def _successful_required_tools_satisfied(callback_handler: Any, run_policy: AgentRunPolicy, baseline: int) -> bool:
    """Return true when every required tool has a successful controller-observed outcome."""

    journal = getattr(callback_handler, "tool_outcome_journal", None)
    if journal is None or not hasattr(journal, "since"):
        return False
    successful = {outcome.tool_name for outcome in journal.since(baseline) if outcome.success}
    return run_policy.required_tool_names.issubset(successful)


def _run_policy_allows_terminal_text(
    callback_handler: Any,
    run_policy: AgentRunPolicy,
    actionless_attempt_count: int,
    baseline: Optional[dict[str, int]] = None,
) -> bool:
    """Return true when an actionless turn is a valid final response under policy."""

    if not run_policy.terminal_after_required_tools:
        return False
    if run_policy.require_successful_required_tools:
        return False
    required_tools_satisfied = _required_tools_satisfied(callback_handler, run_policy, baseline)
    if not required_tools_satisfied:
        return False
    if not run_policy.allow_text_final_after_tools:
        return True
    return actionless_attempt_count > run_policy.max_actionless_after_tools


def _invoke_agent_with_turn_limit(agent: Any, message: str, max_model_turns: int) -> Any:
    """Pass the SDK turn bound while remaining compatible with simple callable test doubles."""

    try:
        parameters = inspect.signature(agent).parameters.values()
        supports_limits = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters) or any(
            parameter.name == "limits" for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_limits = True
    if supports_limits:
        return agent(message, limits={"turns": max_model_turns})
    return agent(message)


def run_agent_until_terminal_state(
    *,
    agent: Any,
    callback_handler: Any,
    current_message: str,
    initial_prompt: str,
    budget_cfg: BudgetConfig,
    operation_start: float,
    max_duration: int | None,
    logger: Any,
    recoverable_retries: int = 2,
    run_policy: Optional[AgentRunPolicy] = None,
) -> AgentRunResult:
    """Run one agent until it reaches a normal terminal state or raises a fatal failure."""
    run_policy = run_policy or AgentRunPolicy()
    initial_reasoning_retry = 2
    actionless_attempt_count = 0
    agent_call_count = 0
    recoverable_attempt_count = 0
    agent_callback_handler = getattr(agent, "_cyber_callback_handler", None) or callback_handler
    run_tool_count_baseline = dict(getattr(agent_callback_handler, "tool_counts", {}) or {})
    outcome_journal = getattr(agent_callback_handler, "tool_outcome_journal", None)
    outcome_baseline = outcome_journal.snapshot() if outcome_journal is not None else 0

    while not interrupted:
        if agent_call_count >= run_policy.max_agent_calls:
            termination_reason = f"Stopped after {agent_call_count} agent calls without reaching the role contract"
            print_status(termination_reason, "WARNING")
            if agent_callback_handler:
                agent_callback_handler.emit_termination("stalled", termination_reason)
            return AgentRunResult("stalled", termination_reason)
        last_tool_call_count = sum(agent_callback_handler.tool_counts.values(), start=0)
        try:
            print_status(
                f"Agent processing: {current_message[:100]}{' ...' if len(current_message) > 100 else ''}",
                "THINKING",
            )
            logger.debug("Agent processing: %s", current_message)

            try:
                iter(getattr(agent, "messages", []))
            except TypeError:
                agent.messages = []
            _ensure_prompt_within_budget(agent)

            agent_call_count += 1
            result = _invoke_agent_with_turn_limit(agent, current_message, run_policy.max_model_turns)
            recoverable_attempt_count = 0

            logger.debug("Agent result: %r", result)
            process_agent_metrics(agent_callback_handler, result)

            stop_reason = str(getattr(result, "stop_reason", "") or "")
            if stop_reason.startswith("limit_"):
                termination_reason = f"Agent stopped at its configured SDK limit: {stop_reason}"
                print_status(termination_reason, "WARNING")
                if agent_callback_handler:
                    agent_callback_handler.emit_termination("stalled", termination_reason)
                return AgentRunResult("stalled", termination_reason)

            result_state = getattr(result, "state", {})
            terminal_tool_completed = (
                result_state.get(TERMINAL_TOOL_COMPLETED_STATE_KEY)
                if isinstance(result_state, dict)
                else None
            )
            if run_policy.require_successful_required_tools and isinstance(terminal_tool_completed, dict):
                completed_tool = str(terminal_tool_completed.get("tool_name", ""))
                if completed_tool in run_policy.required_tool_names:
                    return AgentRunResult(run_policy.terminal_reason, run_policy.terminal_message)
            if (
                run_policy.require_successful_required_tools
                and run_policy.terminal_after_required_tools
                and _successful_required_tools_satisfied(agent_callback_handler, run_policy, outcome_baseline)
            ):
                return AgentRunResult(run_policy.terminal_reason, run_policy.terminal_message)
            terminal_tool_rejected = (
                result_state.get(TERMINAL_TOOL_REJECTED_STATE_KEY)
                if isinstance(result_state, dict)
                else None
            )
            if isinstance(terminal_tool_rejected, dict):
                return AgentRunResult(
                    "required_tool_rejected",
                    str(terminal_tool_rejected.get("error") or "Required tool call was rejected"),
                )
            repeated_tool_loop = (
                result_state.get(REPEATED_TOOL_LOOP_STATE_KEY)
                if isinstance(result_state, dict)
                else None
            )
            if isinstance(repeated_tool_loop, dict):
                tool_name = str(repeated_tool_loop.get("tool_name", "unknown"))
                repeat_count = int(repeated_tool_loop.get("repeat_count", 0) or 0)
                cycle_length = int(repeated_tool_loop.get("cycle_length", 1) or 1)
                if cycle_length > 1:
                    raw_tool_names = repeated_tool_loop.get("tool_names", [])
                    tool_names = list(dict.fromkeys(
                        str(item) for item in raw_tool_names if str(item).strip()
                    )) if isinstance(raw_tool_names, list) else []
                    tool_summary = f" involving {', '.join(tool_names)}" if tool_names else ""
                    message = (
                        f"Stopped agent after {repeat_count} repetitions of a {cycle_length}-call tool cycle"
                        f"{tool_summary}; matching completed results were reused."
                    )
                else:
                    message = (
                        f"Stopped agent after {repeat_count} consecutive identical calls to {tool_name}; "
                        "the latest completed result was reused."
                    )
                logger.warning(message)
                return AgentRunResult("repeated_tool_loop", message)

            tool_total_count = sum(agent_callback_handler.tool_counts.values())
            if tool_total_count > last_tool_call_count:
                actionless_attempt_count = 0
            else:
                actionless_attempt_count += 1
                logger.debug(
                    "Agent returned without new tool calls, actionless_count=%d, tool_total_count=%d",
                    actionless_attempt_count,
                    tool_total_count,
                )

            run_tool_deltas = _tool_count_deltas(agent_callback_handler, run_tool_count_baseline)
            run_tool_total = sum(
                count
                for name, count in run_tool_deltas.items()
                if name not in run_policy.ignored_terminal_tool_names
            )
            if run_policy.max_tool_calls and run_tool_total >= run_policy.max_tool_calls:
                return AgentRunResult(run_policy.terminal_reason, run_policy.terminal_message)

            if _run_policy_allows_terminal_text(
                agent_callback_handler,
                run_policy,
                actionless_attempt_count,
                run_tool_count_baseline,
            ):
                return AgentRunResult(run_policy.terminal_reason, run_policy.terminal_message)

            if agent_callback_handler and agent_callback_handler.should_stop():
                if agent_callback_handler.has_reached_limit():
                    print_status("Budget limit reached - terminating", "SUCCESS")
                    raise BudgetLimitReached("Budget limit reached")
                return AgentRunResult("callback_stop", "Callback requested stop")

            if actionless_attempt_count >= run_policy.max_actionless_calls:
                termination_reason = f"No actions taken after {actionless_attempt_count} attempts"
                if run_policy.required_tool_names or run_policy.min_tool_calls > 0:
                    print_status(termination_reason, "WARNING")
                    if agent_callback_handler:
                        agent_callback_handler.emit_termination("stalled", termination_reason)
                    return AgentRunResult("stalled", termination_reason)
                print_status("No actions taken - completing", "SUCCESS")
                return AgentRunResult("no_actions", termination_reason)

            if sum(run_tool_deltas.values()) == 0:
                if initial_reasoning_retry <= 0:
                    print_status("No actions taken - completing", "SUCCESS")
                    return AgentRunResult("no_actions", "No actions taken")
                initial_reasoning_retry -= 1

            elapsed = time.time() - operation_start
            if max_duration is not None and elapsed >= float(max_duration) * 60.0:
                logger.info("Duration budget reached (elapsed=%ss)", int(elapsed))
                raise BudgetLimitReached("Duration budget reached")

            current_message = ""

            if actionless_attempt_count > 0:
                if actionless_attempt_count == 1:
                    logger.warning(
                        "Attempting to redirect model to emit valid tool calls because no tool calls were detected "
                        "in last execution loop."
                    )

                    while len(agent.messages) > 3:
                        tool_block_count = 0
                        for block in agent.messages[-1].get("content", []):
                            if not isinstance(block, dict):
                                continue
                            if "toolUse" in block or "toolResult" in block:
                                tool_block_count += 1
                        if tool_block_count == 0:
                            agent.messages.pop()
                        else:
                            break

                    required_tools = ", ".join(sorted(run_policy.required_tool_names))
                    if run_policy.actionless_mode == "task_progress":
                        completion_instruction = (
                            f"Treat the required completion tool(s) ({required_tools}) as completion conditions, not "
                            "necessarily the next action. Use them only after their prerequisite work and evidence "
                            "are complete."
                            if required_tools
                            else "Follow the controller's recovery guidance and stay within the assigned task."
                        )
                        current_message += (
                            "**MANDATORY ACTION**: Continue the assigned task under its run policy. "
                            "Call the next registered tool needed to satisfy an unmet acceptance criterion and "
                            f"create durable evidence. {completion_instruction} "
                            "Do not respond with analysis or a plan."
                        )
                    elif required_tools and not run_policy.allow_text_final_after_tools:
                        current_message += (
                            "**MANDATORY ACTION**: A text-only response cannot complete this role. "
                            f"Call {required_tools} now using its registered schema. "
                            "Do not call any tool that is not registered for this role."
                        )
                    else:
                        current_message += (
                            "**MANDATORY ACTION**: Call an available tool now to make concrete progress. "
                            "Do not respond with analysis or a plan."
                        )
                else:
                    logger.warning(
                        "Attempting to redirect model again because no tool calls were detected in last execution loop."
                    )
                    if run_policy.actionless_mode == "task_progress":
                        required_tools = ", ".join(sorted(run_policy.required_tool_names))
                        completion_instruction = (
                            f"Use the required completion tool(s) ({required_tools}) only when their prerequisites "
                            "are complete."
                            if required_tools
                            else "Follow the controller's recovery guidance and stay within the assigned task."
                        )
                        current_message += (
                            "**MANDATORY ACTION**: Resume the same assigned task and call the next registered tool "
                            "that addresses its unmet acceptance criteria. Preserve completed work and durable "
                            f"evidence. {completion_instruction} Do not provide more analysis."
                        )
                    elif not run_policy.allow_text_final_after_tools:
                        required_tools = ", ".join(sorted(run_policy.required_tool_names)) or "an available tool"
                        current_message += (
                            "**MANDATORY ACTION**: A text-only response cannot complete this role. "
                            f"Call {required_tools} now using its registered schema. Do not provide more analysis."
                        )
                    else:
                        current_message += (
                            "**MANDATORY ACTION**: Continue only the assigned workflow role and make progress now. "
                            "Call an available tool if tool progress is required; otherwise provide the final answer "
                            "for this role."
                        )

        except StopIteration as error:
            logger.debug("Agent cycle completed: %s", str(error))
            if agent_callback_handler and agent_callback_handler.has_reached_limit():
                print_status("Step limit reached", "SUCCESS")
                raise BudgetLimitReached("Step limit reached")

        except Exception as error:
            error_str = str(error).lower()
            if isinstance(error, MaxTokensReachedException) or "maxtokensreached" in error_str or "max_tokens" in error_str:
                raise
            if isinstance(error, BudgetLimitReached) or "budget limit" in error_str:
                raise BudgetLimitReached(str(error))
            if is_recoverable_agent_error(error):
                recoverable_attempt_count += 1
                logger.debug("Recoverable provider/network exception", exc_info=error)
                if recoverable_attempt_count <= recoverable_retries:
                    print_status(
                        f"Network/provider timeout - retrying ({recoverable_attempt_count}/{recoverable_retries})",
                        "WARNING",
                    )
                    time.sleep(min(2 ** recoverable_attempt_count, 10))
                    continue

                print_status("Network/provider timeout - generating final report", "WARNING")
                if agent_callback_handler:
                    agent_callback_handler.emit_termination(
                        "network_timeout",
                        "Provider/network timeout detected. Switching to final report.",
                    )
                return AgentRunResult("network_timeout", "Provider/network timeout detected")
            raise

    return AgentRunResult("interrupted", "")


def run_workflow_agent_with_max_token_recovery(
    *,
    agent: Any,
    prompt: str,
    run_policy: Optional[AgentRunPolicy],
    callback_handler: Any,
    initial_prompt: str,
    budget_cfg: BudgetConfig,
    operation_start: float,
    max_duration: int | None,
    logger: Any,
) -> AgentRunResult:
    """Run a workflow role with one safe, controller-directed executor recovery."""

    current_prompt = prompt
    max_token_recovery_attempts = 0
    while True:
        try:
            return run_agent_until_terminal_state(
                agent=agent,
                callback_handler=callback_handler,
                current_message=current_prompt,
                initial_prompt=initial_prompt,
                budget_cfg=budget_cfg,
                operation_start=operation_start,
                max_duration=max_duration,
                logger=logger,
                run_policy=run_policy,
            )
        except MaxTokensReachedException as error:
            classification, removed = classify_and_discard_max_token_output(agent)
            setattr(error, "max_token_classification", classification)
            repeated_pattern = is_repeated_max_token_pattern(agent, classification)
            role = str(getattr(agent, "_cyber_agent_type", "unknown"))
            output_limit = getattr(getattr(agent, "model", None), "_output_tokens", None)
            can_retry = role == "task_executor" and max_token_recovery_attempts < 1 and not repeated_pattern
            logger.warning(
                "MAX_TOKEN_RECOVERY role=%s classification=%s repetition_ratio=%.3f "
                "discarded_tokens=%s partial_removed=%s output_limit=%s attempt=%s "
                "repeated_pattern=%s action=%s",
                role,
                classification.kind,
                classification.repetition_ratio,
                classification.discarded_tokens,
                removed,
                output_limit,
                max_token_recovery_attempts + 1,
                repeated_pattern,
                "retry" if can_retry else "propagate",
            )
            if not can_retry:
                raise

            agent_callback = getattr(agent, "_cyber_callback_handler", None) or callback_handler
            journal = getattr(agent_callback, "tool_outcome_journal", None)
            completed_tools = [
                outcome.tool_name
                for outcome in (journal.entries() if journal is not None else [])
                if outcome.success
            ]
            current_prompt = build_task_executor_max_token_prompt(
                classification,
                completed_tools=completed_tools,
                required_tools=set(run_policy.required_tool_names) if run_policy else set(),
            )
            max_token_recovery_attempts += 1


def finalize_report_and_evaluation(
    *,
    agent: Any | None,
    callback_handler: Any,
    target: str,
    objective: str,
    module: str,
    logger: Any,
) -> None:
    """Generate final report and evaluation artifacts once."""
    if not callback_handler:
        logger.warning("No callback_handler available for evaluation trigger")
        return
    completion_status: dict[str, Any] | None = None
    try:
        try:
            plan = get_memory_client(silent=True).get_active_plan()
        except Exception as error:
            logger.warning("Unable to determine workflow completion before report generation: %s", error)
            plan = None
        completion_status = _build_report_completion_status(plan, callback_handler)
        callback_handler.ensure_report_generated(
            agent,
            target,
            objective,
            module,
            completion_status=completion_status,
        )
        logger.info("Triggering evaluation on completion")
        callback_handler.trigger_evaluation_on_completion()
    except Exception as error:
        logger.warning("Error in final report/evaluation: %s", error)
    finally:
        try:
            if completion_status is None:
                plan = get_memory_client(silent=True).get_active_plan()
                completion_status = _build_report_completion_status(plan, callback_handler)
            workflow_complete = bool(completion_status.get("workflow_complete"))
            termination_complete = completion_status.get("termination_reason") == "complete"
            if workflow_complete and termination_complete:
                callback_handler.emit_assessment_complete()
            else:
                logger.info(
                    "Skipping assessment_complete: workflow_complete=%s termination_reason=%s",
                    workflow_complete,
                    completion_status.get("termination_reason"),
                )
        except Exception as error:
            logger.warning("Unable to determine or emit assessment completion: %s", error)


def _build_report_completion_status(plan: Any, callback_handler: Any) -> dict[str, Any]:
    """Describe whether the final report is based on a completed workflow."""
    coordinator = getattr(callback_handler, "coordinator", None)
    termination_source = coordinator or callback_handler
    termination_reason = getattr(termination_source, "termination_reason", None)
    termination_message = getattr(termination_source, "termination_message", None)
    workflow_complete = bool(plan and getattr(plan, "assessment_complete", False))
    assessment_complete = workflow_complete and termination_reason == "complete"
    if assessment_complete:
        incomplete_reason = None
    elif not workflow_complete and termination_reason:
        incomplete_reason = (
            f"Workflow ended with termination_reason={termination_reason!r} before assessment_complete=true."
        )
    elif not workflow_complete:
        incomplete_reason = "Workflow ended before assessment_complete=true."
    else:
        incomplete_reason = (
            f"Workflow reached assessment_complete=true but termination_reason={termination_reason!r}."
        )
    health_snapshot = None
    health_provider = getattr(callback_handler, "operation_health_snapshot", None)
    if callable(health_provider):
        try:
            health_snapshot = health_provider()
        except Exception:
            logging.getLogger(__name__).debug(
                "Unable to include operation health in report completion status",
                exc_info=True,
            )
    return {
        "assessment_complete": assessment_complete,
        "workflow_complete": workflow_complete,
        "termination_reason": termination_reason,
        "termination_message": termination_message,
        "incomplete_reason": incomplete_reason,
        "unresolved_task_count": (
            health_snapshot.get("unresolved_task_count") if isinstance(health_snapshot, dict) else None
        ),
        "incomplete_phase_ids": (
            health_snapshot.get("incomplete_phase_ids") if isinstance(health_snapshot, dict) else []
        ),
    }


def close_log_outputs() -> None:
    """Close intercepted log streams when present."""
    if hasattr(sys.stdout, "close") and hasattr(sys.stdout, "log"):
        try:
            sys.stdout.close()
        except Exception:
            pass
    if hasattr(sys.stderr, "close") and hasattr(sys.stderr, "log"):
        try:
            sys.stderr.close()
        except Exception:
            pass


def cleanup_operation_resources(
    *,
    agent: Any | None,
    callback_handler: Any,
    args: Any,
    operation_id: str,
    operation_start: float,
    telemetry: Any,
    logger: Any,
) -> None:
    """Close operation resources and persist final report/evaluation state."""
    browser.close_browser()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(channel_close_all())
    loop.run_until_complete(close_oast_providers())
    loop.close()

    if interrupted:
        ui_mode = os.environ.get("CYBER_UI_MODE", "cli").lower()
        if ui_mode == "react":
            close_log_outputs()
            return
        print_status("Exiting immediately due to interrupt", "WARNING")
        close_log_outputs()
        os._exit(1)

    should_finalize = callback_handler is not None

    if should_finalize:
        finalize_report_and_evaluation(
            agent=agent,
            callback_handler=callback_handler,
            target=args.target,
            objective=args.objective,
            module=args.module,
            logger=logger,
        )

    if agent is not None:
        agent.cleanup()

    should_cleanup = not args.keep_memory and not args.memory_path

    if should_cleanup:
        try:
            target_name = sanitize_target_name(args.target)
            logger.debug("Calling clean_operation_memory with target_name=%s", target_name)
            clean_operation_memory(operation_id, target_name)
            logger.info("Memory cleaned up for operation %s", operation_id)
        except Exception as cleanup_error:
            logger.warning("Error cleaning up memory: %s", cleanup_error)
    else:
        logger.debug("Skipping cleanup - memory will be preserved")

    end_time = time.time()
    total_time = end_time - operation_start
    logger.info("Operation %s ended after %.2fs", operation_id, total_time)

    flush_traces(telemetry=telemetry)
    close_log_outputs()


def main():
    """Main execution function"""
    global interrupted

    # Set up signal handlers for Ctrl+C, Ctrl+Z, and SIGTERM (ESC in UI)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTSTP, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGUSR1, dumpstacks)

    # Suppress extra debugging from LiteLLM that is printed to stderr
    litellm.suppress_debug_info = True
    #litellm._turn_on_debug()

    # Check for service mode before normal argument parsing to avoid validation issues
    is_service_mode = "--service-mode" in sys.argv

    # Parse command line arguments first to get the confirmations flag
    parser = argparse.ArgumentParser(
        description="Cyber-AutoAgent - Autonomous Cybersecurity Assessment Tool",
        epilog="⚠️ Use only on authorized targets in safe environments ⚠️",
    )
    parser.add_argument(
        "--module",
        type=str,
        default="web",
        help="Security operational plugins to use (e.g., web, ctf, etc.)",
    )
    parser.add_argument(
        "--objective",
        type=str,
        required=not is_service_mode,
        help="Security assessment objective (required unless in service mode)",
    )
    parser.add_argument(
        "--target",
        type=str,
        required=not is_service_mode,
        help="Target system/network to assess (ensure you have permission!)",
    )
    parser.add_argument(
        "--service-mode",
        action="store_true",
        help="Run in service mode for containerized deployments (keeps container alive)",
    )
    # Unified budget flags
    parser.add_argument(
        "--max-duration",
        dest="max_duration",
        type=int,
        default=DEFAULT_MAX_DURATION,
        help="Maximum duration in minutes for the operation",
    )
    parser.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=None,
        help="Maximum total tokens (input+output+cache) budget",
    )
    parser.add_argument(
        "--max-cost",
        dest="max_cost",
        type=float,
        default=None,
        help="Maximum total cost budget (in provider currency, e.g., USD)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output with detailed debug logging",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model ID to use (defaults configured in defaults.py)",
    )
    parser.add_argument(
        "--region",
        type=str,
        default="us-east-1",
        help="AWS region for Bedrock (default: from AWS_REGION or us-east-1)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["bedrock", "ollama", "litellm", "gemini"],
        default=os.getenv("CYBER_AGENT_PROVIDER", "bedrock"),
        help="Model provider: 'bedrock' for AWS Bedrock, 'ollama' for local models, 'litellm' for universal access (default: from CYBER_AGENT_PROVIDER or bedrock)",
    )
    parser.add_argument(
        "--confirmations",
        action="store_true",
        help="Enable tool confirmation prompts (default: disabled)",
    )
    parser.add_argument(
        "--memory-path",
        type=str,
        help="Path to existing FAISS memory store to load past memories (e.g., /outputs/target_name/OP_20240320_101530)",
    )
    parser.add_argument(
        "--memory-mode",
        type=str,
        choices=["auto", "fresh"],
        default="fresh" if os.getenv("MEMORY_ISOLATION") == "operation" else "auto",
        help="Memory initialization mode: 'auto' loads existing memory if found, 'fresh' starts with new memory",
    )
    parser.add_argument(
        "--keep-memory",
        action="store_true",
        default=True,
        help="Keep memory data after operation completes (default: true)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Base directory for output artifacts (default: ./outputs)",
    )
    parser.add_argument(
        "--continue",
        dest="cont",
        nargs="?",
        type=str,
        const=True,
        help="Continue last operation or the passed operation",
    )
    parser.add_argument(
        "--report",
        nargs="?",
        type=str,
        const=True,
        help="Generate report (without execution) of the last operation or the passed operation",
    )
    parser.add_argument(
        "--eval-rubric",
        action="store_true",
        help="Enable rubric-based evaluation in addition to Ragas metrics",
    )
    parser.add_argument(
        "--mcp-enabled",
        action="store_true",
        help="Enable MCP servers",
    )
    parser.add_argument(
        "--mcp-conns",
        type=str,
        help="Configure MCP servers, requires --mcp-enabled to be applied",
    )
    parser.add_argument(
        "--bug-bounty-header",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Add an HTTP header to mark authorized bug bounty traffic. "
            "May be supplied multiple times, e.g. "
            "--bug-bounty-header X-HackerOne-Research=username "
            "--bug-bounty-header User-Agent=username@wearehackerone.com"
        ),
    )
    parser.add_argument(
        "--heap-monitor",
        action="store_true",
        help="Monitor the heap for usage and trigger dumps when threshold exceeded",
    )

    args = parser.parse_args()

    if args.heap_monitor or os.getenv("CYBER_HEAP_MONITOR", "").lower() == "true":
        # side effect is the heap monitor starts when imported
        importlib.import_module("src.modules.utils.heap_monitor")

    bug_bounty_headers = {}
    if not args.bug_bounty_header:
        env_headers = os.getenv("CYBER_BUG_BOUNTY_HEADERS")
        if env_headers:
            try:
                parsed_headers = json.loads(env_headers)
            except json.JSONDecodeError as exc:
                parser.error(f"CYBER_BUG_BOUNTY_HEADERS must be valid JSON: {exc}")
            if not isinstance(parsed_headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in parsed_headers.items()
            ):
                parser.error("CYBER_BUG_BOUNTY_HEADERS must be a JSON object with string keys and values")
            bug_bounty_headers.update(parsed_headers)

    for header in args.bug_bounty_header:
        if "=" not in header:
            parser.error("--bug-bounty-header must use NAME=VALUE")
        name, value = header.split("=", 1)
        name = name.strip()
        if not name:
            parser.error("--bug-bounty-header name cannot be empty")
        bug_bounty_headers[name] = value

    if args.bug_bounty_header:
        os.environ["CYBER_BUG_BOUNTY_HEADERS"] = json.dumps(bug_bounty_headers)

    if args.cont or args.report:
        args.memory_mode = "auto"

    ensure_workspace_marker_files()

    # React UI passes objective via environment variable
    # Only apply env override if in React UI mode to preserve CLI arg priority
    env_objective = os.environ.get("CYBER_OBJECTIVE")
    if env_objective and os.environ.get("CYBER_UI_MODE") == "react":
        args.objective = env_objective

    # Persist provider/model selections to environment for downstream configuration
    if args.provider:
        os.environ["CYBER_AGENT_PROVIDER"] = args.provider
    if args.model:
        os.environ["CYBER_AGENT_LLM_MODEL"] = args.model

    # Handle service mode
    if args.service_mode:
        # If full parameters are provided (common when the app execs into the service
        # container with explicit args/env), auto-run a one-shot assessment instead of idling.
        has_params = bool(args.target and args.objective)
        ui_mode_env = os.environ.get("CYBER_UI_MODE", "").lower()
        auto_run = has_params and ui_mode_env == "react"

        if auto_run:
            print(
                "Service mode detected with parameters - running one-shot assessment."
            )
            # Fall through to normal execution path below
        else:
            print("Starting Cyber-AutoAgent in service mode...")
            print("Container will stay alive and wait for external requests.")

            # Keep the container alive
            try:
                while True:
                    ensure_workspace_marker_files()
                    time.sleep(30)  # Check every 30 seconds
                    # Health check endpoint implementation pending
            except KeyboardInterrupt:
                print("Service mode interrupted. Shutting down...")
                return
            except Exception as e:
                print(f"Service mode error: {e}")
                return

    if not args.confirmations:
        os.environ["BYPASS_TOOL_CONSENT"] = "true"
    else:
        # Remove the variable if confirmations are enabled
        os.environ.pop("BYPASS_TOOL_CONSENT", None)

    os.environ["DEV"] = "true"

    if "OLLAMA_HOST" in os.environ and not os.environ.get("OLLAMA_API_BASE", ""):
        # Set OLLAMA_API_BASE for LiteLLM
        os.environ["OLLAMA_API_BASE"] = os.environ["OLLAMA_HOST"]

    # Provide a safer default for shell command timeouts unless user overrides
    if not os.environ.get("SHELL_DEFAULT_TIMEOUT"):
        # Many external tools (e.g., nmap, curl to slow hosts) can exceed low defaults
        # Use a safer default to reduce spurious timeouts while keeping responsiveness
        os.environ["SHELL_DEFAULT_TIMEOUT"] = "600"

    # Get centralized region configuration if not provided
    if args.region is None:
        config_manager = get_config_manager()
        args.region = config_manager.get_default_region()

    os.environ["AWS_REGION"] = args.region

    # Get configuration from ConfigManager with CLI overrides
    config_manager = get_config_manager()
    config_overrides = {}
    if args.output_dir:
        config_overrides["output_dir"] = args.output_dir
    # Always enable unified output system
    config_overrides["enable_unified_output"] = True
    if args.model:
        config_overrides["model_id"] = args.model
    # MCP overrides
    if args.mcp_enabled:
        config_overrides["mcp_enabled"] = True
    if args.mcp_conns:
        config_overrides["mcp_conns"] = args.mcp_conns

    # Toggle rubric evaluation via CLI flag
    if args.eval_rubric:
        os.environ["EVAL_RUBRIC_ENABLED"] = "true"

    # Operation ID
    target_sanitized = sanitize_target_name(args.target)
    operation_id = None
    if isinstance(args.cont, str) and args.cont:
        operation_id = args.cont
    elif isinstance(args.report, str) and args.report:
        operation_id = args.report
    elif (isinstance(args.cont, bool) and args.cont) or (isinstance(args.report, bool) and args.report):
        # get the last operation
        base_dir = os.path.abspath(
            args.output_dir
            or os.getenv("CYBER_AGENT_OUTPUT_DIR")
            or get_default_base_dir()
        )
        previous_operations = list(filter(
            lambda d: d.is_dir() and d.name.startswith("OP_"),
            os.scandir(os.path.join(base_dir, target_sanitized))))
        previous_operations.sort(key=lambda e: e.name, reverse=True)
        if previous_operations:
            operation_id = previous_operations[0].name

    if operation_id is None:
        operation_id = f"OP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    config_overrides["operation_id"] = operation_id
    config_overrides["target_name"] = args.target

    # Expose operation ID to tools via environment for consistent evidence tagging
    os.environ["CYBER_OPERATION_ID"] = operation_id

    server_config = config_manager.get_server_config(args.provider, **config_overrides)

    # Set mem0 environment variables based on configuration
    os.environ["MEM0_LLM_PROVIDER"] = server_config.memory.llm.provider.value
    os.environ["MEM0_LLM_MODEL"] = server_config.memory.llm.model_id
    os.environ["MEM0_EMBEDDING_MODEL"] = server_config.embedding.model_id

    mcp_config = config_manager.get_mcp_config(args.provider, **config_overrides)
    if mcp_config.enabled:
        mcp_connections = list(filter(lambda c: '*' in c.plugins or args.module in c.plugins, mcp_config.connections))
    else:
        mcp_connections = []

    # Initialize logger using unified output system
    log_path = get_output_path(
        sanitize_target_name(args.target),
        operation_id,
        "",
        server_config.output.base_dir,
    )
    log_file = os.path.join(log_path, "cyber_operations.log")

    # Enable verbose logging in React mode to capture debug information
    ui_mode = os.environ.get("CYBER_UI_MODE", "cli").lower()
    verbose_mode = bool(
        args.verbose
        or ui_mode == "react"
        or os.environ.get("CYBER_DEBUG", "").lower() == "true"
    )
    logger = setup_logging(log_file=log_file, verbose=verbose_mode)

    latest_pointer = update_latest_output_pointer(
        target_sanitized,
        operation_id,
        server_config.output.base_dir,
    )
    if latest_pointer.success:
        logger.info(
            "Latest output pointer updated: %s (%s)",
            latest_pointer.pointer_path,
            latest_pointer.mode,
        )
    else:
        logger.warning(
            "Latest output pointer not updated: %s",
            latest_pointer.message,
        )

    # Setup telemetry (always enabled for token counting) and observability (deployment-aware)
    telemetry = setup_telemetry(logger)

    # Configure SDK logging based on verbose mode
    from modules.config.system.logger import configure_sdk_logging
    configure_sdk_logging(enable_debug=verbose_mode)

    # Suppress benign OpenTelemetry context cleanup errors that occur during normal operation
    # These happen when async generators are terminated and don't affect functionality
    import logging as stdlib_logging

    otel_logger = stdlib_logging.getLogger("opentelemetry.context")
    otel_logger.setLevel(stdlib_logging.CRITICAL)

    # Register cleanup function to properly close log files
    def cleanup_logging():
        """Ensure log files are properly closed on exit"""
        try:
            # Write session end marker before closing (skip in React mode)
            if os.environ.get("CYBER_UI_MODE", "cli").lower() != "react":
                width = get_terminal_width()
                print("\n" + "=" * width)
                print(
                    f"CYBER-AUTOAGENT SESSION ENDED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                print("=" * width + "\n")
        except Exception:
            pass

        if hasattr(sys.stdout, "close") and callable(sys.stdout.close):
            try:
                sys.stdout.close()
            except Exception:
                pass
        if hasattr(sys.stderr, "close") and callable(sys.stderr.close):
            try:
                sys.stderr.close()
            except Exception:
                pass

    atexit.register(cleanup_logging)

    # Configure rate limiting
    configure_model_rate_limits(provider=config_manager.get_provider())

    if os.environ.get("CYBERAGENT_NO_BANNER", "").lower() not in ("1", "true", "yes"):
        print_banner()

        # Safety warning (only show with banner)
        print_section(
            "⚠️  SAFETY WARNING",
            f"""
{Colors.RED}{Colors.BOLD}EXPERIMENTAL SOFTWARE - AUTHORIZED USE ONLY{Colors.RESET}

• This tool is for {Colors.BOLD}authorized security testing only{Colors.RESET}
• Use only in {Colors.BOLD}safe, sandboxed environments{Colors.RESET}
• Ensure you have {Colors.BOLD}explicit written permission{Colors.RESET} for target testing
• Users are {Colors.BOLD}fully responsible{Colors.RESET} for compliance with applicable laws
• Misuse may result in {Colors.BOLD}legal consequences{Colors.RESET}

{Colors.GREEN}✓{Colors.RESET} I understand and accept these terms before proceeding.
""",
            Colors.RED,
            "⚠️",
        )

    # Auto-setup and environment discovery
    # Pass memory_path to auto_setup to skip cleanup if using existing memory
    available_tools = auto_setup(skip_mem0_cleanup=bool(args.memory_path))

    logger.info("Operation %s initiated", operation_id)
    logger.info("Objective: %s", args.objective)
    logger.info("Target: %s", args.target)
    logger.info(
        "Budget: duration=%sm, tokens=%s, cost=%s",
        str(args.max_duration) if args.max_duration is not None else "unset",
        str(args.max_tokens) if args.max_tokens is not None else "unset",
        str(args.max_cost) if args.max_cost is not None else "unset",
    )
    logger.info("Provider: %s", args.provider)
    logger.info("Model: %s", server_config.llm.model_id)
    logger.info("Temperature: %s", server_config.llm.temperature)
    # FIXME: set server_config.llm.max_tokens earlier, this isn't the real max tokens
    logger.info("Max tokens: %d", server_config.llm.max_tokens)
    if server_config.llm.top_p is not None:
        logger.info("Top P: %s", server_config.llm.top_p)

    # Log extended parameters from environment (model-agnostic)
    thinking_budget = os.getenv("THINKING_BUDGET")
    reasoning_effort = os.getenv("REASONING_EFFORT")
    max_completion = os.getenv("MAX_COMPLETION_TOKENS")

    if thinking_budget:
        logger.info("Thinking budget: %s", thinking_budget)
    if reasoning_effort:
        logger.info("Reasoning effort: %s", reasoning_effort)
    if max_completion:
        logger.info("Max completion tokens: %s", max_completion)

    # Display operation details with unified output information
    output_base_path = get_output_path(
        target_sanitized, operation_id, "", server_config.output.base_dir
    )
    # Keep relative tool paths inside the operation workspace.
    os.makedirs(output_base_path, exist_ok=True, mode=0o775)
    os.chdir(output_base_path)

    # Prepare path display based on environment
    if is_docker():
        output_path_display = f"{output_base_path}\n{Colors.BOLD}Host Path:{Colors.RESET}     {output_base_path.replace('/app/outputs', './outputs')}"
    else:
        output_path_display = output_base_path

    if os.environ.get("CYBER_UI_MODE", "cli").lower() != "react":
        print_section(
            "MISSION PARAMETERS",
            f"""
{Colors.BOLD}Operation ID:{Colors.RESET} {Colors.CYAN}{operation_id}{Colors.RESET}
{Colors.BOLD}Objective:{Colors.RESET}    {Colors.YELLOW}{args.objective}{Colors.RESET}
{Colors.BOLD}Target:{Colors.RESET}       {Colors.RED}{args.target}{Colors.RESET} (sanitized: {target_sanitized})
{Colors.BOLD}Budget:{Colors.RESET}        duration={args.max_duration}m, tokens={args.max_tokens or '—'}, cost={args.max_cost or '—'}
{Colors.BOLD}Environment:{Colors.RESET} {len(available_tools)} existing cyber tools available
{Colors.BOLD}MCP:{Colors.RESET}          {len(mcp_connections)} server(s) available
{Colors.BOLD}Output Path:{Colors.RESET}  {output_path_display}
""",
            Colors.CYAN,
            "🎯",
        )

    # Initialize timing
    operation_start = time.time()
    callback_handler: Optional[AgentEventHandler] = None
    agent = None

    print(f"\n{Colors.DIM}{'─' * 80}{Colors.RESET}\n")

    try:
        # Initial user message to start the agent
        initial_prompt = f"Conduct security assessment of {args.target} for: {args.objective}"

        # Expose at module level for tests patching cyberautoagent.get_initial_prompt
        globals()["get_initial_prompt"] = lambda: initial_prompt

        print_status("Cyber-AutoAgent online and starting", "SUCCESS")

        budget_cfg = BudgetConfig(
            max_duration_minutes=int(args.max_duration) if args.max_duration is not None else 0,
            max_tokens=int(args.max_tokens) if args.max_tokens is not None else None,
            max_cost=float(args.max_cost) if args.max_cost is not None else None,
        )

        # Create agent
        logger.info(
            "Creating agent with budget: duration=%sm, tokens=%s, cost=%s",
            budget_cfg.max_duration_minutes,
            budget_cfg.max_tokens if budget_cfg.max_tokens is not None else "—",
            budget_cfg.max_cost if budget_cfg.max_cost is not None else "—",
        )
        config = AgentConfig(
            target=args.target,
            objective=args.objective,
            budget=budget_cfg,
            available_tools=available_tools,
            op_id=operation_id,
            model_id=args.model,
            region_name=args.region,
            provider=args.provider,
            memory_path=args.memory_path,
            memory_mode=args.memory_mode,
            module=args.module,
            bug_bounty_headers=bug_bounty_headers,
            mcp_connections=mcp_connections,
        )
        runtime_resources = create_agent_runtime_resources(
            target=args.target,
            objective=args.objective,
            config=config,
        )
        callback_handler = runtime_resources.callback_handler

        if not bool(args.report):
            def run_workflow_agent(
                agent: Any,
                prompt: str,
                run_policy: Optional[AgentRunPolicy],
            ) -> str:
                try:
                    message_start = len(agent.messages)
                except (AttributeError, TypeError):
                    message_start = 0
                result = run_workflow_agent_with_max_token_recovery(
                    agent=agent,
                    prompt=prompt,
                    run_policy=run_policy,
                    callback_handler=callback_handler,
                    initial_prompt=initial_prompt,
                    budget_cfg=budget_cfg,
                    operation_start=operation_start,
                    max_duration=args.max_duration,
                    logger=logger,
                )
                try:
                    current_pass_messages = list(agent.messages)[message_start:]
                except (AttributeError, TypeError):
                    current_pass_messages = []
                assistant_text = extract_last_assistant_text(current_pass_messages)
                if assistant_text:
                    return assistant_text
                if run_policy and result.reason == run_policy.terminal_reason:
                    return ""
                return result.message or result.reason

            def workflow_work_runner(
                role: str,
                prompt: str,
                tools: list[Any],
                system_prompt: str,
                run_policy: Optional[AgentRunPolicy] = None,
            ) -> str:
                agent = create_agent(
                    target=args.target,
                    objective=args.objective,
                    config=config,
                    runtime_resources=runtime_resources,
                    system_prompt=system_prompt,
                    tools=tools,
                    name=f"Cyber-AutoAgent {operation_id} {role}",
                    agent_type=role,
                    include_tool_catalog=role != "task_creator",
                )
                try:
                    return run_workflow_agent(agent, prompt, run_policy)
                finally:
                    try:
                        agent.cleanup()
                    except Exception as error:
                        logger.warning("Unable to clean up role agent %s: %s", role, error)

            @contextmanager
            def workflow_executor_session(role: str, tools: list[Any], system_prompt: str):
                agent = create_agent(
                    target=args.target,
                    objective=args.objective,
                    config=config,
                    runtime_resources=runtime_resources,
                    system_prompt=system_prompt,
                    tools=tools,
                    name=f"Cyber-AutoAgent {operation_id} {role}",
                    agent_type=role,
                    include_tool_catalog=role != "task_creator",
                )
                try:
                    callback = getattr(agent, "_cyber_callback_handler", None)
                    recovery_hook = getattr(agent, "_cyber_failure_recovery_hook", None)

                    def run_retained_executor(
                        prompt: str,
                        run_policy: Optional[AgentRunPolicy] = None,
                    ) -> TaskExecutorCycleResult:
                        journal = getattr(callback, "tool_outcome_journal", None)
                        snapshot = journal.snapshot() if journal is not None else 0
                        try:
                            text = run_workflow_agent(agent, prompt, run_policy)
                        except MaxTokensReachedException as error:
                            classification = getattr(error, "max_token_classification", None)
                            kind = getattr(classification, "kind", "output_truncation")
                            outcomes = journal.since(snapshot) if journal is not None else []
                            role_label = "Task creator" if role == "task_creator" else "Task executor"
                            return TaskExecutorCycleResult(
                                text="",
                                outcomes=outcomes,
                                max_tokens_exhausted=True,
                                max_tokens_reason=(
                                    f"{role_label} repeated the same reasoning loop after its bounded recovery."
                                    if kind == "reasoning_loop"
                                    else f"{role_label} reached its output-token limit after its bounded recovery."
                                ),
                            )
                        outcomes = journal.since(snapshot) if journal is not None else []
                        return TaskExecutorCycleResult(
                            text=text,
                            outcomes=outcomes,
                            recovery_required=bool(recovery_hook and recovery_hook.unresolved),
                            recovery_exhausted=bool(recovery_hook and recovery_hook.exhausted),
                            recovery_guidance=_recovery_guidance_with_failed_command_help(
                                recovery_hook,
                                config.available_tools or [],
                            ),
                        )

                    yield run_retained_executor
                finally:
                    try:
                        agent.cleanup()
                    except Exception as error:
                        logger.warning("Unable to clean up role agent %s: %s", role, error)

            workflow = MultiAgentWorkflowController(
                runtime=runtime_resources,
                budget=budget_cfg,
                work_runner=workflow_work_runner,
                executor_session_factory=workflow_executor_session,
            )

            try:
                workflow.run()
            except BudgetLimitReached:
                print_status("Budget limit reached", "SUCCESS")
                logger.debug("Budget limit reached - terminating gracefully")
                if callback_handler and not callback_handler.termination_emitted:
                    callback_handler.emit_termination(
                        "budget_limit",
                        "Operation budget limit reached. Switching to final report.",
                    )
            except MaxTokensReachedException as error:
                print_status("Token limit reached - generating final report", "WARNING")
                logger.debug("Termination exception", exc_info=error)
                try:
                    if callback_handler:
                        callback_handler.emit_termination(
                            "max_tokens",
                            "Model token limit reached. Switching to final report.",
                        )
                        try:
                            plan = get_memory_client(silent=True).get_active_plan()
                        except Exception:
                            plan = None
                        callback_handler.ensure_report_generated(
                            None,
                            args.target,
                            args.objective,
                            args.module,
                            completion_status=_build_report_completion_status(plan, callback_handler),
                        )
                except Exception as max_tokens_finish_error:
                    logger.error("Failed to complete for token limit error", exc_info=max_tokens_finish_error)
            except WorkflowInvariantError as error:
                logger.exception("Workflow invariant error occurred", exc_info=error)
                termination_reason = str(error)
                print_status(f"Agent error: {termination_reason}", "ERROR")
                if callback_handler:
                    callback_handler.emit_termination("error", termination_reason)
                raise
            except Exception as error:
                logger.exception("Unexpected agent error occurred", exc_info=error)
                termination_reason = str(error)
                print_status(f"Agent error: {termination_reason}", "ERROR")
                if callback_handler:
                    callback_handler.emit_termination("error", termination_reason)
                raise
        execution_time = time.time() - operation_start
        logger.info("Operation completed in %.2f seconds", execution_time)

        # Display operation results (suppressed in React mode where handler emits UI events)
        if os.environ.get("CYBER_UI_MODE", "cli").lower() != "react":
            print(f"\n{'=' * 80}")
            print(f"{Colors.BOLD}OPERATION SUMMARY{Colors.RESET}")
            print(f"{'=' * 80}")

        # Generate operation summary
        if callback_handler:
            summary = callback_handler.get_summary()
            elapsed_time = time.time() - operation_start
            minutes = int(elapsed_time // 60)
            seconds = int(elapsed_time % 60)

            # Display summary in terminal mode only
            if os.environ.get("CYBER_UI_MODE", "cli").lower() != "react":
                print(
                    f"{Colors.BOLD}Operation ID:{Colors.RESET}      {operation_id}"
                )

                # Determine status based on completion
                if callback_handler.termination_reason == "complete":
                    status_text = f"{Colors.GREEN}Objective Achieved{Colors.RESET}"
                elif callback_handler.has_reached_limit():
                    status_text = f"{Colors.YELLOW}Step Limit Reached{Colors.RESET}"
                elif callback_handler.termination_reason == "user_abort":
                    status_text = f"{Colors.YELLOW}Operation Cancelled{Colors.RESET}"
                elif callback_handler.termination_reason == "stalled":
                    status_text = f"{Colors.RED}Operation Stalled{Colors.RESET}"
                elif callback_handler.termination_reason == "max_tokens":
                    status_text = f"{Colors.RED}Model Token Limit Reached{Colors.RESET}"
                elif callback_handler.termination_reason == "network_timeout":
                    status_text = f"{Colors.RED}Network Timeout / Rate Limit Reached{Colors.RESET}"
                elif callback_handler.termination_reason == "error":
                    status_text = f"{Colors.RED}Agent Error Occurred{Colors.RESET}"
                elif args.report:
                    status_text = f"{Colors.BLUE}Regenerate Report{Colors.RESET}"
                else:
                    status_text = f"{Colors.GREEN}Operation Completed{Colors.RESET}"

                print(f"{Colors.BOLD}Status:{Colors.RESET}            {status_text}")
                print(
                    f"{Colors.BOLD}Duration:{Colors.RESET}          {minutes}m {seconds}s"
                )

                print(f"\n{Colors.BOLD}Execution Metrics:{Colors.RESET}")
                print(f"  • Duration: {summary.get('duration', 'unknown')}")
                print(f"  • Tools Created: {summary['tools_created']}")
                print(f"  • Evidence Collected: {summary['evidence_collected']} items")
                print(f"  • Memory Operations: {summary['memory_operations']} total")

                if summary["capability_expansion"]:
                    print(f"\n{Colors.BOLD}Capabilities Created:{Colors.RESET}")
                    for tool in summary["capability_expansion"]:
                        print(f"  • {Colors.GREEN}{tool}{Colors.RESET}")

            # Show where evidence and memories are stored
            # Determine memory location based on backend and unified output structure
            # FIXME: memory_location should be returned by the initialized memory system, not duplicated here
            target_name = sanitize_target_name(args.target)
            if os.getenv("MEM0_API_KEY"):
                memory_location = "Mem0 Platform (cloud)"
            elif os.getenv("OPENSEARCH_HOST"):
                memory_location = f"OpenSearch: {os.getenv('OPENSEARCH_HOST')}"
            else:
                memory_location = f"{get_default_base_dir()}/{target_name}/memory"

            # Use unified output paths for evidence storage
            evidence_location = get_output_path(
                sanitize_target_name(args.target),
                operation_id,
                "",  # No subdirectory - show the operation root
                server_config.output.base_dir,
            )

            # Display output paths in terminal mode
            if os.environ.get("CYBER_UI_MODE", "cli").lower() != "react":
                if is_docker():
                    # Docker environment: show both container and host paths
                    host_evidence_location = evidence_location.replace(
                        "/app/outputs", "./outputs"
                    )
                    host_memory_location = memory_location.replace(
                        "./outputs", "./outputs"
                    )
                    print(
                        f"\n{Colors.BOLD}Outputs stored in:{Colors.RESET}"
                        f"\n  {Colors.DIM}Container:{Colors.RESET} {evidence_location}"
                        f"\n  {Colors.GREEN}Host:{Colors.RESET} {host_evidence_location}"
                    )
                    print(
                        f"{Colors.BOLD}Memory stored in:{Colors.RESET}"
                        f"\n  {Colors.DIM}Container:{Colors.RESET} {memory_location}"
                        f"\n  {Colors.GREEN}Host:{Colors.RESET} {host_memory_location}"
                    )
                else:
                    # Local environment: show direct paths
                    print(
                        f"\n{Colors.BOLD}Outputs stored in:{Colors.RESET} {evidence_location}"
                    )
                    print(
                        f"{Colors.BOLD}Memory stored in:{Colors.RESET} {memory_location}"
                    )
                print(f"{'=' * 80}")

    except KeyboardInterrupt:
        ui_mode = os.environ.get("CYBER_UI_MODE", "cli").lower()
        if ui_mode == "react":
            # Emit a structured termination event so the UI shows a clear end-of-operation
            try:
                if callback_handler:
                    callback_handler.emit_termination(
                        "user_abort", "Operation cancelled by user"
                    )  # noqa: SLF001
            except Exception:
                pass
        else:
            print_status("\nOperation cancelled by user", "WARNING")

        # Exit gracefully to allow event flushing and frontend to handle "stopped" state
        # Use 130 (SIGINT) to indicate an intentional interrupt
        sys.exit(130)

    except Exception as e:
        logger.exception("Operation failed")
        termination_reason = str(e)
        print_status(f"\nOperation failed: {termination_reason}", "ERROR")
        try:
            if callback_handler and not isinstance(e, WorkflowInvariantError):
                callback_handler.emit_termination("error", termination_reason)
        except Exception:
            pass
        sys.exit(1)

    finally:
        cleanup_operation_resources(
            agent=agent if "agent" in locals() else None,
            callback_handler=callback_handler,
            args=args,
            operation_id=operation_id,
            operation_start=operation_start,
            telemetry=telemetry,
            logger=logger,
        )


def ensure_workspace_marker_files():
    for p in [Path("/"), Path("/tmp"), Path("/var/tmp"), Path("/app/outputs")]:
        if p.is_dir() and os.access(p, os.W_OK):
            for f in [p / "THIS IS THE WORKSPACE.txt", p / "THIS IS _NOT_ THE TARGET.txt"]:
                try:
                    f.write_text("This is the operation workspace, NOT the target.")
                except Exception:
                    pass


if __name__ == "__main__":
    main()
