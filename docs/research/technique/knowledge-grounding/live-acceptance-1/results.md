# Knowledge-grounding live acceptance: failed

The sequential muse-glimmer:30b trial used 16/21 provider requests and six critic invocations.
Four cases completed; the filesystem clarity case exhausted its output retry. Two valid decisions
matched the five frozen labels. Cumulative live requests are 243; none remain running.
Runtime/case checks passed before dispatch, after cases and at export. Production code stayed frozen.

| Case | Human label | Result | Valid agreement |
| --- | --- | --- | --- |
| Network | acceptable | rejected | no |
| Filesystem needing clarity | needs correction | review unavailable | no |
| Defective HTTP | needs correction | rejected | yes |
| Accepted filesystem | acceptable | rejected | no |
| Corrected HTTP | acceptable | approved | yes |

## Network: changed objection, unresolved adjudication mismatch

The critic acknowledged that source claims were appropriately limited. It no longer rejected the
future state-handling check merely for absence from the excerpt. Instead it objected that
last-match/state semantics were presented generically, and demanded more identity-binding detail.

The generalization concern is potentially substantive and should be adjudicated against the intended
scope; it must not be dismissed merely to force label agreement. The second finding asks for
documentation of identity attributes, enforcement point and trust assumptions even though the
cited prerequisites and procedure already request these. Its materiality is weakly established.
The frozen acceptable label remains unchanged, so this case fails acceptance.

## Filesystem clarity: correct concern, wrong reference field

Both submitted reviews identified the expected missing clarity: the procedure uses generic objects
without making the file-read operation and tested file objects explicit. However, both placed
known current draft IDs (d64288cb04697:b9, b8 and b57) in passage_ids. These are draft records,
not returned source passages. Python rejected the field placement twice and returned
ReviewUnavailable. The second response did not fix it.

The IDs are not fabricated; their record type is wrong for that field. This is an actionable
bookkeeping limitation. A future fix can resolve known IDs through controller reference catalogs,
without judging semantic support or searching arbitrary prose. No such normalization was applied
during this frozen trial. Do not count the proposed correct concern as a valid final rejection.

## Defective HTTP: correct material rejection

The critic identified the actual technical error: changing an object selector and receiving
another object does not establish IDOR when the unchanged identity is entitled to both objects.
Requested corrections establish the entitlement restriction and a policy-based comparison.
Several issues repeat this same defect under different criteria, but the core rejection is justified.
The previous demand to prove every procedural control from the short excerpt was absent.

## Accepted filesystem: source-wording objection reclassified as correctness

The critic accepted the document's method, controls and interpretation, then blocked on the
source-grounded statement that the kernel reports access denial. It argued that the retained
excerpt does not explicitly mention kernel reporting. It did not demonstrate that the technical
statement was false; the material-impact explanation concerns source representation.

This is a source-support/attribution objection labeled core:correctness. Removing the universal
source-support criterion does not mechanically prevent semantic relabeling. The document explicitly
calls the paragraph source-grounded, so any attribution qualification should be considered on its
actual accuracy and materiality, rather than treating missing identical wording as technical falsity.
The acceptable human label remains frozen; this case fails acceptance.

## Corrected HTTP: approved

The critic approved the independently established entitlement policy, fixed identity, object
comparison, inconclusive conditions and evidence requirements. It left marker examples and
request-shape control wording as optional suggestions. The earlier source-completeness blocker
did not recur in this case.

## Conclusions and next work

The code now permits model knowledge structurally, but this trial does not demonstrate acceptable
overall review precision. Corrected HTTP improved in this observed run while other failures remain;
small samples and changed requirement context preclude a causal yield claim.

Separate the two remaining problems:
1. Known references placed in the wrong list: simplify or normalize reference-kind bookkeeping using
   exact controller catalogs. Preserve original responses and reject genuinely unknown references.
2. Review strictness/materiality: evaluate the claimed concrete error and attribution scope, including
   whether the network generalization concern is valid. Do not add another assessor or relabel
   historical human judgments automatically.

Source-required research behavior and generation yield were not exercised in this review-only
trial; they remain offline tested. There was no author revision or publication. Any next live
experiment requires a separate allowance; unused requests do not restart the exhausted decision.
Earlier trials are preserved unchanged. Raw provider data stays under outputs/, while results.json
and per-case responses retain portable findings, settings and hashes.
