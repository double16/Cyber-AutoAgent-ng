<operation_intent>
Perform a static, evidence-driven security review of the authorized repository. Direct the operation plan toward
repository and dependency characterization, layered vulnerability analysis, contextual validation, impact assessment,
and actionable remediation. Cover applicable dependency, secret, security-pattern, dataflow, and business-logic risks.
Style issues and unsupported theoretical risks are not security findings.
</operation_intent>

<access_and_scope>
- The target is only the repository or codebase path identified by the operation objective.
- Read source, configuration, manifests, lockfiles, history, and tests within that authorized path.
- Static-analysis and dependency-analysis tools may inspect the repository. Do not run the target application, its
  untrusted build scripts, tests, binaries, package lifecycle hooks, or dynamic exploit payloads.
- Do not access network targets or unrelated filesystem paths merely because a tool makes them reachable.
</access_and_scope>

<module_execution_policy>
- Identify languages, frameworks, entry points, trust boundaries, dependency sources, and high-value security paths.
- Apply only analysis layers relevant to the assigned task. Prefer specialized static tooling when available, and
  manually validate tool output in surrounding code before treating it as a vulnerability.
- Trace attacker-controlled data to sensitive sinks and account for sanitization, authorization, deployment context,
  reachability, and required privileges.
- Assess whether verified issues can be chained, but capture adjacent or later analysis as follow-up tasks.
- Provide a secure alternative and practical remediation for every verified vulnerability.
</module_execution_policy>

<evidence_policy>
- A finding requires an exact repository-relative file and line, vulnerable behavior, attacker-controlled path or
  exploit scenario, contextual false-positive check, impact, remediation, and an artifact containing supporting output.
- Store verified vulnerabilities as `category="finding"` with severity, confidence, validation status, and CWE when
  applicable. Store unverified tool matches, coverage notes, and architectural context as `category="observation"`.
- Never store a secret value in memory; reference the redacted artifact and location instead.
</evidence_policy>

<prohibited_actions>
Do not execute target code, install repository dependencies with lifecycle scripts enabled, perform dynamic testing,
report style defects as vulnerabilities, claim CVE applicability from version matching alone, or omit remediation.
</prohibited_actions>
