# Clean activation and transport-failure results

**The clean fixture check passed.** The ambiguous plan received a revision request, the real worker
changed it, the real active-task acceptance tool succeeded, and semantic reevaluation approved the
changed publication. The complete counterpart was retained without mutation. Both tasks finished done.

| Check | Initial review | Execution result | Final review | New requests |
| --- | --- | --- | --- | ---: |
| Correlated policy-denial evidence; transport failures inconclusive | Retain | No revision needed | Done, unchanged | 2 |
| Error/denial/timeout incorrectly attributed to policy blocking | Revise | Revision and acceptance succeeded | Done, changed digest approved | 10 |

The initial revision reason identifies the missing non-transport signal needed to separate policy
denial from an inconclusive timeout/resource failure. The replacement explicitly states that a
timeout/reset/I/O error alone does not establish denial. It requires evidence that the private probe
reached the enforcement boundary and was denied, such as an explicit policy-block code/message,
alongside the public baseline and server-side retrieval evidence. The final reviewer recognizes this
distinction. No future probes were executed to obtain that approval.

## Production-path evidence

The journals contain successful `read_artifact`, `revise_hypothesis`, another `read_artifact` and
`record_task_acceptance` outcomes, with **zero failed tool invocations**. Acceptance reports
`complete=true` and `memory_published=true`; the active-task guard did not reject the call. The changed
publication SHA-256 is `9972d826e681c42105bb2fe4941bbd28fe738bab36d491378c82e5aa08a1808f`, matching its
final semantic review. Tool success and semantic approval were checked separately from done status.

Reopening the isolated store reproduced both saved task/publication/acceptance states exactly. All
frozen sources and fixtures match their hashes. The parent counter, results, repair record and frozen
manifest match their before-check hashes. The earlier acceptance rejection remains preserved and is
not overwritten or relabeled by this check.

## Requests and structured output

The check used **12 new requests**, including inspection, prompt preparation, revision, acceptance
and reevaluation. Together with the earlier 38, usage is **50/96** under the same original ceiling.
The new recorder's limit was 58; the parent counter remains historically 38. There were **zero
generation API failures**, and all **five native outputs** independently validate as JSON against
their recorded request schemas. Model reads did not access fixture-label snapshots.

Provider-reported totals are **75,939 input / 9,312 output tokens** and **915.3 seconds** of summed
request duration (about 15.3 minutes). No reliable monetary price is recorded. Raw requests/responses,
source snapshots, initial/final histories, journals, reopened state, `adjudication.json` and
`validation.json` are under `outputs/technique_review_trials/refutation-review-20261125/clean-check/`.

## Validation and instruction review

Repository coverage passed **8,436 tests**, with 47 skipped and 89 deselected. Focused checks passed
**14 tests**, with one skip. The seven new harness cases cover allowance exhaustion, duplicate-run
refusal, failure/prior-record preservation, reopening, real active-task acceptance and assembled
HTTP/TCP/filesystem review inputs.

Every changed production Python file meets the 80% statement and branch floors. Shared interpretation
has 100% statement coverage and no branches; the research driver has 93.3% statement / 87.5% branch
coverage. Ruff and `git diff --check` pass. Measurements and test logs are in the audit directory.

The distinction is consolidated in shared interpretation. Representative assembled inputs cover
initial builder/critic/revision, bounded repair, executor, evaluator, continuation and prompt fitting.
The rule appears once and survives optional context fitting. Available tools match required actions;
future execution is not required. No new output field, semantic classifier, controller gate,
configuration variable or database schema was introduced.

## Readiness and limits

This closes the specific fixture lifecycle and timeout-attribution gaps. It supports considering a
bounded live continuation after choosing its operation and explicit scope. **No live operation was
started, continued, deployed or restarted here.**

This is a small check with `muse-glimmer:30b-mlx`, synthetic current-operation evidence and deterministic
fixture embeddings. It validates conditional plans and workflow integration, not real target behavior,
policy enforcement or vulnerability absence. It is not a general semantic-accuracy or efficiency claim.
