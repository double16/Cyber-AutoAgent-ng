from pathlib import Path
from unittest.mock import Mock

from modules.tools.editor import create_absolute_path_editor


def _editor_delegate(result=None):
    delegate = Mock(return_value=result if result is not None else {"status": "success"})
    delegate.tool_spec = {
        "name": "editor",
        "description": "Wrapped editor instructions: create requires file_text.",
    }
    return delegate


def test_editor_wrapper_resolves_relative_path_from_current_directory(tmp_path: Path):
    delegate = _editor_delegate()
    editor = create_absolute_path_editor(delegate, current_directory=lambda: tmp_path)

    result = editor(command="create", path="artifacts/result.txt", file_text="evidence")

    assert result == {"status": "success"}
    assert delegate.call_args.kwargs["path"] == str((tmp_path / "artifacts/result.txt").resolve())
    assert delegate.call_args.kwargs["file_text"] == "evidence"


def test_editor_wrapper_resolves_parent_relative_path_from_current_directory(tmp_path: Path):
    delegate = _editor_delegate()
    editor = create_absolute_path_editor(delegate, current_directory=lambda: tmp_path / "nested")

    editor(command="view", path="../evidence.txt")

    assert delegate.call_args.kwargs["path"] == str((tmp_path / "evidence.txt").resolve())


def test_editor_wrapper_normalizes_absolute_and_home_relative_paths(tmp_path: Path):
    delegate = _editor_delegate()
    editor = create_absolute_path_editor(delegate, current_directory=lambda: tmp_path)
    absolute = tmp_path / "nested" / ".." / "evidence.txt"

    editor(command="view", path=str(absolute))
    assert delegate.call_args.kwargs["path"] == str((tmp_path / "evidence.txt").resolve())

    editor(command="view", path="~/evidence.txt")
    assert delegate.call_args.kwargs["path"] == str((Path.home() / "evidence.txt").resolve())


def test_editor_wrapper_preserves_source_description_and_required_input_schema(tmp_path: Path):
    editor = create_absolute_path_editor(_editor_delegate({}), current_directory=lambda: tmp_path)
    schema = editor.tool_spec["inputSchema"]["json"]

    assert editor.__name__ == "editor"
    assert editor.tool_spec["name"] == "editor"
    assert set(schema["required"]) == {"command", "path"}
    assert "wrapped editor instructions: create requires file_text." in editor.tool_spec["description"].lower()
    assert "relative paths are resolved" in editor.tool_spec["description"].lower()
