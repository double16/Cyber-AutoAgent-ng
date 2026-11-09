---
schema_version: 1
title: Host and forwarded-header trust
purpose: Test whether untrusted host or forwarding metadata controls security-relevant application decisions.
applicability:
- A mapped operation derives absolute links, routing, redirects or trust decisions from request authority
  or forwarded headers.
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
  when: A mapped operation derives absolute links, routing, redirects or trust decisions from request
    authority or forwarded headers. The assigned work needs method-specific prerequisite checks.
  request: Host and forwarded-header trust reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern host and forwarded-header trust and the assigned work is designing
    future discriminating tests.
  request: Host and forwarded-header trust hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit host and forwarded-header trust hypothesis and its prerequisites are available for
    controlled verification.
  request: Host and forwarded-header trust vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant host and forwarded-header trust boundary has been verified and an authorized minimal
    demonstration with test data is required.
  request: Host and forwarded-header trust execution
  signals_any: []
- phases:
  - analysis
  when: Recorded host and forwarded-header trust observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Host and forwarded-header trust analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/HTTP_Headers_Cheat_Sheet.md
  title: HTTP Security Response Headers Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.790016+00:00'
  selection_reason: HTTP header trust context; host-derived security decisions are original synthesis.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/07-Input_Validation_Testing/17-Testing_for_Host_Header_Injection.md
  title: wstg-host
  retrieved_at: '2026-11-07T10:32:58.926605+00:00'
  selection_reason: WSTG-INPV-17 covers Host/X-Forwarded-Host influence on redirects, caches and reset
    links.
source_coverage: 'Selected retrieved references supply mechanism/control background: HTTP header trust
  context; host-derived security decisions are original synthesis. WSTG-INPV-17 covers Host/X-Forwarded-Host
  influence on redirects, caches and reset links. Limitations: Development-oriented guidance supplies
  mechanism/control background; application policy, live exploitability, negative controls and minimal-impact
  procedure remain original methodology requiring target-specific verification. Original controlled procedures
  and interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Host and forwarded-header trust

## Applicability and prerequisites

A mapped operation derives absolute links, routing, redirects or trust decisions from request
authority or forwarded headers.

Keep the actual connection scheme, host and port within scope. Use a controlled marker hostname, a
test inbox for generated links and knowledge of intended proxy trust boundaries. Do not connect to
another destination merely because a header names it.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The normal request reaches the expected virtual host and generates the intended absolute URL or
security decision. Record actual socket destination, TLS SNI, HTTP authority/Host and normal
forwarding headers separately.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Keep the actual connection scheme, host and port within scope. Use a controlled marker hostname,
   a test inbox for generated links and knowledge of intended proxy trust boundaries. Do not connect
   to another destination merely because a header names it.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Inspect recorded absolute links and proxy-related headers. Design one-header future tests for
   Host, Forwarded host/proto and X-Forwarded-Host/Proto where accepted; an echoed hostname only
   identifies influence, not a harmful trust decision.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Repeat the normal operation and identify the exact field or decision that consumes authority
   metadata.
2. Change one candidate header to an inert controlled hostname while leaving socket destination and
   other authority fields fixed. For HTTP/2, account for :authority and client normalization rather
   than assuming a raw Host edit is transmitted.
3. Observe generated links, redirects or access decisions. For recovery links use only the
   controlled inbox and determine whether a usable test credential is placed in a link to the
   excluded origin; avoid forwarding or exposing real tokens.
4. Compare an invalid host and the trusted canonical host, then restore the baseline. Where multiple
   proxies are involved, vary ordering/duplication only within agreed limits and distinguish
   infrastructure rejection from application consumption.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one incorrect link or reversible trust decision using synthetic account data and an
   owned marker origin. Do not follow sensitive links to uncontrolled hosts; invalidate the test
   link/token and remove temporary content.
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

Virtual-host routing and canonical redirects can legitimately depend on Host. Trusted proxies can
intentionally supply forwarding data. Reflection in an error or link with no sensitive consequence
is not account compromise.

## Evidence requirements

Retain the trust policy, socket/SNI/authority distinction, single-header variations, actual
generated artifact or decision and invalid/canonical controls.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Header acceptance depends on the complete proxy chain. A result on one route does not establish
reset poisoning, cache poisoning or internal routing unless that separate consequence is
demonstrated.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
