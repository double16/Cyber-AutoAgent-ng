"""Filesystem storage of exact returned artifact pages for bounded review recovery."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

_active_inspections: ContextVar[ReturnedArtifactJournal | None] = ContextVar("active_inspections", default=None)


class ReturnedArtifactJournal:
    """One invocation's returned evidence; only paths and small metadata remain in memory."""

    def __init__(self, directory: str | Path, *, binding: dict | None = None, operation_root: str | Path = ".") -> None:
        self.binding = binding
        self.operation_root = Path(operation_root).resolve()
        identity = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest() if binding else uuid4().hex
        self.directory = Path(directory) / identity
        self.directory.mkdir(parents=True, exist_ok=bool(binding))
        self.index = self.directory / "index.jsonl"

    def _reference(self, path: Path) -> str:
        return "artifact:" + path.resolve().relative_to(self.operation_root).as_posix()

    def record_inspection(self, tool: str, call_id: str, parameters: dict, result: str, sources: tuple[str, ...]) -> str:
        """Persist exactly delivered content immediately; retain no large payload in tool outcomes."""
        digest = hashlib.sha256(result.encode()).hexdigest()
        identity = hashlib.sha256(f"{tool}|{call_id}|{digest}".encode()).hexdigest()
        path = self.directory / f"{identity}.json"
        record = {"binding": self.binding, "tool": tool, "call_id": call_id, "parameters": parameters,
                  "source_refs": list(sources), "content_hash": digest, "payload": result}
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        reference = self._reference(path)
        indexed = self._entries()
        if not any(entry["reference"] == reference for entry in indexed):
            with self.index.open("a", encoding="utf-8") as index:
                index.write(json.dumps({"reference": reference, "content_hash": digest}) + "\n")
        return reference

    def _entries(self) -> list[dict]:
        return [json.loads(line) for line in self.index.read_text().splitlines()] if self.index.exists() else []

    def load_inspection(self, reference: str) -> dict:
        """Accept only indexed results belonging to this exact operation/task/snapshot."""
        entry = next((entry for entry in self._entries() if entry["reference"] == reference), None)
        if entry is None:
            raise ValueError("Inspection result is not available to this task and snapshot")
        path = (self.operation_root / reference.removeprefix("artifact:")).resolve()
        if path.parent != self.directory.resolve():
            raise ValueError("Inspection result is outside its journal")
        record = json.loads(path.read_text())
        if record.get("binding") != self.binding or hashlib.sha256(record["payload"].encode()).hexdigest() != entry["content_hash"]:
            raise ValueError("Inspection result binding or content changed")
        return record

    def replay_context(
        self, token_budget: int, estimate_tokens: Callable[[str], int], *, references: set[str] | None = None,
    ) -> str:
        """Balance exact source content and recoverable slices, retaining bounded evidence anchors."""
        if not self.index.exists():
            return ""
        header = ("\n## Retained inspection evidence\nExact previously returned results. availability classifies "
                  "content only, not semantic support; metadata_only may supplement content-bearing evidence.\n")
        footer = ("\n## Inspection replay access\nUse read_inspection_result for retained evidence or this index: "
                  + self._reference(self.index) + "\nOmitted or unreadable evidence is not negative evidence.\n")
        anchor_heading = ("\n## Inspection evidence anchors\n"
                          "Verbatim prefixes; recover complete results using their inspection references.\n")
        remaining = max(0, token_budget - estimate_tokens(
            header + footer + anchor_heading + "Omitted or unreadable returned results: 999999.\n",
        ))
        included, seen = [], set()
        omitted = 0
        candidates = []
        for entry in reversed(self._entries()):
            if references is not None and entry["reference"] not in references:
                continue
            if entry["content_hash"] in seen:
                continue
            seen.add(entry["content_hash"])
            try:
                record = self.load_inspection(entry["reference"])
                # Prefer exact content reads and structured excerpts to broad search summaries.
                priority = 2
                if record["tool"] in {"read_artifact", "read_hypothesis_input"}:
                    priority = 0 if "start_line" in record.get("parameters", {}) else 1
                if record["tool"] == "get_inventory_item" and inspection_has_content(record):
                    priority = 0
                candidates.append((priority, entry, record))
            except (OSError, ValueError, KeyError):
                omitted += 1
        candidates.sort(key=lambda item: item[0])
        source_counts = {}
        ranked = []
        for priority, entry, record in candidates:
            source = tuple(record.get("source_refs", [])) or (
                str(record.get("parameters", {}).get("path", record["tool"])),
            )
            parameters = record.get("parameters", {})
            source = (source, parameters.get("start_line"), parameters.get("start_column"))
            occurrence = source_counts.get(source, 0)
            source_counts[source] = occurrence + 1
            ranked.append((occurrence, priority, entry, record))
        ranked.sort(key=lambda item: (item[0], item[1]))
        candidates = [(priority, entry, record) for _, priority, entry, record in ranked]
        # Divide capacity across sources rather than letting one large result consume it.
        anchors = []
        anchor_budget = min(remaining // 3, 1200)
        for _, entry, record in candidates[:8]:
            text = inspection_anchor(record)
            if not text:
                continue
            block = json.dumps({"inspection_ref": entry["reference"], "availability": "content_bearing",
                                "verbatim_content_prefix": text},
                               ensure_ascii=False)
            cost = estimate_tokens(block)
            if cost <= anchor_budget:
                anchors.append(block)
                anchor_budget -= cost
                remaining -= cost
        anchor_section = anchor_heading + "\n".join(anchors) + "\n" if anchors else ""
        share = max(0, remaining // max(1, min(8, len(candidates))))
        for _, entry, record in candidates:
            availability = "content_bearing" if inspection_has_content(record) else "metadata_only"
            block = json.dumps({"inspection_ref": entry["reference"], "availability": availability, **record},
                               ensure_ascii=False)
            cost = estimate_tokens(block)
            if cost > min(remaining, share):
                serialized = json.dumps(record, ensure_ascii=False)
                length = min(len(serialized), 6000)
                while length:
                    block = json.dumps({"inspection_ref": entry["reference"], "availability": availability, "offset": 0,
                                        "next_offset": length, "text": serialized[:length]},
                                       ensure_ascii=False)
                    if estimate_tokens(block) <= min(remaining, share):
                        break
                    length //= 2
                if not length:
                    omitted += 1
                    continue
                omitted += 1
            included.append(block)
            remaining -= estimate_tokens(block)
        return anchor_section + header + "\n".join(included) + footer + f"Omitted or unreadable returned results: {omitted}.\n"

    def inspection_availability(self, references: list[str] | None = None) -> dict[str, str]:
        """Classify exact task-bound results without judging their relevance or support."""
        references = references if references is not None else [entry["reference"] for entry in self._entries()]
        result = {}
        for reference in references:
            try:
                record = self.load_inspection(reference)
                result[reference] = "content_bearing" if inspection_has_content(record) else "metadata_only"
            except (OSError, ValueError, KeyError):
                result[reference] = "unavailable"
        return result

    def reader(self, max_output_chars: int = 30_000):
        """Allow four cached-result pages; never read original evidence sources."""
        from strands import tool

        reads = 0

        @tool(name="read_inspection_result", inputSchema={"json": {
            "type": "object", "properties": {
                "inspection_ref": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 4000},
            }, "required": ["inspection_ref"],
        }})
        def read_inspection_result(inspection_ref: str, offset: int = 0, max_chars: int = 4000) -> str:
            """Page a retained result or its index from this task and frozen snapshot only."""
            nonlocal reads
            if offset < 0 or not 1 <= max_chars <= 10000:
                raise ValueError("offset must be non-negative and max_chars must be from 1 to 10000")
            if reads >= 4:
                raise ValueError("Saved-result read allowance exhausted; use evidence already returned")
            if inspection_ref == self._reference(self.index):
                text = json.dumps(self._entries())
            else:
                text = json.dumps(self.load_inspection(inspection_ref), ensure_ascii=False)
            length = min(max_chars, len(text[offset:]))
            while True:
                payload = json.dumps({"inspection_ref": inspection_ref, "text": text[offset:offset + length],
                                      "next_offset": offset + length if offset + length < len(text) else None},
                                     ensure_ascii=False)
                if len(payload) <= max_output_chars:
                    reads += 1
                    return payload
                if length == 0:
                    raise ValueError("Output ceiling cannot fit saved-result metadata")
                length //= 2

        return read_inspection_result

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


@contextmanager
def capture_inspections(journal: ReturnedArtifactJournal):
    """Bind a controller-owned journal to the current executor call."""
    token = _active_inspections.set(journal)
    try:
        yield
    finally:
        _active_inspections.reset(token)


def record_delivered_inspection(tool: str, call_id: str, parameters: dict, result: str,
                                sources: tuple[str, ...], journal: ReturnedArtifactJournal | None = None) -> str:
    journal = _active_inspections.get() or journal
    if journal is None or tool not in {"read_artifact", "search_artifact", "get_inventory_item", "read_hypothesis_input"}:
        return ""
    return journal.record_inspection(tool, call_id, parameters, result, sources)


def current_inspection_journal() -> ReturnedArtifactJournal | None:
    """Allow a journal created on the agent thread to retain its scope in tool threads."""
    return _active_inspections.get()


def inspection_has_content(record: dict) -> bool:
    """Determine returned content availability, never its semantic significance."""
    try:
        try:
            payload = json.loads(record["payload"])
        except ValueError:
            payload = ast.literal_eval(record["payload"])
    except (ValueError, SyntaxError):
        return False
    if not isinstance(payload, dict):
        return False
    if record["tool"] == "get_inventory_item":
        return any(entry.get("type") == "source_excerpt" and entry.get("text")
                   for entry in payload.get("context_entries", []))
    return bool(payload.get("content") or payload.get("text") or payload.get("matches"))


def inspection_anchor(record: dict) -> str:
    """Select verbatim content prefixes by tool structure, without interpreting behavior."""
    try:
        try:
            payload = json.loads(record["payload"])
        except ValueError:
            payload = ast.literal_eval(record["payload"])
    except (ValueError, SyntaxError):
        return ""
    if not isinstance(payload, dict):
        return ""
    if record["tool"] == "get_inventory_item":
        texts = [entry["text"] for entry in payload.get("context_entries", [])
                 if entry.get("type") == "source_excerpt" and isinstance(entry.get("text"), str)]
        return "\n".join(texts)[:700]
    content = payload.get("content", payload.get("text"))
    return content[:700] if isinstance(content, str) else ""
