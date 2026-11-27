# Task relevance and semantic review input changes

Status: both comparisons complete; neither promotion gate passed. End-to-end promotion checks deferred.

## Assembled instruction audit

The hypothesis evaluator uses its existing system prompt, task-bound native review schema,
current publication, frozen acceptance criteria and assigned/linked inventory inputs. Its dedicated
input builder includes the available identity catalog, recorded inspection references and bounded
saved-result content. It excludes the producer's authentication selection, credential IDs,
publication-generation instructions, prior tool summaries and delivered technique documents.
Saved-result replay reports omitted/unreadable results and offers the existing inspection reader;
missing content is not negative evidence. The same builder is used for repeated reviews and
continuations, including pending corrections. Existing schema corrections preserve accepted records.
Oversized complete evaluator inputs retain the existing explicit capacity failure; they are not
silently accepted or reduced to the publication's selected citations.

The automatic delivery path supplies a separate task-local context to the existing complete-document
selection call. Its native schema requires task relevance; repair prompts retain both contexts.
Persisted reviews preserve that decision, and schema/context fingerprints distinguish them from
methodology-only reviews. Pending automatic selection binding includes task context and a contract
version. Delivered snapshots remain historical receipts. The lookup/generation context is still
methodology-only; authorship receives no added task context. Selection tries at most three ranked
leads and does not start authorship. Explicit lookup remains available after misses or errors.

Other task evaluators retain their existing tool/outcome and delivered-guidance paths. Controller
provenance and execution acceptance checks have not been weakened. Public tools, database schema,
document versions and multi-procedure document identity are unchanged.

## Validation boundaries

Unit tests exercise actual controller prompt assembly and service candidate review/cache behavior,
plus HTTP, network and filesystem packet variants. These establish routing and contracts, not model
reasoning quality. Full repository coverage and the frozen paired model experiments remain required
before promotion. No deployment or production continuation has been started for this change.

The accepted experiment design remains 16 review cases and 12 selection cases, two arms and two
repetitions, with 128 sequential requests including repairs. Promotion requires reproduced defects to
be corrected in both repetitions without rejecting balanced valid controls. Six paired isolated
end-to-end cases then have a separate 120-request cap. Useful test coverage must not decrease and at
least one additional case must produce a correctly discriminating test. Feedback scores and delivery
receipts alone are not evidence of improvement. Incomplete budgets must be reported as incomplete,
without selective reruns or prompt tuning after seeing results.

## Selection experiment

`selection-inputs.json` freezes 24 complete production-rendered candidate inputs: twelve cases,
two arms, two repetitions planned (48 decisions). Six module documents cover redirects, outbound
requests, JWT verification, CSRF, file boundaries and protected-property writes. Each has a matching
task and a task requesting a different security mechanism. Baseline selection receives methodology
only; candidate selection additionally receives task-local context. Identical baseline prompts across
the matching/mismatching pair are deliberate: purpose information was absent at that boundary.
These measure delivery relevance, not whether methodology-only selection correctly answers its own
narrower request. Candidate document contents and module metadata remain identical across arms.
The native schema, prompt and input hashes are frozen; two unit tests verify pairing and contracts.

Run `UV_CACHE_DIR="$PWD/.uv-cache" uv run python
 docs/research/technique/task-relevance-20261123/selection_trials.py` from the repository root (join
the command onto one line). Results and the shared request counter are isolated beneath
`outputs/technique_review_trials/task-relevance-20261123/`. Existing result files are never rerun.
The shared 128-request cap includes the later review experiment. This initial selection corpus is
web-oriented; generic packet tests also cover network and filesystem cases. No broad cross-phase
or end-to-end effectiveness claim follows from selection alone.

## Observed selection result

All 48 decisions used native structured output and returned valid schemas. Baseline delivery suitability
was 13/24 (11 mismatched deliveries, no valid guides rejected); candidate suitability was 23/24
(no mismatched deliveries, one valid guide rejected). The candidate missed the agreed zero-regression
gate and is not approved for deployment. The failed SSRF decision correctly judged task relevance but
misread the requested destination-control testing methodology as an enforcement implementation
capability. Neither that prompt nor its case has been tuned or selectively rerun.

The current common schema exposes an optional task-relevance field even to the baseline arm; thus
this is a methodology-only versus task-context comparison using current schemas, not a byte-for-byte
historical schema replay. The supplied production documents were unchanged. Results support a small
corpus improvement in delivery suitability, not demonstrated end-to-end effectiveness.

The first full suite produced 8,354 passes, 18 failures and 47 skips. Those failures exposed removed
semantic guidance and saved-result replay plus generation-specific assertions applied to reviewers.
The corrected targeted set passes all 26 cases; full coverage is being rerun. The first coverage
report met the 80% line/branch floor for all changed files, but is not final validation of the fixes.

## Frozen semantic-review comparison

`review-inputs.json` contains 32 complete controller-rendered inputs (16 cases, two arms), with two
repetitions planned for 64 decisions. Eight retained identity cases cover HTTP, network and filesystem
contexts. Eight additional fixtures pair valid and defective DOM, CSRF, protected-property-write and
parser-discrimination proposals. These are explicitly synthetic source-policy premises, not evidence
from a live assessment. The baseline restores the previous evaluator input assembly while keeping
current semantic rules, system prompt and bound output schema identical to the candidate. The
candidate supplies the dedicated review packet with bounded saved observations. Absolute temporary
paths are normalized to a fixed isolated fixture path before hashing.

The input integrity tests verify pair coverage, unchanged publications/system/schema, complete review
instructions and saved evidence. The trials use the production native structured role runner, without
live tools; supplied source observations are complete for each synthetic fixture. Consequently this
comparison tests semantic judgment under complete frozen inputs, not artifact-reader behavior,
large real-operation context, or end-to-end acceptance. Readability/availability remain distinct from
semantic support. Each case records its designated semantic check; final promotion must also inspect
other semantic checks and whole-review consistency rather than treating one correct verdict as a
successful task. The shared allowance has 80 requests left after selection's 48 requests.

## Final coverage and review-study audit

The corrected full repository suite passed: **8,380 passed, 47 skipped, 89 deselected**. The changed-file
coverage check passed its 80% line and branch floors. No deployment was performed.

The initial semantic comparison completed 64 native structured decisions (32 per arm), all schema
valid, using 112 total requests together with selection. Its raw designated-check matches were
24/32 baseline and 22/32 candidate. These are **not valid semantic-effectiveness estimates**: the
fixture's frozen source reference pointed at an HTML artifact instead of an inventory manifest,
causing repeated unavailable-input grounding rejections. Exact identity-label scoring also counted
`adequate` versus `not_applicable` differences for anonymous controls as errors despite both being
potentially sound. Several synthetic positive controls need clearer policy and identity premises.
The full initial inputs/results remain preserved; no selective reruns occurred.

`review-inputs-corrected.json` fixes the inventory binding and passes an additional integrity test.
It has **not been submitted to the model**. This corrects one harness defect, not all control-quality
or scoring limitations. Before another separately frozen study, strengthen the affected positive
controls, define admissible anonymous-identity verdicts and validate whole-review consistency.
Do not consume the initial study's remaining 16 requests on a partial rerun and present it as a new
paired result. End-to-end promotion checks remain deferred because selection missed its gate and
semantic review has no usable comparison yet.

## Corrected review v2 study

A separate corrected study is frozen in `review-inputs-v2.json`, with its protocol in
`review-v2-protocol.json`. Six integrity tests passed before its first model request. An earlier v2
preflight input set was archived before requests because remaining anonymous, network and filesystem
controls needed concrete semantic effects and the role catalog omitted observer/maintainer. The final
inputs include those roles, a valid frozen manifest, saved source/policy content and explicit controls.
DOM controls distinguish active markup from text without server reflection. CSRF controls specify a
cookie-bearing protected action and browser-origin behavior. Property/parser controls specify reserved
state and permitted baselines under a declared single user identity. Identity-only cases retain their
designated-check purpose; inspect other checks and dispositions before calling any whole proposal valid.

The corrected study plans 64 decisions with a separate 80-request cap, following the user's standing
unlimited-request authorization. It writes only to the isolated `review-v2` output directory. Original
112-request results and counter remain unchanged. Controller consistency validation is applied to each
subject review. Anonymous adequate/not_applicable equivalence is declared before inference. No completed
case is selectively rerun. No production prompts were tuned based on either study's model outputs.

## Corrected review result and promotion decision

The corrected study completed 64 requests with valid native output and controller-consistent reviews.
Both baseline and candidate scored **30/32** on the designated checks, detected all **14/14** defect
trials and retained **16/18** valid control trials. Both rejected the single-admin plus anonymous-control
case in both repetitions, incorrectly treating `comparison=none` as incompatible with that procedure.
Other checks and whole-proposal dispositions confirm the same failure rather than a scoring-only
label difference. Mean complete input length was 27,133 characters baseline and 26,382 candidate,
a modest 2.8% reduction in this corpus. The smaller input did not improve semantic accuracy here.

Neither change is promoted. Task-local selection substantially improved suitability on its fixed
corpus (13/24 to 23/24) but rejected one valid guide. The isolated review refactor showed no measured
semantic gain and missed the same valid identity control as baseline. These are narrow boundary
experiments: selection did not exercise preceding alignment/applicability stages and review used
complete synthetic fixtures without artifact-reader calls. Do not present their rates as production
or end-to-end accuracy. No extra critics, stricter model prompts or selective retries were introduced.

The planned gated end-to-end phase is deferred. Its prerequisite gates failed; running it now would
not turn either candidate into an approved improvement. Further work should isolate the two specific
contract-framing problems: testing guidance versus enforcement implementation in selection, and one
authenticated identity plus an anonymous control in review. Prefer clearer controller-presented
semantics or task decomposition over another layer of prompt prohibitions. Preserve these trials as
regression cases and use a separately frozen comparison for any subsequent change.

The implementation remains uncommitted and undeployed. Automated verification is complete for the
current production code: 8,380 tests passed, and every changed Python file meets the 80% line/branch
floor. Six research integrity tests and Ruff also pass. Total model requests across all three studies:
**176**, including the invalid initial 64-request review study, which is excluded from effectiveness
claims. See `selection-results.json`, `review-results.json`, `review-v2-results.json`, and
`validation.json` for the recorded outcomes and limits.

## Contract presentation follow-up (v3)

The next intervention is limited to presentation rather than more critic stages or stronger rubric
instructions. Review views decode authoritative metadata into `declared_authenticated_roles` and
`comparison_between_authenticated_identities`; canonical `none` displays as `not_requested` for the
latter. This does not infer anonymous controls, identity readiness or semantic adequacy. Stored
publications, tool contracts and public enums are unchanged. Selection now names its requested
deliverable using the same reusable-methodology value used in phase guidance. Pending automatic
selection contract bindings increment to invalidate old pending decisions; delivered receipts survive.

The fresh frozen review pair holds the dedicated packet, source-policy records, rubric, publication,
system prompt and native schema constant, changing only the rendered identity value. Selection holds
task-local context and its required task-relevance schema constant, changing only the deliverable
label. Thus its baseline is the earlier task-aware selector, not the methodology-only selector.
`review-inputs-v3.json`, `selection-inputs-v3.json` and `contract-view-protocol.json` preserve inputs and
rules before inference. The two repetitions plan 64 review and 48 selection decisions under a shared
128-request cap, with isolated outputs in `contract-view`. No selective reruns or prompt tuning are
permitted. These experiments are running; no outcome or promotion decision exists yet.

104 focused authentication/context/cache tests and 56 delivery/research-integrity tests passed after
the presentation changes. Full production coverage is rerunning. None of the earlier failed gates
or study results have been rewritten.

## Contract-view follow-up result and rollback

Both follow-up studies completed all 112 decisions within the 128-request cap, with valid native
structured output. Review baseline scored 32/32, while the decoded-view candidate scored 30/32,
missing the undeclared admin identity in both repetitions. Selection baseline and candidate each
scored 23/24: baseline rejected one valid SSRF guide; candidate delivered an open-redirect guide
for a different task once. Neither change passed its gate. The review baseline's improvement from
30/32 in v2 to 32/32 here also demonstrates run-to-run variability; do not interpret two errors as a
precise causal effect estimate.

The two presentation changes were reverted from runtime. Canonical identity metadata remains in
review views; selection no longer adds the extra deliverable field. The shared constant and display
helper were removed. Original schemas, publications and earlier unpromoted task-aware-selection and
review-packet work remain. This rollback does not promote those earlier candidates or modify their
failed study results. The tested source diff is archived as `contract-view-tested-worktree.patch`;
it includes earlier uncommitted work and is an audit snapshot, not a standalone patch to apply blindly.
The frozen v3 inputs remain the authoritative experiment inputs; their generator requires the tested
presentation implementation, while replaying existing frozen inputs does not.

Validation before rollback: 8,390 full-suite passes, 47 skips, 89 deselections, 80% changed-file coverage
floors passed. After rollback: 150 focused/research tests passed, Ruff and diff checks passed. The
coverage report predates rollback and is not a new measurement of reverted source line positions.
A fresh complete coverage run is required for the final worktree snapshot.

No model experiment is still running. Total model requests across the studies in this directory:
**288** (48 initial selection, 64 invalid initial review, 64 corrected review, 112 contract-view
follow-up). No deployment, library version increment, deletion, production run or authoring-cycle
change occurred. End-to-end useful adoption remains unproven; gated end-to-end trials are deferred.


## Subsequent diagnostic authorization and completion

After the failed gates, the user approved an isolated end-to-end diagnostic to learn about actual
outcomes without promoting or deploying the candidates. The six paired cases completed in the
[diagnostic report](../e2e-diagnostic-20261123/README.md): both arms achieved 6/6 supported correct
results and delivered the same guides. There was no incremental effectiveness or adoption gain.
This supersedes the earlier decision to defer all end-to-end work, while preserving failed promotion
gates. The final post-rollback full suite passed 8,381 tests and changed-file coverage floors.
