# Bound technique-selection output

## Implementation

New generations use a request-bound selection schema with the existing fields and verdicts.
Requirement IDs are literal supplied values and the judgment count matches the request count.
Two schema branches distinguish supported judgments (covered or conditional, with passage references)
from unmet judgments (with a required non-whitespace missing-capability description).
Python still checks complete ID coverage, duplicates, citation provenance and semantic review routing.
Stored selection reviews keep their existing schema. Cache fingerprints now include the generation
schema so cached results from an older contract cannot satisfy a changed generation contract.

The complete-document initial and correction prompts are unchanged. The new schema is supplied on
both paths, including native finalization and schema-tool fallback. No critic, semantic policy,
procedure identity, tool field or database migration was added.

## Validation

Component tests cover valid verdicts, foreign IDs, missing or blank missing-capability fields, missing
passages, wrong list length, multiple requirements, controller-owned duplicate detection, compatibility
with stored reviews, schema-cache invalidation, and real native/schema-tool transport. Both Pydantic
generation schemas and runtime StructuredOutputTool schemas are tested. 290 component tests passed.
The final full suite passed 8,306 tests, with 46 skipped and 89 deselected, in 810.16 seconds.
Every selected Python file met 80% line and branch coverage. Ruff and whitespace checks passed.
The superseded coverage run was stopped after the grammar-pattern correction; these results use
the final schema and tests.

## Local-model replay

Reused the nine reconstructed selection fixtures from the preceding ownership study: three repetitions
each of optional presentation details, multipart upload guidance, and a missing explicit temporal
comparison. These use actual production selection and structured output with muse-glimmer:30b-mlx,
without target traffic. They do not measure production adoption or downstream effectiveness.

The first bound-schema arm was contract-valid in nine of nine trials with no correction retries, but
all three unmet judgments emitted only "d" for missing_capability. Their explanations identified the
missing control correctly. This was not counted as adequate missing-capability detail. The initial
non-whitespace pattern allowed one character under whole-string native grammar matching. The corrected
pattern allows full descriptions while still rejecting all-whitespace strings; a regression covers
both JSON Schema validation and whole-string matching, including multiline descriptions.

The corrected arm passed nine of nine trials, each on its first attempt, with nine model requests.
All three negative controls named the full missing before-expiry/after-expiry comparison. Relevant
guidance was selected in all six positive cases. Across both arms, 18 sequential model requests
were used. This is a regression check on a small repeated fixture set, not an estimate of broad
selection performance. The corrected arm meets the controlled regression gate. Deployment and a
fresh production continuation remain separate steps; no deployment or restart was performed.
Preserve both arms under
`outputs/technique_review_trials/bound-selection-20261120/`; corrected-arm attempts are retained as
JSON model dumps with assembled inputs and schemas.
