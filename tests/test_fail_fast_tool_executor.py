"""Tests for review-agent fail-fast tool batch execution."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from strands.tools.executors._executor import ToolExecutor
from strands.types._events import ToolInterruptEvent, ToolResultEvent

from modules.agents.fail_fast_tool_executor import (
    _SKIPPED_TOOL_MESSAGE,
    FailFastSequentialToolExecutor,
)


def _tool_use(tool_use_id: str) -> dict[str, Any]:
    return {"name": "read_artifact", "toolUseId": tool_use_id, "input": {"path": tool_use_id}}


def _collect(executor: FailFastSequentialToolExecutor, tool_uses: list[dict[str, Any]]) -> tuple[list[Any], list[dict[str, Any]]]:
    tool_results: list[dict[str, Any]] = []

    async def collect() -> list[Any]:
        return [
            event
            async for event in executor._execute(
                object(), tool_uses, tool_results, object(), object(), {}, None
            )
        ]

    return asyncio.run(collect()), tool_results


def test_fail_fast_executor_runs_successful_tool_batch_in_order(monkeypatch):
    calls = []

    async def stream_with_trace(_agent, tool_use, tool_results, *_args) -> AsyncGenerator[Any, None]:
        calls.append(tool_use["toolUseId"])
        result = {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []}
        tool_results.append(result)
        yield ToolResultEvent(result)

    monkeypatch.setattr(ToolExecutor, "_stream_with_trace", staticmethod(stream_with_trace))

    events, results = _collect(FailFastSequentialToolExecutor(), [_tool_use("one"), _tool_use("two")])

    assert calls == ["one", "two"]
    assert [event.tool_result["status"] for event in events] == ["success", "success"]
    assert [result["toolUseId"] for result in results] == ["one", "two"]


def test_fail_fast_executor_skips_remaining_tools_after_error_result(monkeypatch):
    calls = []

    async def stream_with_trace(_agent, tool_use, tool_results, *_args) -> AsyncGenerator[Any, None]:
        calls.append(tool_use["toolUseId"])
        result = {"toolUseId": tool_use["toolUseId"], "status": "error", "content": []}
        tool_results.append(result)
        yield ToolResultEvent(result)

    monkeypatch.setattr(ToolExecutor, "_stream_with_trace", staticmethod(stream_with_trace))

    events, results = _collect(
        FailFastSequentialToolExecutor(), [_tool_use("failed"), _tool_use("skipped-one"), _tool_use("skipped-two")]
    )

    assert calls == ["failed"]
    assert [event.tool_result["toolUseId"] for event in events] == ["failed", "skipped-one", "skipped-two"]
    assert [result["status"] for result in results] == ["error", "error", "error"]
    assert results[1]["content"] == [{"text": _SKIPPED_TOOL_MESSAGE}]
    assert results[2]["content"] == [{"text": _SKIPPED_TOOL_MESSAGE}]


def test_fail_fast_executor_skips_remaining_tools_after_exception_result(monkeypatch):
    calls = []

    async def stream_with_trace(_agent, tool_use, tool_results, *_args) -> AsyncGenerator[Any, None]:
        calls.append(tool_use["toolUseId"])
        result = {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []}
        tool_results.append(result)
        yield ToolResultEvent(result, exception=RuntimeError("failed"))

    monkeypatch.setattr(ToolExecutor, "_stream_with_trace", staticmethod(stream_with_trace))

    _events, results = _collect(FailFastSequentialToolExecutor(), [_tool_use("failed"), _tool_use("skipped")])

    assert calls == ["failed"]
    assert [result["toolUseId"] for result in results] == ["failed", "skipped"]


def test_fail_fast_executor_stops_after_interrupt_without_running_remaining_tools(monkeypatch):
    calls = []

    async def stream_with_trace(_agent, tool_use, _tool_results, *_args) -> AsyncGenerator[Any, None]:
        calls.append(tool_use["toolUseId"])
        yield ToolInterruptEvent(tool_use, [])

    monkeypatch.setattr(ToolExecutor, "_stream_with_trace", staticmethod(stream_with_trace))

    events, results = _collect(FailFastSequentialToolExecutor(), [_tool_use("interrupted"), _tool_use("skipped")])

    assert calls == ["interrupted"]
    assert len(events) == 1
    assert results == []
