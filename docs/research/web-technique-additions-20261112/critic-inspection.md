# Independent critic inspection

Reviewed the complete decoded ReviewSnapshot and requirements/frozen contract for each of the four draft hashes recorded in its review-input JSON. No searches or external fetches were performed by the critic. Used only files retained by the source selector.

Inspected primary-source excerpts:

- OpenAPI 3.1.1: root and Operation security fields, empty arrays, empty requirement objects, OR alternatives and multiple schemes. Consistent with draft's declared-security treatment.
- OWASP REST Security: local operation authorization, sensitive-resource protection and management endpoint protection. Provides background, not a documentation confidentiality policy.
- OWASP query-string exposure: history/log/Referer channels and HTTPS limitation. Broad Referer claims are explicitly qualified by draft.
- Referrer Policy editor draft: same/cross-origin distinctions and origin-only stripping of path/query. Draft correctly treats implementation and actual requests as separate evidence.
- RFC9700 HTML sections4.2/4.3 and exact text PKCE/mix-up passages: credential leakage, history, replay and transaction binding controls. Used as OAuth-specific context, not universal browser behavior.
- Stable SRI Recommendation: integrity hashes, resource verification and cross-origin/CORS conditions. Fixture comparison is not represented as target tamper capability.
- OWASP Third Party JavaScript: execution trust and sensitive-data risks, SRI/containment background.
- OWASP Vulnerable Dependency Management: contextual risk assessment. Identification process is accurately labeled original knowledge-allowed methodology.
- OIDC Discovery: issuer consistency and HTTPS JWKS location, with no universal same-host rule imposed.
- OIDC Core: issuer/audience validation, issuer-provided keys and asymmetric key rotation. Wrong-context key acceptance is distinguished from public key availability.
- Exact retained OAuth2 cheat sheet markdown: recommendations1–9, redirect/CSRF, PKCE/nonce, mix-up and transaction binding.

No blocking attribution issues or other material issues found. Each *.critic.json contains PublicationReview-shaped output for its matching unchanged snapshot. No extra semantic assessor or approval vote was used.
