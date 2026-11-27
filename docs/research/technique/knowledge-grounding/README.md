# Knowledge-allowed methodology and scoped source verification

This follow-up changes acceptance gates rather than adding another reviewer. The preceding
single-reviewer trial remains failed: it completed five reviews but falsely rejected network and
corrected HTTP procedures for lacking retrieved support. Cumulative prior live requests remain 227.

## Policy and controller behavior

Requirements now have independent grounding metadata:
knowledge_allowed (default) or source_required. Requirement kind does not determine grounding.
Model knowledge, reasoning, original controls and future checks are valid content bases.
Every document still needs at least one useful retrieved reference. Reference selection judges
relevance and attribution; retrieval success alone does not establish usefulness or correctness.

Only explicit source-required obligations receive per-requirement evidence/authority assessment,
coverage review and targeted research repairs. Examples are requested affected versions, vendor
statements and quotations. Source-required constraints cannot bypass these checks. Knowledge-allowed
requirements receive controller-generated summaries explicitly marked not source-verified. Selected
references remain available to authors; skipping a support inventory does not discard reference text.

Planning plus request review classify grounding using supplied request provenance. Direct lookup
uses the existing compatibility judge. No new role or keyword-based semantic classifier was added.
Legacy inputs default to knowledge_allowed when used for new invocations; archived records, scores
and prior experiment results are not rewritten.

Production publication replaces core:technical_support with core:correctness. The critic identifies
concrete errors and consequential flaws rather than absence from a reference. Source-specific
criteria exist only for source-required obligations. Attribution checks concern what is actually
attributed, not every step in a methodology. Uncertainty belongs in qualifications or future checks
unless it defeats the central method. The single critic and bounded output retry remain.

The publication schema no longer accepts a model-authored repair category. Python maps
source:<requirement> to research coverage, core:attribution to attribution repair, requirement:<id>
to missing requested content, and remaining criteria to authored content. References still establish
availability only; semantic correctness remains model-owned.

## Compatibility and evidence

Public tool arguments, document source minimum, SQL and usefulness evaluation are unchanged.
Existing author, attribution and research limits remain. The explicit offline two-review entry
point uses a legacy criterion catalog for old fixtures; production uses the new catalog.
No historical outcome is relabeled.

The five documents in cases.json are byte-identical to the preceding acceptance set. New explicit
knowledge_allowed requirement metadata changes the subject digests. fixture-provenance.json records
old/new digests, verifies the original raw subject hashes and records unchanged document hashes.
Labels are inherited from the human document adjudications under the newly authorized grounding
policy. This is a new input context, not an identical-input causal comparison.

protocol.json freezes runtime identity, case digests and the proposed future sequential 21-request
trial with muse-glimmer:30b. Its status remains prepared; no live dispatch is authorized.
source-hashes.json and source-snapshots preserve the exact code and tests.

## Verification

Tests distinguish general methodology from mandatory source verification across HTTP, network and
filesystem workflows. New focused cases cover useful background with no per-requirement calls,
the one-reference minimum, mandatory versions/quoted-policy evidence, direct lookup classification,
invented source criteria, criterion-derived ownership and metadata provenance. Existing source
retention and authority tests now explicitly declare source_required. Test fixture projection is
test-only; production does not accept a removed model repair-category field.

Native and schema-tool SDK tests exercise grounding metadata and category-free publication.
Ten exact-input scripted preflight checks passed with zero provider requests. These establish input,
schema and transport integrity, not semantic acceptance. Offline source-required negative controls
are listed in offline-controls.json. Final counts and per-file coverage are in verification.json.

Assembled instructions were reviewed across planner/request review, direct lookup, source selection,
source-required assessment and correction, authoring and repair, publication and output retry,
CLI/operation continuation, SDK schemas and prompt limits. The controller owns policy scope and
routing; the model owns semantic judgments. No deterministic vulnerability scoring was introduced.

## Next acceptance and limitations

After separate authorization, review all five frozen cases sequentially within the shared 21-request
cap. Require correct labels and justified corrections, including both known bad drafts. Unsupported
relabeling as correctness or attribution must be assessed semantically; schema compliance alone is
not acceptance. Preserve incomplete and failed results and do not expand allowances silently.

No new live calls were made. This policy is offline verified, not demonstrated to improve live
model reasoning. Model knowledge can still be wrong; material correctness review remains necessary.
Raw provider inputs belong under outputs/, while these portable records retain hashes and outcomes.

## Final offline result

The repository coverage entry point passed 7,846 tests (46 skipped, 86 deselected, 29 warnings).
Final technique revalidation after the source-required constraint refinement passed 1,206 tests
with one skip. All ten changed production files pass the 80% line and branch coverage floors;
Ruff and diff checks passed. Ten exact-input scripted native/tool preflight checks passed with
zero provider requests. Exact coverage summaries and limitations are in verification.json.
No new live calls were made; cumulative prior live calls remain 227.
