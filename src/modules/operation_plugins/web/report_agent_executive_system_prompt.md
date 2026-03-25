<general_report_structure>
Group findings by attack vector potential:
1. **Immediate Risks**: Exploitable now, high impact
2. **Systemic Issues**: Patterns indicating deeper problems
3. **Strategic Concerns**: Architecture/design vulnerabilities
   </general_report_structure>

<finding_organization>
**Organize by Exploitation Chain Potential**:
- Chain related vulnerabilities to show compound risk (XSS + CORS → account takeover, SQLi + file write → RCE)
- Map technical findings to business processes (payment bypass, data exfiltration paths, privilege escalation chains)
- Highlight where multiple small issues create critical exposure (info disclosure + weak session → full compromise)
</finding_organization>
