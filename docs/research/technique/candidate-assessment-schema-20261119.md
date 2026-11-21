# Candidate-linked hypothesis review schema

The task-bound hypothesis review model now requires a non-empty `input_candidate_ids` list in each
behavior assessment when reconciled candidates exist. Its items remain constrained to those existing
candidate IDs. The controller continues checking complete aggregate coverage and corresponding retained
inspection evidence. Canonical IDs and persisted record shapes are preserved. Reviews without reconciled
candidates retain their existing compatibility path.

Previously, the native schema advertised the allowed IDs but supplied an optional empty-list default.
That allowed native decoding to succeed on output which the controller subsequently rejected for missing
candidate coverage. The change expresses the existing controller requirement at the structured-output
boundary using a required field and `minItems: 1`.

## Verification

Unit cases cover omitted, empty, null and foreign ID lists, valid assessments, multiple-candidate aggregate
coverage, and legacy assessments without reconciled candidates. Both native and Strands tool-compatible
schemas are checked. Controller integration tests inject missing and empty links into both text and
structured paths, then verify recovery on the second attempt and preservation of the inspection receipt.

The full repository coverage run passed 8,264 tests, with 46 skipped and 89 deselected. All selected files
meet the 80% line and branch floor. Ruff and diff checks pass. Existing assembled-prompt tests cover
normal generation, review, revision, bounded repair and prompt fitting. The required links are supplied
through the existing bound schema and candidate context.

## Connected replay protocol

The replay uses `muse-glimmer:30b-mlx`, the production `_evaluate_task` correction loop, native structured
finalization and actual artifact-reader tools. It repeats the two previously failing query-input fixtures
three times each. Whole-query rendering should revise the empty publication into a future test request.
The unlinked-parameter case should retain the empty publication. Every stored canonical review is
validated again through a fresh binding loaded from the frozen publication and durable inspection journal.

These are isolated synthetic observations and fixture controller state. The replay performs no target
acquisition or proposed security tests, and continuation verification reloads the contract without a second
semantic model call. Earlier baseline results used single-attempt finalization, so their failure counts are
not a comparison of the complete production correction loop.

Inputs, native schemas, model messages, correction attempts and canonical results are retained under
`outputs/technique_review_trials/candidate-assessment-schema-20261119/`. Initial sandbox connection
failures are retained separately. A read-only backend status check confirmed the model was loaded when
the first finalization ran longer than expected. That attempt reached its output limit and was recovered
by the existing controller correction path.


## Results

All six trials produced controller-valid reviews with the expected decisions. The three whole-query
trials requested acceptance repair; the three unlinked-parameter trials retained their empty publications.
Every review included the canonical candidate ID and passed fresh-binding continuation validation.
The six trials used seven evaluator attempts and fourteen model requests. One native finalization reached
its output limit and recovered through the existing retry path. There were no candidate-link failures.

[The result summary](candidate-assessment-schema-20261119.json) records per-trial routing, requests,
recovered errors, coverage and final source hashes. These results verify the schema/controller contract
on the previously failing fixtures. Operation-wide adoption and effectiveness remain unmeasured.
