<finding_organization>
**Organize by Vulnerability Class**:
1. **Critical Vulnerabilities**: RCE, SQLi, Auth Bypass requiring immediate action
2. **Exposed Secrets**: AWS keys, database credentials, API tokens
3. **Dependency Risks**: Vulnerable or outdated packages with known CVEs
4. **Weak Cryptography**: MD5/SHA1 usage, insufficient salt, weak keys
5. **Path Traversal & Injection**: Filename/path handling issues
</finding_organization>

Organize only the narrative discussion. Do not create finding inventories, severity summaries, status tables, or
authoritative claims about code locations; Python renders canonical facts and evidence separately.
<observation_context>
Summarize only supplied records labeled `category: informational_observation` as informational context. Do not count,
assign severity, or promote observations to findings; Python owns those facts.
</observation_context>
