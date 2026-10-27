---
name: forensics
description: Structured guidance and playbooks for reviewing and investigating Cyber-AutoAgent operations. Triggers on "review operation", "investigate operation", "forensics", "what happened in the last run", "operation logs".
metadata:
  short-description: Operation forensics and log analysis
  compatibility: claude-code, codex-cli, pi-agent, opencode-agent
---

# Forensics Skill

Structured guidance and playbooks for reviewing and investigating Cyber-AutoAgent operations.

This skill provides a standardized approach to analyzing operation logs (`cyber_operations.log`), the
authoritative SQLite workflow database, and Langfuse traces to understand an agent's reasoning, coverage,
and reasons for termination.

**Triggers:** review operation, investigate operation, forensics, what happened in the last run, operation logs, log analysis

## What This Skill Provides

- Playbooks for locating and assessing operation logs.
- A read-only playbook for querying persisted operation data from SQLite.
- Methods for targeted log inspection using line-numbered searches.
- Guidelines for reconciling planned phases with actual execution.
- Integration with the `langfuse` skill for deep trace analysis.

---

## Playbooks

### Inspect technique use and feedback

Techniques are global guidance, not operation evidence. Inspect source documents in Qdrant and the
operation's `techniques/` debug directory. Produced copies are saved immediately; evaluated-use copies
include their independent sentiment and reason. Lifecycle and use provenance are available read-only:

```sql
SELECT u.operation_id, u.task_uid, u.revision_id, u.score, u.reason, u.use_context,
       r.lineage, r.origin, r.active, r.inactive_reason, r.score_count, r.score_total,
       r.pending_sync
FROM technique_uses AS u
JOIN technique_revisions AS r ON r.revision_id = u.revision_id
WHERE u.operation_id = :operation_id
ORDER BY u.created_at;
```

A NULL score is pending evaluation, not negative sentiment. An inactive current revision may advertise
triggers for regeneration, but cannot be returned as guidance. Superseded revisions preserve history.
`use_context` records the first delivery's generic purpose, phase contexts, applicability and observation
provenance. Legacy receipts contain `{}`: their context is unknown, not reconstructed from task prose.

### "Find the operation log"

Use these commands to locate the `cyber_operations.log` file.

**Scenario: Find the latest operation**
```bash
find outputs -name "cyber_operations.log" -print0 | xargs -0 ls -t | head -n 1
```
*Note: The output path `outputs/{target}/{operation_id}/cyber_operations.log` contains both the logical target and the operation ID.*

**Scenario: Find log by operation ID**
```bash
find outputs -path "*/{operation_id}/cyber_operations.log"
```

---

### "Initial assessment"

Establish the basic metadata and termination reason without reading the whole log.

**Step 1: Read the header (Metadata & Start)**
```bash
head -n 50 <log_path>
```
*Look for: `operation_id`, `start_time`, `budget_limits`.*

**Step 2: Read the tail (Termination & Summary)**
```bash
tail -n 100 <log_path>
```
*Look for: `termination_reason`, `assessment_complete`, `final_metrics`, `workflow_coverage_summary`.*

---

### "Process a continuation"

A continuation resumes an operation using data from a previous operation. The `cyber_operations.log` records
continuation session starts with lines such as:

```text
CYBER-AUTOAGENT SESSION STARTED: 2026-09-23 20:54:17
```

When the user asks to process a continuation:

1. Find every `CYBER-AUTOAGENT SESSION STARTED:` line in the operation log. Each marker starts a session; its
   log section runs up to the next marker or the end of the file.
2. By default, process the latest continuation session. Use the user's specified session if they identify one.
3. Compare multiple continuations only when the user requests a comparison. Cite the relevant timestamps and
   log line numbers when describing changes between sessions.

After code updates, users commonly continue an operation to reuse earlier data, such as the discovered attack
surface or authentication flows. That data may have been collected before the code changes, so do not treat it
as reflecting the updated code unless the continuation produced fresh evidence. When relevant, distinguish
inherited data from observations made during the selected continuation.

---

### "Targeted log inspection"

Locate key events and inspect their reasoning payloads using line numbers.

**Step 1: Search for key event line numbers**
```bash
rg -n -i "phase_transition|task_creation|budget_limit|workflow_coverage_summary|progress_update|assessment_complete" <log_path>
```

**Step 2: Inspect a specific event range**
Once you have a line number (e.g., 1250), read the context (e.g., 20 lines after):
```bash
sed -n '1250,1270p' <log_path>
```
*Preserve exact line numbers when reporting findings.*

### "Query the operation database"

Use the SQLite database for structured workflow state when it is available. This is an optional source: a
missing `sqlite3` executable, missing database, corrupted database, or failed query must not fail the forensic
request. Report the database as unavailable and continue with the operation log and Langfuse playbooks.

SQLite access has a required order: **check availability → run the integrity check → discover the schema → query data**.
The integrity check is a mandatory gate before any other database command. Run it as a separate, sequential command;
do not run it in parallel with `.tables`, `.schema`, or `SELECT`. Do not inspect the schema or query rows unless it
returns exactly `ok`.

The database is always at `outputs/cyber_autoagent.db`.

**Step 1: Check that the optional database tooling and file are available**

Run from the repository root:

```bash
if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "SKIP: sqlite3 is not installed"
elif [ ! -f outputs/cyber_autoagent.db ]; then
  echo "SKIP: outputs/cyber_autoagent.db does not exist"
else
  echo "SQLite database is available"
fi
```

Do not install packages or create a replacement database as part of forensic review.

**Step 2: Run the mandatory integrity check**

Run this before any schema discovery or data query, using a normal writable SQLite connection; do not add
`-readonly`. Opening the database this way lets SQLite perform any required crash recovery first, such as rolling
back a hot journal left by an interrupted transaction. The `PRAGMA integrity_check` statement then checks the
database's structural consistency and reports whether it is `ok`; the pragma itself does not repair arbitrary
corruption. Keep this command separate and sequential so recovery and the integrity result are established before
any schema discovery or data query.

```bash
if ! integrity=$(sqlite3 outputs/cyber_autoagent.db "PRAGMA integrity_check;"); then
  echo "SKIP: SQLite integrity check failed"
elif [ "$integrity" != "ok" ]; then
  echo "SKIP: SQLite database is corrupted: $integrity"
else
  echo "SQLite integrity check passed"
fi
```

If the command fails or returns anything other than exactly `ok`, stop database inspection. Do not retry with another
connection mode or issue schema/data queries. Report the database lookup failure and continue using other sources.

**Step 3: Discover the schema only after the integrity check passes**

```bash
sqlite3 -readonly \
  -cmd ".tables" \
  -cmd ".schema operations" \
  outputs/cyber_autoagent.db
```

The current workflow schema includes `operations`, `plans`, `tasks`, `task_acceptance_results`,
`operation_preflight_results`, `finding_records`, `objective_validation_records`,
`finding_evidence_receipts`, `operation_model_metrics`, `credential_records`,
`credential_target_aliases`, `credential_status_events`, `credential_usage_records`, `mfa_challenges`,
`credential_rotation_requests`, `authentication_flow_records`, and `credential_task_authorizations`. Schema discovery is
authoritative if a database has a different migration level.

**Step 4: Query structured operation data**

Use read-only queries with explicit operation scope. Replace the placeholders with SQL-quoted values:

```bash
sqlite3 -readonly -header -column outputs/cyber_autoagent.db \
  "SELECT logical_target, operation_id, created_at
     FROM operations
    ORDER BY created_at DESC
    LIMIT 20;"
```

```bash
sqlite3 -readonly -header -column outputs/cyber_autoagent.db \
  "SELECT p.logical_target, p.operation_id, p.objective, p.current_phase,
          p.total_phases, p.assessment_complete, p.created_at, p.updated_at
     FROM plans AS p
    WHERE p.logical_target = 'TARGET'
      AND p.operation_id = 'OPERATION_ID';

   SELECT phase, status, title, task_uid, kind, target_scope, status_reason,
          created_at, updated_at
     FROM tasks
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY phase, created_at, task_uid;"
```

For acceptance, evidence, findings, preflight, and model-usage details, query only the tables present in the
discovered schema:

```bash
sqlite3 -readonly -header -column outputs/cyber_autoagent.db \
  "SELECT task_uid, criterion_id, status, disposition, summary, evidence_refs, updated_at
     FROM task_acceptance_results
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY task_uid, criterion_id;

   SELECT finding_uid, fingerprint, verification_task_uid, resolution, created_at, updated_at
     FROM finding_records
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY created_at, finding_uid;

   SELECT candidate_uid, fingerprint, verification_task_uid, resolution, created_at, updated_at
     FROM objective_validation_records
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY created_at, candidate_uid;

   SELECT receipt_uid, source_task_uid, artifact_ref, marker, artifact_fingerprint, created_at
     FROM finding_evidence_receipts
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY created_at, receipt_uid;

   SELECT target_id, target, target_type, status, reason, resolved_addresses, recorded_at
     FROM operation_preflight_results
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY target_id;

   SELECT captured_at, provider, model, total_tokens, cost, inference_time_ms,
          model_calls, correction_loops, efficiency
     FROM operation_model_metrics
    WHERE logical_target = 'TARGET'
     AND operation_id = 'OPERATION_ID'
    ORDER BY captured_at, provider, model;"
```

For credential and authentication provenance, query metadata and lifecycle records only; never select the
`credential_records.payload` column:

```bash
sqlite3 -readonly -header -column outputs/cyber_autoagent.db \
  "SELECT credential_id, target, role, operation_id, credential_type, origin, management_policy,
          status, invalid_at, supersedes_credential_id, created_at, updated_at
     FROM credential_records
    WHERE logical_target = 'TARGET'
      AND (operation_id = 'OPERATION_ID' OR operation_id IS NULL)
    ORDER BY created_at, credential_id;

   SELECT event_id, credential_id, operation_id, status, actor, reason, evidence_refs, created_at
     FROM credential_status_events
    WHERE logical_target = 'TARGET'
      AND (operation_id = 'OPERATION_ID' OR operation_id IS NULL)
    ORDER BY created_at, event_id;

   SELECT usage_id, credential_id, task_uid, authentication_mode, outcome, evidence_refs, created_at
     FROM credential_usage_records
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY created_at, usage_id;

   -- authentication_worker grants worker access; reused_authenticated_context grants validated context access.
   SELECT task_uid, credential_id, authorization_kind, created_at
     FROM credential_task_authorizations
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY task_uid, created_at, credential_id;

   SELECT challenge_id, credential_id, task_uid, method, status, expires_at, metadata, created_at, updated_at
     FROM mfa_challenges
    WHERE logical_target = 'TARGET'
      AND operation_id = 'OPERATION_ID'
    ORDER BY created_at, challenge_id;

   SELECT request_id, credential_id, request_operation_id, maintenance_operation_id, status, reason,
          evidence_refs, claimed_task_uid, failure_reason, staged_credential_id, created_at, updated_at,
          completed_at
     FROM credential_rotation_requests
    WHERE logical_target = 'TARGET'
      AND (request_operation_id = 'OPERATION_ID' OR maintenance_operation_id = 'OPERATION_ID')
    ORDER BY created_at, request_id;

   SELECT flow_id, target, purpose, flow_kind, status, evidence_refs, discovered_operation_id,
          created_at, updated_at, last_validated_at
     FROM authentication_flow_records
    WHERE logical_target = 'TARGET'
      AND discovered_operation_id = 'OPERATION_ID'
    ORDER BY updated_at DESC, flow_id;"
```

Use `credential_target_aliases` when checking user-declared target aliases. Treat database rows as structured
workflow records, not as independent proof: readable or successful records establish availability, while
semantic support or contradiction still requires inspection of the cited artifacts and other evidence. In
particular, credential metadata establishes provenance and lifecycle state, not credential validity or finding
support.

Never use `.output`, `.dump` into a file, `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`, or other write-capable
SQLite commands during forensic review. If any query returns an error, record the failed database lookup and
continue with logs or Langfuse rather than failing the user's request.

---

### "Reconcile plan and health"

Analyze if the operation achieved its goals vs. just running out of budget.

1.  **Phase Coverage**: Compare `applicable_phase_count` in the log against the initial plan.
2.  **Exhaustiveness**: Check for `not_applicable` phases, omitted inventory items, or `partial_failure`. These are explicit results, not necessarily failures.
3.  **Health vs. Coverage**: Cross-check `workflow_coverage_summary` against final health. A high health score can still have skipped phases.
4.  **Logical vs. Resource Termination**: Compare elapsed duration with `maxDurationMinutes`. Check if `progressPercent` reached 100% due to budget utilization rather than phase completion.

---

### "Langfuse trace review"

For deep inspection of LLM reasoning and tool calls, use the `langfuse` skill.

**Operation ID as Session ID**: The `operation_id` found in the log path or header is used as the `session_id` in Langfuse.

```bash
# Example: Fetch all traces for this operation
codex mcp call langfuse get_session_details --session_id "{operation_id}"
```
*(Requires the `langfuse` skill to be installed and configured)*

---

## Rules for Investigation

- **Avoid Log Dumps**: Reasoning payloads are large. Always use `rg` and `sed` for targeted reading.
- **Budget Neutrality**: Do not recommend changing task fan-out due to budget constraints. Budget is a user-controlled parameter.
- **Tool Failures**: Broken or missing tools are handled by the operation; do not flag them as forensic issues.
- **Reporting Overhead**: Do not flag reporting or evaluation time as a budget issue.
- **No-Finish Operations**: An operation may continue even if health suggests it won't finish, as coverage is prioritized. Recommend budget increases in the report instead of complaining.


### Technique bundle investigations

Correlate lifecycle events by operation, task, activity and `component_id`; inspect `research_round`
for targeted follow-up and exhaustion (at most three batches per component). Bundle summaries are
operation-local; published and produced documents contain individual validated techniques. Partial
delivery does not establish complete request coverage.
Inspect `technique_uses.use_context` JSON for `component_purpose`, `requirements`, and `components`
when multiple purposes reuse a revision. Unresolved components have no receipts. Receipt keys remain
operation/task/revision; legacy empty context is unknown. Compare individual source attribution to
overall coverage. Check repair ownership: attribution to selection, missing support to research,
procedure or omitted requested content to authorship. Applicability disagreement is assessment
uncertainty. Judge contribution against the component purpose, not final task completion.
