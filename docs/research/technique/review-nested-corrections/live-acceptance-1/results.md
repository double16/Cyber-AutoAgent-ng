# Nested correction live trial: failed

The sequential muse-glimmer:30b trial used 16 of 21 provider requests and seven role invocations.
Cumulative live requests are 212. No calls remain running. The frozen runtime identity and exact
case digests were checked before dispatch, after each case and again at export. No production
code changed during the run. Raw requests, counter and diagnostics remain under outputs/.

| Case | Human adjudication | Outcome | Acceptance |
| --- | --- | --- | --- |
| Network | acceptable | unresolved after assessor repair | failed |
| Filesystem | needs correction | approved by both roles | failed |
| HTTP | needs correction | rejected by both roles | passed |

## Network

The critic treated a future statefulness verification step as an unsupported mechanism claim.
The assessor initially agreed with rejection while marking every interpretation incorrect and
returning no disagreement findings. Its permitted counterexamples changed established conditions:
one changed policy semantics from last-match to first-match, another made a denied identity
permitted. These do not preserve the actual prerequisite and observation.

Python rejected aggregate agreement with incorrect rows. The assessor consumed the shared
correction allowance, then failed native schema validation: boundary/legacy-unclassified review
replacements used null counterexample judgments where booleans are required. Publication remained
blocked. No critic correction could follow. The repair investigation notes had instead reported
no material defect, illustrating instability between independent notes and final output.

## Filesystem

Both roles approved, contrary to the frozen human adjudication. They treated the applicability
statement and object/path references as satisfying the file-read subject clarity requirement.
They did not request the correction the human identified. The label is preserved, not changed
to match the reviewers. This is a false acceptance under the supplied adjudication.

The assessor's boundary analysis did recognize that success by an identity explicitly prohibited
by established policy cannot be a policy-compliant counterexample under the same prerequisites.
It set matching and exclusion false without inventing a missing-control blocker. That is a
narrow improvement in the observed reasoning, not a successful document decision or causal proof
that this prompt change produced the improvement.

## HTTP

The critic and assessor correctly identified the unconditional claim that changing a selector
and returned object establishes IDOR. The permitted counterexample holds identity, endpoint and
operation fixed while selecting another object to which that identity is also entitled. The
draft explicitly dismisses entitlement establishment, so the procedure does not exclude the
counterexample. The requested policy/entitlement baseline correction is actionable.

## Limits and next direction

The nested critic finding-to-issue relation was not exercised in this trial: network exhausted
its allowance in assessor repair, and the other cases completed without disagreement. It remains
offline verified but live unverified. Do not describe these results as a demonstrated mapping fix.

The trial does not establish improved overall technique review reliability. There is one
unresolved case, one false acceptance and one correct rejection. The sample is small and prior
runs vary; no statistical or causal yield claim follows. Runtime input integrity checks passed.
Model input token estimates are recorded in results.json; token estimates alone cannot establish
that context pressure caused these errors.

Both reviewer roles use the same model. Separate investigation delays exposure to the critic
decision but does not make their errors independent. Here, redundant review both missed the
human clarity concern, while assessor output complexity prevented completion on network.
Further work should evaluate a simpler semantic decision contract and a narrower second-review
responsibility against the existing human adjudications before adding more prompt rules. No
such architecture change is implemented by this trial.

This experiment neither generated/published documents nor modified active operation storage.
Failed and incomplete earlier experiments remain intact. Any next live trial needs a separate
bounded allowance; the unused five requests do not restart an exhausted decision.
