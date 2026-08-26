"""Deterministically extract a target-scoped inventory from one client JavaScript bundle."""

import hashlib
import json
import os
import re
import tempfile
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from strands import tool

from modules.operation_plugins.web.tools.recon_inventory_manifest import (
    _canonical_url,
    records_to_inventory_manifest,
    resolve_inventory_target,
    write_inventory_manifest,
)
from modules.tools.artifact import resolve_operation_artifact_path
from modules.tools.memory import canonical_artifact_reference

_QUOTED_PATH_PATTERN = re.compile(r"[\"'`](/[^\"'`\\\s]{0,300})[\"'`]")
_URL_PATTERN = re.compile(r"https?://[^\s\"'`<>]+", re.IGNORECASE)
_STORAGE_KEY_PATTERN = re.compile(
    r"(?:localStorage|sessionStorage)\.(?:getItem|setItem|removeItem)\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_SOURCE_MAP_PATTERN = re.compile(
    r"(?:sourceMappingURL|sourceURL)\s*=\s*([^\s*]+)", re.IGNORECASE
)
_STATIC_ASSET_SUFFIXES = frozenset(
    {
        ".avif",
        ".css",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".map",
        ".png",
        ".svg",
        ".webp",
    }
)


def _operation_output_path(path: str) -> str:
    """Resolve one output path beneath the active operation root."""

    if not path:
        raise ValueError("output_file is required")
    from modules.tools.memory import _operation_output_root

    root = os.path.realpath(_operation_output_root())
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    resolved = os.path.realpath(candidate)
    if os.path.commonpath((root, resolved)) != root:
        raise ValueError("output_file must remain inside the current operation output")
    return resolved


def _write_json_artifact(path: str, payload: dict[str, Any]) -> str:
    """Atomically persist one extraction artifact and return its canonical reference."""

    resolved = _operation_output_path(path)
    directory = os.path.dirname(resolved)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".client-bundle-", suffix=".json", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as artifact:
            json.dump(payload, artifact, indent=2, sort_keys=True)
            artifact.write("\n")
            artifact.flush()
            os.fsync(artifact.fileno())
        os.replace(temporary, resolved)
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return canonical_artifact_reference(resolved)


def _is_candidate_route(path: str) -> bool:
    """Reject static assets and non-route literals from a quoted bundle value."""

    parsed = urlparse(path)
    if (
        parsed.scheme
        or parsed.netloc
        or not path.startswith("/")
        or path.startswith("//")
    ):
        return False
    normalized_path = parsed.path or "/"
    return not any(
        normalized_path.lower().endswith(suffix) for suffix in _STATIC_ASSET_SUFFIXES
    )


def _origin(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "", ""))


def _bundle_inventory(text: str, target: str) -> dict[str, Any]:
    """Extract stable route and client-auth metadata without vulnerability inference."""

    api_paths = set()
    spa_routes = set()
    for match in _QUOTED_PATH_PATTERN.finditer(text):
        path = match.group(1).split("#", 1)[0]
        if not _is_candidate_route(path):
            continue
        if path.startswith("/api/"):
            api_paths.add(path)
        else:
            spa_routes.add(path)

    target_origin = _origin(target)
    external_origins = {
        _origin(match.group(0).rstrip(".,;"))
        for match in _URL_PATTERN.finditer(text)
        if _canonical_url(match.group(0).rstrip(".,;"))
        and _origin(match.group(0).rstrip(".,;")) != target_origin
    }
    storage_keys = sorted(
        {match.group(1) for match in _STORAGE_KEY_PATTERN.finditer(text)}
    )
    source_maps = sorted(
        {match.group(1).rstrip("*/") for match in _SOURCE_MAP_PATTERN.finditer(text)}
    )
    auth_indicators = sorted(
        indicator
        for indicator, present in {
            "authorization_header": bool(
                re.search(r"authorization", text, re.IGNORECASE)
            ),
            "credentials_option": bool(re.search(r"credentials", text, re.IGNORECASE)),
            "local_storage": "localStorage" in text,
            "session_storage": "sessionStorage" in text,
        }.items()
        if present
    )
    return {
        "api_paths": sorted(api_paths),
        "spa_routes": sorted(spa_routes),
        "auth_storage_keys": storage_keys,
        "auth_indicators": auth_indicators,
        "source_map_references": source_maps,
        "external_origins": sorted(external_origins),
    }


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "source_artifact": {
                    "type": "string",
                    "description": "Current-operation JavaScript bundle artifact.",
                },
                "output_file": {
                    "type": "string",
                    "description": "Output path for the extraction JSON artifact.",
                },
                "inventory_manifest": {
                    "type": "string",
                    "description": "Output path for the validated inventory manifest.",
                },
                "target": {
                    "type": "string",
                    "description": "Registered target value or logical target ID.",
                },
                "target_id": {
                    "type": "string",
                    "description": "Logical target ID; defaults to target-1.",
                },
            },
            "required": ["source_artifact", "output_file", "inventory_manifest"],
        }
    }
)
def client_bundle_inventory(
    source_artifact: str,
    output_file: str,
    inventory_manifest: str,
    target: str = "",
    target_id: str = "target-1",
) -> str:
    """Extract client-bundle routes and metadata into durable evidence and a validated inventory manifest."""

    source_path = resolve_operation_artifact_path(source_artifact)
    source_ref = canonical_artifact_reference(source_path)
    bound_target, resolved_target_id = resolve_inventory_target(
        target or target_id, target_id
    )
    canonical_target = _canonical_url(bound_target)
    if not canonical_target:
        raise ValueError("target must resolve to a registered HTTP(S) target")
    with open(source_path, "rb") as source:
        source_bytes = source.read()
    extracted = _bundle_inventory(
        source_bytes.decode("utf-8", errors="replace"), canonical_target
    )

    extraction_payload = {
        "schema_version": "client_bundle_inventory_v1",
        "source_artifact": source_ref,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "target": canonical_target,
        "target_id": resolved_target_id,
        **extracted,
    }
    extraction_ref = _write_json_artifact(output_file, extraction_payload)
    endpoint_paths = [*extracted["api_paths"], *extracted["spa_routes"]]
    records = [{"url": canonical_target}]
    records.extend(
        {"url": urljoin(canonical_target.rstrip("/") + "/", path.lstrip("/"))}
        for path in endpoint_paths
    )
    manifest = records_to_inventory_manifest(
        records,
        target_id=resolved_target_id,
        target=canonical_target,
        source_ref=extraction_ref,
    )
    manifest_result = write_inventory_manifest(inventory_manifest, manifest)
    return json.dumps(
        {
            "source_artifact": source_ref,
            "extraction_artifact": extraction_ref,
            "inventory_manifest": manifest_result,
            "api_path_count": len(extracted["api_paths"]),
            "spa_route_count": len(extracted["spa_routes"]),
            "external_origin_count": len(extracted["external_origins"]),
        },
        sort_keys=True,
    )
