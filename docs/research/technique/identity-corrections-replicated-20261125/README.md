# Replicated identity-correction replay

This follow-up fixes the preceding diagnostic's fixture ambiguity and model-call overhead without
changing production behavior. Prior outputs under `identity-corrections-*` remain intact.

The six cases cover named and unnamed two-account comparisons, complementary rendering mechanisms,
permitted URL retrieval, linked DOM-source navigation and a complete safe filesystem consumer.
`cases.json` declares canonical sources, inline excerpts, necessary inspection and matching role catalogs.
The manual adjudication rubric is frozen before generation and is not shown to the model.

Both arms use the production correction dispatcher and real publication/revision tools with isolated
SQLite databases. Baseline uses archived pre-change dispatch/guidance and its own proposal field
semantics/validation. Candidate uses current code and schema. The common input publication is a frozen
review input, so it may contain an unnamed comparison that baseline cannot itself publish.

Model decisions are structured. All needed evidence is inline except the explicitly declared linked
DOM consumer, which remains available through the real scoped reader. Non-inspection cases use direct
native finalization. DOM cases use read-only investigation followed by structured finalization.
Python applies the validated decision through the bound revision tool, then the controller dispatches
availability-only acceptance deterministically with zero model calls. This acceptance is fixture
bookkeeping, not the production semantic evaluator. One schema-validation repair is allowed per trial;
mutation validation failures are retained failures. No complete individual trial is selectively rerun.

A fresh ceiling of **96 generation requests**, including errors and repairs, applies to this study.
The preceding study's 96 attempts remain separate historical costs. Planning always includes all
24 trials: six cases, two arms and two repetitions, with counterbalanced arm order. No time reserve or
operation-budget scheduling is introduced. Freeze refuses any existing output directory; no live
operation is restarted, deployed or tested.

Offline verification:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  docs/research/technique/identity-corrections-replicated-20261125/test_replay.py -q
```

The live driver first checks model availability without generation, then freezes the cases, matrix,
both schemas, rubric, baseline resources and relevant source snapshots before any generation request:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run python \
  docs/research/technique/identity-corrections-replicated-20261125/replay.py
```

Raw requests, responses, schema/repair packets, source-read receipts, publications, digests and databases
are retained under `outputs/technique_review_trials/identity-corrections-replicated-20261125/`.
Read `results.md` for measured outcomes and limits. Valid structured output, successful mutation and
availability do not by themselves establish semantic adequacy.
