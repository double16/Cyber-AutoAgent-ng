---
schema_version: 1
title: Browser storage and sensitive-data caching
purpose: Assess whether browser persistence exposes sensitive application data beyond its established
  audience or lifecycle.
applicability:
- A mapped browser flow stores authentication/data in browser storage, client caches or offline state.
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
  when: A mapped browser flow stores authentication/data in browser storage, client caches or offline
    state. The assigned work needs method-specific prerequisite checks.
  request: Browser storage and sensitive-data caching reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern browser storage and sensitive-data caching and the assigned work
    is designing future discriminating tests.
  request: Browser storage and sensitive-data caching hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit browser storage and sensitive-data caching hypothesis and its prerequisites are available
    for controlled verification.
  request: Browser storage and sensitive-data caching vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant browser storage and sensitive-data caching boundary has been verified and an authorized
    minimal demonstration with test data is required.
  request: Browser storage and sensitive-data caching execution
  signals_any: []
- phases:
  - analysis
  when: Recorded browser storage and sensitive-data caching observations or comparison outcomes require
    interpretation of policy, controls and remaining uncertainty.
  request: Browser storage and sensitive-data caching analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/HTML5_Security_Cheat_Sheet.md
  title: HTML5 Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.387366+00:00'
  selection_reason: browser storage and sensitive session data.
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Session_Management_Cheat_Sheet.md
  title: Session Management Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.805862+00:00'
  selection_reason: browser storage and sensitive session data.
- url: https://www.rfc-editor.org/rfc/rfc9111.txt
  title: cache-rfc9111
  retrieved_at: '2026-11-06T21:29:21.193551+00:00'
  selection_reason: RFC9111 defines storage/reuse restrictions, cache keys, authenticated response treatment
    and cache-control semantics.
source_coverage: 'Selected retrieved references supply mechanism/control background: browser storage and
  sensitive session data. browser storage and sensitive session data. RFC9111 defines storage/reuse restrictions,
  cache keys, authenticated response treatment and cache-control semantics. Limitations: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. Original
  controlled procedures and interpretation are knowledge-allowed synthesis, not individually source-verified.
  No generic reference establishes target applicability or exploitation.'
---

# Browser storage and sensitive-data caching

## Applicability and prerequisites

A mapped browser flow stores authentication/data in browser storage, client caches or offline state.

Use a disposable browser profile, synthetic sensitive values and an explicit
confidentiality/lifecycle policy. Distinguish same-origin script, another account in the same
profile, a different origin and a local user threat; ordinary owner access to its own storage is not
a violation.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The authorized account loads the agreed synthetic data through the intended interface. Record which
of localStorage, sessionStorage, IndexedDB, Cache API, HTTP cache, service worker and navigation
history actually holds it.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a disposable browser profile, synthetic sensitive values and an explicit
   confidentiality/lifecycle policy. Distinguish same-origin script, another account in the same
   profile, a different origin and a local user threat; ordinary owner access to its own storage is
   not a violation.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Review recorded storage and response-cache behavior and design future logout, account-switch,
   offline and Back-navigation comparisons. Specify the actor who would gain access and the required
   browser capability; storage presence alone is a risk observation.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Inspect only the test application's origin storage and network responses in the disposable
   profile. Identify exact synthetic values and their intended retention policy without reading
   unrelated browser data.
2. Perform logout or switch to a second controlled account, then test actual access to the same
   values through the relevant UI/storage/cache channel without reusing the first account's valid
   credentials.
3. Compare Back navigation and offline behavior with a fresh-profile control and a second origin.
   Identify whether a displayed page is a transient rendering snapshot, disk-persisted response or
   newly authorized network response.
4. For same-origin script exposure, establish the separate script-execution capability or
   shared-origin trust boundary before claiming credential theft. HTTP cache-control headers alone
   do not describe application-managed Cache API contents.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Show one synthetic value accessible to the policy-excluded actor or after the prohibited
   retention boundary. Clear the disposable profile's test-origin storage/caches and remove service
   workers created for the test.
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

Same-origin code can normally read its own non-HttpOnly storage. Back-button rendering does not
prove a server session remains active. Persistence may be intentional for offline use and still
require a threat-specific assessment.

## Evidence requirements

Retain the sensitivity/retention policy, browser profile and actor context, exact storage/cache
channel, logout/account-switch sequence and fresh-context controls.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

External browser testing cannot establish operating-system disk protections or all browser forensic
recoverability. Do not generalize from one engine or profile to every client.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
