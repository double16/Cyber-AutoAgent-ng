<finding_organization>
**Organize by Environmental Layer**:
- **System Layer**: Host identity, OS, applications, and services
- **User & Access Layer**: Current privileges, local/domain accounts, and session tokens
- **Network Layer**: Topology, interfaces, subnets, and trust relationships
- **Data Layer**: File system structure, sensitive data catalogs, and database services
- **Security Layer**: Status of AV/EDR, firewall rules, and logging configurations
</finding_organization>

Use these layers to frame narrative context only. Do not turn them into an authoritative inventory, claim complete
discovery, or state access, topology, or control status beyond the canonical evidence supplied to the agent. Python
renders coverage, artifacts, and status facts separately.
<observation_context>
Use the supplied `informational_observations` collection for narrative context about discovered environment facts.
Summarize it without counting, assigning severity, or treating observations as verified findings.
</observation_context>
