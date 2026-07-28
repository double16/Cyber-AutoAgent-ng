"""Stop bounded workflow roles immediately after their terminal tool succeeds."""

from __future__ import annotations

import json
from typing import Any

from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

from modules.handlers.tool_recovery import _result_success, _result_text

TERMINAL_TOOL_COMPLETED_STATE_KEY = "terminal_tool_completed"
TERMINAL_TOOL_REJECTED_STATE_KEY = "terminal_tool_rejected"


class TerminalToolHook(HookProvider):
    """End the current Strands event loop when a role's durable terminal action completes."""

    def __init__(self, agent_type: str) -> None:
        self.agent_type = agent_type

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        tool_name = str(event.tool_use.get("name", ""))
        expected_tool = {
            "task_creator": "create_tasks",
            "task_executor": "record_task_acceptance",
        }.get(self.agent_type)
        if tool_name != expected_tool:
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
