"""Typed, protocol-neutral hypothesis deliverables and bounded consumer access."""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from strands import tool

from modules.tools.artifact import resolve_operation_artifact_path

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Hypothesis(BaseModel):
    """A future test, not a vulnerability conclusion."""

    model_config = ConfigDict(extra="forbid")
    hypothesis_id: str = ""
    observed_basis: Text
    preconditions: list[Text] = Field(min_length=1)
    suspected_mechanism: Text
    attacker_controlled_input: Text
    safe_test_steps: list[Text] = Field(min_length=1)
    positive_expectations: list[Text] = Field(min_length=1)
    negative_expectations: list[Text] = Field(min_length=1)
    required_evidence: list[Text] = Field(min_length=1)
    supporting_refs: list[Text] = Field(min_length=1)
    research_refs: list[Text] = Field(default_factory=list)


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


def load_hypothesis_set(reference: str, task: Any, operation_id: str) -> HypothesisSet:
    """Check the explicit producer binding, content digest, and frozen subject assignment."""

    from modules.tools.memory import canonical_artifact_reference

    binding = task.recovery_context.get("hypothesis_output") or {}
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
        or len(subjects) != len(set(subjects)) or set(subjects) != set(basis.item_ids)
    ):
        raise ValueError("hypothesis artifact does not match the frozen producer assignment")
    for subject in result.subjects:
        for hypothesis in subject.hypotheses:
            if not hypothesis.hypothesis_id:
                raise ValueError("hypothesis ID is required")
            for support in hypothesis.supporting_refs:
                resolve_operation_artifact_path(support)
    return result


def build_record_hypotheses_tool(
    task: Any, operation_id: str, output_root: str, persist: Callable[[dict[str, Any]], None],
) -> Any:
    """Bind operation and assignment in Python; the model supplies only future-test reasoning."""

    subject_schema = SubjectHypotheses.model_json_schema()
    schema = {
        "json": {
            "type": "object", "properties": {
                "subjects": {"type": "array", "minItems": 1, "items": subject_schema},
            }, "required": ["subjects"],
        },
    }
    # Nested references resolve against the tool input root, not the array item.
    schema["json"]["$defs"] = subject_schema.pop("$defs", {})

    @tool(name="record_hypotheses", inputSchema=schema)
    def record_hypotheses(subjects: list[dict[str, Any]]) -> str:
        """Publish hypotheses or a reasoned no-test disposition for every assigned inventory subject."""

        normalized = []
        aliases = {"supplied": "hypotheses_supplied", "none": "no_testable_hypotheses"}
        for raw in subjects:
            entry = dict(raw)
            value = str(entry.get("disposition", "")).strip().lower()
            entry["disposition"] = aliases.get(value, value)
            normalized.append(SubjectHypotheses.model_validate(entry))
        ids = [item.item_id for item in normalized]
        basis = task.acceptance.basis
        if not basis.snapshot_hash or not basis.source_refs or not basis.item_ids:
            raise ValueError("hypothesis producer requires a frozen inventory snapshot")
        if len(ids) != len(set(ids)) or set(ids) != set(basis.item_ids):
            raise ValueError("provide exactly one disposition for each assigned subject")
        for subject in normalized:
            seen = set()
            for hypothesis in subject.hypotheses:
                for support in hypothesis.supporting_refs:
                    resolve_operation_artifact_path(support)
                identity = json.dumps(hypothesis.model_dump(exclude={"hypothesis_id"}), sort_keys=True)
                hypothesis.hypothesis_id = hashlib.sha256(
                    f"{task.task_uid}|{subject.item_id}|{identity}".encode()
                ).hexdigest()[:24]
                if hypothesis.hypothesis_id in seen:
                    raise ValueError("duplicate hypothesis for the same subject")
                seen.add(hypothesis.hypothesis_id)
        payload = HypothesisSet(
            operation_id=operation_id, producer_task_uid=task.task_uid, snapshot_hash=basis.snapshot_hash,
            source_refs=list(basis.source_refs), subjects=normalized,
        )
        content = payload.model_dump_json(indent=2).encode()
        digest = hashlib.sha256(content).hexdigest()
        folder = Path(output_root) / "artifacts" / "hypotheses"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{hashlib.sha256(task.task_uid.encode()).hexdigest()[:16]}-{digest[:16]}.json"
        path = folder / filename
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        binding = {"artifact_ref": f"artifact:artifacts/hypotheses/{filename}", "sha256": digest}
        persist(binding)
        return json.dumps({**binding, "subject_ids": sorted(ids)})

    return record_hypotheses


def build_read_hypotheses_tool(
    bindings: list[tuple[str, Any]], operation_id: str, allowed_ids: set[str],
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
                    records.extend(item.model_dump() for item in subject.hypotheses)
        if not dispositions:
            raise ValueError("no validated hypothesis output covers this subject")
        page = records[offset:offset + max_records]
        encoded = json.dumps(page)
        if len(encoded) + len(json.dumps(dispositions)) > 20_000:
            raise ValueError("hypothesis page exceeds 20000 characters; request fewer records")
        return json.dumps({"item_id": item_id, "dispositions": dispositions, "hypotheses": page,
                           "next_offset": offset + len(page) if offset + len(page) < len(records) else None})

    return read_hypotheses
