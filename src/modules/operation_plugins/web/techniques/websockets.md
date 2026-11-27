---
schema_version: 1
title: WebSocket handshake, message and session boundaries
purpose: Distinguish websocket handshake, message and session boundaries from legitimate behavior using
  bounded, policy-aware comparisons.
applicability:
- An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription workflow.
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
  when: An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription
    workflow. while checking prerequisites in an assigned interaction.
  request: WebSocket handshake, message and session boundaries reconnaissance
  signals_any:
  - http.websocket
- phases:
  - hypothesis_generation
  when: An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription
    workflow. while designing future tests from recorded observations.
  request: WebSocket handshake, message and session boundaries hypothesis generation
  signals_any:
  - http.websocket
- phases:
  - vulnerability_verification
  when: An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription
    workflow. while checking a specific assigned boundary hypothesis.
  request: WebSocket handshake, message and session boundaries vulnerability verification
  signals_any:
  - http.websocket
- phases:
  - execution
  when: An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription
    workflow. after a boundary is verified and a minimal demonstration is authorized.
  request: WebSocket handshake, message and session boundaries execution
  signals_any:
  - http.websocket
- phases:
  - analysis
  when: An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription
    workflow. while interpreting recorded results and competing explanations.
  request: WebSocket handshake, message and session boundaries analysis
  signals_any:
  - http.websocket
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/WebSocket_Security_Cheat_Sheet.md
  title: WebSocket Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.508119+00:00'
  selection_reason: handshake origin, message authorization and session controls.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. handshake
  origin, message authorization and session controls. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# WebSocket handshake, message and session boundaries

## Applicability and prerequisites

An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription workflow. Know the expected handshake origins, authentication mechanism, per-message object/action policy and session expiry/logout contract. Use two owned accounts or subscriptions and harmless test messages. Browser plus frame capture/replay capability or equivalent clients are useful; a raw client cannot by itself demonstrate browser cross-site hijacking. Define small connection/message/time bounds and avoid flooding.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned handshake and subprotocol, cookies/tokens, Origin and any first-message authentication. Distinguish HTTP upgrade success from authenticated readiness and message-level permissions.
2. Record observed message types, correlation IDs, owned resource/topic selectors, server events and close codes. Identify whether authorization is checked per action or at subscription time; no inference is made from an open socket alone.

## Hypothesis generation

1. Using recorded frames design separate future origin/CSWSH, message authorization, authentication ordering and session-lifecycle comparisons. Define an allowed browser origin/session baseline and a wrong-origin or unprivileged actor control.
2. For CSWSH specify how a real controlled attacker page would obtain ambient credentials and send/receive a protected harmless message. An arbitrary Origin forged by a nonbrowser client or an unauthenticated upgrade cannot demonstrate that attack.
3. Plan message sequencing and post-logout tests against the established contract, with unique per-test marker events to exclude buffered data. No sockets, messages, logouts or subscriptions are created during hypothesis generation.

## Vulnerability verification

1. Open one accepted connection in an owned context, complete any required authentication and exchange a harmless permitted message. Capture frames and actual protected effect/event, not just status101. Compare an unauthenticated connection/message under the application's intended policy.
2. For origin protection, use a real browser page on an approved controlled untrusted origin. Observe actual cookies/first-message credentials and ability to perform/read one protected operation. Compare the legitimate origin and a credentialless browser. A public socket or a handshake that later denies protected messages is not authenticated hijacking.
3. For per-message authorization, change one owned resource/topic identifier or action using an actor explicitly denied by policy. Compare the owner actor's allowed result and a nonexistent/inert selector. Verify synthetic data/event or durable state; a generic acknowledgement does not prove action execution.
4. For authentication ordering, send one harmless protected request before authentication and compare the same request afterward. Do not treat an accepted ping or public banner before login as a failure. Use only observed message schema; malformed frames provide parser clues, not permission evidence.
5. For logout/expiry, keep one owned connection open, end/invalidate its session through the normal flow and trigger a fresh nonsecret event afterward from an independently authorized owned source. Compare a still-valid connection and distinguish new event delivery from queued/buffered frames. Apply the stated revocation timing/grace policy.
6. Repeat a valid fresh connection baseline and close extra sockets. Stop at the fixed connection/message envelope; no connection exhaustion, compression bomb or oversized message test belongs to this method.

## Execution

1. Demonstrate one prohibited action, synthetic event disclosure or browser-origin trust failure with a single harmless frame/event. Keep all accounts, channels and event payloads test-owned; do not subscribe to unrelated topics or relay real traffic.
2. Unsubscribe and close every test connection, delete/revert test-created messages/state and remove attacker-page content/listeners. Verify no reconnection loop or background browser tab continues the test.

## Analysis

1. Separate transport upgrade, authentication, action authorization and ongoing event entitlement. A success close/status code, open channel or client-visible ACK is not a protected effect.
2. Explain intentional public sockets, browser cookie restrictions, application tokens, subprotocol negotiation, per-topic policy, buffered events and allowed revocation grace. Origin is a browser trust control; it is not a substitute for authentication against nonbrowser clients.

## Evidence requirements

Preserve handshake and relevant frames, browser/origin/credential context, owned resource/topic entitlement, message IDs, allowed/denied controls, actual synthetic state/event and lifecycle timestamps. Redact tokens from public outputs and record connection cleanup.

## Limitations

No broad protocol fuzzing, real-user interception, flooding or compression/resource attack is supplied. Missing browser capability limits CSWSH conclusions; absent fresh events or explicit expiry policy limits lifecycle conclusions. One authorized message says nothing about every action type.

## Trigger examples and non-examples

Matching: An assigned interaction upgrades to WebSocket or uses an observed WebSocket message/subscription workflow. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
