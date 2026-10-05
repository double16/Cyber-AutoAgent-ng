# Support Tools

The `support` directory contains sundry tools for diagnostic information.

## `cyberops-journal.sh`

**Requires**: Ollama install with a completion model, like llama3.2 or qwen3-coder.

Reads the most recent `cyber_operations.log` file in the current directory tree and outputs a human-readable "journal"
of what happened. Points out areas where the agent struggled.

## `cyberops-diagnose.sh`

**Requires**: Ollama install with a completion model, like llama3.2 or qwen3-coder.

Reads the most recent `cyber_operations.log` file in the current directory tree and outputs a human-readable
assessment of agent performance. The focus is on identifying areas of improvement.


## `repair-workflow-state.py`

Diagnoses damaged plan and task records in a structurally valid SQLite database. Empty optional
JSON fields are read as their existing empty defaults; malformed required data blocks continuation.
The tool never reconstructs a plan or acceptance contract from generated prose.

Stop all processes using the database before applying repairs. From the repository root:

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run python support/repair-workflow-state.py \
  --database outputs/cyber_autoagent.db --logical-target bc --operation-id OP_20260926_180128
```

Diagnosis prints record identifiers and damaged field names without exposing their contents.
Provide validated snapshots with `--plan-json path/to/plan.json` and/or
`--tasks-json path/to/tasks.json` to preview replacements. The plan file uses
`OperationPlan.to_dict()` format; the task file is an array of `Task.to_dict()` objects, including
the original task IDs and acceptance contracts. Use a known preserved copy with matching operation
provenance; the utility validates structure and bindings but cannot establish a snapshot's origin.
Snapshots must retain valid existing fields, phase/target bindings, and persisted acceptance criteria.

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run python support/repair-workflow-state.py \
  --database outputs/cyber_autoagent.db --logical-target bc --operation-id OP_20260926_180128 \
  --plan-json path/to/plan.json --tasks-json path/to/tasks.json
```

Add `--apply` to the same command to apply the reviewed replacements. The tool backs up the database
and any source sidecars, updates only damaged fields in the selected operation, validates the result,
and commits atomically. Failed repairs roll back; backups remain for inspection. Source sidecar
copies use `.source-wal`, `.source-shm`, or `.source-journal` suffixes so SQLite cannot replay them
against the standalone backup. Other unresolved records remain blocked and appear in diagnosis.

After repair, repeat diagnosis and resume using the normal continuation command. If SQLite's
structural integrity check fails, this utility refuses repair; preserve the database and use the
existing SQLite recovery procedure first.
