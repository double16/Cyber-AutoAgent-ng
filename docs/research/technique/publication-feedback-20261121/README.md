# Current-publication feedback and adaptable comparisons

## Implementation

Feedback now receives the controller-bound current hypothesis publication before tool history,
including its artifact reference, validated digest, and ordinal in the publication history. The
existing producer loader verifies the digest, operation, task, and frozen subject assignment.
Valid partial publications remain available without establishing complete coverage.
Unavailable or corrupt current content is labeled unavailable; an older snapshot is never substituted.
Continuation uses the same adapter. Results are indented so evidence references address individual
lines rather than one entire serialized task.

The existing hypothesis comparison view now places declared authentication requirements immediately
before future test steps. Semantic interpretation remains model-owned. No keywords infer identities,
no roles are invented, and missing credentials do not invalidate future test design.

The shared security interpretation now connects supporting/refuting outcomes to the same security
consequence and connects future identity comparisons to declared requirements. Technique feedback
uses that shared interpretation and explicitly judges substantive adaptation rather than required
reproduction of tables, checklists, or confidence labels. Existing review and correction counts remain.

## Assembled instruction review

Reviewed the shared interpretation, authoring and review guidance, specialist role system prompt,
hypothesis comparison rendering and semantic rubric, workflow generation/checkpoint/evaluator paths,
feedback evaluator and deferred independent reviewer, native schemas, retained repair instructions,
and prompt-budget guards. Authoring already explicitly permits future procedures and illustrative
payloads; the source-code execution prohibition does not prohibit designing future tests. No extra
prohibition or critic stage was added. Actual technique documents are untrusted guidance, so the
code change cannot retroactively correct misleading examples already authored in the catalog.

Both feedback roles receive the same context inventory, including current publication identity.
Their existing budget guard measures complete inputs and fails explicitly rather than truncating
mandatory evidence. A publication that cannot fit leaves feedback pending; this change does not add
lossy publication summarization. Unit tests cover this behavior at the native-role boundary.

## Replay protocol

Run from the repository root:

```sh
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" \
  uv run python docs/research/technique/publication-feedback-20261121/replay.py
```

Three sanitized recorded cases (DOM-XSS, admin access, and justified empty disposition), three
repetitions, and two arms give 18 trials. Both arms receive the same frozen guidance and reconstructed
publication outcomes. Guidance fixtures contain the body of evaluated technique copies; frontmatter
and lifecycle metadata are omitted in both arms. The baseline uses the feedback module from the pinned Git revision recorded
in each result. The revised arm additionally receives the authoritative current publication and the
updated shared interpretation. Thus the comparison measures the combined intervention, not its
individual causal contributions. Native production feedback schemas and the existing independent
review/correction flow are used. Actual transport requests are capped at 120, including retries.

This is an isolated feedback replay, not authorship, target execution, full workflow acceptance, or
an end-to-end effectiveness test. References inside frozen artifacts are evidence text only; replay
provides no artifact-reading tools. Unsupported judgments may remain pending. No SQLite or Qdrant
production catalog is opened or modified. Frozen fixture files replace the private target address
and operation ID; they are not byte-identical production publications.

Before interpreting scores, inspect whether reasons distinguish final corrections from historical
mistakes, distinguish ordinary rendering from script execution, detect the admin comparison/identity
mismatch, and permit a grounded empty disposition. A higher helpful rate is not the success criterion.
Report failures and disagreements without retrying a trial until it passes.

Raw outputs and transport flags are under the ignored
`outputs/technique_review_trials/publication-feedback-20261121/live/` directory. Earlier sandbox
connection failures are retained separately in its parent and are not semantic measurements.

Publication delivery in this replay is reconstructed directly from fixture values rather than loaded
through the workflow adapter. Loader integrity, producer binding, partial publication handling, and
continuation selection are covered by unit tests. The replay cannot demonstrate that a model has
verified the underlying source artifacts, and model latency is confounded by other work on the same
endpoint. Interpret request counts separately from wall time.

## Validation status

1,672 component tests pass (2 skipped, 6,824 deselected). Every changed production file
passes the 80% statement and branch coverage floors. Ruff and whitespace checks pass.
Both replays are complete, using 75 total requests (63 feedback and 12 focused semantic checks).
See `validation.json` for source hashes, coverage, request counts, and experiment logs.

## Feedback replay results

All 18 trials finished using 63 requests. The baseline revision was
`9941df56c30f99d7a5a179e74569ab02958c8a79`.

| Arm | Resolved trials | Requests | Trials needing correction | Final scores |
| --- | ---: | ---: | ---: | --- |
| Baseline | 8/9 | 33 | 2 | 8 helpful, 1 pending |
| Revised | 9/9 | 30 | 1 | 9 helpful |

The one pending baseline result is a semantic disagreement after the existing correction allowance,
not a JSON schema failure. There were 42 schema-bearing final-decision requests and 21 existing
unstructured independent-investigation requests. Structured decision outputs validated in all
recorded role calls. Three fewer requests is a small descriptive result, not a general efficiency claim.

Manual review of every role output found cleaner final-version descriptions in all three revised
admin trials. Baseline summaries blend earlier unqualified guard claims with corrected unknown
server-enforcement language; one counts old and new snapshots as two hypotheses. Revised summaries
describe the one current hypothesis and preserve its uncertainty.

Neither arm flags the sampled DOM test-discrimination flaw or the admin comparison/identity
mismatch in usefulness feedback. Both arms assign helpful scores to the DOM and admin cases.
The revised empty-result case resolves consistently, but one trial still initially penalizes missing
tables/checklists before correction, and some helpful reasons overstate explicit prerequisite
categorization in the result. Higher scores therefore do not demonstrate increased useful adoption.

The result supports improved provenance presentation and modest feedback consistency. It does not
establish better security reasoning, authoring quality, acceptance decisions, or end-to-end execution.
Detailed findings and output assessments are in `feedback-results.json` and
`feedback-assessment.json`.

## Focused semantic-check follow-up

`semantic_replay.py` uses the remaining allowance in the same persistent 120-request counter.
It compares the existing native `HypothesisSemanticChecks` output on the current DOM and admin
records, with three repetitions per arm (12 checks). The baseline uses the prior comparison view
and prior shared interpretation; the revised arm uses the updated view and shared interpretation.
No prompts are tuned between repetitions and failed trials are preserved.

This uses the production publication rendering, semantic rubric, and native output schema, but is a
focused check rather than the full task evaluator: it omits controller acceptance decisions, artifact
inspection, and larger workflow context. The frozen publication loader is substituted only to render
the fixture. Source grounding must not be interpreted as independently revalidated. Results can test
whether focused reasoning identifies the particular defects, not whether a production run will fix them.


## Focused semantic results and conclusion

All 12 focused checks completed with valid native structured outputs, one request each.

| Defect | Baseline detected | Revised detected |
| --- | ---: | ---: |
| DOM reflection versus script-execution discrimination | 3/3 | 3/3 |
| Missing declared identity restriction for the admin baseline | 0/3 | 0/3 |

The baseline already identifies the DOM discrimination defect. Both arms miss the admin identity
requirement. Baseline marks identity adequate; revised marks it not applicable in every repetition,
reasoning from the declared `none`/empty-role fields without reconciling the proposed authenticated
admin baseline. These reasons show a shortcut; they do not support claiming identity repair.
`comparison=none` can legitimately support single authenticated-identity work, and an empty role
list means unrestricted selection rather than necessarily anonymous execution. No fixed two-role
comparison is universally required here; the specific admin baseline still needs to be represented.

No semantic prompts were changed during either replay. One revised DOM explanation has an incomplete
tail despite being schema-valid; its preceding text correctly identifies the discrimination flaw.
Underlying source grounding and full controller acceptance remain outside this focused experiment.

The implementation and planned validation are complete. Evidence supports improved current-version
presentation and modest feedback resolution consistency. It does not support improved authorship,
identity agreement, or increased useful adoption. The sampled security-reasoning improvement criterion
is unmet. No library documents, historic feedback, production databases, or running operations were changed.

Keep these findings as regression evidence rather than rerunning until the model agrees. Any next
experiment should distinguish task-semantic review from contribution feedback and investigate how
the existing reviewer handles its actual production context. A new critic or more prohibitions are
not justified by these results. Deployment and a production continuation remain separate actions.
See `semantic-results.json` for every focused decision and its reason.


## Follow-up contract clarification

The subsequent recorded-input investigation found that the generic empty-role selection explanation
above was too broad when applied to hypothesis declarations. Hypothesis field descriptions and phase
guidance use none/empty to request no authenticated identity. Generic unrestricted credential selection
only applies when authentication selection is invoked, and does not itself arrange an admin baseline.
The recorded catalog contains only `user`; an `admin` role must not be invented. This clarification
does not change the recorded model outputs or the finding that the proposed admin baseline was not
reconciled with its declaration. See `../identity-presentation-20261122/README.md` for the follow-up.
