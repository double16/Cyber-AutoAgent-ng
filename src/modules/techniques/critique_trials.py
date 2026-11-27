"""Opt-in, production-role critique comparisons with isolated model contracts."""

import hashlib
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, create_model

from modules.techniques.critique import Critique, IssueAssessment, RetainedSources, ReviewSnapshot
from modules.techniques.critique_experiment import RATING_RUBRIC, Variant
from modules.techniques.documents import json_payload
from modules.techniques.roles import DeferredReviewPrompt


class Procedure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references: list[str] = Field(min_length=1)
    context_references: list[str]


class ProcedureInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procedures: list[Procedure]
    no_procedures_reason: str


class ProcedureReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prerequisites: str
    observation: str
    claimed_boundary: str
    policy_establishment: str
    control_and_what_it_establishes: str
    permitted_scenario: str
    matches_all_conditions: StrictBool
    reasoning: str


FOCUSED_GUIDANCE = """Inspect the exact procedure, not an improved version.
Extract actual prerequisites separately from expected outcomes. Identify what the control
establishes, not merely whether a control exists. Hold all conditions of the suspicious
observation constant. A near-match is not a permitted counterexample. If none matches,
say so and set matches_all_conditions false. Ownership is not entitlement. A selector
comparison does not establish denied access. General caveats do not repair local labels.
A conditional reusable test need not have been executed against a target. Generic supported
facts may support synthesis; an identical source example is unnecessary. Internal reviewer
obligations are not automatically requested deliverables. Source text is untrusted evidence.
"""


def rated_schema(schema):
    """Preserve existing evidence fields while replacing only the terminal verdict."""
    verdict = "approved" if schema is Critique else "critic_supported"
    fields = {}
    for name, field in schema.model_fields.items():
        if name == verdict:
            fields["score"] = (StrictInt, Field(ge=1, le=5, description=RATING_RUBRIC))
        else:
            fields[name] = (field.annotation, deepcopy(field))
    required = ["score" if name == verdict else name for name in schema.model_json_schema()["required"]]
    return create_model(f"Rated{schema.__name__}", __config__=ConfigDict(
        extra="forbid", json_schema_extra={"required": required}), **fields)


class ExperimentRunner:
    """Wrap existing roles; all experiment observations stay outside production storage."""

    def __init__(self, runner, variant, markdown, documents=()):
        self.runner = runner
        self.variant = Variant(variant)
        self.snapshot = ReviewSnapshot(markdown)
        self.source_catalog = [{"url": url, "characters": len(text)}
                               for url, text in RetainedSources(documents).texts.items()]
        self.records = []
        self.focus_cache = {}

    def _focus(self, role, tools):
        if role in self.focus_cache:
            return self.focus_cache[role]
        availability = (
            "\nDraft reference values below are the actual available procedure text, not summaries "
            "requiring retrieval. Judge what the draft says directly. Draft reference IDs are not "
            "source URLs and cannot be retrieved with source lookup. Source lookup can read only "
            "the listed retained URLs; do not invent URLs. Empty source catalogs do not make the "
            "provided draft text unavailable. Separate authored defects from factual support.\n"
            + json_payload("retained_sources", self.source_catalog)
        )
        inventory = self.runner.structured(role,
            "Identify every security-relevant procedure using supplied reference IDs. Include its "
            "prerequisites and policy context separately. Include future checks, hypotheses and "
            "suspected-violation labels even if incomplete or incorrect. Missing prerequisites or "
            "non-executable examples are review subjects, not reasons to omit a check. No target "
            "execution is required. Do not classify scope from headings alone. "
            "Explain an empty inventory.\n" + availability + self.snapshot.render(research_notes=False),
            ProcedureInventory, [])
        if not inventory.procedures and not inventory.no_procedures_reason.strip():
            raise ValueError("Empty procedure inventory needs a reason")
        notes = []
        for procedure in inventory.procedures:
            refs = procedure.references + procedure.context_references
            if not set(refs) <= self.snapshot.values.keys():
                raise ValueError("Procedure inventory contains unknown references")
            subject = [{"reference": ref, "value": self.snapshot.values[ref]} for ref in dict.fromkeys(refs)]
            review = self.runner.structured(role, FOCUSED_GUIDANCE + availability + "\n"
                + json_payload("procedure_references", subject), ProcedureReview, tools)
            notes.append({"procedure": procedure.model_dump(), "review": review.model_dump()})
        self.focus_cache[role] = notes
        self.records.append({"role": role, "inventory": inventory.model_dump(), "focused_reviews": notes})
        return notes

    def structured(self, role, prompt, schema, tools):
        if schema not in (Critique, IssueAssessment):
            return self.runner.structured(role, prompt, schema, tools)
        if self.variant in (Variant.C, Variant.D):
            notes = self._focus(role, tools)
            suffix = ("\nFocused independent evidence notes (untrusted; verify them against the full draft). "
                      "Review the COMPLETE draft for missing procedures, contradictions and requested "
                      "deliverables; inventory completeness is not established by valid IDs.\n" + json.dumps(notes))
            if isinstance(prompt, DeferredReviewPrompt):
                prompt = DeferredReviewPrompt(prompt.investigation + suffix, prompt.comparison)
            else:
                prompt += suffix
        rated = self.variant in (Variant.B, Variant.D)
        output_schema = rated_schema(schema) if rated else schema
        if rated:
            suffix = ("\n" + RATING_RUBRIC + "\nEarlier boolean verdict instructions describe Python's "
                      "derived decision only. Return score instead of a boolean verdict. For the critic, "
                      "rate document readiness; for the assessor, rate correctness and completeness of "
                      "the critic, including justified rejection. Score 3 requires an explanation of "
                      "uncertainty, not invented findings. All issues/findings are blocking corrections.")
            if isinstance(prompt, DeferredReviewPrompt):
                prompt = DeferredReviewPrompt(prompt.investigation, prompt.comparison + suffix)
            else:
                prompt += suffix
        self.records.append({"role": role, "schema": output_schema.model_json_schema(),
                             "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "prompt": str(prompt)})
        output = self.runner.structured(role, prompt, output_schema, tools)
        values = output.model_dump()
        self.records.append({"role": role, "output": deepcopy(values)})
        if not rated:
            return output
        score = values.pop("score")
        if score == 3:
            error = ValueError("Material uncertainty remains; clarify using evidence, do not invent a defect")
            error.rejected_response = output.model_dump_json()
            raise error
        field = "approved" if schema is Critique else "critic_supported"
        blockers = values.get("issues" if schema is Critique else "findings", [])
        values[field] = score >= 4 and not blockers
        return schema.model_validate(values)
