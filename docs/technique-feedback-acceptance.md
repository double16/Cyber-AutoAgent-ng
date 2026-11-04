# Technique evidence and independent feedback acceptance

The implementation removes exact model-quotation matching from requirement-source assessment.
Python retains actual supplied and inspected passages for cited selected URLs. Models still judge
technical support. Unknown citations and citations without supplied context remain invalid.

New feedback reviewers return independent contribution findings and referenced material errors in
evaluator fields. Python accepts matching derived scores only when no material errors remain.
Legacy boolean review types describe historical data; new review calls use the independent schema.
Contract corrections and semantic disagreement retain the same single shared allowance. No disputed
score is persisted. Continuation, public tools, SQL schemas and historical scores are unchanged.

## Verification

The repository coverage entry point passed: **7,591 tests passed, 46 skipped, 86 deselected**.
The repository coverage-floor check passed for all measured files. Changed-file coverage:

| File | Line | Branch |
| --- | ---: | ---: |
| `coverage.py` | 99.6% | 97.5% |
| `feedback.py` | 99.2% | 93.2% |
| `critique.py` | 99.7% | 96.7% |

Ruff and diff whitespace checks passed. One additional assembled-schema regression passed separately.

Unit and integration verification includes source paraphrases, fabricated legacy quotations,
missing source context, invalid citations, retained lookup context, contradictions, all three
feedback scores, score disagreement, material errors, invalid evidence references and correction
exhaustion. HTTP, network and filesystem examples exercise the same generic invariants.

Reviewed assembled source-assessment and feedback inputs, initial and correction paths, retained
read evidence, schema exposure through the shared role system prompt, native/schema-tool handling
and fail-closed prompt fitting. No additional model roles or investigation retries were introduced.

## Live acceptance

Live acceptance uses the configured `muse-glimmer:30b`, isolated temporary storage, sequential model
calls and a shared maximum of 32 role invocations. Synthetic contribution controls are distinguished
from captured production replays. Expected synthetic labels are controls, not evidence that a
production receipt deserves the same label. All model results require evidence review.

Useful HTTP, network and filesystem controls, plus the mixed-benefit/harm control, exhausted their
correction allowance because
the evaluator omitted evidence mappings. These are unresolved acceptance failures, even though
Python correctly retained pending feedback. The citation-only control received supported neutral
feedback, as did the method-mismatch control. No prompts were tuned or extra retries granted after observing these failures.

All three captured source reviews completed with supported technical rationales and retained
controller passages: response-based username enumeration, JSON mass assignment and the technical
foundation for an original command-injection test procedure. No model quotation matching was
required. These requirement reviews fix source authority as an input and do not validate a complete
new technique document.

Captured source replays use retained production `read_source` text. Captured feedback uses durable
receipts and task artifacts; original initial-call summaries are unavailable. Legacy delivery
snapshots remain unknown rather than being reconstructed as exact deliveries. The first captured
feedback harness inlined large artifacts and failed prompt fitting before generation. Its corrected
harness exposes the same frozen artifacts through bounded read access; this is a harness correction,
not another semantic decision retry.

## Output exhaustion investigation

The observed November 4 01:15 UTC exhaustion occurred in `technique_critique_assessor` during
critique attempt 2. The last logged allocation was 4,096 generation tokens with approximately
7,021 input tokens and a 48,000-token context window. This is output exhaustion, not demonstrated
input-context overflow. The assessor was investigating independently of the critic.

Investigation instructions now request compact per-check notes and avoid copying whole drafts or
source passages. Generation limits were not increased globally. The complete rejected assessor
response and exact historical assembled input are not retained in the available operation log;
the saved build attempt contains the authored document and preceding critique issues instead.
Consequently an exact exhaustion replay cannot be claimed, and the effectiveness of this prompt
change remains unverified. Do not treat this run as production acceptance.

## Final captured-feedback results

Both bounded captured replays returned supported neutral feedback (`contribution_unestablished`).
The products replay distinguished reflection-specific methodology from authentication and SQL/NoSQL
injection hypotheses. The secrets replay distinguished query-string leakage analysis from
unauthenticated endpoint-access hypotheses. Each reviewer independently returned neutral findings
with no material errors, and Python derived acceptance. The old secrets verdict inversion did not
recur. These are evidence-limited isolated results, not rewritten historical scores.

Overall live acceptance **failed**: four synthetic demonstrated-effect cases exhausted correction
for omitted evidence mappings. Two synthetic neutral controls and three captured source reviews
passed, as did both bounded captured feedback replays. Output-exhaustion mitigation remains
unverified. Do not recommend a production rollout from this acceptance run.

The run used 23 role invocations, including four prompt-fitting failures
without provider generation, and 27 provider input attempts. Calls were sequential.
The shared cap was 32 role invocations; investigation and finalization can use multiple provider calls.

Frozen inputs, outcomes and complete model diagnostics are retained under:

- `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/independent-feedback-acceptance-2ofzt221`
- `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/independent-source-acceptance-11kekrjn`
- `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/captured-feedback-acceptance-yay1gp8g`
- `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/captured-feedback-acceptance-mrhl29e3`
