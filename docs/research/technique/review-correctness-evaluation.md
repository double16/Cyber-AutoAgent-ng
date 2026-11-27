# Purpose-aware correctness evaluation — November 2026

> Historical experiment record: contracts, counts and outcomes below describe the frozen trial,
> not necessarily the current runtime. See [Techniques](../../techniques.md) for current behavior.
> Earlier temporary artifact paths are provenance records; availability is not guaranteed.

The proposed review profile failed live acceptance. Keep it experimental and retain production
behavior. It did not demonstrate a higher yield of useful technique documents.

## Frozen comparison

The proposal keeps boolean verdicts and adds explicit check purpose, correction dispositions,
and independent coverage of issues, requirements and dispositions. It uses the existing critic
and assessor roles without a preflight agent. Configured generation capacity and the single
shared correction allowance were unchanged. Calls ran sequentially against `muse-glimmer:30b`.

The screen contained 16 cases in eight families across HTTP, network and filesystem work,
source synthesis and deliverable completeness. Human adjudication revision 2 marked 12 cases
acceptable and four needing correction. Revision 1 and the explicit revision were preserved;
the two unresolved labels were corrected to needing correction without changing model inputs.
Some acceptable labels still differ from the technical guidance about ordinary-behavior
violation labels; preserve that disagreement rather than treating the labels as universal policy.

The held-out stage used the same four frozen full documents as the earlier verdict study,
with three fresh-conversation repetitions per arm. No author or research budgets were reset,
failed trials were not replaced, and no historical scores or active technique records changed.

## Screening results

| Result against human labels | Baseline | Proposed |
|---|---:|---:|
| Approved | 0 | 0 |
| False rejection | 11 | 5 |
| Correct rejection | 4 | 4 |
| Unresolved decision | 1 | 7 |

The lower false-rejection count is accompanied by more unresolved decisions, not by accepted
guidance. Neither arm accepted any of the 12 human-acceptable cases.

## Held-out results

| Outcome | Baseline | Proposed |
|---|---:|---:|
| Approved | 5 | 1 |
| Rejected | 5 | 6 |
| Unresolved | 2 | 5 |
| Completed | 10/12 | 7/12 |

| Frozen document | Baseline | Proposed |
|---|---|---|
| Authentication corrected | 3 approved | 1 approved, 2 unresolved |
| Authentication original | 2 rejected, 1 unresolved | 3 rejected |
| Header original | 2 rejected, 1 unresolved | 2 rejected, 1 unresolved |
| Header partially corrected | 2 approved, 1 rejected | 1 rejected, 2 unresolved |

Completion and rejection do not establish correctness of every finding. The baseline's two
approvals of the partially corrected header draft missed local labels identifying changed
object selection and header parsing alone as suspected violations. The proposed profile avoided
those approvals, but its rejections also relied on disputed source-support and completeness
objections. Approval avoidance alone does not demonstrate improved critique precision.

## Referenced failure patterns

- **Supplied procedure elements described as absent.** Screening trial 000 rejected the network
  procedure as lacking legitimate outcomes and evidence requirements, although the draft states
  allowed connectivity is legitimate and identifies rules, peers and connection results to record.
  Trial 007 similarly described contract/configuration review and recorded identities as absent.
- **Independent checks confused with critic-row indices.** Screening trials 000, 003 and 011
  exhausted correction on unknown interpretation indices. The assessor added independently
  identified checks as indexed review rows instead of reporting omissions through findings.
- **Purpose disagreement without a consistent disposition.** Screening trials 005, 016 and 023
  exhausted correction on purpose disagreement while attempting to support the critic.
- **Late additional objections after correction.** Held-out trials 000 and 012 initially approved
  the corrected authentication draft, then rejected it on metadata/source/prerequisite objections.
  After correction, the assessor introduced further objections and exhausted the shared allowance.
- **Existing illustrative framing overlooked.** The corrected authentication draft explicitly
  says, “All checks are future validation, not observed results.” Nevertheless, final findings
  in held-out trials 000 and 012 claimed illustrative material was not distinguished from results.
  Held-out trial 014 made a similar claim about header guidance that explicitly says
  “Illustrative only, not observed results.”
- **Source synthesis still treated as unsupported specificity.** Several findings demanded
  source support for complete header-specific GET procedures instead of distinguishing supported
  technical foundations, explicit assumptions and original illustrative methods. Source-wide
  absence claims without complete inspection blocked held-out trials 009 and 023.

These are agent-reviewed findings, not a fully human-adjudicated error count. Their exact
subjects, model outputs and controller diagnostics are retained for inspection.

## Disposition and limits

Both screening and held-out acceptance failed. Do not enable the proposed profile in production
or claim increased useful-document yield. Keep unresolved outcomes and unsupported findings in
the evidence; do not tune the frozen cohort or replace failures with successful reruns.

The screen is small and synthetic, and its human labels contain the noted policy disagreement.
Fresh temperature-zero repetitions are consistency checks, not independent random samples.
This comparison changes several correctness mechanisms together and cannot attribute an effect
to one field or instruction. Accepted-document yield also does not establish downstream task
usefulness; that requires observed application evidence.

Evidence is retained in screen run `critique-comparison-s_9emjkt` and held-out run
`critique-comparison-nke11beg`. Both model-digest checks passed, and the frozen code identities
were checked before the held-out run. The raw evidence remains sensitive internal data.
Implementation verification remains 7,575 repository tests, plus 175 focused tests after final
prompt deduplication; per-file line/branch coverage, Ruff and diff checks passed. No implementation
or generation configuration was changed during the live comparison.
