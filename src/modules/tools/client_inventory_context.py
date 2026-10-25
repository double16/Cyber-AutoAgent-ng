"""Typed HTTP client-declaration adapter for inventory investigation context."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from modules.tools.client_route_extraction import saved_route_index


def _route(value: str) -> tuple[str, str, int | None, str]:
    parsed = urlsplit(value)
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80), parsed.path or "/")


def client_inventory_context(
    manifest: dict[str, Any], record: dict[str, Any], *, read_source: Callable[[str], bytes] | None,
) -> list[dict[str, Any]]:
    """Expose exact declarations and locations; do not infer server behavior or route aliases."""

    attributes = record.get("attributes", {})
    interaction = attributes.get("interaction", {})
    operations = interaction.get("extensions", {}).get("client_source_operations", [])
    parent = next((item for item in manifest["items"] if item["id"] == attributes.get("endpoint_id")
                   and item.get("target_id") == record.get("target_id")), None)
    endpoint = record if record["kind"] == "endpoint" else parent
    if endpoint is None or not str(endpoint["value"]).startswith(("http://", "https://")):
        return []
    entries = []
    payloads = {}
    refs = [*attributes.get("source_artifact_refs", []), *interaction.get("evidence_refs", [])]
    if read_source:
        for reference in dict.fromkeys(refs):
            try:
                content = read_source(reference)
                try:
                    payload = json.loads(content)
                except (ValueError, UnicodeError):
                    continue  # Source references may legitimately point to implementation text.
                if isinstance(payload, dict) and payload.get("schema_version") == "client_bundle_inventory_v2":
                    if payload.get("target_id") != record.get("target_id"):
                        entries.append({"type": "diagnostic", "code": "cross_target_extraction",
                                        "reference": reference, "item_id": record["id"]})
                        continue
                    payloads[reference] = payload
            except (OSError, ValueError, TypeError) as error:
                entries.append({"type": "diagnostic", "code": "source_unavailable", "reference": reference,
                                "item_id": record["id"], "detail": str(error)})

    declared = list(operations)
    for reference, payload in payloads.items():
        for operation in payload.get("operations", []):
            try:
                matches = _route(urljoin(str(payload["target"]), operation["url"])) == _route(endpoint["value"])
            except (KeyError, TypeError, ValueError):
                matches = False
            if matches:
                candidate = {**operation, "source_artifact": payload.get("analysis_artifact")}
                if candidate not in declared:
                    declared.append(candidate)
        if payload.get("request_analysis_status", "complete") != "complete":
            entries.append({"type": "extraction_gap", "reference": reference,
                            "status": payload["request_analysis_status"]})
        if payload.get("unresolved_requests"):
            entries.append({"type": "extraction_gap", "reference": reference,
                            "unresolved_request_count": len(payload["unresolved_requests"]),
                            "relationship": "bundle_wide_not_subject_specific"})
        # Unresolved requests are only subject-local when the extractor retained an explicit candidate URL.
        for gap in payload.get("unresolved_requests", []):
            candidate = gap.get("candidate", {})
            if candidate.get("url") and _route(urljoin(payload["target"], candidate["url"])) == _route(endpoint["value"]):
                entries.append({"type": "extraction_gap", "reference": reference, "gap": gap})

    # Routes describe client behavior, not an HTTP operation implemented by the SPA URL.
    if read_source:
        from modules.tools.memory import _operation_output_root

        sources = {*attributes.get("implementation_refs", []),
                   *(payload["analysis_artifact"] for payload in payloads.values() if payload.get("analysis_artifact"))}
        seen_digests = set()
        for reference in sorted(sources):
            try:
                content = read_source(reference)
                expected = {payload["analysis_sha256"] for payload in payloads.values()
                            if payload.get("analysis_artifact") == reference and payload.get("analysis_sha256")}
                if expected and expected != {hashlib.sha256(content).hexdigest()}:
                    entries.append({"type": "diagnostic", "code": "source_changed", "reference": reference})
                    continue
                digest = hashlib.sha256(content).hexdigest()
                if digest in seen_digests:
                    continue
                seen_digests.add(digest)
                index = saved_route_index(content, Path(_operation_output_root()) / "artifacts" / "client_route_indexes")
                if index["route_analysis_status"] != "complete":
                    entries.append({"type": "diagnostic", "code": "route_analysis_incomplete", "reference": reference,
                                    "status": index["route_analysis_status"]})
                for link in index.get("literal_links", []):
                    if _route(urljoin(endpoint["value"], link["url"])) == _route(endpoint["value"]):
                        entries.append({"type": "client_link", "item_id": record["id"],
                                        "reference": reference, **link, "server_behavior": "unestablished"})
                        declared.append({"source_artifact": reference, "source_location": link["source_location"],
                                         "_route_context": True})
                for link in index["route_links"]:
                    if _route(urljoin(endpoint["value"], link["path"])) != _route(endpoint["value"]):
                        continue
                    entries.append({"type": "client_route", "reference": reference,
                                    **{key: value for key, value in link.items() if key != "definitions"}})
                    declared.extend({"source_artifact": reference, "source_location": location, "_route_context": True}
                                    for location in [link["source_location"], link["element_source_location"]])
                    for definition in link["definitions"]:
                        declared.append({"source_artifact": reference, "source_location": definition["source_location"],
                                         "_route_context": True, "_route_definition": definition})
            except (OSError, ValueError, TypeError) as error:
                entries.append({"type": "diagnostic", "code": "source_unavailable", "reference": reference,
                                "detail": str(error)})

    for operation in declared:
        reference = operation.get("source_artifact")
        if operation.get("_route_definition"):
            entries.append({"type": "client_route_definition", "reference": reference,
                            "relationship": "client_route_calls", **operation["_route_definition"]})
        if not operation.get("_route_context"):
            entries.append({"type": "client_operation", "item_id": record["id"], "declaration": operation,
                            "evidence_kind": operation.get("evidence_kind", "client_source"),
                            "server_behavior": "unestablished"})
            try:
                if _route(urljoin(endpoint["value"], operation["url"])) != _route(endpoint["value"]):
                    entries.append({"type": "diagnostic", "code": "operation_endpoint_mismatch",
                                    "item_id": record["id"], "endpoint": endpoint["value"],
                                    "declared_url": operation["url"], "reference": reference})
            except (KeyError, TypeError, ValueError):
                entries.append({"type": "diagnostic", "code": "invalid_operation_url", "item_id": record["id"]})
        location = operation.get("source_location", {})
        if not reference or not location or read_source is None:
            continue
        try:
            content = read_source(reference)
            digest = hashlib.sha256(content).hexdigest()
            expected = {payload["analysis_sha256"] for payload in payloads.values()
                        if payload.get("analysis_artifact") == reference and payload.get("analysis_sha256")}
            if expected and expected != {digest}:
                entries.append({"type": "diagnostic", "code": "source_changed", "reference": reference})
                continue
            start, end = location.get("start_line"), location.get("end_line")
            lines = content.decode("utf-8", errors="replace").splitlines()
            if (type(start) is not int or type(end) is not int or start < 1 or end < start or end > len(lines)):
                raise ValueError("Invalid recorded source location")
            # Small independent excerpts remain pageable under the normal tool output ceiling.
            remaining = 8000
            next_line, next_column = start, 1
            for line_number in range(start, min(end, start + 199) + 1):
                if remaining == 0:
                    break
                line = lines[line_number - 1]
                for column in range(0, max(1, len(line)), 1000):
                    text = line[column:column + min(1000, remaining)]
                    entries.append({"type": "source_excerpt", "reference": reference, "line": line_number,
                                    "column": column + 1, "text": text, "sha256": digest,
                                    "digest_verification": "verified" if expected else "unrecorded"})
                    remaining -= len(text)
                    next_line, next_column = ((line_number + 1, 1) if column + len(text) == len(line)
                                              else (line_number, column + len(text) + 1))
                    if remaining == 0:
                        break
            if next_line <= end:
                entries.append({"type": "diagnostic", "code": "excerpt_limit", "reference": reference,
                                "next_line": next_line, "next_column": next_column, "end_line": end})
        except (OSError, ValueError, TypeError) as error:
            entries.append({"type": "diagnostic", "code": "source_unavailable", "reference": reference,
                            "detail": str(error)})
    return entries
