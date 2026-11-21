# Production executor-core comparison

This pilot used 31 of the newly approved 100 generation requests; 69 remain unused. No calls
are running. Four cases were frozen before dispatch: unavailable policy with lookup/no lookup,
and permitted shared access with no lookup/lookup. Each case had fresh isolated SQLite state,
an agent, callback journal, conversation manager and artifact directory.

## Production paths and deliberate boundaries

The pilot invokes actual `create_agent` with the `task_executor` profile, the master system prompt,
controller acceptance/executor/tool-selection instruction fragments, production outcome recording,
failure recovery and terminal hooks, `run_agent_until_terminal_state`, and the real bound
`record_task_acceptance` tool. Accepted evidence is snapshotted and persisted through the normal
SQLite acceptance code. Byte-for-byte checks verified each original artifact against its immutable
acceptance snapshot. Artifact availability is not treated as semantic correctness.

This is an executor-core pilot, not a full operation. Task assignment is fixed rather than built
by a model. It excludes controller scheduling, model task-prompt criticism/evaluation, phase/task
status transitions and whole runtime initialization. Shared runtime resources are assembled for
isolation; operation-global runtime hooks are not all installed. Production agent construction
adds the terminal, outcome-recording and failure-recovery hooks. Qdrant publication is replaced
with a local no-index adapter; task evidence updates still use real SQLite persistence.

Endpoint I/O is the same synthetic fixture used in the discovery study. Lookup is a controlled
adapter returning the exact frozen HTTP document, not semantic catalog search or new generation.
The lookup arm makes `get_technique` available without requiring a call or inserting the document
into the initial task prompt. The other arm omits that tool. Only registered fixture tools are
available; no external assessment target is accessible. Global scores/catalogs are unchanged.

## Results

| Case | Lookup available | No lookup | Generation requests, lookup / none |
| --- | --- | --- | --- |
| Policy unavailable | Correct inconclusive, ledger recorded | Correct inconclusive, ledger recorded | 8 / 8 |
| Shared access permitted | Correct no violation observed, ledger recorded | Correct no violation observed, ledger recorded | 9 / 6 |

All four runs reached `task_executor_done`, published a result artifact and persisted an acceptance
ledger. All four classifications matched the fixture oracle and cited identity, policy and
cross-account request observations. Both lookup-enabled agents voluntarily consulted the frozen
guide once. The lookup arm used 17 requests overall versus 14 without it. This is a request-count
comparison, not token, monetary or duration cost.

[Results](results.json) bind decisions, observations, lookup requests, acceptance entries and artifact
hashes. Exact artifact copies are linked by filename in that record. Full journals, assembled
inputs, SQLite databases and source/snapshot artifacts remain private under
`outputs/technique_review_trials/production-executor-20261115`.

No classification benefit appeared in these two pairs. The earlier custom-harness uncertainty
advantage did not recur here. Production prompting, model role/profile, terminal contract and
resource setup differ from that earlier study, so the reason cannot be assigned to one factor.
Two voluntary consultations demonstrate use in this curated tool setting, not an increased
production adoption rate or actual catalog retrieval quality. Consultation alone does not prove
every procedural step was applied or that the guide caused the conclusion.

Some shared-access explanations emphasize the misleading error label and underdescribe the
returned allowed object. Correct labels and cited records do not establish every explanatory
sentence. The oracle score checks classification and observation coverage; it does not silently
upgrade ledger acceptance into full semantic approval. The frozen guide's known false-negative
rule is unchanged and remains a defect despite these successful task labels.

## Verification and reproducibility

Nine offline tests passed. They exercise direct acceptance/snapshot persistence, scripted real
SDK tool execution, production terminal policy and journal recording, optional lookup, the global
request cap, incomplete-case retention and restoration of the provider transport. Scripted SDK
preflight made zero live generation requests. Both research Python files exceed 80% line and
branch coverage; combined coverage was 95%. Ruff and whitespace checks passed.

The exact executed script is retained as `executed-run.py.txt`, with its hash in [protocol.json](protocol.json).
After execution, one protocol-description string was line-wrapped to meet the line limit;
behavior and model inputs were unchanged. No production code changed in this pilot. Existing
full repository validation remains separately recorded; it was not rerun for research-only code.

The next useful comparison should test missing technical knowledge and actual catalog delivery,
with a production controller/evaluator path preflight. These simple method-comparison fixtures
already have strong baseline performance. More mandatory lookups or more critic rules are not
supported by these observations.
