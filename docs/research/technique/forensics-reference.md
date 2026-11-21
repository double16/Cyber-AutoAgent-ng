# Technique forensics reference

This is a research-side reference for detailed technique lifecycle fields and study records. The
[forensics skill](../../../.agents/skills/forensics/SKILL.md) contains the current interpretation
rules used during ordinary operation reviews. This page points to current contracts and preserves
diagnostic detail outside the general-purpose skill.

## Current operation interpretation

A technique recommendation is a lead for a worker to consider a lookup. It is not proof that the
guidance was retrieved, applicable or used. Evaluate a recorded use against the exact delivered
document and the task's observed behavior. A score of 1 means helpful contribution was identified,
0 means contribution was not established, and -1 means misleading or harmful effects were identified.
Missing target proof or a terminal disposition alone does not prove non-contribution.

Use `technique_uses.use_context` to establish the recorded delivery context and, when present,
the delivered document snapshot. Do not reconstruct a missing historical snapshot from a later
revision. Current feedback evidence includes the frozen task tool outcomes and referenced text;
availability establishes access only, not semantic contribution. An evidence access gap leaves the
decision pending rather than making it neutral. For phase-specific interpretation, use the phase of
the reviewed task, not the operation's currently active phase.

## Current generation and publication

Production publication is a single-critic path. The critic returns a summary, material issues and
optional suggestions; controller code assigns issue and requirement references, resolves inspected
source passages and derives publication status. A contract or reference error is a review failure,
not a semantic defect. Exhausted review retries block publication without spending another author
attempt. Re-review follows any content repair and applies to the exact revised document.

New requirements distinguish `knowledge_allowed` from `source_required`, independently of
requirement kind. General methodology can use sound reasoning and future checks, while explicit
versions, quotations, vendor claims and similar obligations require source verification. Documents
still need useful retrieved reference material. Retrieval, authority and cited-text availability do
not independently prove support or correctness.

When reviewing a generated bundle, preserve component boundaries. Partial delivery is not full
request coverage; a sibling component's omission does not automatically invalidate a component.
Separate missing factual support, missing requested content, authored-procedure defects and unknown
target prerequisites. Unknown target conditions belong in future validation unless they undermine
the method's stated assumptions.

## Forensic record map

Use these records to follow the current pipeline without relying on legacy two-review events:

| Question | Evidence to inspect |
| --- | --- |
| What technique policy applied? | `technique_policy` for module and restrictions; `technique_source_preferences` for preferred sources. |
| Was a technique recommended or retrieved? | `technique_recommended` is a lookup lead; correlate task and trigger with `get_technique` tool outcomes. Neither proves contribution. |
| Did publication complete? | `technique_build_progress` with `stage=critique` and `status=review_completed` records the critic review; `invalid_review` marks contract/reference failure; `stage=publication_review` with `status=unavailable` means publication was blocked by review unavailability. |
| What sources and requirements were reviewed? | Inspect current requirement grounding, selected source URLs, authority/support assessments and retained inspected passages. See the knowledge-grounding record. |
| Did guidance help the task? | `technique_evaluated` records `score`, `reason`, revision and task; compare it with the exact delivery snapshot and task-local tool evidence. |
| Is a feedback decision unresolved? | Inspect its evidence gaps and access diagnostics. Missing evidence keeps the decision pending; it is not neutral sentiment. |
| How long did role work take? | `technique_model_call.role_calls` counts role invocations. Provider attempts require the corresponding CLI JSONL records; these counts are not interchangeable. |

The `technique_uses` receipt joins operation, task and revision. Its `use_context` records delivery
purpose, phase, applicability and observation provenance; new receipts also include
`delivered_documents`. Missing delivery snapshots remain unknown. Current single-review publication
uses no critique-assessor event or agreement field.

For a semantic publication rejection, identify the material issue and the exact obligation, draft
content or inspected source that it concerns. A contract failure is not a rejection. Current
publication retries once on invalid output; exhaustion blocks publication and does not consume an
author attempt. After an author repair, review the exact revised document. Detailed schemas and
correction ownership are in the single-reviewer and grounding records below.

For exact lifecycle event names, schemas, role inputs, correction ownership and retry behavior, see
the focused current records:

- [Single-reviewer publication](../technique-single-reviewer/README.md)
- [Knowledge-based grounding](../technique-knowledge-grounding/README.md)
- [Purpose-aware review](../technique-review-purpose/README.md)
- [Materiality and repair precision](../technique-review-materiality/README.md)
- [Incremental review corrections](../technique-review-incremental-corrections/README.md)
- [Nested correction references](../technique-review-nested-corrections/README.md)
- [Support-scope generation follow-up](../technique-generation-yield-pilot/upstream-scope-followup/README.md)

## Calibration and study interpretation

Calibration and generation-yield records are experiments, not operation evidence. Read each study's
frozen protocol, exact subject identity, adjudication, outcome and limitations together. A completed
model call, passing scripted transport check or label match alone does not establish semantic
quality. Preserve incomplete outcomes and distinguish human acceptance of an exact draft from
pipeline completion or production reliability.

- [Review calibration](../technique-review-calibration/README.md)
- [Generation-yield pilot](../technique-generation-yield-pilot/README.md)
- [Earlier technique studies and reports](../README.md)

Raw operation context and provider payloads remain in private operation-local storage. The research
records retain shareable protocols, source snapshots, hashes and adjudications; do not copy sensitive
raw prompts into these documents.
