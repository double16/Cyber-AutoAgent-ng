---
schema_version: 1
title: Evidence-driven challenge hypothesis development
purpose: Convert challenge observations into discriminating future experiments.
applicability:
- Convert challenge observations into discriminating future experiments.
phases: &id001
- hypothesis_generation
active: true
triggers:
- phases: *id001
  when: A challenge hint or observed behavior suggests a concrete security-relevant mechanism.
  signals_any: []
  request: Evidence-driven challenge hypothesis development
sources:
- url: https://wstg.owasp.org/v4.2/3-The_OWASP_Testing_Framework/0-The_Web_Security_Testing_Framework/
  title: OWASP Web Security Testing Framework
  retrieved_at: '2026-10-25'
  selection_reason: Primary specification or assessment methodology relevant to this guidance.
source_coverage: Small module-authored starter; primary references do not exhaust implementation-specific
  cases.
---

# Evidence-driven challenge hypothesis development

## Prerequisites
Keep the challenge's declared objective, authorization, and frozen observations available. A hint guides investigation; it does not establish implementation or success.

## Procedure
1. List the concrete observed behavior and distinguish it from assumptions suggested by hints or names.
2. State one suspected mechanism and the inputs or state transitions that could affect it.
3. Define the smallest future experiment that distinguishes that mechanism from an ordinary explanation. Include baseline, changed input, expected positive result, and negative control.
4. Identify prerequisites and evidence locations. Use separate experiments when mechanisms, required identities, or success conditions differ.
5. Explain a no-testable-hypotheses disposition when inspected evidence supports no grounded experiment. Missing optional details do not independently establish that disposition.

## Interpretation and limits
Do not execute future experiments during hypothesis generation. A reflected string, generic error, guessed flag, or tool success is not objective completion. Prefer available artifact inspection and request capabilities over requiring a particular exploit framework. Record unsuccessful discriminating tests as useful information when they resolve the hypothesis.
