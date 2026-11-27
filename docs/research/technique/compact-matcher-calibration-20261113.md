# Compact matcher and phase contribution calibration

This change reduces matching to lookup leads containing short trigger, subject and observation IDs.
Python owns provenance and cache identity. There is no confidence score, exact quote, digest or
per-match explanation in the model response. Semantic relevance remains model-owned.

## Checks and limitations

The installed `muse-glimmer:30b-mlx` model completed the continuation calibration on November 13:
one static JavaScript technique was reused and two unrelated shared-bundle leads were excluded.
The run completed in 67.66 seconds without contract failures. This is a small calibration of the
compact response and passage-ID contracts, not a production reliability estimate. The final batched
matcher prompt is additionally covered by scripted tests; the live sample preceded subject batching.

Two sanitized hypothesis artifacts from continuation `OP_20261020_110442` are frozen in
`tests/techniques/fixtures/phase_contribution.json`. The source task IDs and original artifact
digests are retained. Historical neutral scores remain unchanged. The proposed positive reference
scores ask whether delivered guidance contributed conditional hypotheses, controls and future checks,
without requiring confirmed server-side path resolution. They are calibration expectations, not
replacement production evaluations.

The first live attempt reached the 120-second request timeout for both cases. A rerun with a
600-second timeout per request completed in 570.15 seconds: both cases passed, with evaluator and
independent reviewer each deriving +1 (helpful) and no material errors reported.

| Frozen case | Historical score | Evaluator | Independent reviewer |
| --- | --- | --- | --- |
| Azure-path conditional hypothesis | 0 | +1 | +1 |
| DigitalOcean-path conditional hypothesis | 0 | +1 | +1 |

The judgments recognize the observed conditional hypothesis structure, prerequisites, controls,
expectations and evidence requirements without demanding completed target validation. Historical
scores remain unchanged. The [retained decisions](phase-contribution-live-20261113.json) include the
fixture hash and all four structured outputs.

One output inconsistency remains: both DigitalOcean responses retained `neutral_basis=not_used`
alongside helpful_contribution=true. Python derived +1 from the contribution findings, and the
independent reviewer did not flag the metadata inconsistency. This does not change the derived score,
but the run must not be described as entirely free of output inconsistencies. No business logic was
changed during this rerun.

This is a two-case calibration, not evidence of general scoring reliability. Scripted tests establish
assembled-contract consistency only; model completion alone is not semantic approval.

## Reproduction

Run from the repository root using the installed model; no model download is required:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  tests/techniques/test_continuation_review.py -m ollama \
  --ollama-model muse-glimmer:30b-mlx --ollama-timeout 120 -q -s
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  tests/techniques/test_phase_contribution.py -m ollama \
  --ollama-model muse-glimmer:30b-mlx --ollama-timeout 600 -q -s
```

## Final deterministic verification

The repository coverage entry point, selecting technique and native-structured tests, completed
with 1,413 passing tests and one skip. The repository coverage-floor evaluator passed for all nine
changed/new source files: minimum line coverage was 97.4%, and minimum branch coverage was 90.9%.
Ruff and whitespace checks passed. These checks cover contracts and controller behavior; they do
not replace broader live usefulness calibration beyond these two cases.
