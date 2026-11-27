# Prose evidence-reference recovery acceptance

> Historical experiment record: contracts, counts and outcomes below describe the frozen trial,
> not necessarily the current runtime. See [Techniques](../../techniques.md) for current behavior.
> Earlier temporary artifact paths are provenance records; availability is not guaranteed.

Python recovers complete known IDs from evaluator prose as an unpaired inventory with field
origins and exact evidence excerpts. It does not infer mappings or usefulness. Acceptance requires
valid reviewer mappings drawn from that inventory, matching scores and no material errors.
Raw evaluator findings remain recorded separately from reviewer-confirmed normalized mappings.
Unknown, shortened and stale IDs require correction. Invalid authored mappings are never replaced,
and reviewer omissions are not recovered. Output schemas and the single correction limit are unchanged.

## Automated verification

The technique suite passed 963 tests, with one skipped. After adding the final outside-inventory
regression, the policy/feedback subset passed 84 tests. Coverage for feedback.py was 98.7% line and
93.3% branch; the repository coverage-floor checker passed. Ruff and whitespace checks passed.
Tests cover exact tokens, punctuation, duplicates, partial and unknown IDs, reviewer confirmation,
missing reviewer mappings, references outside the inventory and pending feedback on exhaustion.
Existing HTTP, network and filesystem scoring and evidence-preservation regressions remain passing.

Reviewed assembled initial and correction inputs, complete retained evidence, reviewer inventory
instructions, unchanged native/tool output schemas and fail-closed prompt fitting. No conditional
schema constraints, additional roles, historical-score changes or SQL changes were introduced.

## Live results

The six frozen controls used muse-glimmer:30b sequentially with isolated storage:

| Control | Result |
| --- | --- |
| HTTP application | Helpful (+1), after one evaluator correction |
| Network exclusion | Pending after correction; mappings and complete prose IDs absent |
| Filesystem application | Pending after correction; mappings and complete prose IDs absent |
| Citation only | Neutral (0) |
| Method mismatch | Neutral (0) |
| Mixed benefit and harm | Harmful (-1) |

This is four supported results and two exhausted decisions, versus two supported results and four
exhausted decisions in the original unchanged-schema run. It is a small control set, not evidence
of a general production success rate. The six controls used authored mappings for accepted results;
they did not spontaneously exercise successful prose recovery.

A seventh controlled case froze evaluator findings containing complete known IDs in prose with an
empty mapping list. The live independent reviewer produced a supported helpful assessment with
valid mappings. Python adopted these as reviewer-confirmed mappings without correction. Diagnostics
confirm the raw evaluator mapping list remained empty and the recovered inventory remained unpaired.
The frozen evaluator is a test fixture, not a live evaluator success.

Overall production acceptance remains **unresolved** because two frozen controls exhausted correction.
No disputed scores were persisted. The active operation and historical records were not modified.
No prompts were tuned repeatedly or extra semantic retries granted during acceptance.

## Artifacts and limits

Counts: {'provider_inputs': 14, 'model_role_invocations': 14, 'controller_role_calls': 15}. The shared maximum was 28 role invocations including corrections; one
controller role call was the frozen evaluator, not a provider generation.

- `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/prose-recovery-acceptance-9a9du1q1`
- `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/controlled-prose-recovery-vxqza4qy`

## Prompt-clarity follow-up: candidate not retained

A subsequent bounded experiment made missing-mapping corrections explicitly name the three mapping
fields, demand complete reference prefixes, and prohibit abbreviated/prose-only references. It also
added a pre-submission mapping check to the shared rubric. The schema, scoring, reference recovery
and correction allowance were unchanged. No pairing was supplied by Python.

The candidate passed 965 technique tests, Ruff and the coverage floor, but live acceptance failed:
only citation-only and method-mismatch controls passed. HTTP application and mixed harm regressed
from their prior passing results. Network and filesystem controls still exhausted correction. A new
filesystem ownership/exclusion control also exhausted correction, this time in reviewer mapping
validation. Thus the candidate had two passes out of six original controls and failed the fresh
control. This small experiment establishes a failed acceptance gate, not a statistical estimate of
model performance or proof of the exact internal cause.

The wording changes and their candidate-only regression test were removed after preserving the
candidate source and complete diagnostics. The prior prose-recovery implementation and prompt were
restored. No additional semantic retries or further tuning were performed during this round.
The remaining omissions are not fixed; production acceptance remains unresolved.

The round used 16 role invocations and 16 provider inputs, sequentially,
below the 28-invocation cap. Candidate source, harness, frozen controls and diagnostics: `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/mapping-correction-acceptance-c5x6h6ch`.

After restoration, the technique suite passed **964 tests**, with one skipped. Ruff, whitespace
checks and the repository per-file coverage-floor check passed. The failed wording candidate is
archived and is not the current prompt.
