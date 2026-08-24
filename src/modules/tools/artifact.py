"""Read-only access to artifacts produced by the current operation."""

import os
from collections.abc import Iterable
from typing import Any

from strands import tool

from modules.tools.memory import _artifact_path_from_ref, _operation_output_root

ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER = "ARTIFACT_TOTAL_READ_LIMIT_REACHED"
ARTIFACT_PAGE_LIMIT_REACHED_MARKER = "ARTIFACT_PAGE_LIMIT_REACHED"
ARTIFACT_READ_POLICY_VIOLATION_MARKER = "ARTIFACT_READ_POLICY_VIOLATION"
ARTIFACT_DIRECTORY_LISTING_LIMIT = 25


def _resolved_operation_path(candidate: str, root: str) -> str:
    """Resolve one candidate and reject paths that escape the operation root."""

    resolved = os.path.realpath(candidate)
    if os.path.commonpath([root, resolved]) != root:
        raise ValueError("Artifact path is outside the current operation output")
    return resolved


def resolve_operation_artifact_path(path: str) -> str:
    """Resolve a readable current-operation artifact path without allowing escapes.

    Canonical artifact references and absolute paths must resolve inside the operation
    output directory. Relative paths prefer its ``artifacts/`` directory and then
    fall back to the operation output directory itself.
    """

    root = os.path.realpath(_operation_output_root())
    if str(path).startswith(("artifact:", "artifact_id:")):
        resolved = _resolved_operation_path(_artifact_path_from_ref(path), root)
    elif os.path.isabs(path):
        resolved = _resolved_operation_path(path, root)
    else:
        resolved = ""
        for candidate in (os.path.join(root, "artifacts", path), os.path.join(root, path)):
            candidate_resolved = _resolved_operation_path(candidate, root)
            if os.path.isfile(candidate_resolved):
                resolved = candidate_resolved
                break
        if not resolved:
            raise ValueError("Artifact does not exist")
    if not os.path.isfile(resolved):
        raise ValueError("Artifact does not exist")
    return resolved


def _resolve_operation_directory_path(path: str) -> str | None:
    """Resolve an existing in-scope directory supplied to the artifact reader."""

    root = os.path.realpath(_operation_output_root())
    text = str(path or "").strip()
    if text.startswith("artifact_id:"):
        artifact_id = text.split(":", 1)[1]
        if not artifact_id or artifact_id != os.path.basename(artifact_id):
            return None
        candidates = [os.path.join(root, "artifacts", artifact_id)]
    elif text.startswith("artifact:"):
        raw_path = text.removeprefix("artifact:")
        candidates = [raw_path if os.path.isabs(raw_path) else os.path.join(root, raw_path)]
    elif os.path.isabs(text):
        candidates = [text]
    else:
        candidates = [os.path.join(root, "artifacts", text), os.path.join(root, text)]

    for candidate in candidates:
        resolved = _resolved_operation_path(candidate, root)
        if os.path.isdir(resolved):
            return resolved
    return None


def _directory_read_guidance(directory: str, root: str) -> str:
    """Return a bounded, non-recursive file listing for a directory read attempt."""

    files = []
    for entry in os.scandir(directory):
        try:
            resolved = _resolved_operation_path(entry.path, root)
        except ValueError:
            continue
        if entry.is_file() and os.path.isfile(resolved):
            files.append(f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}")
    files.sort()
    listed_files = files[:ARTIFACT_DIRECTORY_LISTING_LIMIT]
    omitted_count = len(files) - len(listed_files)
    payload: dict[str, Any] = {
        "status": "directory",
        "message": "read_artifact requires one file. Retry with one listed artifact_ref.",
        "directory": f"artifact:{os.path.relpath(directory, root).replace(os.sep, '/')}",
        "artifact_refs": listed_files,
        "omitted_file_count": omitted_count,
    }
    return str(payload)


@tool
def read_artifact(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """Read a bounded text excerpt from an artifact in the current operation output.

    Args:
        path: A single canonical artifact file reference, safe absolute file path, or relative file path. Relative
            paths resolve from artifacts/ first, then from the current operation output directory. Directory paths
            return a bounded list of immediate artifact files to choose from; their contents are not read.
        start_line: One-based first line to return.
        max_lines: Number of lines to return, from 1 through 500.
    """

    root = os.path.realpath(_operation_output_root())
    try:
        resolved = resolve_operation_artifact_path(path)
    except ValueError:
        directory = _resolve_operation_directory_path(path)
        if directory is not None:
            return _directory_read_guidance(directory, root)
        raise
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


def create_bounded_artifact_reader(
    max_reads: int | None = None,
    *,
    allowed_artifact_refs: Iterable[str] | None = None,
    max_reads_per_artifact: int | None = None,
    max_lines_per_read: int | None = None,
) -> Any:
    """Create an agent-local artifact reader with optional path and page limits."""

    if max_reads is None:
        try:
            max_reads = max(1, int(os.getenv("CYBER_WORKFLOW_ARTIFACT_READ_LIMIT", "4")))
        except ValueError:
            max_reads = 4
    if max_reads_per_artifact is not None and max_reads_per_artifact < 1:
        raise ValueError("max_reads_per_artifact must be at least 1")
    if max_lines_per_read is not None and not 1 <= max_lines_per_read <= 500:
        raise ValueError("max_lines_per_read must be between 1 and 500")

    allowed_paths = None
    if allowed_artifact_refs is not None:
        allowed_paths = {
            resolve_operation_artifact_path(reference)
            for reference in allowed_artifact_refs
        }
    calls = 0
    reads_by_path: dict[str, int] = {}
    seen_ranges: set[tuple[str, int, int]] = set()

    @tool(name="read_artifact")
    def bounded_read_artifact(path: str, start_line: int = 1, max_lines: int = 200) -> str:
        """Read a bounded text excerpt from a current-operation artifact."""

        nonlocal calls
        try:
            resolved = resolve_operation_artifact_path(path)
        except (OSError, TypeError, ValueError) as error:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: Artifact is not available to this evaluator"
            ) from error
        if allowed_paths is not None and resolved not in allowed_paths:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: Artifact is not available to this evaluator"
            )
        if max_lines_per_read is not None:
            max_lines = min(max_lines, max_lines_per_read)
        if start_line < 1 or not 1 <= max_lines <= 500:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: Artifact page parameters are invalid"
            )
        page = (resolved, start_line, max_lines)
        if calls >= max_reads:
            raise RuntimeError(
                f"{ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER}: "
                f"Artifact read limit reached ({max_reads})"
            )
        if page in seen_ranges:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: Artifact page was already read"
            )
        if max_reads_per_artifact is not None and reads_by_path.get(resolved, 0) >= max_reads_per_artifact:
            raise RuntimeError(
                f"{ARTIFACT_PAGE_LIMIT_REACHED_MARKER}: "
                f"Artifact page limit reached ({max_reads_per_artifact})"
            )
        result = read_artifact(resolved, start_line, max_lines)
        calls += 1
        reads_by_path[resolved] = reads_by_path.get(resolved, 0) + 1
        seen_ranges.add(page)
        return result

    bounded_read_artifact.__name__ = "read_artifact"
    return bounded_read_artifact
