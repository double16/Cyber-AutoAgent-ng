# Broken Crystals web technique coverage

This crosswalk uses the vulnerability overview in the [Broken Crystals README](https://github.com/BrightSec/brokencrystals#vulnerabilities-overview)
as a regression checklist for the generic web technique library. It records coverage of the described
class and its relevant interfaces; it does not assert that a particular deployment remains vulnerable.
The README also includes dated or environment-specific exploit examples, so the target owner's current
instance and authorization govern any live assessment.

| ID | README behavior | Web technique documents |
| --- | --- | --- |
| jwt-authentication | JWT algorithm, signature, key selection (`kid`, `jku`, `jwk`, `x5u`, `x5c`), weak secret | `jwt-verification.md`; `path-traversal-file-inclusion.md`; `sql-injection.md`; `os-command-injection.md` |
| weak-login | Weak/default credentials and bounded login guessing | `default-and-weak-credentials.md`; `rate-limiting-sensitive-flow-abuse.md` |
| common-files | Publicly reachable configuration and common files | `sensitive-backup-configuration-files.md`; `excessive-data-exposure.md` |
| cookie-security | Session cookie Secure and HttpOnly attributes | `security-headers-and-cookie-attributes.md`; `session-lifecycle-logout-revocation.md` |
| csrf-cors | CSRF protection, CORS policy, Origin behavior, authenticated and anonymous forms | `csrf.md`; `cors.md` |
| xss-reflected | Reflected XSS in query and response-body contexts | `xss-dom-message-trust.md` |
| xss-stored | Stored XSS in user-submitted content | `xss-dom-message-trust.md`; `file-upload-processing.md` |
| xss-dom | DOM XSS through client-side response handling | `xss-dom-message-trust.md` |
| directory-listing | Public directory indexes | `sensitive-backup-configuration-files.md` |
| file-upload | Upload validation, storage, retrieval and active content | `file-upload-processing.md`; `html-css-injection.md`; `xxe.md` |
| path-disclosure | Internal paths disclosed by error responses | `error-information-disclosure.md` |
| security-headers | Browser security response headers | `security-headers-and-cookie-attributes.md`; `clickjacking.md`; `tls-mixed-content.md` |
| html-injection | HTML interpretation in form values | `html-css-injection.md`; `xss-dom-message-trust.md` |
| css-injection | CSS interpretation from URL or form input | `html-css-injection.md` |
| http-methods | Unexpected methods, upload/delete/read routes, and method mapping | `http-method-and-override-boundaries.md`; `file-upload-processing.md` |
| ldap-injection | LDAP filter manipulation and error disclosure | `ldap-injection.md`; `error-information-disclosure.md` |
| file-inclusion | Local/remote file inclusion through HTTP or gRPC | `path-traversal-file-inclusion.md`; `ssrf.md` |
| mass-assignment | Hidden privilege fields on create/update | `mass-assignment.md`; `function-level-authorization.md` |
| exposed-config-secrets | Public API configuration and secret values | `excessive-data-exposure.md`; `sensitive-backup-configuration-files.md` |
| command-injection | OS command execution in HTTP, GraphQL and gRPC interfaces | `os-command-injection.md`; `graphql.md` |
| ssti | Server-side template interpretation | `ssti.md` |
| ssrf | External, internal and cloud-metadata request destinations | `ssrf.md`; `path-traversal-file-inclusion.md` |
| sql-injection | SQL query and header-driven injection | `sql-injection.md`; `error-information-disclosure.md` |
| open-redirect | Unvalidated redirect destination | `open-redirects.md` |
| source-control-files | Public Git/SVN/Mercurial metadata | `sensitive-backup-configuration-files.md` |
| xxe | XML external entity processing, including uploaded SVG/XML | `xxe.md`; `file-upload-processing.md` |
| hidden-upload | Hidden upload path, stored filename markup and uploaded SVG | `file-upload-processing.md`; `html-css-injection.md`; `xss-dom-message-trust.md` |

Technique documents remain target independent. Alternate protocols or surfaces in the README, including
GraphQL and gRPC, are covered by the applicable generic interface and vulnerability techniques; protocol
support alone does not establish an exploitable condition.
