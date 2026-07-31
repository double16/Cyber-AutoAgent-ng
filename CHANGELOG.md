# Cyber-AutoAgent-ng Changelog

### Features

- Resolve SecLists once per operation and provide its verified root to wordlist-capable agents through the tools guide.
- Add a final ATT&CK enrichment pass that uses linked terminal task evidence, preserves first-pass CWE mappings,
  persists retryable results, and overlays the merged taxonomy into final and report-only output.
- Add a task-trace taxonomy annotator that catalog-validates and persists CWE and MITRE ATT&CK mappings after finding
  capture, with bundled fallback taxonomy data, optional cached refreshes, confidence labels, executive-summary
  coverage tables, and auditable catalog references.
- Refine generated report sections with a configurable actor/critic cycle, retain the latest actor revision with prose
  critic feedback when review remains unresolved, and add AI-generated-content disclaimers to final reports.
- Validate resolved hosts, IPs, explicit TCP services, CIDRs, and local filesystem targets before assessment startup,
  and emit a pass/fail/skip preflight event for every target.
- Show the latest operation health score, band, and entered target in the terminal title for interactive and headless TTY sessions.
- Add deterministic operation-health scoring to progress events, including inventory-based phase fan-out prediction,
  and show the compact score and band with a distinguishing stethoscope marker in interactive stream output, the
  persistent footer, and headless output.
- Quarantine unavailable or broken shell commands per operation.
- Present shell commands solely by capability and applicability, without ranking metadata.
- Detect exact repeating tool-call cycles, reuse matching completed results, and gracefully stop an agent that ignores
  the cached-result guidance.
- Add executable target registries and per-task target scopes so logical `--target` names can coexist with concrete
  URLs, hosts, CIDRs, and filesystem paths from the objective.
- Rename the React terminal `/plugins` command to `/modules`.
- Replace the persistent main orchestrator loop with a Python-owned multi-agent workflow that creates focused role agents for planning, task execution, and evaluation. Actor/critic refinement for improved quality.
- Add interactive React terminal `continue` and `report` commands for previous operations.
- Add readline-style editing shortcuts to the React terminal command entry.
- Move React thinking/spinner status into a persistent footer line above the existing metrics footer.
- Add recording-aware terminal mode with `--recording` override and automatic parent-process detection for `asciinema`.
- Maintain an `outputs/<target>/latest` pointer to the current operation directory.
- Show indexed progress for each final report agent call in both the React terminal UI and headless output.
- Replace iteration-based operation limits with duration, token, and cost budgets; progress reports the highest utilization across configured budgets.
- Correct token and cost metrics by aggregating usage across multiple agents and apply per-agent model pricing.
- Generalize React event handling for multi-agent workflows with per-agent handlers and operation-wide metric aggregation.
- Add persistent command history recall to the React terminal input, excluding slash commands.
- Add React footer ETA display after duration using progress percentage and formatted remaining time.

### Fixes

- Persist operation preflight resolution and route-check facts for taxonomy and later workflow policy checks, clarify
  configured taxonomy refresh URLs in finding reports, and require a globally routable resolved target before T1190
  can be recorded.
- Run verified-finding taxonomy annotation once at terminal workflow completion before final ATT&CK enrichment,
  use compact flattened TOON catalogs and complete mapping schemas, and feed rejected taxonomy responses back to the
  annotator for targeted correction without inheriting assessment or module prompts.
- Harden React terminal event framing against chunk-split and truncated JSON,
  isolate Python stderr from structured stdout events, and show an `output truncated`
  notice instead of attempting to parse discarded frames.
- Make incomplete-operation report budgets recommend continuing the existing operation for missing tasks, unless the
  report explicitly recommends a rerun as a new operation.
- Use the shared workflow activity formatter in the React stream display while preserving status colors.
- Normalize logged taxonomy response envelopes and add opt-in Ollama compatibility tests for taxonomy annotations.
- Seed high-signal CWE candidates for path traversal, SSRF, XXE, CSRF, IDOR/BOLA, SSTI, unsafe deserialization,
  file upload, open redirect, and common injection variants using industry terminology and aliases.
- Seed SQL-injection and XSS taxonomy candidates deterministically and require exact artifact references and confidence
  thresholds in annotator prompts.
- Use short task-scoped ordinal acceptance criterion IDs so detailed acceptance descriptions do not confuse models.
- Rank taxonomy candidates by high-signal technique aliases, retry schema and semantic annotation failures after
  finding validation, and report annotation failures separately from unsupported mappings.
- Replace compound module phase names with distinct, industry-aligned capabilities for hypothesis generation,
  vulnerability testing, exploit-chain analysis, finding validation, impact assessment, and coverage closure.
- Generate canonical next-step guidance for incomplete operations when the Appendix B model output is invalid.
- Prevent objective prose and remote exploit-path hints from being inferred as executable preflight targets.
- Retry transient `httpx.ReadTimeout` failures through the shared model rate-limit backoff policy.
- Clarify that concise plans should avoid redundant phases rather than minimize phase count, and add advisory module
  minimum phase contracts to guide complete phase decomposition without enforcing a fixed plan shape.
- Add module-specific advisory phase contracts for code security, context navigation, CTF, threat emulation, and web
  reconnaissance planning.
- Align CTF planning with inventory, hypothesis testing, and impact/flag-confirmation phases while preserving
  evidence for each vulnerability-chain link and branch.
- Harden model JSON parsing by preserving valid payloads, preventing malformed-response echoing during retries, and
  rejecting unrecoverable truncated responses instead of accepting ambiguous repairs.
- Restore canonical MITRE ATT&CK and CWE mapping headings in finding report prompts.
- Update taxonomy refresh to use MITRE CWE's current XML ZIP feed, parse XML records, and report the actual failed source URL.
- Use the operation output directory as the process working directory so relative files stay in its workspace.
- Mark final reports incomplete when workflow completion gating has not passed, without clamping progress status.
- Keep unverified security claims in a dedicated report section instead of silently downgrading them to observations.
- Stop active XBOW benchmark containers when the benchmark runner is interrupted with Ctrl-C.
- Show final report progress labels as the React terminal thinking task title while reporting.
- Skip public OSINT recon tools for non-public hostnames in `specialized_recon_orchestrator`.
- Bound React inline final-report file reads to a preview so very large reports cannot spike heap on operation completion or exit.
- Harden React terminal early-cancel and exit cleanup so stuck execution shutdown cannot leave the npm process hanging.
- Stop active Python or Docker assessment processes when headless auto-run receives SIGINT/SIGTERM/SIGHUP.

## v0.9.0

- Replace React UI model pricing with model.dev. (Fixes #55)
- Configure Ollama keep-alive for models to avoid extra start up time. Defaults to 30m.
- Add bug bounty header markers. (Fixes #63)
- Add idor_specialist tool. (Fixes #22)
- Publish tools image to `public.ecr.aws/bramblethorn/cyber-autoagent-ng/tools:latest`. (Fixes #20)
- Only pass temperature if the model supports it.
- Refactor output of the following tools to avoid agent misdirection. (Fixes #105)
  - mem0_list, mem0_retrieve, list_uncompleted_tasks, get_plan response, store_plan
- Improve rate limiting with back-off when HTTP responses 429 (rate limit) and 503 (service unavailable) occur. This feature is always enabled.
- Fix mem0_retrieve bug, missing 'cross_operation' reference.
- React UI requires Node.js 22.x or higher.
- Python dependency updates.

## v0.8.1

- Support thinking/reasoning for LiteLLM (#24)
- Activate a new task and return in `create_tasks` tool
- Report generation ensures a blank line before Markdown tables
- advanced_payload_coordinator.py: only do param discovery if no params are provided, limit scans to 5 params

## v0.8.0

Features:
- Task system (#26)
- System prompt optimization
- Rejection of early phase transition or termination (#89)
- Ollama context length set via `OLLAMA_CONTEXT_LENGTH` env var (models do not need to be extended)
- Option for continuing an operation
- Option for re-generate a report (#21)
- Improved reporting with more finding detail
- Add a methodology appendix to the report
- Modules may be nested in directories (#12)
- Add memory model config to React UI (#7)

Bug fixes:
- React UI memory leak fixes
- Workaround agent sending incorrect arguments for shell tool
- Reduce the default temperature of agents
- Limit reasoning content to three messages, prune to one when budget is tight


**NOTE:** Requires rebuilding the cyber-autoagent-tools image

## v0.7.0

Tool calling improvements

**NOTE:** Requires rebuilding the cyber-autoagent-tools image

- fix Dockerfile.tools build, tool check was not working, so several tools were not working
- Rewrite advanced_payload_coordinator.py using dalfox, sstimap and commix, optimize for model usage
- Refactor auth_chain_analyzer.py and specialized_recon_coordinator.py for correctness and optimize for model usage
- Improve tool guidance in system prompt
- Change tool_catalog to include all tool information and help text from shell commands
- Token usage estimation is closer to reality
- Apply reasoning loop workaround to all agents

## v0.6.0

- Module inheritance
- Externalized modules
- Sundry fixes

## v0.5.0

Improved context window management, important system prompt fixes for guidance, improved reporting.

- dependency updates
- add web_recon module for reconnaissance without exploitation
- make reporting work with only observations for non-exploitation use cases
- reporting uses all findings when MEMORY_ISOLATION=shared
- increase PROMPT_TELEMETRY_THRESHOLD to more reasonable value of 85% to allow for more input context
- fix sliding conversation manager to preserve first messages: initial user prompt was getting lost
- improve handling of failure cases
- patch OllamaModel usage reporting: input and output tokens are swapped
- apply CYBER_AGENT_OUTPUT_DIR everywhere instead of hardcoded “outputs” directory
- set context window message limit based on prompt token limit: 100 lines default, 200 lines for >= 128,000, 300 lines for >= 400,000
- use full paths with LLM content, some models prepend hallucinated filesystem roots
- add operation_paths information to system prompt to control LLM filesystem scope
- add reflection_snapshot information to system prompt (was already referenced by execution prompts)
- run execution prompt optimizer before system prompt rebuilding to load the optimized prompt in the same step
- improve agent continuation message with budget, check point and actions
- update bedrock models to global.anthropic.claude-opus-4-5-20251101-v1:0 / us.anthropic.claude-sonnet-4-5-20250929-v1:0

## v0.4.2

Prompt budget consider output tokens (#62)

## v0.4.1

- add back erroneously removed `python_repl` and `sleep` tools
- fix incorrect model parameters (i.e., max output tokens) when swarm model == main model
- validate swarm agent model and fall back to primary model
- fix broken tool calling (ollama, gemini) in report and specialist agents
- relax prompt optimizer validation for line count increase
- minor efficiency updates

## v0.4.0

Context size improvements
- Estimate tokens for system prompt and tools instead of using constants
- Rename 'general' module to 'web'
- swarm tool allows model selection using selected provider or ollama
- Allow modules to specify which built-in tools to use
- Refactor XBOW benchmark script to python

## v0.3.1

I'm not sure what happened here. 😆

## v0.3.0

Browser fixes, web search tools. (#42)

* Add browser instructions for element format. Fix some bad json output. (Fixes #37, #38)
* Add web search tools.

## v0.2.0

- model rate limiting
- add forward and reverse channels
- add out-of-band system testing
- fix evaluation bug that failed converting data to JSON
- improve XBOW benchmark script

## v0.1.5

- Dockerfile optimization
- Add tool `tool_catalog` to list all tools
- Browser tool fixes for concurrency and summarization
- Configure swarm agents with conversation manager and hooks

## v0.1.3

Release v0.1.3: React Terminal UI, Evaluation System, Architecture Refactor

Major release introducing React-based terminal interface, automated evaluation system, and comprehensive architecture refactoring.

Key Features:
- React Terminal UI with guided setup and real-time monitoring
- RAGAS evaluation system with 8 automated metrics
- Self-hosted Langfuse observability
- Prompt optimization system
- Modular architecture refactor (agents/, config/, handlers/)
- Centralized configuration management
- Enhanced memory system

## v0.1.1

Release v0.1.1

Significant architecture improvements with Strands framework integration, enhanced memory management, and local model support.

Key Changes:
- Local Model Support: Added Ollama integration for fully offline operation
- Strands Framework: Integrated swarm tools and migrated to mem0 memory system
- Stop Tool: Added explicit agent termination control with reason tracking
- System Prompts: Overhauled prompts based on failure mode analysis
- CI/CD & Docker: Added GitHub Actions workflows and optimized Docker support

## v0.1

First release of Cyber-AutoAgent
