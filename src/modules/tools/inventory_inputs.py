"""Project explicit input candidates without inferring security significance or server behavior."""

import ast
import hashlib
import json
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit


def _route(value: str) -> tuple:
    parsed = urlsplit(value)
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80), parsed.path or "/")


def input_candidates(manifest: dict, subject: dict, entries: list[dict]) -> list[dict]:
    """Deduplicate names and locations while retaining all explicit, subject-local origins."""
    attributes = subject.get("attributes", {})
    endpoint = subject if subject["kind"] == "endpoint" else next((
        item for item in manifest["items"] if item["id"] == attributes.get("endpoint_id")
        and item.get("target_id") == subject.get("target_id") and item["kind"] == "endpoint"
    ), None)
    candidates: dict[tuple, dict] = {}

    def add(name: Any, location: Any, origin: dict) -> None:
        if not isinstance(name, str) or not name or not isinstance(location, str) or not location:
            return
        key = (name, location)
        if key not in candidates:
            identity = json.dumps([subject["id"], name, location], ensure_ascii=False)
            candidates[key] = {"type": "input_candidate", "subject_id": subject["id"],
                               "candidate_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                               "name": name, "location": location, "origins": []}
        if origin not in candidates[key]["origins"]:
            candidates[key]["origins"].append(origin)

    records = [subject]
    if endpoint is not None:
        if subject is endpoint:
            records += [item for item in manifest["items"] if item["kind"] == "parameter"
                        and item.get("attributes", {}).get("endpoint_id") == endpoint["id"]
                        and item.get("target_id") == endpoint.get("target_id")]
        else:
            records.append(endpoint)
    for record in records:
        attrs = record.get("attributes", {})
        origin = {"kind": "recorded_input", "source_record_id": record["id"]}
        for value in attrs.get("interaction", {}).get("inputs", []):
            add(value.get("name"), value.get("location"), origin)
        if record["kind"] == "parameter":
            add(attrs.get("name", record["value"]), attrs.get("location"),
                {"kind": "linked_parameter", "source_record_id": record["id"]})

    if endpoint is None or not endpoint["value"].startswith(("http://", "https://")):
        return sorted(candidates.values(), key=lambda row: row["candidate_id"])
    try:
        route = _route(endpoint["value"])
    except ValueError:
        return sorted(candidates.values(), key=lambda row: row["candidate_id"])
    for name, _ in parse_qsl(urlsplit(endpoint["value"]).query, keep_blank_values=True):
        add(name, "query", {"kind": "inventory_url", "source_record_id": endpoint["id"]})
    for entry in entries:
        if entry["type"] not in {"client_operation", "client_link"}:
            continue
        declaration = entry.get("declaration", entry)
        try:
            url = urljoin(endpoint["value"], declaration["url"])
            if _route(url) != route:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        origin = {"kind": entry["type"],
                  "reference": declaration.get("source_artifact", entry.get("reference")),
                  "source_location": declaration.get("source_location", {})}
        for name, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            add(name, "query", origin)
        for value in declaration.get("inputs", []):
            add(value.get("name"), value.get("location"), origin)
    return sorted(candidates.values(), key=lambda row: row["candidate_id"])


def inspection_covers_input(candidate: dict, record: dict) -> bool:
    """Correlate source locations with returned content; never treat a listed path as inspection."""
    if any(origin["kind"] in {"recorded_input", "linked_parameter", "inventory_url"}
           for origin in candidate["origins"]):
        return True
    from modules.tools.inventory_investigation import returned_source_ranges

    try:
        try:
            payload = json.loads(record["payload"])
        except ValueError:
            payload = ast.literal_eval(record["payload"])
    except (ValueError, SyntaxError):
        return False
    if not isinstance(payload, dict):
        return False
    ranges = list(returned_source_ranges(payload))
    if record["tool"] in {"read_artifact", "read_hypothesis_input"} and payload.get("content"):
        ranges.append({"reference": payload.get("artifact_ref"), "line": payload.get("start_line", 1),
                       "end_line": payload.get("end_line", 0), "column": payload.get("start_column", 1)})
    return any(
        span["reference"] == origin.get("reference")
        and span.get("column", 1) == 1
        and span["line"] <= origin.get("source_location", {}).get("start_line", 0)
        and span.get("end_line", span["line"]) >= origin.get("source_location", {}).get("end_line", 0)
        for origin in candidate["origins"] for span in ranges
    )
