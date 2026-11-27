# Reviewed authoring examples and advisory excerpt delivery

A reviewed example improved some authoring semantics in one pair. Supplying a selected guide excerpt
also produced two supported task artifacts and two helpful feedback judgments. Voluntary lookup
remained unused. These are promising observations for simpler delivery and better authoring input,
not evidence of reliable end-to-end improvement across operations.

## Authorship comparison

Both arms replayed the same previously validated source packet through the production
`author_and_improve` helper. The no-example first model input was byte-for-byte identical to the
previous observation's first author input. The second arm changed only `existing_examples`, adding
the first 6,000 characters of the reviewed object-level authorization module guide through the
existing production example format. Instructions, critic brief, retained sources, request and
schemas were held constant. No source research or new semantic gate was added.

Each arm completed with two author calls and one advisory critic invocation, using four provider
requests including critic investigation. The critic did not veto authorship or re-review the
revision. This experiment exercised authorship assembly directly, not catalog publication or reuse
of these two new documents.

| Check | No example | Reviewed example |
| --- | --- | --- |
| Policy-specific denied entitlement | Weak: non-ownership becomes a denial shortcut | Explicitly excludes sharing, delegation, public visibility and role grants |
| Confirmation requires 2xx | Yes, incorrectly excluding disclosure in other response classes | No blanket 2xx condition |
| Invalid-value behavior becomes a confirmation prerequisite | Still present | Still present |
| Lookup request describes the method | Yes | Yes; also adopts the example's broader phase organization |

The invalid-value condition existed in each first draft and survived advice and revision. A valid
selector returning protected data contrary to established policy is not invalidated merely because
an invalid selector also returns that data. The example improves grounding of the method but does
not establish general semantic reliability. Review was manual and unblinded, against criteria
frozen before current outputs were inspected. There was one sample per author arm.

## Delivery comparison

Four fresh task-controller runs used the same module catalog, model, tools, verification context,
simulated network subject and frozen task contract. Case order alternated arms: prohibited access
with voluntary lookup, sharing with excerpt, prohibited access with excerpt, sharing with voluntary
lookup. Each case had its own catalog, SQLite state and artifacts.

The delivery source was the previously selected and reviewed header-selector module guide, revision
`73c8a6b9-8c05-5aaa-9345-029736792841`. Its bytes were checked against the prior selected bundle.
The excerpt contains complete Controls, Vulnerability verification, and Interpretation and analysis
sections, with source identification. The newly authored documents above were not used for these
task runs, so the delivery results do not validate their quality.

The research wrapper supplied the excerpt as advisory user context to the executor and evaluator.
Builder and critic inputs, role schemas and available lookup tools were unchanged. Python recorded
the exact delivered text on first executor dispatch; the existing contribution evaluator and
independent feedback reviewer were unchanged. Excerpt delivery does not establish reading or use.

| Case | Arm | Requests | Controller | Label | Explanation | Feedback |
| --- | --- | ---: | --- | --- | --- | --- |
| Prohibited access | Voluntary lookup | 32 | done | Correct | Unsupported: denial labels override returned data | No delivery receipt |
| Legitimate sharing | Excerpt | 35 | done | Correct | Supported: permitted sharing versus denied reverse access | Helpful, +1 |
| Prohibited access | Excerpt | 34 | done | Correct | Supported: policy-denied returned data despite error labels | Helpful, +1 |
| Legitimate sharing | Voluntary lookup | 28 | done | Correct | Supported: effective returned content matches permissions | No delivery receipt |

All four labels were correct. Explanation review distinguishes the unsupported baseline from the
three supported results. Both excerpt results have valid guidance/result mappings, accepted
independent feedback reviews and one score each. No feedback correction or pending score remained.
The two voluntary arms received relevant recommendations but never called `get_technique`.

Feedback identified concrete methodological correspondence: stable identity and policy,
selector-only variation, and interpretation of returned data against entitlement. These judgments
do not prove causal dependence on guidance. The sharing baseline also performed correctly without
retrieval. Two unreplicated pairs cannot distinguish a durable effect from sampling variation.

All cases initially fabricated `memory:obs-*` acceptance references and recovered using the existing
artifact. Tool-catalog calls numbered four, eight, five and five respectively. Requests above include
repair and, for excerpt arms, contribution evaluation; they are not isolated delivery-cost estimates.

## What this supports

- Keep the bounded advisory author cycle. Completion is working; adding critic vetoes would not
  resolve the observed semantic mistakes.
- Favor relevant reviewed examples as authoring input. This uses an existing input channel and
  improved specific distinctions here, while leaving a known weakness unresolved.
- Selected advisory context is a stronger candidate for the next production-path pilot than
  expecting a model to initiate another lookup workflow. Preserve optional lookup and adaptation.
- Keep feedback unchanged during that pilot. It completed with supported mappings for the delivered
  excerpts and did not fabricate scores for recommendation-only cases.

This is not a general rollout recommendation. An integrated delivery path must preserve provenance,
scope, conditional applicability, controller-owned receipts and prompt fitting. This research wrapper
adds a small excerpt after base prompt construction; no tight prompt-fitting boundary was exercised.
The curated section selection is research-specific, not a generic production excerpt selector.

## Validation and accounting

The focused suite passed **35 tests**, covering advisory authorship, structured/text delivery,
continuation, unchanged roles, exact snapshots, invalid inputs, controller isolation and frozen-result
scoring. The new research delivery helper and its tests have **100% statement and branch coverage**;
the coverage-floor check passed. Ruff and whitespace checks passed.

SQLite integrity checks passed for all four catalogs before schema and receipt queries. Receipt
snapshots matched the exact supplied text. Provider input audits found that exact excerpt in both
excerpt arms and neither voluntary arm. Every captured generation request used
`muse-glimmer:30b-mlx`. Frozen accepted artifact payloads were resolved from the acceptance ledger,
not mutable result aliases. The [structured results](results.json) retain hashes and judgments.

Total: **137 generation dispatch attempts**: eight for authorship and 129 for the four tasks,
including feedback. No case reached its 60-request cap or the wave's 240-request cap. Embeddings and
model-metadata calls are excluded. No calls remain running. Raw records and invocation scripts are
under `outputs/technique_review_trials/low-friction-20261116`.

No production behavior, global catalog, existing sentiment or target-operation evidence changed.
These synthetic HTTP tasks do not establish efficacy on real targets, other protocols, whole-operation
planning or broad document quality. The full repository suite was not repeated for this research-only
change; the affected helper and relevant production paths were covered by the focused suite above.
