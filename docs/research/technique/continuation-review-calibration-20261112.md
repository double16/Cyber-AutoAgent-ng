# Continuation review calibration, 2026-11-12

The regression fixture comes from operation OP_20261020_110442, continuation starting
2026-11-11 13:26:44. Historical technique scores and operation records were not changed.

## Method

The [fixture](../../../tests/techniques/fixtures/continuation_review_connections.json) retains
the entire captured static JavaScript procedure, including H3 version prerequisites and future
checks, plus sanitized Swagger/chat and authentication/static-asset shared-bundle decoys.
The test assembles inputs through production selection and recommendation code. Scripted mode
tests contracts only. Provider mode submits those inputs and the production structured system
prompt to installed Ollama model `muse-glimmer:30b-mlx`, at temperature 0, low reasoning,
48,000 context tokens and an 8,192-token output allowance. It contacts the model only, not a target.
The direct provider check does not exercise the role runner's accounting, retry or budget hooks.

Run provider validation using the configured model host:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  tests/techniques/test_continuation_review.py -m ollama \
  --ollama-model muse-glimmer:30b-mlx --ollama-host "$OLLAMA_HOST" \
  --ollama-timeout 600 -q
```

## Results and limits

The final check passed all three cases in one test, in 74.61 seconds:

| Case | Expected and observed result |
|---|---|
| Complete static JavaScript procedure | Reused; version identification and future checks count despite different risk framing |
| Swagger subject with chat operation elsewhere in a shared bundle | No chat recommendation |
| Authentication workflow with a vendor library elsewhere in a shared bundle | No static-library recommendation |

Earlier development checks exposed an output-limit failure at a smaller 4,096-token allowance
and mistaken passage numbers accompanying exact quotes. The final contract permits Python to
resolve a known but mistaken passage number only when the exact quote occurs in one submitted
passage. Unknown IDs, absent quotes and ambiguous matches remain unresolved. This adds no model
calls or production retry allowance.

This is a small calibration smoke check using reconstructed observations and one model. It
does not establish a population accuracy rate, live-target effectiveness, or immunity to
semantically incorrect judgments. Exact reference correspondence validates provenance, not
semantic relevance. A deployed continuation remains separate validation.
