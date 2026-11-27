# Controller effectiveness follow-up

The plumbing is corrected, but reliable end-to-end reasoning and useful document adoption are not
established. The final eight-case cohort finished; [results and accounting](live-results.json) preserve
both the frozen artifact scores and the separate explanation review.

## Changes implemented

- Exact-name tool catalog queries return complete descriptions and runtime input schemas. Capability
  listings stay compact. Canonical mixed-case names work; ambiguous case-insensitive matches do not
  select an arbitrary tool. Tests use the real SDK registry and dynamically changed registries.
- Explicit evaluator repair metadata no longer enters the legacy instruction-driven correction path.
  A `repair.kind=none` response cannot trigger that fallback merely because explanatory instructions
  are present. Legacy responses without a repair object retain bounded correction. Semantic rejection
  remains rejection; this does not turn failed assessments into `done` or replace dedicated guards.
- The fixture provides readable inputs, clear session/selector/observation parameters, source HTTP
  request metadata, an explicit verification context and a simulated network subject. The input file
  is not the assessed subject. Its contract is frozen before task creation and permits eight simulated
  requests for own-account and cross-account comparison.
- Fixture scoring resolves accepted ledger references rather than a mutable `result.json` alias.
  Missing, escaping or conflicting accepted sources do not silently fall back to the newest file.
  Explanation correctness and evaluator consistency are reviewed separately from label correctness.

No technique procedure identities, additional critics, stricter semantic reviewer prompts, dependency
updates or SQL migrations were introduced. `delivery.py` is an experimental user-context wrapper;
automatic guidance delivery was not added to production.

## Experiment validity and readiness

Earlier cohorts omitted technique phase metadata and inherited the web module's phase-1 reconnaissance
filter. They also represented the fixture input file as a filesystem subject. Those no-adoption
observations cannot estimate behavior for a correctly assigned HTTP verification task. The historical
runs remain intact and labelled diagnostic; no completed failure was erased.

Correcting the phase and HTTP header metadata alone left the filesystem-subject preflight empty.
Representing the simulated endpoint as a network subject produced the intended recommendation in
**one generation request**, without changing matcher instructions or the document trigger.
A separate generic methodology request reused the published header-selector revision in **seven
requests**, with one resolved component and no unresolved parts or new technique required:
`73c8a6b9-8c05-5aaa-9345-029736792841`.

The final cohort used `vulnerability_verification`, target `https://fixture.invalid`, source-origin
observations and actual simulated `X-Account` request fields. No target request reaches that hostname;
only the registered fixture tools implement interactions. The module document remains unchanged at
SHA-256 `2ced46a1262fa7e83ff8a1f156fb24f5609efa0155319f106c9b6e82a107dca6`.

## Final results

| Policy | Arm | Requests | Controller | Frozen classification | Explanation review |
| --- | --- | ---: | --- | --- | --- |
| Unavailable | Lookup available | 20 | Partial failure | Inconclusive | Correct uncertainty; unsupported denial wording also present |
| Unavailable | No lookup | 13 | Partial failure | Inconclusive | Supported |
| Legitimate sharing | No lookup | 21 | Done | Violation | Wrong: error label overrides returned records and explicit policy |
| Legitimate sharing | Lookup available | 39 | Done | No violation observed | Label right, reasoning wrong: claims all reads were denied |
| Prohibited cross-account read | Lookup available | 20 | Done | Violation | Supported: identifies cross-account data outside private policy |
| Prohibited cross-account read | No lookup | 13 | Partial failure | Inconclusive | Misses the scripted disclosure because of the error label |
| Legitimate sharing | Advisory context | 21 | Done | No violation observed | Frozen artifact supported; acceptance summary/evaluator reason stale |
| Prohibited cross-account read | Advisory context | 21 | Done | Inconclusive | Misses the scripted disclosure despite supplied guidance |

All three lookup-enabled natural cases received the relevant recommendation. **None called
`get_technique` or retrieved the document.** The real SDK integration test confirms that recommendations
reach the next model input; they are not merely written to a cache.

Label-only scores were 3/3 with lookup available versus 1/3 without. That is not evidence of document
use: no document was fetched, one correct label has an unsupported explanation, and each condition
has only one case per arm. Recommendations, extra tool availability and sampling are not isolated
causal factors. Both unavailable-policy cases correctly retained uncertainty; their task status
remained partial because the frozen criterion requires an observed policy.

The advisory arm supplied the same previously resolved bundle as user data to the existing executor
and evaluator only. Builder/critic prompts and output schemas were unchanged. It yielded one correct
artifact out of two. In the sharing case the accepted snapshot's hash and content are valid, but a
retained acceptance summary and evaluator reason still describe an earlier inconclusive result.
This is a summary/source inconsistency, not frozen-file corruption. The vulnerable case remained
inconclusive. These outcomes do not support rolling this prototype out as a general effectiveness fix.
Advisory input estimates peaked at 11,665 and 11,598 tokens, within the 40,000-token context envelope;
no prompt-fitting boundary was exercised.

Publication/acceptance friction also remains: the sharing lookup case fabricated `memory:obs-*`
references despite having a valid result artifact, then needed repair. A local observation label is
not a durable memory reference. These attempts and their costs are included.

## Validation and accounting

The final production repository coverage run passed **8,121 tests**, with 46 skips and 89 deselections.
All selected files met the 80% line and branch coverage floors. The workflow component suite passed
1,016 tests with 17 skips. The closing focused suite passed **120 tests**, including real SDK recommendation delivery,
unknown/mixed-case tools, CLI output/limits, frozen scoring and HTTP/network/filesystem payload
preservation. Research coverage was **97% combined**, with every file above 80% line and branch;
the advisory-delivery and frozen-scoring modules had 100% coverage. Ruff and `git diff --check` passed.

Initial sandbox failures were denied local sockets, interface discovery and cache access; successful
repository runs used those local test capabilities. No global catalog, active-operation evidence,
existing scores or original experiment outcomes were modified. A SQLite lifecycle warning appeared
in focused coverage checks; this is not a completed resource-leak audit.

**446 generation dispatch attempts** were used under the user's unlimited allowance: **168** in the
final cohort, **9** in generic-delivery/recommendation preflights and **269** in retained diagnostic,
interrupted or superseded cohorts. Embedding, model-metadata and source-fetch calls are not included
in generation counts. Cold isolated catalogs/caches make these counts unsuitable as a production
cost forecast. Each case had a separate 100-request accounting ceiling; no final case exhausted it.

Raw traces, SQLite/Qdrant stores, per-case budgets, source snapshots and invocation scripts remain
under ignored `outputs/technique_review_trials/`. The final cohort is
`controller-network-ready-20261116`; its `protocol.json` records all eight cases and frozen sources.

For a fresh four-case basic run:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" PYTHONPATH=src \
  uv run python docs/research/technique/full-controller-20261115/run.py \
  --output outputs/technique_review_trials/my-fresh-controller-run --limit 100
```

Existing output directories are rejected. The eight-case controlled study invocation is preserved
with the final cohort; it separately labels advisory-context delivery.

## Interpretation and next direction

Existing documents can be recommended and reused without adding structured procedure identities.
The explicit-repair fix removes a real redundant execution path. Those are supported improvements.
Neither optional lookup nor the two-case advisory prototype establishes reliable reasoning gains.

The next work should improve the evidence presented to reasoning roles: paired baseline/variant
facts tied to explicit policy and immutable source versions, and evaluator input that exposes the
current frozen result rather than relying on a potentially stale summary. Keep comparison/provenance
facts controller- or adapter-owned and conclusions in the model. Test the actual intended operation,
not a synthetic error label alone. Do not add another critic or tune generic guards to this fixture.

Live semantic cases here are synthetic HTTP authorization comparisons, not whole-operation or
real-target trials. Scripted controller tests cover network and filesystem payloads but do not prove
live model efficacy in those domains. The label-only fixture grade is intentionally kept separate
from explanation review and evaluator consistency; `done` alone is not a correctness metric.
