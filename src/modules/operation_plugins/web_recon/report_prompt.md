<module_report_configuration>
Module: General Web Application Attack Surface Assessment
Focus: OWASP Top 10, authentication/authorization weaknesses, input validation, configuration posture, safe verification evidence

Assessment constraint: NO exploitation or weaponization performed; observations are based on non-destructive verification and observed security behavior
</module_report_configuration>

<domain_lens>
DOMAIN_LENS:
overview: Comprehensive web application security assessment identifying verified weaknesses across authentication, authorization, input handling, and exposure posture. Focus on OWASP Top 10 attack vectors with emphasis on evidence-backed, non-destructive verification
analysis: Analyze findings through OWASP Top 10 and trust-boundary enforcement. Prioritize by likelihood, scope, and business impact. Describe potential attack paths at a high level without providing weaponization detail
immediate: Address high-likelihood authorization failures and session integrity gaps within 48 hours. Add monitoring and alerts for suspicious access patterns. Patch configuration exposures with available mitigations
short_term: Improve security headers (CSP, HSTS, X-Frame-Options), strengthen logging and monitoring, implement rate limiting on sensitive endpoints, conduct focused code review for affected functions
long_term: Adopt secure SDLC practices, automate security testing in CI/CD (SAST/DAST/API tests), establish vulnerability management, implement centralized policy enforcement (ABAC/RBAC)
framework: OWASP Top 10 2021, NIST Cybersecurity Framework, CWE/SANS Top 25
</domain_lens>

<audience_adaptation>
General assessments serve diverse stakeholders:
- **Executives**: Risk quantification, business impact, strategic priorities
- **Technical Teams**: Specific vulnerabilities, reproduction steps, patches
- **Compliance**: Regulatory implications, audit findings, gap analysis
</audience_adaptation>
