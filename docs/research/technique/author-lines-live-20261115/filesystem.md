# Reusable Read-Access Comparison

## Purpose
Compare observed read access for a file object against an established access policy by verifying process identity and using allowed and denied controls, with explicit evidence requirements.

## Prerequisites
1. Established policy artifact: documented owner, group, mode bits, ACLs, SELinux/AppArmor labels, and the intended read-allowed principal set for the object. Policy must be captured before testing.
2. Process identity baseline: UID, GID, supplementary groups, and process name/PID for the test actor. Identity is obtained from the running process, not assumed.
3. Test environment isolation: a dedicated test user or service account whose membership is known and does not change during the comparison.
4. Evidence collection capability: ability to record syscall results, file metadata, and process credentials with timestamps.

## Controls
- Allowed control: a principal explicitly listed in policy with read permission on the object. Expected outcome is successful read.
- Denied control: a principal explicitly excluded from policy or lacking read permission. Expected outcome is access denied.
- Process identity control: run the same read attempt from two different identities to confirm identity is the discriminating factor.

## Procedure
1. Establish policy
   - Retrieve current metadata: owner, group, mode, ACLs, security labels.
   - Map policy to expected read-allowed principals. Record the mapping as the baseline.
2. Capture process identity
   - Record UID, EUID, GID, EGID, supplementary groups, and command line for the test process.
   - Verify identity matches the principal under test.
3. Baseline metadata snapshot
   - Record inode, size, mode, ACLs, and timestamps before any access attempt.
4. Execute allowed control
   - Attempt read using the allowed principal identity.
   - Record return code, bytes read, and error string if any.
   - Expected legitimate behavior: read succeeds and data matches baseline.
5. Execute denied control
   - Attempt read using the denied principal identity.
   - Record return code and error.
   - Expected legitimate behavior: read fails with access not allowed. The open(2) semantics require that the caller have read permission on the object; otherwise the requested access to the file is not allowed.
6. Execute identity variation
   - Repeat read with a different principal while keeping object unchanged.
   - Confirm outcome changes with identity, not with object state.

## Evidence Requirements
- Policy artifact with version and timestamp.
- Metadata snapshot before and after tests.
- Process identity dump for each attempt.
- Syscall result log: return code, errno, bytes read, timestamp.
- Chain of custody for logs.

## Interpretation
- Legitimate: allowed control succeeds and denied control fails with permission error, and process identity matches policy mapping.
- Boundary violation indicator: denied control succeeds, or allowed control fails without policy change, or process identity does not match the principal claimed.
- Unknown: missing policy artifact or identity record; treat as future check.

## Limitations
Policy may change between snapshot and test. File system ACLs, capabilities, and mandatory access controls can override mode bits. This method describes comparison only; it does not prove vulnerability.

## Future Checks
- Re-validate policy after any configuration change.
- Confirm no privilege escalation mechanisms are active for the test process.
- Verify logging integrity for evidence retention.