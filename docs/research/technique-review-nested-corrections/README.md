# Nested correction mappings and semantic review scope

This is a separate follow-up to the failed incremental-correction live trial. That trial used
16 requests and remains failed; cumulative live requests are 196. This revision makes no
new live requests. It does not establish improved live model reasoning.

## Controller change

Canonical finding responses put complete blocking issues inside their own issues field.
Python derives issue_ids from containment, combines the nested issues with other root blockers,
and passes the complete merged critique and responses to the independent assessor. Existing
explicit issue links are supported. No relation is inferred from prose, shared words or labels.
Conflicting issue definitions, duplicate root IDs, unknown references and issues nested in
rejected findings fail validation. The submitted patch is preserved separately from normalization.
Accepted findings still need an issue or an actual interpretation change; all findings and
disputed rows still need explicit dispositions. The correction allowance remains one.

The captured flat response with missing mappings still fails unchanged. An offline structural
counterfactual explicitly nests each previously emitted issue under its finding response and
verifies the merge. This tests the new relation contract only, not semantic correctness.
The historical trial is not retroactively repaired.

## Reasoning guidance

Shared source-support guidance requires meaning-based assessment across retained passages.
Faithful paraphrases and direct consequences of supported premises need no identical causal
wording. A material support blocker must identify a missing premise, contradiction or
unjustified generalization; nearby words and source authority remain insufficient by themselves.

Shared security interpretation guidance fixes prerequisites and the suspicious observation
when considering permitted counterexamples. If no permitted scenario satisfies both, the
reviewer explains why and sets matching false; false exclusion then does not independently
prove a defect. Prerequisite establishment remains separately reviewable. Merely declaring an
expected result is insufficient. An actual check must stay represented when its interpretation
is revised; disagreement does not remove it from the unchanged draft.

These are semantic tasks for the model. Python checks consistency and reference structure,
not vulnerability meaning or source entailment. No target, tool or protocol keyword scoring
was added.

## Provenance and verification

provenance.json records exact old/new source hashes and runtime identities. The reconstructed
preimage runtime identity matches the failed live trial's frozen identity. revision.patch and
source-preimages/source-snapshots preserve both sides without changing earlier records.

Focused merge/SDK tests cover nested mapping, explicit legacy links, shared issues, conflicting
definitions, unknown IDs, immutable submitted payloads, preserved rows and blocked persistent
disagreement. Both SDK output modes exercise the complete correction/re-review path. HTTP,
network and filesystem controller tests cover matching versus impossible counterexamples and
assert the complete subject plus shared rules survive initial, independent and correction inputs.
These scripted responses do not judge source entailment or demonstrate semantic improvement.

Complete assembled inputs were reviewed across initial critic, independent investigation,
critic comparison, indexed correction, assessor repair, native/tool schema handoff and prompt
fitting. Shared guidance reaches authoring and hypothesis review through the existing common
source; source assessment and critique use the same support scope. No conflicting full-table
instruction is appended to the indexed correction. Legacy and experimental correctness contracts
remain unchanged. Public tools, SQL, persisted scores and historical records are unchanged.

Verification results are recorded in verification.json when complete. Six exact-input preflight
checks use the same frozen network, filesystem and HTTP cases, without provider dispatch.
The preflight protocol has prepared status; live dispatch requires a separate authorization.

## Next acceptance

After offline checks pass, freeze and authorize a fresh bounded sequential trial. Accept useful
network guidance, detect filesystem file-read clarity, and reject unsupported HTTP entitlement
claims with correct reasons. A successful mapping alone is not semantic acceptance. Preserve
disagreements and exhausted corrections as failures; do not prescribe a favorable outcome.

## Final offline result

The repository coverage entry point completed successfully: 7,807 passed, 46 skipped,
86 deselected, 29 warnings in 656.54 seconds. The final SDK diagnostic assertions passed
separately in all four native/tool and issue/revision combinations. Ruff and diff checks passed.

| Production file | Line coverage | Branch coverage |
| --- | ---: | ---: |
| critique.py | 99.0% | 96.5% |
| review_corrections.py | 98.9% | 97.6% |
| security_interpretation.py | 100.0% | 100.0% |
| support_scope.py | 100.0% | 100.0% |

The repository per-file coverage checker passed for all four changed production files.
All six exact-input scripted SDK preflight checks passed with zero provider requests.
See verification.json for counts and coverage summaries. Live semantic acceptance remains
unverified. No model calls are running; cumulative prior live requests remain 196.
