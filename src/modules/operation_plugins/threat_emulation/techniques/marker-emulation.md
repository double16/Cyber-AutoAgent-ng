---
schema_version: 1
title: Marker-based adversary behavior emulation
purpose: Design bounded behavior simulations with observable markers and cleanup.
applicability:
- Design bounded behavior simulations with observable markers and cleanup.
phases: &id001
- execution
- analysis
active: true
triggers:
- phases: *id001
  when: An authorized emulation objective identifies adversary behavior and a detection question.
  signals_any: []
  request: Marker-based adversary behavior emulation
sources:
- url: https://attack.mitre.org/resources/adversary-emulation-plans/
  title: 'MITRE ATT&CK: Adversary Emulation Plans'
  retrieved_at: '2026-10-25'
  selection_reason: Primary specification or assessment methodology relevant to this guidance.
source_coverage: Small module-authored starter; primary references do not exhaust implementation-specific
  cases.
---

# Marker-based adversary behavior emulation

## Prerequisites
Use an explicit authorized behavior, bounded environment, stop conditions, and detection objective. A vulnerability or CVE alone does not imply an ATT&CK technique.

## Procedure
1. Identify the evidenced adversary behavior and the defensive observation the simulation should exercise.
2. Choose the smallest permitted action that represents the behavior, with a distinct non-sensitive marker and a cleanup plan.
3. Record the expected telemetry before execution. Specify a baseline or negative control that helps distinguish the marker from unrelated activity.
4. Execute only when the current phase permits live actions. Preserve command, trace, and artifact evidence for the behavior actually performed.
5. Compare observed telemetry with the stated detection question, then clean up permitted test state and record residual limitations.

## Interpretation and limits
Map ATT&CK only when execution evidence supports the behavior and confidence is sufficient. A missing alert does not prove an ineffective control without validated telemetry collection. Prefer interchangeable available capabilities; do not require a named framework or reproduce published commands verbatim. Stop when scope, reversibility, or evidence prerequisites are unmet.
