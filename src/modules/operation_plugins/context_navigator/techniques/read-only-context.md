---
schema_version: 1
title: Read-only system and trust-boundary mapping
purpose: Map an assigned system context without modifying its state.
applicability:
- Map an assigned system context without modifying its state.
phases: &id001
- reconnaissance
- analysis
active: true
triggers:
- phases: *id001
  when: Recorded system or service evidence suggests an unexamined identity, dependency, or trust boundary.
  signals_any: []
  request: Read-only system and trust-boundary mapping
sources:
- url: https://csrc.nist.gov/pubs/sp/800/115/final
  title: 'NIST SP 800-115: Technical Guide to Information Security Testing and Assessment'
  retrieved_at: '2026-10-25'
  selection_reason: Primary specification or assessment methodology relevant to this guidance.
source_coverage: Small module-authored starter; primary references do not exhaust implementation-specific
  cases.
---

# Read-only system and trust-boundary mapping

## Prerequisites
Confirm the assigned host, access context, and permitted read-only activities. An available shell does not expand authorization.

## Procedure
1. Record the observed operating environment, identity context, services, and explicit scope boundaries.
2. Inspect available configuration and service metadata using the least intrusive permitted read.
3. Map observed dependencies and trust relationships with provenance for each edge. Separate a configured destination from a verified reachable service.
4. Identify potentially sensitive locations by metadata where possible, without unnecessarily reading or exporting their content.
5. Publish the bounded context map with uncertainty, inaccessible sources, and focused follow-up questions.

## Controls and interpretation
Compare independent observations when identities or dependencies conflict. Missing permissions and missing records are limitations rather than proof of absence. Do not alter configuration, enumerate unrelated networks, or collect credentials as a mapping shortcut. Built-in readers, available shell utilities, and remote metadata interfaces are interchangeable capabilities, subject to scope.
