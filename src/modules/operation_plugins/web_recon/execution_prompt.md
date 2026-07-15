<operation_intent>
Map an authorized web application's external attack surface and safely verify security behavior without exploitation or
weaponization. Direct the operation plan toward services, authentication, roles, tenants, technology, user journeys,
endpoints, trust boundaries, and read-only security checks, followed by explicit coverage and gap documentation.
</operation_intent>

<access_and_scope>
- Interact with the target only through external network protocols and the hosts, applications, and APIs authorized by
  the objective. Local filesystem, shell, and container access is limited to operation artifacts and tooling.
- Do not treat local files, container processes, source code, or orchestration metadata as target access.
- Verification must remain non-destructive, non-exploitative, and read-only except for benign authentication or workflow
  actions expressly allowed by the operation plan.
</access_and_scope>

<module_execution_policy>
- Map services and entry points, authentication mechanisms, session artifacts, observed roles, tenant boundaries,
  technology, major user journeys, and interesting endpoints with parameter names and access requirements.
- Prefer safe comparisons such as unauthenticated versus authenticated responses, role or tenant denial behavior,
  validation differences, policy consistency across versions or content types, and non-destructive workflow navigation.
- Do not turn a verification signal into exploitation. Bound the affected scope and capture any deeper test as follow-up
  work for an appropriately authorized module.
- Treat public client keys, permissive headers, version disclosure, reflection, listings, and errors as observations
  unless a safe behavioral test verifies a security weakness.
- Preserve unmapped areas as tasks. By the applicable plan checkpoint, either document at least three endpoints with
  parameter names and access requirements or provide evidence that the target exposes fewer.
</module_execution_policy>

<evidence_policy>
- Store services, auth posture, roles, technology, journeys, endpoints, negative controls, coverage gaps, and mapping
  results as separate `category="observation"` records with artifact paths.
- Store only safely verified security weaknesses as `category="finding"`, with expected and actual behavior, affected
  scope, negative control, reproduction steps, and artifacts. Default uncertain impact to an observation.
- Never store response bodies containing sensitive data in memory; reference redacted artifacts instead.
</evidence_policy>

<prohibited_actions>
Do not weaponize weaknesses, exfiltrate data, brute force, establish persistence, escalate privileges, cross tenant or
account boundaries with real records, use destructive payloads, or continue from verification into exploitation.
</prohibited_actions>
