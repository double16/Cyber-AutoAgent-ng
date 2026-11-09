# Single-reviewer publication

Production now uses one critic. It receives the complete decoded draft, request, frozen criteria
and retained evidence, and returns summary, material issues and optional suggestions. Python
derives approval from an empty issue list. Model-authored interpretation tables, approval votes,
counterexample booleans, agreement flags, issue IDs and correction mappings are absent.

Python assigns issue IDs, derives requirement IDs from criteria, resolves source URLs and excerpts
from inspected passage IDs, and rejects unknown references. Missing deliverables may reference
their requested criterion without citing nonexistent content. Source support/attribution complaints
require inspected source passages; semantic correspondence remains a model judgment.

One output retry preserves the original subject, rejected response and source inspection.
Exhaustion reports ReviewUnavailable and a publication_review/unavailable event, blocks publication,
and does not request an author rewrite. Valid issues retain existing research, attribution and
author repair ownership and limits. Each revised draft receives a fresh review and publication
rechecks the exact reviewed bytes. Public tool arguments, SQL, persisted scores and feedback review
are unchanged.

The earlier assessor workflow remains accessible through the explicit legacy critique.assess
entry point for isolated experiments. Production service imports publication_review.assess.
The calibration worker selects the new path with review_mode=single; missing mode preserves
historical experiment compatibility. There is no production switch back to two reviewers.

## Verification

The repository coverage entry point passed 7,828 tests (46 skipped, 86 deselected). The final
technique suite, including added single-mode worker tests, passed 1,190 tests with one skip.
All five changed production files pass the 80% line and branch floor; publication_review.py
has 100% line and 93.8% branch coverage. Ruff passed. See verification.json for exact summaries.

Tests cover material rejection, suggestions-only approval, missing deliverables, source provenance,
invalid criteria/draft/passage references, exhausted retry without author rewrite or publication,
strict rejection of legacy output in production, and complete-subject SDK retries in native and
schema-tool modes. Existing CLI, operation, author/research repair and feedback tests pass.
Old semantic text fixtures are explicitly projected to the new schema in test code only; dedicated
strict schema and SDK tests bypass this fixture migration. Production does not normalize old
approval/interpretation payloads into the new contract.

The complete assembled critic input, SDK system schema, retry, author repair and prompt-limit
guard were reviewed. No second-review instructions or full interpretation table are required by
the new production path. Existing shared authoring and hypothesis-review contracts remain intact.
Historical two-review instructions in the documentation/playbook are labeled as legacy/offline.

## Five frozen cases

cases.json preserves the three most recent adjudicated cases plus previously accepted filesystem
and corrected HTTP guidance. Labels remain frozen. The accepted filesystem delivery export
contains database lifecycle fields; fixture-provenance.json records the original captured
pre-publication subject, verifies its hash against the export's source_sha256, and verifies the
authored body is byte-identical. The human judgment is inherited from that same delivered content,
not a newly rewritten document.

protocol.json freezes the runtime and case digests. source-hashes.json and source-snapshots preserve
the exact current implementation and tests. preflight.py performs ten scripted native/tool checks
and cannot dispatch live inference. Results are recorded in preflight-results.json on completion.
These checks do not establish semantic correctness.

## Next live acceptance

The proposed trial uses muse-glimmer:30b, five cases sequentially, and a shared maximum of 21
provider requests. All five decisions must agree with the human labels and supply actionable
reasoning. Wrong or incomplete results block rollout. Do not adjust labels or silently extend
an exhausted allowance. A separate user authorization is required; protocol status remains prepared.

No new live requests were made. Cumulative prior live requests remain 212. Earlier failed trials
are preserved in their existing directories and have not been relabeled as successful.
