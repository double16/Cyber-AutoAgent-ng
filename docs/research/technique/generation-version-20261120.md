# Generated technique compatibility and prompt simplification

## Implementation

Generated revisions now carry a controller-owned `generation_version`, initially 1. This is distinct
from Markdown `schema_version`. Migration 0016 adds a nullable column without relabeling legacy
rows. Generated rows require an exact version match; module-authored documents follow their
existing lifecycle. No environment setting or model-authored version field was introduced.

The eligibility check applies to document and trigger retrieval, new use receipts, explicit reuse,
and executor continuation replay. Qdrant filters exclude incompatible documents before retrieval
limits; repository metadata is checked again before delivery. A version-specific index identity
forces reconciliation of existing indexes after an upgrade. Selection-cache and generation-lease
identities include the version. Generated revision identities include the version while keeping the
source hash and lineage separate. Historical documents, feedback, and exact delivery snapshots
remain available for investigation; historical snapshots are not rewritten as fresh deliveries.

Agent-facing rendering keeps the complete procedure, authored applicability, source references,
and revision identity. It omits controller source-coverage commentary and lifecycle feedback.
Selection, authoring examples, explicit lookup, and automatic delivery use this rendering. Produced
and evaluated diagnostic exports preserve full metadata. Resolution-event origin comes from the
repository, rather than from rendered frontmatter.

Future-test identity guidance now follows proposed steps, independently of the generation task's
execution authentication. Reviewer prompts contain bound IDs without prefilled status, decision,
or semantic-check judgments. The legacy internal schema fixture remains available to existing
contract tests but is not rendered into agent prompts. A short authoring example contrasts token
stability with cross-session consequences and public configuration with protected information.
The author/advisory-critic/optional-revision lifecycle is unchanged.

## Assembled instruction review

Reviewed builder, prompt critic, revision, bounded repair, executor, and evaluator assembly through
the existing multi-module prompt tests (web, web reconnaissance, CTF, source security, and base
fallback), including fitted evaluator prompts. The shared identity rule is present on each path;
review IDs survive fitting without affirmative judgments. Native finalization closes investigation
and permits completing assessments from supplied evidence; it does not request publication changes
to repair the review itself. Technique structured schema correction retains the original role and
request. Neither path requires unavailable tools for finalization. Author format repair and optional
advisory revision inherit the authoring instructions and supplied source context.

Limitations: these assembly checks establish consistency, not semantic correctness. Future
observations, installed tools, and target-specific generated instructions can differ from fixtures.
The semantic replay below exercises the actual evaluator rather than the entire authoring pipeline.

## Validation

The companion JSON preserves the protocol, source hashes, reviews, request counts, rendering
measurements, and raw-record hashes. Nine sequential trials used the actual production evaluator,
native investigation/finalization, and fresh controller binding validation. Each trial completed on
its first evaluator attempt, using two model requests (18 total).

| Case | Expected decision | Agreement |
| --- | --- | --- |
| Historical CSRF publication with identity and discrimination defects | Revise | 3/3 |
| Historical OIDC publication with ambiguous public/protected metadata criteria | Revise | 2/3 |
| Source-grounded conditional file-containment proposal with baseline and control | Retain | 3/3 |

All nine reviews satisfied controller validation, but only eight matched the expected semantic
status. All CSRF reviews identified the authenticated/cross-session identity mismatch. The OIDC
acceptance incorrectly treated presence of client metadata fields as sufficient discrimination.
Some revision explanations also lean too far toward demanding observed confirmation of future
prerequisites; their correct status does not establish fully correct reasoning. The valid conditional
control was retained in all three repetitions, which is useful but limited evidence against blanket
rejection of uncertainty. No additional critic or stricter fixture-specific reviewer prompt was added.

These are reconstructed, smaller frozen inputs using historical publication content, not exact
replays of the original Langfuse calls. There is no baseline arm, newly authored document trial, live
target interaction, or full operation. Do not infer causal improvement, better authorship, or increased
useful adoption from these counts.

On two historical produced snapshots, clean rendering reduced 15,400 characters to 11,761 and
16,311 to 12,140 while preserving each complete procedure body. This measures document rendering,
not whole-prompt tokens or observed inference savings.

The final repository coverage entry point passed **8,339 tests**, with 46 skipped and 89 deselected,
in 805.85 seconds. All selected Python files met the 80% line and branch coverage floors. Ruff and
whitespace checks passed. The final log is `/private/tmp/generation-version-verified-coverage.log`.
Earlier runs were superseded after reconciliation fixes, fixture updates, and final line formatting;
the counts above are from the final source layout. No production code changed after that run began.

Regression coverage includes null/old/future generation versions, module compatibility, migration
preservation, version-sensitive revision identity, cache isolation, retrieval limits, index rebuilding,
executor replay versus historical snapshots, complete procedure rendering, diagnostic history,
and assembled/fitted hypothesis instructions.

Implementation and regression validation are complete. OIDC semantic discrimination remains
inconsistent in this small replay; broad effectiveness and fresh-document authorship remain unproven.

## Rollout

No active operation was restarted or deployed during this work. On deployment, old generated
revisions become ineligible and replacements are generated on demand. This can cause a temporary
increase in generation activity. Increment the code version for material authoring or methodology
changes rather than routine edits. Do not bulk-delete old documents or reset feedback.

A fresh continuation is still required to establish useful downstream adoption and test quality.
