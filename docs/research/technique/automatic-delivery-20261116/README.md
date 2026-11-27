# Opt-in production technique delivery

Implemented `TECHNIQUE_AUTO_DELIVERY=false` by default. Enabling it supplies one selected existing
document per task as advisory context. Optional lookup remains available. Automatic delivery cannot
start research or authorship, change acceptance criteria, or count as execution or contribution.

## Controller and prompt behavior

The executor's live hook waits for a task-local recommendation, then tries its highest-ranked lead
once. `select_existing` uses the existing compatibility and complete-document selection reviews,
including module restrictions and phase checks. A miss or unavailable selection preserves execution
and manual lookup. The task-local cache stores selection metadata, not a substitute document body;
first delivery reads current active content from the authoritative store.

The complete guide and compatibility assessment are serialized as untrusted data. No semantic
excerpt selector, procedure identity, new critic or contribution rubric was introduced. The source
document is never truncated to fit. Live insertion checks estimated input plus preferred output
reservation after normal prompt fitting. Missing or insufficient capacity defers delivery without
a receipt. Python freezes the exact supplied packet immediately before inserting it into model input.

Actor repair/continuation and hypothesis-publication checkpoints can replay the prior active packet
without a lookup tool. Replay is conservatively fitted as an optional whole packet; a tight input
retains the base task and evidence unchanged. Evaluator native/text paths receive the recorded
packet before fitting, where it can also be omitted as one section. The live hook removes an optional
packet when capacity becomes insufficient, preserving evidence and valid non-empty message blocks.
Inactive content is retained for evaluating past use but is not newly supplied for execution.

Reviewed initial execution, dynamic tool-observation recommendations, actor continuation, restricted
repair, publication checkpoints, evaluator structured/text calls, output correction and prompt-fitting
paths. Shared guidance wording labels compatibility as methodology-level only and keeps frozen task
scope and acceptance authoritative. Embedded document headings are JSON-escaped, so a fitter cannot
drop individual caveats or mistake document sections for controller optional sections.

## Live production-path check

One isolated legitimate-sharing HTTP fixture used the normal production controller and automatic
hook, without the earlier experiment delivery wrapper. It completed in **36 generation requests**.
The established header-selector guide was selected, delivered and retained in subsequent provider
inputs. The task finished `done` with a supported frozen artifact distinguishing permitted A-to-B
sharing from denied B-to-A access despite misleading error labels.

SQLite integrity passed before schema and receipt queries. Exactly one current-task automatic
receipt froze the supplied text. The unchanged contribution evaluator and independent reviewer
accepted a helpful **+1** judgment, with zero feedback corrections. Exact packet text appeared in
18 provider inputs; this measures availability, not reading or causal contribution. All recorded
generation calls used `muse-glimmer:30b-mlx`. No calls remain running.

The [curated result](live-result.json) preserves frozen accepted-payload and delivery hashes, feedback
and request accounting. Raw isolated stores, prompts and invocation script remain under
`outputs/technique_review_trials/automatic-delivery-pilot-20261116`.

The live check preceded the final optional-replay fitting safeguard. Tight-capacity behavior of the
final code is verified offline; the live case did not stress that boundary. This single synthetic
HTTP result does not establish broad utility, cost savings or real-target effectiveness. The pilot
did not modify global catalog feedback or publish new module documents.

## Deployment pilot

Deploy the updated application, set `TECHNIQUE_AUTO_DELIVERY=true` for the pilot process, and restart
that process. `docker/docker-compose.yml` forwards the setting. Run the representative operation
normally; inspect `technique_auto_delivery` events, exact receipt snapshots and contribution feedback.
The default stays false. Existing current-task snapshots remain available on continuation or review
after disabling the setting; new automatic selections stop.

The focused capacity/delivery suite passes **41 tests**, including HTTP, network and filesystem
method requests, unavailable selection, invalid caches, inactive revisions, exact snapshots,
idempotent replay, real SDK hook ordering and complete-packet omission. The source-inspection
regression plus that suite passes **42 tests**. Full final repository coverage and source hashes are
recorded separately in `validation.json`.

Final repository validation passed **8,179 tests**, with 46 skips and 89 deselections. Every measured
Python file passed the 80% statement/branch coverage floors. The delivery module has **100% statement
and 98.2% branch coverage**. Ruff and whitespace checks passed. The final code was held stable for
this run; an earlier source-inspection failure during overlapping edits was rerun successfully.
