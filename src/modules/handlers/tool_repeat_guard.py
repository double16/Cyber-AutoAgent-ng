"""Bound repeated tool-call cycles within one agent invocation."""

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
DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH = 8
REPEATED_TOOL_LOOP_STATE_KEY = "repeated_tool_loop"
_INVOCATION_STATE_KEY = "_cyber_tool_repeat_guard"
_GUIDANCE = (
    "Repeated tool-call cycle suppressed by the loop guard. The preceding content is the most recent completed "
    "result for this exact call. Do not continue the same call cycle; use the returned result or take a different "
    "action."
)


@dataclass
class _InvocationRepeatState:
    """Mutable state shared by tool callbacks in one Strands invocation."""

    history: list[str] = field(default_factory=list)
    active_cycle: tuple[str, ...] = ()
    suppressed: int = 0
    completed_results: dict[str, ToolResult] = field(default_factory=dict)
    tool_names: dict[str, str] = field(default_factory=dict)
    call_fingerprints: dict[str, str] = field(default_factory=dict)
    repeated_calls: dict[str, tuple[tuple[str, ...], int]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


class _CachedResultTool(AgentTool):
    """Internal tool replacement that returns a prior result without side effects."""

    def __init__(self, selected_tool: AgentTool, result: ToolResult, repeat_count: int, cycle_length: int) -> None:
        super().__init__()
        self._selected_tool = selected_tool
        self._result = copy.deepcopy(result)
        self._repeat_count = repeat_count
        self._cycle_length = cycle_length

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
        content.append(
            {
                "text": (
                    f"[Loop guard cycle length {self._cycle_length}, repeat {self._repeat_count}] {_GUIDANCE}"
                )
            }
        )
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


def _cycle_signature(cycle: tuple[str, ...]) -> str:
    """Return a stable identity for an exact normalized tool-call cycle."""

    canonical = json.dumps(cycle, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _get_state(invocation_state: dict[str, Any]) -> _InvocationRepeatState:
    state = invocation_state.get(_INVOCATION_STATE_KEY)
    if isinstance(state, _InvocationRepeatState):
        return state
    state = _InvocationRepeatState()
    invocation_state[_INVOCATION_STATE_KEY] = state
    return state


def _canonical_cycle(cycle: tuple[str, ...]) -> tuple[str, ...]:
    """Return a rotation-independent identity for a non-empty cycle."""

    rotations = [cycle[index:] + cycle[:index] for index in range(len(cycle))]
    return min(rotations)


def _repeating_suffix(
    history: list[str],
    repeat_threshold: int,
    max_cycle_length: int,
) -> tuple[tuple[str, ...], tuple[str, ...], int] | None:
    """Return the shortest exact suffix cycle, its canonical identity, and repetition count."""

    largest_cycle = min(max_cycle_length, len(history) // repeat_threshold)
    for cycle_length in range(1, largest_cycle + 1):
        cycle = tuple(history[-cycle_length:])
        repeat_count = 0
        end = len(history)
        while end >= cycle_length and tuple(history[end - cycle_length:end]) == cycle:
            repeat_count += 1
            end -= cycle_length
        if repeat_count >= repeat_threshold:
            return cycle, _canonical_cycle(cycle), repeat_count
    return None


def normalize_tool_repeat_threshold(value: Any) -> int:
    """Return a supported threshold, preserving zero as the disable switch."""

    if isinstance(value, int) and not isinstance(value, bool) and (value == 0 or value >= 2):
        return value
    logger.warning(
        "CYBER_TOOL_REPEAT_THRESHOLD must be 0 or at least 2; using default %d",
        DEFAULT_TOOL_REPEAT_THRESHOLD,
    )
    return DEFAULT_TOOL_REPEAT_THRESHOLD


def normalize_tool_repeat_max_cycle_length(value: Any) -> int:
    """Return a supported maximum cycle length."""

    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    logger.warning(
        "CYBER_TOOL_REPEAT_MAX_CYCLE_LENGTH must be at least 1; using default %d",
        DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH,
    )
    return DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH


class ToolRepeatGuardHook(HookProvider):
    """Reuse results from repeated call cycles and stop an agent that ignores guidance."""

    def __init__(
        self,
        repeat_threshold: int = DEFAULT_TOOL_REPEAT_THRESHOLD,
        max_cycle_length: int = DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH,
    ) -> None:
        if repeat_threshold < 2:
            raise ValueError("repeat_threshold must be at least 2")
        if max_cycle_length < 1:
            raise ValueError("max_cycle_length must be at least 1")
        self.repeat_threshold = repeat_threshold
        self.max_cycle_length = max_cycle_length
        self.history_limit = repeat_threshold * max_cycle_length

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        state = _get_state(event.invocation_state)
        fingerprint = _fingerprint(event.tool_use)
        tool_id = _tool_use_id(event.tool_use)

        with state.lock:
            state.history.append(fingerprint)
            if len(state.history) > self.history_limit:
                del state.history[:-self.history_limit]
            retained_fingerprints = set(state.history)
            for completed_fingerprint in list(state.completed_results):
                if completed_fingerprint not in retained_fingerprints:
                    state.completed_results.pop(completed_fingerprint, None)
                    state.tool_names.pop(completed_fingerprint, None)

            state.tool_names[fingerprint] = str(event.tool_use.get("name", "unknown"))
            repeated_cycle = _repeating_suffix(
                state.history,
                self.repeat_threshold,
                self.max_cycle_length,
            )
            if repeated_cycle is None:
                state.active_cycle = ()
                state.suppressed = 0
            else:
                _, canonical_cycle, _ = repeated_cycle
                if canonical_cycle != state.active_cycle:
                    state.active_cycle = canonical_cycle
                    state.suppressed = 0

            state.call_fingerprints[tool_id] = fingerprint
            if repeated_cycle is None:
                return

            cycle, _, repeat_count = repeated_cycle
            state.repeated_calls[tool_id] = (cycle, repeat_count)
            completed_result = state.completed_results.get(fingerprint)
            can_reuse = (
                completed_result is not None
                and event.selected_tool is not None
            )
            if not can_reuse:
                return

            state.suppressed += 1
            event.selected_tool = _CachedResultTool(
                event.selected_tool,
                completed_result,
                repeat_count,
                len(cycle),
            )
            logger.warning(
                "Suppressing repeated tool-call cycle: tool=%s cycle_length=%d repeat=%d",
                str(event.tool_use.get("name", "unknown")),
                len(cycle),
                repeat_count,
            )

            if state.suppressed < 2:
                return

            if self._stop_repeated_loop(state, event, cycle, repeat_count):
                logger.warning(
                    "Stopping agent after repeated tool-call cycle: tool=%s cycle_length=%d repeat=%d",
                    str(event.tool_use.get("name", "unknown")),
                    len(cycle),
                    repeat_count,
                )

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        state = _get_state(event.invocation_state)
        tool_id = _tool_use_id(event.tool_use)

        with state.lock:
            fingerprint = state.call_fingerprints.pop(tool_id, "")
            repeated_call = state.repeated_calls.pop(tool_id, None)
            if event.cancel_message is not None:
                if repeated_call is not None:
                    cycle, repeat_count = repeated_call
                    if self._stop_repeated_loop(state, event, cycle, repeat_count):
                        logger.warning(
                            "Stopping agent after repeated canceled tool-call cycle: "
                            "tool=%s cycle_length=%d repeat=%d",
                            str(event.tool_use.get("name", "unknown")),
                            len(cycle),
                            repeat_count,
                        )
                return
            if isinstance(event.selected_tool, _CachedResultTool):
                return
            if not fingerprint or fingerprint not in state.history or not isinstance(event.result, dict):
                return
            state.completed_results[fingerprint] = copy.deepcopy(event.result)

    @staticmethod
    def _stop_repeated_loop(
        state: _InvocationRepeatState,
        event: BeforeToolCallEvent | AfterToolCallEvent,
        cycle: tuple[str, ...],
        repeat_count: int,
    ) -> bool:
        """Stop the current agent after a repeated executed or canceled call cycle."""

        request_state = event.invocation_state.setdefault("request_state", {})
        if not isinstance(request_state, dict):
            logger.warning("Unable to stop repeated tool loop because request_state is not a dictionary")
            return False
        if request_state.get("stop_event_loop") is True:
            return False
        request_state["stop_event_loop"] = True
        request_state[REPEATED_TOOL_LOOP_STATE_KEY] = {
            "cycle_length": len(cycle),
            "repeat_count": repeat_count,
            "tool_name": str(event.tool_use.get("name", "unknown")),
            "tool_names": [state.tool_names.get(item, "unknown") for item in cycle],
            "cycle_signature": _cycle_signature(cycle),
        }
        return True
