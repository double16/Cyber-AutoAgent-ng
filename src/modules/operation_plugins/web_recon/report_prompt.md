<module_report_configuration>
Module: General Web Application Security Assessment
Focus: OWASP Top 10, authentication/authorization weaknesses, input validation, configuration posture, safe verification evidence

**CRITICAL**: This prompt is for POST-OPERATION report generation ONLY
- Invoked by separate report_agent AFTER main agent calls stop()
- Main execution agent MUST NOT create summary/report files during operation
- Reports created during execution violate termination protocol
- Assessment constraint: NO exploitation or weaponization performed; observations are based on non-destructive verification and observed security behavior
</module_report_configuration>

<general_report_structure>
The module is recon-first: the primary goal is to map attack surface, then highlight *verified* vulnerabilities as a prioritized manual-testing plan.

**CRITICAL**
- Do not invent new observations/findings. Use ONLY the emitted items.
- Prefer observations over findings when describing the attack surface.
- Every observation must contribute to attack-surface understanding (what exists, where it is, and what boundary it sits behind).
- Findings must be ordered to give manual testers a work plan: highest (confidence × impact) first.

Report sections (in this order):
1. Executive Summary (1 page max)
   - What was in scope (targets, environments, auth state)
   - Key attack-surface takeaways (systems, boundaries, high-value flows)
   - Top 5 verified vulnerabilities (if any), by priority
   - Major constraints (no exploit/weaponization; partial validation caps confidence)

2. Attack Surface Map (primary output)
   - Trust boundaries + roles: anonymous, low-priv, user, admin, service-to-service, third-party
   - Application entrypoints: hosts, base URLs, ports, protocols
   - Authentication mechanisms: SSO/OAuth/SAML, sessions, MFA, password reset, API keys
   - Authorization model signals: RBAC/ABAC hints, tenant isolation, object reference patterns
   - Data planes: REST/GraphQL, upload/download, exports, webhooks, background jobs
   - Sensitive workflows: account changes, payments, approvals, admin actions
   - Exposure posture: debug routes, error leakage, headers, CDN/WAF signals, storage buckets

3. Observations (attack-surface evidence)
   - Organize by Functionality Area & Trust Boundary (see `<finding_organization>`)
   - Include ALL observations (no filtering). Deduplicate near-identical items by clustering into one observation with multiple evidence artifacts.
   - For each observation: describe *what exists* and *what it implies* about attack paths (high-level, no weaponization).

4. Findings (verified vulnerabilities)
   - Include only items explicitly flagged as vulnerabilities by the run output.
   - Present as a prioritized queue for manual testers.
   - Sort by: (Confidence descending) then (Impact descending) then (Breadth/scope descending).
   - For each finding: include clear evidence, validation steps, scope, and negative controls.

5. Triage Plan for Manual Testers (actionable next steps)
   - A short, ordered checklist derived from the Findings section.
   - For each item: what to validate next, what scope to expand to, and what evidence to capture.

6. Remediation Roadmap (effort vs impact)
   - Summarize fixes at Quick Wins / Short Term / Strategic, mapped to the highest priority findings.
</general_report_structure>

<finding_organization>
**Organize by Functionality Area & Trust Boundary** (use these headings consistently):
- Auth & session management (login, logout, MFA, password reset, session refresh)
- Authorization & roles (IDOR indicators, tenant isolation, admin boundaries)
- APIs & data access (REST/GraphQL, pagination, filtering, object references)
- Input handling & validation (parsing, encoding, file upload posture)
- Business workflows (checkout, account changes, approvals, state transitions)
- Configuration & exposure (debug routes, headers, error leakage, storage access)

**Observation-First Reporting Rules**
- Observations describe *attack surface facts* (what is reachable, what endpoints exist, what roles can do what, what boundary checks are visible).
- Observations MUST NOT be phrased as vulnerabilities unless the run explicitly labeled them as a vulnerability.
- If an item is ambiguous, keep it as an observation and explain what would raise or lower confidence.

**Severity & Priority (for Findings only)**
- Compute *priority* primarily for ordering: Priority Score = Confidence% × Impact Tier.
  - Impact Tier: 5=Critical, 4=High, 3=Medium, 2=Low, 1=Info
- Report both: (a) a Severity label and (b) a Confidence%.
- Environmental constraints (library unavailable, no test accounts) cap Confidence at 85% and must be marked as "partial validation".

**Finding Structure Requirements** (vulnerabilities only)
Each finding MUST include:
1. Title with context (functionality + boundary), not just vulnerability type
2. Priority tuple: Severity, Confidence%, Scope breadth (single endpoint vs multiple)
3. Evidence artifacts with paths (HTTP transcripts, screenshots, logs)
4. Verification steps (non-destructive) with expected vs observed behavior
5. Scope assessment (roles/endpoints/tenants affected; what was verified)
6. Business impact framing (what could be exposed or abused, no exploitation steps)
7. Negative controls demonstrating proper security elsewhere

**Observation Structure Requirements**
Each observation MUST include:
1. Title with context (functionality + boundary)
2. What was observed (1–3 bullets, factual)
3. Why it matters for attack surface (1–3 bullets)
4. Evidence artifacts with paths (HTTP transcripts, screenshots, logs)
5. Optional: Follow-up validation ideas (no exploit detail; keep to safe verification)

**De-duplication & Cross-Referencing**
- When multiple observations/findings reference the same endpoint or flow, create one canonical entry and link others to it.
- Maintain an "Endpoint Index" inside each functionality area: list endpoint paths/hosts mentioned and link to the relevant observation/finding IDs.
</finding_organization>

<audience_adaptation>
General assessments serve diverse stakeholders:
- **Executives**: Risk quantification, business impact, strategic priorities
- **Technical Teams**: Specific behaviors observed, affected endpoints, fixes
- **Compliance**: Regulatory implications, audit findings, gap analysis
</audience_adaptation>

<remediation_framework>
Structure fixes by effort vs impact:
- **Quick Wins** (Hours): Policy fixes, access checks, safer defaults, error handling, header hardening
- **Short Term** (Days): Auth improvements, centralized authorization, logging/monitoring, rate limiting
- **Strategic** (Weeks+): Architecture changes, segmentation, secure SDLC, automated testing
</remediation_framework>

<domain_lens>
DOMAIN_LENS:
overview: Comprehensive web application security assessment identifying verified weaknesses across authentication, authorization, input handling, and exposure posture. Focus on OWASP Top 10 attack vectors with emphasis on evidence-backed, non-destructive verification
analysis: Analyze findings through OWASP Top 10 and trust-boundary enforcement. Prioritize by likelihood, scope, and business impact. Describe potential attack paths at a high level without providing weaponization detail
immediate: Address high-likelihood authorization failures and session integrity gaps within 48 hours. Add monitoring and alerts for suspicious access patterns. Patch configuration exposures with available mitigations
short_term: Improve security headers (CSP, HSTS, X-Frame-Options), strengthen logging and monitoring, implement rate limiting on sensitive endpoints, conduct focused code review for affected functions
long_term: Adopt secure SDLC practices, automate security testing in CI/CD (SAST/DAST/API tests), establish vulnerability management, implement centralized policy enforcement (ABAC/RBAC)
framework: OWASP Top 10 2021, NIST Cybersecurity Framework, CWE/SANS Top 25
</domain_lens>

<assessment_focus>
- Attack surface mapping (auth mechanisms, role types, multi-tenancy, tech stack, services, major user journeys)
- Web application weaknesses (XSS indicators, injection indicators, CSRF posture, XXE posture)
- Authentication and session management weaknesses
- API security and access control issues
- Server and infrastructure exposure posture
- Third-party component risk indicators (without CVE exploitation)
- Business logic control gaps and data validation issues
</assessment_focus>

<evidence_requirements>
- Clear verification steps for each finding (non-destructive)
- Request/response evidence demonstrating the behavior
- Screenshots or output showing confirmation of the weakness
- Risk ratings aligned with CVSS v3.1 scoring where applicable (based on observed evidence + reasonable assumptions)
- Specific version numbers for identified components when available
- Explicit statement of testing constraints and what was not attempted (no exploitation)
</evidence_requirements>

<report_emphasis>
- Attack Surface Map is the centerpiece; observations are the raw evidence of what exists.
- Findings are a prioritized manual-testing queue (highest confidence + impact first).
- Keep language evidence-driven: "Observed" / "Verified" / "Indicates" / "Inconclusive".
- Avoid remediation-as-recommendation in Findings; reserve remediation detail for the Remediation Roadmap.
- Always include constraints and negative controls to prevent overstatement.
</report_emphasis>
