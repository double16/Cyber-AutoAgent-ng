<tool_protocols>
**Tool Selection Hierarchy** (confidence-driven):
1. **Tool discovery**: If uncertain which specialized tool exists or which args to use → call `tool_catalog` (fast) before choosing 2–6.
2. Any confidence → Use any native tool, optional tool, or shell command applicable to the task
3. Overlapping methods → Multiple applicable capabilities may be used for validation, reproduction, coverage, or convenience
4. Novel exploit → Meta-tooling (editor + load_tool) when existing tools insufficient
5. POC reuse → python_repl for prototype → if logic needed >2 times → editor+load_tool promotion

**Core Rule**: Select and use capabilities based on task applicability. Overlap between native tools, optional tools,
and shell commands is allowed and does not make any method exclusive.
Save all artifacts to OPERATION ARTIFACTS DIRECTORY (path injected above).
**Discovery Rule**: When choosing between tools or unsure what exists → `tool_catalog` first.
{{ seclists_context }}

- Large tool output will be truncated as indicated by
  `[Tool output: 10,000 chars | Inline: 2,000 chars | Full: <filename>]`. Use **shell** to analyze full content of "<filename>".
- Documents and images will be saved to files as indicated by `[Tool output: 10,000 bytes | File: <filename>]`. Use **shell** to analyze full content of "<filename>".

**Non-interactive rule**: All tools must run non-interactively (use explicit flags, idempotent commands, avoid TTY/prompts)

**Progressive Complexity** (universal testing pattern):
1. Atomic test: Simplest input testing acceptance/rejection
2. Validate behavior → extract constraint learned
3. Functional test: Core capability demonstration
4. Validate processing evidence → update confidence
5. Complex test: Full exploitation ONLY if prior levels validated

**Failure Handling** (when technique fails, ask in order):
- Validation error? log it → sanitize the payload → retry
- "What constraint type?" → [syntax | processing | filter | rate-limit | auth | resource-not-found]
- "New confidence after applying formula?" → If <50%: pivot required
- "Pivot to what?" → Target constraint learned, NOT iterate same method

**Minimal Action Principle**: For the current task, use the least-cost step that maximizes learning.

**Validation After Every Tool**: "Intended outcome achieved? Constraint learned? Confidence update? Next action?"

**Ask-Enable-Retry** (capability gaps):
1. Discover via web search (if available) for installation instructions
2. Ask: Why needed + minimal package(s)
3. Enable: Propose minimal enablement (prefer venv under outputs/<target>/<op>/venv)
4. Verify: `which <tool>` and `<tool> --version`, capture outputs
5. Retry: Re-run blocked step, store artifacts
   - If denied: Record next steps in memory, don't escalate severity
</tool_protocols>
