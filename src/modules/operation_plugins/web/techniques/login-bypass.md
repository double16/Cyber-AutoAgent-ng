---
schema_version: 1
title: Login bypass
purpose: Determine whether a login flow creates usable authenticated access without satisfying its required
  authentication policy.
applicability:
- A mapped login or authentication transition has an observed request sequence and a protected test resource.
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
  when: A mapped login or authentication transition has an observed request sequence and a protected test
    resource. The assigned work needs method-specific prerequisite checks.
  request: Login bypass reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern login bypass and the assigned work is designing future discriminating
    tests.
  request: Login bypass hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit login bypass hypothesis and its prerequisites are available for controlled verification.
  request: Login bypass vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant login bypass boundary has been verified and an authorized minimal demonstration with
    test data is required.
  request: Login bypass execution
  signals_any: []
- phases:
  - analysis
  when: Recorded login bypass observations or comparison outcomes require interpretation of policy, controls
    and remaining uncertainty.
  request: Login bypass analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Authentication_Cheat_Sheet.md
  title: Authentication Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.626925+00:00'
  selection_reason: authentication policy and failure handling.
source_coverage: 'Selected retrieved references supply mechanism/control background: authentication policy
  and failure handling. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Login bypass

## Applicability and prerequisites

A mapped login or authentication transition has an observed request sequence and a protected test
resource.

Use a disposable account, known valid and invalid authenticators, clean client contexts and a
protected identity-returning resource. Establish whether anonymous or guest access is intended, and
separate MFA requirements into their applicable policy.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A valid login reaches the account's synthetic private marker; an invalid login and a fresh anonymous
context cannot reach that same protected resource under the established policy.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a disposable account, known valid and invalid authenticators, clean client contexts and a
   protected identity-returning resource. Establish whether anonymous or guest access is intended,
   and separate MFA requirements into their applicable policy.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded sequences identify the transition claimed to establish authentication and any
   client-controlled success flags, identifiers or state tokens. Design future
   omissions/substitutions without treating an existing authenticated browser as anonymous.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Repeat valid and invalid login controls with isolated cookie jars; verify the resulting server
   identity at the protected resource.
2. Omit or change one authentication input or one observed transition at a time, preserving a
   syntactically valid request and excluding all previously valid credentials.
3. Follow the returned session using a new request to the protected identity resource. A redirect,
   success field, token-shaped response or UI route alone does not prove authentication.
4. Compare a fresh anonymous session, the invalid-authenticator flow and the valid flow again.
   Resolve guest accounts, public resources, stale sessions and upstream SSO before claiming bypass.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Use the bypassed session only to read the disposable account's agreed private marker. Log out and
   invalidate test sessions; avoid password spraying or testing other people's accounts.
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

A login page can return 200 on failure, and a preexisting SSO session can authenticate legitimately.
A token may identify an anonymous session rather than an authenticated principal.

## Evidence requirements

Retain the expected authentication policy, isolated session provenance, valid/invalid/modified
transitions and the subsequent protected-resource identity result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

The result applies to the tested authentication path and account policy. Device trust, conditional
access and federation can require additional controls not available externally.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
