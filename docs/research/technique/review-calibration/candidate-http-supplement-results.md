# Candidate HTTP follow-up

The candidate did not produce a controller-valid review. It exhausted the single correction
allowance after four provider requests and two critic invocations. Independent assessment never
started. Six requests remained under this follow-up's cap, but remaining request budget does not
increase the per-decision correction allowance.

Both critic responses identified the real substantive defect: changing an object selector does
not prove unauthorized access when the unchanged identity may legitimately access both objects.
The requested correction was actionable: establish entitlement and show access outside it.
This reasoning agrees with the human label, but is not a valid, independently assessed rejection.

## Contract failure

The frozen input had one request requirement, whose ID was `method`. Acceptance criteria used a
separate namespace, including `requirement:method`, `core:interpretation` and
`core:technical_support`. The blocker correctly placed `core:interpretation` in `criterion_id`,
but its initial `requirement_ids` also contained `core:interpretation` and `core:technical_support`.
Python rejected those unknown requirement IDs.

The correction retained the same namespace confusion. It replaced `method` with
`requirement:method` and retained the two `core:*` values in `requirement_ids`, claiming that
these were corrected IDs from the frozen acceptance contract. All three values were invalid in
that field. It supplied a disposition for the prior blocker, so adding correction accounting did
not prevent this particular failure. The final diagnostic names the revised blocker's unknown
requirement references.

The contract distinguishes request requirements from acceptance criteria, but the live model
confused their related IDs and the correction did not repair it. This is a controller-contract
reliability failure with semantically useful findings, not evidence that the document was approved
or safely rejected. Do not bypass validation or classify this as a passing negative control.
The interpretation fields also supplied entitlement controls absent from the draft; a correct
blocker does not make those supplied controls a faithful description of the authored procedure.

## Provenance and disposition

Both offline transport checks passed. The candidate code identity still matched the frozen
original receipt after the run. Complete critic attempts, input sizes, failure, settings and
request hashes are in [the JSON result](candidate-http-supplement-results.json). The highest
recorded prompt estimate was 10,385 tokens against a 40,000-token limit. Full authored records
were checked at provider dispatch. No observed truncation explains this failure.

Raw requests and diagnostic logs remain private under
`outputs/technique_review_trials/candidate-http-supplement-20261105-1`. No code or prompt was
changed and no further model calls were made after correction exhaustion. No draft was corrected
or re-reviewed.

Across the original trial and both separate HTTP follow-ups, 39 live requests were used:
30 original, five supplemental baseline HTTP, four supplemental candidate HTTP. The original
trial remains incomplete rather than being retroactively completed by later runs.

| Subject | Human label | Baseline result | Candidate result |
| --- | --- | --- | --- |
| Network | acceptable | Approved after contract correction | Approved |
| Filesystem | acceptable | False rejection after assessor disagreement | Approved after contract correction |
| HTTP, separate follow-ups | needs_correction | Correct rejection with independent agreement | Contract failure after correction; no independent assessment |

The candidate avoided the observed filesystem false rejection, but failed to finish the defective
HTTP control. This evidence does not establish production readiness or improved generation yield.
The next change should address requirement/criterion namespace separation and correction guidance,
with deterministic reference validation and an unchanged HTTP replay after offline regressions.
That would be a new revision and experiment, not a replacement for this failed result.
