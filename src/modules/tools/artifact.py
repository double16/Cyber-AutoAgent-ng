"""Read-only access to artifacts produced by the current operation."""

import os
from typing import Any

from strands import tool

from modules.tools.memory import _artifact_path_from_ref, _operation_output_root


@tool
def read_artifact(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """Read a bounded text excerpt from an artifact in the current operation output.

    Args:
        path: Absolute path or path relative to the current operation output directory.
        start_line: One-based first line to return.
        max_lines: Number of lines to return, from 1 through 500.
    """

    root = _operation_output_root()
    if str(path).startswith(("artifact:", "artifact_id:")):
        resolved = _artifact_path_from_ref(path)
    else:
        candidate = path if os.path.isabs(path) else os.path.join(root, path)
        resolved = os.path.realpath(candidate)
    if os.path.commonpath([root, resolved]) != root:
        raise ValueError("Artifact path is outside the current operation output")
    if not os.path.isfile(resolved):
        raise ValueError("Artifact does not exist")
    if start_line < 1:
        raise ValueError("start_line must be at least 1")
    if max_lines < 1 or max_lines > 500:
        raise ValueError("max_lines must be between 1 and 500")

    with open(resolved, "r", encoding="utf-8", errors="replace") as artifact_file:
        lines = []
        total_lines = 0
        end_line = start_line + max_lines - 1
        for total_lines, line in enumerate(artifact_file, 1):
            if start_line <= total_lines <= end_line:
                lines.append(line.rstrip("\n"))

    payload: dict[str, Any] = {
        "artifact_ref": f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}",
        "start_line": start_line,
        "end_line": start_line + len(lines) - 1 if lines else start_line - 1,
        "total_lines": total_lines,
        "content": "\n".join(lines),
    }
    return str(payload)


def create_bounded_artifact_reader(max_reads: int | None = None) -> Any:
    """Create an agent-local artifact reader with a strict call allowance."""

    if max_reads is None:
        try:
            max_reads = max(1, int(os.getenv("CYBER_WORKFLOW_ARTIFACT_READ_LIMIT", "4")))
        except ValueError:
            max_reads = 4
    calls = 0

    @tool(name="read_artifact")
    def bounded_read_artifact(path: str, start_line: int = 1, max_lines: int = 200) -> str:
        """Read a bounded text excerpt from a current-operation artifact."""

        nonlocal calls
        if calls >= max_reads:
            raise RuntimeError(f"Artifact read limit reached ({max_reads})")
        calls += 1
        return read_artifact(path, start_line, max_lines)

    bounded_read_artifact.__name__ = "read_artifact"
    return bounded_read_artifact
