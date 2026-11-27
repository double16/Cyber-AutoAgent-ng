---
schema_version: 1
title: Object-level authorization/IDOR
purpose: Test whether an actor can read or change a specific object without its required entitlement.
applicability:
- A recorded operation addresses an object by a URL, body, nested, batch, or indirect identifier.
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
  when: A recorded operation addresses an object by a URL, body, nested, batch, or indirect identifier.
    The assigned work needs method-specific prerequisite checks.
  request: Object-level authorization/IDOR reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern object-level authorization/idor and the assigned work is designing
    future discriminating tests.
  request: Object-level authorization/IDOR hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit object-level authorization/idor hypothesis and its prerequisites are available for
    controlled verification.
  request: Object-level authorization/IDOR vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant object-level authorization/idor boundary has been verified and an authorized minimal
    demonstration with test data is required.
  request: Object-level authorization/IDOR execution
  signals_any: []
- phases:
  - analysis
  when: Recorded object-level authorization/idor observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Object-level authorization/IDOR analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.md
  title: Insecure Direct Object Reference Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.526055+00:00'
  selection_reason: object authorization versus identifier complexity.
source_coverage: 'Selected retrieved references supply mechanism/control background: object authorization
  versus identifier complexity. Limitations: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Original controlled procedures and
  interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Object-level authorization/IDOR

## Applicability and prerequisites

A recorded operation addresses an object by a URL, body, nested, batch, or indirect identifier.

Use two test identities A and B, their known test objects, and an established action-by-object
entitlement matrix. Ownership alone is insufficient: identify sharing, delegated access, public
visibility and role grants before selecting a prohibited comparison.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A can perform the chosen action on object A and B can perform the same action on object B. Choose an
unshared B object for which A has no grant; verify that object exists through B rather than
interpreting a 404 alone.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use two test identities A and B, their known test objects, and an established action-by-object
   entitlement matrix. Ownership alone is insufficient: identify sharing, delegated access, public
   visibility and role grants before selecting a prohibited comparison.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Identify the actor, object selector and operation from recorded captures. Design a future A-on-B
   comparison, an A-on-A positive control and a nonexistent-object control; list any unknown sharing
   policy instead of assuming that different owners must be isolated.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Repeat the permitted A-on-A request, isolating A's credentials from all B credentials and ambient
   browser sessions.
2. Change only the object selector to B's known unshared test object. For batch and nested requests,
   change one selector at a time and identify which returned element belongs to B.
3. Inspect the actual protected property or durable state, not just response status, length or
   echoed identifiers. For writes, have B read back the exact test object using a separate session.
4. Restore A-on-A and compare with a nonexistent selector. If shared access or a privileged role
   explains the result, select a genuinely prohibited entitlement or classify the comparison as
   inconclusive.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one forbidden read of a synthetic marker or one reversible field change on B's test
   object. Restore the field with B's authorized identity and confirm restoration; do not enumerate
   unrelated identifiers.
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

A changed object can be legitimately public, shared, delegated or covered by an organizational role.
An identical generic response can be an application shell, cache entry or asynchronous
acknowledgement.

## Evidence requirements

Retain the entitlement basis, isolated actor context, baseline and substituted selector, protected
marker or read-back state, nonexistent-object control and restoration result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

One selector and action establish only that tested path. Random identifiers reduce guessing but do
not establish authorization. Unknown sharing or unreadable state prevents a conclusive violation
claim.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
