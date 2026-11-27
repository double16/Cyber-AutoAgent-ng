# Candidate HTTP replay after reference-catalog revision

The revised candidate correctly rejected the unchanged, human-labeled defective HTTP fixture.
Independent assessment agreed with the rejection, with no correction. The review used five
provider requests and two role invocations, within its six-request allocation. Across the failed
candidate HTTP review and this revision, nine of the ten authorized requests were used.

The critic used the exact request requirement ID `method` and kept `core:interpretation` in
`criterion_id`. Its blocker identified the substantive defect and requested entitlement-policy
establishment and a controlled comparison against that entitlement. Its interpretation assessment
now described the authored controls as they were: selector mutation, a missing-selector control
and repeated initial request, with no entitlement-establishment control. It did not describe a
silently improved method as the existing draft.

The independent assessor supplied the concrete permitted counterexample that the same identity
may be entitled to both objects. That scenario matches the draft's observation: changing only the
selector returns a different object. It correctly marked the scenario as matching, found no
excluding control, and agreed with the critic's rejection. `actual_discriminator` was empty rather
than an invented control. This is an actionable, controller-valid negative-control result.

Both scripted transport modes passed first. The runtime identity still matched the revised
frozen protocol after completion, and the unchanged subject hash matches the prior failed HTTP
review. Complete findings, assessment, settings, input sizes and request hashes are retained in
[the JSON result](candidate-http-reference-catalog-results.json). Raw inputs and diagnostics remain
private under `outputs/technique_review_trials/candidate-http-reference-catalog-20261105-1`.

| Run | Provider requests | Outcome |
| --- | --- | --- |
| Original matched trial | 30 | Incomplete; one baseline false filesystem rejection |
| Separate baseline HTTP | 5 | Correct rejection, independent agreement |
| Candidate HTTP before catalog revision | 4 | Invalid requirement IDs after correction; no assessment |
| Candidate HTTP after catalog revision | 5 | Correct rejection, independent agreement, no correction |
| Total | 44 | No additional calls running |

The prior failures remain recorded. This single-case recovery is evidence that the revised
contract worked for this input; it does not prove the revision caused every reasoning improvement
or establish reliability across documents. Network and filesystem were not replayed live against
this revision. No corrected document was authored or re-reviewed, and no generation yield was
measured. The original matched trial remains incomplete rather than being retroactively replaced
by successful follow-ups.
