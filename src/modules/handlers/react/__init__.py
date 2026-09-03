"""
Structured event integration handlers.

This module contains handlers and utilities for emitting structured events
from SDK callbacks, including tool handling and lifecycle hooks.
"""

# Unified handler provides output interception in modules.handlers.output_interceptor
from modules.handlers.output_interceptor import (
    OutputInterceptor,
    intercept_output,
    setup_output_interception,
)

from .agent_event_handler import AgentEventHandler, OperationEventCoordinator
from .hooks import ReactHooks
from .tool_emitters import ToolEventEmitter

__all__ = [
    "AgentEventHandler",
    "OperationEventCoordinator",
    "OutputInterceptor",
    "ReactHooks",
    "ToolEventEmitter",
    "intercept_output",
    "setup_output_interception",
]
