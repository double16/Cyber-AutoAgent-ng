"""Filesystem-backed detection of complete tool-result preview copies."""

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from strands.hooks import BeforeToolCallEvent, HookProvider

logger = logging.getLogger(__name__)


class PreviewCopyGuard(HookProvider):
    """Share preview records across agents without retaining their contents in memory."""

    def __init__(self, artifacts_dir: Path | None) -> None:
        self.records_dir = artifacts_dir / ".preview_copies" if artifacts_dir is not None else None

    def register_hooks(self, registry: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)

    def record(self, preview: str, artifact_ref: str) -> None:
        """Atomically publish a deduplicated preview record for this operation."""
        if not preview or self.records_dir is None:
            return
        temporary = None
        try:
            self.records_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(preview.encode("utf-8")).hexdigest()
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.records_dir, delete=False) as output:
                temporary = Path(output.name)
                json.dump({"preview": preview, "artifact_ref": artifact_ref}, output)
            os.replace(temporary, self.records_dir / f"{digest}.json")
        except OSError:
            logger.warning("Could not persist preview-copy record", exc_info=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _strings(value: Any) -> Iterator[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from PreviewCopyGuard._strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from PreviewCopyGuard._strings(item)

    def before_tool(self, event: Any) -> None:
        """Cancel calls containing a complete exact preview in any string argument."""
        if self.records_dir is None or not self.records_dir.is_dir() or event.cancel_tool:
            return
        arguments = event.tool_use.get("input", {})
        for path in self.records_dir.glob("*.json"):
            try:
                with path.open(encoding="utf-8") as source:
                    record = json.load(source)
                preview = record["preview"]
                reference = record["artifact_ref"]
                if not isinstance(preview, str) or not preview or not isinstance(reference, str):
                    continue
                if any(preview in value for value in self._strings(arguments)):
                    event.cancel_tool = (
                        "INCOMPLETE_PREVIEW_COPY: Tool arguments contain an entire incomplete preview. "
                        f"Use the complete saved artifact at {reference}; do not save the preview as source data."
                    )
                    return
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("Could not read preview-copy record %s", path, exc_info=True)
