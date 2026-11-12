---
schema_version: 1
title: OAuth and OpenID Connect flow boundaries
purpose: Assess OAuth/OIDC flow binding and discovery/key-fetch trust using policy-aware
  controlled comparisons.
applicability:
- An assigned authorization, callback, token or federated-login interaction uses OAuth
  or OIDC.
- An assigned relying-party interaction retrieves OIDC discovery metadata or signing
  keys.
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
  when: An assigned OAuth/OIDC flow or discovery/signing-key fetch requires prerequisite
    mapping.
  request: OAuth and OpenID Connect flow boundaries reconnaissance
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - hypothesis_generation
  when: An assigned OAuth/OIDC flow or discovery/signing-key fetch requires future
    test design from recorded observations.
  request: OAuth and OpenID Connect flow boundaries hypothesis generation
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - vulnerability_verification
  when: An assigned OAuth/OIDC flow or discovery/signing-key fetch requires controlled
    verification of an explicit boundary hypothesis.
  request: OAuth and OpenID Connect flow boundaries vulnerability verification
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - execution
  when: An assigned OAuth/OIDC flow or discovery/signing-key fetch requires a minimal
    demonstration of a verified boundary violation.
  request: OAuth and OpenID Connect flow boundaries execution
  signals_any:
  - http.oauth
  - http.oidc
- phases:
  - analysis
  when: An assigned OAuth/OIDC flow or discovery/signing-key fetch requires interpretation
    of recorded comparisons.
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
  selection_reason: OAuth2 security best current practice specifies redirect matching,
    PKCE, mix-up prevention and token replay controls.
- url: https://openid.net/specs/openid-connect-core-1_0.html
  title: 'Final: OpenID Connect Core 1.0 incorporating errata set 2'
  retrieved_at: '2026-11-07T10:32:59.154688+00:00'
  selection_reason: OpenID Connect Core specifies ID-token issuer/audience/signature
    validation and nonce binding; it is not an OAuth access-token profile.
- url: https://openid.net/specs/openid-connect-discovery-1_0.html
  title: OpenID Connect Discovery 1.0 incorporating errata set 2
  retrieved_at: '2026-11-12'
  selection_reason: Issuer consistency, provider configuration and HTTPS signing-key
    metadata; public discovery and cross-host endpoints can be legitimate.
source_coverage: 'Independently selected retrieved references supply mechanism/control
  background. authorization code flow, PKCE and redirect binding. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability,
  negative controls and minimal-impact procedure remain original methodology requiring
  target-specific verification. OAuth2 security best current practice specifies redirect
  matching, PKCE, mix-up prevention and token replay controls. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability,
  negative controls and minimal-impact procedure remain original methodology requiring
  target-specific verification. OpenID Connect Core specifies ID-token issuer/audience/signature
  validation and nonce binding; it is not an OAuth access-token profile. Limits: Must
  be read alongside current OAuth BCP; legacy optional flows in Core do not override
  newer recommendations. Numbered procedures and controls are original knowledge-allowed
  synthesis, not individually source-verified. No completed target execution or affected-version
  claim is supplied. Discovery supplies issuer and metadata requirements, not a universal
  same-host fetch rule. Controlled redirect, destination and credential-forwarding
  comparisons are original knowledge-allowed methodology, not source-proven SSRF.
  Public metadata and public signing keys are expected. No affected-version assertions
  are made.'
---


# OAuth and OpenID Connect flow boundaries

## Applicability and prerequisites

An assigned authorization, callback, token or federated-login interaction uses OAuth or OIDC. Use an authorized test client and two isolated owned browser sessions, test accounts, registered callback details and the expected client/issuer/resource policy. Identify which endpoints belong to the assessed party; third-party identity-provider testing needs separate scope. Use a browser, capture/replay capability and local token inspection or equivalents.

For discovery-only work, browser sessions and test accounts are needed only when the chosen comparison requires login. Establish the configured issuer, relying-party owner, trust policy, metadata provenance and whether fetches occur in the browser or server. A public discovery URL alone does not establish a server-side fetch. Controlled metadata, redirect and key responses require an authorized test issuer or equivalent owned configuration; do not modify a shared production issuer.

Preserve configured HTTP/HTTPS proxies where supported. Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. From assigned captures identify client, authorization server, redirect URI, response/grant type, scopes and resource server. Distinguish authorization via access tokens from authentication via ID tokens; do not treat token names as proof of consumers.
2. Record transaction/session binding mechanisms: state, PKCE challenge/method/verifier, OIDC nonce, issuer and callback routing. Inspect only observed flow metadata; unknown provider configuration remains a prerequisite, not a reason to probe unrelated issuers.

3. For an assigned discovery interaction, map the configured issuer, returned issuer, metadata URL, JWKS URI and observed redirect chain. Distinguish public configuration retrieval from sensitive information disclosure; record actual internal information and its audience policy rather than labeling all endpoint URLs sensitive. Do not guess additional discovery hosts.

## Hypothesis generation

1. From recorded flows design separate tests for redirect binding, authorization-code transaction/client binding, login CSRF/mix-up and ID-token validation. Specify which owned session and actor each value came from and which protected effect would establish a violation.
2. Plan accepted fresh flows and negative controls such as an unrelated verifier, invalid code and wrong-session callback. Missing state alone is not a login-CSRF finding: another transaction-bound mechanism may supply protection.
3. Plan legitimate signed wrong-audience/issuer/nonce ID-token comparisons only when a test issuer or owned issuance flow permits them. Editing a signed claim primarily tests integrity; leave impossible claim branches untested. Do not redeem codes during hypothesis generation.

4. From recorded discovery evidence, plan separate issuer-consistency, destination-policy, key-source and outbound-credential tests. Specify the input the excluded actor controls and the actual relying party making the fetch. A legitimate cross-host JWKS URL is not a violation. Define an accepted issuer/key baseline, an invalid or mismatched issuer control, and the observable prohibited effect. Missing control of a test issuer leaves that branch unresolved.

## Vulnerability verification

1. Complete a fresh normal flow for account A, observing final application identity and one authorized API response. Keep account B in a separate cookie jar. Preserve the exact initial transaction and avoid accidental automatic login.
2. Test redirect handling with one approved controlled callback variation, such as a changed path or a distinct controlled origin, while holding the client fixed. Observe actual authorization response destination; rejection, normalization and an error-page link differ from code/token delivery. Do not follow redirects to unapproved hosts.
3. Start fresh transactions for each code test. Change only the verifier or swap a code between the two owned sessions/clients as appropriate, with invalid-code and correct-session controls. A consumed/expired code failure is not evidence of correct binding. Check the resulting application identity, not just token endpoint status.
4. For OIDC, compare the token accepted by the relying party against the established issuer, client audience, signature/key and transaction nonce rules. Use a correctly signed wrong-context token where possible; isolate each rule. An access token accepted at its intended API is not an ID-token validation bypass.
5. Re-run a normal flow and classify the exact violated binding. A successful replay of a valid bearer access token may be intended; sender constraint, revocation or one-time policy must be established before calling replay a defect.

### Discovery and key-fetch branch

1. Capture a successful normal discovery/key-fetch sequence through the actual relying party. Record configured and returned issuer, TLS destinations, cache state and accepted keys. Discovery issuer consistency and ID-token issuer validation are distinct checks.
2. On the owned test issuer, vary one field or one redirect at a time. Compare issuer mismatch rejection separately from a policy-permitted cross-host destination and a controlled policy-excluded destination. Use only approved collectors; do not probe internal addresses or unrelated hosts. A collector request proves reachability, not arbitrary SSRF or token acceptance.
3. To test credential forwarding, use a disposable synthetic credential marker configured through an authorized test transport, or an equivalently isolated test identity. Inspect only the controlled receiving endpoint. Hold the original request constant while changing one redirect destination; compare an accepted destination and a destination excluded by the established credential-audience policy. Do not solicit real service credentials. If marker injection is unavailable, preserve the limitation rather than forwarding real tokens.
4. For key-source trust, use owned keys and synthetic identities. Compare a legitimate rotation within the trusted issuer with a key source or issuer context the actor is not allowed to select. Check the resulting relying-party identity and authorization, not merely a downloaded JWKS or cache entry. A key identifier alone is not a trust anchor, and identical key IDs across issuers do not establish equivalence.
5. Isolate cached from fresh behavior with disposable client configuration or supported cache controls. Do not flush shared caches. Repeat the normal flow after each variation; failed tests caused by stale keys, expiry or an unavailable issuer are inconclusive.

## Execution

1. Demonstrate the verified flow error with a harmless identity display or one owned protected resource, using only the two test accounts. Prove which account the browser acquired without touching other users or collecting real tokens through a third-party destination.
2. Revoke test grants/tokens where the owned flow supports it, remove temporary callbacks and test clients, and end both sessions. Verify the ordinary flow still works and controlled callback content is removed.

3. For a verified discovery violation, retain one synthetic outbound marker or harmless test-account effect. Restore test metadata, redirect handlers, client configuration and disposable cache state; revoke synthetic credentials and verify the normal issuer/key flow.

## Analysis

1. Separate authorization-server acceptance, code delivery, token redemption and relying-party login; a failure in one does not establish the others. Evaluate PKCE, nonce and state in the complete flow, not as isolated missing parameters.
2. Explain expected public-client behavior, valid multi-audience rules, code expiry/reuse, cross-site cookie policy and deliberate account switching. A weak-looking response type or absent state is context, not standalone proof of account takeover.

3. Public discovery and signing keys are expected; cross-host endpoints and legitimate key rotation can be valid. Distinguish a metadata fetch, a policy-excluded network request, leaked credentials and actual wrong-context token acceptance. Require evidence of the specific actor-controlled input and violated destination, credential-audience or issuer/key trust rule for the corresponding claim.

## Evidence requirements

Preserve full redirect chain and transaction timeline, browser-session labels, code/token provenance, verifier/nonce relationships, accepted and rejected comparisons and final identity/resource effects. For discovery branches retain issuer configuration provenance, metadata/key hashes, redirect and receiver records, cache context, isolated variations and restoration evidence. Keep secret values internal; public explanations use placeholders.

## Limitations

External observations cannot establish all client registrations or issuer trust settings. Unsupported grants, third-party providers and unsigned claim mutations remain untested rather than disproven. No token theft or broad provider assessment is authorized.

## Trigger examples and non-examples

Matching: An assigned OAuth/OIDC flow or relying-party discovery/key-fetch interaction has a relevant binding or trust question. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
