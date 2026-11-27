---
schema_version: 1
title: SAML assertion and browser SSO boundaries
purpose: Distinguish saml assertion and browser sso boundaries from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned SSO interaction carries a SAML request, response or assertion.
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
  when: An assigned SSO interaction carries a SAML request, response or assertion. while checking prerequisites
    in an assigned interaction.
  request: SAML assertion and browser SSO boundaries reconnaissance
  signals_any:
  - http.saml
- phases:
  - hypothesis_generation
  when: An assigned SSO interaction carries a SAML request, response or assertion. while designing future
    tests from recorded observations.
  request: SAML assertion and browser SSO boundaries hypothesis generation
  signals_any:
  - http.saml
- phases:
  - vulnerability_verification
  when: An assigned SSO interaction carries a SAML request, response or assertion. while checking a specific
    assigned boundary hypothesis.
  request: SAML assertion and browser SSO boundaries vulnerability verification
  signals_any:
  - http.saml
- phases:
  - execution
  when: An assigned SSO interaction carries a SAML request, response or assertion. after a boundary is
    verified and a minimal demonstration is authorized.
  request: SAML assertion and browser SSO boundaries execution
  signals_any:
  - http.saml
- phases:
  - analysis
  when: An assigned SSO interaction carries a SAML request, response or assertion. while interpreting
    recorded results and competing explanations.
  request: SAML assertion and browser SSO boundaries analysis
  signals_any:
  - http.saml
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/SAML_Security_Cheat_Sheet.md
  title: SAML Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.126438+00:00'
  selection_reason: assertion processing, signature validation and replay.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. assertion
  processing, signature validation and replay. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# SAML assertion and browser SSO boundaries

## Applicability and prerequisites

An assigned SSO interaction carries a SAML request, response or assertion. Obtain owned IdP/SP test identities, trusted issuer/key metadata, expected audience/recipient/time and request-correlation policy. Know whether SP-initiated or unsolicited IdP-initiated login is supported. A local XML/signature inspector and browser/replay capability are optional equivalents; no live IdP configuration access is assumed.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned binding and decode the message locally without sending it to public decoders. Locate Response/Assertion IDs, issuer, subject, audience, recipient, validity, InResponseTo and RelayState. Encryption may prevent inspecting assertions; record that limit.
2. Identify exactly which element is signed and the identity element consumed by the SP if observable. Trust comes from established metadata, not an embedded KeyInfo certificate or a successful XML parse. A namespace only recommends the method.

## Hypothesis generation

1. Design future signature-integrity, signed-element selection, recipient/audience, time, request correlation and replay comparisons using recorded messages. Keep each branch distinct and specify a normal successful fresh login and an invalid-signature control.
2. For wrapping hypotheses describe how a modified structure would preserve a valid signed element while attempting to influence a different consumed element. Do not assume generic XML rewriting works; list ID resolution, schema and signed-element-selection unknowns.
3. For audience/time/correlation tests prefer legitimately signed messages from an owned test IdP. Mutation without valid signing confounds those checks with integrity. Do not send assertions or begin SSO transactions in this context.

## Vulnerability verification

1. Complete a fresh accepted login and verify the test identity at the SP in a clean session. Preserve the initiating request and assertion timestamps. Compare missing/corrupted assertion behavior with an independent valid session. Obtain a newly issued valid message and a fresh SP transaction for each non-replay variant, preserving its request reference and testing within validity. Compare against a separate matched untouched message; a previously consumed assertion can be rejected for replay and cannot serve as a signature or claim-policy control.
2. Alter one signed identity/attribute value without recomputing the signature. Separately remove or replace signature/key material as justified by observed processing. Acceptance requires the prohibited identity or entitlement at the SP; XML acceptance or a success page is insufficient.
3. If a selection hypothesis is assigned, use a controlled duplicate/wrapper structure with an unmodified signed assertion and a conflicting owned-test identity outside it. Compare invalid-signature and ordinary-message controls. Demonstrate that the SP consumed the unauthorized value rather than merely accepting the original signed subject.
4. With authorized signing capability, vary audience, recipient, validity or request reference one at a time. For replay use the same assertion in a new isolated session within its validity window, observing the established one-time/correlation policy. Unsolicited SSO may legitimately lack InResponseTo; assess its actual login-CSRF protections separately.
5. Test RelayState destination changes only to approved controlled locations and observe real navigation. Re-run a normal login after each structural branch; parser errors and rejected duplicate IDs are negative results for that structure only.

## Execution

1. Use the verified branch for one harmless SP identity/entitlement read involving owned accounts. Do not create administrator access, retrieve unrelated assertions or make permanent trust-metadata changes.
2. End SP and IdP test sessions as supported, delete temporary test messages/fixtures from callback hosts, and verify normal login. Restore any deliberately changed test-IdP attributes or metadata through the owner.

## Analysis

1. Distinguish signature validation from signed-element selection and policy validation. A valid signature on one element does not prove the consumer used that element, while a rejected signature says nothing about independently signed wrong-audience handling.
2. Explain clock skew, encrypted assertions, legitimate unsolicited login, IdP/SP logout differences and allowed RelayState routes. A self-signed federation certificate is not inherently weak; the relevant issue is trusted key binding, not public CA issuance.

## Evidence requirements

Retain sensitive message bytes and exact transformations, initiating request references, signed and consumed element relationships, trust policy, session identity checks and baseline/negative-control outcomes. Avoid publishing assertion values or personal identifiers.

## Limitations

This method cannot establish unseen federation partners, private-key protection or every XML-library edge case. Unsupported signing/issuance capability makes semantic assertion-policy branches untested. XML entity and browser CSRF chains need their own established prerequisites.

## Trigger examples and non-examples

Matching: An assigned SSO interaction carries a SAML request, response or assertion. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
