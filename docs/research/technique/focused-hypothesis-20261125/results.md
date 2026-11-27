# Focused hypothesis results

All 24 frozen trials finished using 26 of 64 allowed generation requests. No completed trial was
rerun. There was no target execution, production persistence, deployment or operation restart.

| Measure | Baseline | Candidate |
| --- | ---: | ---: |
| Trials | 12 | 12 |
| Trials with complete structured outputs | 10 | 10 |
| Failed trials | 2 | 2 |
| Generation requests | 12 | 14 |
| Source classifications matching the predeclared label | 7/8 | 8/8 |
| Authorization edit trials completed | 0/2 | 0/2 |
| Rendering edit trials completed | 2/2 | 2/2 |

## Semantic assessment

Both arms identified the unsafe DOM flow and excluded the safe text sink in both repetitions. The
baseline also identified the TCP shell sink, but in one repetition requested additional inspection
of transport binding instead of proposing a conditional test. Its explanation found the relevant
processing boundary; this is not evidence that the candidate recovered a sink the baseline missed.
Both arms requested inspection of the explicitly unexamined filesystem consumer. The difference in
classification labels is too small to establish a general source-inspection improvement, especially
because these fixtures supply short source snippets rather than requiring navigation of a large bundle.

Every authorization edit trial failed cross-field validation: an identity comparison was returned
without explicit roles. Native structured output did not prevent this semantic relationship failure.
The synthetic edit packet did not provide a declared-role catalog. Production dispatch now includes
the existing role catalog and task objective, but that context addition was not exercised by this
diagnostic. No claim of improved authorization-edit reliability follows from these results.

Rendering edits in both arms narrowed combined claims and added future prerequisites. Baseline
retained complementary prompt-injection and SSRF branches. Candidate independently narrowed both
records to prompt injection in both repetitions. This shows a possible loss of complementary context
when correcting related records independently. New production split support can preserve independent
proposals in a single atomic edit, but the diagnostic did not exercise that list input.

The candidate distinguished canary placement from literal reflection more explicitly in some records,
but still did not establish why following the submitted instruction would violate intended policy.
Baseline SSRF positives likewise treated a server-originated callback to a supplied destination as
SSRF without establishing prohibited destination policy. One baseline prompt-injection revision did
identify future confirmation of a restrictive system prompt, although its concrete controls remained
underspecified. These are partial edits, not demonstrated security-discriminating tests or findings.

## Contract and prompt verification

The final component suites passed 1,300 tests with 17 skips. Ten offline harness tests passed;
the driver has 98.9% statement and 95.0% branch coverage. Tests cover positive and negative HTTP,
TCP and filesystem examples, runtime-advertised split schema, atomic split failure, retained siblings,
one-record tool binding, stalled edits, fatal results, allowance exhaustion, continuation, cumulative
outcomes and final acceptance separation. Invalid structured or prose-only bound reviews retain a
controller failure envelope and do not become semantic approval.

The assembled-path audit covered initial production, prerequisite inspection, focused edit, acceptance,
retry, continuation and fitting paths. It corrected inherited acceptance-directed recovery instructions
inside edit dispatches and restored the task objective plus declared-role context. Shared semantic
guidance remains advisory; no new critic or semantic keyword gate was added. Required correction
records survive prompt fitting, and each revision still receives subsequent semantic review.

The semantic probe used research-specific output schemas with production structured transport, not
the complete execution controller or production tool calls. It supplied editing packets as data and
did not exercise actual acceptance, digest updates, inspection navigation or split application. Source
hashes and matching review/workflow snapshots preserve the probe version. Later recovery/context
changes and split support are covered by controller tests; `validation-hashes.json` identifies the
final code separately. Failure records retain diagnostics; rejected native response bodies were not
retained by this harness.

Final repository coverage completed with **8,405 passed, 47 skipped and 89 deselected**. All final
production and unit-test hashes match the frozen validation manifest. The per-file floor evaluator
explicitly selected all three modified production files, including uncommitted changes:

| Production file | Statement coverage | Branch coverage |
| --- | ---: | ---: |
| `hypothesis_review.py` | 99.0% | 98.4% |
| `multi_agent_workflow.py` | 90.8% | 85.1% |
| `hypotheses.py` | 100.0% | 99.2% |

Ruff and whitespace checks passed. Earlier coverage jobs that predated completed changes were stopped;
their partial measurements are not used here. The final result is a complete, successful suite run.

## Interpretation

The implementation establishes isolated, resumable correction bookkeeping and preserves the primary
evaluator failure. The paired diagnostic does not demonstrate better end-to-end effectiveness or
more reliable semantic edits. It also does not show fewer model requests: separate candidate edits
required additional calls. A controlled continuation should measure publication repair completion,
retention of complementary mechanisms, actual source-flow inspection and discriminating future tests.
Do not treat feedback sentiment, valid JSON or completion alone as correctness evidence.
