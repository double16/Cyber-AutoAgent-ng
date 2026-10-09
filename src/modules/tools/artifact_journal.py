"""Filesystem storage of exact returned artifact pages for bounded review recovery."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4


class ReturnedArtifactJournal:
    """One invocation's returned evidence; only paths and small metadata remain in memory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory) / uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=False)
        self.index = self.directory / "index.jsonl"

    def record(self, tool: str, parameters: dict, result: str) -> None:
        payload = ast.literal_eval(result)
        content = payload.get("content", payload.get("matches", []))
        digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        record = {
            "tool": tool,
            "parameters": parameters,
            "source_ref": payload["artifact_ref"],
            "content_hash": digest,
            "payload": result,
        }
        path = self.directory / f"{uuid4().hex}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        with self.index.open("a", encoding="utf-8") as index:
            index.write(
                json.dumps({"path": path.name, "source_ref": record["source_ref"], "content_hash": digest}) + "\n"
            )

    def context(self, token_budget: int, estimate_tokens: Callable[[str], int]) -> str:
        """Pack complete returned payloads; disclose omissions without drawing conclusions."""
        if not self.index.exists():
            return ""
        groups: dict[str, list[dict]] = {}
        for line in self.index.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            groups.setdefault(item["content_hash"], []).append(item)
        included, omitted = [], []
        header = "\nExact evidence returned during this review (duplicate content grouped):\n"
        limitation = (
            "\nOmitted or unreadable evidence is an access limitation, not contradiction or missing observations.\n"
        )
        all_sources = sorted({item["source_ref"] for records in groups.values() for item in records})
        omission_label = "\nOmitted returned payloads due to context capacity: "
        reserve = estimate_tokens(header + omission_label + limitation + json.dumps(all_sources))
        token_budget = max(0, token_budget - reserve)
        for records in groups.values():
            sources = sorted({item["source_ref"] for item in records})
            record = json.loads((self.directory / records[0]["path"]).read_text(encoding="utf-8"))
            block = json.dumps(
                {
                    "source_refs": sources,
                    "tool": record["tool"],
                    "parameters": record["parameters"],
                    "returned_payload": record["payload"],
                },
                ensure_ascii=False,
            )
            if estimate_tokens(block) <= token_budget:
                included.append(block)
                token_budget -= estimate_tokens(block)
            else:
                omitted.extend(sources)
        return (
            header
            + "\n".join(included)
            + omission_label
            + json.dumps(sorted(set(omitted)))
            + limitation
        )
