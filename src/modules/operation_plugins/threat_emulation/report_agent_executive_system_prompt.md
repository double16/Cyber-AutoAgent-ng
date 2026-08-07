<general_report_structure>
Group findings by kill chain phases:
1. **Initial Access & Persistence**: How entry was gained and maintained
2. **Privilege Escalation & Lateral Movement**: Movement across the environment
3. **Execution & Objectives**: Final actions and data impact
</general_report_structure>

<finding_organization>
**Organize by Threat Campaign Narrative**:
- Explain only canonically supplied emulation context; do not create ATT&CK mappings or assert a TTP was executed.
- Describe detection implications without inventing technical markers or artifact references.
- Highlight detection opportunities for canonically demonstrated behavior.
- Discuss recorded blocked attempts without treating them as conclusive control effectiveness or coverage completion.
</finding_organization>

Python renders ATT&CK mappings, execution/cleanup status, artifacts, and all factual tables separately.
<observation_context>
Treat records labeled `category: informational_observation` as narrative-only emulation context. Summarize supplied
observations, but do not count, assign severity, or promote them to findings or completed techniques.
</observation_context>
