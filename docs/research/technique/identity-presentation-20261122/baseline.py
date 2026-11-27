"""Frozen pre-separator publication view for research replay, not production use."""

import json
from typing import Any

from modules.agents.hypothesis_context import review_input_candidates
from modules.handlers.utils import sanitize_toon_value
from modules.tools.hypotheses import hypothesis_binding, load_hypothesis_set


def _rows(label: str, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    lines = [f"{label}[{len(rows)}]{{{','.join(fields)}}}:"]
    for row in rows:
        lines.append("  " + ",".join(sanitize_toon_value(
            json.dumps(row[field], ensure_ascii=False, sort_keys=True)
            if isinstance(row[field], (dict, list)) else str(row[field])
        ) for field in fields))
    return "\n".join(lines)


def publication_review(task: Any, operation_id: str) -> str:
    """Render every current record, independently of acceptance claims and superseded snapshots."""

    binding = hypothesis_binding(task)
    publication = load_hypothesis_set(binding.get("artifact_ref", ""), task, operation_id)
    subjects = [{"item_id": subject.item_id, "disposition": subject.disposition,
                 "reason": subject.reason, "hypothesis_count": len(subject.hypotheses)}
                for subject in publication.subjects]
    records = [{"item_id": subject.item_id, **hypothesis.model_dump()}
               for subject in publication.subjects for hypothesis in subject.hypotheses]
    candidates = review_input_candidates(task) if any(not subject.hypotheses for subject in publication.subjects) else {}
    candidate_rows = [row for subject_rows in candidates.values() for row in subject_rows]
    return (
        _rows("reconciled_input_candidates", ("subject_id", "candidate_id", "name", "location", "origins"),
              candidate_rows)
        + f"\n## Authoritative current hypothesis publication\n{binding['artifact_ref']} sha256={binding['sha256']}\n"
        "Only this publication establishes current coverage. Older snapshots and prose summaries are history.\n"
        + _rows("subjects", ("item_id", "disposition", "reason", "hypothesis_count"), subjects)
        + "\nComparison views below reproduce current records; they add no evidence or judgments.\n"
        + "".join(
            f"\n### Hypothesis {record['hypothesis_id']} (subject {record['item_id']})\n"
            + _rows("comparison", ("field", "value"), [
                {"field": key, "value": record[key]} for key in (
                    "observed_basis", "suspected_mechanism", "preconditions", "attacker_controlled_input",
                    "authentication_requirement", "safe_test_steps", "positive_expectations",
                    "negative_expectations", "required_evidence", "supporting_refs",
                )
            ])
            for record in records
        )
    )
