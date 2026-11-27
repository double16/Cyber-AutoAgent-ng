# Technique authorship, selection and voluntary adoption

The subsequent [authoring-example and delivery comparison](../low-friction-20261116/README.md)
completed both proposed experiments. It found supported results and helpful feedback in two
excerpt-delivery cases, alongside partial authoring improvement and a persistent invalid-control
weakness. Voluntary lookup remained unused; broader effectiveness is still unproven.

The advisory authorship cycle completed, and explicit selection reused the resulting guide.
Voluntary retrieval remains the observed gap: both natural tasks received relevant recommendations,
but neither fetched a document. The new guide also retained substantive procedural weaknesses.
These observations support keeping publication mechanics, selection, adoption and quality separate.

## Scope and method

No post-change target operation was available. Two isolated synthetic HTTP verification cases used
the existing production task controller, prompt builder, executor, evaluator, real embeddings,
module catalog, SQLite acceptance and recommendation integration. Endpoint I/O used registered
fixture tools only. Whole-operation planning and real-target effectiveness were not tested.

First, a guide was generated through the new author → advisory critic → author cycle using three
freshly fetched public references and normal source assessment. Catalog search was intentionally
suppressed only during this seed generation to exercise cold-start authorship without existing
catalog examples. Search was restored for the subsequent tasks. Each task received an independent
copy of the module catalog plus the new guide. No forced lookup or automatic guidance delivery was
added. This deliberate duplicate seed is not evidence that ordinary lookup would regenerate it.

Full model inputs and lifecycle events were retained. Outcomes were reviewed against the frozen
accepted artifact resolved from the acceptance ledger, rather than a mutable latest-result alias.
SQLite integrity checks passed before receipt queries. This is an unpaired observation, not a
controlled estimate of technique benefit or an adoption rate.

## Results

| Stage/case | Generation requests | Result |
| --- | ---: | --- |
| Fresh guide, including source assessment | 12 | Published after one advisory critic and one revision; no critic veto or re-review |
| Prohibited cross-account access | 34 | `done`; accepted artifact correctly identifies disclosure despite a misleading denial label |
| Legitimate sharing | 32 | `done`; accepted artifact incorrectly claims permitted reads were denied despite returned authorized data |
| Separate explicit generic lookup | 8 | One resolved component, zero unresolved; reused the new guide without research or authorship |

Both natural tasks received recommendations for the established header-selector guide and the new
guide. The sharing case also received an object-level authorization recommendation. **Neither called
`get_technique`.** Their catalogs contained no technique-use receipts. No neutral or helpful feedback
was manufactured from recommendations alone, and contribution feedback was not exercised.

The explicit diagnostic selected new revision `2159bd6c-9d4b-59f0-b99b-f03420ab3b90`, ranked first
ahead of the established module guide. Its requirement judgments were covered, and delivery created
an isolated diagnostic receipt. This investigator-requested lookup is not voluntary task adoption;
its unscored receipt is expected because no consuming task was executed for it.

Both natural tasks initially submitted invented `memory:obs-*` evidence references despite having
a result artifact. They recovered using the artifact reference after ten and eight tool-catalog
calls respectively. Those calls are included above. The sharing evaluator accepted an unsupported
interpretation, so `done` and successful artifact acceptance do not establish semantic correctness.

## Authorship quality

The advisory revision improved policy documentation, parameter isolation and evidence wording.
It did not resolve two important issues, recorded before reviewing task outcomes:

- A denied baseline is still described using an object authorized for identity B, without clearly
  requiring that policy denies identity A. Legitimate sharing is a counterexample.
- Confirmation still requires invalid-value controls to behave as expected and not return the same
  data. Unexpected invalid-value behavior does not invalidate an established valid-selector
  disclosure contrary to policy.

The guide's generated lookup request also repeats the generic phase objective instead of naming
the method clearly. Its semantic trigger nevertheless matched, and explicit retrieval succeeded.
The exact guide remained unchanged in the isolated catalogs; it was not added to the global catalog
or module source documents. Neither a new semantic gate nor additional critic was introduced.

## Implications

Authoring can now finish without critic-driven refusal loops. That improvement does not establish
broad semantic quality. Selection can find and deliver a matching document when requested, but its
coverage judgment is not a technical-correctness guarantee. In these cases the executor bypassed
document retrieval; the trace does not establish why, or that fetching a guide would have helped.

The next bounded comparison should test reducing the separate lookup step by supplying a small,
already selected guidance excerpt as advisory task context. Compare voluntary lookup and that
delivery on the same cases, scoring application and explanation correctness separately. Earlier
automatic-context prototypes had mixed outcomes, so this is a hypothesis to test, not a rollout
recommendation. For authoring, test relevant verified examples as input before adding more prompt
requirements or critics. Keep the current feedback machinery unchanged during these comparisons.

## Validation and accounting

The existing controller and frozen-result scoring checks passed **11 tests**. Ruff passed for the
experiment invocation scripts. No production business logic, prompts, schemas, global scores or
catalog content changed, so the full repository suite was not repeated for this observation.

The first attempt used nine requests before a harness-only source adapter error: direct fetches
omitted the titles normally supplied by the production research wrapper. Its source assessment and
first author response completed, but assembly raised `KeyError: title`. That attempt is retained
separately and excluded from completed-case results, not from accounting.

Total: **95 generation dispatch attempts**: nine failed-adapter, 78 completed seed/task observation,
and eight explicit selection diagnostic. Embeddings, source fetches and model-metadata calls are
excluded. All captured generation requests used `muse-glimmer:30b-mlx`. No calls remain running.

The [structured results](results.json) include accepted payload hashes, explanations, source-guide
inspection and request accounting. Private raw inputs, sources, stores and invocation scripts are
under `outputs/technique_review_trials/adoption-observation-20261116` and the corrected
`adoption-observation-20261116-2` directory. The frozen generated document SHA-256 is
`5b1a9843ad7cb46f7f891722e7f74520924f9e07b8d58f76f35365512b10f193`.
