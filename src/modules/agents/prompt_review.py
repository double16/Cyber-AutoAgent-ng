"""Validate task prompt review provenance without interpreting semantic judgments."""

from collections.abc import Collection
from typing import Any

from modules.agents.structured_outputs import TaskPromptChecklistOutput, TaskPromptCritiqueOutput

CHECK_CATEGORIES = {
    "task_coverage": {"objective"},
    "scope": {"scope"},
    "execution_policy": {"execution_policy"},
    "evidence": {"evidence"},
    "capability_fit": {"tool_availability", "execution_policy", "objective"},
}


def normalize_task_prompt_checklist(
    data: dict[str, Any], *, requirement_refs: Collection[str] | None = None, draft: str | None = None,
) -> dict[str, Any]:
    """Require all five decisions and correlate cited requirements and verbatim draft excerpts."""
    checklist = TaskPromptChecklistOutput.model_validate(data)
    issues = []
    for name, categories in CHECK_CATEGORIES.items():
        check = getattr(checklist, name)
        if check.issue is None:
            continue
        issue = check.issue
        if issue.category not in categories:
            raise ValueError(f"Check {name} requires a matching issue category")
        if not issue.message.strip():
            raise ValueError("Failed checks require a concrete defect message")
        if requirement_refs is not None and issue.requirement_ref not in requirement_refs:
            raise ValueError(f"Unknown or non-authoritative requirement reference: {issue.requirement_ref}")
        if draft is not None and issue.draft_excerpt and issue.draft_excerpt not in draft:
            raise ValueError("draft_excerpt must be copied verbatim from the proposed draft")
        if issue.category == "tool_availability" and not issue.tool_names:
            raise ValueError("tool_availability issues require tool_names")
        issues.append(issue.model_dump(exclude={"requirement_ref", "draft_excerpt"}))
    return TaskPromptCritiqueOutput(issues=issues, repairable=checklist.repairable).model_dump()
