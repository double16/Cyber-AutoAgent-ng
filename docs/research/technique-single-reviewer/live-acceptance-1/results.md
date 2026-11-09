# Single-reviewer live acceptance: failed on semantics

All five cases completed with 15/21 provider requests, five critic invocations, no assessor,
no output retries and no JSON/schema/reference failures. The smaller contract completed without
the bookkeeping failures seen in previous trials. Cumulative live requests: 227. None running.
Runtime and case identity checks passed; every job explicitly selected review_mode=single.

| Case | Human label | Decision | Agreement |
| --- | --- | --- | --- |
| Network | acceptable | rejected | no |
| Filesystem needing clarity | needs correction | rejected | yes |
| Defective HTTP | needs correction | rejected | yes |
| Previously accepted filesystem | acceptable | approved | yes |
| Corrected HTTP | acceptable | rejected | no |

Three of five publication decisions matched the frozen human adjudications. Acceptance required
all five decisions and actionable reasons, so rollout remains blocked. Completed review output
does not establish correct review.

## Network false rejection

The critic again treated 'verify subsequent packets follow state, not re-evaluate policy' as an
unsupported factual assertion. It requested removal/qualification despite the future verification
framing and the human's acceptance of the exact document. This is the same unresolved distinction
between an asserted mechanism and a future check seen in earlier runs. The judgment is preserved
as a disagreement with human adjudication, not used to relabel the fixture.

## Filesystem clarity correctly identified

The critic identified requirement:human_file_subject and requested an explicit statement that
the operation is file read access and the tested objects are files identified by path/identifier.
It grounded the issue in the generic object terminology and did not repeat the prior literal
source-wording or impossible-counterexample objections. This is an actionable correction aligned
with the human judgment. Optional suggestions still include additional file metadata; those are
not independent blockers.

## Defective HTTP correctly rejected, with a caveat

The primary issue correctly distinguishes selector influence from an authorization violation:
establish that the unchanged identity lacks entitlement to object B before calling access IDOR.
However, the second overlapping issue also treats selector-absence rejection as necessary for
confirmation. This overstates a request-shape control's role; entitlement-based unauthorized access
does not universally require such a rejection. Count the core publication decision as aligned,
without claiming every requested correction is justified. This limits the reasoning-quality result.

## Accepted filesystem preserved

The clearer previously accepted filesystem document was approved. The critic accepted the
retained read-permission/denial facts and policy comparison methodology. Checklist expansion and
retrieval provenance remained optional suggestions. The fixture uses the captured pre-publication
document, with exact body/source identity verified against the accepted delivery export.

## Corrected HTTP false rejection

The critic acknowledged the correct interpretation, controls and inconclusive conditions, then
rejected it because the brief retained IDOR excerpts did not separately support ownership versus
entitlement, independently established policy, identity stability and the limited meaning of a
request-shape negative control. These are methodological comparisons/exclusions already labeled
as original synthesis; the human had accepted this exact fixture.

The review also reverses repair ownership: a source_attribution issue requests removal or
qualification of procedural claims, while an authored_content issue requests metadata attribution
changes. Python validates structural references but does not judge these semantic category errors.
No author or attribution repair was executed in this isolated trial.

## Implications and limits

The single-reviewer workflow is mechanically simpler and completed this small sample without
contract repair. It has not demonstrated acceptable semantic precision. The dominant remaining
problem is source-support overreach against original methodological controls and future checks,
with an additional unnecessary-control demand in the defective HTTP review.

Do not restore the assessor merely to compensate: prior runs show correlated errors and contract
failures. Do not add bookkeeping fields to address these semantic errors. Any next change should
target the materiality and support-versus-synthesis decision itself, evaluated against the retained
human judgments. No such follow-up change or live allowance is included in this result.

The sample is small and earlier runs varied; these observations are not a statistical or causal
yield comparison. Results, exact outputs and provider-request hashes remain available in
results.json and per-case response records; raw inputs remain private under outputs/.
All earlier failed trials remain unchanged. The unused six requests do not restart any decision.
