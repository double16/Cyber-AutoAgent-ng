"""Reference validation for semantic, requirement-level technique reviews."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Passage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passage_id: int = Field(description="Use the exact one-based ID of the passage containing the quote.")
    quote: str = Field(
        min_length=1,
        description="A short exact contiguous substring of that passage. Preserve punctuation and whitespace; "
                    "do not paraphrase, add ellipses or quote across passage boundaries.",
    )


class RequirementJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    verdict: Literal["covered", "conditional", "unmet"]
    passages: list[Passage] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    missing_capability: str = ""


class SelectionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1)
    judgments: list[RequirementJudgment] = Field(min_length=1)

    def validate_references(self, requirements, passages):
        expected = {row["id"] for row in requirements}
        ids = [row.requirement_id for row in self.judgments]
        if len(ids) != len(expected) or set(ids) != expected:
            raise ValueError("Review every requested requirement exactly once")
        for row in self.judgments:
            if row.verdict == "unmet" and not row.missing_capability.strip():
                raise ValueError("Unmet requirements need a missing requested capability")
            for citation in row.passages:
                if citation.passage_id not in passages:
                    raise ValueError("Review passage must quote the submitted revision")
                if citation.quote not in passages[citation.passage_id]:
                    matches = [key for key, text in passages.items() if citation.quote in text]
                    if len(matches) != 1:
                        raise ValueError("Review passage must quote the submitted revision")
                    citation.passage_id = matches[0]

    @property
    def reusable(self):
        return all(row.verdict != "unmet" for row in self.judgments)


def document_passages(markdown):
    """Number bounded passages without dropping characters, even for single-line documents."""
    return {index + 1: markdown[start:start + 1600]
            for index, start in enumerate(range(0, len(markdown), 1600))}
