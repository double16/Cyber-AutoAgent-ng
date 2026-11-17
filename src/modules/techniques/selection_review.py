"""Reference validation for semantic, requirement-level technique reviews."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Passage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passage_id: int = Field(description="Use the exact one-based ID of the passage in the submitted revision.")
    quote: str = Field(
        default="",
        description="Optional legacy quote. New reviews should return passage_id only. If supplied, preserve whitespace; "
                    "do not paraphrase, add ellipses or quote across passage boundaries.",
    )


class RequirementJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    verdict: Literal["covered", "conditional", "unmet"]
    passages: list[Passage] = Field(default_factory=list)
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
            if row.verdict != "unmet" and not row.passages:
                raise ValueError("Covered and conditional judgments require a passage reference")
            for citation in row.passages:
                if citation.passage_id not in passages:
                    raise ValueError("Review passage must quote the submitted revision")
                if citation.quote and citation.quote not in passages[citation.passage_id]:
                    matches = [key for key, text in passages.items() if citation.quote in text]
                    if len(matches) != 1:
                        raise ValueError("Review passage must quote the submitted revision")
                    citation.passage_id = matches[0]

    @property
    def reusable(self):
        return all(row.verdict != "unmet" for row in self.judgments)


def document_passages(markdown):
    """Number bounded passages without dropping characters, even for single-line documents."""
    passages = {}
    start = 0
    while start < len(markdown):
        end = min(start + 1600, len(markdown))
        boundary = markdown.rfind("\n", start + 800, end)
        if end < len(markdown) and boundary >= 0:
            end = boundary + 1
        passages[len(passages) + 1] = markdown[start:end]
        start = end
    return passages
