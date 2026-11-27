"""Isolated critique experiment contracts; never select production behavior implicitly."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class Variant(StrEnum):
    """Predeclared factorial comparison arms."""

    A = "boolean_current"
    B = "rated_current"
    C = "boolean_focused"
    D = "rated_focused"


RATING_RUBRIC = """Rate readiness or support using these anchored values:
1: fundamentally incorrect or unsupported.
2: material correction required; materially incorrect or incomplete.
3: material uncertainty remains; available evidence cannot resolve the judgment.
4: acceptable or supported, with only nonblocking concerns.
5: complete and fully supported.
Do not average procedures or compensate for a defective procedure with other strengths.
A rating of 3 does not require inventing a defect. Explain what remains unresolved.
"""


class RatedJudgment(BaseModel):
    """Internal experimental verdict; semantic judgments remain model-owned."""

    model_config = ConfigDict(extra="forbid")
    score: StrictInt = Field(ge=1, le=5)
    reason: str = Field(min_length=1)
    blocking_findings: list[str]
    references_valid: StrictBool

    @property
    def accepted(self) -> bool:
        """A high rating cannot override an unresolved blocker or invalid provenance."""
        return self.score >= 4 and not self.blocking_findings and self.references_valid


class TrialMetrics(BaseModel):
    """Human-adjudicated errors, ordered before transport convenience."""

    model_config = ConfigDict(extra="forbid")
    false_approvals: StrictInt = Field(ge=0)
    false_rejections: StrictInt = Field(ge=0)
    reasoning_errors: StrictInt = Field(ge=0)
    contract_failures: StrictInt = Field(ge=0)
    exhausted_corrections: StrictInt = Field(ge=0)

    def ranking(self) -> tuple[int, ...]:
        return (self.false_approvals, self.false_rejections, self.reasoning_errors,
                self.contract_failures, self.exhausted_corrections)


def select_candidate(results: dict[Variant, TrialMetrics], *, challenger_only: bool = False) -> Variant:
    """Rank completed screening arms; stable ties prefer fewer behavioral changes."""
    if set(results) != set(Variant):
        raise ValueError("Screen all four variants before selecting a candidate")
    eligible = [arm for arm in Variant if not challenger_only or arm is not Variant.A]
    return min(eligible, key=lambda arm: (results[arm].ranking(), list(Variant).index(arm)))
