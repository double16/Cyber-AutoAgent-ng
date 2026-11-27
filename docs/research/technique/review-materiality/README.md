# Narrow material review and concrete repairs

Human adjudication accepted the generated network draft and rejected the last filesystem draft
because it obscured that the method concerned reading files. The preceding run completed no
pipeline documents, despite one human-useful authored draft. This follow-up tests reviewer precision
on those exact documents rather than immediately spending another generation allowance.

The three frozen [cases](cases.json) are:

| Case | Exact document label | Expected decision |
| --- | --- | --- |
| Human-reviewed network | acceptable and useful | Approve |
| Human-reviewed filesystem, last draft | needs correction | Reject with an actionable subject/operation correction |
| Original HTTP selector | needs correction | Reject the unconditional IDOR interpretation |

The input requests and retained source pools are unchanged. Labels are carried from the human
adjudications, not inferred from model outcomes. Network/filesystem human judgments concern the
exact generated documents; they do not independently establish every disputed factual assertion.
Do not rewrite those labels or substitute a repaired draft in response to a model disagreement.

## Revision

Shared guidance narrows blockers to concrete material errors, unsupported material assertions and
missing requested deliverables. Optional elaboration is advisory. A missing retrieval date alone
is advisory unless an actual obligation or version-dependent claim makes it material; unavailable
provenance must never be invented. Explicit user constraints remain authoritative.

Future procedural imperatives are allowed: describing how to test is distinct from performing a
target test now. Author repairs preserve concrete objects, identities and operations, use the
smallest necessary change, and avoid turning file reads or connection tests into abstract prose.
Presentation preferences remain author-only; no named tool or literal file phrase is enforced
by Python.

Counterexample analysis is required for actual boundary-violation claims. For prerequisite or
legitimate-behavior checks, the assessor explains that no violation is claimed, uses false for the
two counterexample booleans and does not invent a boundary discriminator. Actual misleading
violation labels still require defects. All interpretations and decisions still receive independent
review, with the existing shared correction allowance. No field or extra model role was added.

The [revision patch](revision.patch) preserves changes relative to the prior frozen runtime, whose
preimage hash was verified. Both investigation and finalization instructions were inspected for
unconditional counterexample demands and obsolete exact-excerpt wording. Initial/repair author
prompts retain the concrete-subject guidance. Source, request, critique, repair and prompt-fitting
regressions remain in the affected suite.

## Prepared trial

[Protocol](protocol.json) proposes 21 sequential requests for the three static reviews, including
all retained-source lookups and corrections. It is not authorized for live execution yet. The
prior 147 live calls and failed trials remain preserved. No generation or module publication is
included. A successful review result would establish bounded label agreement, not generation yield.

Scripted preflight 1 was prepared before the final investigation wording clarification; its raw
record is retained under outputs. Preflight 2 checks the final runtime through both output modes.
Scripted decisions have no semantic standing and must not be counted as valid defect judgments.
The coordinator lives under `outputs/technique_review_trials/review-materiality-20261106-2/run.py`;
its default mode is scripted and `--run` requires a separately authorized protocol.

## Live calibration started

The user authorized up to 21 sequential requests. The fixed three-case review calibration is
running under `outputs/technique_review_trials/review-materiality-20261106-2`; no generation
is included. Final decisions are pending. Earlier 147 live requests remain separate.

## Live outcome

[All labels matched](results.md), but reasoning acceptance remains incomplete: the filesystem
rejection missed the user's clarity concern, and some network counterexample rows restate
legitimate behavior. The run used 15 of 21 requests with no corrections. Cumulative live calls
are 162; none are running. No generation improvement is claimed.
