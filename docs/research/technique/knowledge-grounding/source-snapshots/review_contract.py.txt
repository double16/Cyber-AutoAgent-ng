"""Finite controller-owned criteria; semantic materiality remains a model judgment."""

import hashlib
import json
from dataclasses import dataclass

from modules.techniques.support_scope import SUPPORT_SCOPE_GUIDANCE

CORE_CRITERIA = (
    ("core:correctness",
     ("Identify concrete technical errors, contradictions or consequential procedural flaws; "
     "model knowledge and reasoning are allowed without retrieved support.")),
    ("core:interpretation",
     "Outcome interpretations distinguish legitimate behavior from boundary violations."),
    ("core:operational_sufficiency",
     "The requested method can be followed with explicit necessary prerequisites and controls."),
    ("core:attribution",
     ("Attribution accurately describes retained sources and limitations in the document, including "
     "metadata; body placement is required only when explicitly requested.")),
    ("core:restrictions",
     "The method complies with supplied restrictions and document policies."),
)


@dataclass(frozen=True)
class ReviewContract:
    payload: str
    identifier: str
    criterion_ids: frozenset[str]

    @classmethod
    def freeze(cls, requirements, *, restrictions=()):
        if len({row.id for row in requirements}) != len(requirements):
            raise ValueError("Acceptance requirements must have unique controller IDs")
        criteria = [{"id": key, "obligation": value, "origin": "shared_review_policy"}
                    for key, value in CORE_CRITERIA]
        criteria += [{"id": f"requirement:{row.id}", "obligation": row.description,
                      "kind": row.kind, "grounding": row.grounding, "origin": "validated_request", "requirement_id": row.id}
                     for row in requirements]
        criteria += [{"id": f"source:{row.id}", "obligation": row.description,
                      "origin": "source_required", "requirement_id": row.id}
                     for row in requirements if row.grounding == "source_required"]
        criteria += [{**row, "id": f"restriction:{index}"} for index, row in enumerate(restrictions, 1)]
        payload = json.dumps({
            "acceptance_criteria": criteria,
            "reference_fields": {
                "requirement_ids": {"allowed_values": [row.id for row in requirements], "empty_list_allowed": True},
                "criterion_id": {"allowed_values": [row["id"] for row in criteria]},
            },
        }, ensure_ascii=False, sort_keys=True)
        return cls(payload, hashlib.sha256(payload.encode()).hexdigest(), frozenset(row["id"] for row in criteria))

    @classmethod
    def legacy(cls, requirements):
        """Compatibility catalog for explicit historical two-review experiments only."""
        current = cls.freeze(requirements)
        payload = json.loads(current.payload)
        for row in payload["acceptance_criteria"]:
            if row["id"] == "core:correctness":
                row.update(id="core:technical_support",
                           obligation="Asserted factual mechanisms have adequate retained support.")
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return cls(text, hashlib.sha256(text.encode()).hexdigest(),
                   frozenset(row["id"] for row in payload["acceptance_criteria"]))

    @classmethod
    def for_context(cls, requirements, context, module_policy):
        restrictions = [{"obligation": value, "origin": "user_methodology_restriction"}
                        for value in (context.methodology_constraints if context is not None else [])]
        policy = context.module_technique_policy if context is not None else module_policy
        restrictions += [{"obligation": row["restriction"], "origin": "module_technique_policy",
                          "module": row["module"]} for row in policy]
        return cls.freeze(requirements, restrictions=restrictions)

    def render(self):
        return (
            f"Frozen acceptance contract sha256={self.identifier}\n{self.payload}\n"
            "Reference fields have separate catalogs: requirement_ids uses only the exact request requirement "
            "IDs in reference_fields.requirement_ids.allowed_values. criterion_id uses one exact acceptance "
            "criterion from reference_fields.criterion_id.allowed_values. Do not copy an acceptance criterion "
            "into requirement_ids or add/remove a prefix. Use requirement_ids=[] for an existing-content "
            "defect supported by draft_references when no request requirement is cited. Missing-deliverable "
            "defects still require the relevant request requirement IDs. These rules also apply on correction.\n"
            + SUPPORT_SCOPE_GUIDANCE
            + "Only these criteria can justify a blocker. Identify material impact on the advertised method; "
            "optional elaboration belongs in suggestions. New claims introduced by the draft can violate "
            "existing criteria; do not invent new mandatory deliverables or silently broaden scope.\n"
        )
