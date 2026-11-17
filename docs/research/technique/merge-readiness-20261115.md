# Merge-readiness review of accumulated technique work

## Completed practical changes

The corrected module document is published at
`src/modules/operation_plugins/web/techniques/header-selector-authorization.md`.
Its independent source selection, fresh critic, exact reviewed hash and parser validation are
recorded in [publication audit](header-selector-publication-20261115/README.md). Historical
experiment subjects remain unchanged. Publication means a module-owned file; global Qdrant was
not imported or modified.

The [full task-controller harness](full-controller-20261115/README.md) now exercises prompt
construction, execution, acceptance, evaluator and task-state transitions. The approved real
embedding preflight indexed 64 revisions and ranked the corrected document first. Four live
cases finished with two publication failures and two accepted artifacts that failed independent
supported-correct grading. These results do not establish increased usefulness; see the linked
report. The executor-core results remain separate.

## Scope and review grouping

Do not merge the entire dirty workspace as one undocumented change or discard pre-existing work.
Recommended review groups:

1. Corrected header-selector document, its publication audit and relevant changelog entry.
2. Planning/feedback/source-packet/author-format fixes with production code, their corresponding
   component tests, shared native-finalization truncation support and technique documentation.
3. Compact matching/trigger changes and their calibration fixtures, reviewed with their own
   earlier study; these predate the latest publication work.
4. Research harnesses, frozen input/output hashes, independent review decisions and synthesis.
   Retain manifests and exact subjects needed to reproduce conclusions. Keep private raw model
   conversations, SQLite/Qdrant stores and operation logs under ignored `outputs`, not in a PR.
5. Existing Langfuse skill refresh and file-upload additions should be attributed and reviewed
   separately. They were preserved; this work does not establish their independent review status.

Some changelog and documentation files span groups. Split their hunks deliberately when preparing
PRs; do not rename or delete artifacts merely to make the working tree appear clean. No commits,
PRs, staging changes or history rewrites were performed during this review.

## Verification and limits

Read-only Ruff checks passed for all 35 modified/new production and test Python files. New
research harness checks passed as recorded in their reports. `git diff --check` passed.
The focused final suite passed 93 tests, including 80 document checks and controller/core
preflights. The repository coverage entry point subsequently passed 8,113 tests, with 46 skips
and 89 deselections; selected Python files passed the 80% line and branch floors. The reported
combined coverage was 91% (including the research-harness coverage collected during validation).

A SQLite resource warning appeared during focused tests. No assertion failed; it remains a
lifecycle warning, not evidence of a clean resource-leak audit. The agent and Qdrant cleanup paths
are explicitly registered, but this review does not claim all underlying SQLite connection
lifetimes were audited.

No dependency versions, schema migrations, live-operation evidence, global scores or existing
experiment outcomes were changed. Production usefulness improvement is not established by test
counts. Live full-controller/catalog verification is complete with the limitations above. The proposed review groups
are a merge-preparation recommendation, not a request for permission to perform the completed
review or an assertion that all accumulated work belongs in one PR.


## November 16 follow-up

The [completed controller follow-up](controller-followup-20261116/README.md) supersedes the earlier
fixture's adoption interpretation: its lookup phase and assessed subject were misconfigured. The
corrected preflight recommends and reuses the intended document, but the eight-case study does not
establish reliable reasoning or useful natural document adoption. All diagnostic failures remain.

Review the live catalog schema-help/canonical-name fix and evaluator legacy-retry fix as production
changes, with component tests, task-tracking documentation and changelog entries. Treat corrected
fixtures, frozen scoring and advisory delivery as a separate research group. Advisory delivery remains
research-only. The final production suite passed 8,121 tests and all selected coverage floors; the
new real-SDK delivery and frozen-scoring tests passed in the 120-test closing focused suite
(research files: 97% combined coverage, all above the line/branch floors). The known
summary/source inconsistency and semantic misses remain explicit limitations, not approved behavior
or evidence of a clean whole-operation efficacy result. No staging, commits or PRs were performed.
