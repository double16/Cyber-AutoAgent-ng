# Whole-request reuse results

All sixteen frozen trials completed using 113 of the 160 allowed generation requests. No completed
trial was rerun. Each arm used independent copies of the same catalog. Raw outcomes, role events and
rendered bundles are under `outputs/technique_review_trials/reuse-first-20261124/`.

| Request (two repetitions) | Baseline requests | Candidate requests | Baseline components | Candidate components |
| --- | ---: | ---: | ---: | ---: |
| Recorded path-query methodology | 15 | 9 | 2 | 2 |
| JWT algorithm and claim tests | 14 | 8 | 3 | 2 |
| TCP object authorization | 15 | 13 | 2 | 2 |
| Export-root containment | 31 | 8 | 6 | 2 |
| Total | 75 | 38 | 13 | 8 |

The candidate used 49.3% fewer generation requests. Every candidate trial delivered one document,
with no unresolved component and no planner invocation. Both arms selected the same distinct
revision for each case. Baseline repetition sometimes split one methodology into several components
that ultimately reused the same revision. The recorded thirteen-component production expansion did
not reproduce in this small replay; the largest baseline expansion here was five components.

## Content assessment

The path guide supplies allowed, nonexistent and approved outside-boundary marker comparisons,
separates read from interpreted inclusion, and preserves unknown policy and fixture prerequisites.
The JWT guide supplies separate integrity/unsecured-token, algorithm/key-confusion and claim-policy
branches. It distinguishes tampered claims from legitimately signed wrong-policy tokens, with
accepted baselines, negative controls and protected effects. One document can therefore support
several future hypotheses without giving each procedure a new structured identity.

The object-authorization guide supports protocol adaptation, two identities, known objects,
entitlement policy, positive/nonexistent controls and protected-property comparisons. Ownership
alone does not establish a prohibited comparison.

The export-root case reused the path guide's containment comparisons, but that guide explicitly
requires application/network access and prohibits local filesystem inspection. The request did not
specify a local inspection requirement. This is useful conditional guidance, not evidence that the
same selection would satisfy a source-only or local-filesystem execution request. Both arms shared
this limitation. Representative contract tests cover filesystem requests; this replay alone does
not establish general filesystem suitability.

## Assembled instruction review

Reviewed the alignment and abstraction path, root-request preservation, full-document selection,
bound native/schema-tool output, repair input retention, prompt fitting, cached reviews and planner
fallback. The fast path uses the existing compatibility and selection instructions; it introduces no
new model role or prompt. Selection receives the full aligned methodology request and original
request origin. It judges requested capabilities against complete numbered document passages;
unknown prerequisites are conditional and missing essential capabilities are unmet. Python checks
references, lifecycle, generation version and phase before delivery.

Selection repair retains the authoritative document; overflow fails explicitly. Cache fingerprints
include the assembled request and schema, with lifecycle checked again after reuse. The fallback
retains its independent advisory plan review and author/editor behavior. Continuation uses the same
service and receipt path. Success bypasses planning, research and authorship; a genuine miss can
cost extra preparation work before fallback. Provider/schema failures remain unresolved failures,
not catalog misses. No public schema, database migration or document-version bump was introduced.

## Interpretation

This supports the fast path as a simpler, cheaper way to deliver already available methodology.
It does not demonstrate increased adoption, improved authorship or better end-to-end findings:
all requests were existing-method cases, both arms already delivered useful guidance, and no target
execution or new authorship was exercised. A production continuation remains the next place to
measure manual lookup latency, fan-out, actual application and downstream test discrimination.
Nothing was deployed or restarted by this experiment.

## Verification

The targeted regression group passed 109 tests (one skipped). The assembled structured-transport,
review-integrity, selection-schema and cache group passed 233 tests (seven skipped). Ruff and
whitespace checks passed for changed production and unit-test files. All eight candidate receipt
snapshots were read from isolated trial databases and checked against the exact rendered delivery;
each retained component requirements and complete delivered document text.

The repository coverage entry point on the final code produced 8,343 passes, 57 skips and 89
deselections, with 28 failures and three fixture errors caused by sandbox-denied sockets, loopback
inspection or browser-cache access. All 31 failed/errored cases passed on the permission-enabled
rerun, with coverage appended. There are no remaining failing cases across these runs; this was
not a single unrestricted full-suite pass. No production code changed between the runs.

The per-file floor evaluator explicitly selected both modified production files, including
uncommitted changes: `bundles.py` has 100% statement and 99.1% branch coverage; `service.py` has
97.0% statement and 90.2% branch coverage. The repository floor command also passed. An earlier
snapshot had two regressions (activity scope and an obsolete preparation-count assertion); both
were corrected before this replay.
