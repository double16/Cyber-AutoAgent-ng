# Task-relevant technique matching and hypothesis comparison

This change batches operation observations at executor dispatch, ranks leads against assigned work,
and caches complete-document selection judgments within the task. It preserves advisory authorship,
whole-document delivery, explicit lookup, frozen acceptance and independent usefulness feedback.
Automatic delivery remains opt-in.

## Implementation and assembled inputs

Operation binding enables deferred matching. Initial seeding and tool callbacks collect observations;
the SDK_FIRST before-model hook flushes them before ordinary fitting, and automatic delivery remains
SDK_LAST. One model decision sees the assigned objective, subjects, restrictions and bounded source
observations together. Embeddings retrieve candidates rather than determine final lead order.
Duplicate content retains source aliases, assigned evidence takes priority, and unchanged packets
skip embedding retrieval and matching. Direct synchronous observation callers retain compatibility.

The cache identity includes the bounded observations and content digests, assigned objective,
subjects, restrictions, phase and trigger fingerprints. Cache reuse refreshes provenance aliases
without another model call. No task count, phase dependency or acceptance rule is inferred from
generated text. Matching failure leaves optional guidance unavailable.

Selection caches use operation/task scope, request and requirement inputs, document content and
applicable policy. Validated judgments persist with operation debug data for continuation.
Corrupt caches are optional misses; failed semantic reviews remain unresolved rather than authorizing
research as a catalog miss. Authoritative lifecycle and phase checks still govern actual delivery.
Required capabilities and original generic request are distinguished from authoring enrichment;
explicitly requested payloads and controls remain required.

The current hypothesis publication is rendered as one comparison view per hypothesis, with mechanism,
prerequisites, steps, positive and negative/control outcomes, evidence requirements and identities
adjacent. Its publication hash and hypothesis IDs remain authoritative. Views add no evidence or
semantic decisions. The existing test_discrimination check reasons about overlap and ordinary behavior,
with the existing revision path. No new critic or deterministic vulnerability inference was introduced.

Reviewed the shared specialist system, native/text matcher schemas, selection input and rejected-output
repair, executor initial/continuation observation hooks, automatic delivery after fitting, publication
rendering, evaluator schema/retry and hypothesis correction/continuation. Existing assembled-prompt
tests exercise web, web_recon, CTF, code-security and base modules. Required publication records remain
one non-optional section; optional recommendations/guidance can be omitted as complete sections.

## Focused evidence

A matched scripted replay of three identical source observations uses three legacy matcher calls
versus one batched call while retaining the same lead. Unchanged replay skips both the model and
embedding retrieval. Additional tests cover HTTP/network/filesystem objectives, lifecycle changes,
document changes, failed providers, unknown output IDs, bounded observations, alias provenance,
continuation, empty recommendations, and explicit-miss authorship followed by reuse.

A live production-matcher probe uses a cloned web catalog and synthetic observations. For a homepage
DOM injection task, a mixed bundle containing unrelated authentication code yields HTML/DOM injection
leads. Network-listener and local-file-permission tasks yield no web-only leads. Each fixture uses one
matcher request; these results establish relevance for these inputs, not universal applicability.

A matched live semantic probe uses the old flat publication/rubric and the new comparison
publication/rubric with the same bound output schema. The baseline requests revision for all four
records. The comparison input requests revision for both overlapping/ordinary-outcome cases and
judges test discrimination adequate for both conditional outcome/control cases. This is 2/4 versus
4/4 on the prespecified discrimination labels, not a measure of whole-task correctness or evidence
grounding. The probe uses the specialist structured runner with the actual semantic rubric,
publication rendering and bound schema, rather than the complete workflow evaluator transport.
One call per arm is insufficient to establish a causal or general improvement.

The diagnostic's first positional label report was corrected to join labels to immutable hypothesis
IDs after publication reordered records. Model inputs and outputs were unchanged.

Raw synthetic stores, prompts, provider requests, results and invocation scripts are isolated under
outputs/technique_review_trials/batched-adoption-20261117. Curated details are in
[probe-results.json](probe-results.json).

## End-to-end check: failed effectiveness

The isolated full-controller legitimate-sharing case used the production hook, a cloned reviewed
catalog, real models/embeddings and synthetic endpoint tools. It completed in 45 generation requests
but the task ended partial_failure with an incomplete classification acceptance manifest. There is
no accepted result. The produced classification was violation, whereas the fixture policy and returned
objects support no_violation_observed: A may read A and B; B may read B and is denied A. The executor
treated error labels accompanying permitted data as an authorization violation.

The intended reviewed header-selector guide was selected and supplied. Feedback recorded neutral
contribution (0). Its rationale also treats error-labeled own-account responses as lacking a legitimate
baseline, despite the fixture's policy and returned own-account objects. This rationale does not
independently establish that the methodology's prerequisites were absent.

The earlier comparable pilot completed correctly in 36 requests with positive feedback. Matching
calls fell from eight to seven, but total requests increased from 36 to 45. The new run made 18
tool_catalog calls versus five previously. This single comparison cannot attribute those differences
to batching or estimate general cost savings. Better end-to-end effectiveness and increased useful
adoption remain unproven. A useful next experiment would organize observed policy, identities and
returned effects into a comparison view before execution classification and usefulness assessment,
rather than add another critic or stricter error-label instructions.

Core batching/delivery paths were exercised live. Final alias-retention and request-origin safeguards
were added after pilot startup and verified offline. An earlier SQLite integrity check and scoped
receipt query confirmed one exact automatic-delivery snapshot. A final read-only score lookup could
not open the database; the neutral score is corroborated by the technique_evaluated event.
See [live-result.json](live-result.json). All 50 generation requests across the pilot and probes are
complete. No deployment or changes to the running target operation were made.

## Final code validation

The repository coverage entry point passed **8,211 tests**, with 46 skips and 89 deselections.
Every measured Python file meets the 80% statement and branch floors. New matching code has 100%
statement / 94.1% branch coverage; selection caching has 100% / 100%. Ruff and git diff --check pass.
Source hashes and per-file coverage are recorded in [validation.json](validation.json).

Tests cover real SDK ordering (match → fit → supply → provider), persistent/in-memory selection
caches, explicit-miss advisory authorship followed by reuse, positive/negative matching, lifecycle
changes, alias retention, observation priority, provider failures, and whole-packet fitting.
Assembled prompt checks cover initial, revision, bounded repair, execution and evaluator paths
across web, web_recon, CTF, code-security and base modules. The initial full run exposed six assertions
tied to intentionally changed rubric wording and cache-directory creation; they were updated, and
the final full run used frozen source.
