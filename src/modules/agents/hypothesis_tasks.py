"""Deterministic, protocol-neutral task assignments from validated hypothesis publications."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from modules.tools.memory import TaskProposal


@dataclass(frozen=True)
class HypothesisAssignment:
    item_ids: tuple[str, ...]
    proposal: TaskProposal
    publications: tuple[dict[str, Any], ...]

    @property
    def hypothesis_ids(self) -> list[str]:
        return sorted({value for row in self.publications for value in row["hypothesis_ids"]})


def build_assignments(batch: Any, publications: list[tuple[Any, dict, Any]]) -> list[HypothesisAssignment]:
    """Partition each atomic inventory group by explicit authentication requirements.

    Validate the complete input before callers persist any tasks. No semantic field is inferred.
    """

    assignments = []
    for target_id, _kind, label, item_ids in batch.groups:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for producer, binding, publication in publications:
            for subject in publication.subjects:
                if subject.item_id not in item_ids:
                    continue
                for hypothesis in subject.hypotheses:
                    requirement = hypothesis.authentication_requirement.model_dump()
                    requirement["roles"] = sorted(requirement["roles"])
                    key = json.dumps(requirement, sort_keys=True)
                    row = {"item_id": subject.item_id, "producer_task_uid": producer.task_uid,
                           "artifact_ref": binding["artifact_ref"], "sha256": binding["sha256"],
                           "hypothesis_ids": [hypothesis.hypothesis_id]}
                    if not hypothesis.hypothesis_id:
                        raise ValueError("Published hypothesis requires a stable hypothesis_id")
                    buckets.setdefault(key, []).append(row)
        for key, rows in sorted(buckets.items()):
            rows.sort(key=lambda row: (row["item_id"], row["producer_task_uid"], row["hypothesis_ids"]))
            identity = json.dumps({"snapshot": batch.snapshot_ref, "subjects": sorted(item_ids),
                                   "authentication": json.loads(key), "publications": rows}, sort_keys=True)
            family = "hypothesis_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
            objective = (
                "Assess every assigned hypothesis using its declared preconditions, safe steps, "
                "positive expectations and negative controls. Retain evidence for each outcome or "
                "identify a concrete unavailable prerequisite. Do not assess unassigned hypotheses."
            )
            proposal = TaskProposal(
                title=f"Test assigned hypotheses: {label}", objective=objective,
                snapshot_refs=[batch.snapshot_ref], methods=[], target_ids=[target_id],
                workstream=family, authentication_requirement=json.loads(key),
                criteria=[{"description": objective}],
            )
            assignments.append(HypothesisAssignment(tuple(item_ids), proposal, tuple(rows)))
    return assignments
