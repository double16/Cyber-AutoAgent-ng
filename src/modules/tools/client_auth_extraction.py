"""Deterministic extraction of secret-free client authentication metadata."""

import json
import re

_HEADER_NAME = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_STORAGE_GET = (
    r"(?:localStorage|sessionStorage)\.getItem\(\s*[\"'](?P<storage_key>[^\"']+)[\"']\s*\)"
)
_OBJECT_BINDING = re.compile(
    rf"(?:[\"'](?P<quoted_header>{_HEADER_NAME})[\"']|(?P<bare_header>[A-Za-z][A-Za-z0-9_-]*))"
    rf"\s*:\s*(?P<bearer>[\"']Bearer\s*[\"']\s*\+\s*)?{_STORAGE_GET}",
    re.IGNORECASE,
)
_HEADER_METHOD_BINDING = re.compile(
    rf"(?:headers|requestHeaders)\.(?:set|append)\(\s*[\"'](?P<header_name>{_HEADER_NAME})[\"']\s*,\s*"
    rf"(?P<bearer>[\"']Bearer\s*[\"']\s*\+\s*)?{_STORAGE_GET}\s*\)",
    re.IGNORECASE,
)
_AXIOS_BINDING = re.compile(
    rf"(?:axios(?:\.[A-Za-z_$][\w$]*)*|[A-Za-z_$][\w$]*\.defaults)\.headers"
    rf"(?:\.[A-Za-z_$][\w$]*)*\.(?P<header_name>{_HEADER_NAME})\s*=\s*"
    rf"(?P<bearer>[\"']Bearer\s*[\"']\s*\+\s*)?{_STORAGE_GET}",
    re.IGNORECASE,
)


def extract_storage_header_bindings(text: str) -> list[dict[str, str]]:
    """Return statically observed storage-to-header templates without executing client code."""

    bindings: set[str] = set()
    for pattern in (_OBJECT_BINDING, _HEADER_METHOD_BINDING, _AXIOS_BINDING):
        for match in pattern.finditer(text):
            groups = match.groupdict()
            header_name = groups.get("quoted_header") or groups.get("bare_header") or groups.get("header_name")
            storage_key = groups.get("storage_key")
            if not header_name or not storage_key:
                continue
            bindings.add(
                json.dumps(
                    {
                        "header_name": header_name,
                        "storage_key": storage_key,
                        "value_template": "Bearer {value}" if groups.get("bearer") else "{value}",
                    },
                    sort_keys=True,
                )
            )
    return [json.loads(binding) for binding in sorted(bindings)]
