"""Bound repeated identical tool calls within one agent invocation."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolGenerator, ToolResult, ToolSpec, ToolUse

from modules.config.system.logger import get_logger

logger = get_logger("Handlers.ToolRepeatGuard")

DEFAULT_TOOL_REPEAT_THRESHOLD = 3
REPEATED_TOOL_LOOP_STATE_KEY = "repeated_tool_loop"
_INVOCATION_STATE_KEY = "_cyber_tool_repeat_guard"
_GUIDANCE = (
    "Identical tool call suppressed by the loop guard. The preceding content is the most recent completed result. "
    "Do not repeat this call with unchanged arguments; continue using this result or change the arguments."
)


@dataclass
class _InvocationRepeatState:
    """Mutable state shared by tool callbacks in one Strands invocation."""

    fingerprint: str = ""
    streak: int = 0
    suppressed: int = 0
    completed_fingerprint: str = ""
    completed_result: ToolResult | None = None
    call_fingerprints: dict[str, str] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


class _CachedResultTool(AgentTool):
    """Internal tool replacement that returns a prior result without side effects."""

    def __init__(self, selected_tool: AgentTool, result: ToolResult, repeat_count: int) -> None:
        super().__init__()
        self._selected_tool = selected_tool
        self._result = copy.deepcopy(result)
        self._repeat_count = repeat_count

    @property
    def tool_name(self) -> str:
        return self._selected_tool.tool_name

    @property
    def tool_spec(self) -> ToolSpec:
        return self._selected_tool.tool_spec

    @property
    def tool_type(self) -> str:
        return self._selected_tool.tool_type

    async def stream(self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any) -> ToolGenerator:
        del invocation_state, kwargs
        result = copy.deepcopy(self._result)
        result.pop("_toolUseId", None)  # type: ignore[misc]
        result["toolUseId"] = str(tool_use.get("toolUseId", ""))
        content = list(result.get("content", []))
        content.append({"text": f"[Loop guard repeat {self._repeat_count}] {_GUIDANCE}"})
        result["content"] = content
        yield ToolResultEvent(result)


def _json_fallback(value: Any) -> dict[str, str]:
    """Return a deterministic, type-preserving representation for unusual inputs."""

    value_type = type(value)
    return {
        "type": f"{value_type.__module__}.{value_type.__qualname__}",
        "value": str(value),
    }


def _fingerprint(tool_use: ToolUse) -> str:
    payload = {
        "input": tool_use.get("input", {}),
        "name": str(tool_use.get("name", "")),
    }
    canonical = json.dumps(
        payload,
        default=_json_fallback,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tool_use_id(tool_use: ToolUse) -> str:
    return str(tool_use.get("_toolUseId", tool_use.get("toolUseId", "")))


def _get_state(invocation_state: dict[str, Any]) -> _InvocationRepeatState:
    state = invocation_state.get(_INVOCATION_STATE_KEY)
    if isinstance(state, _InvocationRepeatState):
        return state
    state = _InvocationRepeatState()
    invocation_state[_INVOCATION_STATE_KEY] = state
    return state


def normalize_tool_repeat_threshold(value: Any) -> int:
    """Return a supported threshold, preserving zero as the disable switch."""

    if isinstance(value, int) and not isinstance(value, bool) and (value == 0 or value >= 2):
        return value
    logger.warning(
        "CYBER_TOOL_REPEAT_THRESHOLD must be 0 or at least 2; using default %d",
        DEFAULT_TOOL_REPEAT_THRESHOLD,
    )
    return DEFAULT_TOOL_REPEAT_THRESHOLD


class ToolRepeatGuardHook(HookProvider):
    """Reuse repeated results and gracefully stop an agent that ignores them."""

    def __init__(self, repeat_threshold: int = DEFAULT_TOOL_REPEAT_THRESHOLD) -> None:
        if repeat_threshold < 2:
            raise ValueError("repeat_threshold must be at least 2")
        self.repeat_threshold = repeat_threshold

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        state = _get_state(event.invocation_state)
        fingerprint = _fingerprint(event.tool_use)
        tool_id = _tool_use_id(event.tool_use)

        with state.lock:
            if fingerprint == state.fingerprint:
                state.streak += 1
            else:
                state.fingerprint = fingerprint
                state.streak = 1
                state.suppressed = 0
                state.completed_fingerprint = ""
                state.completed_result = None

            state.call_fingerprints[tool_id] = fingerprint
            can_reuse = (
                state.streak >= self.repeat_threshold
                and state.completed_fingerprint == fingerprint
                and state.completed_result is not None
                and event.selected_tool is not None
            )
            if not can_reuse:
                return

            state.suppressed += 1
            event.selected_tool = _CachedResultTool(
                event.selected_tool,
                state.completed_result,
                state.streak,
            )
            logger.warning(
                "Suppressing repeated identical tool call: tool=%s repeat=%d",
                str(event.tool_use.get("name", "unknown")),
                state.streak,
            )

            if state.suppressed < 2:
                return

            request_state = event.invocation_state.setdefault("request_state", {})
            if not isinstance(request_state, dict):
                logger.warning("Unable to stop repeated tool loop because request_state is not a dictionary")
                return
            request_state["stop_event_loop"] = True
            request_state[REPEATED_TOOL_LOOP_STATE_KEY] = {
                "repeat_count": state.streak,
                "tool_name": str(event.tool_use.get("name", "unknown")),
            }
            logger.warning(
                "Stopping agent after repeated identical tool loop: tool=%s repeat=%d",
                str(event.tool_use.get("name", "unknown")),
                state.streak,
            )

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        state = _get_state(event.invocation_state)
        tool_id = _tool_use_id(event.tool_use)

        with state.lock:
            fingerprint = state.call_fingerprints.pop(tool_id, "")
            if isinstance(event.selected_tool, _CachedResultTool):
                return
            if not fingerprint or fingerprint != state.fingerprint or not isinstance(event.result, dict):
                return
            state.completed_fingerprint = fingerprint
            state.completed_result = copy.deepcopy(event.result)
