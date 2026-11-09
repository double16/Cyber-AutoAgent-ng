# Upstream-scope generation follow-up, trial 1

The run exhausted its 30-request allowance. No document completed the publication/review pipeline.
Network and filesystem reached authoring; HTTP was not attempted. This is not a successful yield
improvement result. Complete unpublished drafts are retained for separate human quality judgments;
pipeline rejection alone does not prove that a draft is useless.

| Request | Outcome | Requests | Complete authored drafts retained |
| --- | --- | --- | --- |
| Network | Critic/assessor disagreement exhausted correction | 13 | 1 |
| Filesystem | Budget exhausted during second-draft review | 17 | 2 |
| HTTP | Unattempted after exhaustion | 0 | 0 |

Cumulative live calls across experiments are 147. None are running. All three requests remain in
the denominator. No failed case was replaced, no review limit was reset, and no active operation
or published module technique was modified.

## Drafts for human review

The last complete authored versions are [network draft 1](network-draft-1.md) and
[filesystem draft 2](filesystem-draft-2.md). The preceding [filesystem draft 1](filesystem-draft-1.md)
is retained for comparison, not selected as a replacement outcome. These are exact assembled
review subjects without a claim of reviewer acceptance. Their hashes and complete evidence are
in [results.json](results.json). The fixed requests remain unchanged from the prior pilot.

Human judgments should distinguish materially correct/useful documents from drafts requiring
correction, with concrete reasons. The last-version selection rule was declared before generation;
do not choose whichever version receives the best retrospective label. Human-usable draft yield
is pending. Pipeline-completed yield is 0/3. These are separate measures.

## Observations

Network progressed past the research gate that blocked it in pilot 1. Its author produced a
complete method. The initial critic approved it, while the independent assessor identified an
unsupported claim about subsequent packets bypassing policy re-evaluation, attribution concerns
and other procedure issues. The corrected critic rejected those defects; the assessor still
found the response incomplete, exhausting the shared correction. An unsupported factual claim
is a legitimate type of defect and must not be waived as original synthesis.

The network assessment also constructed a permitted counterexample to an observation the draft
called legitimate, then treated that permitted behavior as part of the interpretation defect.
This suggests that the all-check counterexample contract is still failing to distinguish
prerequisite, legitimate-behavior and boundary-violation purposes reliably. The presence of other
real defects does not make that reasoning valid. Full assessments are retained rather than the
truncated exception alone.

Filesystem reached drafting. Its first rejection treated imperative future procedure steps as
contradicting the request that no target execution was requested. There is a real wording tension
in a prerequisite saying controls can be applied without execution, but describing how future
execution would occur is not itself performing a target assessment. The requested correction
pushed the author toward procedure-design phrasing. A later review then disputed whether the
bounded open(2) excerpt supported an error-return assertion in controller-added attribution and
the body. Budget exhausted before independent review of that revision completed.

The source excerpts omit much of the underlying manuals, including contextual headings. Source
context limits, genuine unsupported claims, inflated procedure demands and review-purpose
confusion must remain separate hypotheses. Do not infer that all objections are false, or that
more forceful prompting alone will fix them. The existing experimental purpose-aware contracts
provide a candidate for a separate follow-up, but were not silently substituted in this run.

## Limits and next evidence

No generation improvement has been established: pilot 1 completed one human-accepted document,
whereas this run completed none. The source-scope and author-style changes occurred together,
there was no randomized matched comparison, and budget allocation differed because network now
reached review. Human judgment of the complete drafts can help distinguish author quality from
review-gate failures. It cannot retroactively turn this pipeline run into a successful completion.

Before another live experiment, adjudicate the exact last drafts. Then consider purpose-sensitive
review of prerequisite/legitimate/boundary checks, explicit distinction between writing future
procedures and performing target execution, and a separately frozen source-context variant that
retains relevant surrounding manual sections. Each intervention needs its own provenance and
bounded protocol; no additional model calls were made during this analysis.

## Human adjudication

The user judged the exact network draft acceptable and useful, and the last filesystem draft as
needing correction. The filesystem document lost explicit file-access context and made the reader
search to determine what was being read; the previously accepted version was preferred. Hash-bound
judgments are recorded in human-adjudications.json. Human-usable last-draft yield is 1/3, while
pipeline completion remains 0/3. The accepted network draft is evidence of a review-gate false
block against this human label. It does not establish that every factual objection was invalid.

The filesystem critic explicitly requested removing imperative execution instructions and
reframing the method as procedure design because no target execution was requested. That feedback
likely contributed to abstraction, although the simultaneous prompt changes prevent attributing
all degradation to one instruction. Writing future testing instructions is different from executing
a target test now. The repair should have preserved explicit file objects, read operations and
process identity while correcting only the actual wording conflict.

The user's broader conclusion is that review is too demanding. The next calibration should make
blocking materiality narrower and repairs smaller: advisory source/date/detail issues remain
suggestions unless they undermine a requested conclusion; original synthesis need not source every
control step; legitimate observations need no violation counterexample; concrete imperative steps
are valid in future methodology. Preserve the subject and operation during every repair. Real
incorrect security interpretations and unsupported material mechanisms still require correction.
No code or model calls were changed in response to this adjudication.
