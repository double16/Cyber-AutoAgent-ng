# Adaptable selection and outcome discrimination

The implementation keeps the existing selection verdicts and hypothesis records. Selection receives phase
identifiers, explicit methodology restrictions and module policy, with the phase authoring rubric omitted.
The selection instruction allows ordinary contextual adaptation and relevant procedures within a larger document.
Prompt fingerprints isolate judgments made with the revised instructions from earlier cached judgments.

The shared interpretation foundation shrank from 430 to 86 words. The assembled hypothesis semantic rubric
shrank from 935 to 484 words. Counterexample fields and authored-content classifications belong to the legacy
technique-review instructions. Production authorship retains its advisory critic and optional single revision.
Hypothesis schema descriptions use existing fields to ask for distinguishing observations, controls and evidence.

## Evaluation protocol

The paired replay uses `muse-glimmer:30b-mlx` through the production native structured-output adapter.
Baseline instructions come from the implementation's parent commit. Selection invokes the actual `_select`
method against one fixed synthetic candidate per case. The five selection cases span all technique phase
contexts and include contextual adaptation, incompatible methods, unknown prerequisites, relevant subsets
and an explicitly missing capability. Each arm runs three times, alternating order by repetition.

The six empty-publication cases reuse the earlier evidence-first study's HTTP, network and filesystem fixtures.
Both arms use the current evidence-first assembly and bound evidence-ID schema. Only the semantic rubric
changes between arms. Publication receipts and inspection evidence remain in the isolated replay directory.
Controller-invalid outputs are failures, irrespective of their apparent semantic intent. Additional populated
publication cases compare ordinary file-write behavior with an unauthorized protected-property change.

These are fixed synthetic observations without target acquisition or execution of proposed tests. The replay
measures selection and review decisions, with actual schema and provenance checks. It does not measure
operation-wide effectiveness. Single-candidate selection does not measure retrieval ranking or automatic
recommendation applicability. Populated-review controller pilots exercise routing without a complete operation.

Local replay inputs, runner scripts, individual results and retained receipts are under
`outputs/technique_review_trials/adaptable-selection-20261119/`. Initial sandbox connection failures and fixture
setup errors are retained separately under `initial-setup-errors/`. Live runs are sequential and resumable.

## Selection results

| Framing | Correct decisions | Contract-valid trials | Model requests | Mean prompt characters |
|---|---:|---:|---:|---:|
| Baseline | 11/15 | 14/15 | 18 | 5,878 |
| First revision | 9/15 | 12/15 | 17 | 2,727 |
| Retained procedure-mapping refinement | 15/15 | 15/15 | 15 | 2,393 |

The first revision failed the promotion criterion and was replaced. All three baseline trials and all
three first-revision trials rejected the general DOM method. The refinement reused it in all three trials.
The refinement also excluded the incompatible multipart method and the missing expiry-validation method
in every trial, and reused the network method with unknown prerequisites and the filesystem subset.
The recorded production selection prompt and schema exactly match the evaluated refinement.

The refinement ran after the first paired cohort, using the same fixed candidates, requests and verdict
schema, with three repetitions. Baseline results were reused rather than regenerated. Timing therefore has
order and service-load confounds. This small development set demonstrates the measured change; it does
not establish generalization to new requests or actual useful adoption in operations.

## Empty-publication review results

Both rubrics produced 12 controller-valid reviews out of 18 trials. Every controller-valid review made the
expected decision. Each arm retained all three bare-route empty results and requested conditional tests
for all storage-identity, network-access and filesystem-access trials. The six query-input trials per arm
failed the existing controller requirement to assess input-candidate IDs. These failures are retained as
invalid outputs; their apparent decisions are excluded from semantic correctness counts. The replay used
one controller-validation attempt per review, so it does not measure the production correction loop.

## Verification

The final full coverage run passed 8,256 tests, with 46 skipped and 89 deselected. All selected Python files
meet the 80% line and branch coverage floor. Ruff and diff checks pass. Assembled generation, review,
revision, bounded repair and prompt-fitting checks verify one shared foundation and no leaked technique
counterexample fields. Runtime publication and revision tool schemas retain the new descriptions.

A scratch launcher initially named `queue.py` shadowed Python's standard library during child imports and
recursively started isolated comparison processes. The process tree was stopped before further model
requests, the launcher was renamed, and both child scripts passed startup dry runs before resuming.
The earlier sandbox connection and fixture setup failures remain separately recorded.

## Populated reviews and controller routing

Both baseline and shorter rubrics made the expected decision in all six populated-publication trials
per arm, with valid schema and provenance. Each rejected the ordinary-write proposal in three trials and
retained the protected-property proposal in three trials. The revised controller pilots routed the weak
proposal to `partial_failure` with acceptance repair and accepted the stronger proposal as `done` with
no repair. Fixture state and retained evidence exercise real evaluation/routing code without a complete
operation or actor repair execution.

The connected comparison used 100 model requests across the initial cohort, selection refinement and
populated/controller checks. Counts include retries and failed structured outputs. Prompt character
averages describe the last recorded attempt text per trial, excluding schemas and system messages.
Source hashes, per-file coverage, counts and controller outcomes are recorded in
[the result summary](adaptable-selection-20261119.json).

The retained change improves selection on these fixed cases and reduces instruction length while
preserving tested review decisions. Increased useful adoption and end-to-end effectiveness in a fresh
operation remain unmeasured. The query-input assessment contract was unresolved in this cohort. The
[candidate-schema follow-up](candidate-assessment-schema-20261119.md) records its fix and production-path replay.
