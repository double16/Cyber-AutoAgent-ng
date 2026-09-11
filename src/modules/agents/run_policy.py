"""Agent run-loop completion policy."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

ActionlessMode = Literal["auto", "required_tool", "task_progress"]


@dataclass(frozen=True)
class AgentRunPolicy:
    """Optional completion criteria for an agent managed by the run loop.

    The default policy preserves the generic worker behavior: the run loop keeps
    asking for tool calls until normal callback termination, stall detection, or
    another fatal condition occurs. Roles with a known terminal tool sequence can
    require specific tools and then allow a final text-only response.
    """

    min_tool_calls: int = 0
    required_tool_names: frozenset[str] = field(default_factory=frozenset)
    terminal_after_required_tools: bool = False
    require_successful_required_tools: bool = False
    allow_text_final_after_tools: bool = True
    max_actionless_after_tools: int = 0
    max_actionless_calls: int = 3
    max_agent_calls: int = 64
    max_model_turns: int = 64
    max_tool_calls: int = 0
    actionless_mode: ActionlessMode = "auto"
    ignored_terminal_tool_names: frozenset[str] = field(default_factory=frozenset)
    terminal_reason: str = "agent_completed_required_tools"
    terminal_message: str = "Agent completed required tool calls"
    recovery_objective: str = ""
    recovery_next_action: str = ""
    recovery_allowed_tool_names: frozenset[str] = field(default_factory=frozenset)

    def __init__(
        self,
        min_tool_calls: int = 0,
        required_tool_names: Iterable[str] = frozenset(),
        terminal_after_required_tools: bool = False,
        require_successful_required_tools: bool = False,
        allow_text_final_after_tools: bool = True,
        max_actionless_after_tools: int = 0,
        max_actionless_calls: int = 3,
        max_agent_calls: int = 64,
        max_model_turns: int = 64,
        max_tool_calls: int = 0,
        actionless_mode: ActionlessMode = "auto",
        ignored_terminal_tool_names: Iterable[str] = frozenset(),
        terminal_reason: str = "agent_completed_required_tools",
        terminal_message: str = "Agent completed required tool calls",
        recovery_objective: str = "",
        recovery_next_action: str = "",
        recovery_allowed_tool_names: Iterable[str] = frozenset(),
    ):
        object.__setattr__(self, "min_tool_calls", min_tool_calls)
        object.__setattr__(self, "required_tool_names", frozenset(required_tool_names))
        object.__setattr__(self, "terminal_after_required_tools", terminal_after_required_tools)
        object.__setattr__(self, "require_successful_required_tools", require_successful_required_tools)
        object.__setattr__(self, "allow_text_final_after_tools", allow_text_final_after_tools)
        object.__setattr__(self, "max_actionless_after_tools", max_actionless_after_tools)
        object.__setattr__(self, "max_actionless_calls", max(1, int(max_actionless_calls)))
        object.__setattr__(self, "max_agent_calls", max(1, int(max_agent_calls)))
        object.__setattr__(self, "max_model_turns", max(1, int(max_model_turns)))
        object.__setattr__(self, "max_tool_calls", max_tool_calls)
        if actionless_mode not in {"auto", "required_tool", "task_progress"}:
            raise ValueError("actionless_mode must be auto|required_tool|task_progress")
        object.__setattr__(self, "actionless_mode", actionless_mode)
        object.__setattr__(self, "ignored_terminal_tool_names", frozenset(ignored_terminal_tool_names))
        object.__setattr__(self, "terminal_reason", terminal_reason)
        object.__setattr__(self, "terminal_message", terminal_message)
        object.__setattr__(self, "recovery_objective", str(recovery_objective or ""))
        object.__setattr__(self, "recovery_next_action", str(recovery_next_action or ""))
        object.__setattr__(self, "recovery_allowed_tool_names", frozenset(recovery_allowed_tool_names))
