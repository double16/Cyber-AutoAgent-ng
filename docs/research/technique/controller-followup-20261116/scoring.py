"""Resolve the fixture's accepted result from durable ledger references, never a mutable alias."""

import json
from pathlib import Path


def accepted_result(case_root, acceptance):
    """Return one distinct accepted fixture payload and its references, or explicit absence."""
    root = Path(case_root).resolve()
    candidates = {}
    for result in acceptance:
        for reference in result.get("evidence_refs", []):
            if not reference.startswith("artifact:"):
                continue
            path = (root / reference.removeprefix("artifact:")).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Accepted artifact escapes the isolated case")
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict) or not {"decision", "observations"} <= payload.keys():
                continue
            identity = json.dumps(payload, sort_keys=True)
            if identity not in candidates:
                candidates[identity] = {"payload": payload, "references": []}
            candidates[identity]["references"].append(reference)
    if len(candidates) > 1:
        raise ValueError("Multiple distinct accepted fixture results; do not choose a mutable latest result")
    return next(iter(candidates.values()), None)
