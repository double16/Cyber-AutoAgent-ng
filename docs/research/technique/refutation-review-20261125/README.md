# Matching refutation review

This follow-up preserves the exact HTTP proposal missed in the
[previous isolated continuation](../identity-corrections-production-20261125/results.md).
It tests the shared instruction change against six frozen cases: incomplete and complete future
plans for HTTP destination policy, TCP gateway connection policy and filesystem containment.
Each complete plan differs from its incomplete counterpart only by a matching refuting observation.
Case IDs are neutral; expected verdicts remain in the harness and never enter task or evidence inputs.

The change keeps the existing `test_discrimination` verdict and reason. It asks the model to assess
supporting and refuting observations of the same probe for every claimed consequence. Successful
baselines and unrelated invalid-input controls cannot replace that refutation. The observation may
appear anywhere in the plan. No schema, keyword classifier, new critic or execution requirement is
introduced. The controller continues to own publication bindings, completeness and routing.

`replay.py` freezes sources, incident data and fixtures before generation. It runs two repetitions
in independent SQLite/Qdrant stores, reversing case order in the second. Native production structured
reviews use the real controller, review schema, provenance checks and persistence. The model is
`muse-glimmer:30b-mlx`. Deterministic fixture embeddings avoid remote embedding calls.

After all reviews, one preregistered repair continues the first HTTP incomplete task through the
production worker and acceptance/evaluator path, using its pending correction state. If the evaluator
mistakenly completed it, the repair is skipped and the miss retained. No completed trial is rerun.
All review, schema-repair, worker and reevaluation requests share a 96-request cap. Raw requests,
responses, failures, journals and source snapshots are retained in
`outputs/technique_review_trials/refutation-review-20261125/`.

Only retained-artifact and bound bookkeeping tools are configured. The inference service is the sole
network dependency used for generation; no real target, browser, shell or credential tools are given
to the model. Previous operation state and evidence remain untouched. No deployment or live operation
restart is part of this study.

Run offline tests:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  docs/research/technique/refutation-review-20261125 -q
```

Run a fresh study from the repository root with the configured Ollama service:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run python \
  docs/research/technique/refutation-review-20261125/replay.py
```

An existing output directory is rejected. Read `results.md` for measurements and limitations. Offline
scripted verdict tests verify transport and routing; the actual model replay assesses instruction
following. Neither constitutes executed vulnerability evidence.

The finished study matched all 12 review judgments and used 38/96 requests including repair. The
worker revised the HTTP plan and received semantic approval, but the live acceptance call was rejected:
the initial harness omitted task activation. The current driver fixes that lifecycle step, verified by
a real-tool regression test; the completed model case was not rerun. Read the results for that
qualification and the repaired plan's remaining timeout/policy-attribution ambiguity.
