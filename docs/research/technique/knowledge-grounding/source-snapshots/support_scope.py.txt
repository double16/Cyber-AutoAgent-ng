"""Shared policy for references, model knowledge and required verification."""

SUPPORT_SCOPE_GUIDANCE = (
    "Model knowledge, reasoning and original methodology are valid content bases. "
    "Retrieved references need not support every factual statement or procedural step. "
    "Missing citations alone are not defects. Block concrete technical errors, misleading conclusions, "
    "materially inaccurate attribution or missing requested deliverables. "
    "Verify retrieved support only for source_required obligations and claims explicitly attributed "
    "to a source. References may contribute background without supporting the whole procedure. "
    "Missing retrieval dates alone are advisory unless an explicit requirement makes them material. "
    "Accurate attribution in metadata counts; body placement requires an explicit obligation. "
    "Do not execute now does not forbid describing future testing unless it explicitly forbids describing execution steps. "
    "Optional detail and presentation elaboration belong in suggestions. "
    "Future verification steps and original controls are permitted. Request the smallest necessary correction; "
    "uncertainty belongs in qualifications or future checks unless it defeats the central method. "
)
