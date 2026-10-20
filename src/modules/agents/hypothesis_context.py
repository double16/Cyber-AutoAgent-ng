"""Filesystem-backed investigation context and authoritative hypothesis review rendering."""

import hashlib
import json
from pathlib import Path
from typing import Any

from modules.handlers.utils import sanitize_toon_value
from modules.tools.artifact import resolve_operation_artifact_path
from modules.tools.hypotheses import hypothesis_binding, load_hypothesis_set
from modules.tools.inventory_provenance import operation_provenance
from modules.tools.memory import _load_inventory_manifest, canonical_artifact_reference

HYPOTHESIS_GROUNDING_GUIDANCE = """Summary records are not exhaustive implementation evidence. Empty input lists
mean inputs are unrecorded, not absent; reconcile them with explicitly linked parameter records.
An input can ground a conditional future test without proving unsafe handling or backend technology;
its presence alone does not require a hypothesis. When a summary cannot characterize an interaction,
follow its supplied implementation/source references with targeted artifact searches around the assigned
subject, handlers, components, or request functions. Extraction artifacts may reference the full implementation.
Stay within supplied operation artifacts and existing inspection limits; exhaustive inspection is not required.
Listed operations are not exhaustive supported methods; inferred GET does not mean GET-only. Missing
operation provenance is unknown. Assigned-subject scope permits relevant linked implementation and call-path
inspection without expanding testing scope. Names are investigation leads, not proof of functionality.
The generation task's authentication requirement governs generation only, not proposed future tests.
Declare each hypothesis's own authentication_requirement: no roles for unauthenticated tests, one
declared role for single-session tests, or account/role/tenant comparisons when supported by observations.
Consider unauthenticated versus each relevant role and same-role accounts where an access boundary
supports them; different-role tests require distinct declared roles. One role is not a cross-role gap.
Declared roles alone do not justify tests. Preserve ownership and trust-boundary observations and their
source references; do not infer them from names. Missing session readiness does not justify an empty
publication; propose the future prerequisites without logging in during generation.
Observed functionality may support
conditional future tests: separate observed facts, assumed preconditions, and behavior the test must establish.
A reasoned no_testable_hypotheses is valid when inspected evidence supports no grounded, safe,
security-relevant test; do not invent hypotheses or require a minimum count. Missing optional metadata,
unexamined necessary evidence, and inspection limits do not themselves justify that disposition.
If concrete necessary evidence remains unexamined, identify the source and missing information through
existing prerequisite handling: bind source_refs to the frozen parent inventory and name the referenced
implementation artifact in missing_input. Do not treat optional absent attributes as prerequisites.
"""


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


def _subject_records(task: Any, items: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Select assigned records and explicit same-target supporting parameters only."""

    assigned = {item["id"]: item for item in items if item["id"] in task.acceptance.basis.item_ids}
    supporting = {}
    for item in items:
        parent = assigned.get(item.get("attributes", {}).get("endpoint_id"))
        if (item["id"] not in assigned and item["kind"] == "parameter" and parent is not None
                and parent["kind"] == "endpoint" and item.get("target_id") is not None
                and item["target_id"] == parent.get("target_id")):
            supporting[item["id"]] = item
    return list(assigned.values()), list(supporting.values())


def frozen_input_context(task: Any, output_root: str) -> str:
    """Keep complete assigned records on disk; never turn their availability into execution proof."""

    basis = task.acceptance.basis
    identity = {"context_version": 2, "source_refs": list(basis.source_refs), "snapshot_hash": basis.snapshot_hash,
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
            supporting = {}
            for reference in basis.source_refs:
                manifest, digest = _load_inventory_manifest(reference, persist_normalization=False)
                if digest != basis.snapshot_hash:
                    raise ValueError("Frozen inventory snapshot changed")
                selected, linked = _subject_records(task, manifest["items"])
                records.update({item["id"]: item for item in selected})
                supporting.update({item["id"]: item for item in linked})
            cached = {"identity": identity, "fingerprints": fingerprints, "items": list(records.values()),
                      "supporting_items": list(supporting.values())}
            _save(path, cached)
        items = cached["items"]
        supporting_items = cached["supporting_items"]
        rows = [{"id": item["id"], "kind": item["kind"], "value": item["value"],
                 "attributes": item.get("attributes", {})} for item in items]
        # Oversized records remain complete on disk and can be paged through existing artifact tools.
        rendered = _rows("assigned_inventory", ("id", "kind", "value", "attributes"), rows)
        detail = "The records below include all assigned attributes."
        if len(rendered) > 12_000:
            rendered = _rows("assigned_inventory", ("id", "kind", "value"), rows)
            detail = "Attributes are omitted below; complete attributes remain in the assigned-record artifact."
        sources = _resolved_input_sources(task, set(basis.source_refs), items=items + supporting_items)
        source_path = path.with_name(path.stem + "-sources.json")
        source_rows = [{"reference": reference, "source": source, "availability": _availability(reference)}
                       for reference, source in sorted(sources.items())]
        _save(source_path, source_rows)
        operations = [{"item_id": item["id"],
                       "operations": item.get("attributes", {}).get("interaction", {}).get("operations", []),
                       "provenance": operation_provenance(item.get("attributes", {}).get("interaction", {}))}
                      for item in items]
        input_rows = []
        for item in items + supporting_items:
            interaction = item.get("attributes", {}).get("interaction", {})
            input_rows.append({
                "subject_id": (item["attributes"]["endpoint_id"] if item in supporting_items else item["id"]),
                "source_record_id": item["id"], "inputs": interaction.get("inputs", []),
                "operations": interaction.get("operations", []),
                "provenance": operation_provenance(interaction),
            })
        input_summary = _rows("recorded_inputs", (
            "subject_id", "source_record_id", "inputs", "operations", "provenance",
        ), input_rows)
        if len(input_summary) > 12_000:
            input_summary = "Input summary exceeds prompt limit; inspect complete supporting_items and items below."
        return (
            "\nResolved investigation references (availability is not inspection or semantic support):\n"
            + _rows("sources", ("reference", "source", "availability"), source_rows[:32])
            + f"\nComplete references: artifact:{source_path.relative_to(output_root).as_posix()}\n"
            + _rows("recorded_operations", ("item_id", "operations", "provenance"), operations)
            + "\n" + input_summary
            + f"\n## Frozen investigation inputs (not completion evidence)\n"
            f"Complete assigned and supporting records: artifact:{path.relative_to(output_root).as_posix()}\n{rendered}\n"
            "Supporting parameters are context only, not additional publication subjects. "
            f"{detail} Absent optional attributes in complete records are unrecorded; "
            "their absence alone does not establish a missing prerequisite.\n"
        )
    except (OSError, TypeError, ValueError, KeyError) as error:
        return f"\nFrozen input context unavailable: {error}; do not invent records.\n"


def _availability(reference: str) -> str:
    try:
        return "available" if Path(resolve_operation_artifact_path(reference)).is_file() else "unavailable"
    except (OSError, ValueError):
        return "unavailable"


def _resolved_input_sources(
    task: Any, source_refs: set[str], *, items: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Resolve assigned provenance and one hop of explicit links from bounded JSON summaries."""

    references = {source: source for source in sorted(source_refs)}
    if items is None:
        items = []
        for source in sorted(source_refs):
            try:
                manifest, digest = _load_inventory_manifest(source, persist_normalization=False)
                if digest == task.acceptance.basis.snapshot_hash:
                    items.extend(manifest["items"])
            except (OSError, ValueError, TypeError, KeyError):
                continue
    assigned, supporting = _subject_records(task, items)
    for item in assigned + supporting:
        attributes = item.get("attributes", {})
        for field in ("source_artifact_refs", "implementation_refs"):
            for reference in attributes.get(field, []):
                if isinstance(reference, str) and reference.startswith("artifact:"):
                    references.setdefault(reference, f"assigned item {item['id']} {field}")
        for reference in attributes.get("interaction", {}).get("evidence_refs", []):
            if isinstance(reference, str) and reference.startswith("artifact:"):
                references.setdefault(reference, f"inventory record {item['id']} evidence_refs")
    # Bound summary inspection; the full assigned records remain available on disk.
    for reference in sorted(set(references) - source_refs)[:32]:
        try:
            with open(resolve_operation_artifact_path(reference), encoding="utf-8") as stream:
                text = stream.read(1_000_001)
            if len(text) > 1_000_000:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                for value in payload.values():
                    if isinstance(value, str) and value.startswith("artifact:"):
                        references.setdefault(value, reference)
        except (OSError, ValueError, TypeError):
            continue
    return references


def prerequisite_input_sources(task: Any, source_refs: set[str]) -> set[str]:
    """Use the same reference resolution for normal context and prerequisite recovery."""

    return set(_resolved_input_sources(task, source_refs))


def _inspection_details(outcome: Any) -> tuple[str, dict[str, Any]]:
    inputs = outcome.structured_input
    if not isinstance(inputs, dict):
        return "", {}
    field = {"read_artifact": "path", "search_artifact": "path",
             "get_inventory_item": "manifest_ref", "read_hypothesis_input": "source_ref"}[outcome.tool_name]
    source = inputs.get(field)
    if not isinstance(source, str):
        return "", {}
    try:
        reference = canonical_artifact_reference(source)
    except (OSError, ValueError):
        return "", {}
    lookup = {key: inputs[key] for key in (
        "start_line", "max_lines", "offset", "max_chars", "search_pattern", "search_regex",
        "context_lines", "item_id",
    ) if key in inputs}
    return reference, lookup


def retain_inspections(task: Any, outcomes: list[Any], output_root: str) -> None:
    """Retain only a bounded ledger of successful inspection arguments and returned references."""

    path = _inspection_path(task, output_root)
    entries = json.loads(path.read_text()) if path.exists() else []
    for outcome in outcomes:
        if not outcome.success or outcome.tool_name not in {
            "read_artifact", "search_artifact", "get_inventory_item", "read_hypothesis_input",
        }:
            continue
        reference, lookup = _inspection_details(outcome)
        entry = {"tool": outcome.tool_name, "input": outcome.input_summary,
                 "inspected_reference": reference, "lookup": lookup,
                 "returned_references": list(outcome.artifact_refs)}
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
             "inspected_reference": entry.get("inspected_reference", "unknown"),
             "lookup": entry.get("lookup", {}),
             "returned_references": entry.get("returned_references", entry.get("references", []))}
            for entry in entries]
    return ("\nAlready inspected sources and recorded lookup arguments (bounded recent history):\n"
        "Returned references alone are not inspected. Reads and searches may cover only part of a source;\n"
        "no recorded inspection means not recorded, not proof that the source was never inspected.\n") + _rows(
        "inspections", ("tool", "input", "inspected_reference", "lookup", "returned_references"), rows,
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
