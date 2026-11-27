# Matching refutation review results

This study follows the [preserved evaluator miss](../identity-corrections-production-20261125/results.md).
The exact unchanged HTTP proposal is retained in
`tests/fixtures/hypothesis_refutation/recorded-http-miss.json`. Its incomplete plan has a successful
public baseline and an invalid-input control, but neither describes policy enforcement on the private
probe that would refute its claimed destination-policy violation.

The shared security interpretation now asks for a supporting and matching refuting observation of
the same probe for each claimed consequence. The hypothesis rubric routes an absent distinction
through the existing `test_discrimination=revise` and reason. It accepts the distinction anywhere in
the plan, requires no particular wording, and does not require future tests to be executed now.
No schema, controller semantic classifier, critic stage or runtime gate was added.

## Review outcomes

All **12/12 reviews** matched the frozen fixture judgments: six incomplete plans received revision
requests and six complete counterparts were retained. Both repetitions caught the exact previously
missed HTTP plan. Its content matches the old approved publication after excluding its generated ID.
Each incomplete-plan reason identifies the missing observation for the same probe; it does not merely
request more evidence or reject the conditional mechanism. Complete-plan reasons identify the matching
refutation already supplied. This is a narrow follow-up result, not a general accuracy guarantee.

| Case | Repetition 1 | Repetition 2 | Requests, 1 / 2 |
| --- | --- | --- | ---: |
| HTTP, missing private-probe refutation | Revise | Revise | 4 / 3 |
| HTTP, complete counterpart | Retain | Retain | 2 / 2 |
| TCP gateway, missing private-session refutation | Revise | Revise | 2 / 2 |
| TCP gateway, complete counterpart | Retain | Retain | 2 / 3 |
| Filesystem, missing outside-root refutation | Revise | Revise | 2 / 2 |
| Filesystem, complete counterpart | Retain | Retain | 2 / 3 |

The review matrix consumed **29 generation requests**, including retained-artifact inspection turns
and native finalization. All 12 native review outputs independently parse as JSON and validate against
their actual recorded request schemas. Review digests bind the corresponding saved publications.
Incomplete plans intentionally end this stage as `partial_failure`, with repair instructions; these
are correct revision outcomes rather than failed transport trials. Complete plans end as `done`.

## Repair and lifecycle finding

The preregistered repair consumed another **nine requests**, for **38/96 overall**. The real worker
successfully revised the publication, adding a negative private-canary outcome, and the production
semantic evaluator approved the changed digest. The task finished `done`. However, this is **not a
clean live acceptance-tool pass**: the acceptance invocation was rejected because the harness had
not activated the task before entering the production runner. The ordinary workflow owns that
transition; the fixture entry point omitted it. The controller's semantic review subsequently approved
the publication using the existing availability ledger. Its done status does not erase the failed call.

After the process exited, the harness was fixed to activate the pending task first. A regression test
uses the real acceptance tool to demonstrate rejection before activation and success after activation.
The final offline harness suite passes 13 tests. The completed live case was not rerun. Its rejection,
original frozen driver, revision journal, acceptance ledger and final semantic review remain preserved.
`harness-lifecycle-fix.json` records the exact one-line driver change and both hashes; production and
fixture hashes did not change. The fixed lifecycle has offline coverage, not a new live-model result.

A later [clean activation check](../refutation-clean-check-20261125/results.md) exercises the corrected
lifecycle in a new isolated operation using 12 of the remaining requests. It preserves this study's
rejected acceptance call and closes the live fixture-validation gap separately.

The added negative outcome includes error/denial/timeout with no returned private data and attributes
that to policy blocking. It addresses the previously absent same-probe branch, but error or timeout
alone cannot establish policy enforcement rather than a transport failure. The retained evidence still
needs to distinguish those causes during future execution. This qualification limits the repair's
semantic approval; it is not executed proof of safe destination enforcement.

All **38 requests returned responses**. All **15 native outputs**, including prompt preparation and
final reevaluation, independently validate as JSON against their recorded schemas. No API or native
JSON/schema failures occurred; the inactive-task acceptance rejection is counted separately as a
failed tool invocation. Provider-reported totals are **254,276 input / 24,925 output tokens** and about
**42.4 minutes** of summed request duration. No reliable monetary inference price is recorded.

Reopening both isolated stores reproduced the saved task, publication and acceptance state exactly,
including the repaired task's final state. Every saved semantic review matches its publication digest.
The complete audit is under `outputs/technique_review_trials/refutation-review-20261125/`, with
`adjudication.json`, `validation.json`, raw requests, source snapshots and repair journals. The prior
study and its costs remain unchanged. No live assessment operation was continued or restarted.

## Frozen method

Six fixtures pair incomplete and complete HTTP, TCP-gateway and filesystem plans. Each complete
proposal differs only by an added matching refuting observation. Case identifiers are neutral and
expected verdicts remain outside model inputs. Two independent stores run all six cases in opposite
orders. Sources, fixtures, request ceiling and one preregistered HTTP repair are frozen before any
generation. Every model request, including artifact inspection, schema repair and final reevaluation,
counts against the same 96-request ceiling.

Reviews use the real production evaluator, native structured finalization, bound schema, controller
normalization and SQLite/Qdrant persistence. One pending HTTP task then receives the real production
executor/revision/acceptance path; completed tasks are not rerun. Scripted offline verdicts test
transport and routing, not model semantic ability. No real target or execution tool is available.

The first sandbox preflight was blocked from the configured private-network inference service before
generation or study creation. The permitted inference connection then ran the fresh study. The earlier
study's missed review and request costs remain unchanged and separate.

## Instruction review

Representative assembled inputs cover initial prompt building, critic/revision and bounded repair,
executor, evaluator, retained continuation, focused correction and prompt fitting. The shared rule
appears once, available tools still match required actions, future execution remains prohibited, and
the rule survives optional-context fitting. Existing semantic verdict/reason fields remain authoritative;
Python checks routing, review completeness and publication provenance rather than interpreting prose.
The live requests retain the complete actual instructions and schemas for further inspection.

## Validation

The repository coverage entry point passed **8,436 tests**, with 47 skipped and 89 deselected. The
first sandboxed attempt had 28 failures and three setup errors caused by blocked local servers,
loopback/interface access and a user cache. Repeating the same command with the required permissions
passed; no source changes were made between those runs. The research harness separately passed
**12 tests** before generation and **13** after the lifecycle regression was added; 29 focused
harness/assembled-input/continuation cases passed before generation.

Every changed production Python file, including the untracked shared executor helper, meets the
80% statement and branch floors. The two production files changed in this follow-up measured
`security_interpretation.py` 100% statement / 100% branch and `hypothesis_review.py` 99.0% / 98.4%.
The final research driver measured 95.6% statement / 91.7% branch coverage. Ruff and whitespace checks
passed. The study's `validation.json` retains all per-file production coverage summaries.

## Limitations

This is a small single-model follow-up, not a broad quality or efficiency comparison. There is no new
baseline arm; the prior exact incident is historical evidence. Expected fixture verdicts are authored
judgments, not a deterministic classifier. A hypothetical probe's negative outcome does not disprove
every possible exploitation path. Tests measure the proposed conditional plan and its stated policy.
Deterministic fixture embeddings do not assess embedding quality, and no vulnerability was executed.
The live complete counterparts place their matching refutations in `negative_expectations`; an
offline test also confirms that storing it in test steps introduces no structural schema requirement.
This does not establish model consistency for every equivalent field placement or multi-consequence plan.
