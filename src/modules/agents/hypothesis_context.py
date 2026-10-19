"""Filesystem-backed investigation context and authoritative hypothesis review rendering."""

import hashlib
import json
from pathlib import Path
from typing import Any

from modules.handlers.utils import sanitize_toon_value
from modules.tools.artifact import resolve_operation_artifact_path
from modules.tools.hypotheses import hypothesis_binding, load_hypothesis_set
from modules.tools.memory import _load_inventory_manifest


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _rows(label: str, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    lines = [f"{label}[{len(rows)}]{{{','.join(fields)}}}:"]
    for row in rows:
        lines.append("  " + ",".join(sanitize_toon_value(
            json.dumps(row[field], ensure_ascii=False) if isinstance(row[field], (dict, list)) else str(row[field])
        ) for field in fields))
    return "\n".join(lines)


def frozen_input_context(task: Any, output_root: str) -> str:
    """Keep complete assigned records on disk; never turn their availability into execution proof."""

    basis = task.acceptance.basis
    identity = {"source_refs": list(basis.source_refs), "snapshot_hash": basis.snapshot_hash,
                "item_ids": sorted(basis.item_ids)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = Path(output_root) / "artifacts" / "hypothesis_context" / f"{key}.json"
    try:
        fingerprints = {
            reference: hashlib.sha256(Path(resolve_operation_artifact_path(reference)).read_bytes()).hexdigest()
            for reference in basis.source_refs
        }
        cached = json.loads(path.read_text()) if path.exists() else {}
        if cached.get("identity") != identity or cached.get("fingerprints") != fingerprints:
            records = {}
            for reference in basis.source_refs:
                manifest, digest = _load_inventory_manifest(reference, persist_normalization=False)
                if digest != basis.snapshot_hash:
                    raise ValueError("Frozen inventory snapshot changed")
                records.update({item["id"]: item for item in manifest["items"] if item["id"] in basis.item_ids})
            cached = {"identity": identity, "fingerprints": fingerprints, "items": list(records.values())}
            _save(path, cached)
        items = cached["items"]
        rows = [{"id": item["id"], "kind": item["kind"], "value": item["value"],
                 "attributes": item.get("attributes", {})} for item in items]
        # Oversized records remain complete on disk and can be paged through existing artifact tools.
        rendered = _rows("assigned_inventory", ("id", "kind", "value", "attributes"), rows)
        detail = "The records below include all assigned attributes."
        if len(rendered) > 12_000:
            rendered = _rows("assigned_inventory", ("id", "kind", "value"), rows)
            detail = "Attributes are omitted below; complete attributes remain in the assigned-record artifact."
        return (
            f"\n## Frozen investigation inputs (not completion evidence)\n"
            f"Complete assigned records: artifact:{path.relative_to(output_root).as_posix()}\n{rendered}\n"
            f"{detail} Absent optional attributes in complete records are unrecorded; "
            "their absence alone does not establish a missing prerequisite.\n"
        )
    except (OSError, TypeError, ValueError, KeyError) as error:
        return f"\nFrozen input context unavailable: {error}; do not invent records.\n"


def retain_inspections(task: Any, outcomes: list[Any], output_root: str) -> None:
    """Retain only a bounded ledger of successful inspection arguments and returned references."""

    path = _inspection_path(task, output_root)
    entries = json.loads(path.read_text()) if path.exists() else []
    for outcome in outcomes:
        if not outcome.success or outcome.tool_name not in {
            "read_artifact", "search_artifact", "get_inventory_item", "read_hypothesis_input",
        }:
            continue
        entry = {"tool": outcome.tool_name, "input": outcome.input_summary,
                 "references": list(outcome.artifact_refs)}
        if entry in entries:
            entries.remove(entry)
        entries.append(entry)
    _save(path, entries[-24:])


def _inspection_path(task: Any, output_root: str) -> Path:
    key = hashlib.sha256(f"{task.task_uid}|{task.acceptance.basis.snapshot_hash}".encode()).hexdigest()
    return Path(output_root) / "artifacts" / "hypothesis_context" / f"{key}-inspections.json"


def inspection_context(task: Any, output_root: str) -> str:
    path = _inspection_path(task, output_root)
    if not path.exists():
        return ""
    entries = json.loads(path.read_text())
    rows = [{"tool": entry["tool"], "input": entry["input"][:800],
             "references": entry["references"]} for entry in entries]
    return "\nAlready inspected sources and recorded lookup arguments:\n" + _rows(
        "inspections", ("tool", "input", "references"), rows,
    )


def publication_review(task: Any, operation_id: str) -> str:
    """Render every current record, independently of acceptance claims and superseded snapshots."""

    binding = hypothesis_binding(task)
    publication = load_hypothesis_set(binding.get("artifact_ref", ""), task, operation_id)
    subjects = [{"item_id": subject.item_id, "disposition": subject.disposition,
                 "reason": subject.reason, "hypothesis_count": len(subject.hypotheses)}
                for subject in publication.subjects]
    records = [{"item_id": subject.item_id, **hypothesis.model_dump()}
               for subject in publication.subjects for hypothesis in subject.hypotheses]
    return (
        f"\n## Authoritative current hypothesis publication\n{binding['artifact_ref']} sha256={binding['sha256']}\n"
        "Only this publication establishes current coverage. Older snapshots and prose summaries are history.\n"
        + _rows("subjects", ("item_id", "disposition", "reason", "hypothesis_count"), subjects)
        + "\n" + _rows("hypotheses", ("item_id", "hypothesis_id", "observed_basis", "preconditions",
                                       "suspected_mechanism", "attacker_controlled_input", "safe_test_steps",
                                       "positive_expectations", "negative_expectations", "required_evidence",
                                       "supporting_refs", "authentication_requirement"), records)
    )
