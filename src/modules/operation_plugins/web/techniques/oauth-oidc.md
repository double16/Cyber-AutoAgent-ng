---
schema_version: 1
title: OAuth and OpenID Connect flow boundaries
purpose: Distinguish oauth and openid connect flow boundaries from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC.
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
  when: An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC.
    while checking prerequisites in an assigned interaction.
  request: OAuth and OpenID Connect flow boundaries reconnaissance
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - hypothesis_generation
  when: An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC.
    while designing future tests from recorded observations.
  request: OAuth and OpenID Connect flow boundaries hypothesis generation
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - vulnerability_verification
  when: An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC.
    while checking a specific assigned boundary hypothesis.
  request: OAuth and OpenID Connect flow boundaries vulnerability verification
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - execution
  when: An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC.
    after a boundary is verified and a minimal demonstration is authorized.
  request: OAuth and OpenID Connect flow boundaries execution
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - analysis
  when: An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC.
    while interpreting recorded results and competing explanations.
  request: OAuth and OpenID Connect flow boundaries analysis
  signals_any:
  - http.oauth
  - http.oidc
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/OAuth2_Cheat_Sheet.md
  title: OAuth 2.0 Protocol Cheatsheet
  retrieved_at: '2026-11-06T21:29:20.227793+00:00'
  selection_reason: authorization code flow, PKCE and redirect binding.
- url: https://www.rfc-editor.org/rfc/rfc9700.txt
  title: oauth-rfc9700
  retrieved_at: '2026-11-06T21:29:21.136006+00:00'
  selection_reason: OAuth2 security best current practice specifies redirect matching, PKCE, mix-up prevention
    and token replay controls.
- url: https://openid.net/specs/openid-connect-core-1_0.html
  title: 'Final: OpenID Connect Core 1.0 incorporating errata set 2'
  retrieved_at: '2026-11-07T10:32:59.154688+00:00'
  selection_reason: OpenID Connect Core specifies ID-token issuer/audience/signature validation and nonce
    binding; it is not an OAuth access-token profile.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. authorization
  code flow, PKCE and redirect binding. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. OAuth2 security best current practice
  specifies redirect matching, PKCE, mix-up prevention and token replay controls. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. OpenID
  Connect Core specifies ID-token issuer/audience/signature validation and nonce binding; it is not an
  OAuth access-token profile. Limits: Must be read alongside current OAuth BCP; legacy optional flows
  in Core do not override newer recommendations. Numbered procedures and controls are original knowledge-allowed
  synthesis, not individually source-verified. No completed target execution or affected-version claim
  is supplied.'
---

# OAuth and OpenID Connect flow boundaries

## Applicability and prerequisites

An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC. Use an authorized test client and two isolated owned browser sessions, test accounts, registered callback details and the expected client/issuer/resource policy. Identify which endpoints belong to the assessed party; third-party identity-provider testing needs separate scope. Use a browser, capture/replay capability and local token inspection or equivalents.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. From assigned captures identify client, authorization server, redirect URI, response/grant type, scopes and resource server. Distinguish authorization via access tokens from authentication via ID tokens; do not treat token names as proof of consumers.
2. Record transaction/session binding mechanisms: state, PKCE challenge/method/verifier, OIDC nonce, issuer and callback routing. Inspect only observed flow metadata; unknown provider configuration remains a prerequisite, not a reason to probe unrelated issuers.

## Hypothesis generation

1. From recorded flows design separate tests for redirect binding, authorization-code transaction/client binding, login CSRF/mix-up and ID-token validation. Specify which owned session and actor each value came from and which protected effect would establish a violation.
2. Plan accepted fresh flows and negative controls such as an unrelated verifier, invalid code and wrong-session callback. Missing state alone is not a login-CSRF finding: another transaction-bound mechanism may supply protection.
3. Plan legitimate signed wrong-audience/issuer/nonce ID-token comparisons only when a test issuer or owned issuance flow permits them. Editing a signed claim primarily tests integrity; leave impossible claim branches untested. Do not redeem codes during hypothesis generation.

## Vulnerability verification

1. Complete a fresh normal flow for account A, observing final application identity and one authorized API response. Keep account B in a separate cookie jar. Preserve the exact initial transaction and avoid accidental automatic login.
2. Test redirect handling with one approved controlled callback variation, such as a changed path or a distinct controlled origin, while holding the client fixed. Observe actual authorization response destination; rejection, normalization and an error-page link differ from code/token delivery. Do not follow redirects to unapproved hosts.
3. Start fresh transactions for each code test. Change only the verifier or swap a code between the two owned sessions/clients as appropriate, with invalid-code and correct-session controls. A consumed/expired code failure is not evidence of correct binding. Check the resulting application identity, not just token endpoint status.
4. For OIDC, compare the token accepted by the relying party against the established issuer, client audience, signature/key and transaction nonce rules. Use a correctly signed wrong-context token where possible; isolate each rule. An access token accepted at its intended API is not an ID-token validation bypass.
5. Re-run a normal flow and classify the exact violated binding. A successful replay of a valid bearer access token may be intended; sender constraint, revocation or one-time policy must be established before calling replay a defect.

## Execution

1. Demonstrate the verified flow error with a harmless identity display or one owned protected resource, using only the two test accounts. Prove which account the browser acquired without touching other users or collecting real tokens through a third-party destination.
2. Revoke test grants/tokens where the owned flow supports it, remove temporary callbacks and test clients, and end both sessions. Verify the ordinary flow still works and controlled callback content is removed.

## Analysis

1. Separate authorization-server acceptance, code delivery, token redemption and relying-party login; a failure in one does not establish the others. Evaluate PKCE, nonce and state in the complete flow, not as isolated missing parameters.
2. Explain expected public-client behavior, valid multi-audience rules, code expiry/reuse, cross-site cookie policy and deliberate account switching. A weak-looking response type or absent state is context, not standalone proof of account takeover.

## Evidence requirements

Preserve full redirect chain and transaction timeline, browser-session labels, code/token provenance, verifier/nonce relationships, accepted and rejected comparisons and final identity/resource effects. Keep secret values internal; public explanations use placeholders.

## Limitations

External observations cannot establish all client registrations or issuer trust settings. Unsupported grants, third-party providers and unsigned claim mutations remain untested rather than disproven. No token theft or broad provider assessment is authorized.

## Trigger examples and non-examples

Matching: An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
