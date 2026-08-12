#!/usr/bin/env python3
"""Deterministic conversion of common web recon output into inventory manifests."""

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from strands import tool

SUPPORTED_RECON_FORMATS = (
    "katana",
    "feroxbuster",
    "ffuf",
    "gobuster",
    "dirsearch",
    "httpx",
    "gospider",
    "url_list",
    "specialized_recon",
    "auth_chain",
    "inventory_manifest",
)
RECON_FORMAT_ALIASES = {
    "ferox": "feroxbuster",
    "ffuf_csv": "ffuf",
    "ffuf_json": "ffuf",
    "gospider_text": "gospider",
    "httpx_jsonl": "httpx",
    "katana_jsonl": "katana",
    "specialized_recon_orchestrator": "specialized_recon",
    "auth_chain_analyzer": "auth_chain",
    "inventory": "inventory_manifest",
    "manifest": "inventory_manifest",
}
URL_PATTERN = re.compile(r"https?://[^\s\]\[<>{}\"']+")
STATUS_PATTERN = re.compile(r"(?:status(?:_code)?[=: ]+|\bStatus:\s*)(\d{3})", re.IGNORECASE)


def resolve_inventory_target(target: str, target_id: str = "target-1") -> tuple[str, str]:
    """Resolve the active task's registered target value and logical target ID when available."""

    try:
        from modules.tools.memory import _get_active_plan, resolve_bound_executable_target

        resolved = resolve_bound_executable_target(target)
        plan = _get_active_plan()
        matches = [item for item in plan.targets if item.value.rstrip("/") == resolved.rstrip("/")]
        if len(matches) == 1:
            return resolved, matches[0].target_id
        return resolved, target_id
    except Exception:
        return target, target_id


def _canonical_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip().rstrip(".,;"))
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return ""
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    formatted_host = f"[{host}]" if ":" in host else host
    netloc = formatted_host if port in {None, default_port} else f"{formatted_host}:{port}"
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def _service_boundary(value: str) -> tuple[str, str, int]:
    parsed = urlparse(_canonical_url(value))
    default_port = 80 if parsed.scheme == "http" else 443
    return parsed.scheme, parsed.hostname or "", parsed.port or default_port


def _in_scope(value: str, target: str) -> bool:
    if not target:
        return True
    canonical_target = _canonical_url(target)
    if not canonical_target:
        return True
    return _service_boundary(value) == _service_boundary(canonical_target)


def _stable_id(kind: str, target_id: str, value: str) -> str:
    digest = hashlib.sha256(f"{target_id}\0{kind}\0{value}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}"


def _record(url: str, *, method: str = "GET", status: Any = None, technologies: Any = None) -> Dict[str, Any]:
    return {
        "url": url,
        "method": str(method or "GET").upper(),
        "status": int(status) if str(status or "").isdigit() else None,
        "technologies": technologies if isinstance(technologies, list) else [],
    }


def _json_values(text: str) -> List[Any]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, list) else [payload]
    except json.JSONDecodeError:
        values = []
        for line in text.splitlines():
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return values


def _urls_from_text(text: str) -> List[Dict[str, Any]]:
    records = []
    for line in text.splitlines():
        status_match = STATUS_PATTERN.search(line)
        status = status_match.group(1) if status_match else None
        records.extend(_record(match.group(0), status=status) for match in URL_PATTERN.finditer(line))
    return records


def _parse_katana(text: str) -> List[Dict[str, Any]]:
    records = []
    for value in _json_values(text):
        if not isinstance(value, dict):
            continue
        request = value.get("request") if isinstance(value.get("request"), dict) else {}
        response = value.get("response") if isinstance(value.get("response"), dict) else {}
        url = request.get("endpoint") or request.get("url") or value.get("url")
        if url:
            records.append(_record(url, method=request.get("method", "GET"), status=response.get("status_code")))
    return records or _urls_from_text(text)


def _parse_feroxbuster(text: str) -> List[Dict[str, Any]]:
    records = []
    for value in _json_values(text):
        values = value.get("results", []) if isinstance(value, dict) and isinstance(value.get("results"), list) else [value]
        for item in values:
            if isinstance(item, dict) and (item.get("url") or item.get("target")):
                records.append(
                    _record(
                        item.get("url") or item.get("target"),
                        method=item.get("method", "GET"),
                        status=item.get("status") or item.get("status_code"),
                    )
                )
    return records or _urls_from_text(text)


def _parse_ffuf(text: str) -> List[Dict[str, Any]]:
    values = _json_values(text)
    records = []
    for value in values:
        if not isinstance(value, dict):
            continue
        for item in value.get("results", []) if isinstance(value.get("results"), list) else [value]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url and isinstance(item.get("input"), dict):
                url = item["input"].get("FUZZ")
            if url and str(url).startswith(("http://", "https://")):
                records.append(_record(url, status=item.get("status"), method=item.get("method", "GET")))
    if records:
        return records
    try:
        for row in csv.DictReader(io.StringIO(text)):
            if row.get("url"):
                records.append(_record(row["url"], status=row.get("status"), method=row.get("method", "GET")))
    except csv.Error:
        pass
    return records or _urls_from_text(text)


def _parse_httpx(text: str) -> List[Dict[str, Any]]:
    records = []
    for value in _json_values(text):
        if isinstance(value, dict) and (value.get("url") or value.get("input")):
            url = value.get("url") or value.get("input")
            if not str(url).startswith(("http://", "https://")):
                scheme = value.get("scheme") or "https"
                url = f"{scheme}://{url}"
            records.append(
                _record(
                    url,
                    status=value.get("status_code"),
                    technologies=value.get("tech") or value.get("technologies"),
                )
            )
    return records or _urls_from_text(text)


def _parse_gobuster(text: str) -> List[Dict[str, Any]]:
    records = _urls_from_text(text)
    for line in text.splitlines():
        match = re.match(r"\s*(/[^\s]*)", line)
        if not match:
            continue
        status_match = STATUS_PATTERN.search(line)
        records.append(_record(match.group(1), status=status_match.group(1) if status_match else None))
    return records


def _parse_url_list(text: str) -> List[Dict[str, Any]]:
    """Treat every non-empty line as one auditable URL candidate."""

    return [_record(line.strip()) for line in io.StringIO(text) if line.strip()]


def _parse_specialized_recon(text: str) -> List[Dict[str, Any]]:
    """Normalize specialized_recon_orchestrator's recon_result_v1 output."""

    values = _json_values(text)
    if len(values) != 1 or not isinstance(values[0], dict):
        return []
    payload = values[0]
    metadata = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if metadata.get("format") != "recon_result_v1":
        return []
    technologies = payload.get("technologies") if isinstance(payload.get("technologies"), list) else []
    records = [_record(url, technologies=technologies) for url in payload.get("live_hosts", []) if isinstance(url, str)]
    records.extend(_record(url, technologies=technologies) for url in payload.get("endpoints", []) if isinstance(url, str))
    return records


def _parse_auth_chain(text: str) -> List[Dict[str, Any]]:
    """Normalize auth_chain_analyzer's structured JSON output."""

    values = _json_values(text)
    if len(values) != 1 or not isinstance(values[0], dict):
        return []
    payload = values[0]
    if not isinstance(payload.get("auth_endpoints"), list) or "flow_analysis" not in payload:
        return []
    records = []
    for endpoint in payload["auth_endpoints"]:
        if not isinstance(endpoint, dict):
            continue
        url = endpoint.get("full_url") or endpoint.get("url")
        if url:
            records.append(_record(url, method=endpoint.get("method", "GET"), status=endpoint.get("status")))
    return records


def _is_complete_http_url(value: str) -> bool:
    """Return whether one stripped line contains only a parseable HTTP(S) URL."""

    candidate = str(value or "").strip()
    if not candidate or any(character.isspace() for character in candidate):
        return False
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        parsed.port
    except ValueError:
        return False
    return True


PARSERS = {
    "katana": _parse_katana,
    "feroxbuster": _parse_feroxbuster,
    "ffuf": _parse_ffuf,
    "gobuster": _parse_gobuster,
    "dirsearch": _urls_from_text,
    "httpx": _parse_httpx,
    "gospider": _urls_from_text,
    "url_list": _parse_url_list,
    "specialized_recon": _parse_specialized_recon,
    "auth_chain": _parse_auth_chain,
}


def _infer_format(text: str) -> str:
    values = _json_values(text)
    if (
        len(values) == 1
        and isinstance(values[0], dict)
        and "items" in values[0]
        and "unassessed_gaps" in values[0]
    ):
        return "inventory_manifest"
    if '"format": "recon_result_v1"' in text or '"format":"recon_result_v1"' in text:
        return "specialized_recon"
    if '"auth_endpoints"' in text and '"flow_analysis"' in text:
        return "auth_chain"
    if '"request"' in text and '"endpoint"' in text:
        return "katana"
    if '"results"' in text and ('"FUZZ"' in text or '"ffufhash"' in text):
        return "ffuf"
    if '"status_code"' in text and ('"tech"' in text or '"input"' in text):
        return "httpx"
    if '"type":"response"' in text.replace(" ", "") or '"wildcard"' in text:
        return "feroxbuster"
    url_sample = []
    for line in io.StringIO(text):
        candidate = line.strip()
        if not candidate:
            continue
        url_sample.append(candidate)
        if len(url_sample) == 5:
            break
    if url_sample and all(_is_complete_http_url(candidate) for candidate in url_sample):
        return "url_list"
    raise ValueError(f"Unable to infer recon source format; choose one of: {', '.join(SUPPORTED_RECON_FORMATS)}")


def _structured_inventory_fields(text: str, source_format: str) -> tuple[List[Dict[str, Any]], List[str], List[Any]]:
    """Return workflow, technology, and parameter supplements for native structured outputs."""

    values = _json_values(text)
    payload = values[0] if len(values) == 1 and isinstance(values[0], dict) else {}
    if source_format == "specialized_recon":
        return (
            [],
            list(payload.get("technologies") or []) if isinstance(payload.get("technologies"), list) else [],
            list(payload.get("parameters") or []) if isinstance(payload.get("parameters"), list) else [],
        )
    if source_format != "auth_chain":
        return [], [], []
    workflows = []
    for mechanism in payload.get("auth_mechanisms", []) if isinstance(payload.get("auth_mechanisms"), list) else []:
        if isinstance(mechanism, dict):
            workflows.append({"description": mechanism.get("type") or mechanism.get("mechanism"), "attributes": mechanism})
    flow = payload.get("flow_analysis") if isinstance(payload.get("flow_analysis"), dict) else {}
    for step in flow.get("authentication_steps", []) if isinstance(flow.get("authentication_steps"), list) else []:
        workflows.append({"description": str(step), "attributes": {"source": "authentication_steps"}})
    return workflows, [], []


def records_to_inventory_manifest(
    records: Iterable[Dict[str, Any]],
    *,
    target_id: str,
    target: str = "",
    source_ref: str = "",
    workflows: Optional[Iterable[Dict[str, Any]]] = None,
    technologies: Optional[Iterable[str]] = None,
    parameters: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Build a canonical manifest dictionary from normalized recon records."""

    items: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    seen = set()
    candidate_count = 0
    for record in records:
        candidate_count += 1
        url = _canonical_url(str(record.get("url") or ""))
        if not url:
            gaps.append({"reason": "unparseable_url", "value": str(record.get("url") or "")[:500]})
            continue
        if not _in_scope(url, target):
            gaps.append({"reason": "out_of_scope", "value": url})
            continue
        parsed_url = urlparse(url)
        endpoint_identity = urlunparse(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params, "", "")
        )
        endpoint_key = ("endpoint", endpoint_identity)
        endpoint_id = _stable_id("endpoint", target_id, endpoint_identity)
        service_value = f"{parsed_url.scheme}://{parsed_url.netloc}"
        service_key = ("service", service_value)
        if service_key not in seen:
            items.append(
                {
                    "id": _stable_id("service", target_id, service_value),
                    "target_id": target_id,
                    "kind": "service",
                    "value": service_value,
                    "attributes": {
                        "interaction": {
                            "interface": "http",
                            "operations": [str(record.get("method") or "GET").upper()],
                            "inputs": [],
                            "success_signals": [],
                            "failure_signals": [],
                            "evidence_refs": ([source_ref] if source_ref else []),
                        }
                    },
                }
            )
            seen.add(service_key)
        if endpoint_key not in seen:
            interaction = {
                "interface": "http",
                "operations": [str(record.get("method") or "GET").upper()],
                "inputs": [],
                "success_signals": ([f"HTTP {record['status']}"] if record.get("status") else []),
                "failure_signals": [],
                "evidence_refs": ([source_ref] if source_ref else []),
            }
            items.append(
                {
                    "id": endpoint_id,
                    "target_id": target_id,
                    "kind": "endpoint",
                    "value": url,
                    "attributes": {"interaction": interaction},
                }
            )
            seen.add(endpoint_key)
        for name, _value in parse_qsl(urlparse(url).query, keep_blank_values=True):
            parameter_key = ("parameter", endpoint_id, name)
            if parameter_key in seen:
                continue
            items.append(
                {
                    "id": _stable_id("parameter", target_id, f"{endpoint_id}:{name}"),
                    "target_id": target_id,
                    "kind": "parameter",
                    "value": name,
                    "attributes": {
                        "endpoint_id": endpoint_id,
                        "interaction": {
                            "interface": "http",
                            "operations": [str(record.get("method") or "GET").upper()],
                            "inputs": [{"name": name, "location": "query"}],
                            "success_signals": [],
                            "failure_signals": [],
                            "evidence_refs": ([source_ref] if source_ref else []),
                        },
                    },
                }
            )
            seen.add(parameter_key)
        for technology in record.get("technologies") or []:
            technology = str(technology).strip()
            key = ("technology", technology.lower())
            if technology and key not in seen:
                items.append(
                    {
                        "id": _stable_id("technology", target_id, technology.lower()),
                        "target_id": target_id,
                        "kind": "technology",
                        "value": technology,
                        "attributes": {"evidence_refs": ([source_ref] if source_ref else [])},
                    }
                )
                seen.add(key)
    for parameter in parameters or []:
        name = str(
            parameter.get("name") or parameter.get("parameter") or parameter.get("value") or ""
            if isinstance(parameter, dict)
            else parameter
        ).strip()
        key = ("parameter", "", name)
        if not name or key in seen:
            continue
        items.append(
            {
                "id": _stable_id("parameter", target_id, name),
                "target_id": target_id,
                "kind": "parameter",
                "value": name,
                "attributes": {
                    "interaction": {
                        "interface": "http",
                        "operations": ["GET"],
                        "inputs": [{"name": name, "location": "unknown"}],
                        "success_signals": [],
                        "failure_signals": [],
                        "evidence_refs": ([source_ref] if source_ref else []),
                    }
                },
            }
        )
        seen.add(key)
    for technology in technologies or []:
        value = str(technology).strip()
        key = ("technology", value.lower())
        if value and key not in seen:
            items.append(
                {
                    "id": _stable_id("technology", target_id, value.lower()),
                    "target_id": target_id,
                    "kind": "technology",
                    "value": value,
                    "attributes": {"evidence_refs": ([source_ref] if source_ref else [])},
                }
            )
            seen.add(key)
    for workflow in workflows or []:
        value = str(workflow.get("value") or workflow.get("description") or workflow.get("name") or "").strip()
        if not value:
            continue
        key = ("workflow", value)
        if key in seen:
            continue
        items.append(
            {
                "id": _stable_id("workflow", target_id, value),
                "target_id": target_id,
                "kind": "workflow",
                "value": value,
                "attributes": dict(workflow.get("attributes") or {}),
            }
        )
        seen.add(key)
    return {
        "schema_version": 1,
        "items": items,
        "unassessed_gaps": gaps,
        "extraction": {
            "source_artifact_count": 1 if source_ref else 0,
            "candidate_count": candidate_count,
            "added_count": len(items),
        },
    }


def write_inventory_manifest(path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Atomically write and validate one inventory manifest."""

    absolute_path = _inventory_manifest_output_path(path)
    from modules.tools.memory import _load_inventory_manifest, canonical_artifact_reference

    directory = os.path.dirname(absolute_path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".inventory-manifest-", suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2, sort_keys=True)
            output.write("\n")
        temporary_reference = canonical_artifact_reference(temporary)
        validated, digest = _load_inventory_manifest(temporary_reference)
        os.replace(temporary, absolute_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    reference = canonical_artifact_reference(absolute_path)
    return {
        "path": absolute_path,
        "artifact_ref": reference,
        "item_count": len(validated["items"]),
        "validation_status": "valid",
        "sha256": digest,
    }


def _inventory_manifest_output_path(path: str) -> str:
    """Resolve one manifest output path inside the current operation root."""

    if not path:
        raise ValueError("inventory manifest output path is required")
    from modules.tools.memory import _operation_output_root

    operation_root = os.path.realpath(_operation_output_root())
    candidate_path = path if os.path.isabs(path) else os.path.join(operation_root, path)
    absolute_path = os.path.realpath(candidate_path)
    if os.path.commonpath((absolute_path, operation_root)) != operation_root:
        raise ValueError(f"Inventory manifest output must remain inside the operation output root: {operation_root}")
    return absolute_path


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "source_artifact": {
                    "type": "string",
                    "description": (
                        "Canonical artifact reference, safe absolute path, or relative current-operation path "
                        "containing supported recon output or an inventory manifest. Relative paths resolve from "
                        "artifacts/ first, then from the operation output directory."
                    ),
                },
                "output_file": {
                    "type": "string",
                    "description": "Path for the additional validated version-1 inventory manifest.",
                },
                "source_format": {
                    "type": "string",
                    "enum": ["auto", *SUPPORTED_RECON_FORMATS],
                    "description": "Canonical recon or inventory-manifest format. Defaults to auto detection.",
                },
                "target_id": {
                    "type": "string",
                    "description": "Logical operation target ID. Defaults to target-1.",
                },
                "target": {
                    "type": "string",
                    "description": "Optional logical target ID or registered target value used for scope filtering.",
                },
            },
            "required": ["source_artifact", "output_file"],
        }
    }
)
def recon_output_to_inventory_manifest(
    source_artifact: str,
    output_file: str,
    source_format: str = "auto",
    target_id: str = "target-1",
    target: str = "",
) -> str:
    """Convert supported recon output or validate-copy an inventory manifest into a validated inventory manifest."""

    from modules.tools.artifact import resolve_operation_artifact_path
    from modules.tools.memory import canonical_artifact_reference

    source_path = resolve_operation_artifact_path(source_artifact)
    source_ref = canonical_artifact_reference(source_path)
    with open(source_path, "r", encoding="utf-8", errors="replace") as source:
        text = source.read()
    normalized_format = str(source_format or "auto").strip().lower().replace("-", "_")
    normalized_format = RECON_FORMAT_ALIASES.get(normalized_format, normalized_format)
    if normalized_format == "auto":
        normalized_format = _infer_format(text)
    if normalized_format not in {*PARSERS, "inventory_manifest"}:
        raise ValueError(f"source_format must be auto or one of: {', '.join(SUPPORTED_RECON_FORMATS)}")
    if normalized_format == "inventory_manifest":
        output_path = _inventory_manifest_output_path(output_file)
        if output_path == os.path.realpath(source_path):
            raise ValueError("inventory manifest output_file must differ from source_artifact")
        try:
            manifest = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("inventory_manifest source must be a JSON object") from error
        if not isinstance(manifest, dict):
            raise ValueError("inventory_manifest source must be a JSON object")
        result = write_inventory_manifest(output_file, manifest)
        result.update({"source_artifact": source_ref, "source_format": normalized_format})
        return json.dumps(result, sort_keys=True)
    bound_target, resolved_target_id = resolve_inventory_target(target or target_id, target_id)
    records = PARSERS[normalized_format](text)
    if not records:
        raise ValueError(f"No inventory candidates were parsed from {normalized_format} output")
    if _canonical_url(bound_target):
        for record in records:
            if str(record.get("url") or "").startswith("/"):
                record["url"] = urljoin(bound_target.rstrip("/") + "/", str(record["url"]).lstrip("/"))
    workflows, technologies, parameters = _structured_inventory_fields(text, normalized_format)
    manifest = records_to_inventory_manifest(
        records,
        target_id=resolved_target_id,
        target=bound_target,
        source_ref=source_ref,
        workflows=workflows,
        technologies=technologies,
        parameters=parameters,
    )
    if not manifest["items"]:
        raise ValueError("Recon output produced no in-scope inventory items")
    result = write_inventory_manifest(output_file, manifest)
    result.update({"source_artifact": source_ref, "source_format": normalized_format})
    return json.dumps(result, sort_keys=True)
