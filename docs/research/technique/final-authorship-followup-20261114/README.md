# Final implementation authorship follow-up

The candidate is frozen in [protocol.json](protocol.json), with unchanged HTTP, network and
filesystem requests and retained sources in [cases.json](cases.json). These cases cover more
than the HTTP-focused operation replay. Authors receive no reference drafts or prior judgments.

The [exact-input preflight](preflight.json) passed all six scripted SDK checks: three cases in
native mode and three in schema-tool mode. These checks made zero live provider requests and
establish transport/controller compatibility only, not semantic quality.

The proposed live run permits at most 30 sequential provider requests, including retries and
repairs. Every case stays in the denominator if the allowance is exhausted. It uses isolated
SQLite/Qdrant and exports, with no active-operation or global-technique writes. Full review
subjects, rejected drafts and sources are retained; final documents await human adjudication
of their exact hashes. The user authorized the 30-request allowance on November 14, 2026.

The first invocation recorded three connection failures before producing content. Those
attempts remain in the total. A [retry protocol](retry-protocol.json) limits the retry to the
remaining 27 requests. Initial records are retained under
`outputs/technique_review_trials/final-authorship-live-20261114-1`; the retry uses the adjacent
`final-authorship-live-20261114-2` directory. No failed attempt is discarded or refunded.

Primary outcome: human-accepted complete useful documents divided by all three requests.
Separately report pipeline approval, failure stage, repair ownership and provider requests.
Historical results (one useful document in the original pilot, no completed documents in the
next capped follow-up) are descriptive comparisons only. Runtime and model differences prevent
causal attribution. This is a conditional authorship experiment, not a matched historical trial.

## Completed live results

[Results](results.json): two of three cases completed the pipeline. Network used six provider
requests, filesystem five, and HTTP four. Including the three initial connection failures,
18 of the authorized 30 requests were used; 12 remain unused. All cases were attempted.
HTTP failed during structured author output after one truncation recovery, before publication
review. No complete HTTP document was returned; this was not a semantic document rejection.

Exact generated documents are retained as [network](generated-network.md) and
[filesystem](generated-filesystem.md). Both remain pending human adjudication. Neither required
a document-author repair after publication review. The successful candidate path reached
authoring from the frozen source pools that blocked earlier historical runs, but this small
sample cannot identify which code or model change caused the difference.

[Output observations](output-observations.json) record visible defects in both completed
documents: their bodies are collapsed onto one line and contain a literal `<|message|>` token,
so Markdown headings, lists and code do not render as intended. The network document also
includes illustrative Python that loads JSON but looks up policy entries by tuple keys without
normalizing that schema. Its example omits identity-attestation and time checks required by the
surrounding method, although the draft calls the example illustrative and asks for adaptation.
These are assistant observations, not human usefulness judgments. They show that pipeline
approval is insufficient evidence of polished, directly usable authorship.

Conditional pipeline completion is 2/3. Human-accepted useful-document yield is still unknown.
This is encouraging evidence for obtaining drafts, with continuing output-quality and truncation
problems. Actual agent application, increased use and task benefit were not measured.

[The formatting investigation and line-oriented author check](../authorship-formatting-20261115.md)
subsequently traced the single-line body before assembly and changed the author wire schema.
Its three successful author calls are separate from this experiment's publication outcomes;
they do not change this run's 2/3 pipeline result or pending human judgments.

## Measuring increased use separately

A later comparison should freeze the model, task inputs, tool capabilities and technique catalog,
then alternate task runs with and without access to the delivered guidance. Retain all planned
tasks and report lookup, delivery, artifact-backed application and task acceptance separately.
Successful lookup is availability, not application; a reviewer usefulness score alone is not
proof of task benefit. Keep prior-operation evidence advisory and require fresh task-local
execution evidence. These task runs are not part of the proposed authorship allowance.

A true comparison between old and final implementations additionally requires a preserved old
runtime, identical sources/catalog and repeated runs. The existing 18-case results do not supply
that control. Do not relabel a new candidate-only run as a matched comparison.
