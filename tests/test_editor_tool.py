from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from modules.tools.editor import create_absolute_path_editor


def _editor_delegate(result=None):
    delegate = AsyncMock(return_value=result if result is not None else {"status": "success"})
    delegate.tool_spec = {
        "name": "editor",
        "description": "Wrapped editor instructions: create requires file_text.",
    }
    return delegate


@pytest.mark.asyncio
async def test_editor_wrapper_resolves_relative_path_from_current_directory(tmp_path: Path):
    delegate = _editor_delegate()
    editor = create_absolute_path_editor(delegate, current_directory=lambda: tmp_path)

    result = await editor(command="create", path="artifacts/result.txt", file_text="evidence", tool_context=None)

    assert result == {"status": "success"}
    assert delegate.call_args.kwargs["path"] == str((tmp_path / "artifacts/result.txt").resolve())
    assert delegate.call_args.kwargs["file_text"] == "evidence"


@pytest.mark.asyncio
async def test_editor_wrapper_resolves_parent_relative_path_from_current_directory(tmp_path: Path):
    delegate = _editor_delegate()
    editor = create_absolute_path_editor(delegate, current_directory=lambda: tmp_path / "nested")

    await editor(command="view", path="../evidence.txt", tool_context=None)

    assert delegate.call_args.kwargs["path"] == str((tmp_path / "evidence.txt").resolve())


@pytest.mark.asyncio
async def test_editor_wrapper_normalizes_absolute_and_home_relative_paths(tmp_path: Path):
    delegate = _editor_delegate()
    editor = create_absolute_path_editor(delegate, current_directory=lambda: tmp_path)
    absolute = tmp_path / "nested" / ".." / "evidence.txt"

    await editor(command="view", path=str(absolute), tool_context=None)
    assert delegate.call_args.kwargs["path"] == str((tmp_path / "evidence.txt").resolve())

    await editor(command="view", path="~/evidence.txt", tool_context=None)
    assert delegate.call_args.kwargs["path"] == str((Path.home() / "evidence.txt").resolve())


@pytest.mark.asyncio
async def test_editor_wrapper_preserves_source_description_and_required_input_schema(tmp_path: Path):
    editor = create_absolute_path_editor(_editor_delegate({}), current_directory=lambda: tmp_path)
    schema = editor.tool_spec["inputSchema"]["json"]

    assert editor.__name__ == "editor"
    assert editor.tool_spec["name"] == "editor"
    assert set(schema["required"]) == {"command", "path"}
    assert schema["properties"]["command"]["enum"] == ["view", "create", "str_replace", "insert"]
    assert "operation-relative" in editor.tool_spec["description"].lower()


@pytest.mark.asyncio
async def test_sdk_editor_round_trip_and_rejection(tmp_path):
    from types import SimpleNamespace

    from strands.sandbox.not_a_sandbox_local_environment import NotASandboxLocalEnvironment
    from strands.vended_tools import file_editor

    editor = create_absolute_path_editor(file_editor, current_directory=lambda: tmp_path)
    context = SimpleNamespace(agent=SimpleNamespace(sandbox=NotASandboxLocalEnvironment()), cancel_signal=None)
    await editor(command="create", path="test.txt", file_text="first\nsecond\n", tool_context=context)
    assert "first" in await editor(command="view", path="test.txt", tool_context=context)
    await editor(command="str_replace", path="test.txt", old_str="first", new_str="updated", tool_context=context)
    await editor(command="insert", path="test.txt", insert_line=1, new_str="inserted", tool_context=context)
    assert "inserted" in (tmp_path / "test.txt").read_text()
    with pytest.raises(ValueError):
        await editor(command="str_replace", path="test.txt", old_str="missing", new_str="x", tool_context=context)
