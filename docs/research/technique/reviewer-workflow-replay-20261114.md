# Technique reviewer workflow replay — 2026-11-14

## Protocol and boundaries

The reviewed continuation is `OP_20261020_110442`, starting 2026-11-13 12:59:07.
The first live replay used the production role runner and `muse-glimmer:30b-mlx` on the
18 recorded methodology requests from redacted logs. Redacted/secret-bearing input is not a
clean positive control. Resolution stopped immediately after planning acceptance;
the downstream technique response was a placeholder. This experiment measures planning and
correction only. It does not establish retrieval accuracy, publication quality, delivery yield,
usefulness, or an end-to-end improvement over the operation.

The historical operation had 18 completed lookup calls, five successful calls and four distinct
newly delivered revisions. Fifteen invocations reached planning and six plans were accepted.
These denominators differ from the planning replay and must not be compared as delivery rates.

## First planning replay

Eleven of 18 requests reached accepted planning. Nine requests used local correction; three of
those recovered. Six requests failed planning after correction, and one failed request alignment.
The model still repeated identical method request strings with different requirement lists.
Local removals reduced large proposals, but some duplicates and a semantic omission remained.
The LLM endpoint request was accepted as three independent methods: prompt injection,
SSRF through tool use, and mass assignment. This resolves the earlier grouping disagreement in
this case, without establishing that all future grouping decisions will be correct.

These results preceded two follow-up controller changes:

- Identical method request strings group their distinct structured requirements and input
  provenance. If the existing requirement bound would be exceeded, separate records remain;
  no obligations are silently discarded. Semantically similar wording is not merged by Python.
- Phase-only framing has no restriction-conflict decision. Explicit user or module restrictions
  retain the existing conflict path and semantic review.

The first replay results do not validate those subsequent changes.

## Follow-up validation

The usefulness replay completed all four frozen delivery cases without changing operation scores:

| Method | Historical score | Replay score |
| --- | --- | --- |
| Login and credential leakage | +1 | +1 |
| LLM prompt injection and tool-use SSRF | +1 | 0 |
| Unauthenticated API disclosure | +1 | +1 |
| Node.js spawn command injection | Pending | +1 |

All replay decisions have consistent neutral/effect fields. The LLM case has
`neutral_basis=contribution_unestablished`; contribution was not established by the available
records. This is not a negative assessment of the guidance. Positive explanations still rely
heavily on hypothesis structure, leaving causal attribution weak. The integration now supplies
the frozen acceptance contract as baseline obligations and records outcome-order provenance;
that addition was not part of this already-running usefulness replay.

The end-to-end replay uses actual retrieval, publication and web research in separate temporary
Qdrant and SQLite stores. Its current module catalog is not an exact reconstruction of the
historical global catalog. Sixteen requests completed before the process stopped; the cause
of termination is unknown. Cases 17 and 18 subsequently completed after resuming the isolated state.

Of all 18 requests, five delivered guidance. Four were complete bundles; one delivered
two methods and explicitly retained unresolved mass-assignment guidance. Nine component
deliveries represent four distinct generated revisions, with later component and request reuse.
These counts do not establish complete request coverage or independent semantic correctness.

| Failure stage among all 18 | Requests |
| --- | --- |
| Publication source-evidence contract | 2 |
| Source-attribution contract | 1 |
| Request alignment/preservation | 2 |
| Research/source coverage | 1 |
| Authored-content review | 2 |
| Truncated decisions after recovery | 2 |
| Plan review after correction | 3 |

The repeated-source-attribution failure returned several contributions for one exact selected
URL. Source assessment now groups those contributions losslessly by URL, retaining distinct
reasons and limitations. Unknown or unselected references still fail, and contributions that
cannot fit existing bounds remain invalid rather than being truncated. The running replay
predates this fix for its completed cases, so it does not validate that fix live.

Publication contract failures remain unresolved pipeline outcomes, not semantic rejection.
No relaxed source-evidence acceptance rule was introduced without the failed reviewer response.
Neither the end-to-end sample nor the changed usefulness score establishes a controlled
reliability improvement. Raw prompts and operation evidence remain private.

The replay did not retain failed generated documents (`service.exports` was disabled).
For case 17, the truncated error excerpt therefore cannot independently establish whether
the critic's claim that stored-XSS content was missing was correct. That outcome remains an
authored-content review failure, without an independent judgment of the failed document.

## Assembled instruction review

Scripted transport coverage includes native and schema-tool finalization, local planner edits,
prior review history, independent investigation before comparison, bounded contract repair,
truncation recovery and prompt fitting. Frozen delivery content is retained through lifecycle
changes. Publication short references normalize to durable snapshot IDs; canonical legacy
references remain available. Contract validity and passing scripted checks are not semantic
approval of generated guidance.

## Verification completed during follow-up

After all current changes, the repository coverage entry point completed with 8,101 passing
tests, 46 skips and 89 deselections. Repository coverage was 91%, and the repository floor
checker passed every selected Python file at 80% line and branch coverage. The focused
technique suite passed 1,422 tests with nine skips and three deselections, excluding local TLS
tests exercised in the repository run. All eight changed production modules passed the
line and branch floors; their focused combined coverage was 98%. Ruff and whitespace checks passed.
These are code verification results, separate from semantic model acceptance.

## Publication packet correction

Inspection of assembled production code exposed a contract mismatch: authoring source excerpts
were repeated in publication inputs without passage IDs, while grounding issues required IDs
from a separate lookup. The authoring evidence also duplicated criteria and included unrelated
authoring examples. Publication now receives a role-specific packet with the draft, criteria
once and controller-identified prefixes of selected retained sources. Each prefix is bounded
to 2,000 characters; unread text remains unknown and can be looked up. Integrity checks cover
those supplied source records. No grounding acceptance check was relaxed, and prefix delivery
does not establish semantic support. This change was made after the completed replay cases;
their outcomes do not validate it.

A focused live check with manually framed fictional-policy documents returned valid reviewer
outputs for both variants, citing the controller-provided passage IDs. Both variants were
rejected. The intended positive fixture carried the default `Module-authored guidance` coverage
label, which became the reviewer's reported attribution defect; it was not a clean positive
control for production framing. These results establish a working evidence-reference path,
not correct semantic discrimination. A matched check using `assemble_document` subsequently completed;
the failed fixture setup before its model call is not counted as a model result.

The publication repair path now appends only newly looked-up passages, without repeating
already-visible source records. Both sets participate in input-integrity checks. Required phase
contexts remain explicit in the publication packet, while authoring examples and the duplicate
criteria are omitted.

## Matched production-framed packet check

With the actual production document assembler, the supported-attribution control was accepted
and the contradictory-attribution control was rejected. The latter cited the supplied source
passage ID and identified the exact strict/permissive discrepancy. Both outputs were valid;
there was no publication-contract failure. These two synthetic cases establish a narrow
working path, not broad publication reliability or validation of the interrupted end-to-end run.

The critic reported the contradiction twice: once under attribution and once under correctness.
The correctness issue referenced only the controller-owned source selection reason. Routing
solely by criterion would unnecessarily send that second issue to the document author.
Publication now derives attribution ownership for correctness issues confined to those frozen
source-attribution fields. Mixed procedure/source references remain authored-content issues.
No defect is deemed valid by this routing; source evidence and subsequent review remain required.

Exact synthetic snapshots and raw reviewer decisions are frozen in
`tests/techniques/fixtures/publication_source_packet_replay.json`. Their snapshot hashes were
matched against the recorded live review subjects before freezing. Scripted replay verifies
reference validity and repair ownership; a workflow test verifies one author call followed by
attribution repair and re-review rather than another author call.

The full 18-case replay is complete. Its 5/18 delivery yield has not established a controlled
end-to-end reliability improvement; four bundles were complete and one was partial. Planning
was accepted in 13 of the 16 requests that reached it. Eleven requests used correction, and
eight subsequently reached accepted planning. These are controller/model acceptance outcomes,
not independent semantic approval. The replay preceded the final publication packet and
planner message-boundary changes, so it does not validate the final implementation end to end.

## Planner instruction/data boundary follow-up

The final failed requests still included planner instructions as methodology components.
The controller now sends the existing planner and plan-review rules as system content and
only invocation data as user content. Initial planning, corrections, and review history retain
this boundary; no new review rules or stages were added. Legacy injected text runners retain
the combined prompt string. Native and schema-tool transport tests verify the separate
messages, complete integrity records, budget accounting and native truncation recovery.

A focused planning-only replay of cases 14, 16 and 18 completed with the production role
runner. All three reached accepted single-component plans. Case 14 passed directly; cases
16 and 18 used local removal corrections from three and seven components respectively.
The accepted requests preserve the original methods and the phase framing. This small
sequential sample is diagnostic, not a controlled effect estimate or evidence of delivered
document quality. It stops before retrieval, generation or publication; it must not replace
the 5/18 end-to-end delivery result.

The separated-message implementation passed the technique suite with 1,422 tests, nine skips
and three deselections. All changed production modules meet the line and branch floors;
combined coverage remains 98%. Complete assembled transport checks include native and
schema-tool modes, correction history, integrity checks, prompt fitting, and native truncation
recovery with controller policy retained in the system message.
