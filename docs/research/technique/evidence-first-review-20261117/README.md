# Matcher identifiers and evidence-first review

**Completed:** retain canonical matcher IDs, schema-bound inspection evidence IDs, citation-schema
corrections and the evidence-first review assembly. The final full suite passed 8,246 tests and
all coverage floors. The paired evidence-ID cohort produced 18/18 valid, expected evidence-first
reviews versus 16/18 valid baseline reviews. See evidence-id-results.json, validation.json and
review-controller-result.json. Earlier failed cohorts below are retained as research history.

This follow-up addresses the November 17 running-operation review. The matcher returned canonical
inventory subject IDs where the controller expected temporary aliases, and a hypothesis evaluator
accepted an empty result despite acknowledging whole-query HTML rendering.

## Changes

Matching advertises canonical subject IDs in its bound native JSON schema. Legacy aliases normalize
before validation; unknown subject, trigger and observation references remain invalid. Cache identity
version 2 prevents reuse of earlier alias-based decisions. Invalid results remain distinct from valid
empty results, including on unchanged replay. Failure events expose field paths and supplied IDs,
without exporting observation text or arbitrary exception messages.

The provisional hypothesis-review assembly places frozen input context and controller-observed
outcomes before the publication. Duplicate worker conclusions and acceptance summaries are omitted
from this specialized review. Other evaluator paths retain their existing context. No reviewer,
semantic check, minimum hypothesis count, or vulnerability inference was added. The shared semantic
rubric already distinguishes conditional proposals from completed exploitation; it remains unchanged.

## Checks and experiment

The focused matcher and assembled-path checks passed: 38 tests. Existing assembled tests exercise
builder, critic, revision, bounded repair, executor, evaluator, and prompt fitting for web and web_recon.
Additional assertions verify evidence precedes review instructions and worker conclusions are omitted.
Eight offline research checks validate all six fixture assemblies and resumable success/failure records.
The research runner has 97% combined coverage; the full repository coverage result is pending.

The replay protocol fixes six cases and three repetitions per arm before model execution:
whole-query HTML rendering, an unlinked parameter, editable storage identity, a bare route,
network record access, and filesystem containment. Four motivate conditional tests; two do not
establish a security-relevant mechanism. Ground truth concerns whether to revisit an empty publication,
not whether a vulnerability exists.

Run using the project environment:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run python \
  docs/research/technique/evidence-first-review-20261117/run.py
```

Outputs are isolated under
outputs/technique_review_trials/evidence-first-review-20261117/connected.
Existing result files are never overwritten. Root-level failure records retain the initial
sandbox connection failures; these are not semantic results.

The runner uses actual controller prompt assembly and bound schemas with synthetic inline
controller-observed outcomes and no reader loop. Baseline assembly is reconstructed from the
preceding arrangement. This is a controlled reviewer diagnostic, not full-controller acceptance
validation. Protocol results, citation quality and explanation quality must be inspected before
promoting the provisional assembly. A fresh isolated controller run remains required afterward.
No production operation or historical review was changed.

## Interim model observation

The first whole-query pair completed. Baseline retained the empty publication despite describing
decoded query text entering createContextualFragment; revised requested a hypothesis. The revised
explanation calls the source "reflected XSS", overstating a conditional mechanism as a demonstrated
vulnerability. This is mixed evidence, not a promotion result. Both semantic explanation quality and
citations require assessment across the remaining trials.

## Repository verification status

The first full run reported 8,190 passed, 28 failed and three errors. Failures include sandbox
PermissionError responses in local server/browser/network fixtures; this is not a passing suite.
The coverage floor also failed for unchanged authentication.py (79.2% branch coverage).
Changed matching.py measured 99.0% statements / 91.2% branches; changed workflow controller
measured 90.7% statements / 85.0% branches. A fresh full run with the fixtures' required local
permissions is in progress. Ruff and git diff whitespace checks passed.

## Citation-schema correction and retry

The permission-enabled repository run completed with **8,231 passed, 46 skipped and
89 deselected**, and the coverage floor passed. This run predates the citation-schema correction.

All 36 original role invocations completed. Baseline produced one schema failure, two expected
requests to revise a grounded empty result, five expected retentions of negative cases, and ten
missed grounded cases. Revised produced seven schema failures, five expected revision requests,
five expected negative retentions, and one unnecessary revision of the bare-route case.
These are decision counts, not independent semantic or provenance approvals. The review assembly
was reverted; production retains its previous context arrangement.

The original comparison was confounded by a schema mismatch. The shared citation alias already
declared a pattern, but Pydantic omitted it from generated JSON Schema. The bound empty-result
conditional branch also explicitly advertised plain strings. Python still rejected invalid
reference kinds afterward. The earlier explanation that no pattern was declared was incomplete.

The shared alias now explicitly exports its canonical artifact-reference pattern through
WithJsonSchema. The bound conditional branch derives its item schema from that same alias.
Python normalization remains in place for legacy presentation variants; artifact existence,
current-operation provenance and semantic support remain separate controller checks.

Regression tests check native and Strands tool-compatible schemas against canonical artifact
and artifact_id references, memory references, bare paths and empty references.
The focused review and harness suite passed **153 tests**.

A fresh 36-role-invocation replay is running under
outputs/technique_review_trials/evidence-first-review-20261117/citation-schema-retry.
It preserves the same cases, repetitions, model selection and two prompt arrangements.
dispatch-count.json counts actual Ollama dispatches, including native correction calls.
Old results remain unchanged. A new permission-enabled repository coverage run is also pending.
No review-assembly promotion decision will rely on the confounded first comparison alone.

## Completed citation retry and final branch correction

The citation retry completed all **36 actual Ollama dispatches**. There were no citation-format
failures. Baseline requested revision in 4/12 grounded cases and retained all 6 negative cases.
Evidence-first requested revision in 10/12 grounded cases and retained all 6 negative cases;
its remaining two grounded trials failed structured validation because kind was missing.
These counts do not establish that citations referred to inspected content or that all explanations
were semantically sound. XSS explanations still sometimes state an outcome too strongly.

The schema required kind at the outer level, but its oneOf branches did not repeat that requirement.
The native-decoder failures motivate explicitly requiring kind within both branches. This changes
schema representation, not Python acceptance policy. A fresh identical paired run is in progress
under citation-schema-complete, preserving both earlier cohorts. Promotion remains pending.

The full repository suite after the citation-pattern fix passed **8,237 tests**, with 46 skipped,
89 deselected, and all coverage floors met. After the final branch-requirement correction, the
focused suite passed **196 tests**. Statement / branch coverage was 99.0% / 98.4% for
hypothesis_review.py, 98.1% / 86.4% for structured_outputs.py, and 99.0% / 91.2% for matching.py.
Ruff and whitespace checks pass.

The isolated canonical-matcher controller pilot completed in **30 actual model requests**.
Its immutable accepted result correctly classified permitted sharing as no_violation_observed,
passed the fixture's evidence-ID and decisive-probe checks, and received helpful (+1) feedback.
See controller-result.json for the accepted payload and feedback. This pilot used synthetic tools
and an isolated cloned catalog; one success does not establish general cost or reliability gains.
The pilot does not validate the provisional hypothesis-review arrangement.

## Complete-schema result and provenance audit

The final schema-only cohort completed 36 actual dispatches with **zero schema failures**.
Decision agreement improved from 5/12 grounded cases under baseline to 12/12 under evidence-first;
both retained all six negative cases. However, every output cited invented artifact references
such as artifact:1 or artifact:源. These are format-valid, but do not refer to retained inspections.
The harness had supplied summaries without recording real inspection receipts or invoking
production review validation. Therefore no output from this cohort establishes provenance-valid
review completion, and this result does not justify production promotion. Full decision and citation
records are retained in complete-schema-results.json.

The next cohort, provenance-validated, records the same synthetic source content through the actual
inspection journal and places the returned receipt into the controller ledger. Both arms use these
receipts. Each output passes through the production bound-model canonicalization, review normalization,
and validate_review checks. Up to three attempts are allowed, with controller validation errors recorded
and returned for citation/contract correction; no additional semantic critic is introduced.
Actual dispatches and every correction output are retained separately.

Sixteen offline harness checks passed, including acceptance of a real content receipt and rejection
of an invented reference. The harness exceeds 80% statement and branch coverage, and Ruff passes.
The new model cohort is in progress. It still uses inline synthetic observations without a reader loop;
it does not replace a future full hypothesis-controller run. Production review assembly remains unchanged.

## Final disposition

The provenance-validated cohort finished after **106 actual model dispatches**. Neither arm produced
a controller-valid review: baseline 0/18 and evidence-first 0/18. Model outputs repeatedly substituted
invented short references such as artifact:1 for the supplied retained receipt paths, including after
controller corrections listed the exact available content references. Five trials also encountered a
native output limit during correction. See provenance-results.json for recorded validation failures.

**Do not promote the evidence-first review arrangement.** Its earlier decision gains remain a
diagnostic observation, not valid end-to-end completion. Stop repeating the same correction loop.
The next candidate is a controller-owned citation boundary: model-selectable short evidence IDs
bound in the output schema and expanded through an exact receipt mapping in Python. Such a change
must support evidence added during evaluator investigation, preserve raw decisions, reject unknown
IDs, and pass real provenance validation before another comparative study. It is not implemented by
this follow-up and should not be inferred from the citation-pattern fix.

Retain the independently verified matcher and schema corrections. The full suite, focused final
coverage, Ruff, research-harness checks, and isolated controller pilot are recorded above and in
validation.json and controller-result.json. Production hypothesis-review assembly is unchanged.
No deployment, commit, or live target operation mutation was performed. All model cohorts are finished.

## Evidence-ID implementation

The follow-up now binds inspection_refs in the model-facing hypothesis-review schema to short
IDs (e1, e2, ...). Python expands selected IDs through an exact invocation-local receipt mapping
before the existing canonical model and provenance checks run. Canonical artifact references remain
supported at the Python compatibility boundary. Unknown short IDs remain invalid. No semantic judgment
or evidence support is inferred by this expansion.

The mapping refreshes when the schema is generated, including native finalization after investigation.
Journal index order preserves IDs when additional results arrive. Unavailable records are not
advertised; metadata records remain selectable but do not become content-bearing evidence.
An empty journal advertises no selectable citations. Raw input dictionaries are not modified.
All persisted canonical review fields still contain artifact references, not short IDs.

Targeted review and harness tests passed (168 checks before the additional native handoff test).
The additional real Strands/native finalization test verifies a receipt added after investigation
is selectable and expands correctly. Seven evidence-ID component checks pass. The full repository
suite and a fresh provenance-validating paired cohort are running. New model outputs are isolated
under evidence-id-bound; earlier results remain unchanged. The evidence-first arrangement remains
experimental; production prompt assembly is unchanged.

The evidence-ID component now passes eight tests, including actual native finalization and actual
tool-compatible Strands finalization. Targeted coverage measured 100% statements and branches for
review_evidence_ids.py and 99.0% / 98.4% for hypothesis_review.py.

The first live baseline whole-query review completed with valid controller provenance. Its one
correction concerned omitted input_candidate_ids, not a citation reference; the corrected result
requested a conditional DOM-injection test. This is one valid completion, not a reliability estimate.
The rest of the evidence-id-bound cohort and full-suite verification remain pending.

## Evidence-ID cohort and promotion

The evidence-ID cohort completed after **48 actual Ollama dispatches**. Production controller
validation accepted 16/18 baseline reviews and all 18/18 evidence-first reviews. Baseline requested
revision in 11/12 grounded cases and retained 4/6 negative cases; the remaining negative trials hit
native output limits. Evidence-first requested revision in all 12 grounded trials and retained all
six negative trials. There were no citation-provenance failures. See evidence-id-results.json for
canonical reviews, corrections, failures and exact counts.

Retain the evidence-ID boundary and promote the tested evidence-first assembly: assigned context,
inspection ledger and controller-observed outcomes precede the current publication; duplicated worker
conclusions and acceptance summaries are omitted from this specialized review. Existing grounding,
discrimination, identity, evidence-availability and provenance checks remain in place. This change
does not accept hypotheses automatically or remove semantic review.

The full repository suite before the assembly promotion passed **8,244 tests** (46 skipped,
89 deselected) with the evidence-ID implementation. Two additional focused runtime-path tests cover
actual native/tool-compatible finalization. Following assembly promotion, assembled-path checks
passed and the research suite passed **21 tests**. Research runner files exceed 80% statement and
branch coverage. Final full-suite verification of the promoted tree is running.

A bounded real-controller evaluator integration pilot is also running under
outputs/technique_review_trials/evidence-id-controller-20261118. It checks a grounded network
record-access case and a bare-route negative case through actual controller validation and routing
with a real native model, artifact readers and synthetic retained evidence. The production evaluator
prompt and review contracts are used; the model runner is injected and this is not a complete
operation or actor-repair cycle. Offline tests verify both routes and the 30-request cap.

This is a small synthetic comparison with three repetitions per case. It supports the narrow
implementation choice, not a population-level accuracy claim, successful exploitation, or a general
increase in useful technique adoption.


## Final verification

The promoted implementation passed **8,246 repository tests**, with 46 skipped and 89 deselected.
All repository statement and branch floors, Ruff, and whitespace checks passed. The final focused
boundary suite passed 205 tests, and the research suite passed 21 tests. Coverage and source hashes
are recorded in validation.json.

The isolated live controller integration check completed both cases in **four actual requests**:
the grounded network case requested publication revision with acceptance repair, and the bare-route
case completed with a retained empty disposition. Both reviews passed actual controller provenance
validation; the referenced receipt files remain available in the controller pilot's case directories.
See review-controller-result.json.

The paired replay's journals were temporary: its controller-valid labels describe validation at
generation time, not artifact availability after the temporary directories were deleted. Prompts,
schemas, canonical reviews and validation failures remain recorded; the live controller pilot
retains actual receipts for audit. The pair order was fixed (baseline first), not randomized.

All model jobs are complete. No deployment or live target assessment was performed. Broader
authorship quality, full-operation effectiveness and increased useful adoption remain unproven.
