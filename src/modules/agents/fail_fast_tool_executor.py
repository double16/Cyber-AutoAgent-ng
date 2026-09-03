"""Sequential tool execution that stops a model-requested batch after a failure."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from strands.tools.executors._executor import ToolExecutor
from strands.types._events import ToolInterruptEvent, ToolResultEvent
from strands.types.tools import ToolResult, ToolUse

_SKIPPED_TOOL_MESSAGE = (
    "Tool execution stopped because an earlier tool call in this response failed. "
    "Use the returned result before requesting another tool."
)


class FailFastSequentialToolExecutor(ToolExecutor):
    """Execute tools in order and skip remaining calls after the first failure.

    Models may emit several tool uses in a single response.  Running a review
    agent's batch sequentially lets an early failure prevent later calls from
    consuming bounded evidence-read allowance or obscuring the first failure.
    """

    async def _execute(
        self,
        agent: Any,
        tool_uses: list[ToolUse],
        tool_results: list[ToolResult],
        cycle_trace: Any,
        cycle_span: Any,
        invocation_state: dict[str, Any],
        structured_output_context: Any = None,
    ) -> AsyncGenerator[Any, None]:
        for index, tool_use in enumerate(tool_uses):
            failed = False
            async for event in ToolExecutor._stream_with_trace(
                agent,
                tool_use,
                tool_results,
                cycle_trace,
                cycle_span,
                invocation_state,
                structured_output_context,
            ):
                yield event
                if isinstance(event, ToolInterruptEvent):
                    return
                if isinstance(event, ToolResultEvent) and (
                    event.exception is not None or event.tool_result.get("status") != "success"
                ):
                    failed = True

            if failed:
                for skipped_tool_use in tool_uses[index + 1 :]:
                    skipped_result: ToolResult = {
                        "toolUseId": str(skipped_tool_use.get("toolUseId", "")),
                        "status": "error",
                        "content": [{"text": _SKIPPED_TOOL_MESSAGE}],
                    }
                    tool_results.append(skipped_result)
                    yield ToolResultEvent(skipped_result)
                return
