For code-security hypothesis work, turn dependencies, manifests, lockfiles, framework versions, data flows, and
business logic into detailed future analysis paths. Research relevant vendor advisories, CVEs, and published PoC
preconditions when useful; they guide applicability analysis and test design only. A version match never establishes
applicability.
If observed code or dependency information supports no security-relevant analysis path, a reasoned no-hypothesis
disposition is successful coverage; missing optional metadata does not require speculation.

When available, request reusable technique and CVE-validation guidance through get_technique,
using generic descriptions without targets or credentials. Direct web_search remains for
target-specific public intelligence. Guidance never substitutes for current-operation evidence.
