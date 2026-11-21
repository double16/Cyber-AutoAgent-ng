# Conditional generation-yield pilot

This candidate-only pilot is prepared, not authorized or started. It asks whether the accepted
review revision can produce materially correct, useful documents from three fixed requests:
HTTP, network and filesystem. No comparative improvement or production reliability claim can
follow from this pilot alone.

[Cases](cases.json) contain only requests, explicit requirement kinds and bounded retained source
context. Reference documents and human review labels were excluded. Each input has a content
hash. The source pool reuses known provenance but not research-readiness receipts. Fresh source
selection and requirement-support assessment must decide whether it is adequate before drafting.
If adequate support cannot be established, preserve that failure rather than forcing a document.

[Protocol](protocol.json) proposes a global cap of 30 sequential provider requests, including all
alignment, assessment, authoring, critique, independent review, repair and retries. All three
requests remain planned regardless of budget. Exhaustion leaves unfinished cases incomplete.
Production repair ownership and decision correction limits remain unchanged. A model call or
controller approval alone never counts as useful-document yield.

Measure human-accepted complete useful documents divided by all three frozen requests. Human
adjudication binds exact output hashes and reasons. Separately record model approval, source
failure, author exhaustion, contract failures, semantic disagreement and budget exhaustion.
Preserve rejected drafts and the last complete authored version for review; do not manufacture
missing output or replace a failed request with an easier one.

Use normal production assembly and review paths with isolated SQLite/Qdrant and exports. Do not
publish module techniques, open active operation storage, import reference drafts as examples or
rewrite historical scores. Raw model payloads stay private under outputs; shareable records,
provenance, exact generated documents free of secrets and human judgments belong here.

The next implementation step is the isolated generation harness and scripted SDK preflight.
No new live allowance has been granted. The earlier review experiments used 98 live calls.

## Harness implementation

`src/modules/techniques/generation_pilot.py` now drives production request alignment, source
assessment, authoring and review with a fresh per-request SQLite store and in-memory Qdrant.
The isolated index uses a local constant vector because retrieval starts empty; this pilot does
not measure embedding or retrieval quality. Frozen research returns only retained records and
never fabricates additional sources. Generated documents remain pending human adjudication.
Failures and lifecycle events are saved alongside private exports under the supplied output root.

Unit tests exercise production generation, rejected inputs, failure capture, source isolation and
legacy/structured adapters. Provider transport instrumentation now counts each actual attempt, including failures, and
restores the selected transport afterward. Tests confirm rejected-author diagnostics and exact
review-subject exports are retained. Full scripted SDK preflight still needs integration before
any live execution.
There is deliberately no live CLI entry point yet.

## Scripted SDK integration

The full production generation path now passes scripted tests through the real Strands SDK in
both native structured-output and schema-tool modes. Payloads are scripted and do not represent
semantic judgments. The technique suite passes 1,083 tests with one skip; harness coverage meets
the per-file line and branch floor and Ruff passes. No live requests were made.

These are SDK unit fixtures, not yet a preflight receipt for all three exact pilot inputs. The
exact-input coordinator and its authorization gate remain to be implemented and tested before
requesting a live allowance. No live CLI has been enabled.

## Exact-input coordination

`generation_pilot_coordinator.py` now checks the ordered input manifest and frozen runtime,
requires both output modes to complete with recorded scripted SDK dispatches, rejects provider
requests during offline preflight, and writes a receipt only after every case succeeds. Live
execution requires explicit authorization, a bounded cap and an exact matching receipt. Cases
are planned independently of the cap; failures and unattempted cases remain recorded. Workers
execute sequentially with one persistent budget. This is an internal callable API, not an enabled
live CLI. The exact-input SDK worker still needs integration before this pilot can run.

## Exact preflight complete

[The exact-input record](exact-preflight.json) now contains six passing checks: all three frozen
requests through native and schema-tool SDK paths, eight scripted model dispatches each. The final
technique suite passes 1,109 tests with one skip. All four harness files meet the per-file 80%
line and branch coverage floor, and Ruff passes. Earlier preparation notes above describe their
creation-time state; this section is the current readiness status.

The live CLI is implemented but requires explicit authorization and an allowed request cap in the
frozen protocol. An unauthorized invocation was checked and rejected before output creation or
provider dispatch. The default command is scripted preflight only:

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" \
  uv run python -m modules.techniques.generation_pilot_cli \
    --protocol docs/research/technique-generation-yield-pilot/protocol.json \
    --cases docs/research/technique-generation-yield-pilot/cases.json \
    --output outputs/technique_review_trials/NEW-UNUSED-DIRECTORY
```

After user authorization is recorded, `--run` repeats exact preflight and invokes production
models with one shared provider-request allowance. Every real request, including retries, counts.
Each input receives fresh isolated storage and all outputs remain pending human adjudication.
No active operation or module publication is used. Frozen-source research cannot retrieve new
articles; an inadequate foundation remains a failure in the three-request denominator.

The proposed live cap is 30 sequential requests, approximately 24 base calls in scripted preflight
plus room for retained-source inspection and corrections. Actual live behavior may exhaust it;
that produces incomplete outcomes rather than permission to exceed or replace cases. No new live
allowance has been granted yet. Total live calls from earlier experiments remain 98.

## Live pilot started

The user authorized up to 30 new sequential provider requests. The fixed three-request pilot
is running under `outputs/technique_review_trials/generation-yield-live-20261106-1`, beginning
with fresh exact-input preflight. Real outputs are not yet adjudicated; no yield result can
be claimed until the trial outcomes and exact generated document hashes are reviewed.

## Pilot 1 outcome

[The first live pilot](trial-1/README.md) completed in 19 of 30 requests: one filesystem document
awaits human usefulness judgment; network and HTTP failed research coverage before authoring.
Model-approved output rate is 1/3; useful-document yield remains pending. Total live calls across
all experiments are 117, with none running. The failure records and exact output are preserved.

The filesystem output is now human-adjudicated acceptable and useful. Final conditional useful
document yield is 1/3 (33.3%). The user prefers the headings, numbered lists and specific technical
language. The two research failures remain in the denominator; no matched yield comparison has
been performed.

## Upstream scope follow-up

[A separate prepared revision](upstream-scope-followup/README.md) shares support-scope guidance
with requirement assessment, incorporates the user's author-format preferences, and clarifies
interpretation coverage for methodology checks. All six exact-input SDK checks and offline
regressions pass. It awaits a new 30-request allowance; no new live calls have been made.
