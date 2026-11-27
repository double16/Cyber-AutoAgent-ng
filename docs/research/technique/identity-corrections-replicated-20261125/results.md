# Replicated correction results

The fresh comparison completed both repetitions without budget-capped trials. It used **36 of 96 allowed
requests**: candidate completed **12/12** trials and baseline **10/12**. Baseline failed both required
unnamed-role account comparisons on its hypothesis-only explicit-role restriction. Candidate preserved
`comparison=account, roles=[]` and two future account sessions in both repetitions.

This demonstrates the narrow identity-contract compatibility fix. It does **not** establish general
reasoning superiority, consistently separated consequences, or fewer requests. No target test, live
operation continuation, deployment or restart occurred.

A subsequent [isolated production-path continuation](../identity-corrections-production-20261125/results.md)
exercises the real worker runtime and semantic evaluator omitted here. Its request costs and runtime
serialization failure are recorded separately; they do not change this frozen study's measurements.

## Frozen protocol

The six fixtures now explicitly require two-account comparisons, use role catalogs matching the
observations, identify linked sources with canonical `artifact:` references, and supply the complete
safe filesystem consumer under a static, filename-only attacker model. The rendering fixture establishes
an LLM processing path and protected template regions; the URL fixture limits future probes to owned
canaries. These changes are common to both arms and were frozen before any generation.

Both arms use the production correction dispatcher and real publication/revision tools with isolated
SQLite persistence. Baseline uses archived pre-change dispatcher/guidance and its own proposal schema;
candidate uses current code/schema. The initial publication is a common externally supplied review
input, including the unnamed comparison that baseline's own producer could not publish.

Model decisions are structured. All evidence is inline except the explicitly declared DOM consumer;
that case uses the real scoped reader before native finalization. Python applies the typed result
through the bound revision tool. Availability-only acceptance is deterministic and uses **zero model
requests**; it is not the production semantic acceptance evaluator. One schema-validation repair is
allowed per trial. Both baseline unnamed-role trials failed after that repair, remained pending, and
left their initial publications unchanged. No completed trial was selectively rerun.

The entire 24-trial matrix, both schemas, manual adjudication rubric, source resources and dependencies
were frozen before generation. Final hashes match the frozen manifest. This is a new 96-request study;
the preceding study's 96 attempts remain retained historical costs, not overwritten or relabeled.

## Completion, requests and retained evidence

| Measurement | Baseline | Candidate |
| --- | ---: | ---: |
| Planned and started trials | 12 | 12 |
| Durable revision plus availability completed | 10 | 12 |
| Failed trials retained | 2 | 0 |
| Generation requests, including repairs | 18 | 18 |
| Capped or unstarted trials | 0 | 0 |
| Real DOM consumer reads | 2/2 | 2/2 |
| Original rendering sibling preserved | 2/2 | 2/2 |
| Unsupported filesystem escape withdrawn | 2/2 | 2/2 |
| Model requests for availability acceptance | 0 | 0 |

| Case | Baseline requests, repetitions 1 / 2 | Candidate requests, repetitions 1 / 2 |
| --- | ---: | ---: |
| Named account comparison | 1 / 1 | 1 / 1 |
| Unnamed account comparison | 2 / 2, both failed | 1 / 1 |
| Complementary rendering | 1 / 1 | 1 / 1 |
| Permitted URL fetching | 1 / 1 | 1 / 1 |
| Linked DOM consumer | 3 / 3 | 4 / 4 |
| Safe filesystem containment | 1 / 1 | 1 / 1 |

Candidate's additional DOM request in each repetition reads the publication as well as the necessary
consumer. On the ten cases completed in both arms, baseline uses 14 requests and candidate 16. Raw
arm totals are equal because baseline also spends four requests on the two failed unnamed comparisons.
Neither accounting supports a general request-efficiency claim. The main protocol saving is directly
observable: availability bookkeeping no longer invokes a model, and fully supplied cases no longer
force a redundant investigation pass.

## Semantic adjudication

**Identity declarations.** Named-role edits in both arms preserve `account/member` and compare the same
unshared protected record under two distinct future member sessions. Candidate's unnamed-role edits
preserve `account/[]`, do not invent roles, and retain credential establishment as a future prerequisite.
The two baseline failures specifically reproduce the removed cross-field restriction rather than a
transport or JSON-string argument failure. This is a contract-compatibility result, not proof that the
candidate reasons better. One unnamed candidate edit assumes `GET /records/{id}` despite the fixture
not establishing that selector syntax; interface details must remain observed or conditional.

**Complementary mechanisms.** Both arms preserve the original retrieval sibling in both repetitions and
replace marker reflection with protected-template controls. Candidate's first edit stays focused on
header/footer modification. Its second, and both baseline edits, also mix prohibited retrieval into
the current instruction-override record. Thus mechanism loss from the earlier probe is not reproduced,
but consistent consequence separation is not demonstrated. No atomic split was selected in this study;
split success/failure remains covered by component tests. The rendering fixture corrects one record
beside a retained sibling; it does not measure two simultaneously pending complementary edits.

**Permitted fetching.** All four edits distinguish public callbacks from prohibited private owned-canary
access. They require evidence of server-originated private access rather than treating a callback alone
as vulnerability proof. One candidate repetition lacks a matching negative expectation for the private
probe being blocked; its negative list instead covers public and invalid-URL controls. That plan remains
incomplete on refutation despite a grounded positive consequence.

**Linked source navigation.** All four DOM trials actually read `artifact:render.js` through the scoped
reader and receive the `innerHTML` implementation. Their output connects the URL input to that boundary
and requires observable script-execution side effects rather than reflection. No trial calls the source
missing. The improvement relative to the ambiguous earlier fixture occurs in both arms and cannot be
attributed to candidate guidance. These are conditional plans, not executed DOM findings.

**Safe containment.** All four filesystem trials withdraw the escape premise based on the complete
trusted-root `resolve()`/`relative_to()` consumer and static filename-only control. Neither arm mistakes
permitted in-root reads for an escape or invents a race outside the supplied attacker model. Again, the
clearer common fixture improves both arms; it does not demonstrate a candidate-specific advantage.

Manual case-level verdicts and references are saved in `adjudication.json`. Bookkeeping measurements
come from actual records and read receipts; semantic judgments are not inferred from JSON validity,
feedback sentiment, keywords or successful tool calls.

## Validation and practical limits

The expanded component suite passed **1,421 tests**, with **17 skips**. All **29** new offline driver
tests pass; driver coverage is **98.4% statement and 93.5% branch**, exceeding both 80% floors. Tests
exercise both arms' runtime schemas, schema repairs, unchanged failed publications, atomic structured
inputs, reader scope rejection, deterministic availability, counter isolation, stream/transport failures,
and freeze/matrix integrity. Ruff and whitespace checks pass.

The assembled-path review covers direct structured finalization, linked-source investigation followed
by native finalization, schema-only repair, bound mutation, deterministic availability, and the production
prompt-fitting path. Baseline and candidate use their respective field descriptions and validation.
The current correction record and concerns remain required; sibling context remains advisory.

Production source is unchanged by this follow-up. The benchmark invokes the real correction dispatcher
and mutation tools, but its structured-output adapter and availability stub do not validate the complete
production executor or semantic acceptance evaluator. Two repetitions over six small synthetic cases, including one directly linked consumer, are a bounded
diagnostic, not a statistical generalization or a large-bundle recovery benchmark. No actual security tests or credential provisioning were executed.

The results support the identity-contract fix and justify a bounded, monitored integration continuation
as a next validation step, retaining ordinary semantic review. They do not justify bypassing review or
claiming all proposals are execution-ready: selector assumptions, mixed mechanisms and refutation
controls still need case-level review. This task does not start that continuation.

The frozen-source repository coverage entry point passed **8,414 tests**, with **47 skips and
89 deselections**. Every currently changed production Python file, including prior worktree changes,
passes both 80% floors:

| File | Statement | Branch |
| --- | ---: | ---: |
| `hypothesis_context.py` | 99.2% | 95.8% |
| `hypothesis_review.py` | 99.0% | 98.4% |
| `multi_agent_workflow.py` | 90.8% | 85.1% |
| `security_interpretation.py` | 100.0% | 100.0% |
| `hypotheses.py` | 100.0% | 99.2% |

`validation.json`, `harness-coverage.json` and `integrity.json` retain source/test hashes, coverage,
complete-matrix checks, input-publication equivalence and request accounting beside the raw trials.
The final source, fixture, rubric and schema-resource hashes match the pre-generation freeze.
