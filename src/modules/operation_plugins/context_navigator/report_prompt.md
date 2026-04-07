<module_report_configuration>
Module: Environment Discovery and Context Navigation
Focus: System context, network topology, data landscape, security posture
</module_report_configuration>

<domain_lens>
DOMAIN_LENS:
overview: Comprehensive environment discovery report documenting system roles, network infrastructure, sensitive data locations, and security controls. Focus on mapping the target landscape and identifying high-value follow-on targets
analysis: Analyze findings to identify the system's role and criticality within the organization. Prioritize by potential for lateral movement, credential access, and business impact. Map trust relationships and network dependencies
immediate: Identify high-value targets for follow-on operations and critical security gaps that enable immediate lateral movement or data access. Catalog sensitive data locations without direct access
short_term: Map the full network topology, identify all user and service accounts, and document the defensive control baseline (AV/EDR, logging, etc.)
long_term: Assess overall organizational security posture across system, network, and data layers. Provide strategic recommendations for hardening and monitoring
framework: Environmental Discovery Methodology, MITRE ATT&CK (Discovery/Lateral Movement), NIST Cybersecurity Framework
</domain_lens>

<audience_adaptation>
Discovery reports serve operational and strategic stakeholders:
- **Operation Leads**: Next-step priorities, high-value targets, lateral movement paths
- **Blue Teams**: Baseline visibility, identified misconfigurations, security software coverage
- **Security Architects**: Trust relationship maps, network segmentation gaps, dependency analysis
</audience_adaptation>
