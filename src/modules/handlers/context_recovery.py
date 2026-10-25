"""Lossless, deterministic emergency compaction of tool payloads."""

import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_PROTECTED_FIELDS = frozenset({
    "criterion_id", "acceptance_results", "evidence_receipts", "receipt_uid",
    "disposition", "plan_overview",
})


def latest_reasoning_index(messages: list[dict[str, Any]]) -> int | None:
    """Find the assistant turn whose complete reasoning must survive reduction."""
    return next((index for index in range(len(messages) - 1, -1, -1)
                 if messages[index].get("role") == "assistant" and any(
                     isinstance(block, dict) and "reasoningContent" in block
                     for block in messages[index].get("content", [])
                 )), None)


@contextmanager
def preserve_reasoning_turn(messages: list[dict[str, Any]]):
    """Temporarily pin the latest reasoning turn using the SDK's tool-pair protection."""
    index = latest_reasoning_index(messages)
    if index is None:
        yield
        return
    message = messages[index]
    original = deepcopy(message.get("metadata"))
    metadata = message.setdefault("metadata", {})
    metadata.setdefault("custom", {})["pinned"] = True
    try:
        yield
    finally:
        if original is None:
            message.pop("metadata", None)
        else:
            message["metadata"] = original


def _controller_record(value: Any) -> bool:
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return _controller_record(json.loads(value))
        except ValueError:
            return False
    if isinstance(value, dict):
        return bool(_PROTECTED_FIELDS.intersection(value)) or any(
            _controller_record(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_controller_record(item) for item in value)
    return False


def compact_recent_results(
    messages: list[dict[str, Any]], root: Path, input_limit: int,
    estimate: Callable[[], int | None], protected_indices: set[int],
) -> int:
    """Archive large results before replacing inline data; stop as soon as input fits."""
    compacted = 0
    for index, message in enumerate(messages):
        if index == 0 or index in protected_indices or message.get("role") == "system":
            continue
        for block in message.get("content", []):
            tokens = estimate()
            if tokens is None or tokens <= input_limit:
                return compacted
            result = block.get("toolResult") if isinstance(block, dict) else None
            if not isinstance(result, dict):
                continue
            content = result.get("content", [])
            if _controller_record(content):
                continue
            try:
                rendered = json.dumps(content, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                continue
            if len(rendered) <= 2000:
                continue
            digest = hashlib.sha256(rendered.encode()).hexdigest()
            relative = Path("artifacts/context_reduction") / f"{digest}.json"
            try:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(rendered, encoding="utf-8")
            except OSError:
                logger.warning("Could not archive tool result for context reduction", exc_info=True)
                continue
            result["content"] = [{"json": {
                "compacted": True,
                "artifact_ref": f"artifact:{relative.as_posix()}",
                "tool_use_id": result.get("toolUseId"),
                "status": result.get("status"),
                "original_chars": len(rendered),
                "preview": rendered[:256],
                "retrieval": "Read the archived original before drawing conclusions from omitted content.",
            }}]
            compacted += 1
    return compacted
