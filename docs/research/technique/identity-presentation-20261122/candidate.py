"""Experimental rendering only; no role inference or workflow decisions."""

import json
from pathlib import Path

from modules.agents.hypothesis_context import _rows

CONTRACTS = json.loads(Path(__file__).with_name("contracts.json").read_text())


def render(publication, binding):
    subjects = [{"item_id": s.item_id, "disposition": s.disposition, "reason": s.reason,
                 "hypothesis_count": len(s.hypotheses)} for s in publication.subjects]
    parts = [
        "\n## Authoritative current hypothesis publication\n"
        + binding["artifact_ref"] + " sha256=" + binding["sha256"] + "\n"
        "Only this publication establishes current coverage. Older snapshots and prose summaries are history.\n",
        _rows("subjects", ("item_id", "disposition", "reason", "hypothesis_count"), subjects),
        "\nComparison views below reproduce current records; they add no evidence or judgments.\n",
    ]
    for subject in publication.subjects:
        for h in subject.hypotheses:
            record = h.model_dump()
            parts += [
                f"\n### Hypothesis {h.hypothesis_id} (subject {subject.item_id})\n",
                _rows("comparison", ("field", "value"), [
                    {"field": key, "value": record[key]} for key in (
                        "observed_basis", "suspected_mechanism", "preconditions", "attacker_controlled_input",
                        "positive_expectations", "negative_expectations", "supporting_refs",
                    )
                ]),
                "\n### Proposed procedure and declared authentication\n",
                _rows("procedure", ("field", "index", "value"), [
                    {"field": key, "index": index, "value": value}
                    for key in ("safe_test_steps", "required_evidence")
                    for index, value in enumerate(record[key], 1)
                ]),
                "\n" + _rows("authentication_requirement", ("comparison", "roles"), [
                    record["authentication_requirement"],
                ]),
                "\nHypothesis authentication contract: "
                + CONTRACTS["hypothesis_authentication"],
                "\nComparison meaning: " + CONTRACTS["comparison"],
                "\n",
            ]
    return "".join(parts)
