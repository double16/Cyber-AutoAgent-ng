"""Tool-specific structured event emitters."""

from collections.abc import Callable
from typing import Any


class ToolEventEmitter:
    """Handles emission of tool-specific side-channel events."""

    def __init__(self, emit_func: Callable[[dict[str, Any]], None]):
        """
        Initialize the tool event emitter.

        Args:
            emit_func: Function to emit UI events
        """
        self.emit_ui_event = emit_func

    def emit_tool_specific_events(self, tool_name: str, tool_input: Any) -> None:
        """
        Route tool inputs to appropriate specialized emitters.

        Args:
            tool_name: Name of the tool being executed
            tool_input: Input parameters for the tool
        """
        emitter_map = {
            "http_request": self._emit_http_request,
            "python_repl": self._emit_python_repl,
            "generate_security_report": self._emit_report_generator,
            "think": self._emit_think_operation,
        }

        emitter = emitter_map.get(tool_name)
        if emitter:
            # Specific emitters only need tool_input
            emitter(tool_input)
        else:
            # Generic emitter needs tool_name and tool_input
            self._emit_generic_tool_params(tool_name, tool_input)

    def _emit_http_request(self, tool_input: Any) -> None:
        """Emit HTTP request details."""
        if isinstance(tool_input, dict):
            method = tool_input.get("method", "GET")
            url = tool_input.get("url", "")
            # Emit structured event for request tracking (not for display)
            if url:
                self.emit_ui_event(
                    {"type": "http_request_start", "method": method, "url": url}
                )

    def _emit_generic_tool_params(self, tool_name: str, tool_input: Any) -> None:  # pylint: disable=unused-argument
        """Emit generic tool parameters for tools without specialized handlers."""
        # REMOVED: Generic tools no longer emit metadata events
        # The StreamDisplay component already properly formats tool parameters
        # from the tool_start event in the default case. Emitting metadata here
        # causes duplicate display of the same information.
        # This was the root cause of the duplicate tool parameter display issue.

    def _emit_python_repl(self, tool_input: Any) -> None:
        """Emit Python REPL execution details."""
        if isinstance(tool_input, dict):
            code = tool_input.get("code", "")
            if code:
                # Emit code execution event for tracking/metrics (not for display)
                # StreamDisplay already handles the visual display
                lines = code.count("\n") + 1
                self.emit_ui_event(
                    {
                        "type": "code_execution",
                        "language": "python",
                        "lines": lines,
                        "preview": code[:100] + "..." if len(code) > 100 else code,
                    }
                )

    def _emit_report_generator(self, tool_input: Any) -> None:
        """Emit report generation details."""
        if isinstance(tool_input, dict):
            target = tool_input.get("target", "")
            report_type = tool_input.get("report_type", "security_assessment")
            self.emit_ui_event(
                {"type": "metadata", "content": {"target": target, "type": report_type}}
            )

    def _emit_think_operation(self, tool_input: Any) -> None:
        """Emit think operation details."""
        if isinstance(tool_input, dict):
            # Check various possible field names
            thought = ""
            for field in ["thought", "thinking", "content", "text"]:
                if field in tool_input:
                    thought = str(tool_input[field])
                    break

            if thought:
                self.emit_ui_event(
                    {
                        "type": "metadata",
                        "content": {
                            "thinking": thought[:100] + "..."
                            if len(thought) > 100
                            else thought
                        },
                    }
                )
        elif isinstance(tool_input, str):
            self.emit_ui_event(
                {
                    "type": "metadata",
                    "content": {
                        "thinking": tool_input[:100] + "..."
                        if len(tool_input) > 100
                        else tool_input
                    },
                }
            )
