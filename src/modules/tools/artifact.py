"""Read-only access to artifacts produced by the current operation."""

import ast
import os
import re
from collections.abc import Iterable, Mapping
from math import ceil
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
TOOL_RESULT_CONTEXT_FRACTION = 0.10
TOOL_RESULT_CHARS_PER_TOKEN = 4
TOOL_RESULT_MAX_CHARS = 30_000
ARTIFACT_SEARCH_MAX_PATTERN_CHARS = 512
ARTIFACT_SEARCH_MAX_CONTEXT_LINES = 10
ARTIFACT_SEARCH_MAX_MATCHES = 50


def artifact_max_bytes_for_context_window(context_window_tokens: int) -> int:
    """Return the clamped UTF-8 page budget for one resolved input context window."""

    if isinstance(context_window_tokens, bool) or not isinstance(context_window_tokens, int):
        raise TypeError("context_window_tokens must be a positive integer")
    if context_window_tokens < 1:
        raise ValueError("context_window_tokens must be a positive integer")
    context_budget = int(context_window_tokens * ARTIFACT_BYTES_PER_TOKEN * ARTIFACT_PAGE_CONTEXT_FRACTION)
    return min(ARTIFACT_MAX_BYTES_PER_READ, max(ARTIFACT_MIN_BYTES_PER_READ, context_budget))


def resolve_tool_result_max_chars(
    context_window_tokens: int,
    configured_max_chars: str | None = None,
) -> int:
    """Resolve the router's character ceiling from context tokens or an override."""

    if isinstance(context_window_tokens, bool) or not isinstance(context_window_tokens, int):
        raise TypeError("context_window_tokens must be a positive integer")
    if context_window_tokens < 1:
        raise ValueError("context_window_tokens must be a positive integer")

    computed = min(
        TOOL_RESULT_MAX_CHARS,
        ceil(context_window_tokens * TOOL_RESULT_CONTEXT_FRACTION * TOOL_RESULT_CHARS_PER_TOKEN),
    )
    if configured_max_chars is None:
        return computed
    try:
        configured = int(configured_max_chars)
    except (TypeError, ValueError):
        return computed
    return configured if configured > 0 else computed


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


def _directory_read_guidance(directory: str, root: str, max_output_chars: int | None = None) -> str:
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

    def payload_for(references: list[str]) -> dict[str, Any]:
        return {
            "status": "directory",
            "message": "read_artifact requires one file. Retry with one listed artifact_ref.",
            "directory": f"artifact:{os.path.relpath(directory, root).replace(os.sep, '/')}",
            "artifact_refs": references,
            "omitted_file_count": len(files) - len(references),
        }

    payload = payload_for(listed_files)
    while listed_files and max_output_chars is not None and len(str(payload)) > max_output_chars:
        listed_files.pop()
        payload = payload_for(listed_files)
    if max_output_chars is not None and len(str(payload)) > max_output_chars:
        raise ValueError("artifact reader output limit is too small for directory guidance")
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


def _read_artifact_with_limit(
    path: str,
    start_line: int,
    max_lines: int,
    max_bytes: int,
    max_output_chars: int | None = None,
) -> str:
    """Read a bounded artifact excerpt, returning a byte page when line mode overflows."""

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
        selected_start_byte: int | None = None
        while True:
            line_start_byte = artifact_file.tell()
            raw_line = artifact_file.readline(max_bytes + 1)
            if not raw_line:
                break
            total_lines += 1
            line_exceeds_limit = len(raw_line) > max_bytes and not raw_line.endswith(b"\n")
            if start_line <= total_lines <= end_line:
                if selected_start_byte is None:
                    selected_start_byte = line_start_byte
                normalized_line = raw_line.rstrip(b"\n")
                additional_size = len(normalized_line) + (1 if lines else 0)
                if line_exceeds_limit or content_size + additional_size > max_bytes:
                    return _read_artifact_bytes(
                        resolved,
                        selected_start_byte,
                        max_bytes,
                        max_output_chars=max_output_chars,
                        page_start_line=start_line,
                        page_max_lines=max_lines,
                    )
                decoded_line = normalized_line.decode("utf-8", errors="replace")
                lines.append(decoded_line)
                content_size += additional_size
            if line_exceeds_limit:
                while raw_line and not raw_line.endswith(b"\n"):
                    raw_line = artifact_file.readline(8192)

    def payload_for(page_lines: list[str]) -> dict[str, Any]:
        return {
            "artifact_ref": f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}",
            "start_line": start_line,
            "end_line": start_line + len(page_lines) - 1 if page_lines else start_line - 1,
            "total_lines": total_lines,
            "content": "\n".join(page_lines),
        }

    original_line_count = len(lines)
    payload = payload_for(lines)
    while lines and max_output_chars is not None and len(str(payload)) > max_output_chars:
        lines.pop()
        payload = payload_for(lines)
    if max_output_chars is not None and original_line_count and not lines:
        raise ValueError("artifact reader output limit is too small for one complete line")
    if max_output_chars is not None and len(str(payload)) > max_output_chars:
        raise ValueError("artifact reader output limit is too small for result metadata")
    return str(payload)


def _read_artifact_bytes(
    path: str,
    start_byte: int,
    max_bytes: int,
    start_line: int | None = None,
    max_lines: int | None = None,
    max_output_chars: int | None = None,
    *,
    page_start_line: int | None = None,
    page_max_lines: int | None = None,
) -> str:
    """Read one byte page, optionally narrowed to a bounded line range."""

    if start_byte < 0:
        raise ValueError("start_byte must be at least 0")
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be at least 1")
    if max_lines is not None and not 1 <= max_lines <= 500:
        raise ValueError("max_lines must be between 1 and 500")
    if page_start_line is not None and page_start_line < 1:
        raise ValueError("page_start_line must be at least 1")
    if page_max_lines is not None and not 1 <= page_max_lines <= 500:
        raise ValueError("page_max_lines must be between 1 and 500")
    root = os.path.realpath(_operation_output_root())
    resolved = resolve_operation_artifact_path(path)
    byte_size = os.path.getsize(resolved)
    if start_byte > byte_size:
        raise ValueError("start_byte is beyond the artifact size")
    with open(resolved, "rb") as artifact_file:
        artifact_file.seek(start_byte)
        byte_page = artifact_file.read(max_bytes)

    content_start = 0
    current_line = page_start_line if page_start_line is not None else 1
    if start_line is not None:
        while current_line < start_line and content_start < len(byte_page):
            newline = byte_page.find(b"\n", content_start)
            if newline < 0:
                content_start = len(byte_page)
                break
            content_start = newline + 1
            current_line += 1

    content_end = len(byte_page)
    content_line_limit = page_max_lines if page_max_lines is not None else max_lines
    if content_line_limit is not None and content_start < len(byte_page):
        line_end = content_start
        for _ in range(content_line_limit):
            newline = byte_page.find(b"\n", line_end)
            if newline < 0:
                line_end = len(byte_page)
                break
            line_end = newline + 1
        content_end = line_end

    content = byte_page[content_start:content_end]

    def payload_for(page_content: bytes) -> dict[str, Any]:
        page_end = content_start + len(page_content)
        end_byte = start_byte + page_end
        next_start_byte = end_byte if end_byte < byte_size else None
        payload: dict[str, Any] = {
            "artifact_ref": f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}",
            "start_byte": start_byte,
            "end_byte": end_byte,
            "next_start_byte": next_start_byte,
            "eof": end_byte >= byte_size,
            "byte_size": byte_size,
            "content": page_content.decode("utf-8", errors="replace"),
        }
        if content_line_limit is not None:
            returned_line_count = page_content.count(b"\n") + int(
                bool(page_content) and not page_content.endswith(b"\n")
            )
            payload["start_line"] = current_line
            payload["end_line"] = current_line + returned_line_count - 1
        if next_start_byte is not None:
            reached_byte_limit = len(page_content) == max_bytes
            payload["truncated"] = True
            payload["truncation_reason"] = (
                "byte_limit_reached" if reached_byte_limit else "line_limit_reached"
            )
            payload["pagination"] = {
                "path": payload["artifact_ref"],
                "start_byte": next_start_byte,
                "max_bytes": max_bytes,
                "message": "Continue with start_byte=next_start_byte and the same max_bytes.",
            }
        else:
            payload["truncated"] = False
        return payload

    payload = payload_for(content)
    if max_output_chars is not None and len(str(payload)) > max_output_chars:
        lower, upper = 0, len(content)
        while lower < upper:
            midpoint = (lower + upper + 1) // 2
            candidate = payload_for(content[:midpoint])
            if len(str(candidate)) <= max_output_chars:
                lower = midpoint
            else:
                upper = midpoint - 1
        content = content[:lower]
        payload = payload_for(content)
        if content and payload["next_start_byte"] is not None:
            payload["truncated"] = True
            payload["truncation_reason"] = "output_limit_reached"
            while content and len(str(payload)) > max_output_chars:
                content = content[:-1]
                payload = payload_for(content)
                payload["truncated"] = True
                payload["truncation_reason"] = "output_limit_reached"
    if max_output_chars is not None and len(str(payload)) > max_output_chars:
        raise ValueError("artifact reader output limit is too small for result metadata")
    return str(payload)


def _search_artifact(
    path: str,
    pattern: str,
    *,
    regex: bool,
    context_lines: int,
    max_matches: int,
    match_offset: int,
    max_output_chars: int | None = None,
) -> str:
    """Search the full artifact and return one bounded slice of match context."""

    if not pattern:
        raise ValueError("search_pattern is required for search mode")
    if len(pattern) > ARTIFACT_SEARCH_MAX_PATTERN_CHARS:
        raise ValueError(
            f"search_pattern exceeds the maximum length of {ARTIFACT_SEARCH_MAX_PATTERN_CHARS} characters"
        )
    if not 0 <= context_lines <= ARTIFACT_SEARCH_MAX_CONTEXT_LINES:
        raise ValueError(f"context_lines must be between 0 and {ARTIFACT_SEARCH_MAX_CONTEXT_LINES}")
    if not 1 <= max_matches <= ARTIFACT_SEARCH_MAX_MATCHES:
        raise ValueError(f"max_matches must be between 1 and {ARTIFACT_SEARCH_MAX_MATCHES}")
    if match_offset < 0:
        raise ValueError("match_offset must be at least 0")

    try:
        matcher = re.compile(pattern) if regex else None
    except re.error as error:
        raise ValueError(f"invalid regular expression: {error}") from error
    resolved = resolve_operation_artifact_path(path)
    root = os.path.realpath(_operation_output_root())
    matching_lines: list[tuple[int, int, int]] = []
    total_lines = 0
    scanned_bytes = 0
    with open(resolved, "rb") as artifact_file:
        while True:
            raw_line = artifact_file.readline()
            if not raw_line:
                break
            total_lines += 1
            scanned_bytes += len(raw_line)
            line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
            found = matcher.search(line) if matcher is not None else None
            column = line.find(pattern) if matcher is None else -1
            if found is not None or column >= 0:
                start, end = found.span() if found is not None else (column, column + len(pattern))
                matching_lines.append((total_lines, start, end))

    selected_lines = matching_lines[match_offset : match_offset + max_matches]
    matches: list[dict[str, Any]] = []
    if selected_lines:
        context_numbers = {
            context_number
            for number, _, _ in selected_lines
            for context_number in range(max(1, number - context_lines), min(total_lines, number + context_lines) + 1)
        }
        with open(resolved, "rb") as artifact_file:
            context_lines_by_number: dict[int, str] = {}
            line_number = 0
            while line_number < max(context_numbers):
                raw_line = artifact_file.readline()
                if not raw_line:
                    break
                line_number += 1
                if line_number in context_numbers:
                    context_lines_by_number[line_number] = raw_line.rstrip(b"\r\n").decode(
                        "utf-8", errors="replace"
                    )
        def excerpt(line: str, start: int, end: int, width: int) -> tuple[str, int]:
            first = max(0, min(start - max(0, (width - (end - start)) // 2), len(line) - width))
            return line[first:first + width], first + 1

        for number, start, end in selected_lines:
            line = context_lines_by_number[number]
            content, first_column = excerpt(line, start, end, max(320, end - start))
            matches.append(
                {
                    "line": number,
                    "content": content,
                    "content_start_column": first_column,
                    "content_truncated": len(content) < len(line),
                    "match_column": start + 1,
                    "context": [
                        {
                            "line": context_number,
                            "content": content if context_number == number else context_lines_by_number[context_number][:160],
                            "content_truncated": (
                                len(content) < len(line) if context_number == number
                                else len(context_lines_by_number[context_number]) > 160
                            ),
                        }
                        for context_number in range(
                            max(1, number - context_lines), min(total_lines, number + context_lines) + 1
                        )
                    ],
                }
            )

    next_match_offset = match_offset + len(matches) if match_offset + len(matches) < len(matching_lines) else None
    payload: dict[str, Any] = {
        "artifact_ref": f"artifact:{os.path.relpath(resolved, root).replace(os.sep, '/')}",
        "mode": "search",
        "regex": regex,
        "match_count": len(matching_lines),
        "matches": matches,
        "match_offset": match_offset,
        "scanned_lines": total_lines,
        "scanned_bytes": scanned_bytes,
        "next_match_offset": next_match_offset,
        "eof": True,
        "match_limit_reached": next_match_offset is not None,
    }
    if max_output_chars is not None:
        while len(matches) > 1 and len(str(payload)) > max_output_chars:
            matches.pop()
            payload["next_match_offset"] = (
                match_offset + len(matches) if match_offset + len(matches) < len(matching_lines) else None
            )
            payload["match_limit_reached"] = payload["next_match_offset"] is not None
        if matches and len(str(payload)) > max_output_chars:
            matches[0]["context"] = []
        if matches and len(str(payload)) > max_output_chars:
            match = matches[0]
            original = context_lines_by_number[match["line"]]
            selected = selected_lines[0]
            minimum = max(1, selected[2] - selected[1])
            lower, upper = minimum, len(match["content"])
            while lower < upper:
                midpoint = (lower + upper + 1) // 2
                match["content"], match["content_start_column"] = excerpt(
                    original, selected[1], selected[2], midpoint
                )
                match["content_truncated"] = len(match["content"]) < len(original)
                if len(str(payload)) <= max_output_chars:
                    lower = midpoint
                else:
                    upper = midpoint - 1
            match["content"], match["content_start_column"] = excerpt(
                original, selected[1], selected[2], lower
            )
            match["content_truncated"] = len(match["content"]) < len(original)
        if len(str(payload)) > max_output_chars:
            raise ValueError("artifact search output limit is too small for a matching excerpt")
    return str(payload)


def create_artifact_reader(context_window_tokens: int, *, max_output_chars: int | None = None) -> Any:
    """Create a context-bound reader for current-operation artifacts."""

    if max_output_chars is not None and max_output_chars < 1:
        raise ValueError("max_output_chars must be at least 1")

    @tool(name="read_artifact")
    def read_artifact(
        path: str,
        start_line: int | None = None,
        max_lines: int | None = None,
        start_byte: int | None = None,
        max_bytes: int | None = None,
    ) -> str:
        """Read a bounded current-operation artifact with line or byte paging."""

        root = os.path.realpath(_operation_output_root())
        try:
            if max_bytes is not None and start_byte is None:
                start_byte = 0
            if start_byte is not None or max_bytes is not None:
                if start_byte is None or max_bytes is None:
                    missing = "start_byte" if start_byte is None else "max_bytes"
                    raise ValueError(
                        f"byte paging requires both start_byte and max_bytes; missing {missing}"
                    )
                if start_line is not None and start_byte > 0:
                    raise ValueError(
                        f"start_line requires start_byte=0: start_line={start_line} cannot be combined with "
                        f"start_byte={start_byte}; omit start_line or use start_byte=0"
                    )
                max_page_bytes = artifact_max_bytes_for_context_window(context_window_tokens)
                if max_bytes < 1:
                    raise ValueError(f"max_bytes={max_bytes} must be at least 1 byte")
                if max_bytes > max_page_bytes:
                    raise ValueError(
                        f"max_bytes={max_bytes} exceeds the maximum byte page size of {max_page_bytes} bytes"
                    )
                byte_max_lines = max_lines if max_lines is not None else (200 if start_line is not None else None)
                return _read_artifact_bytes(
                    path,
                    start_byte,
                    max_bytes,
                    start_line,
                    byte_max_lines,
                    max_output_chars,
                )
            start_line = 1 if start_line is None else start_line
            max_lines = 200 if max_lines is None else max_lines
            return _read_artifact_with_limit(
                path,
                start_line,
                max_lines,
                artifact_max_bytes_for_context_window(context_window_tokens),
                max_output_chars,
            )
        except ValueError:
            directory = _resolve_operation_directory_path(path)
            if directory is not None:
                return _directory_read_guidance(directory, root, max_output_chars)
            raise

    return read_artifact


def create_artifact_searcher(*, max_output_chars: int | None = None) -> Any:
    """Create a full-artifact literal and regular-expression search tool."""

    if max_output_chars is not None and max_output_chars < 1:
        raise ValueError("max_output_chars must be at least 1")

    @tool(name="search_artifact")
    def search_artifact(
        path: str,
        search_pattern: str,
        search_regex: bool = False,
        context_lines: int = 2,
        max_matches: int = 25,
        match_offset: int = 0,
    ) -> str:
        """Search an entire current-operation artifact and return bounded matching context."""

        return _search_artifact(
            path,
            search_pattern,
            regex=search_regex,
            context_lines=context_lines,
            max_matches=max_matches,
            match_offset=match_offset,
            max_output_chars=max_output_chars,
        )

    return search_artifact


def create_bounded_artifact_searcher(
    *,
    max_searches: int | None = None,
    allowed_artifact_refs: Iterable[str] | None = None,
    max_searches_per_artifact: int | None = None,
    max_output_chars: int | None = None,
) -> Any:
    """Create a scoped full-artifact search tool with optional path and call limits."""

    if max_searches is None:
        try:
            max_searches = max(1, int(os.getenv("CYBER_WORKFLOW_ARTIFACT_READ_LIMIT", "4")))
        except ValueError:
            max_searches = 4
    if max_searches_per_artifact is not None and max_searches_per_artifact < 1:
        raise ValueError("max_searches_per_artifact must be at least 1")
    if max_output_chars is not None and max_output_chars < 1:
        raise ValueError("max_output_chars must be at least 1")
    allowed_paths = (
        {resolve_operation_artifact_path(reference) for reference in allowed_artifact_refs}
        if allowed_artifact_refs is not None
        else None
    )
    calls = 0
    searches_by_path: dict[str, int] = {}

    @tool(name="search_artifact")
    def search_artifact(
        path: str,
        search_pattern: str,
        search_regex: bool = False,
        context_lines: int = 2,
        max_matches: int = 25,
        match_offset: int = 0,
    ) -> str:
        """Search an entire allowed artifact and return bounded matching context."""

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
        if calls >= max_searches:
            raise RuntimeError(f"{ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER}: Search limit reached")
        if (
            max_searches_per_artifact is not None
            and searches_by_path.get(resolved, 0) >= max_searches_per_artifact
        ):
            raise RuntimeError(f"{ARTIFACT_PAGE_LIMIT_REACHED_MARKER}: Artifact search limit reached")
        try:
            result = _search_artifact(
                resolved,
                search_pattern,
                regex=search_regex,
                context_lines=context_lines,
                max_matches=max_matches,
                match_offset=match_offset,
                max_output_chars=max_output_chars,
            )
        except ValueError as error:
            raise RuntimeError(f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: {error}") from error
        calls += 1
        searches_by_path[resolved] = searches_by_path.get(resolved, 0) + 1
        return result

    return search_artifact


def create_bounded_artifact_reader(
    max_reads: int | None = None,
    *,
    context_window_tokens: int,
    allowed_artifact_refs: Iterable[str] | None = None,
    omitted_large_artifact_sizes: Mapping[str, int] | None = None,
    max_reads_per_artifact: int | None = None,
    max_lines_per_read: int | None = None,
    max_output_chars: int | None = None,
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
    if max_output_chars is not None and max_output_chars < 1:
        raise ValueError("max_output_chars must be at least 1")
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
    returned_ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
    guided_requests: set[tuple[str, tuple[Any, ...]]] = set()
    terminal_byte_page_guards: dict[str, str] = {}
    blocked_pagination_modes: set[tuple[str, str]] = set()
    successful_pages: dict[tuple[str, str], list[dict[str, Any]]] = {}
    replayed_epochs: dict[tuple[str, str], set[int]] = {}
    reduction_state = context_reduction_state if context_reduction_state is not None else {"epoch": 0}

    def reduction_epoch() -> int:
        """Return the current evaluator-local context reduction epoch."""

        try:
            return max(0, int(reduction_state.get("epoch", 0)))
        except (AttributeError, TypeError, ValueError):
            return 0

    def compression_replay(
        resolved: str,
        pagination_mode: str,
        page_range: tuple[int, int],
    ) -> str | None:
        """Replay one prior page when a real context reduction made it stale."""

        epoch = reduction_epoch()
        state_key = (resolved, pagination_mode)
        if epoch < 1 or epoch in replayed_epochs.get(state_key, set()):
            return None
        for record in reversed(successful_pages.get(state_key, [])):
            start, end = record["range"]
            if record["epoch"] >= epoch or not (page_range[0] < end and start < page_range[1]):
                continue
            payload = ast.literal_eval(record["result"])
            payload["compression_recovery"] = True
            payload["compression_recovery_epoch"] = epoch
            replayed_epochs.setdefault(state_key, set()).add(epoch)
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
        """Read a bounded current-operation artifact with line or byte paging."""

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
        if max_bytes is not None and start_byte is None:
            start_byte = 0
        byte_mode = start_byte is not None or max_bytes is not None
        pagination_mode = "byte" if byte_mode else "line"
        state_key = (resolved, pagination_mode)
        if state_key in blocked_pagination_modes:
            raise RuntimeError(
                f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: This artifact {pagination_mode} page was already rejected "
                "as overlapping; return the requested JSON decision without rereading it"
            )
        if byte_mode and (start_byte is None or max_bytes is None):
            missing = "start_byte" if start_byte is None else "max_bytes"
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: byte paging requires both start_byte and max_bytes; "
                f"missing {missing}"
            )
        if byte_mode and max_bytes < 1:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: max_bytes={max_bytes} must be at least 1 byte"
            )
        if byte_mode and max_bytes > resolved_max_bytes:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: max_bytes={max_bytes} exceeds the maximum "
                f"byte page size of {resolved_max_bytes} bytes"
            )
        if byte_mode and start_line is not None and start_byte > 0:
            raise RuntimeError(
                f"{ARTIFACT_READ_POLICY_VIOLATION_MARKER}: start_line requires start_byte=0: "
                f"start_line={start_line} cannot be combined with start_byte={start_byte}; "
                "omit start_line or use start_byte=0"
            )
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
                "and requires explicit byte paging with start_byte and max_bytes; use max_lines to narrow "
                "line-based reads when applicable"
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
                _read_artifact_bytes(
                    resolved,
                    start_byte,
                    max_bytes,
                    start_line,
                    byte_max_lines,
                    max_output_chars,
                )
                if byte_mode
                else _read_artifact_with_limit(
                    resolved,
                    start_line,
                    max_lines,
                    resolved_max_bytes,
                    max_output_chars,
                )
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
            replay = compression_replay(resolved, pagination_mode, page_range)
            if replay is not None:
                return replay
            blocked_pagination_modes.add(state_key)
            raise RuntimeError(
                f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: This exact artifact page was already returned during "
                "this evaluation"
            )
        if any(
            page_range[0] < end and start < page_range[1]
            for start, end in returned_ranges.get(state_key, [])
        ):
            replay = compression_replay(resolved, pagination_mode, page_range)
            if replay is not None:
                return replay
            if byte_mode:
                terminal_byte_page_guards[resolved] = (
                    "This artifact has already received an overlapping byte page. "
                    "Use the returned page, provided digest, or another artifact."
                )
            blocked_pagination_modes.add(state_key)
            raise RuntimeError(
                f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: This artifact page overlaps content already returned "
                "during this evaluation"
            )
        if byte_mode and any(
            0 < min(abs(page_range[0] - end), abs(start - page_range[1])) <= ARTIFACT_NEARBY_BYTE_PAGE_GAP
            for start, end in returned_ranges.get(state_key, [])
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
        returned_ranges.setdefault(state_key, []).append(page_range)
        successful_pages.setdefault(state_key, []).append({
            "epoch": reduction_epoch(),
            "range": page_range,
            "result": result,
        })
        return result

    bounded_read_artifact.__name__ = "read_artifact"
    bounded_read_artifact._cyber_context_reduction_state = reduction_state
    return bounded_read_artifact
