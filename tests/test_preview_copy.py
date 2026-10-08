"""Exact preview-copy guarding across tools and operation continuations."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands import Agent, tool
from strands.tools.executors import SequentialToolExecutor

from modules.handlers.preview_copy import PreviewCopyGuard
from modules.handlers.tool_router import ToolRouterHook


def event(arguments, name="runtime_mcp_tool"):
    return SimpleNamespace(tool_use={"name": name, "input": arguments}, cancel_tool=False)


@pytest.mark.parametrize("arguments", [
    {"file_text": "the complete preview"},
    {"nested": [{"value": "prefix the complete preview suffix"}]},
    {"command": "cat > result <<EOF\nthe complete preview\nEOF"},
    {"data": ("the complete preview",)},
])
def test_guard_blocks_full_preview_copies_and_reloads(tmp_path, arguments):
    PreviewCopyGuard(tmp_path).record("the complete preview", "artifact:artifacts/full.log")
    guard = PreviewCopyGuard(tmp_path)
    call = event(arguments)
    guard.before_tool(call)
    assert "INCOMPLETE_PREVIEW_COPY" in call.cancel_tool
    assert "artifact:artifacts/full.log" in call.cancel_tool
    assert set(vars(guard)) == {"records_dir"}


@pytest.mark.parametrize("arguments", [
    {"text": "the complete"}, {"text": "rewritten preview"},
    {"path": "artifact:artifacts/full.log"}, {"count": 12},
])
def test_guard_allows_excerpts_and_artifact_references(tmp_path, arguments):
    guard = PreviewCopyGuard(tmp_path)
    guard.record("the complete preview", "artifact:artifacts/full.log")
    call = event(arguments)
    guard.before_tool(call)
    assert call.cancel_tool is False


def test_records_are_atomic_deduplicated_and_operation_local(tmp_path):
    guard = PreviewCopyGuard(tmp_path / "operation-one")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: guard.record("preview", "artifact:artifacts/full.log"), range(20)))
    files = list(guard.records_dir.iterdir())
    assert len(files) == 1
    assert json.loads(files[0].read_text())["preview"] == "preview"
    call = event({"text": "preview"})
    PreviewCopyGuard(tmp_path / "operation-two").before_tool(call)
    assert call.cancel_tool is False


def test_guard_handles_disabled_empty_and_corrupt_records(tmp_path):
    PreviewCopyGuard(None).record("preview", "artifact:full.log")
    call = event({"text": "preview"})
    PreviewCopyGuard(None).before_tool(call)
    guard = PreviewCopyGuard(tmp_path)
    guard.record("", "artifact:full.log")
    assert not guard.records_dir.exists()
    guard.records_dir.mkdir()
    for index, payload in enumerate(["bad json", "[]", '{"preview":"","artifact_ref":"ref"}',
                                     '{"preview":123,"artifact_ref":"ref"}']):
        (guard.records_dir / f"{index}.json").write_text(payload)
    guard.before_tool(call)
    assert call.cancel_tool is False
    call.cancel_tool = "prior cancellation"
    guard.before_tool(call)
    assert call.cancel_tool == "prior cancellation"


def test_record_failure_does_not_leave_partial_records(tmp_path, monkeypatch):
    guard = PreviewCopyGuard(tmp_path)
    monkeypatch.setattr("modules.handlers.preview_copy.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    guard.record("preview", "artifact:full.log")
    assert list(guard.records_dir.iterdir()) == []


@pytest.mark.parametrize("block", ["text", "json"])
def test_router_returns_saved_artifact_without_registering_body_preview(tmp_path, block):
    router = ToolRouterHook(max_result_chars=10, artifacts_dir=tmp_path, artifact_threshold=5)
    result = {"status": "success", "toolUseId": "id", "extra": "preserved",
              "content": [{block: "abcdefghij-more content"}]}
    call = SimpleNamespace(tool_use={"name": "network_or_filesystem_tool"}, result=result)
    asyncio.run(router._truncate_large_results_async(call))
    text = str(call.result["content"])
    assert "INCOMPLETE PREVIEW" not in text
    assert "abcdefghij" not in text
    assert text.count("Complete output:") == 1
    assert call.result["extra"] == "preserved"
    assert call.result["status"] == "success"
    assert call.result["toolUseId"] == "id"
    copied = event({"file_text": "abcdefghij"})
    ToolRouterHook(artifacts_dir=tmp_path)._preview_copy_guard.before_tool(copied)
    assert copied.cancel_tool is False
    assert not router._preview_copy_guard.records_dir.exists()


def test_router_does_not_register_complete_or_unsaved_outputs(tmp_path):
    router = ToolRouterHook(max_result_chars=20, artifacts_dir=tmp_path, artifact_threshold=5)
    call = SimpleNamespace(tool_use={"name": "tool"}, result={"content": [{"text": "complete"}]})
    asyncio.run(router._truncate_large_results_async(call))
    assert "INCOMPLETE PREVIEW" not in str(call.result)
    assert not router._preview_copy_guard.records_dir.exists()
    router = ToolRouterHook(max_result_chars=5)
    call.result = {"content": [{"text": "long unsaved output"}]}
    asyncio.run(router._truncate_large_results_async(call))
    assert "Complete output:" not in str(call.result)


def test_guard_registers_for_all_tools(tmp_path):
    registrations = []
    guard = PreviewCopyGuard(tmp_path)
    guard.register_hooks(SimpleNamespace(add_callback=lambda *args: registrations.append(args)))
    assert len(registrations) == 1
    assert registrations[0][1] == guard.before_tool


@pytest.mark.parametrize("role", ["controller", "worker", "critic"])
def test_sdk_cancels_preview_copy_before_tool_execution(tmp_path, role):
    calls = []

    @tool
    def runtime_added_tool(content: str) -> str:
        """Process supplied content."""
        calls.append(content)
        return "processed"

    guard = PreviewCopyGuard(tmp_path)
    guard.record("complete preview", "artifact:artifacts/full.log")
    agent = Agent(model=Mock(), tools=[runtime_added_tool], hooks=[guard], callback_handler=None)
    results = []
    uses = [{"toolUseId": role, "name": "runtime_added_tool", "input": {"content": "complete preview"}}]

    async def collect():
        return [item async for item in SequentialToolExecutor()._execute(
            agent, uses, results, Mock(), Mock(), {},
        )]

    asyncio.run(collect())
    assert calls == []
    assert results[0]["status"] == "error"
    assert "artifact:artifacts/full.log" in str(results[0])
