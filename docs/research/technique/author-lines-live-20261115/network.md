# Policy-Based Connectivity Comparison with Identity Binding

## Purpose
Compare observed connectivity to an explicitly defined policy, bind each connection attempt to an identity, and require controlled evidence before labeling an outcome as legitimate or a boundary violation.

## Prerequisites
1. Documented policy baseline: authorized source identities, destination services, ports, protocols, and time windows.
2. Identity binding mechanism: mapping of network identifiers to identities via certificates, device identifiers, user accounts, or authenticated sessions.
3. Measurement capability: ability to capture connection attempts, rule decisions, and state creation without altering production traffic.
4. Controls for reproducibility: isolated test harness, versioned policy snapshots, and timestamped logs.

## Method

### 1. Establish Policy and Identity Baseline
1. Enumerate policy rules as allow/deny entries with identity attribute.
2. Record expected legitimate outcomes for each identity-service pair.
3. Define evidence requirements: rule match decision, identity proof, timestamp, and state entry metadata.

### 2. Prepare Comparison Controls
1. Baseline measurement: capture connectivity under known-good conditions with policy enforced.
2. Controlled variation: change one variable at a time, e.g., identity, source, destination, or time window.
3. Negative control: attempt a connection explicitly denied by policy with a valid identity.

### 3. Execute Comparison
1. For each test case, record: source identity, destination, protocol/port, policy rule matched, action taken, state entry created or not.
2. Bind identity to connection via authentication logs or certificate validation at the time of attempt.
3. Capture rule matching order evidence: which rule matched and whether later rules would have applied.

### 4. Evidence Requirements
1. Identity proof: authentication token, certificate serial, or session identifier with issuance time.
2. Policy snapshot: versioned policy text and effective timestamp.
3. Decision evidence: firewall or proxy log showing rule identifier, action, and match criteria.
4. State evidence: state table entry creation or absence with creation time and timeout.
5. Temporal correlation: synchronized timestamps across identity, policy, and network logs.

### 5. Interpretation
1. Legitimate: observed action matches policy for the bound identity and evidence is complete.
2. Boundary violation hypothesis: action contradicts policy for the bound identity with complete evidence.
3. Insufficient evidence: missing identity binding or timestamp correlation; classify as unknown and require future check.

## Limitations
Identity binding depends on external authentication systems; the method does not prove compromise, only policy conformance. Rule matching order semantics are implementation specific; verify with authoritative documentation for the target platform.

## Future Checks
1. Validate identity binding freshness and revocation status at test time.
2. Confirm log integrity and tamper resistance for evidence chain.
3. Re-run comparison after policy changes to ensure baseline remains valid.