---
schema_version: 1
title: TLS and mixed content
purpose: Assess transport authenticity, confidentiality and browser use of insecure subresources on mapped
  application flows.
applicability:
- An assigned HTTPS service or browser flow carries sensitive data or references resources using insecure
  transport.
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
  when: An assigned HTTPS service or browser flow carries sensitive data or references resources using
    insecure transport. The assigned work needs method-specific prerequisite checks.
  request: TLS and mixed content reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern tls and mixed content and the assigned work is designing future
    discriminating tests.
  request: TLS and mixed content hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit tls and mixed content hypothesis and its prerequisites are available for controlled
    verification.
  request: TLS and mixed content vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant tls and mixed content boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: TLS and mixed content execution
  signals_any: []
- phases:
  - analysis
  when: Recorded tls and mixed content observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: TLS and mixed content analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Transport_Layer_Security_Cheat_Sheet.md
  title: Transport Layer Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.911462+00:00'
  selection_reason: transport configuration and browser security.
source_coverage: 'Selected retrieved references supply mechanism/control background: transport configuration
  and browser security. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# TLS and mixed content

## Applicability and prerequisites

An assigned HTTPS service or browser flow carries sensitive data or references resources using
insecure transport.

Keep the exact authorized host, scheme and port boundary. Use a TLS client with certificate
verification enabled and a real browser with synthetic data. Establish expected client trust policy;
do not infer insecure server behavior from a tool configured to ignore certificates.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The intended HTTPS connection validates the hostname and chain using the expected trust store, and
the ordinary browser flow completes with its network requests recorded.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Keep the exact authorized host, scheme and port boundary. Use a TLS client with certificate
   verification enabled and a real browser with synthetic data. Establish expected client trust
   policy; do not infer insecure server behavior from a tool configured to ignore certificates.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded certificates, negotiated settings and browser resource URLs identify future
   discriminating checks. Distinguish an insecure-looking URL from a browser request that was
   blocked or automatically upgraded; no plaintext transmission is inferred solely from markup.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Connect only to the authorized TLS endpoint and record hostname/SNI, validation outcome,
   negotiated protocol and relevant certificate dates/chain. Compare with normal system trust rather
   than a verification-disabled client.
2. If permitted, perform a bounded protocol/cipher capability check using equivalent TLS tooling.
   Interpret results against the established current transport policy, not an obsolete universal
   cipher list embedded in this method.
3. Load the assigned flow in a clean browser and inspect the actual network treatment of HTTP
   subresources, forms, redirects and cookies. Identify which sensitive bytes or active content
   actually travel insecurely, if any.
4. Compare a securely loaded resource and a browser-blocked/auto-upgraded reference. Test initial
   HTTP entry only when that exact endpoint is authorized; do not expand scheme or port scope to
   look for redirects.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate only an actual insecure request carrying a synthetic marker or an active resource
   loaded without the required transport guarantee. Do not perform interception or downgrade
   attacks; remove test content and clear the disposable browser state.
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

Private trust roots can be legitimate for managed clients. Mixed-content references may be blocked
or upgraded before transmission. A header omission alone does not prove plaintext credential
exposure.

## Evidence requirements

Retain client trust settings, authorized connection tuple, TLS validation/negotiation output and
browser network evidence distinguishing attempted from completed insecure loads.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

External testing cannot prove private-key custody or backend service transport. Supported TLS
settings and browser behavior may vary by client and endpoint; describe the tested policy and
context.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
