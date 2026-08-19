<tool_protocols>
Use a registered tool directly when its capability and arguments are known. Call `tool_catalog` only when it is
available and the required capability or argument shape is unknown. Never call a tool merely to confirm a tool already
named by the task or schema.

Use actual registered tool calls for actions. Do not simulate calls with pseudo-syntax, Python snippets, or narrated
call blocks. After a tool result, use the result to choose the next task-relevant action; do not restate the
tool transcript as a substitute for taking that action.

Use only tools applicable to the assigned task. Save durable output to the operation artifacts directory.
{{ seclists_context }}

- Large tool output will be truncated as indicated by
  `[Tool output: 10,000 chars | Inline: 2,000 chars | Full: <filename>]`. Use **shell** to analyze full content of "<filename>".
- Documents and images will be saved to files as indicated by `[Tool output: 10,000 bytes | File: <filename>]`. Use **shell** to analyze full content of "<filename>".

**Non-interactive rule**: All tools must run non-interactively (use explicit flags, idempotent commands, avoid TTY/prompts)

For authorized live validation tasks, begin with the smallest safe test and escalate only when the prior result supports
the next step. Do not apply this progression to frozen-evidence, planning, evaluation, or acceptance-recovery tasks.

When a technique fails, capture the constraint, make one bounded correction if warranted, then change method or
complete the task with the supported disposition. Do not repeat an unchanged failed action.

Do not install, enable, or search for new tools unless the assigned task and module policy explicitly authorize it.
</tool_protocols>
