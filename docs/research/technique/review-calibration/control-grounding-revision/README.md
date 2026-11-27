# Reference-grounded control explanations

The preceding [positive acceptance run](../final-revision-acceptance/results.md) failed: its
filesystem assessor exhausted correction on the exact-excerpt contract. Network and corrected
HTTP passed. That run used 17 of 21 requests and remains failed. Total live calls through it: 61.

This revision allows faithful explanations grounded in current procedure-body records. The
internal assessor schema adds optional `control_references`; `actual_discriminator` can quote or
paraphrase actual steps. Python validates supplied control IDs against the frozen body and records
the exact resolved text. Legacy literal excerpts still resolve directly. If a separate control
list is absent, supplied current body draft references can ground a paraphrase. Metadata and stale
control IDs are rejected. An excluding control requires both an explanation and body evidence.

These are availability checks, not semantic scoring. A valid reference can be irrelevant. The
assessor must judge whether the explanation is faithful, whether the step is a real procedural
control, and whether it excludes the same-observation permitted scenario. A permitted matching
scenario without an excluding control still contradicts approval. Empty or invented controls must
not be silently repaired. No keyword or deterministic vulnerability judgment was added.

The [patch](revision.patch) preserves both production changes, including experimental correctness
guidance. Its reconstructed pre-change runtime identity matches the frozen failed run. Initial,
independent-investigation, comparison, correction, native-schema and schema-tool paths were checked
for contradictory exact-quote instructions. Full authored-input guards remain in force.

[Captured filesystem replay](captured-filesystem-offline-replay.json) shows both prior assessor
responses now resolve to available body evidence without live calls. This establishes reference
availability only, not renewed semantic approval. Earlier failures are not relabeled as passes.

The [prepared protocol](protocol.json) uses the same three complete, human-adjudicated positive
subjects and a new frozen runtime identity. It proposes 21 sequential provider requests: roughly
15 for three reviews plus six for corrections. The four unused requests in the previous allowance
are not transferred. The user granted a new allowance of up to 21 sequential requests for this revision. All previous labels
remain bound to unchanged subjects; no further fixture adjudication is required.

The coordinator is prepared under
`outputs/technique_review_trials/control-grounding-acceptance-20261106-1/run.py`. Its default mode
runs scripted transport checks. Live mode rejects missing authorization, checks each exact subject
label, reuses only matching preflight evidence, preserves completed outcomes and shares a persistent
counter. The new revision must complete review before claims about production readiness or document
generation yield; a later success cannot replace this preceding failed trial.

## Live outcome

The [live result](results.md) failed overall: network and filesystem passed without correction,
but corrected HTTP remained semantically disputed after correction. It used 18 of 21 requests;
cumulative live calls are 79. There are no calls running. The next intervention is described in
[the scope-calibration design](next-intervention.md); no subsequent revision or live replay has
been started.
