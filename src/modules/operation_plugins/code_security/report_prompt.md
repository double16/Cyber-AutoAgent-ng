<module_report_configuration>
Module: Code Security and Static Analysis
Focus: Vulnerabilities, dependency risks, hardcoded secrets, supply chain security
</module_report_configuration>

<domain_lens>
DOMAIN_LENS:
overview: Comprehensive static analysis report documenting security vulnerabilities, dependency risks, hardcoded secrets, and remediation recommendations. Focus on CWE/SANS Top 25 and OWASP Top 10 for code
analysis: Analyze findings through the lens of exploitability from an attacker's perspective and business impact. Prioritize by severity (Critical/High/Medium/Low) and potential for data breach or system compromise. Identify high-risk files and supply chain dependencies
immediate: Rotate all exposed secrets (AWS keys, DB credentials) within 24 hours. Patch critical vulnerabilities (SQLi, RCE, Auth Bypass) and dependencies with known active exploits
short_term: Implement parameterized queries, add input validation frameworks, upgrade high-severity vulnerable dependencies, and establish a secrets management solution
long_term: Integrate SAST into CI/CD pipeline, conduct developer security training, establish secure coding standards, and implement regular dependency audits
framework: CWE/SANS Top 25, OWASP Top 10, NIST Cybersecurity Framework
</domain_lens>

<audience_adaptation>
Code security reports serve diverse development and security stakeholders:
- **Developers**: Specific code locations, vulnerable patterns, remediation examples
- **DevOps/SRE**: Dependency versions, CI/CD integration, supply chain risk
- **Security Teams**: Risk quantification, compliance gaps, remediation roadmaps
</audience_adaptation>
