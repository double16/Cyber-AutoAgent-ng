# Technique review calibration

Status: the latest revised HTTP replay passed with independent agreement and no correction.
The original matched trial exhausted its allowance and remains incomplete.
Human fixture adjudication is confirmed in [adjudication 2](human-adjudication-2.json).
[Live trial 1](live-results-1.md) has final, incomplete outcomes. A separately authorized baseline-HTTP
follow-up completed in 5 of its 10 allowed requests, correctly rejecting the defective draft.
See [the baseline supplemental result](baseline-http-supplement-results.md).
The separately authorized [candidate-HTTP follow-up](candidate-http-supplement-results.md)
failed its controller contract after one correction, using 4 requests. Total live requests across
the original trial and the first two follow-ups: 39. The later catalog revision and its replay
are recorded below; the final total is 44. Production acceptance remains unestablished.

The baseline includes the new review policy: blockers and suggestions, controller-derived approval,
criterion-linked materiality and full-document integrity checks. The earlier committed version is
an ancestor for reconstruction, not the active comparison policy.

`baseline.json` identifies the ancestor commit, exact runtime hashes and the reconstruction patch.
In an isolated checkout of that commit, apply `baseline.patch` using `git apply`. Verify reconstructed
files against the manifest before inference. The patch includes new Python modules needed by the
policy; generated technique catalogs are not part of this matched review input.

`protocol.json` defines the model, budget, case families and adjudication requirements. [The corpus](corpus.json)
contains complete controller-framed documents, exact retained source context and explicit
requirement kinds. Each acceptable/needs_correction label must bind the exact input digest and include
a rationale. Pending or stale adjudications prohibit live inference.

Compare the new-policy baseline with the final candidate on three fixed cases. Alternate arm order,
retain every outcome, and stop at the cumulative request cap. An exhausted allowance leaves remaining
cases incomplete. Approval counts do not establish useful-document yield or production readiness.

Existing failed trials remain failed. Their known harness errors and missing temporary artifacts are
reported in the [earlier acceptance report](../technique/technique-generation-acceptance.md).

## Prepared fixtures

Review the exact [network](fixtures/network-policy.md), [filesystem](fixtures/filesystem-policy.md)
and [HTTP](fixtures/http-selector.md) subjects. Record `acceptable` or `needs_correction` plus a
reason against the input hashes in [preflight-inputs.json](preflight-inputs.json). The model never
receives human labels. Do not change a subject after adjudication without obtaining a new label.

[Offline preflight 3](offline-preflight-3.json) records all 12 passing case/arm/transport outcomes,
exact runtime identities and input hashes. This is scripted contract verification, with zero live
provider requests and no semantic judgment. Earlier attempts remain in private outputs: preflight 1
failed because baseline worker job paths were relative; preflight 2 passed before later code changes.
The live entry point repeats preflight for the exact candidate identity before using its receipt.

From the repository root, validate inputs without inference:

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run python -m modules.techniques.review_calibration \
  docs/research/technique-review-calibration/corpus.json
```

Use `--verify-transports --baseline-root <reconstructed-baseline>` for scripted checks. After all
labels are recorded, `--run --baseline-root <reconstructed-baseline>` performs fresh preflight and
the bounded live comparison. Use a new output directory for each attempt; retain failures.

## Verification

[Verification record](verification.json): final affected suites passed 2,197 tests with 18 skips;
Ruff and whitespace checks passed. The repository coverage entry point ran the full suite; its
seven failures were stale test exception expectations, corrected and passed in the final rerun.
The other 7,685 tests passed. Full-suite coverage plus the final affected rerun passes the 80%
line and branch floor for every measured source file. The failed initial result remains recorded.

Assembled critic and assessor inputs were exercised through native and schema-tool SDK paths,
including independent investigation/finalization, correction prompts, full-document preservation
and prompt fitting. These checks validate contracts, not model reasoning quality. The live trial remains incomplete; semantic acceptance has not been established.

## Reference catalog follow-up

The [reference catalog revision](reference-catalog-revision.md) addresses the candidate HTTP
contract failure. Its offline regressions and all 12 transport checks pass. The [unchanged HTTP replay](candidate-http-reference-catalog-results.md) completed successfully
in five requests, with independent agreement and no correction. It used only the remainder of the
original candidate-HTTP allowance: nine of ten requests used across both revisions. Total live
requests across all recorded trials are 44. The prior failure remains preserved; this single-case
recovery does not establish general reliability or useful-document generation yield.

## Remaining acceptance and yield work

[Final-revision positive acceptance](final-revision-acceptance/README.md) contains an exact corrected
HTTP draft, the unchanged network/filesystem subjects and an authorized 21-request protocol.
The [positive run](final-revision-acceptance/results.md) completed with two approvals and an
unresolved filesystem excerpt-contract failure, using 17 of 21 requests. A separate
[control-grounding run](control-grounding-revision/results.md) completed with network and filesystem
approvals but unresolved semantic disagreement on corrected HTTP. It used 18 of 21 requests;
cumulative live requests are 79. No calls remain running. The next intervention concerns
support-versus-synthesis scope, operational conclusions and attribution requirements.
[The generation-yield design](generation-yield-next-phase.md) keeps document generation separate
from review accuracy and preserves the current excerpt rule until a separate comparison.
