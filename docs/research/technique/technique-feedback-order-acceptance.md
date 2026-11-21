# Feedback schema-order acceptance

> Historical experiment record: contracts, counts and outcomes below describe the frozen trial,
> not necessarily the current runtime. See [Techniques](../../techniques.md) for current behavior.
> Earlier temporary artifact paths are provenance records; availability is not guaranteed.

The retained change moves evidence_mappings from near the beginning of ContributionFeedback to
its final field, after reason. IndependentFeedbackReview uses the same nested schema, so both
roles receive the same order. Fields, values, defaults, validation constraints, scores, correction
allowances and prose-ID recovery are unchanged. The previous and new decoded schemas compare
equal when JSON object-key order is ignored. There are no conditional schema branches.

## Hypothesis and controlled comparison

Prior failures contained empty mapping lists despite explanations recognizing a concrete
contribution. Asking for mappings after the explanation could help an autoregressive model produce
the references once it has described the correspondence. This mechanism is a hypothesis, not a
proven account of internal model reasoning. Normalized tool-output order alone cannot establish
the raw provider's generation sequence.

The trial reused the exact seven frozen input files from the activity-specific acceptance run;
parsed input files were checked for equality. Only schema property order changed. No prompts were
tuned during the replay. The prior run completed three controls and left four pending. The new
run completed all seven, with authored mappings and no correction, recovery or pending decisions.

| Control | Result |
| --- | --- |
| HTTP hypothesis, no target requests | Helpful (+1) |
| Filesystem hypothesis, no file access | Helpful (+1) |
| Network execution/exclusion | Helpful (+1) |
| HTTP verification/refutation | Helpful (+1) |
| Harmful filesystem analysis | Harmful (-1) |
| Citation only | Neutral (0) |
| Unknown activity, insufficient contribution evidence | Neutral (0) |

Every completed decision passed independent review. All effect mappings resolved to known
controller references. Both unknown-profile controls remained neutral for substantive evidence
reasons, not because their activity was unknown. No historical records or active operation were
modified; all feedback was isolated and no disputed score was persisted.

## Verification and limits

The technique suite passed **985 tests**, with one skipped. Ruff, whitespace and the repository
per-file coverage-floor checks passed. The schema regression checks that mappings follow reason
in both evaluator and nested reviewer schemas and that no conditional schema was introduced.
Earlier repository-wide activity-context integration verification passed 7,614 tests; that full
run predates this ordering-only change. No new workflow behavior was added by this trial.

This is one small frozen control set using muse-glimmer:30b, not a general production success-rate
estimate or a multi-model validation. Large task artifacts and output-exhaustion scenarios were not
replayed here. Existing fail-closed reference checks, independent review and correction limits remain
necessary. Earlier failed schema and wording experiments remain failures, not retroactively accepted.

The run used 14 role invocations and 14 provider inputs, sequentially,
within its 28-invocation cap. Frozen inputs, outcomes, accepted source, harness and complete model
diagnostics: `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/mapping-order-acceptance-6fd1h3ot`.
