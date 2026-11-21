# Incremental technique review corrections

This revision addresses a captured failure where a critic correction omitted its interpretation
table. The controller retains unchanged rows rather than asking the model to repeat them.
Every disputed row requires an explicit disposition; every independent finding requires a
referenced response. The assessor still judges the complete merged decision.

The earlier trial remains in ../technique-review-purpose/ and is not relabeled as successful.
This is an output-contract change, not evidence that the model's semantic reasoning improved.
No new live requests were made. The cumulative live request count remains 180.

Offline regressions: 2,289 passed, 18 skipped. Two additional complete SDK correction round
trips passed, covering native structured output and schema-tool output. Focused tests cover
invalid references, omitted dispositions, selective revisions, withdrawal, aggregate repair,
preservation and publication blocking after persistent disagreement. Both changed production
files meet the repository's 80% line and branch floor. Ruff passed.

The source snapshots, exact hashes and revision patch preserve this candidate. Coverage and
results are in results.json. Scripted responses establish transport and controller behavior
only; they do not establish live semantic acceptance.

Assembled-input review covered the initial full critic prompt, indexed critic correction,
assessor investigation/comparison, assessor contract correction, merged finding responses,
retained sources, subject-integrity hook, legacy paths and experimental correctness path.
Corrections rebuild their output instruction rather than appending a conflicting full-table
request. No new roles, public tool arguments, SQL migrations or score changes were introduced.

Next live acceptance should use the three frozen purpose-trial cases and evaluate reasoning,
not label agreement alone. Freeze the complete runtime identity before a newly authorized run.
Do not consume an earlier trial's unused allowance or overwrite its outcomes.
