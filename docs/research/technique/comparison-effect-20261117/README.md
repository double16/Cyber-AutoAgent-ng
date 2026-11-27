# Execution interpretation and evidence-view experiments

The previous full-controller case produced a false authorization violation despite retrieving the
intended guide. These exploratory experiments test input organization and output meaning without
adding critics or stronger semantic prohibition lists. No experimental renderer was promoted to
production.

## Static diagnostic results

Every format received the same identity bindings, permission policy, own-account baselines and
cross-account results. Cases cover permitted sharing, actual unauthorized access, and unavailable
policy. Response error labels are preserved throughout.

| Format | Correct classification | Fully supported fixture result |
|---|---:|---:|
| Flat observations | 2/3 | 2/3 |
| Joined comparison rows | 2/3 | 0/3 |
| Separate response labels and returned objects | 3/3 | 1/3 |
| Separate effects with explicit source lists | 2/3 | 0/3 |
| Bound comparison IDs with Python source expansion | 2/3 | 0/3 |
| Narrow data-access labels, flat input | 2/3 | 1/3 |
| Narrow data-access labels, separate effects | 3/3 | 0/3 |
| Workflow evaluator role, flat input | 2/3 | 0/3 |
| Workflow evaluator role, separate effects | 1/3 | 0/3 |

There were 27 structured role invocations. Transport-level generation counts were not instrumented.
Each cell is one case/format decision, with no repetitions or statistical efficacy claim.
The first seven formats use the technique-specialist runner. The last two use the workflow
evaluator's role boundary and native structured transport, but not a complete assembled task
evaluation. Role framing and incomplete workflow context limit extrapolation to production.

Separating actual returned objects from response labels helped in some decisions, but small format
changes reversed the result. Narrower output labels fixed permitted sharing in flat input while
introducing a false negative for vulnerable access. Source-list and bound-ID variants did not
reliably select the evidence needed for the claim. Consequently none is ready for production adoption.

“Fully supported” retains the existing fixture's prespecified requirements: valid references, identity
and policy citations, and a cited cross-account probe. Some inconclusive decisions correctly cited
policy unavailability without a cross-account citation; those still fail this stricter fixture metric.
Classification accuracy and citation completeness are reported separately rather than changing the
scorer after seeing results. Canonical comparison IDs expand only through declared source links;
Python never computes the security verdict.

[results.json](results.json) preserves decisions, explanations and scoring. Full prompts and raw
results remain under outputs/technique_review_trials/comparison-effect-20261117. Run.py supports
the default flat/joined comparison, --effects, --provenance, --bound, --narrow and --execution-role.
Every invocation requires a fresh output directory; model calls use the existing provider setup.

## Concrete harness defect

The full-controller harness advertised acceptance-repair paths that can require read_artifact,
but explicitly replaced executor core tools with fixture tools and omitted that capability.
The failed prior case made 18 tool_catalog calls. That omission can confound completion and cost
measurements; it does not explain away the independently observed wrong classification.

The harness now includes the real operation-scoped artifact reader alongside read_fixture.
Tests verify reading the assigned operation file and rejecting an outside path. Generation remains
mocked in offline tests; no external target I/O is available.

The corrected full-controller run used a fresh cloned catalog and the same guide and classification
contract, changing reader availability only. It completed in **38 generation requests**, with a supported,
frozen accepted no_violation_observed result and helpful (+1) feedback. The executor used read_artifact.
It made four tool_catalog calls versus 18 in the failed previous run. This single run does not establish
a causal or general improvement; the earlier successful pilot also completed in 36 requests.
See [live-result.json](live-result.json).

## Controller follow-up

The live trace exposed another contradiction: invalid memory:obs-* citations triggered evidence creation
despite a usable artifact satisfying the task's artifact-only requirement. The resulting read-only turn
still advertised record_task_acceptance as required, sending the actor back to tool discovery.

The controller now distinguishes optional invalid citations from actual unmet frozen evidence requirements.
Available alternatives must cover the declared kinds and minimum counts. Correction inputs show those
available references without rejected candidates; they do not automatically accept the artifact or its
conclusions. Explicit memory/observation requirements, unreadable artifacts and insufficient artifact counts
still require evidence creation. Those turns now request evidence and a summary, leaving acceptance for
the subsequent appropriately tooled turn.

Seventeen focused checks cover the routing and assembled continuation inputs, including HTTP, network and
filesystem contexts, unavailable alternatives, minimum counts and explicit memory/observation requirements.
The successful live run started before this routing change; its behavior is validated separately by tests.
The final repository coverage run passed **8,227 tests**, with all measured Python files meeting the
80% statement and branch floors. The research/harness suite passed 24 tests; Ruff and whitespace checks
passed. Source hashes and coverage are recorded in [validation.json](validation.json). No experimental renderer
or narrower verdict label was promoted. The running target operation was unchanged.
