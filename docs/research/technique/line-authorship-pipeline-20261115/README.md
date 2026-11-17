# Line-oriented authorship through generation and publication

This follow-up runs production alignment, source assessment, authoring and publication over the
same frozen network, filesystem and HTTP requests and retained sources. The candidate includes
the line-oriented structured author schema. SQLite/Qdrant and exports are isolated; retrieval
starts empty, embeddings provide local bookkeeping only, and no live research or target execution
occurs. This does not measure bundle planning, catalog retrieval quality or task usefulness.

All six exact-input scripted SDK checks passed in native and schema-tool modes. The first live
run used nine remaining requests from the original allowance. Network completed after six
requests; filesystem exhausted the allowance before authoring and HTTP was unattempted. Those
results are retained in [initial-results.json](initial-results.json), not discarded as invalid.

The user explicitly approved up to 21 additional sequential requests to finish the incomplete
cases. [Continuation inputs](continuation-cases.json) preserve the original filesystem and HTTP
hashes. They start with fresh isolated state; the completed network case is not rerun. Its
[exact document](generated-network.md) and recorded hash remain fixed. All three original cases
remain in the primary denominator, and the earlier budget failures remain visible.

The new documents remain pending human adjudication of their exact hashes. Pipeline approval,
assistant technical observations and observed task benefit are separate outcomes. Prior
authorship-only drafts have an [assistant review](prior-draft-review.json); that review is not
an acceptance judgment for this full-path run's documents.

## HTTP review observations

The first HTTP draft contains a false-negative stopping rule: an unchanged response to an
invalid header value is treated as proof that the header is not a selector. A pure-function
[counterexample](http-first-draft-counterexample.json) shows invalid-value fallback to the
authenticated principal alongside valid-value selection of another principal's object. This
uses no target access and makes no claim about an agent's observed detection rate.

The [first publication review](http-first-publication-review.json) requested explicit policy
and object-attribution conditions that were already present in its [exact subject](http-first-review-subject.json).
Its correctness issue also conflated changed object content with equal response structure;
different bodies can have the same structure. The observation about over-reliance on status
and shape is useful, but these reasons do not accurately describe all existing draft conditions.

The [second review](http-second-publication-review.json) demanded an internal passage ID in
the published prose. The frozen `core:attribution` criterion requires body placement only when
explicitly requested; this request did not require it. Reviewer evidence belongs in the issue's
`passage_ids`. The assembled input also tells the reviewer to cite source passage IDs for
attribution verification. The output demonstrates conflation of reviewer evidence with document
requirements; that adjacent instruction is a plausible contributor, not an established cause.
No prompt, contract or stage was changed during this frozen run.

## Completed results

[The combined record](results.json) retains all original cases and budget interruptions.

| Case | Pipeline outcome | Author calls | Publication reviews | Attribution repairs |
| --- | --- | --- | --- | --- |
| Network | Completed | 1 | 1 | 0 |
| Filesystem | Completed after budget restart | 1 | 1 | 0 |
| HTTP | Completed after revisions | 2 | 3 | 1 |

All three completed documents have readable multiline bodies and no observed message token.
Exact documents are [network](generated-network.md), [filesystem](generated-filesystem.md) and
[HTTP](generated-http.md). Conditional pipeline completion is 3/3. Human-accepted useful-document
yield remains unknown; the HTTP document needs technical correction before being relied upon.

This full-path check used 25 requests: nine in the first run and 16 in the continuation, including
three filesystem requests interrupted before authoring. Cumulative usage with the preceding pilot
and author-only check is 46 of 51 authorized requests; five remain unused. No calls are running.
The extension was used only for the two budget-incomplete cases. No completed case was retried
to improve the outcome, and no global technique catalog or operation scores were updated.

### Approval did not resolve the HTTP defect

The HTTP procedure body is byte-identical between the second and final review subjects after
trimming document edges. Only `metadata.sources[0].selection_reason` changed; the exact
[metadata delta](http-metadata-delta.json), [second subject](http-second-review-subject.json),
[final subject](http-final-review-subject.json) and [final decision](http-final-publication-review.json)
are retained. These were not identical complete review inputs: source metadata and derived
snapshot references changed, and model investigation notes could differ.

The final reviewer returned no issues. The approved body still contains the invalid-header
stopping rule disproved by the pure-function counterexample. It was not corrected by the
attribution repair. Approval of that subject therefore does not establish correct interpretation.
This separates improved formatting/completion from unresolved semantic review consistency.

The source-metadata repair did preserve the procedure rather than generating it a third time.
Earlier progress observations that described three author attempts were corrected against the
typed provider schemas and role events: there were two author calls and one attribution repair.

### Technical reading and limits

Network provides an independent policy matrix, identity binding, controls and correlated evidence.
Its state-table requirements need tailoring to the actual enforcement mechanism. Filesystem
explicitly separates intended policy, process identity, object state and allowed/denied attempts;
no material error was identified in this limited reading. Neither conclusion is human adjudication,
full factual verification or observed task benefit.

The useful next task comparison targets the HTTP counterexample: a vulnerable selector with
invalid-value fallback, alongside an enforcing control, each with and without the frozen guidance.
Grade classifications and task-local observation coverage against fixture-owned policy and
behavior. That would measure whether an agent follows, ignores or compensates for the bad rule.
The logical counterexample alone cannot establish its empirical effect on an agent.

## Verification

All ten exact-input scripted SDK checks passed: six for the initial three cases and four for
the two continuation cases, across native and schema-tool modes. The generation harness tests
passed 37 tests. Pure fixture assertions verified the recorded counterexamples, exact generated
file hashes matched the controller outcomes, and whitespace checks passed. This turn changed
research records only; no production prompt, code, schema, module catalog or task score changed.
