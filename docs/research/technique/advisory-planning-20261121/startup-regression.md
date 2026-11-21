# Continuation startup lineage collision

The November 21 continuation beginning at 15:06:01 failed during module synchronization with
sqlite3.IntegrityError: UNIQUE constraint failed: technique_revisions.lineage. The traceback is at
lines 634780–634802 of outputs/bc/OP_20261020_110442/cyber_operations.log.

The new restoration path scanned only module-origin revisions, then promoted a historical module
revision without accounting for a current revision from another publisher in that lineage. A
compatible generated successor reproduces this failure through the actual module synchronization
entry point. The unique index prevented duplicate current revisions; this failure does not require
manual row deletion, index removal or a database reset.

Synchronization now preserves an eligible current successor when the module source is unchanged,
including a successor deactivated by feedback. If the current owner is absent or incompatible,
restoration supersedes any remaining owner in the same transaction before promoting the module
revision. Existing documents, uses and scores remain intact. Source-content changes continue to
publish their own module revision through the existing publication path.

Regression tests cover active and feedback-deactivated generated successors through module service
synchronization, repeated synchronization, and fallback from an incompatible generated revision.
They retain the unique index and verify lookup results and historical uses. No production database
rows were edited and no continuation was restarted during this fix.

Final validation: 1,605 technique-component tests passed, with one skipped. The changed repository
file has 99.03% statement and 95.83% branch coverage, checked with the repository coverage-floor
implementation against the final component run. Ruff and whitespace checks pass. Detailed results
and the source hash are in startup-regression-validation.json.
