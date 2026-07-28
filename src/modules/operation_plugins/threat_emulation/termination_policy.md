## Module completion criteria

Classify the phase against its own criteria and the following operation-level outcomes:

- Requested threat behavior is mapped to applicable MITRE ATT&CK techniques and authorized emulation steps.
- Every executed TTP has marker or IoC evidence, expected and actual behavior, and a documented detection opportunity.
- Blocked or inapplicable TTPs have evidence-backed control or environment explanations.
- Every created marker is removed with verification, or the operation is explicitly partial with residual artifacts and
  required cleanup work documented. Unverified cleanup cannot satisfy completion.
- No evidence indicates production credential use, sensitive-data collection or exfiltration, destructive behavior, or
  activity outside the authorized emulation scope.

Use `done` only when the phase criteria and applicable outcomes are evidence-backed. Use `partial_failure` when useful
emulation evidence exists but material execution, detection, or cleanup requirements remain. Use `blocked` only for a
concrete external authorization, access, defensive-control, target-availability, or capability dependency. Budget
consumption alone is never a completion requirement.
