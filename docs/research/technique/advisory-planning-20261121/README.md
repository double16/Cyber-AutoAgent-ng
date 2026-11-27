# Advisory planning and technique adoption investigation

## Change and boundaries

Production planning now makes one independent advisory assessment. No recommendations accept the
draft. Recommendations receive one planner revision, followed by Python structure and provenance
validation, without a second semantic assessment. A malformed initial draft may receive one structural
repair before advisory assessment. Unresolved controller errors remain explicit.

Public technique lookup and hypothesis publication schemas are unchanged. The reviewer schema no
longer advertises an approval vote. Legacy scripted approval fields are normalized at the boundary.
The local-edit correction contract remains intact. Existing generation version 1 remains appropriate:
this change does not modify technique authorship instructions or the authored document format.

The active operation was not restarted, and no deployment or production catalog mutation was performed.

## Evidence investigation

The selected continuation began at log line 615794, November 20 at 21:03:22.
Complete Langfuse trace exports were retrieved for:

| Task | Trace |
| --- | --- |
| Email parent route | 22327014fca81cefedb9b727227530c5 |
| DOM CSRF | ea452d9784a137823b837b02cafc243f |
| OIDC client | 3081fe492f9979768fc68730e5e9a6b1 |
| Chat query | 59285a4a3aaa92f5f4bbd0086567b337 |

The email parent trace is not the later sendSupportEmail task. That later task was investigated from
its produced documents, published hypotheses and operation log; no exact trace export was available
in the discovery response. Do not transfer the parent's inputs to its child.

The four exports contain 99, 316, 155 and 254 observations respectively (verify the exported inventory
for exact counts). A derived inventory is retained in the ignored experiment directory. It records
executor observation IDs and input sizes without copying sensitive prompt values into this report.

Sampled executor calls contain automatic guidance and baseline/security interpretation instructions.
Their presence does not prove complete document delivery in every call, semantic applicability, or
model attention. No general prompt-fitting loss was established, so delivery was not changed.

The module CSRF guidance already covers browser feasibility, preflight, credential attachment and
protected actions. SSRF guidance already distinguishes intended egress from destination-policy
violations. OIDC guidance already describes policy-aware flow comparisons. Adding these instructions
again would duplicate existing guidance.

Fresh generated guidance is less consistent: the mail SSRF document treats callbacks, errors or timing
as evidence without consistently establishing a prohibited destination; CSRF fingerprint guidance
emphasizes stability and replay without consistently requiring a protected action. Publications can
weaken comparisons further. The final sendSupportEmail publication treats recipient control,
reflection and ordinary outbound fetching as positive indicators without adequate distinguishing
controls. These are semantic quality limitations, not proof of failed receipt or delivery bookkeeping.

Selection also remains imperfect. CVE guidance selected for chat without a CVE and authentication
guidance delivered to email did not demonstrate contribution in the continuation feedback. Feedback
is supporting evidence, not independent proof of applicability or correctness.

The optional SQLite check returned exactly ok for integrity, but read-only schema discovery
failed with unable to open database file (14). Database inspection stopped at that point, without
retrying another connection mode or querying rows. Catalog findings use exported document metadata,
current source files, code inspection and container reads; no total count of obsolete global rows
is claimed.

## Confirmed module catalog defect and fix

The evaluated CVE document's metadata records active=true and origin=module:web, with lineage
module:web:techniques/hypothesis-generation--cve-applicability-hypothesis-generation-for-llm-chat-query-endpoints.md.
Read-only inspection of the running cyber-autoagent container found 64 current module Markdown files
under /app/src/modules/operation_plugins/web/techniques and no document with that title. An old copy
exists only in an archived experiment checkout under outputs. The current repository also lacks that
module source. Its source hash exactly matches the archived copy:
d4bfda7741d5def7406732ab0fab653074b5c4f7a849ab9c50814aa8cdc90e1a.

Synchronization previously published current files but did not retire removed sources. Consequently
a document could be absent from the authoritative module catalog while its prior revision remained
active and selectable. Generated-version filtering deliberately did not exclude module-origin rows.

Successful module synchronization now reconciles the source-selected revision set for each loaded
module. Removed current revisions become inactive/noncurrent. Withdrawal preserves an existing negative-feedback
cause; other withdrawals use module_removed. Historical
source revisions remain stored but are excluded from discovery; exact past snapshots remain available
to historical review. Reintroduction and source reversion select the appropriate revision without
discarding scores, and feedback-based or source-declared inactivity survives restoration. Late feedback
cannot overwrite a withdrawn revision's lifecycle reason, and late positive feedback cannot erase a
known negative-feedback deactivation on restoration. Failed scans and unavailable module roots do
not trigger retirement for that module. Other module origins and generated revisions are untouched.

Embedding-index identity changes from phase-index-v4 to phase-index-v5 so eligibility filters apply
before retrieval limits even for existing vector payloads. This may rebuild embeddings once and does
not invalidate generated version 1. Terminal synchronization output includes a retired count.

## Isolated paired experiment

The harness runs actual baseline and advisory bundle planning with native structured output through
TechniqueRoleRunner. It uses isolated in-memory vector storage and fixture SQLite files. Alignment is
held constant, and lookup is replaced at its boundary: no research, target requests, authorship,
hypothesis publication or downstream execution occurs.

Five requests cover email, CSRF, OIDC, chat and filesystem containment. Each receives three repetitions
under each arm. Baseline code is pinned to d0cc1a7b84edc4d783519fcb25a8589391b59b0f.
Raw inputs, schemas and outputs remain sensitive internal artifacts under
outputs/technique_review_trials/advisory-20261121. Requests are sequential and bounded at 210.

This experiment measures planning validity, component coverage, overlap and model requests. It does
not establish improved authorship, meaningful downstream discrimination, useful adoption or a causal
end-to-end uplift. Those require subsequent fresh authoring and execution observations.

## Rollout

Deploy separately after validation. Review the first ten completed tasks, then follow their hypotheses
into execution. Assess authorship, selection, useful adaptation and executed discrimination separately.
Do not infer effectiveness from successful schema validation, delivery receipts, citations or positive
feedback alone.

## Guidance-application checks

A second isolated harness uses each completed plan's method requests, the same synthetic observation
and the same guidance for both arms of a case. It uses the production role runner, shared security
interpretation instructions and SubjectHypotheses schema, without target tools. Component IDs,
requirement provenance and lifecycle history are omitted from this focused reasoning input. References
to artifact:source.txt label the directly supplied synthetic observation; no production inspection
receipt, publication binding or acceptance result is claimed.

CSRF, OIDC, chat and filesystem use current module guidance; email uses a frozen generated guidance
body saved as a research fixture. Its body SHA256 is
12a4990083cc0ce2483c0e7dfc5c4f331fa285e03869b51fabf338dacd47a80b.
This is a focused application study, not full workflow execution, fresh authorship or a live operation
comparison. It does not measure the catalog-retirement fix, which is verified through storage tests.

Offline semantic assessment considers legitimate baseline, controlled variation, distinguishing
security consequence, attribution, explicit unknown prerequisites and future identity requirements.
It does not require all sections of a multi-procedure guide, a minimum hypothesis count, confirmed
unsafe server handling or already available future credentials. Generic successful execution,
reflection, callbacks or user-directed functionality alone do not establish a security consequence.
An empty result is assessed by its explanation rather than automatically counted as failure.

## Repository validation

The authoritative coverage run completed with 8,346 passed, 46 skipped and 89 deselected.
Every selected source file passes the repository's 80% line and branch floor. Changed-file results:

| File | Statement coverage | Branch coverage |
| --- | ---: | ---: |
| bundles.py | 100% | 99.02% |
| repository.py | 98.99% | 95.45% |
| service.py | 97.11% | 90.74% |
| storage.py | 100% | 96.88% |
| versioning.py | 100% | 100% |
| authentication_requirements.py | 100% | 100% |
| tools/hypotheses.py | 100% | 99.19% |

The shared terminal formatter has 36 passing tests, 100% statement coverage and 98.71% branch coverage.
Its scoped coverage command avoids unrelated global coverage failures from running only one UI test
file. Ruff and whitespace checks pass. Exact validation metadata and source hashes are in validation.json.

## Identity-schema clarification

The original application trials use their frozen, pre-clarification schemas. All ten CSRF hypotheses
in six original outputs left roles empty despite authenticated future-test prerequisites. One OIDC
advisory-arm attempt failed native parsing/validation because an identity comparison omitted roles.
These are retained outcomes; the original study is not rerun until it passes.

The shared authentication schema now explains that comparison=none does not mean unauthenticated,
and that empty roles mean no explicit role restriction at the general selection boundary. The
hypothesis field additionally states the existing future-test identity contract, including single-
session and cross-identity role requirements. Defaults, enum values, wire fields and runtime validators
are unchanged; no semantic critic or refusal stage was added.

A separate probe repeats the same frozen CSRF input three times per schema arm. The legacy schema
is asserted equal to the recorded pre-change schema. Only the three identity descriptions differ.
Actual native transport schemas are captured for verification. One further smoke check retries the
failed OIDC input under the described schema; this single retry is not a general success-rate estimate.


## Completed results and interpretation

| Measure | Baseline | Advisory |
| --- | ---: | ---: |
| Planning trials | 15 | 15 |
| Accepted plans | 14 | 15 |
| Actual planning model requests | 58 | 46 |
| Planner role invocations | 28 | 30 |
| Reviewer role invocations | 28 | 15 |
| Application attempts | 14 | 15 |
| Valid application outputs | 14 | 14 |
| Application validation failures | 0 | 1 |
| Applications blocked by planning | 1 | 0 |

Planning requests fell by 20.7% in this small study. Acceptance measures pipeline availability,
not semantic correctness: the advisory policy intentionally accepts a structurally valid revision
without a second semantic vote. The original baseline filesystem repetition 1 was refused after its
second review. It was not executed downstream. The OIDC advisory repetition 0 failed cross-field
identity validation; that failure is retained. There are 13 pairs with successful application outputs.

Offline semantic assessment is mixed for email, OIDC and chat. Header-injection comparisons generally
require an observable header effect, but relay-policy distinctions remain weak. OIDC public/protected
classification is incomplete in some outputs; ordinary metadata fetching is sometimes treated as
SSRF. Chat proposals sometimes establish prompt use, fetching or object binding without identifying
a prohibited effect. CSRF outputs generally include browser feasibility and protected-state read-back,
but all ten hypotheses in the original six outputs omit roles. All five executed filesystem outputs
provide meaningful allowed-versus-outside fixture comparisons while preserving unknown prerequisites.

The separate identity-description probe produced valid native outputs in 3/3 described-schema trials
versus 1/3 legacy-schema trials. Its single OIDC smoke retry also validated. All seven actual requests
carried native JSON schemas, and transport capture verifies the description difference. However,
described-schema outputs still omitted roles: this does not establish correct identity authoring.
The probe is small, uses focused input without a declared role catalog, and does not reproduce full
production context. Its result must not be pooled with the original application study.

Total model inference requests: 140 (104 planning, 29 application, 7 identity checks).
No failed result was discarded or resampled until success. The OIDC retry is separately labeled.
Raw internal records and hashes are indexed in results.json; qualitative notes are in
semantic-assessment.json. Retrieved trace exports were redacted after analysis; their inventory
input-size counts describe the original inspected inputs.

Implementation and isolated validation are complete. The concrete gains are a bounded advisory
planning path, retirement of obsolete module sources, and clearer existing identity contracts.
Better end-to-end authorship, meaningful discrimination and increased useful adoption remain
unproven. These results do not justify additional critics or stronger refusal prompts.

Deployment and a controlled continuation remain separate. Evaluate fresh tasks or explicitly
replanned affected work; do not count inherited accepted publications as newly improved behavior.
Technique generation compatibility does not retroactively invalidate hypothesis publications.

### Startup follow-up

A later continuation exposed a lineage-owner collision in module restoration. Unchanged module
sources now preserve an eligible generated successor; fallback restoration replaces an incompatible
owner atomically. See startup-regression.md for the failure, regression coverage and fix. The earlier
experiment results above remain historical measurements and do not claim to cover this later fix.
