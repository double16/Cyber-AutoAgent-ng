"""Compatibility wrapper for the Strands editor tool."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from strands import tool


def create_absolute_path_editor(
    editor_tool: Callable[..., dict[str, Any]],
    *,
    current_directory: Callable[[], Path] = Path.cwd,
) -> Any:
    """Wrap an editor tool so its ``path`` input is always absolute.

    The Strands editor rejects relative paths.  Resolving them here lets agents use
    the process working directory without needing a corrective tool call.  This is
    intentionally not limited to an operation output directory: editor is also used
    for module tooling and other non-artifact files.
    """

    wrapped_description = str(editor_tool.tool_spec["description"])

    @tool(name="editor")
    def absolute_path_editor(
        command: str,
        path: str,
        file_text: str | None = None,
        insert_line: str | int | None = None,
        new_str: str | None = None,
        old_str: str | None = None,
        pattern: str | None = None,
        search_text: str | None = None,
        fuzzy: bool = False,
        view_range: list[int] | None = None,
    ) -> dict[str, Any]:
        """Edit a file or directory, resolving relative paths from the current directory.

        Relative paths and user-relative paths (``~/...``) are accepted.  The path
        passed to the underlying editor is always absolute.
        """

        resolved_path = Path(path).expanduser()
        if not resolved_path.is_absolute():
            resolved_path = current_directory() / resolved_path
        return editor_tool(
            command=command,
            path=str(resolved_path.resolve()),
            file_text=file_text,
            insert_line=insert_line,
            new_str=new_str,
            old_str=old_str,
            pattern=pattern,
            search_text=search_text,
            fuzzy=fuzzy,
            view_range=view_range,
        )

    absolute_path_editor.__name__ = "editor"
    absolute_path_editor.tool_spec["description"] = (
        f"{wrapped_description}\n\n"
        "Relative paths are resolved from the current working directory before the editor is invoked."
    )
    return absolute_path_editor
