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

**Step 2: Validate the database without modifying it**

NOTE: the integrity check requires write access, therefore `-readonly` is omitted.

```bash
if ! integrity=$(sqlite3 outputs/cyber_autoagent.db "PRAGMA integrity_check;"); then
  echo "SKIP: SQLite integrity check failed"
elif [ "$integrity" != "ok" ]; then
  echo "SKIP: SQLite database is corrupted: $integrity"
else
  echo "SQLite integrity check passed"
fi
```

If validation fails, do not retry with a writable connection. Continue the investigation using other sources.

**Step 3: Discover the schema before issuing table-specific queries**

```bash
sqlite3 -readonly \
  -cmd ".tables" \
  -cmd ".schema operations" \
  outputs/cyber_autoagent.db
```

The current workflow schema includes `operations`, `plans`, `tasks`, `task_acceptance_results`,
`operation_preflight_results`, `finding_records`, `finding_evidence_receipts`, and
`operation_model_metrics`. Schema discovery is authoritative if a database has a different migration level.

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

Use `finding_evidence_receipts` to locate artifact references and their source tasks. Treat database rows as
structured workflow records, not as independent proof: readable or successful records establish availability,
while semantic support or contradiction still requires inspection of the cited artifacts and other evidence.

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
