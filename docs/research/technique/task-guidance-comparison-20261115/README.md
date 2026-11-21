# Controlled guidance task comparison

The user authorized up to 100 new provider requests. This completed comparison used 62;
38 remain unused. No calls are running. An earlier diagnostic setup failure used zero requests
and is retained under `outputs/technique_review_trials/task-guidance-comparison-20261115`.

[Protocol](protocol.json) freezes 12 planned runs: three synthetic endpoint behaviors, with and
without the exact approved HTTP document, repeated twice with arm order reversed. Each run used
a fresh task agent and observation ledger. The model received the same known policy and request
tool in both arms; behavior labels and grading were hidden. No external targets, production
operation stores or global technique scores were accessed.

The three behaviors were invalid-value fallback with unauthorized valid-value selection,
correct authorization enforcement, and unauthorized disclosure with different status/shape.
Fixture-owned policy permits each principal to read only its own account. Python grades the
classification and verifies that cited observations exist and include an A-to-B request.
No model critic grades task success. This is not the production operation executor: it uses a
fresh Strands task agent, the existing local model factory/profile and native finalization bridge.

## Results

| Arm | Correct classifications | Correct with cited decisive observation | Provider requests | Observations |
| --- | --- | --- | --- | --- |
| Frozen guidance | 6/6 | 6/6 | 38 | 26 |
| No guidance | 6/6 | 6/6 | 24 | 24 |

[Exact decisions and observations](results.json) retain every planned run. Both arms correctly
identified all four vulnerable runs and both enforcing controls. Guidance used 58% more provider
requests, but only two more observations. This is request count, not token, monetary or wall-time
cost. Many no-guidance runs collected multiple observations within one model turn; the difference
is substantially request organization rather than greater evidence coverage.

The defective guidance did not produce a false negative here. In all four vulnerable guidance
runs, A-to-B disclosure was observed before the invalid-value control. Thus the model reordered
the procedure and had decisive evidence before the bad stopping rule could become decisive.
Guidance runs tested an invalid value in four of six runs, compared with zero of six without
it. This is an exploratory trace observation consistent with procedural influence, not proof
that each action was caused by the document.

No correctness benefit was observed. That does not establish no benefit in harder tasks: the
explicit policy and tiny fixture may create a ceiling effect. Two repeats, nonrandom case order,
no seed control and no context-length control limit causal inference. Techniques were supplied
directly, so lookup, voluntary adoption, retention and increased production use remain unmeasured.
The document's false-negative rule remains logically wrong even though these agents compensated
for it; this result does not validate its prose or justify making it mandatory.

## Verification and provenance

`test_fixture.py` tests both policy outcomes, invalid-value fallback, unknown principals,
evidence grading, complete offline harness execution, request exhaustion and transport restoration.
Eight tests pass, with 98% combined line/branch coverage and both files above 80% line and branch
coverage. Ruff and whitespace checks pass. The offline model is scripted, not a semantic judge.

The exact executed script is preserved in `executed-run.py.txt` with its hash in the protocol.
After completion, Ruff added import-group blank lines only. Model conversations stay private under
`outputs/technique_review_trials/task-guidance-comparison-20261115-2`. Research-only code and
records were added; production behavior, prompts and acceptance rules were unchanged.
