"""Agent run-loop completion policy."""

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable


@dataclass(frozen=True)
class AgentRunPolicy:
    """Optional completion criteria for an agent managed by the run loop.

    The default policy preserves the generic worker behavior: the run loop keeps
    asking for tool calls until normal callback termination, stall detection, or
    another fatal condition occurs. Roles with a known terminal tool sequence can
    require specific tools and then allow a final text-only response.
    """

    min_tool_calls: int = 0
    required_tool_names: FrozenSet[str] = field(default_factory=frozenset)
    terminal_after_required_tools: bool = False
    allow_text_final_after_tools: bool = True
    max_actionless_after_tools: int = 0
    ignored_terminal_tool_names: FrozenSet[str] = field(default_factory=frozenset)
    terminal_reason: str = "agent_completed_required_tools"
    terminal_message: str = "Agent completed required tool calls"

    def __init__(
        self,
        min_tool_calls: int = 0,
        required_tool_names: Iterable[str] = frozenset(),
        terminal_after_required_tools: bool = False,
        allow_text_final_after_tools: bool = True,
        max_actionless_after_tools: int = 0,
        ignored_terminal_tool_names: Iterable[str] = frozenset(),
        terminal_reason: str = "agent_completed_required_tools",
        terminal_message: str = "Agent completed required tool calls",
    ):
        object.__setattr__(self, "min_tool_calls", min_tool_calls)
        object.__setattr__(self, "required_tool_names", frozenset(required_tool_names))
        object.__setattr__(self, "terminal_after_required_tools", terminal_after_required_tools)
        object.__setattr__(self, "allow_text_final_after_tools", allow_text_final_after_tools)
        object.__setattr__(self, "max_actionless_after_tools", max_actionless_after_tools)
        object.__setattr__(self, "ignored_terminal_tool_names", frozenset(ignored_terminal_tool_names))
        object.__setattr__(self, "terminal_reason", terminal_reason)
        object.__setattr__(self, "terminal_message", terminal_message)
