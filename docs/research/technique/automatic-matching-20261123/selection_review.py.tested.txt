"""Reference validation for semantic, requirement-level technique reviews."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


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


class TaskRelevance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serves_task: bool
    reason: str = Field(min_length=1)


class SelectionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1)
    judgments: list[RequirementJudgment] = Field(min_length=1)
    task_relevance: TaskRelevance | None = None

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
        return (all(row.verdict != "unmet" for row in self.judgments)
                and (self.task_relevance is None or self.task_relevance.serves_task))


def selection_output_model(requirements, *, task_local=False):
    """Bind new generation to supplied IDs and verdict-specific bookkeeping; keep stored reviews compatible."""
    identifiers = tuple(row["id"] for row in requirements)
    if (not identifiers or any(not isinstance(value, str) or not value.strip() for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError("Selection requires nonempty, unique requirement IDs")
    identifier_type = Literal[identifiers]
    supported = create_model(
        "SupportedRequirementJudgment", __base__=RequirementJudgment,
        requirement_id=(identifier_type, ...),
        verdict=(Literal["covered", "conditional"], ...),
        passages=(list[Passage], Field(min_length=1)),
    )
    unmet = create_model(
        "UnmetRequirementJudgment", __base__=RequirementJudgment,
        requirement_id=(identifier_type, ...),
        verdict=(Literal["unmet"], ...),
        missing_capability=(str, Field(
            min_length=1, pattern=r"[\s\S]*\S[\s\S]*",
            description="Name the requested procedure or control that the document lacks.",
        )),
    )
    extra = {"task_relevance": (TaskRelevance, ...)} if task_local else {}
    return create_model(
        "BoundSelectionReview", __base__=SelectionReview,
        judgments=(list[supported | unmet], Field(min_length=len(identifiers), max_length=len(identifiers))),
        **extra,
    )


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
