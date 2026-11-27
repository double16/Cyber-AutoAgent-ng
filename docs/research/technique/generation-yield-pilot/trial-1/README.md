# Generation-yield pilot 1

The fixed three-request trial completed using 19 of 30 provider requests and 16 role invocations.
It produced one model-approved document. Human usefulness adjudication is pending, so no useful
output rate can yet be claimed. All three requests remain in the denominator.

| Request | Outcome | Requests | Authoring reached |
| --- | --- | --- | --- |
| Network | Research coverage exhausted | 4 | No |
| Filesystem | Generated; human review pending | 11 | Yes |
| HTTP | Research coverage exhausted | 4 | No |

Cumulative live requests across experiments: 117. No calls remain running. Eleven requests remain
unused in this allowance; they do not authorize replacing failed cases or reopening their repair
limits. No target execution, active-operation access or module publication occurred.

## Exact document for human review

[Generated filesystem document](generated-filesystem.md) is copied byte-for-byte from the rendered
pilot output. Its SHA-256 is
`78b7b925b8b1faa57d27a9be2da9fb46d67d4381a08d4ad494627d8220ce6ad1`.
It includes controller-added lifecycle metadata; it is an experimental export, not a published
module technique. Judge whether its actual procedure is materially correct and useful for the
frozen request, and record `acceptable_and_useful` or `needs_correction` with a reason. Do not infer
usefulness from model approval or the presence of citations.

The request was to describe a reusable read-access comparison using an established policy, actual
process identity, allowed/denied controls and evidence requirements, without target execution or
a particular tool. [Machine-readable results](results.json) retain the input hashes and model
findings. [The adjudication record](human-adjudication.json) remains pending.

## Observed research failures

Network source assessment accepted the retained pf.conf facts about rule precedence and state
creation as authoritative and useful for original synthesis. Requirement assessment nevertheless
blocked on absence of identity-binding mechanisms, controls and evidence-collection procedures in
the retained excerpts. It asked for user/process/certificate binding in pf, although the request
required generic identity binding, not those particular mechanisms. HTTP similarly blocked because
the bounded IDOR excerpt did not supply header-selector mechanics or a complete comparison method.
Neither request reached an author. Frozen research could not obtain additional source material,
so the normal three-batch limit terminated both with unresolved coverage.

This repeats the support-versus-synthesis distinction upstream of the critic/assessor changes.
The source judgments themselves recognized that relevant technical facts could anchor original
procedure design, but requirement judgments placed absent procedures into factual gaps. The
retained pool is genuinely short and cannot support arbitrary factual claims. These observations
suggest a scope-classification problem, but do not prove that every missing factual foundation
should have been waived. The failures remain research-coverage outcomes under the frozen inputs.

Filesystem requirement assessment instead treated identity resolution, controls and evidence
collection as procedural design grounded in read-permission facts and allowed drafting. The critic
and independent assessor approved the output. They used the no-security-checks exception despite
a methodology containing policy comparisons and allowed/denied controls. That review-completeness
judgment warrants examination; it is not proof the document is harmful or useless. Human review
must evaluate the actual document rather than treating empty issues as a quality guarantee.

## What this measures

Model-approved output rate is one of three requests. Human-accepted yield is pending and will be
zero or one of three for this trial. This is conditional on the frozen retained source excerpts,
not end-to-end retrieval performance. There was no matched baseline generation run, so improved
generation yield is not established. Earlier review-acceptance successes and failed trials remain
separate evidence. Raw provider payloads stay private under
`outputs/technique_review_trials/generation-yield-live-20261106-1`.

Next, obtain exact-output human adjudication. Then consider consolidating support-scope guidance
at the shared source used by both requirement assessment and review, with positive and negative
regressions for original synthesis versus genuinely unsupported factual mechanisms. Do not alter
these frozen inputs or retry failed cases as though this trial had succeeded.

## Human adjudication

The user accepted the exact filesystem document as materially correct and useful, describing it
as beyond previous versions and preferring its headings, numbered lists and more specific
technical language over prose. The judgment binds the unchanged hash recorded above.
Human-accepted useful-document yield for this fixed pilot is **1/3 (33.3%)**. Network and HTTP
remain failed requests in that denominator.

This is observed yield conditional on the frozen sources. The user's comparative quality
feedback is valuable qualitative evidence, but no matched generation baseline establishes a
comparative yield improvement. No additional model calls were made and no historical technique
score was changed. Total live requests remain 117.
