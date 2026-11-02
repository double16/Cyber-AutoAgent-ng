# Critique verdict evaluation — November 2026

The staged live comparison did not establish a benefit from replacing the terminal boolean
critique verdict with a 1–5 rating. Keep production behavior unchanged. Neither arm met the
strict acceptance criteria: both had unresolved decisions, and independent review sometimes
supported findings contradicted by its own evidence.

## Protocol

Eight short cases were screened across four arms: boolean/current, rated/current,
boolean/focused and rated/focused. Focused review inventories procedures and reviews them
separately before full-document review. Ratings replace the terminal verdict only; logical
evidence predicates remain boolean. Python requires a rating of at least 4 and no blockers,
and validates references and decision consistency. The model judges meaning.

Human screening adjudication marked seven cases acceptable and unconditional token reuse as
needing correction. Three acceptable labels differed from the provisional negatives for
unconditional header selection, storage reading and other-owner file reading. Preserve this
disagreement: these labels are evaluation inputs, not a revision of the technical guidance
against treating ordinary behavior alone as a violation. No raw outcomes were rewritten to
match the labels.

| Screening arm | False approvals | False rejections | Unresolved |
|---|---:|---:|---:|
| Boolean/current | 0 | 7 | 0 |
| Rated/current | 0 | 4 | 2 |
| Boolean/focused | 0 | 7 | 0 |
| Rated/focused | 0 | 7 | 0 |

Rated/current was the unique strongest challenger under the first two predeclared ranking
dimensions. Remaining reasoning-error counts were not human-adjudicated or imputed as zero;
they could not change that unique selection. Selection authorized further testing, not adoption.

Four frozen full documents were then reviewed by baseline and challenger, three times each,
in seeded order with fresh role conversations. The documents were original and corrected
authentication guidance, and original and partially corrected header guidance. Existing
research, author and shared critique-correction limits were preserved. No technique repository
or active-operation database was opened; no historical score was changed.

## Held-out results

| Arm | Approved | Rejected | Unresolved | Completed |
|---|---:|---:|---:|---:|
| Boolean/current | 3 | 5 | 4 | 8/12 |
| Rated/current | 2 | 3 | 7 | 5/12 |

| Frozen document | Boolean/current | Rated/current |
|---|---|---|
| Authentication original | 2 rejected, 1 unresolved | 3 unresolved |
| Authentication corrected | 3 approved | 2 approved, 1 unresolved |
| Header original | 3 rejected | 1 rejected, 2 unresolved |
| Header partially corrected | 3 unresolved | 2 rejected, 1 unresolved |

These are controller-validated outcomes, not human-certified accuracy rates. A completed
rejection can still contain an unsupported finding. The exact corrected authentication draft
had SHA-256 `b02a05018dd2c08f457a41173d79c7250b5bb97a2633ca6c3f43a2e4b67aa312`;
approval of that version does not approve a different revision.

## Observed reasoning and repair failures

The following are agent-proposed semantic findings with retained model evidence. They do not
replace human adjudication of individual findings.

- **Dropping a valid finding during correction.** In held-out trial 004, the rated critic
  identified the storage-boundary defect. After the assessor identified omitted enforcement
  and replay defects, the correction added those but dropped the storage defect. The final
  assessor disagreed and the shared allowance was exhausted.
- **Reviewing one dimension and missing another.** In trial 005, the first independent review
  challenged source objections because assumptions and future checks were already explicit.
  The critic then approved. Only the next review raised omitted boundary defects, exhausting
  the same allowance. Correcting one objection did not establish completeness.
- **Accepting a mismatched counterexample.** In trial 006, final issue `auth-content-2` said
  returning data despite missing entitlements could be legitimate for a public/shared object.
  The final assessor's interpretation 4 explicitly acknowledged that public/shared access did
  not satisfy the missing-entitlement condition, yet supported the critique retaining that
  issue. Relevant draft references are `d3cce3199eed5:b79`, `:b80` and `:b81`.
- **Confusing interpretation with quotation and role ownership.** In trial 021, the rated
  assessor initially supported the corrected authentication draft. Its contract correction
  then objected that critic interpretations were paraphrases and that controls did not exclude
  nonmatching counterexamples. Verbatim copying belongs to the assessor's actual-discriminator
  evidence field; a critic interpretation may summarize the actual control. The decision ended
  unresolved despite the draft's explicit gates.
- **Unsupported source objections.** Trials 001 and 003 exhausted correction on source
  grounding: missing passages/absence explanation and a source-wide absence claim without
  complete inspection. A missing prewritten example is not itself missing technical support.

The screen also exposed demands for observed target results in reusable future procedures,
helper fields mistaken for authored content, and supplied controls described as absent.
Numerical verdicts did not consistently prevent those errors.

## Capacity and execution limits

The model was `muse-glimmer:30b`, 27.9B parameters, Q4_K_M, digest
`de878ce33ad81d060001db1469a02eebe4d86f0ad58cfe52dc062fdcbe4464c1`.
The final digest matched the initial capture. Calls were sequential. Temperature was 0;
the scheduling seed was 20261101, with no fixed inference seed. Fresh repetitions are not
independent random samples. The configured input limit was 40,000 tokens.

The screen recorded 264 model inputs ranging from 1,433 to 9,106 controller-counted tokens.
The held-out stage recorded 175 inputs ranging from 9,230 to 28,787 tokens. No observed input
overflow explains these failures. This does not rule out effects from context organization,
attention, model size or quantization; those were not isolated experimentally.

Two held-out trials ended with provider stop reason `max_tokens` and
`MaxTokensReachedException`. The first contained reasoning without a final structured
critique. Output-generation exhaustion is distinct from input-context overflow. The other
nine unresolved outcomes were recorded as `ValueError` from validation or review disagreement.

The first output failure exposed a harness bug that aborted the schedule without a terminal
status. Its original outcome and traceback were preserved, then its status was recovered as
unresolved. A bookkeeping fix records known output exhaustion and continues unattempted jobs;
unexpected errors are recorded and re-raised. The failed trial was not retried. No model-facing
prompt, schema, corpus, generation budget or correction allowance changed during continuation.
Earlier setup runs with schema or requirement-kind errors were excluded from this comparison.

## Interpretation and remaining work

Do not adopt the graded verdict from this study. There are too few cases for broad accuracy
claims, repetitions are not independent samples, and the rated arm changes the anchored rubric
and schema as well as the terminal value type. The focused screen changes review organization
and helper context together; it is not a causal test of context length.

Further experiments should separately examine preservation of valid findings through repair,
complete review across source and interpretation dimensions, counterexamples that satisfy all
conditions, and generation capacity for a complete final response. Any prompt or budget change
requires a new frozen cohort; do not silently rerun failed decisions until they pass.

## Final bounded header repair

The final permitted header author call (three of three) ran separately after the comparison.
It produced a format-valid draft with SHA-256
`e2bb28fa3047db47d493c28a9b97f16b53980b10ac9b924ce408444e3b1d4cca`.
Source metadata and coverage were preserved; no research batch or author-budget reset occurred.

The critic and independent assessor initially rated it 4. The assessor marked permitted
outcomes of prerequisite checks V0/V0a as matching counterexamples without excluding controls,
while supporting approval. The controller rejected those inconsistent fields. These checks
explicitly label suspected violation as not applicable: their purpose is to establish policy
and the header contract. The model had failed to keep prerequisite establishment separate from
a claimed boundary violation. The contract correction then rated support 2 and added a source
overclaim objection. Disagreement exhausted the single shared allowance; the result is unresolved.

This also illustrates an unresolved contract-design question: the internal review relies on
prose to distinguish prerequisite checks from violation claims. A future experiment could make
that distinction explicit in structured metadata, without inferring it from keywords or making
Python judge vulnerability semantics.

No final header version was accepted or published, and no author attempts remain. Its model
digest was verified again at completion. Evidence is retained in isolated run
`critique-canonical-header-bbcn078g`. This separate failed check does not replace any of the
24 held-out outcomes or change the failed acceptance disposition.

## Evidence and verification

Screening artifacts are retained in isolated run `critique-comparison-teskozcg`; held-out
artifacts are in `critique-comparison-yjwj46cl`. Each retains frozen inputs, model and source
manifests, exact internal model evidence, diagnostics and outcomes. Raw evidence is sensitive
and is not reproduced here. The human adjudication and selection rationale are retained
separately from provisional expectations and raw model outputs.

Repository verification after the harness fix: **7,540 passed, 46 skipped, 86 deselected**.
All selected Python files passed the 80% line and branch coverage floor. The changed CLI had
98.84% line and 93.75% branch coverage; Ruff and diff checks passed. An earlier restricted run
failed on fixture socket, loopback and cache permissions; the same repository entrypoint
passed with those local test permissions available.
