# Full task-controller and catalog check

> November 16 correction: the historical run below used a filesystem subject and inherited the
> module's reconnaissance lookup context. Its negative adoption result is not a fair test of the
> verification method. See the [follow-up](../controller-followup-20261116/README.md) for corrected
> assignment metadata, recommendation readiness and current results. Historical outcomes are retained.

The entry point is `run.py`. It reuses the isolated fixture I/O and production executor-core
harness, then invokes the actual `MultiAgentWorkflowController._run_task`, production prompt
builder/critic and task evaluator, real state transitions and the real SQLite acceptance ledger.
Lookup-enabled cases construct an isolated real web-module technique catalog through
`build_service`; normal task-technique binding, semantic selection and local use receipts apply.
All provider generation calls share the remaining 69-request ceiling. Whole-operation planning
and external target I/O remain excluded. Active operation stores and global catalogs are not used.

## Offline readiness

The scripted full-controller path reached task status `done`, including prompt construction,
SDK executor calls, immutable acceptance artifacts, evaluator decision and persisted transition.
The source fixture and unknown-policy conditions are not supplied to the model as expected
answers. Offline responses are explicit scripted specimens, not semantic judgments.

A separate mocked-catalog branch verifies directory isolation and closing the catalog client.
The focused document/core/controller suite passed 93 tests; the new controller harness has 95%
combined coverage, with both files above 80% line and branch coverage. Ruff and whitespace checks
pass. Native provider generation is not used for these offline checks.

The repository test entry point also passed 8,113 tests, with 46 skips and 89 deselections;
selected Python files meet the 80% line and branch coverage floors.

The fixture filesystem target is consistent across the plan, runtime system instructions and
memory scope. The earlier executor-core synthetic URL is not carried into that assignment.

## Live state

Automatic approval review initially rejected the embedding preflight because endpoint trust
was not established. The user then explicitly approved transmission of module technique Markdown
and retrieval queries to `http://192.168.253.101:11434` (`mxbai-embed-large:latest`).
The approved preflight succeeded: 64 revisions were indexed in an isolated local catalog and
semantic search returned five results. This proves availability, not semantic support or benefit.

The live run and approved extension finished. See [retained results](live-results.json) and
[catalog preflight](catalog-preflight.json). All cases used the real task controller, production
prompt builder/critic, executor, SQLite acceptance ledger and recovery paths; successful
publications also reached the evaluator. No whole-operation planner or external target was run.

| Policy condition | Lookup available | Requests | Controller outcome | Independent fixture grade |
| --- | --- | ---: | --- | --- |
| Unknown | Yes | 43 | Partial failure; no accepted artifact | No classification published |
| Unknown | No | 18 | Done; inconclusive artifact accepted | Label correct; decisive probe absent |
| Shared | No | 24 | Done; inconclusive artifact accepted | Decisive probes present; label wrong |
| Shared | Yes | 45 | Partial failure; no accepted artifact | No classification published |

The original run used 69 requests, including eight requests in a cap-interrupted shared/no-lookup
attempt. An explicitly approved 100-request extension used 69 more, restarting only that
interrupted case and the unattempted case. Completed failures were retained without rerunning.
Total use was **138 of 169 approved requests**, leaving **31 unused**. The table contains 130
requests for final attempts; the interrupted attempt accounts for the other eight.

The unknown-policy lookup case called `get_technique` once. Its fixture-specific request entered
research and exhausted three batches without resolving a component: the selected introductory
OWASP content did not support the requested fixture-specific methodology. It returned an explicit
error, not a usable technique. The shared-policy lookup case never called `get_technique`; it made
33 tool-catalog calls and stalled. Both isolated catalogs retained 64 revisions and zero use
receipts. The successful direct preflight ranked the corrected header-selector document first,
with its exact published hash; this does not establish that either executor selected it.

Neither lookup-enabled case delivered an accepted artifact. Neither no-lookup case passed the
complete independent supported-correct grade. Controller `done` here reflects the frozen
artifact-publication criterion, not an independent guarantee of probe or classification quality.
These four cases provide **no evidence of improved end-to-end effectiveness or increased useful
technique adoption**. They do not overturn the narrower author-format or executor-core findings.

Limitations: one case per condition and arm; synthetic filesystem fixture tools with sparse
parameter descriptions; generated task instructions sometimes interpreted observation IDs as
selector values. Tool discovery repeatedly returned a narrow catalog, so catalog registration
and fixture-interface fidelity need review before treating this as a real-target efficacy result.
The research failure is partly a domain mismatch: a reusable web technique cannot document
private fixture identifiers. No stricter prompt or additional critic was added during the run.

Raw events, SQLite/Qdrant data and original/extension manifests remain under ignored
`outputs/technique_review_trials/full-controller-20261115` and
`outputs/technique_review_trials/full-controller-extension-20261115`. The extension invocation is
retained alongside its manifest. Active-operation secrets and target artifacts were excluded;
no global catalog was imported or modified.
