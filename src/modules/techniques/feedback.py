"""Evidence findings determine technique usefulness independently of task completion."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class ContributionFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methodological_purpose: str = Field(min_length=1, max_length=2000)
    observed_use: str = Field(max_length=4000)
    helpful_contribution: StrictBool
    contribution: str = Field(max_length=4000)
    misleading_or_harmful: StrictBool
    harmful_effect: str = Field(max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.helpful_contribution != bool(self.contribution.strip()):
            raise ValueError("Helpful contribution must agree with contribution evidence")
        if self.misleading_or_harmful != bool(self.harmful_effect.strip()):
            raise ValueError("Misleading or harmful finding must agree with effect evidence")
        if (self.helpful_contribution or self.misleading_or_harmful) and not self.observed_use.strip():
            raise ValueError("A demonstrated effect requires observed use")
        return self

    @property
    def score(self) -> int:
        if self.misleading_or_harmful:
            return -1
        return 1 if self.helpful_contribution else 0
