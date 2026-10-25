"""Typed, protocol-neutral hypothesis deliverables and bounded consumer access."""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from strands import tool

from modules.authentication_requirements import AuthenticationRequirement, validate_comparison_requirement
from modules.tools.artifact import resolve_operation_artifact_path
from modules.tools.artifact_references import require_artifact_reference
from modules.tools.tool_result_policy import bounded_inline_result

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Hypothesis(BaseModel):
    """A future test, not a vulnerability conclusion."""

    model_config = ConfigDict(extra="forbid")
    hypothesis_id: str = ""
    authentication_requirement: AuthenticationRequirement
    observed_basis: Text
    preconditions: list[Text] = Field(min_length=1)
    suspected_mechanism: Text
    attacker_controlled_input: Text
    safe_test_steps: list[Text] = Field(min_length=1)
    positive_expectations: list[Text] = Field(min_length=1)
    negative_expectations: list[Text] = Field(min_length=1)
    required_evidence: list[Text] = Field(min_length=1)
    supporting_refs: list[Text] = Field(
        min_length=1, description="Current-operation artifact: or artifact_id: references, not memory IDs or URLs.",
    )
    research_refs: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_authentication(self) -> "Hypothesis":
        validate_comparison_requirement(self.authentication_requirement)
        if self.authentication_requirement.comparison != "none" and not self.authentication_requirement.roles:
            raise ValueError("comparison hypotheses require explicit roles")
        if self.authentication_requirement.comparison == "none" and len(self.authentication_requirement.roles) > 1:
            raise ValueError("single-session hypotheses allow at most one role")
        return self


class SubjectHypotheses(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: Text
    disposition: Literal["hypotheses_supplied", "no_testable_hypotheses"]
    reason: str = ""
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_disposition(self) -> "SubjectHypotheses":
        if self.disposition == "hypotheses_supplied" and not self.hypotheses:
            raise ValueError("hypotheses_supplied requires at least one hypothesis")
        if self.disposition == "no_testable_hypotheses" and (self.hypotheses or not self.reason.strip()):
            raise ValueError("no_testable_hypotheses requires a reason and no hypotheses")
        return self


class HypothesisSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["hypothesis_set_v1"] = "hypothesis_set_v1"
    operation_id: Text
    producer_task_uid: Text
    snapshot_hash: Text
    source_refs: list[Text] = Field(min_length=1)
    subjects: list[SubjectHypotheses] = Field(min_length=1)


def hypothesis_contract(task: Any) -> Any:
    """Freeze the additional deliverable requirement before a producer is stored."""

    from dataclasses import replace

    from modules.tools.memory import EvidenceRequirement

    criteria = tuple(
        replace(criterion, evidence_requirements=(
            *criterion.evidence_requirements,
            *(() if any(req.kind == "hypothesis_set" for req in criterion.evidence_requirements)
              else (EvidenceRequirement(kind="hypothesis_set"),)),
        ))
        for criterion in task.acceptance.criteria
    )
    return replace(task, acceptance=replace(task.acceptance, criteria=criteria, manifest_hash=""))


def hypothesis_binding(task: Any) -> dict[str, Any]:
    """Return the authoritative cumulative publication, never a superseded or legacy binding."""

    publications = task.recovery_context.get("hypothesis_publications", [])
    if not isinstance(publications, list):
        raise ValueError("Invalid hypothesis publication history")
    if not publications:
        return {}
    binding = publications[-1]
    if not isinstance(binding, dict) or not binding.get("artifact_ref") or not binding.get("sha256"):
        raise ValueError("Invalid authoritative hypothesis publication binding")
    return binding


def publication_state(binding: dict[str, Any], publication: HypothesisSet) -> dict[str, Any]:
    """Project canonical agent metadata from a validated publication, including legacy bindings."""
    return {
        **{key: value for key, value in binding.items() if key not in {"subject_ids", "published_subject_ids"}},
        "total_published_subject_ids": sorted(subject.item_id for subject in publication.subjects),
        "last_updated_subject_ids": binding.get("last_updated_subject_ids", binding.get("published_subject_ids", [])),
    }


def _validate_supporting_refs(subject: SubjectHypotheses) -> None:
    for index, hypothesis in enumerate(subject.hypotheses):
        for support in hypothesis.supporting_refs:
            try:
                resolve_operation_artifact_path(require_artifact_reference(support))
            except (OSError, TypeError, ValueError) as error:
                raise ValueError(
                    f"Subject {subject.item_id} hypotheses[{index}].supporting_refs: invalid reference {support!r}. "
                    "Cite an existing current-operation artifact: or artifact_id: reference; "
                    "for memory context, cite its underlying artifact. " + str(error)
                ) from error


def load_hypothesis_set(
    reference: str, task: Any, operation_id: str, *, require_complete: bool = True,
) -> HypothesisSet:
    """Check the explicit producer binding, content digest, and frozen subject assignment."""

    from modules.tools.memory import canonical_artifact_reference

    binding = hypothesis_binding(task)
    canonical = canonical_artifact_reference(reference)
    # Acceptance bookkeeping may copy the published artifact into an immutable evidence snapshot.
    # The controller binding and the submitted copy must both retain the complete content digest.
    bound = binding.get("artifact_ref")
    if not bound:
        raise ValueError("hypothesis artifact is not bound to this producer")
    original = Path(resolve_operation_artifact_path(bound)).read_bytes()
    if hashlib.sha256(original).hexdigest() != binding.get("sha256"):
        raise ValueError("bound hypothesis artifact digest changed")
    path = resolve_operation_artifact_path(canonical)
    content = Path(path).read_bytes()
    if hashlib.sha256(content).hexdigest() != binding.get("sha256"):
        raise ValueError("hypothesis artifact digest changed")
    result = HypothesisSet.model_validate_json(content)
    basis = task.acceptance.basis
    subjects = [item.item_id for item in result.subjects]
    if (
        result.operation_id != operation_id or result.producer_task_uid != task.task_uid
        or result.snapshot_hash != basis.snapshot_hash or set(result.source_refs) != set(basis.source_refs)
        or len(subjects) != len(set(subjects)) or not set(subjects).issubset(basis.item_ids)
    ):
        raise ValueError("hypothesis artifact does not match the frozen producer assignment")
    for subject in result.subjects:
        for hypothesis in subject.hypotheses:
            if not hypothesis.hypothesis_id:
                raise ValueError("hypothesis ID is required")
        _validate_supporting_refs(subject)
    missing = sorted(set(basis.item_ids) - set(subjects))
    if require_complete and missing:
        raise ValueError(f"Publish missing assigned subjects with record_hypotheses: {', '.join(missing)}")
    return result


def bind_hypothesis_acceptance(task: Any, operation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Attach validated controller-owned publication evidence without inventing semantic acceptance."""

    if payload.get("status") not in {"satisfied", "assessed_negative"}:
        return payload
    corrections = hypothesis_correction_state(task)
    if corrections.get("pending"):
        raise ValueError("Revise or withdraw the requested hypotheses before acceptance: "
                         + ", ".join(sorted(corrections["pending"])))
    binding = hypothesis_binding(task)
    reference = binding.get("artifact_ref", "")
    if not reference:
        raise ValueError(
            "Publish assigned subjects with record_hypotheses before successful acceptance; missing subjects: "
            + ", ".join(sorted(task.acceptance.basis.item_ids))
        )
    load_hypothesis_set(reference, task, operation_id)
    # Retained older publications are provenance, not additional independent deliverables.
    obsolete = {item["artifact_ref"] for item in task.recovery_context["hypothesis_publications"][:-1]}
    references = [ref for ref in payload.get("evidence_refs", []) if ref not in obsolete]
    return {**payload, "evidence_refs": list(dict.fromkeys([*references, reference]))}


def hypothesis_correction_state(task: Any) -> dict[str, Any]:
    """Recover task-local correction progress without treating edits as semantic approval."""
    review = task.recovery_context.get("hypothesis_review", {})
    digest = review.get("publication_sha256")
    if not digest:
        return {}
    binding = hypothesis_binding(task)
    if digest != binding.get("sha256"):
        saved = binding.get("hypothesis_corrections", {})
        return saved if saved.get("reviewed_sha256") == digest else {}
    pending = {}
    for subject in review.get("subject_reviews", []):
        for item in subject.get("hypotheses", []):
            if item.get("decision") in {"revise", "withdraw"}:
                pending[item["hypothesis_id"]] = {
                    "item_id": subject["item_id"], "decision": item["decision"],
                    "reason": item.get("reason", ""), "subject_reason": subject.get("reason", ""),
                    "semantic_checks": item.get("semantic_checks", {}),
                }
    return {"reviewed_sha256": digest, "expected_sha256": binding.get("sha256"), "pending": pending}


def _correction_content(value: Any) -> Any:
    """Ignore serialization order and generated identifiers when comparing corrections."""
    if isinstance(value, dict):
        return {key: _correction_content(item) for key, item in value.items() if key != "hypothesis_id"}
    if isinstance(value, list):
        return sorted((_correction_content(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value


def build_revise_hypothesis_tool(
    task: Any, operation_id: str, output_root: str, persist: Callable[[dict[str, Any]], None],
    *, current_task: Callable[[], Any],
    validate_authentication: Callable[[AuthenticationRequirement], None] | None = None,
) -> Any:
    """Expose a single-record correction while Python preserves the cumulative publication."""
    initial = hypothesis_correction_state(task)
    replacement_schema = Hypothesis.model_json_schema()
    replacement_schema["properties"].pop("hypothesis_id")
    definitions = replacement_schema.pop("$defs", {})
    schema = {"json": {
        "type": "object", "properties": {
            "hypothesis_id": {"type": "string", "enum": sorted(initial.get("pending", {}))},
            "replacement": {"anyOf": [replacement_schema, {"type": "null"}]},
            "reason": {"type": "string", "minLength": 1},
        }, "required": ["hypothesis_id", "replacement", "reason"], "additionalProperties": False,
        "$defs": definitions,
    }}

    mutation_lock = Lock()

    def apply_revision(hypothesis_id: str, replacement: dict[str, Any] | None, reason: str) -> str:
        latest = current_task()
        binding = hypothesis_binding(latest)
        state = hypothesis_correction_state(latest)
        if (state.get("reviewed_sha256") != initial.get("reviewed_sha256")
                or state.get("expected_sha256") != binding.get("sha256")):
            raise ValueError("Correction publication changed; rebuild the correction tool")
        target = state.get("pending", {}).get(hypothesis_id)
        if target is None:
            raise ValueError("Hypothesis is not awaiting correction in this review")
        if not reason.strip():
            raise ValueError("Explain the revision or withdrawal")
        publication = load_hypothesis_set(binding["artifact_ref"], latest, operation_id, require_complete=False)
        subject = next(item for item in publication.subjects if item.item_id == target["item_id"])
        original = next(item for item in subject.hypotheses if item.hypothesis_id == hypothesis_id)
        revised = Hypothesis.model_validate(replacement) if replacement is not None else None
        if revised is not None and _correction_content(revised.model_dump()) == _correction_content(original.model_dump()):
            raise ValueError(f"Hypothesis is unchanged. Resolve this concern or withdraw it: {target['reason']}")
        remaining = [item for item in subject.hypotheses if item.hypothesis_id != hypothesis_id]
        if revised is not None:
            remaining.append(revised)
        corrected = SubjectHypotheses(
            item_id=subject.item_id, disposition="hypotheses_supplied" if remaining else "no_testable_hypotheses",
            reason="" if remaining else reason.strip(), hypotheses=remaining,
        )
        pending = {key: value for key, value in state["pending"].items() if key != hypothesis_id}

        def save(updated: dict[str, Any]) -> None:
            updated["hypothesis_corrections"] = {
                "reviewed_sha256": state["reviewed_sha256"], "expected_sha256": updated["sha256"],
                "pending": pending,
            }
            persist(updated)

        writer = build_record_hypotheses_tool(
            latest, operation_id, output_root, save, current_task=current_task,
            validate_authentication=validate_authentication,
        )
        return writer(subjects=[corrected.model_dump(mode="json")], mode="replace")

    @tool(name="revise_hypothesis", inputSchema=schema)
    def revise_hypothesis(hypothesis_id: str, replacement: dict[str, Any] | None, reason: str) -> str:
        """Resolve a review concern with a complete replacement, or replacement=null to withdraw it.

        Python preserves all other records. Changed content still requires semantic review.
        Do not execute the proposed future tests.
        """
        with mutation_lock:
            return apply_revision(hypothesis_id, replacement, reason)

    return revise_hypothesis


def build_record_hypotheses_tool(
    task: Any, operation_id: str, output_root: str, persist: Callable[[dict[str, Any]], None],
    *, current_task: Callable[[], Any] | None = None,
    validate_authentication: Callable[[AuthenticationRequirement], None] | None = None,
) -> Any:
    """Bind operation and assignment in Python; the model supplies only future-test reasoning."""

    subject_schema = SubjectHypotheses.model_json_schema()
    schema = {
        "json": {
            "type": "object", "properties": {
                "subjects": {"type": "array", "minItems": 1, "items": subject_schema},
                "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"},
            }, "required": ["subjects"],
        },
    }
    # Nested references resolve against the tool input root, not the array item.
    schema["json"]["$defs"] = subject_schema.pop("$defs", {})

    @tool(name="record_hypotheses", inputSchema=schema)
    def record_hypotheses(subjects: list[dict[str, Any]], mode: str = "append") -> str:
        """Append hypotheses by default. Use mode=replace with a subject's full corrected list to remove records.

        The returned artifact is cumulative. total_published_subject_ids lists all published subjects;
        last_updated_subject_ids lists subjects submitted by this call.
        published_subject_ids is its legacy alias.
        Python binds the latest complete publication to acceptance.
        """

        mode = mode.strip().lower()
        mode = {"add": "append", "merge": "append", "overwrite": "replace"}.get(mode, mode)
        if mode not in {"append", "replace"}:
            raise ValueError("mode must be append or replace")
        grouped = {}
        aliases = {"supplied": "hypotheses_supplied", "none": "no_testable_hypotheses"}
        for raw in subjects:
            entry = dict(raw)
            value = str(entry.get("disposition", "")).strip().lower()
            entry["disposition"] = aliases.get(value, value)
            subject = SubjectHypotheses.model_validate(entry)
            if validate_authentication is not None:
                for hypothesis in subject.hypotheses:
                    validate_authentication(hypothesis.authentication_requirement)
            prior = grouped.get(subject.item_id)
            if prior is not None:
                if prior.disposition != subject.disposition:
                    raise ValueError(f"Conflicting dispositions for subject {subject.item_id}")
                subject.hypotheses = [*prior.hypotheses, *subject.hypotheses]
            grouped[subject.item_id] = subject
        normalized = list(grouped.values())
        ids = [item.item_id for item in normalized]
        basis = task.acceptance.basis
        if not basis.snapshot_hash or not basis.source_refs or not basis.item_ids:
            raise ValueError("hypothesis producer requires a frozen inventory snapshot")
        if not ids or not set(ids).issubset(basis.item_ids):
            raise ValueError(
                "Submit one or more assigned subject IDs; foreign IDs are not allowed"
            )
        for subject in normalized:
            for hypothesis in subject.hypotheses:
                identity = json.dumps(hypothesis.model_dump(exclude={"hypothesis_id"}), sort_keys=True)
                hypothesis.hypothesis_id = hashlib.sha256(
                    f"{task.task_uid}|{subject.item_id}|{identity}".encode()
                ).hexdigest()[:24]
            subject.hypotheses = list({item.hypothesis_id: item for item in subject.hypotheses}.values())
            _validate_supporting_refs(subject)
        latest_task = current_task() if current_task is not None else task
        if latest_task.acceptance.manifest_hash != task.acceptance.manifest_hash:
            raise ValueError("Frozen hypothesis producer assignment changed; rebuild the bound tool")
        binding = hypothesis_binding(latest_task)
        previous = (load_hypothesis_set(binding["artifact_ref"], latest_task, operation_id, require_complete=False)
                    if binding else None)
        merged = {subject.item_id: subject for subject in previous.subjects} if previous else {}
        for subject in normalized:
            prior = merged.get(subject.item_id)
            if mode == "append" and prior is not None and prior.hypotheses:
                if not subject.hypotheses:
                    raise ValueError(
                        f"Subject {subject.item_id} has hypotheses; use mode=replace for a no-hypothesis disposition"
                    )
                subject.hypotheses = list({
                    item.hypothesis_id: item for item in [*prior.hypotheses, *subject.hypotheses]
                }.values())
            subject.hypotheses.sort(key=lambda item: item.hypothesis_id)
            merged[subject.item_id] = subject
        payload = HypothesisSet(
            operation_id=operation_id, producer_task_uid=task.task_uid, snapshot_hash=basis.snapshot_hash,
            source_refs=list(basis.source_refs), subjects=[merged[key] for key in sorted(merged)],
        )
        content = payload.model_dump_json(indent=2).encode()
        digest = hashlib.sha256(content).hexdigest()
        if binding and binding["sha256"] == digest:
            artifact_ref = binding["artifact_ref"]
        else:
            folder = Path(output_root) / "artifacts" / "hypotheses"
            folder.mkdir(parents=True, exist_ok=True)
            filename = f"{hashlib.sha256(task.task_uid.encode()).hexdigest()[:16]}-{digest[:16]}.json"
            path = folder / filename
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)
            artifact_ref = f"artifact:artifacts/hypotheses/{filename}"
        binding = {
            "artifact_ref": artifact_ref, "sha256": digest,
            "subject_ids": sorted(merged), "published_subject_ids": sorted(ids),
            "total_published_subject_ids": sorted(merged), "last_updated_subject_ids": sorted(ids),
            "missing_subject_ids": sorted(set(basis.item_ids) - set(merged)),
            "hypothesis_ids_by_subject": {
                key: [item.hypothesis_id for item in value.hypotheses] for key, value in sorted(merged.items())
            },
            "hypothesis_counts": {key: len(value.hypotheses) for key, value in sorted(merged.items())},
        }
        publications = list(latest_task.recovery_context.get("hypothesis_publications", []))
        persist(binding)
        # Direct tool users and callbacks retain the same durable metadata contract.
        if not publications or publications[-1]["sha256"] != digest:
            publications.append(binding)
        else:
            publications[-1] = binding
        task.recovery_context["hypothesis_publications"] = publications
        return json.dumps(binding)

    return record_hypotheses


def build_read_hypotheses_tool(
    bindings: list[tuple[str, Any]], operation_id: str, allowed_ids: set[str],
    hypothesis_ids: set[str] | None = None,
) -> Any:
    @tool(name="read_hypotheses")
    def read_hypotheses(item_id: str, offset: int = 0, max_records: int = 5) -> str:
        """Read validated hypothesis records for one assigned subject, with bounded pagination."""

        if item_id not in allowed_ids or offset < 0 or not 1 <= max_records <= 10:
            raise ValueError("use an assigned item_id, non-negative offset, and max_records from 1 to 10")
        records = []
        dispositions = []
        for reference, producer in bindings:
            payload = load_hypothesis_set(reference, producer, operation_id)
            for subject in payload.subjects:
                if subject.item_id == item_id:
                    dispositions.append({"producer_task_uid": producer.task_uid,
                                         "disposition": subject.disposition, "reason": subject.reason})
                    records.extend(item.model_dump() for item in subject.hypotheses
                                   if hypothesis_ids is None or item.hypothesis_id in hypothesis_ids)
        if not dispositions:
            raise ValueError("no validated hypothesis output covers this subject")
        page = records[offset:offset + max_records]
        encoded = json.dumps(page)
        if len(encoded) + len(json.dumps(dispositions)) > 20_000:
            raise ValueError("hypothesis page exceeds 20000 characters; request fewer records")
        return json.dumps({"item_id": item_id, "dispositions": dispositions, "hypotheses": page,
                           "next_offset": offset + len(page) if offset + len(page) < len(records) else None})

    return read_hypotheses


def build_hypothesis_input_reader(source_refs: set[str], max_inline_chars: int = 30_000) -> Any:
    """Bind checkpoint follow-up inspection to the critic's identified frozen inputs."""

    @tool(name="read_hypothesis_input")
    def read_hypothesis_input(source_ref: str, offset: int = 0, max_chars: int = 4000) -> str:
        """Read a bounded page of an identified prerequisite input; offsets count characters."""

        if source_ref not in source_refs:
            raise ValueError(f"Use an identified source_ref from: {sorted(source_refs)}")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= max_chars <= 10_000:
            raise ValueError("max_chars must be from 1 to 10000")
        path = resolve_operation_artifact_path(require_artifact_reference(source_ref))
        with open(path, encoding="utf-8", errors="replace") as stream:
            # Stream through preceding characters without retaining the whole input in memory.
            remaining = offset
            while remaining:
                chunk = stream.read(min(remaining, 10_000))
                if not chunk:
                    break
                remaining -= len(chunk)
            page = stream.read(max_chars)
            more = bool(stream.read(1))
        def encode(length: int) -> str:
            return json.dumps({"source_ref": source_ref, "text": page[:length],
                               "next_offset": offset + length if more or length < len(page) else None},
                              ensure_ascii=False)

        low, high = 0, len(page)
        while low < high:
            middle = (low + high + 1) // 2
            if len(encode(middle)) <= max_inline_chars:
                low = middle
            else:
                high = middle - 1
        result = encode(low)
        if len(result) > max_inline_chars or (page and low == 0):
            raise ValueError("Inline ceiling is too small for a source reference and one character")
        return result

    return bounded_inline_result(read_hypothesis_input)
