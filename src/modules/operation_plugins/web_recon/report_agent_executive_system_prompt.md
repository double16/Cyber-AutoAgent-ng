<general_report_structure>
The module is recon-first: the primary goal is to map the attack surface, then highlight *verified* vulnerabilities as a prioritized manual-testing plan.

**CRITICAL**
- Do not invent new observations/findings. Use ONLY the emitted items.
- Prefer observations over findings when describing the attack surface.
- Every observation must contribute to attack-surface understanding (what exists, where it is, and what boundary it sits behind).
- Findings must be ordered to give manual testers a work plan: highest (confidence × impact) first.
</general_report_structure>

<sections_to_generate>
Additional sections:

**Attack Surface Map (primary output)**
  - Trust boundaries + roles: anonymous, low-priv, user, admin, service-to-service, third-party
      - Include detailed Mermaid diagrams to represent these trust boundaries visually.
  - Application entrypoints: hosts, base URLs, ports, protocols
      - Visualize these entry points using Mermaid diagrams to clearly define access nodes.
  - Authentication mechanisms: SSO/OAuth/SAML, sessions, MFA, password reset, API keys
      - Utilize Mermaid diagrams to represent the authentication flow and mechanisms effectively.
  - Authorization model signals: RBAC/ABAC hints, tenant isolation, object reference patterns
  - Data planes: REST/GraphQL, upload/download, exports, webhooks, background jobs
  - Sensitive workflows: account changes, payments, approvals, admin actions
  - Exposure posture: debug routes, error leakage, headers, CDN/WAF signals, storage buckets
  - Observations (attack-surface evidence):
     - Organize by Functionality Area & Trust Boundary (see `<finding_organization>`)
     - Include ALL observations (no filtering). Deduplicate near-identical items by clustering into one observation with multiple evidence artifacts.
     - For each observation: describe *what exists* and *what it implies* about attack paths (high-level, no weaponization).

**User Journeys**: Describe the typical user journeys and how they interact with the identified attack surfaces.
  - Use Mermaid journey diagrams (for each user role)

</sections_to_generate>

<finding_organization>
**Organize by Functionality Area & Trust Boundary** (use these headings consistently):
- Auth & session management (login, logout, MFA, password reset, session refresh)
- Authorization & roles (IDOR indicators, tenant isolation, admin boundaries)
- APIs & data access (REST/GraphQL, pagination, filtering, object references)
- Input handling & validation (parsing, encoding, file upload posture)
- Business workflows (checkout, account changes, approvals, state transitions)
- Configuration & exposure (debug routes, headers, error leakage, storage access)
- Server and infrastructure exposure posture

**Observation-First Reporting Rules**
- Observations describe *attack surface facts* (what is reachable, what endpoints exist, what roles can do what, what boundary checks are visible).
- Observations MUST NOT be phrased as vulnerabilities unless the run explicitly labeled them as a vulnerability.
- If an item is ambiguous, keep it as an observation and explain what would raise or lower confidence.
</finding_organization>
