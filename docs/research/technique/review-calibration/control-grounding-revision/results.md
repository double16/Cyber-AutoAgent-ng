# Reference-grounding acceptance result

This run failed acceptance because corrected HTTP remained disputed after the shared correction.
Network and filesystem completed with approvals matching the human labels. Both grounded-control
assessments used valid body references without an exact-excerpt contract correction.

| Subject | Human label | Disposition | Requests |
| --- | --- | --- | --- |
| Network policy | acceptable | Approved with independent agreement | 5 |
| Filesystem policy | acceptable | Approved with independent agreement | 5 |
| Corrected HTTP selector | acceptable | Semantic disagreement after correction | 8 |

Total: 18 of 21 provider requests, eight role invocations, sequential execution. Three requests
remain unused in this allowance. Cumulative live requests across all experiments: 79. No calls
remain running. Complete findings, correction outcomes, model settings, input sizes and request
hashes are in [results.json](results.json); raw payloads remain private under outputs.

## What improved

The previous revision's filesystem review failed twice on exact wording despite cited body
records. The unchanged fixture now completed in five requests with no correction. This shows
that the new reference-grounding contract worked on that input in this run. It does not establish
general reliability or prove that all semantic differences were caused by the contract change.
No reference-contract failure occurred across these three reviews.

## Corrected HTTP disagreement

The critic initially approved the corrected HTTP document with no issues. The independent
assessor agreed that its violation interpretation was conditional and logically distinguished
legitimate selection from access outside entitlement. It found no permitted scenario satisfying
both independently established denial and the suspicious observation. It nevertheless proposed
three blockers:

1. Technical support: the 123-character retained source excerpt covered generic IDOR, not each
   header-selector test mechanic, identity check, marker comparison or negative-control step.
2. Operational sufficiency: the method did not distinguish missing-selector rejection from
   authorization rejection, despite explicitly withholding an authorization conclusion from that
   request-shape control.
3. Attribution: the body did not repeat a source limitation even though metadata described the
   source as a technical foundation and the procedure as original synthesis.

The critic corrected its response by adopting the first two issues. The final assessor accepted
those objections but continued to require the omitted attribution blocker, so the corrected
rejection remained disputed. This was an unresolved semantic disagreement, not a successful
negative-control result and not a controller-contract failure. The attribution objection was
already present in the first findings; it was not a newly introduced demand on the final pass.

The exception text truncates the final assessment. The complete original and final findings,
critic issues and final disposition remain in the JSON record and private diagnostics. No
findings were lost merely because the displayed exception was shorter.

## Interpretation and limits

The findings suggest a persistent confusion between original procedure design and factual claims
requiring source support. The procedure makes header selection conditional on a future API-contract
check; it does not claim all headers select objects. The retained source provides an IDOR concept,
not a prewritten testing method. Absence of the exact testing workflow in that excerpt alone does
not demonstrate an incorrect workflow. Conversely, original synthesis does not excuse unsupported
asserted mechanisms. That distinction still needs independent semantic assessment.

The missing-selector objection also appears to broaden the advertised purpose of that control.
The draft explicitly uses it for request shape and says it cannot establish authorization. A
control need not establish a conclusion the procedure withholds. A real operational defect would
need to show why the separate entitlement-bound comparison cannot support its stated conclusion.

The attribution demand depends on requiring the limitation specifically in the body. The frozen
criteria require accurate attribution and limitations but do not prescribe body placement. The
human label is acceptable. These observations make the proposed blockers questionable, but they
are analysis of the disagreement, not a replacement independent model verdict or new adjudication.
The retained excerpt is genuinely short and cannot independently support arbitrary technical facts.

Full authored records were validated at provider dispatch. The largest recorded prompt estimate
was 12,625 tokens against the configured 40,000-token context. There is no observed truncation
explaining this result. No generation yield was measured, and production readiness is unestablished.
