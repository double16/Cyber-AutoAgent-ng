"""Read-only access to artifacts produced by the current operation."""

import ast
import os
from collections.abc import Iterable, Mapping
from typing import Any

from strands import tool

from modules.tools.memory import _artifact_path_from_ref, _operation_output_root

ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER = "ARTIFACT_TOTAL_READ_LIMIT_REACHED"
ARTIFACT_PAGE_LIMIT_REACHED_MARKER = "ARTIFACT_PAGE_LIMIT_REACHED"
ARTIFACT_READ_POLICY_VIOLATION_MARKER = "ARTIFACT_READ_POLICY_VIOLATION"
ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER = "ARTIFACT_READ_SIZE_LIMIT_REACHED"
ARTIFACT_READ_REPEAT_GUARD_MARKER = "ARTIFACT_READ_REPEAT_GUARD"
ARTIFACT_READ_OVERLAP_GUARD_MARKER = "ARTIFACT_READ_OVERLAP_GUARD"
ARTIFACT_DIRECTORY_LISTING_LIMIT = 25
ARTIFACT_BYTES_PER_TOKEN = 4
ARTIFACT_PAGE_CONTEXT_FRACTION = 0.10
ARTIFACT_MIN_BYTES_PER_READ = 8 * 1024
ARTIFACT_MAX_BYTES_PER_READ = 64 * 1024
ARTIFACT_NEARBY_BYTE_PAGE_GAP = 256


def artifact_max_bytes_for_context_window(context_window_tokens: int) -> int:
    """Return the clamped UTF-8 page budget for one resolved input context window."""

    if isinstance(context_window_tokens, bool) or not isinstance(context_window_tokens, int):
        raise TypeError("context_window_tokens must be a positive integer")
    if context_window_tokens < 1:
        raise ValueError("context_window_tokens must be a positive integer")
    context_budget = int(context_window_tokens * ARTIFACT_BYTES_PER_TOKEN * ARTIFACT_PAGE_CONTEXT_FRACTION)
    return min(ARTIFACT_MAX_BYTES_PER_READ, max(ARTIFACT_MIN_BYTES_PER_READ, context_budget))


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


def _bounded_read_guidance(path: str, root: str, reason: str, message: str) -> str:
    """Return a successful, non-content response for a non-distinct evaluator read."""

    return str({
        "status": "not_directly_readable",
        "reason": reason,
        "artifact_ref": f"artifact:{os.path.relpath(path, root).replace(os.sep, '/')}",
        "content": "",
        "message": message,
        "guidance": "Use the controller-provided evidence and review digest; do not reread this artifact page.",
    })


def artifact_review_metadata(path: str, max_bytes: int) -> dict[str, Any]:
    """Return deterministic evaluator-review metadata without materializing artifact content."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    root = os.path.realpath(_operation_output_root())
    resolved = resolve_operation_artifact_path(path)
    byte_size = os.path.getsize(resolved)
    return {
        "artifact_ref": f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}",
        "byte_size": byte_size,
        "reviewable": byte_size <= max_bytes,
    }


def _read_artifact_with_limit(path: str, start_line: int, max_lines: int, max_bytes: int) -> str:
    """Read a bounded artifact excerpt without materializing more than ``max_bytes``."""

    root = os.path.realpath(_operation_output_root())
    resolved = resolve_operation_artifact_path(path)
    if start_line < 1:
        raise ValueError("start_line must be at least 1")
    if max_lines < 1 or max_lines > 500:
        raise ValueError("max_lines must be between 1 and 500")

    with open(resolved, "rb") as artifact_file:
        lines = []
        content_size = 0
        total_lines = 0
        end_line = start_line + max_lines - 1
        while True:
            raw_line = artifact_file.readline(max_bytes + 1)
            if not raw_line:
                break
            total_lines += 1
            line_exceeds_limit = len(raw_line) > max_bytes and not raw_line.endswith(b"\n")
            if start_line <= total_lines <= end_line:
                normalized_line = raw_line.rstrip(b"\n")
                additional_size = len(normalized_line) + (1 if lines else 0)
                if line_exceeds_limit or content_size + additional_size > max_bytes:
                    raise ValueError(
                        f"{ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER}: Requested artifact page exceeds "
                        f"the {max_bytes}-byte limit"
                    )
                decoded_line = normalized_line.decode("utf-8", errors="replace")
                lines.append(decoded_line)
                content_size += additional_size
            if line_exceeds_limit:
                while raw_line and not raw_line.endswith(b"\n"):
                    raw_line = artifact_file.readline(8192)

    payload: dict[str, Any] = {
        "artifact_ref": f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}",
        "start_line": start_line,
        "end_line": start_line + len(lines) - 1 if lines else start_line - 1,
        "total_lines": total_lines,
        "content": "\n".join(lines),
    }
    return str(payload)


def _read_artifact_bytes(
    path: str,
    start_byte: int,
    max_bytes: int,
    start_line: int | None = None,
    max_lines: int | None = None,
) -> str:
    """Read one byte page, optionally narrowed to a bounded line range."""

    if start_byte < 0:
        raise ValueError("start_byte must be at least 0")
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be at least 1")
    if max_lines is not None and not 1 <= max_lines <= 500:
        raise ValueError("max_lines must be between 1 and 500")
    root = os.path.realpath(_operation_output_root())
    resolved = resolve_operation_artifact_path(path)
    byte_size = os.path.getsize(resolved)
    if start_byte > byte_size:
        raise ValueError("start_byte is beyond the artifact size")
    with open(resolved, "rb") as artifact_file:
        artifact_file.seek(start_byte)
        byte_page = artifact_file.read(max_bytes)

    content_start = 0
    current_line = 1
    if start_line is not None:
        while current_line < start_line and content_start < len(byte_page):
            newline = byte_page.find(b"\n", content_start)
            if newline < 0:
                content_start = len(byte_page)
                break
            content_start = newline + 1
            current_line += 1

    content_end = len(byte_page)
    if max_lines is not None and content_start < len(byte_page):
        line_end = content_start
        for _ in range(max_lines):
            newline = byte_page.find(b"\n", line_end)
            if newline < 0:
                line_end = len(byte_page)
                break
            line_end = newline + 1
        content_end = line_end

    content = byte_page[content_start:content_end]
    end_byte = start_byte + content_end
    payload: dict[str, Any] = {
        "artifact_ref": f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}",
        "start_byte": start_byte,
        "end_byte": end_byte,
        "next_start_byte": end_byte if end_byte < byte_size else None,
        "eof": end_byte >= byte_size,
        "byte_size": byte_size,
        "content": content.decode("utf-8", errors="replace"),
    }
    if max_lines is not None:
        returned_line_count = content.count(b"\n") + int(bool(content) and not content.endswith(b"\n"))
        payload["start_line"] = current_line
        payload["end_line"] = current_line + returned_line_count - 1
    return str(payload)


def create_artifact_reader(context_window_tokens: int) -> Any:
    """Create a context-bound reader for current-operation artifacts."""

    @tool(name="read_artifact")
    def read_artifact(
        path: str,
        start_line: int | None = None,
        max_lines: int | None = None,
        start_byte: int | None = None,
        max_bytes: int | None = None,
    ) -> str:
        """Read a bounded text excerpt or an explicit byte page."""

        root = os.path.realpath(_operation_output_root())
        try:
            if max_bytes is not None and start_byte is None:
                start_byte = 0
            if start_byte is not None or max_bytes is not None:
                if start_byte is None or max_bytes is None:
                    raise ValueError("byte paging requires start_byte and max_bytes")
                if start_line is not None and start_byte > 0:
                    raise ValueError("start_line requires start_byte to be zero")
                if max_bytes < 1 or max_bytes > artifact_max_bytes_for_context_window(context_window_tokens):
                    raise ValueError("max_bytes exceeds the artifact reader page budget")
                byte_max_lines = max_lines if max_lines is not None else (200 if start_line is not None else None)
                return _read_artifact_bytes(path, start_byte, max_bytes, start_line, byte_max_lines)
            start_line = 1 if start_line is None else start_line
            max_lines = 200 if max_lines is None else max_lines
            return _read_artifact_with_limit(
                path,
                start_line,
                max_lines,
                artifact_max_bytes_for_context_window(context_window_tokens),
            )
        except ValueError:
            directory = _resolve_operation_directory_path(path)
            if directory is not None:
                return _directory_read_guidance(directory, root)
            raise

    return read_artifact


def create_bounded_artifact_reader(
    max_reads: int | None = None,
    *,
    context_window_tokens: int,
    allowed_artifact_refs: Iterable[str] | None = None,
    omitted_large_artifact_sizes: Mapping[str, int] | None = None,
    max_reads_per_artifact: int | None = None,
    max_lines_per_read: int | None = None,
    context_reduction_state: dict[str, int] | None = None,
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
    resolved_max_bytes = artifact_max_bytes_for_context_window(context_window_tokens)

    allowed_paths = None
    if allowed_artifact_refs is not None:
        allowed_paths = {
            resolve_operation_artifact_path(reference)
            for reference in allowed_artifact_refs
        }
    omitted_large_paths = {
        resolve_operation_artifact_path(reference): int(byte_size)
        for reference, byte_size in (omitted_large_artifact_sizes or {}).items()
    }
    calls = 0
    reads_by_path: dict[str, int] = {}
    seen_pages: set[tuple[Any, ...]] = set()
    returned_ranges: dict[str, list[tuple[int, int]]] = {}
    guided_requests: set[tuple[str, tuple[Any, ...]]] = set()
    terminal_byte_page_guards: dict[str, str] = {}
    blocked_paths: set[str] = set()
    successful_pages: dict[str, list[dict[str, Any]]] = {}
    replayed_epochs: dict[str, set[int]] = {}
    reduction_state = context_reduction_state if context_reduction_state is not None else {"epoch": 0}

    def reduction_epoch() -> int:
        """Return the current evaluator-local context reduction epoch."""

        try:
            return max(0, int(reduction_state.get("epoch", 0)))
        except (AttributeError, TypeError, ValueError):
            return 0

    def compression_replay(resolved: str, page_range: tuple[int, int]) -> str | None:
        """Replay one prior page when a real context reduction made it stale."""

        epoch = reduction_epoch()
        if epoch < 1 or epoch in replayed_epochs.get(resolved, set()):
            return None
        for record in reversed(successful_pages.get(resolved, [])):
            start, end = record["range"]
            if record["epoch"] >= epoch or not (page_range[0] < end and start < page_range[1]):
                continue
            payload = ast.literal_eval(record["result"])
            payload["compression_recovery"] = True
            payload["compression_recovery_epoch"] = epoch
            replayed_epochs.setdefault(resolved, set()).add(epoch)
            return str(payload)
        return None

    def guide_once(resolved: str, page: tuple[Any, ...], reason: str, message: str) -> str:
        """Guide a first non-distinct request, then retain a repeat guard for stubborn retries."""

        key = (reason, page)
        if key in guided_requests:
            raise RuntimeError(
                f"{ARTIFACT_READ_REPEAT_GUARD_MARKER}: Repeated artifact read guidance for {reason}"
            )
        guided_requests.add(key)
        return _bounded_read_guidance(resolved, os.path.realpath(_operation_output_root()), reason, message)

    @tool(name="read_artifact")
    def bounded_read_artifact(
        path: str,
        start_line: int | None = None,
        max_lines: int | None = None,
        start_byte: int | None = None,
        max_bytes: int | None = None,
    ) -> str:
        """Read a bounded text excerpt or an explicit byte page."""

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
        if resolved in blocked_paths:
            raise RuntimeError(
                f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: This artifact page was already rejected as overlapping; "
                "return the requested JSON decision without rereading it"
            )
        if max_bytes is not None and start_byte is None:
            start_byte = 0
        byte_mode = start_byte is not None or max_bytes is not None
        if byte_mode and (start_byte is None or max_bytes is None):
            raise RuntimeError(f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: byte page parameters are invalid")
        if byte_mode and not 1 <= max_bytes <= resolved_max_bytes:
            raise RuntimeError(f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: byte page parameters are invalid")
        if byte_mode and start_line is not None and start_byte > 0:
            raise RuntimeError(f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: byte page parameters are invalid")
        if byte_mode and resolved in terminal_byte_page_guards:
            return guide_once(
                resolved,
                ("terminal_byte_page_guard",),
                "artifact_byte_page_guarded",
                terminal_byte_page_guards[resolved],
            )
        if resolved in omitted_large_paths and not byte_mode:
            raise RuntimeError(
                f"{ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER}: Artifact is {omitted_large_paths[resolved]} bytes "
                f"and requires explicit byte paging with start_byte and max_bytes"
            )
        if byte_mode:
            byte_max_lines = max_lines if max_lines is not None else (200 if start_line is not None else None)
            if max_lines_per_read is not None and byte_max_lines is not None:
                byte_max_lines = min(byte_max_lines, max_lines_per_read)
        else:
            start_line = 1 if start_line is None else start_line
            max_lines = 200 if max_lines is None else max_lines
            byte_max_lines = None
        if max_lines_per_read is not None and not byte_mode:
            max_lines = min(max_lines, max_lines_per_read)
        if start_line is not None and start_line < 1:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: Artifact page parameters are invalid"
            )
        if byte_max_lines is not None and not 1 <= byte_max_lines <= 500:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: Artifact page parameters are invalid"
            )
        if not byte_mode and not 1 <= max_lines <= 500:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: Artifact page parameters are invalid"
            )
        page = (
            (resolved, start_byte, max_bytes, start_line, byte_max_lines)
            if byte_mode
            else (resolved, start_line, max_lines)
        )
        try:
            result = (
                _read_artifact_bytes(resolved, start_byte, max_bytes, start_line, byte_max_lines)
                if byte_mode
                else _read_artifact_with_limit(resolved, start_line, max_lines, resolved_max_bytes)
            )
        except ValueError as error:
            if str(error).startswith(ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER):
                raise RuntimeError(str(error)) from error
            raise
        payload = ast.literal_eval(result)
        if byte_mode:
            page_range = (int(payload["start_byte"]), int(payload["end_byte"]))
        else:
            page_range = (int(payload["start_line"]), int(payload["end_line"]) + 1)
        if page in seen_pages:
            replay = compression_replay(resolved, page_range)
            if replay is not None:
                return replay
            blocked_paths.add(resolved)
            raise RuntimeError(
                f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: This exact artifact page was already returned during "
                "this evaluation"
            )
        if any(
            page_range[0] < end and start < page_range[1]
            for start, end in returned_ranges.get(resolved, [])
        ):
            replay = compression_replay(resolved, page_range)
            if replay is not None:
                return replay
            if byte_mode:
                terminal_byte_page_guards[resolved] = (
                    "This artifact has already received an overlapping byte page. "
                    "Use the returned page, provided digest, or another artifact."
                )
            blocked_paths.add(resolved)
            raise RuntimeError(
                f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: This artifact page overlaps content already returned "
                "during this evaluation"
            )
        if byte_mode and any(
            0 < min(abs(page_range[0] - end), abs(start - page_range[1])) <= ARTIFACT_NEARBY_BYTE_PAGE_GAP
            for start, end in returned_ranges.get(resolved, [])
        ):
            terminal_byte_page_guards[resolved] = (
                "This artifact received a near-adjacent byte-page request. Continue only from a returned "
                "next_start_byte or use the provided digest."
            )
            return guide_once(
                resolved,
                page,
                "nearby_page",
                f"This byte page is within {ARTIFACT_NEARBY_BYTE_PAGE_GAP} bytes of content already returned.",
            )
        if calls >= max_reads:
            return guide_once(
                resolved,
                page,
                "evaluator_read_budget_exhausted",
                f"The evaluator-wide successful-read budget ({max_reads}) is exhausted.",
            )
        if max_reads_per_artifact is not None and reads_by_path.get(resolved, 0) >= max_reads_per_artifact:
            return guide_once(
                resolved,
                page,
                "artifact_page_budget_exhausted",
                f"This artifact has reached its successful-page budget ({max_reads_per_artifact}).",
            )
        calls += 1
        reads_by_path[resolved] = reads_by_path.get(resolved, 0) + 1
        seen_pages.add(page)
        returned_ranges.setdefault(resolved, []).append(page_range)
        successful_pages.setdefault(resolved, []).append({
            "epoch": reduction_epoch(),
            "range": page_range,
            "result": result,
        })
        return result

    bounded_read_artifact.__name__ = "read_artifact"
    bounded_read_artifact._cyber_context_reduction_state = reduction_state
    return bounded_read_artifact
