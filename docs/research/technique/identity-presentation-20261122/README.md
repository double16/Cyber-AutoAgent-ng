# Identity presentation investigation

## Recorded input findings

Reviewed trace `e416136bd078db74a68c42c2802850b5`, final evaluator generation
`710b21c0960c4333` at 2026-11-21 18:13:02 UTC, for task
`66ba6744-d519-430d-b962-f005499502da`. The accepted review is at operation log line
638637. Earlier review line 638491 requested grounding/discrimination changes while treating
identity as adequate; the final review again treated identity as adequate.

The authenticated admin-session comparison is present in both future steps and required evidence.
It was not fitted away: the final evaluator budget events show no reductions, with roughly
23,000 estimated input tokens. The decoded user input is approximately 90,000 characters.
The native output requires identity semantic checks but does not display the meanings of the
hypothesis authentication declaration beside that check.

The frozen task has comparison=none, roles=[] for generation; its guidance explicitly distinguishes
that from future test needs. The declared role catalog contains only `user`. An `admin` label must
not be invented. A missing admin-capability prerequisite or needed adjustment to proposed steps
must be reasoned about by the model, separately from credential readiness.

There is an important contextual contract distinction. Generic `AuthenticationRequirement.roles`
allows unrestricted credential selection when empty, once authentication selection is invoked.
Hypothesis field descriptions and phase guidance use none/empty to request no authenticated
identity. Workflow task-requirement rendering independently decides authentication need from role,
comparison and assigned anonymous-comparison metadata; generic credential selection does not mean
that an empty hypothesis declaration automatically establishes an admin session. Earlier research
wording that generalized unrestricted selection to hypothesis identity needs was too broad.

The current publication renderer also has no final line separator. In the recorded assembled
input its last authentication row runs directly into the semantic rubric text. This is a formatting
boundary defect, not evidence that the identity mismatch would otherwise have been detected.

## Fixed experiment

`candidate.py` is a research-only presentation, not a production change. It displays steps and
required evidence as individual rows before the existing authentication declaration, then displays
the then-existing hypothesis field description and generic comparison meaning from their
models. These strings and the baseline renderer are now frozen in `contracts.json` and `baseline.py`;
tests verify that they reproduce the first captured inputs exactly. The independent production
contract correction made after the study started is not substituted into this frozen comparison. It does not infer required identities, add output fields, invent roles or change the rubric.

The fixed protocol in `protocol.json` covers HTTP, network and source/filesystem cases, including
valid anonymous, single-session none, and cross-role tests. Two repetitions of eight cases in two
arms produce 32 focused native checks. Three repetitions of the recorded admin context in two
arms add six bound full-decision checks. The study is capped at 45 transport requests; failures
remain failures. The protocol defines promotion thresholds before model calls. Do not promote the
identity presentation if it fails those thresholds, and do not tune prompts during the study.

The recorded-context fixture is a redacted, decoded export. It excludes the prior assistant answer
and finalization handoff to avoid giving the replay its answer. The candidate changes only the
current-publication section. Both arms retain the generation/task context, evidence, tool history,
semantic rubric and review-output instructions. The native role harness uses the recorded evaluator
system boundary plus its normal structured-finalization contract and the production bound model;
this is not a replay of live artifact tools or controller acceptance persistence.

Run from the repository root:

```sh
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" \
  uv run python docs/research/technique/identity-presentation-20261122/replay.py
```

Raw results are isolated under `outputs/technique_review_trials/identity-presentation-20261122/`.
No production database, existing technique document, or historical feedback is modified.


## Results

The frozen layout study completed all 38 trials, with valid native structured outputs in every trial.

| Measure | Baseline | Candidate |
| --- | ---: | ---: |
| Focused identity mismatches detected | 6/6 | 6/6 |
| Valid controls incorrectly revised | 3/10 | 2/10 |
| Recorded-context identity mismatches detected | 0/3 | 0/3 |

The candidate fails every predeclared promotion condition: no detection improvement, false rejections
of valid single-session-plus-anonymous controls, and no improvement in the recorded context. It is
**not promoted**. The production view retains its existing shape.

Recorded-context reasons treat the required future baseline as optional, available from the general
role catalog, or irrelevant because generation does not execute comparisons. These are observed
reasons; the study does not isolate one causal mechanism. Schema validity and controller contract
consistency do not establish semantic correctness: all six full-decision outputs were contract-valid
while missing the identity issue.

## Independent contract fixes

Code inspection confirms the controller supports comparison `none` for a single authenticated
context with a separately assigned anonymous control. The generic description's old “no
cross-identity comparison” wording was broader than that behavior. The canonical description now
refers to comparisons between authenticated accounts, roles, or tenants, with anonymous controls
separate. The same definition is rendered in the existing semantic rubric; generation/inspection
wording likewise specifies authenticated comparisons. No enum, defaults, validators, role catalog,
review count, or persisted shape changed.

The current publication renderer now ends with a newline so appended semantic instructions cannot
merge into the last data row. Assembled generation/review tests cover the shared definition and
boundary. These are contract-accuracy and formatting fixes, not promotion of the failed layout.

After the frozen comparison finished, six fresh, balanced current-contract smoke cases were run
under the same 45-request cap. Each protocol (HTTP, network, source/filesystem) included a valid
single-authenticated-context-plus-anonymous control and a missing-role declaration. All six matched
the expected identity judgment, using six native structured-output requests.

These smoke procedures explicitly distinguish one authenticated context from the anonymous control
and use smaller inputs plus production grounding/rubric text through the native role harness. They
are not a paired causal test and do not reproduce the original large context. They support a small
regression check, not a claim that the original continuation, authorship, or useful adoption is fixed.
No prompt was tuned after smoke results arrived. All outputs are preserved in `results.json`.

## Final verification

- Full repository coverage: **8,366 passed, 47 skipped, 89 deselected**.
- Every changed production file passes the 80% statement and branch floors, including the shared workflow file.
- Research projection/reproducibility checks: **10 passed**.
- Ruff and whitespace checks pass.
- **44 model requests**: 38 frozen comparison requests and six current-contract smoke requests; cap 45.
- All 44 inference requests used native structured schemas.

`validation.json` records source hashes, coverage, counts and logs. No production catalog, historical
feedback, running operation, or deployment was changed. Better end-to-end behavior remains unproven;
a fresh continuation after deploying the fixes is still needed to assess that.
