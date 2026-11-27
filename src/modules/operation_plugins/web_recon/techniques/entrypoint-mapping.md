---
schema_version: 1
title: HTTP application entry-point mapping
purpose: Build an observed request and response map for later hypothesis work.
applicability:
- Build an observed request and response map for later hypothesis work.
phases: &id001
- reconnaissance
active: true
triggers:
- phases: *id001
  when: HTTP interactions expose routes, methods, forms, headers, cookies, or request parameters.
  signals_any: []
  request: HTTP application entry-point mapping
sources:
- url: https://wstg.owasp.org/v4.2/4-Web_Application_Security_Testing/01-Information_Gathering/06-Identify_Application_Entry_Points/
  title: 'OWASP WSTG: Identify Application Entry Points'
  retrieved_at: '2026-10-25'
  selection_reason: Primary specification or assessment methodology relevant to this guidance.
source_coverage: Small module-authored starter; primary references do not exhaust implementation-specific
  cases.
---

# HTTP application entry-point mapping

## Prerequisites
Use the assigned application boundary and permitted access context. Capture traffic only when the current phase allows it; frozen-evidence tasks inspect existing captures.

## Procedure
1. Record each observed operation with its origin, path, method, access context, and evidence reference.
2. Identify inputs in query strings, request bodies, path segments, headers, and cookies. Preserve their parent operation and input location.
3. Compare requests and responses across ordinary navigation and documented workflows. Record redirects, content types, response fields, and explicitly linked client declarations.
4. Separate observed requests from inferred operations. An empty parameter list means unrecorded inputs unless complete evidence establishes otherwise.
5. Publish a bounded inventory of assigned entry points, trust boundaries, and unresolved capture limitations.

## Controls and interpretation
Do not infer that an endpoint supports only the observed method. A route name, reflected input, or successful response is not a vulnerability. Keep unrelated origins and ports outside the assigned boundary. Any available request capture, browser, or artifact reader may supply observations; no named tool is required.
