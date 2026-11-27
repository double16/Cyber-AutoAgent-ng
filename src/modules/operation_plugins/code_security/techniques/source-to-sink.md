---
schema_version: 1
title: Source-to-sink injection analysis
purpose: Trace externally controlled values to security-sensitive operations.
applicability:
- Trace externally controlled values to security-sensitive operations.
phases: &id001
- analysis
- hypothesis_generation
- vulnerability_verification
active: true
triggers:
- phases: *id001
  when: Source code passes externally controlled data toward a query or other interpreter boundary.
  signals_any: []
  request: Source-to-sink injection analysis
sources:
- url: https://cwe.mitre.org/data/definitions/89.html
  title: 'MITRE CWE-89: SQL Injection'
  retrieved_at: '2026-10-25'
  selection_reason: Primary specification or assessment methodology relevant to this guidance.
source_coverage: Small module-authored starter; primary references do not exhaust implementation-specific
  cases.
---

# Source-to-sink injection analysis

## Prerequisites
Inspect the assigned source revision and its relevant call paths. A dependency name, scanner warning, or suspicious variable name is only a lead.

## Procedure
1. Locate the input source and record the exact file, symbol, and source revision.
2. Follow the value through assignments, transformations, validation, and reachable call edges to the interpreter boundary.
3. Distinguish data parameters from text used to construct executable syntax. For SQL, examine whether controlled values change query structure or remain bound data.
4. Inspect relevant validation and safe API use before deciding that the path is unsafe. Explain unresolved dynamic calls and missing code.
5. State the necessary reachability and input preconditions, then define a focused verification with an ordinary input and a discriminating control.

## Interpretation and limits
A sink alone does not establish externally controlled reachability; a source alone does not establish unsafe interpretation. Preserve both vulnerable and safe paths. Use any available source reader or static analyzer; no particular scanner or language runtime is mandatory. Do not execute untrusted repository code merely to inspect it.
