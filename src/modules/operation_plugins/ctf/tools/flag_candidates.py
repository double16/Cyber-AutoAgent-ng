"""Deterministic, artifact-backed flag candidate discovery for CTF operations."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from strands import tool

from modules.tools import memory as memory_tools

_BRACED_FLAG = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]{0,31}\{[^{}\r\n]{1,512}\}")
_HEX_FLAG = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{128}|[0-9A-Fa-f]{64})(?![0-9A-Fa-f])")
_MAX_ARTIFACT_BYTES = 1_000_000
_MAX_CANDIDATES = 20


def _artifact_matches(artifact_ref: str) -> List[Dict[str, Any]]:
    """Return bounded, deduplicated flag-shaped matches without exposing them to the tool response."""

    artifact_path = Path(memory_tools._artifact_path_from_ref(artifact_ref))
    with artifact_path.open(encoding="utf-8", errors="replace") as artifact_file:
        text = artifact_file.read(_MAX_ARTIFACT_BYTES)
    matches: List[Dict[str, Any]] = []
    seen = set()
    for match_type, pattern in (("braced", _BRACED_FLAG), ("hex", _HEX_FLAG)):
        for match in pattern.finditer(text):
            value = match.group(0)
            if value in seen:
                continue
            seen.add(value)
            matches.append(
                {
                    "value": value,
                    "match_type": match_type,
                    "line": text.count("\n", 0, match.start()) + 1,
                }
            )
    return matches


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "evidence_artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Operation artifact references to scan for flag-shaped values.",
                },
                "max_candidates": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_CANDIDATES,
                    "description": "Maximum candidates to register, from 1 through 20. Defaults to 10.",
                },
            },
            "required": ["evidence_artifacts"],
        }
    }
)
def discover_flag_candidates(evidence_artifacts: List[str], max_candidates: int = 10) -> str:
    """Scan CTF artifacts for flag shapes and register opaque, artifact-backed validation candidates."""

    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or not 1 <= max_candidates <= _MAX_CANDIDATES
    ):
        raise ValueError("max_candidates must be an integer from 1 through 20")
    artifacts = memory_tools._validated_artifact_paths(
        evidence_artifacts,
        require_one=True,
        allow_delimited_strings=True,
    )
    discovered: List[Dict[str, Any]] = []
    seen_values = set()
    for artifact_ref in artifacts:
        for match in _artifact_matches(artifact_ref):
            if match["value"] in seen_values:
                continue
            seen_values.add(match["value"])
            candidate = json.loads(
                memory_tools.store_objective_candidate(
                    "flag",
                    match["value"],
                    f"Deterministically extracted a {match['match_type']} flag-shaped value from artifact line "
                    f"{match['line']}.",
                    ["Inspect the cited artifact and independently reproduce the extraction."],
                    [artifact_ref],
                )
            )
            discovered.append(
                {
                    "candidate_ref": candidate["candidate_ref"],
                    "candidate_uid": candidate["candidate_uid"],
                    "verification_task_ref": candidate["verification_task_ref"],
                    "verification_task_uid": candidate["verification_task_uid"],
                    "status": candidate["status"],
                    "match_type": match["match_type"],
                    "evidence_artifact": artifact_ref,
                    "artifact_line": match["line"],
                }
            )
            if len(discovered) >= max_candidates:
                return json.dumps({"candidate_count": len(discovered), "candidates": discovered}, sort_keys=True)
    return json.dumps({"candidate_count": len(discovered), "candidates": discovered}, sort_keys=True)
