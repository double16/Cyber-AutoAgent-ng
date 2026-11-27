---
schema_version: 1
title: Request smuggling/desynchronization
purpose: Test whether front-end and back-end HTTP parsing disagree about request boundaries on an isolated
  connection.
applicability:
- A mapped HTTP service uses multiple parsers or proxy layers and controlled desynchronization testing
  is explicitly permitted.
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
  when: A mapped HTTP service uses multiple parsers or proxy layers and controlled desynchronization testing
    is explicitly permitted. The assigned work needs method-specific prerequisite checks.
  request: Request smuggling/desynchronization reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern request smuggling/desynchronization and the assigned work is designing
    future discriminating tests.
  request: Request smuggling/desynchronization hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit request smuggling/desynchronization hypothesis and its prerequisites are available
    for controlled verification.
  request: Request smuggling/desynchronization vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant request smuggling/desynchronization boundary has been verified and an authorized
    minimal demonstration with test data is required.
  request: Request smuggling/desynchronization execution
  signals_any: []
- phases:
  - analysis
  when: Recorded request smuggling/desynchronization observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Request smuggling/desynchronization analysis
  signals_any: []
sources:
- url: https://www.rfc-editor.org/rfc/rfc9112.txt
  title: http-rfc9112
  retrieved_at: '2026-11-06T21:29:21.129994+00:00'
  selection_reason: RFC9112 sections6 and11 define message framing and request-smuggling security considerations.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/07-Input_Validation_Testing/15-Testing_for_HTTP_Splitting_Smuggling.md
  title: wstg-smuggling
  retrieved_at: '2026-11-07T10:33:40.380574+00:00'
  selection_reason: WSTG-INPV-15 supplies historical parser-disagreement context, not contemporary normative
    framing rules.
source_coverage: 'Selected retrieved references supply mechanism/control background: RFC9112 sections6
  and11 define message framing and request-smuggling security considerations. WSTG-INPV-15 supplies historical
  parser-disagreement context, not contemporary normative framing rules. Limitations: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. Historical
  examples include obsolete IIS behavior and outdated duplicate-length discussion; use RFC9112 for current
  requirements. Does not cover every HTTP2 downgrade/client-side desync. Original controlled procedures
  and interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Request smuggling/desynchronization

## Applicability and prerequisites

A mapped HTTP service uses multiple parsers or proxy layers and controlled desynchronization testing
is explicitly permitted.

Require an isolated test route/connection pool or a controlled environment where other users'
requests cannot be affected. Establish exact bytes, protocol versions, intermediary transformations,
request caps and abort conditions. Without isolation, restrict work to hypothesis and analysis.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

Two well-formed requests on the agreed connection produce their expected ordered responses and
synthetic per-request markers. A normal close/reconnect returns a clean baseline.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Require an isolated test route/connection pool or a controlled environment where other users'
   requests cannot be affected. Establish exact bytes, protocol versions, intermediary
   transformations, request caps and abort conditions. Without isolation, restrict work to
   hypothesis and analysis.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded protocol behavior identify a specific framing ambiguity, such as conflicting
   lengths or translation between HTTP versions. Design future exact-byte and unambiguous controls
   on an isolated channel; a timeout alone is not a desynchronization result.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Verify that tooling preserves the intended wire bytes and connection reuse; record HTTP/1.1
   versus HTTP/2 translation and any proxy normalization.
2. On the isolated channel, submit one agreed ambiguous framing case followed only by the tester's
   own marker request. Keep body content inert and avoid victim-request interception payloads.
3. Compare response order, marker attribution and leftover-byte handling with an equivalent
   unambiguous framing request. Require repeatable cross-request boundary influence, not merely a
   400, delay or connection close.
4. Close the entire test connection/pool after each trial, reestablish a fresh baseline and stop on
   unexpected traffic or service degradation. Treat front-end rejection and normalized forwarding as
   limited negative results.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate only a harmless marker request being interpreted under the wrong boundary within the
   isolated test channel. Close and drain test connections and confirm clean ordered responses
   afterward; never poison a shared production queue.
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

Read timeouts, slow backends, buffering, pipelining errors and client-side header rewriting can
resemble parser disagreement. A rejected ambiguous request may be correct defensive behavior.

## Evidence requirements

Retain isolation assurance, exact transmitted bytes and negotiated protocols, connection
identifiers, paired response sequence and unambiguous/fresh-connection controls.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

A safe isolated negative does not cover all proxy hops or protocol translations. No generic raw
payload is universally valid; derive framing from the actual parsers and avoid broad automated
payload sweeps.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
