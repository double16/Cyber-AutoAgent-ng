"""Opt-in purpose-aware review; legacy production contracts stay unchanged."""

import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from modules.techniques.critique import Critique, Interpretation, InterpretationReview, IssueAssessment, ReviewSnapshot
from modules.techniques.review_correctness import (
    GUIDANCE,
    CheckPurpose,
    CorrectionDisposition,
    PurposeReview,
    validate_dispositions,
)
from modules.techniques.roles import DeferredReviewPrompt


class PurposeInterpretation(Interpretation):
    check_purpose: CheckPurpose


class CorrectnessCritique(Critique):
    model_config = ConfigDict(extra="forbid", json_schema_extra=None)
    interpretations: list[PurposeInterpretation]
    correction_dispositions: list[CorrectionDisposition]


class CorrectnessInterpretationReview(InterpretationReview, PurposeReview):
    counterexample_matches_check: StrictBool | None
    control_excludes_counterexample: StrictBool | None
    check_purpose: CheckPurpose


class CoverageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=1)
    assessment: str = Field(min_length=1)
    supports_critic: StrictBool
    draft_references: list[str]


class CorrectnessAssessment(IssueAssessment):
    model_config = ConfigDict(extra="forbid", json_schema_extra=None)
    interpretation_reviews: list[CorrectnessInterpretationReview]
    issue_reviews: list[CoverageReview]
    requirement_reviews: list[CoverageReview]
    disposition_reviews: list[CoverageReview]


class CorrectnessRunner:
    """Adapt internal schemas and preserve complete experimental correction evidence."""

    def __init__(self, runner, requirements, markdown=None):
        self.snapshot = ReviewSnapshot(markdown) if markdown is not None else None
        self.runner = runner
        self.requirements = requirements
        self.records = []
        self.candidate = None
        self.previous = []
        self.assessments = []

    def structured(self, role, prompt, schema, tools):
        if schema not in (Critique, IssueAssessment):
            return self.runner.structured(role, prompt, schema, tools)
        if schema is Critique:
            if self.candidate is not None:
                self.previous = [
                    {"reference": f"previous:{i}", "issue": issue} for i, issue in enumerate(self.candidate["issues"])
                ]
            suffix = (
                GUIDANCE + "\nAccount for every previous issue as retained, revised or withdrawn. "
                "Explain each disposition; reference current replacement issue IDs. Do not drop valid "
                "findings while adding omissions. Return empty dispositions on the first call.\n"
                + json.dumps({"previous_issues": self.previous, "complete_previous_critic": self.candidate})
            )
            output_schema = CorrectnessCritique
        else:
            suffix = (
                GUIDANCE + "\nIndependently classify each interpretation's purpose. Evaluate every "
                "issue and requirement exactly once in issue_reviews/requirement_reviews using their "
                "IDs as reference. supports_critic judges the accuracy of the critic, not document approval. "
                "A missing requirement correctly rejected supports the critic. Assess whether every "
                "correction disposition is justified, including every revision and withdrawal. Return one "
                "disposition_reviews row per previous issue reference. Disputed withdrawals cite current "
                "draft or requirement references; previous issue IDs are not current issue IDs. Purpose "
                "disagreement or unjustified withdrawal requires referenced disagreement findings. "
                "Legacy rejected responses may omit purpose and normalize null to false. Raw previous "
                "outputs preserve those fields. Correct a quotation/reference error without inventing "
                "a new semantic objection merely to change the verdict.\n"
                + json.dumps(
                    {
                        "complete_critic": self.candidate,
                        "previous_issues": self.previous,
                        "raw_previous_assessments": self.assessments,
                    }
                )
            )
            output_schema = CorrectnessAssessment
        if isinstance(prompt, DeferredReviewPrompt):
            prompt = DeferredReviewPrompt(prompt.investigation + "\n" + GUIDANCE, prompt.comparison + "\n" + suffix)
        else:
            prompt += "\n" + suffix
        self.records.append({"role": role, "prompt": str(prompt), "schema": output_schema.model_json_schema()})
        output = self.runner.structured(role, prompt, output_schema, tools)
        data = output.model_dump()
        self.records.append({"role": role, "output": deepcopy(data)})
        try:
            if schema is Critique:
                self.candidate = deepcopy(data)
                validate_dispositions(
                    [r["reference"] for r in self.previous],
                    [r.id for r in output.issues],
                    output.correction_dispositions,
                )
                data.pop("correction_dispositions")
                for row in data["interpretations"]:
                    row.pop("check_purpose")
            else:
                self.assessments.append(deepcopy(data))
                self._validate_assessment(output)
                data.pop("issue_reviews")
                data.pop("requirement_reviews")
                data.pop("disposition_reviews")
                for row in data["interpretation_reviews"]:
                    row.pop("check_purpose")
                    # Applicability has been validated; legacy guards receive no counterexample.
                    for key in ("counterexample_matches_check", "control_excludes_counterexample"):
                        if row[key] is None:
                            row[key] = False
            return schema.model_validate(data)
        except ValueError as error:
            error.rejected_response = output.model_dump_json()
            raise

    def _validate_assessment(self, output):
        for reviews, expected in (
            (output.issue_reviews, [i["id"] for i in self.candidate["issues"]]),
            (output.requirement_reviews, [r.id for r in self.requirements]),
            (output.disposition_reviews, [r["reference"] for r in self.previous]),
        ):
            if sorted(r.reference for r in reviews) != sorted(expected):
                raise ValueError("Review every issue and requirement exactly once")
        if output.critic_supported and any(
            not r.supports_critic
            for r in output.issue_reviews + output.requirement_reviews + output.disposition_reviews
        ):
            raise ValueError("Unsupported coverage judgment requires disagreement findings")
        if self.snapshot is not None:
            for review in output.issue_reviews + output.requirement_reviews + output.disposition_reviews:
                if not set(review.draft_references) <= self.snapshot.values.keys():
                    raise ValueError("Coverage review contains unknown draft references")
        for row in output.interpretation_reviews:
            if row.interpretation_index >= len(self.candidate["interpretations"]):
                raise ValueError("Unknown interpretation index")
            purpose = self.candidate["interpretations"][row.interpretation_index]["check_purpose"]
            if purpose != row.check_purpose and output.critic_supported:
                raise ValueError("Purpose disagreement requires disagreement findings")
