"""Read individual records from a validated current-operation inventory manifest."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from strands import tool

from modules.tools.artifact import resolve_operation_artifact_path
from modules.tools.inventory_investigation import investigation_entries, investigation_page
from modules.tools.memory import _load_inventory_manifest, canonical_artifact_reference

_INVENTORY_ITEM_KINDS = ("endpoint", "parameter", "workflow", "service", "technology")
_MATCH_LIST_LIMIT = 20
_INPUT_SCHEMA = {
    "json": {
        "type": "object",
        "properties": {
            "manifest_ref": {
                "type": "string",
                "description": "Current-operation inventory manifest artifact reference",
            },
            "item_id": {"type": "string", "description": "Exact inventory item ID"},
            "kind": {
                "type": "string",
                "enum": list(_INVENTORY_ITEM_KINDS),
                "description": "Inventory category; use alone to list matches or combine to narrow results",
            },
            "endpoint_id": {
                "type": "string",
                "description": "Endpoint ID; also matches parameters linked to that endpoint",
            },
            "target_id": {"type": "string", "description": "Exact target ID"},
            "value": {"type": "string", "description": "Exact item value"},
            "view": {"type": "string", "enum": ["record", "investigation"], "default": "record",
                     "description": "Investigation returns linked records, diagnostics and bounded source excerpts"},
            "context_offset": {"type": "integer", "minimum": 0, "default": 0},
            "context_limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
        "required": ["manifest_ref"],
    }
}


def create_inventory_item_reader(
    *,
    allowed_manifest_refs: Iterable[str] | None = None,
    expected_manifest_digests: Mapping[str, str] | None = None,
    allowed_source_refs: Iterable[str] | None = None,
    max_output_chars: int = 30_000,
) -> Any:
    """Create an inventory lookup tool optionally restricted to frozen manifest refs."""

    if max_output_chars < 256:
        raise ValueError("max_output_chars must be at least 256")
    allowed_refs = (
        {canonical_artifact_reference(reference) for reference in allowed_manifest_refs}
        if allowed_manifest_refs is not None
        else None
    )
    expected_digests = {
        canonical_artifact_reference(reference): str(digest)
        for reference, digest in (expected_manifest_digests or {}).items()
    }
    source_refs = ({canonical_artifact_reference(reference) for reference in allowed_source_refs}
                   if allowed_source_refs is not None else None)
    input_schema = deepcopy(_INPUT_SCHEMA)
    if allowed_refs is not None and len(allowed_refs) == 1:
        input_schema["json"]["properties"]["manifest_ref"]["enum"] = sorted(allowed_refs)

    @tool(name="get_inventory_item", inputSchema=input_schema)
    def get_inventory_item(
        manifest_ref: str,
        item_id: str = "",
        kind: str = "",
        endpoint_id: str = "",
        target_id: str = "",
        value: str = "",
        view: str = "record",
        context_offset: int = 0,
        context_limit: int = 20,
    ) -> str:
        """Look up records in the supplied current-operation inventory manifest.

        Select by exact item_id, kind (endpoint, parameter, workflow, service, technology),
        endpoint_id, target_id, or exact value. Combine selectors to narrow results. A single
        match returns the full record; multiple matches return compact candidate records with
        IDs that can be used for an exact follow-up lookup.

        This tool looks up matching records rather than returning the complete manifest. Use
        read_artifact to inspect the full manifest and search_artifact to search its text.
        Prefer view=investigation for one subject's linked inputs, declarations, mismatches,
        and exact source excerpts. Follow next_context_offset to inspect remaining context.
        """

        view = {"raw": "record", "context": "investigation"}.get(view, view)
        if view not in {"record", "investigation"}:
            raise ValueError("view must be record or investigation")
        if type(context_offset) is not int or context_offset < 0:
            raise ValueError("context_offset must be a non-negative integer")
        if type(context_limit) is not int or not 1 <= context_limit <= 50:
            raise ValueError("context_limit must be from 1 to 50")
        if allowed_refs is not None and manifest_ref.strip() not in allowed_refs:
            raise RuntimeError(f"Inventory manifest is not available to this worker; use one of: {sorted(allowed_refs)}")
        reference = canonical_artifact_reference(manifest_ref)
        selectors = {
            "item_id": item_id.strip(),
            "kind": kind.strip(),
            "endpoint_id": endpoint_id.strip(),
            "target_id": target_id.strip(),
            "value": value.strip(),
        }
        if not any(selectors.values()):
            raise ValueError("Provide item_id or at least one of kind, endpoint_id, target_id, or value")
        if selectors["kind"] and selectors["kind"] not in _INVENTORY_ITEM_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(_INVENTORY_ITEM_KINDS)}")

        manifest, digest = _load_inventory_manifest(reference, persist_normalization=False)
        expected_digest = expected_digests.get(reference)
        if expected_digest and digest != expected_digest:
            return json.dumps({"status": "snapshot_changed", "manifest_ref": reference}, sort_keys=True)

        matches = []
        for item in manifest["items"]:
            attributes = item.get("attributes", {})
            matched_endpoint = item.get("id") == selectors["endpoint_id"] or (
                isinstance(attributes, dict) and attributes.get("endpoint_id") == selectors["endpoint_id"]
            )
            if selectors["item_id"] and item.get("id") != selectors["item_id"]:
                continue
            if selectors["kind"] and item.get("kind") != selectors["kind"]:
                continue
            if selectors["endpoint_id"] and not matched_endpoint:
                continue
            if selectors["target_id"] and item.get("target_id") != selectors["target_id"]:
                continue
            if selectors["value"] and item.get("value") != selectors["value"]:
                continue
            matches.append(item)

        if len(matches) == 1:
            if view == "investigation":
                cache: dict[str, bytes] = {}

                def read_source(source: str) -> bytes:
                    canonical = canonical_artifact_reference(source)
                    if source_refs is not None and canonical not in source_refs:
                        raise ValueError("Source is not available to this worker")
                    if canonical not in cache:
                        with open(resolve_operation_artifact_path(canonical), "rb") as stream:
                            content = stream.read(16_000_001)
                        if len(content) > 16_000_000:
                            raise ValueError("Source exceeds the 16000000-byte inspection limit")
                        cache[canonical] = content
                    return cache[canonical]

                entries = investigation_entries(manifest, matches[0], read_source=read_source)

                def retain_entry(entry: dict[str, Any]) -> str:
                    from modules.tools.memory import _operation_output_root

                    text = json.dumps(entry, ensure_ascii=False, sort_keys=True, indent=2)
                    key = hashlib.sha256(text.encode()).hexdigest()
                    root = Path(_operation_output_root())
                    path = root / "artifacts" / "inventory_context" / f"{key}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                    return "artifact:" + path.relative_to(root).as_posix()

                return investigation_page(
                    entries, offset=context_offset, limit=context_limit, max_chars=max_output_chars,
                    retain_entry=retain_entry,
                    header={"status": "found", "view": "investigation", "manifest_ref": reference,
                            "manifest_sha256": digest, "item_id": matches[0]["id"]},
                )
            response = {
                "status": "found",
                "manifest_ref": reference,
                "manifest_sha256": digest,
                "item": matches[0],
            }
            encoded = json.dumps(response, ensure_ascii=False, sort_keys=True)
            if len(encoded) > max_output_chars:
                return json.dumps(
                    {
                        "status": "item_too_large",
                        "manifest_ref": reference,
                        "item_id": matches[0]["id"],
                        "guidance": "Use read_artifact with this manifest reference to inspect the item fields.",
                    },
                    sort_keys=True,
                )
            return encoded
        if not matches:
            return json.dumps({"status": "not_found", "manifest_ref": reference}, sort_keys=True)

        listed_matches = [
            {
                "id": item["id"],
                "target_id": item["target_id"],
                "kind": item["kind"],
                "value": str(item["value"])[:120],
            }
            for item in matches[:_MATCH_LIST_LIMIT]
        ]
        response = {
            "status": "ambiguous",
            "manifest_ref": reference,
            "match_count": len(matches),
            "matches": listed_matches,
            "truncated": len(matches) > len(listed_matches),
        }
        encoded = json.dumps(response, ensure_ascii=False, sort_keys=True)
        while len(encoded) > max_output_chars and listed_matches:
            listed_matches.pop()
            response["truncated"] = True
            encoded = json.dumps(response, ensure_ascii=False, sort_keys=True)
        return encoded

    return get_inventory_item
