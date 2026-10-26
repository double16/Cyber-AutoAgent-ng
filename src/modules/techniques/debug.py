"""Operation-local technique snapshots, deliberately separate from evidence artifacts."""

import hashlib
import json
import os
import uuid
from pathlib import Path


class TechniqueExports:
    def __init__(self, operation_root: str) -> None:
        self.directory = Path(operation_root) / "techniques"

    def write(self, revision: str, markdown: str, *, task: str = "") -> Path:
        stage = f"evaluated-{hashlib.sha256(task.encode()).hexdigest()[:16]}" if task else "produced"
        # IDs are controller-owned, but normalize defensively before constructing a filesystem name.
        identifier = str(uuid.UUID(revision))
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{identifier}.{stage}.md"
        if task and destination.exists():
            return destination
        temporary = self.directory / f".{uuid.uuid4()}.tmp"
        try:
            temporary.write_text(markdown, encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def write_rejected(self, activity: str, response: str, *, attempt: int, stage: str, reason: str) -> Path:
        """Keep bounded sensitive diagnostics outside evidence and published guidance."""
        directory = self.directory / "builds" / str(uuid.UUID(activity))
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = directory / f"attempt-{attempt}.json"
        temporary = directory / f".{uuid.uuid4()}.tmp"
        payload = {
            "attempt": attempt,
            "stage": stage,
            "reason": reason,
            "response": response[:128_000],
            "truncated": len(response) > 128_000,
        }
        try:
            with open(
                temporary, "x", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)
            ) as stream:
                json.dump(payload, stream, ensure_ascii=False)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
