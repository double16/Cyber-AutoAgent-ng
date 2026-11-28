"""Resolve operation-relative paths for the SDK file editor."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from strands import tool
from strands.types.tools import ToolContext


def create_absolute_path_editor(
    editor_tool: Callable[..., Any],
    *,
    current_directory: Callable[[], Path] = Path.cwd,
) -> Any:
    """Expose supported SDK editor commands with operation-relative path resolution."""

    @tool(name="editor", context="tool_context")
    async def absolute_path_editor(
        command: Literal["view", "create", "str_replace", "insert"],
        path: str,
        tool_context: ToolContext,
        file_text: str | None = None,
        insert_line: int | None = None,
        new_str: str | None = None,
        old_str: str | None = None,
        view_range: list[int] | None = None,
    ) -> Any:
        """View, create, replace a unique string, or insert text in a file.

        Accept absolute, operation-relative, and user-relative paths.
        """
        resolved_path = Path(path).expanduser()
        if not resolved_path.is_absolute():
            resolved_path = current_directory() / resolved_path
        return await editor_tool(
            command=command,
            path=str(resolved_path.resolve()),
            tool_context=tool_context,
            file_text=file_text,
            insert_line=insert_line,
            new_str=new_str,
            old_str=old_str,
            view_range=view_range,
        )

    absolute_path_editor.__name__ = "editor"
    return absolute_path_editor
