# Matched review trial 1

The 30-request trial is incomplete and does not establish production acceptance. The candidate
avoided one human-adjudicated false rejection on the completed cases, but neither arm completed
the defective HTTP control. No document-generation yield was measured.

| Frozen subject | Human label | New-policy baseline | Candidate | Requests, baseline / candidate |
| --- | --- | --- | --- | --- |
| Network policy | acceptable | Approved, one contract correction | Approved, no correction | 7 / 5 |
| Filesystem policy | acceptable | Rejected after assessor disagreement | Approved, one contract correction | 8 / 7 |
| HTTP selector | needs_correction | Incomplete; critic output failed controller validation | Unattempted | 3 / 0 |

Total: 30 provider requests, 13 role invocations, sequential execution. The original allowance is
exhausted. The [machine-readable record](live-results-1.json) retains the complete decisions,
independent assessments, correction outcomes, input sizes and request hashes. Sensitive raw
requests and full diagnostic logs remain under the trial's private output directory.

## Observed reasoning errors

The baseline filesystem critic initially found no blockers. Its assessor then treated explicit
prerequisite-verification instructions as missing because they did not provide sufficiently concrete
implementation details. It demanded platform-specific identity, namespace, inode and error-code
checks even though the request explicitly did not require command syntax. The draft already
instructed independent policy establishment, identity and object binding, distinction of missing
paths from denial, and repetition of the allowed control. The critic adopted the assessor's demands
as two blockers; the assessor then accepted that rejection. This is a false block against the
human label, not successful correction merely because the two roles eventually agreed.

The baseline assessor's permitted scenario assumed that the file was actually authorized through
ACLs, groups, privilege or sharing. It explicitly marked that scenario as not satisfying the draft's
prerequisites, yet used it to support an interpretation defect. It also called a violation condition
unconditional while quoting the condition that the file was independently established as denied
and policy remained unchanged. This confuses insufficient implementation detail with a missing
logical condition. Concrete execution examples may improve guidance, but their absence does not
by itself make an explicitly conditional interpretation unconditional.

The candidate accepted the same filesystem document. That matches the human label, but one case
cannot attribute the difference causally to the policy changes or establish robustness.

## Contract and bookkeeping failures

The baseline network assessor and candidate filesystem assessor each paraphrased a discriminator
where the contract required an exact body excerpt. Both recovered after one correction. These
failures cost four additional requests in total because corrections repeat investigation and
finalization. Short exact excerpts can also pass availability checks without being a useful
standalone control explanation; semantic interpretation still needs the surrounding procedure.

The baseline HTTP critic identified the substantive defect: changing object selection is not
sufficient to establish access outside the unchanged identity's entitlement. However, it placed
`core:interpretation` and `core:operational_sufficiency` in `requirement_ids`, where only `method`
was valid. An offline replay of the retained response reproduced that controller validation error
without model calls. Its interpretation assessment also supplied entitlement checks absent from
the authored procedure. This response is useful diagnostic evidence, but it is not a valid,
independently assessed rejection. The correction could not dispatch because the cap was exhausted.
See [the HTTP diagnostic](http-incomplete-diagnostic.json).

## Context and limits

The model was `muse-glimmer:30b`, temperature 0, low reasoning, with a configured context of 40,000
and output limit of 4,096. The largest recorded prompt estimate was 10,702 tokens. Full authored
records were checked at each actual provider dispatch, and both runtime identities still matched
the frozen preflight receipt after the trial. There is no observed context truncation explaining
these errors. This does not exclude limitations in reasoning, attention or output capacity.

The original protocol, offline verification and interim checkpoints retain their creation-time
state. The final live counts and dispositions are authoritative in this report and its JSON record.
No failed outcome was replaced, no historical score was rewritten and no active operation was used.

The user subsequently authorized a separate baseline-HTTP follow-up. Its additional allowance is
capped at 10 requests in [a separate protocol](baseline-http-supplement-protocol.json). It does not
complete the candidate's HTTP result or retroactively change this exhausted trial's disposition.
