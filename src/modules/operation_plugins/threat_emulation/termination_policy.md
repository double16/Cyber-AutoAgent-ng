## Module completion criteria

## Recommended Minimum Phase Contract

Use these recommendations as the default phase decomposition. They are advisory guidance, not a mandatory phase count
or fixed plan schema. Adjacent recommendations may be merged only when the resulting phase explicitly preserves every
included capability, evidence requirement, and coverage outcome. Omit a recommendation only when it is demonstrably
inapplicable and document the reason. Cleanup remains an explicit capability and must not disappear inside a summary.

1. **Threat Mapping and Emulation Design** — Map requested behavior to applicable MITRE ATT&CK techniques, authorized
   identities and systems, safe markers, expected telemetry, and cleanup requirements.
2. **Marker-based TTP Execution** — Execute only authorized benign-marker TTPs and document blocked or partially
   observed techniques.
3. **Detection Validation** — Verify each marker and telemetry opportunity against expected detection behavior.
4. **Cleanup and Residual Verification** — Remove task-created markers, verify removal, and document any residual artifacts
   and required cleanup work before completion.

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
