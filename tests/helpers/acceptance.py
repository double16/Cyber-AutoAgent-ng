"""Strict task acceptance fixtures shared by unit tests."""

from modules.tools.memory import AcceptanceBasis, AcceptanceContract, AcceptanceCriterion, EvidenceRequirement


def make_acceptance(criterion_id: str = "test-outcome") -> AcceptanceContract:
    return AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(
            kind="procedure",
            description="Bounded test procedure",
            source_refs=["target:target-1", "plan:phase-1"],
            procedure={
                "methods": ["test-fixture"],
                "limits": {"max_items": 1},
                "stop_condition": "first_limit_reached",
                "gap_policy": "record_unassessed",
                "output_kind": "inventory_manifest",
            },
        ),
        criteria=[
            AcceptanceCriterion(
                id=criterion_id,
                description="Complete the test task objective",
                evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
            )
        ],
    )


def acceptance_dict(criterion_id: str = "test-outcome") -> dict:
    return make_acceptance(criterion_id).to_dict()


def task_proposal(
    title: str,
    objective: str,
    criterion_description: str = "test-outcome",
    *,
    evidence_kind: str = "inventory_manifest",
    target_ids: list[str] | None = None,
) -> dict:
    """Return the canonical bounded procedure proposal used by task-creation tests."""

    return {
        "title": title,
        "objective": objective,
        "basis_description": "Bounded test procedure",
        "methods": ["test-fixture"],
        "limits": {"max_items": 1},
        "output_kind": "inventory_manifest" if evidence_kind == "inventory_manifest" else "artifact",
        "criteria": [{"description": criterion_description}],
        "target_ids": list(target_ids or []),
    }
