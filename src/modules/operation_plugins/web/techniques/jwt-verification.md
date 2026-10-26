---
schema_version: 1
title: JWT trust-boundary vulnerability verification
purpose: Validate a JWT hypothesis using controlled requests and current-operation access evidence.
applicability:
- An assigned HTTP interaction contains a compact JWT or JWE candidate.
phases:
- vulnerability_verification
active: true
triggers:
- phases:
  - vulnerability_verification
  when: A JWT or JWE candidate appears in an assigned HTTP interaction, or a JWT trust-boundary hypothesis
    is assigned for finding validation.
  signals_any:
  - http.jwt
  request: JWT trust-boundary vulnerability verification
sources:
- url: https://www.rfc-editor.org/rfc/rfc7519.html
  title: 'RFC 7519: JSON Web Token'
  retrieved_at: '2026-10-26'
  selection_reason: Claim semantics and signed, encrypted, and unsecured token distinctions.
- url: https://www.rfc-editor.org/rfc/rfc8725.html
  title: 'RFC 8725: JWT Best Current Practices'
  retrieved_at: '2026-10-26'
  selection_reason: Algorithm, key, issuer, audience, and token-type trust boundaries.
- url: https://portswigger.net/web-security/jwt
  title: JWT attacks | Web Security Academy
  retrieved_at: '2026-10-26'
  selection_reason: Practical signature and attacker-controlled key-selection assessment cases.
- url: https://portswigger.net/web-security/jwt/algorithm-confusion
  title: Algorithm confusion attacks | Web Security Academy
  retrieved_at: '2026-10-26'
  selection_reason: Distinguishes key-format prerequisites from merely observing an asymmetric algorithm.
source_coverage: RFCs define trust requirements; PortSwigger supplies JWS assessment cases. This is not
  an exhaustive JWE, library-specific, or CVE verification guide.
---

# JWT trust-boundary vulnerability verification

## Prerequisites and applicability

Start with an assigned JWT hypothesis, authorized request access, a known protected operation, and
owned test identities or resources needed for the proposed comparison. Observation may trigger this
lookup before those prerequisites exist; record missing prerequisites and stop that branch.
Use local token processing and available equivalent tools. Keep tokens, private keys, and recovered
secrets in sensitive evidence; redact report and log values. No external token decoders are needed.
This procedure validates findings during vulnerability_verification, not hypothesis generation.

## Establish attribution and controls

1. Preserve the original request and response. Replay an unchanged valid token and verify the actual
   identity or resource access through a stable application result. Record timestamps and state.
2. In an isolated request/session, remove competing credentials, session cookies, automatic login,
   and token refresh. Retain only protocol necessities. Compare token absent, malformed token, and
   a valid lower-privilege or different owned identity where available. Record redirects and cache
   behavior. If access is public or alternate authentication explains it, JWT bypass is unproven.
3. Choose only the assigned branch below. Keep request context stable, change one trust boundary at
   a time, record generated-token provenance, and repeat the valid baseline after the variant.
   A baseline that fails or changes state makes the comparison inconclusive.

## Controlled verification branches

- **Integrity:** alter one security-relevant claim while retaining the original signature. Separately
  test `alg: none` with an empty signature segment and trailing dot when relevant.
- **Weak HMAC key:** only with symmetric signing and an authorized bounded offline candidate set,
  reproduce the original MAC locally. Then sign one controlled claim change. Stop at the agreed
  bound; failure to recover a key establishes no strength guarantee.
- **Untrusted key selection:** change one relevant claim and sign with an owned private key.
  Embed its public JWK in `jwk`, or publish that public key in a controlled JWKS referenced by
  `jku`; match `kid` values. Never embed or host private keys. Compare against an unrelated key.
  Investigate `kid` resolution
  only where supported; filesystem/database injection requires separately scoped guidance.
- **Algorithm confusion:** obtain the relevant public verification key, preserve candidate byte
  encodings including newlines, and sign a controlled HS256 token using those bytes as the HMAC
  key. Contrast with an unrelated key and the unchanged asymmetric token. An algorithm edit alone
  is insufficient; failure with one encoding does not exclude other verifier representations.
- **Claims and purpose:** compare legitimately signed tokens with the relevant wrong issuer,
  audience, or token purpose where obtainable. For expiry/not-before use known signed values and
  observed time beyond allowed skew, or an authorized test issuer. Changing a claim without a
  valid signature tests integrity, not independent claim enforcement. Missing required issuance
  capability makes that branch untested. Logout/revocation tests require an explicit policy.

For hosted key tests, use only approved callback infrastructure; a key fetch alone does not prove
forged-token acceptance. Do not turn this into unrestricted internal-network probing. Missing
headers do not prove that injected headers will be ignored or trusted. JWE/nested tokens require
appropriate decryption and validation knowledge; do not apply JWS mutation recipes blindly.

## Finding decision and evidence handoff

Confirm an authentication or authorization bypass only when a controlled invalid/untrusted token reproducibly violates an identified
trust policy and yields a demonstrated identity, privilege, or protected-resource outcome. Preserve
baseline, variant, and negative-control request/response references, exact local transformation,
identity/resource expectations, timing, and repeat result. Explain competing authentication and
cache controls. Describe only the boundary and impact actually demonstrated.

A status change, HTTP 200, echoed claim, key-fetch callback, local signature match, or library/version
presence alone cannot prove authorization bypass. A cryptographically weak key demonstrated offline
may support that narrower weakness; it does not by itself establish live access. Rejection is a
negative result for the tested variant, not proof every JWT boundary is secure. Unavailable identities,
keys, stable baselines, or policy evidence mean inconclusive, not confirmed or disproven.

Include source references as methodology, separate from current-operation evidence. Do not infer
ATT&CK behavior or a CVE solely from token format. Stop after sufficient minimal proof. Certificate,
content-type injection, full JWE attacks, and implementation-specific CVEs need their own prerequisites
and verification techniques. No code or payloads from sources are copied into this procedure.
