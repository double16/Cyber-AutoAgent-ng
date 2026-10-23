"""Role-specific semantic review boundaries and schema-derived answer capacity."""

from typing import Any

from modules.handlers.conversation_budget import estimate_prompt_tokens

REVIEW_CHECKS = {
    "plan_critic": "phase coverage; dependencies; measurable criteria; controller ownership",
    "task_prompt_critic": "the five required task prompt checks",
    "authentication_flow_critic": "observed flow fields; ordered steps; evidence support; assigned scope",
    "registration_flow_critic": "observed registration controls; ordered transitions; evidence support; scope",
    "task_evaluator": "frozen acceptance; semantic evidence support; unresolved gaps; permitted disposition",
    "phase_evaluator": "effective task states; remaining executable work; permitted terminal conditions",
    "report_critic": "section requirements; factual grounding; unsupported claims; material omissions",
}
REVIEW_ROLES = frozenset(REVIEW_CHECKS)


def required_review_answer_tokens(schema: dict[str, Any], model_id: str) -> int:
    """Reserve space for schema-required records, never infer output counts from input inventories."""
    definitions = schema.get("$defs", {})

    def size(node: dict[str, Any], seen: frozenset[str] = frozenset()) -> int:
        ref = node.get("$ref")
        if ref:
            if ref in seen:
                return 0
            return size(definitions.get(ref.rsplit("/", 1)[-1], {}), seen | {ref})
        variants = node.get("anyOf", node.get("oneOf"))
        if variants:
            return max(size(option, seen) for option in variants)
        if node.get("type") == "array":
            count = min(int(node.get("minItems", 0)), 32769)
            item = node.get("items", {})
            # A required record needs semantic reasoning, not just its JSON punctuation.
            return count * (size(item, seen) + 256)
        if node.get("type") == "object":
            properties = node.get("properties", {})
            return sum(
                estimate_prompt_tokens(model_id, extra_content=name) + size(properties.get(name, {}), seen)
                for name in node.get("required", [])
            )
        literal = node.get("const", node.get("enum", [""])[0])
        return estimate_prompt_tokens(model_id, extra_content=str(literal)) + 8

    return size(schema)
