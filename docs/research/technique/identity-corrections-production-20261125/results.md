# Production-path continuation results

This study continues the isolated fixture operation `OP_FIXTURE_INTEGRATION_20261125` through
the actual production executor and semantic evaluator. It follows the
[replicated structured-output comparison](../identity-corrections-replicated-20261125/results.md),
whose Python adapter and availability-only acceptance did not exercise that complete path.

**Two cases were repaired; one evaluator miss remains.** The controller marked all three tasks done,
but the private-probe refutation publication stayed unchanged and still lacks its required negative
outcome. This is not a three-case semantic pass. The run used **36/96 requests**: ten before the
runtime fix and 26 during the same-operation continuation. Every request has a retained response.

The [matching-refutation follow-up](../refutation-review-20261125/results.md) preserves this miss and
tests updated shared guidance in a fresh study. Its outcomes and costs are separate from these results.

## Final outcomes

| Case | Continuation requests | Durable result | Manual adjudication |
| --- | ---: | --- | --- |
| Exact selector / unnamed accounts | 7 | Changed publication; approved | Uses `GET /records?record_id=…`, preserves `account/[]`, ownership and absent-sharing controls |
| Complementary rendering mechanisms | 17 | Two changed records; approved | Separates `user_text` template override from `url` destination-policy bypass; both mechanisms preserved |
| Private-probe refutation | 2 | Unchanged publication; approved | **Miss:** negative expectations still cover only public and invalid-URL controls |

The rendering evaluator initially retained the coarse retrieval sibling, then requested its repair
after approving the instruction-override edit. The second correction specifies the `url` field,
allowed-public control, owned-private probe, server-side retrieval evidence and a blocked-probe
outcome. Its previously repaired instruction-override sibling remains present. Neither edit executes
the proposed tests. The instruction-override negative-expectations list remains control-focused even
though the plan includes a probe comparison; approval is not a claim of perfect plan completeness.

The final refutation task explicitly requires a matching negative private-probe outcome in its frozen
criterion. Both production review calls nevertheless accept the seed. The semantic reviewer's reason
cites public and invalid-URL controls rather than addressing the absent private-denial outcome.
Requests 25–26 and the unchanged publication preserve this failure. No completed task was rerun to
replace the miss. Before considering live rollout, a follow-up should test whether the evaluator
checks every claimed consequence against a corresponding refuting observation across HTTP, network
and filesystem cases; this run does not establish that invariant.

After the process finished, reopening the same stores reproduced all saved statuses, publications and
review bindings exactly. Every approving review names its current publication digest, and the frozen
production source hashes still match. A correct digest binding establishes which plan was reviewed;
it does not make the erroneous refutation approval semantically correct.

Provider-reported totals were 58,624 input / 7,766 output tokens before the fix and 174,069 input /
21,378 output tokens during continuation: **232,693 input and 29,144 output tokens** overall. Summed
provider request duration was about **42.4 minutes**, including failed-attempt costs. Local inference
has no reliable monetary price recorded here. These costs do not support an efficiency claim relative
to the earlier adapter benchmark.

## Reproduced runtime defect

The initial frozen production attempt reproduced a serialization failure that the prior adapter
avoided. The worker produced a grounded selector correction but supplied the replacement as a
JSON-encoded object string. Strands rejected it before the revision function ran. A repeated rejected
submission ended the selector task without changing its publication. The attempt was stopped at ten
generation requests; its failures, requests, responses and source snapshots remain preserved.

The revision boundary now normalizes one JSON encoding of an object or nonempty list before existing
strict validation and atomic publication. Its advertised schema remains object/list/null. Malformed
JSON, encoded primitives and encoded null are rejected; explicit null remains withdrawal. This is a
runtime compatibility repair, not evidence of better semantic reasoning.

The same operation was reopened after a SQLite backup, without reseeding. A separate continuation
audit directory preserves the prior records. Ten initial requests and at most 86 continuation
requests share the original ceiling of 96. Completed tasks are not selectively rerun.

## Interpretation and boundaries

Native structured schemas constrain model reviews, prompt construction and acceptance decisions;
workers use real schema-advertised production tools. No Python benchmark adapter turns a model's
final answer into a mutation. The executor journal, controller correction state, acceptance tools,
publication digests and subsequent semantic reviews are all real.

Only retained-artifact, inventory and task-bound bookkeeping tools are configured. No real target
requests, browser or shell actions, credential provisioning, MCP assessment tools, deployment or
live operation restart occur. SQLite/Qdrant persistence is real; embeddings use deterministic fixture
vectors. Embedding quality and real-target vulnerability detection are outside this study.

These are conditional future test plans. Successful publication and availability acceptance do not
establish their semantic adequacy or prove a vulnerability. This is one three-case continuation with
one model, not a paired replicated comparison or evidence of a general efficiency improvement.

Raw records are retained under
`outputs/technique_review_trials/identity-corrections-production-20261125/`, with continuation
requests, executor outcomes, histories, results and before/after state in `continuation/`.

## Validation

The final repository coverage entry point completed with **8,436 passed, 47 skipped and 89 deselected**.
The separate fixture-driver suite passed **11 tests**. Ruff and `git diff --check` passed.
The repository floor evaluator was applied to every changed production Python file, including the
untracked new executor helper; all exceeded 80% statement and branch coverage.

| Production file | Statement coverage | Branch coverage |
| --- | ---: | ---: |
| `src/cyberautoagent.py` | 86.0% | 81.3% |
| `hypothesis_context.py` | 99.2% | 95.8% |
| `hypothesis_review.py` | 99.0% | 98.4% |
| `multi_agent_workflow.py` | 90.8% | 85.1% |
| `workflow_executor.py` | 100% | 100% |
| `security_interpretation.py` | 100% | 100% |
| `hypotheses.py` | 100% | 99.2% |

The research driver separately measured 96.7% statement / 80.0% branch coverage for `run.py` and
94.8% / 92.9% for `resume.py`. Tests cover direct object and encoded-object calls through the actual
Strands tool runtime, atomic split support, rejection without mutation, preserved pending repairs,
generic HTTP/TCP/filesystem executor cases, token/recovery/loop metadata and failure cleanup.
The continuation's `validation.json` retains the measured production coverage summary.
