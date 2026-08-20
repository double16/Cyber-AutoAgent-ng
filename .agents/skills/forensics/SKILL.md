---
name: forensics
version: 0.1.0
description: Structured guidance and playbooks for reviewing and investigating Cyber-AutoAgent operations. Triggers on "review operation", "investigate operation", "forensics", "what happened in the last run", "operation logs".
metadata:
  short-description: Operation forensics and log analysis
  compatibility: claude-code, codex-cli, pi-agent, opencode-agent
---

# Forensics Skill

Structured guidance and playbooks for reviewing and investigating Cyber-AutoAgent operations.

This skill provides a standardized approach to analyzing operation logs (`cyber_operations.log`) and Langfuse traces to understand an agent's reasoning, coverage, and reasons for termination.

**Triggers:** review operation, investigate operation, forensics, what happened in the last run, operation logs, log analysis

## What This Skill Provides

- Playbooks for locating and assessing operation logs.
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
