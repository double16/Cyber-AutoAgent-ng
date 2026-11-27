# Final bounded review acceptance

The same frozen support-scope revision matched all four human labels with independent agreement
and no corrections. Three positive fixtures and one negative control were reviewed separately;
this summary combines their evidence without rewriting the preceding failed trials.

| Fixture | Human label | Decision | Independent review | Requests |
| --- | --- | --- | --- | --- |
| Network policy | acceptable | Approve | Agree | 5 |
| Filesystem policy | acceptable | Approve | Agree | 5 |
| Corrected HTTP | acceptable | Approve | Agree | 5 |
| Original defective HTTP | needs_correction | Reject | Agree | 4 |

The negative-control critic identified the unconditional IDOR claim and requested an entitlement
baseline. Its independent assessor constructed a matching permitted scenario: the unchanged
identity is entitled to both objects, so changing the selector legitimately returns a different
object. No authored control excludes that scenario. The rejection concerns actual missing
entitlement discrimination, not normal header-based selection itself.

Positive review evidence is in [results.json](results.json); negative evidence is in
[negative-http-results.json](negative-http-results.json). Both retain runtime identity, complete
findings, independent assessment and request provenance. Raw inputs stay private under outputs.
The runtime identity was checked after both runs. No prompt or code change occurred between them.

The combined allowance used 19 of 21 requests: 15 positive, four negative. Cumulative live calls
across all preserved experiments are 98. No model calls remain running. No corrected fixture was
silently substituted for the original negative control.

This is successful bounded review acceptance for these inputs. It does not establish general
model reliability or improved useful-document yield. The next distinct evaluation is a bounded
generation pilot with frozen requests, human judgments of exact output hashes, complete failure
denominators and explicit source/research scope. A matched generation comparison is needed before
claiming improved yield. Earlier failed trials remain failed and available for analysis.
