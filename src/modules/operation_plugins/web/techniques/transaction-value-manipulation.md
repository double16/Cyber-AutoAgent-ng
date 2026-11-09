---
schema_version: 1
title: Transaction and value manipulation
purpose: Test whether security-relevant transaction values remain bound to server-side calculation and
  authorization.
applicability:
- A mapped transaction accepts or returns amount, quantity, currency, recipient, discount or similar protected
  values.
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
  when: A mapped transaction accepts or returns amount, quantity, currency, recipient, discount or similar
    protected values. The assigned work needs method-specific prerequisite checks.
  request: Transaction and value manipulation reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern transaction and value manipulation and the assigned work is designing
    future discriminating tests.
  request: Transaction and value manipulation hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit transaction and value manipulation hypothesis and its prerequisites are available
    for controlled verification.
  request: Transaction and value manipulation vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant transaction and value manipulation boundary has been verified and an authorized minimal
    demonstration with test data is required.
  request: Transaction and value manipulation execution
  signals_any: []
- phases:
  - analysis
  when: Recorded transaction and value manipulation observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Transaction and value manipulation analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Transaction_Authorization_Cheat_Sheet.md
  title: Transaction Authorization Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.875723+00:00'
  selection_reason: binding authorization to transaction values.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/10-Business_Logic_Testing/01-Test_Business_Logic_Data_Validation.md
  title: wstg-business-values
  retrieved_at: '2026-11-07T10:33:40.380270+00:00'
  selection_reason: WSTG-BUSL-01 distinguishes business-valid data from syntactic validation.
source_coverage: 'Selected retrieved references supply mechanism/control background: binding authorization
  to transaction values. WSTG-BUSL-01 distinguishes business-valid data from syntactic validation. Limitations:
  Development-oriented guidance supplies mechanism/control background; application policy, live exploitability,
  negative controls and minimal-impact procedure remain original methodology requiring target-specific
  verification. Original controlled procedures and interpretation are knowledge-allowed synthesis, not
  individually source-verified. No generic reference establishes target applicability or exploitation.'
---

# Transaction and value manipulation

## Applicability and prerequisites

A mapped transaction accepts or returns amount, quantity, currency, recipient, discount or similar
protected values.

Use a sandbox transaction or reversible test reservation with no real monetary settlement. Establish
authoritative pricing, units, rounding, permitted discounts and which values the actor may choose;
user-editable values are not inherently untrusted business decisions.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The normal transaction uses the expected server-calculated values, and any approval/challenge
corresponds to those exact values and recipient.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a sandbox transaction or reversible test reservation with no real monetary settlement.
   Establish authoritative pricing, units, rounding, permitted discounts and which values the actor
   may choose; user-editable values are not inherently untrusted business decisions.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded requests identify client-supplied totals, signed quotes and authorization steps.
   Design future single-field tampering and post-approval value-change tests, with correct-price and
   invalid-input controls.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Create a fresh test transaction with known inputs and compare the server's authoritative quote
   and final calculation.
2. Change one protected value such as total, discount or recipient while preserving ordinary inputs.
   Confirm whether the server recalculates, rejects or actually commits the proposed value.
3. Where separate approval exists, approve the normal values and change one significant value before
   final execution. Verify whether prior approval is invalidated or incorrectly reused.
4. Compare valid boundary values and invalid/nonrepresentable inputs using the established unit and
   rounding rules. Read the final ledger/order state instead of trusting a displayed total or an
   echoed request.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one incorrect committed synthetic value in the sandbox or a reversible test
   reservation. Cancel/refund through the approved test process, verify balances/state and avoid
   real recipients.
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

Taxes, currency conversion, prorating, coupons and eventual reconciliation can legitimately change
totals. A UI mismatch is not a transaction-integrity violation unless the unauthorized value affects
the authoritative state.

## Evidence requirements

Retain calculation/authorization policy, units and rounding basis, original and varied transaction
values, quote/approval binding and final ledger or order result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

External observations may not reveal settlement or reconciliation. A safe sandbox test does not
establish behavior of every payment provider or currency.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
