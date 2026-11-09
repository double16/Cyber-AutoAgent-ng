---
schema_version: 1
title: Tenant isolation
purpose: Test whether a request can cross an established tenant boundary through data, routing or processing
  context.
applicability:
- A recorded application operation selects a tenant through identity, path, host, header, body or job
  context.
- The relevant operation, identities, data and side effects are within the resolved assessment scope.
phases:
- reconnaissance
- hypothesis_generation
- vulnerability_verification
- execution
- analysis
active: true
triggers:
- phases:
  - reconnaissance
  when: A recorded application operation selects a tenant through identity, path, host, header, body or
    job context. The assigned work needs method-specific prerequisite checks.
  request: Tenant isolation reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern tenant isolation and the assigned work is designing future discriminating
    tests.
  request: Tenant isolation hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit tenant isolation hypothesis and its prerequisites are available for controlled verification.
  request: Tenant isolation vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant tenant isolation boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Tenant isolation execution
  signals_any: []
- phases:
  - analysis
  when: Recorded tenant isolation observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Tenant isolation analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.md
  title: Multi-Tenant Application Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.597139+00:00'
  selection_reason: tenant identity and data isolation.
source_coverage: 'Selected retrieved references supply mechanism/control background: tenant identity and
  data isolation. Limitations: Development-oriented guidance supplies mechanism/control background; application
  policy, live exploitability, negative controls and minimal-impact procedure remain original methodology
  requiring target-specific verification. Original controlled procedures and interpretation are knowledge-allowed
  synthesis, not individually source-verified. No generic reference establishes target applicability or
  exploitation.'
---

# Tenant isolation

## Applicability and prerequisites

A recorded application operation selects a tenant through identity, path, host, header, body or job
context.

Use two authorized test tenants T1 and T2, distinct members and unique synthetic data. Establish
that the chosen actor is not a member, guest, support delegate or cross-tenant administrator of T2.
Fix the expected isolation policy and permitted request hosts.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

Each member can read its own tenant marker through the assigned operation. Verify object identifiers
and tenant labels independently with their legitimate member sessions.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use two authorized test tenants T1 and T2, distinct members and unique synthetic data. Establish
   that the chosen actor is not a member, guest, support delegate or cross-tenant administrator of
   T2. Fix the expected isolation policy and permitted request hosts.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Identify competing tenant selectors in recorded interactions. Design future tests that vary one
   selector while leaving actor and object membership fixed, with same-tenant and nonexistent-tenant
   controls.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Run T1's allowed operation with a clean T1 session and establish which tenant marker or write
   destination it uses.
2. Change one supplied tenant selector to T2 while retaining the T1 actor. Test contradictory
   selector precedence separately; do not simultaneously change host, token, object and header.
3. Check that returned data or a queued job's final effect actually belongs to T2. For writes, read
   back with T2's legitimate member. Distinguish a tenant label echoed from input from server-side
   routing.
4. Restore the T1 selector, test an invalid selector, and compare a deliberately shared object if
   the product supports sharing. Cross-tenant access is a violation only where the established
   policy excludes this actor.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Read one T2 synthetic marker or perform one reversible change on an agreed T2 test object. Remove
   test jobs or records and verify both tenants' expected state afterward.
3. Verify restoration through the relevant authoritative channel. If cleanup cannot be confirmed,
   stop further changes and preserve that limitation.

## Analysis

1. Compare the observed result with the established policy and the local controls above. A
   successful request, available artifact, changed response or tool execution establishes
   availability only; confirm the precise protected data or effect needed for the claim.
2. Classify the tested mechanism as supported, refuted for the tested case, or inconclusive. Keep
   the tested actor, object, state and channel explicit, and preserve competing explanations and
   untested variants.

### False positives and competing explanations

Global templates, public catalogs, shared projects, federation and support roles can intentionally
span tenants. Identical data or numeric object IDs alone does not establish cross-tenant provenance.

## Evidence requirements

Retain tenant membership and sharing basis, selector precedence, distinct tenant markers, isolated
actor context, controlled comparisons and final job/read-back result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

A passing HTTP path does not establish isolation for exports, asynchronous jobs, search, file
storage or other data planes. Keep untested planes explicit rather than inferring their behavior.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
