"""Resolve explicit inventory relationships without interpreting their security significance."""

import json
from collections.abc import Callable
from typing import Any

from modules.tools.client_inventory_context import client_inventory_context
from modules.tools.inventory_provenance import operation_provenance

SourceReader = Callable[[str], bytes]


def investigation_entries(
    manifest: dict[str, Any], subject: dict[str, Any], *, read_source: SourceReader | None = None,
) -> list[dict[str, Any]]:
    """Return stable, subject-local context; listed references do not establish inspection."""

    entries: list[dict[str, Any]] = []
    items = manifest["items"]
    records = [subject]
    parent_id = subject.get("attributes", {}).get("endpoint_id")
    if parent_id:
        parents = [item for item in items if item["id"] == parent_id]
        if not parents:
            entries.append({"type": "diagnostic", "code": "missing_parent", "item_id": subject["id"],
                            "parent_id": parent_id})
        elif parents[0].get("target_id") != subject.get("target_id"):
            entries.append({"type": "diagnostic", "code": "cross_target_parent", "item_id": subject["id"],
                            "parent_id": parent_id})
        else:
            records.extend(parents[:1])
    if subject["kind"] == "endpoint":
        records.extend(item for item in items if item["kind"] == "parameter"
                       and item.get("attributes", {}).get("endpoint_id") == subject["id"]
                       and item.get("target_id") == subject.get("target_id"))

    for record in records:
        attributes = record.get("attributes", {})
        interaction = attributes.get("interaction", {})
        entries.append({"type": "record", "id": record["id"], "kind": record["kind"],
                        "value": record["value"], "target_id": record.get("target_id"),
                        "relationship": "assigned" if record is subject else "explicitly_linked",
                        "parent_id": attributes.get("endpoint_id"),
                        "inputs": interaction.get("inputs", []),
                        "operations": operation_provenance(interaction)})
        if not interaction.get("inputs"):
            entries.append({"type": "diagnostic", "code": "inputs_unrecorded", "item_id": record["id"]})
        for operation, provenance in operation_provenance(interaction).items():
            if provenance != "observed":
                entries.append({"type": "diagnostic", "code": "operation_not_observed", "item_id": record["id"],
                                "operation": operation, "provenance": provenance})
        for field in ("source_artifact_refs", "implementation_refs"):
            for reference in attributes.get(field, []):
                entries.append({"type": "source_reference", "item_id": record["id"], "reference": reference,
                                "relationship": "broad_implementation" if field == "implementation_refs" else field})
        entries.extend(client_inventory_context(manifest, record, read_source=read_source))
    for gap in manifest.get("unassessed_gaps", []):
        if isinstance(gap, dict) and (gap.get("item_id") in {item["id"] for item in records}
                                     or gap.get("endpoint_id") in {item["id"] for item in records}):
            entries.append({"type": "extraction_gap", "gap": gap})
    # Complementary source fragments frequently repeat identical declarations.
    return prioritize_entries(list({json.dumps(entry, sort_keys=True): entry for entry in entries}.values()))



def prioritize_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep subject-local declarations and bounded verbatim source blocks ahead of metadata."""
    records, groups, trailing = [], [], []
    for entry in entries:
        kind = entry["type"]
        if kind == "record":
            records.append(entry)
        elif kind in {"client_route", "client_route_definition", "client_operation"}:
            groups.append([entry])
        elif kind == "source_excerpt":
            if not groups:
                groups.append([])
            previous = groups[-1][-1] if groups[-1] else {}
            if (previous.get("type") == "source_excerpt"
                    and previous["reference"] == entry["reference"]
                    and previous.get("end_line", previous["line"]) + 1 == entry["line"]
                    and entry["column"] == 1
                    and len(previous["text"]) + len(entry["text"]) < 2000):
                previous["text"] += "\n" + entry["text"]
                previous["end_line"] = entry["line"]
            else:
                groups[-1].append(dict(entry))
        else:
            trailing.append(entry)
    # First show one excerpt from every declaration before long implementations consume the page.
    heads, tails = [], []
    for group in groups:
        count = 2 if group[0]["type"] != "source_excerpt" else 1
        heads.extend(group[:count])
        tails.extend(group[count:])
    return records[:1] + heads + tails + records[1:] + trailing


def investigation_page(
    entries: list[dict[str, Any]], *, offset: int, limit: int, header: dict[str, Any], max_chars: int,
) -> str:
    """Page complete entries without silently truncating an operation or excerpt."""

    page = entries[offset:offset + limit]
    while True:
        next_offset = offset + len(page)
        navigation = {}
        for index in range(next_offset, len(entries)):
            navigation.setdefault(entries[index]["type"], index)
        payload = {**header, "remaining_context": navigation, "context_entries": page, "context_count": len(entries),
                   "next_context_offset": next_offset if next_offset < len(entries) else None}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) <= max_chars:
            return encoded
        if not page:
            raise ValueError("Investigation output ceiling is too small for response metadata")
        page.pop()
        if not page and offset < len(entries):
            raise ValueError("Investigation entry exceeds output ceiling; use read_artifact or increase the ceiling")


def returned_source_ranges(output: Any) -> tuple[dict[str, Any], ...]:
    """Capture exact returned excerpts before tool-output summaries are shortened."""

    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return ()
    if not isinstance(output, dict):
        return ()
    if "content" in output:
        blocks = output["content"] if isinstance(output["content"], list) else []
        return tuple(entry for block in blocks if isinstance(block, dict)
                     for entry in returned_source_ranges(block.get("text")))
    if output.get("view") != "investigation":
        return ()
    entries = output.get("context_entries", [])
    if not isinstance(entries, list):
        return ()
    return tuple({key: entry[key] for key in ("reference", "line", "column", "sha256")}
                 | {"characters": len(entry["text"])}
                 | ({"end_line": entry["end_line"]} if "end_line" in entry else {})
                 for entry in entries if isinstance(entry, dict) and entry.get("type") == "source_excerpt"
                 and all(key in entry for key in ("reference", "line", "column", "sha256", "text"))
                 and isinstance(entry["text"], str))
