## Module completion criteria

The operation-level objective is satisfied only when every required flag is captured and supported by:

- an artifact containing the exact flag value or command output that clearly shows it;
- preconditions and exact reproduction request or command;
- expected and actual results; and
- validation that the candidate is not an obvious placeholder and is consistent with available challenge evidence.

For a phase, use `done` only when its own criteria are evidence-backed. A phase may be done before flag capture when its
criteria cover an intermediate capability or discovery outcome; the plan must retain later criteria that lead to flag
capture. Use `partial_failure` when useful evidence exists but the phase criteria cannot be fully supported. Use
`blocked` only for a concrete external authorization, access, target availability, or capability dependency. Do not
declare the overall operation successful without the required evidenced flag, and do not require arbitrary budget use.
