"""Stop bounded workflow roles immediately after their terminal tool succeeds."""

from __future__ import annotations

import json
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from modules.agents.run_policy import AgentRunPolicy
from modules.handlers.tool_recovery import _result_success, _result_text

TERMINAL_TOOL_COMPLETED_STATE_KEY = "terminal_tool_completed"
TERMINAL_TOOL_REJECTED_STATE_KEY = "terminal_tool_rejected"
POLICY_TERMINAL_STATE_KEY = "policy_terminal"


class TerminalToolHook(HookProvider):
    """End the current Strands event loop when a role's durable terminal action completes."""

    def __init__(self, agent_type: str) -> None:
        self.agent_type = agent_type
        self.policy: AgentRunPolicy | None = None
        self.successful_tools: set[str] = set()
        self.called_tools: set[str] = set()
        self.tool_count = 0

    def configure(self, policy: AgentRunPolicy) -> None:
        """Install invocation-local completion criteria and reset prior completions."""
        self.policy = policy
        self.successful_tools.clear()
        self.called_tools.clear()
        self.tool_count = 0

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool)
        registry.add_callback(BeforeToolCallEvent, self._before_tool)

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        """Cancel queued calls after a terminal result in this sequential batch."""
        state = event.invocation_state.get("request_state")
        if isinstance(state, dict) and any(
            key in state
            for key in (
                POLICY_TERMINAL_STATE_KEY,
                TERMINAL_TOOL_COMPLETED_STATE_KEY,
                TERMINAL_TOOL_REJECTED_STATE_KEY,
            )
        ):
            event.cancel_tool = "Worker completed; return control to the workflow controller."

    def _policy_terminal(self, event: AfterToolCallEvent, tool_name: str) -> bool:
        policy = self.policy
        if policy is None:
            return False
        self.called_tools.add(tool_name)
        if tool_name not in policy.ignored_terminal_tool_names:
            self.tool_count += 1
        if _result_success(event.result, event.exception):
            self.successful_tools.add(tool_name)
        reason = policy.terminal_check() if policy.terminal_check is not None else None
        # A controller predicate is authoritative when supplied: tool availability
        # or a successful tool status alone cannot establish functional completion.
        if policy.terminal_check is None and policy.terminal_after_required_tools and policy.required_tool_names:
            completed = self.successful_tools if policy.require_successful_required_tools else self.called_tools
            matches = (name in completed for name in policy.required_tool_names)
            satisfied = any(matches) if policy.required_tool_match == "any" else all(matches)
            if satisfied and self.tool_count >= policy.min_tool_calls:
                reason = policy.terminal_reason
        if not reason:
            return False
        state = event.invocation_state.setdefault("request_state", {})
        if isinstance(state, dict):
            state["stop_event_loop"] = True
            state[POLICY_TERMINAL_STATE_KEY] = {"reason": reason}
        return True

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        tool_name = str(event.tool_use.get("name", ""))
        expected_tool = {
            "task_creator": "create_tasks",
            "task_executor": "record_task_acceptance",
        }.get(self.agent_type)
        if tool_name != expected_tool or (self.policy is not None and self.policy.terminal_check is not None):
            self._policy_terminal(event, tool_name)
            return
        if not _result_success(event.result, event.exception):
            self._record_terminal_failure(event, tool_name)
            return
        try:
            payload: Any = json.loads(_result_text(event.result))
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict) or payload.get("complete") is not True:
            return
        request_state = event.invocation_state.setdefault("request_state", {})
        if isinstance(request_state, dict):
            request_state["stop_event_loop"] = True
            request_state[TERMINAL_TOOL_COMPLETED_STATE_KEY] = {"tool_name": tool_name}

    def _record_terminal_failure(self, event: AfterToolCallEvent, tool_name: str) -> None:
        if self.agent_type not in {"task_creator", "task_executor"}:
            return
        request_state = event.invocation_state.setdefault("request_state", {})
        if isinstance(request_state, dict):
            request_state["stop_event_loop"] = True
            request_state[TERMINAL_TOOL_REJECTED_STATE_KEY] = {
                "tool_name": tool_name,
                "error": _result_text(event.result),
            }
