# Cyber-AutoAgent-ng Changelog

### Features

- Persist secret-free, target-scoped authentication-flow descriptors discovered on demand, so later operations can
  reuse validated login or protected-resource setup metadata while inventory observations remain untrusted hints.
- Add typed, frozen authentication-flow descriptors to attack-surface inventory and pass only matching login,
  validation, and redirect-origin metadata to the controller authentication worker; browser username/password flows
  now use the shared browser, while mapped API forms retain direct opaque-context setup and scoped shell access.
- Add a controller-owned authentication worker and operation-memory authenticated HTTP contexts that retain validated
  session cookies or authorization tokens without exposing them to task executors, logs, artifacts, reports, or
  persistent workflow state; the worker supports mapped API forms, browser and MFA flows, OAuth2 client credentials,
  and configured API keys.
- Capture full-page browser screenshots as durable artifacts for visual workflow evidence, including self-registration outcomes.
- Preserve deterministic self-registration and authentication workflow metadata from SPA JavaScript routes, and
  require client-side API mapping tasks to run the scoped bundle-inventory tool before inventory synthesis.
- Move credential-management tools from universal workflow core tools to deterministic, task-scoped optional bundles,
  reducing prompt context for planning, evaluation, and unauthenticated work.
- Insert a controller-managed credential-provisioning phase for web assessments when completed inventory and
  authentication-workflow outputs identify an authorized self-registration flow without usable credentials.
- Let unconstrained snapshot-dependent phases use live tool descriptions and prior workstream context to choose
  route-scoped bundled or standalone task families without hard-coded tool or vulnerability metadata.
- Run web phase-2 unauthenticated baseline mapping directly in the controller from frozen inventory interactions,
  preserving durable evidence and route-scoped coverage while bypassing executor prompt construction.
- Allow snapshot-dependent task batches to fan out independent, workstream-scoped task families across the same
  frozen route groups, preserving separate coverage for goals such as XSS, LFI, and SSRF.
- Make web authentication coverage and hypothesis phases deterministically consume the latest completed inventory
  snapshot, rejecting task creation when that snapshot is unavailable.
- Make web-recon produce a controller-owned phase-1 inventory manifest and keep phases 2 through 5 within that frozen
  inventory scope.
- Add credential-aware web assessment phases for unauthenticated baselines, authenticated role/account coverage, and
  store-backed authorization comparisons; allow read-only, bounded IDOR comparisons with existing credentials in
  web-recon and strengthen the CTF access-context workflow.
- Add credential rotation lifecycle records that atomically claim, stage, complete, fail, or cancel operation-managed
  rotations, retaining audit evidence until the replacement succeeds.
- Allow a queued rotation to be explicitly claimed and launched as a standard, constrained maintenance operation from
  the credential manager.
- Bind each launched rotation to one controller-created maintenance task and prevent maintenance operations from
  creating unrelated task fan-out.
- Exercise registration reuse, store-backed multi-tenant IDOR, mailbox-snapshot email MFA, OAuth protected-resource
  use, and API-key authentication against the deterministic loopback authentication fixture.
- Add an explicit mailbox-snapshot step for email MFA so workflow retrieval can accept only IMAP messages that arrived
  after the target was asked to send a code.
- Document the credential-rotation state machine and the two-step email-MFA mailbox snapshot flow.
- Add a secret-safe React credential-management workflow with lifecycle history and queued operation-managed
  credential rotations.
- Add target-scoped OAuth2 client-credentials exchange and API-key request-material tools that keep transient tokens
  and secrets out of persistent evidence.
- Add credential-ID-backed IDOR specialist login replay for planned account, role, or tenant comparison pairs.
- Add deterministic authenticated-coverage planning for each resolved target, including safe role/account/tenant
  comparison contexts and explicit coverage gaps.
- Add a validated username/password form-material adapter for mapped authentication flows.
- Add a SQLite-backed credential store with typed login/API/OAuth credentials, lifecycle status history, exact
  resolved-target scoping, explicit aliases, agent checkout tools, password/TOTP generation, objective sanitization,
  authentication context for tasks/findings, interactive email-MFA handoff, operation-managed credential rotation,
  target-scoped authenticated-task binding, and masked credential-use reporting.
- Sanitize email-login and OAuth client secrets supplied in an operation objective before task planning or model use.
- Generate configured TOTP MFA codes from checked-out credential IDs while keeping the provisioning secret inside the
  credential store, and reserve direct provisioning-secret generation for standalone callers.
- Add optional AES-256-GCM encryption at rest for credential payloads, including fail-closed key validation and
  in-place migration of existing plaintext credential records.
- Support safe credential-store encryption-key rotation through temporary prior-key configuration and automatic
  authenticated re-encryption under the new primary key.
- Record completed email-MFA mailbox challenges against the checked-out target credential without retaining the
  one-time code, and report both the target and mailbox credentials used in that flow.
- Mark failed or abandoned interactive and mailbox MFA handoffs as blocked instead of leaving their challenges pending.
- Reject stale email-MFA messages using the IMAP server-assigned delivery timestamp, preventing old mailbox codes
  from satisfying a new authentication challenge.
- Distinguish MFA credential use from authenticated request use in report provenance without exposing credential
  payloads.
- Bind retained MFA challenge metadata to its initiating task for credential-flow provenance.

### Fixes

- Redact URL-embedded usernames and passwords independently while preserving the surrounding URL context, and avoid
  treating generic field labels such as `password` or `secret` as global runtime secrets in diagnostics and exports.
- Make artifact byte-page validation errors identify the invalid parameter, supplied value, and applicable limit.
- Preserve the secret-free browser storage-key metadata needed by browser authentication flows, so opaque contexts
  capture required bearer tokens alongside cookies and reject role-inaccessible validation routes before reuse.
- Version authentication and registration flow descriptors so stale stored flow contracts are ignored and rediscovered
  after future authentication-flow behavior changes.
- Repair unbalanced structured JSON responses by conservatively closing missing object and array delimiters while
  rejecting mismatched delimiters and unterminated strings.
- Reset cookies, client storage, and session headers before each browser-based credential authentication worker while
  retaining the process-wide browser service, preventing browser-form authentication from running after shutdown.
- Bind authentication-flow recorder target, purpose, origin scope, and flow kind in controller-created discovery
  tools, preventing free-form flow-kind calls from stalling authentication setup.
- Give authentication-flow discovery the same bounded, paged artifact reader used by task execution, allowing it to
  inspect large browser HTML artifacts without repeatedly navigating or accessing unrestricted files.
- Increase the default Stagehand browser-operation timeout to 240 seconds, sanitize terminal formatting from
  structured Stagehand responses, and retry a failed browser action once only when diagnostics prove it caused no
  browser-side effect. Browser observations now return labeled safe DOM metadata when semantic observation times out.
- Prevent failed authentication setup from reaching a task executor with a stale authenticated marker. Authentication
  setup is now persisted as secret-free controller state, uses a narrow login/MFA-only worker bundle, and records an
  explicit authenticated-coverage gap when validation fails. Self-registration flows now retain a same-target login
  redirect as an evidence-backed success signal for later reuse.
- Compare credential target URLs by parsed scheme, host, port, and path boundaries; register checked-out credential
  secrets with runtime redaction; and keep authenticated execution available for the valid credential subset when a
  multi-credential setup only partially succeeds.
- Make controller authentication setup read the SQLite credential store, so registered same-target credentials remain
  eligible across operation continuations and later operations; record a redacted pre-prompt setup outcome for every
  authentication decision.
- Supply the controller-owned authentication worker with frozen target, credential-ID, and origin context; skip it
  when credentials are unavailable, gate executor request access on validated opaque sessions, and reject local or
  unrelated direct browser navigation during authentication setup.
- Preserve non-secret `credential_id` metadata in diagnostic redaction while continuing to mask credential payloads.
- Replace the misleading four-request credential-registration cap with a one-credential task-result bound, so browser
  interactions and credential-storage corrections are not mistaken for request-budget exhaustion.
- Preserve non-secret `credential_type` metadata in diagnostic tool events and give self-registration tasks the
  canonical username/password credential contract, preventing repeated credential-storage corrections.
- Clarify self-registration execution: fill all visible required controls in the mapped form, reuse a synthetic
  profile during bounded validation repair, and retain only secret-free registration field metadata for later flows.
- Resolve authentication flows before credential checkout: a credential-free worker discovers one target-scoped flow
  when needed, then isolated workers authenticate one credential at a time. API-form context setup now resolves its
  stored login and validation URLs internally instead of requiring agents to repeat them.
- Retry Stagehand browser LLM requests with prompt-directed, locally normalized JSON when an LLM provider rejects
  structured output, caching that compatibility mode per model for later browser actions.

- Log secret-safe before-and-after form-control state and DOM-event diagnostics for browser actions, and validate
  Stagehand-compatible action-result contracts without class-identity coupling to prevent successful no-ops.
- Give credential-provisioning tasks a dedicated browser and client-state inspection scope while excluding finding,
  credential-lifecycle, and unrelated workflow directives from their prompts and executor tools.
- Keep evaluator and recovery instructions task-local in prompt memory, preventing a prior credential slot's
  temporary authorization guidance from contradicting a later provisioning task.
- Split credential provisioning into one bounded task per missing role-specific identity, isolating registration
  failures and retries from other credentials in the same flow.
- Authorize `generate_registration_email` in credential-provisioning task metadata so valid self-registration
  prompts no longer fall back with a misleading tool-authorization error.
- Avoid generic inventory-fan-out health predictions for controller-owned dynamic phases, preventing deterministic
  reset-phase replacements from appearing to have missing tasks.
- Run safe unauthenticated GET baselines for mapped SPA authentication routes in the controller, using their structured
  client-route URL while retaining executor fallback for malformed or out-of-scope workflows.
- Return byte-bounded artifact excerpts with explicit truncation and pagination instructions, preventing a normal
  oversized read from entering artifact-failure recovery before its required continuation page can be read.
- Trim console log messages and suppress whitespace-only entries to prevent blank timestamped log lines.
- Wait for and report rendered-page changes after authentication-form submissions, generate collision-resistant
  registration emails, and classify incomplete credential-role quotas as partial phase failures while carrying their
  coverage gaps into later work.
- Recreate reset controller-owned credential-provisioning tasks with preserved replacement lineage, preventing
  archived phase work from incorrectly terminating continuations during generic task creation.
- Preserve executable browser, credential, and coverage-gap observation tools during bounded self-registration
  recovery, so unavailable artifact evidence triggers a scoped retry instead of an artifact-only dead end.
- Route frozen unauthenticated-baseline groups without a safe, in-scope executable HTTP interaction to task executors
  instead of failing them immediately in the controller.
- Resolve credential-provisioning prerequisites from completed, contract-validated task workstreams when generated
  plan metadata contains display labels, ensuring authorized registration manifests remain available for provisioning.
- Preserve structured prerequisite providers through plan persistence, keep web snapshot metadata attached as
  credential provisioning shifts later phases, and treat failed or unsafe controller baseline requests as gaps.
- Materialize one controller-owned credential-provisioning phase from the web phase-task contract, place it after
  the latest structured inventory or authentication-workflow provider, and prevent model-authored duplicates.
- Give controller-managed credential-provisioning tasks the scoped interactive browser tools needed to complete
  modern self-registration flows, while excluding global browser-header mutation.
- Plan web credential provisioning after attack-surface mapping and before authenticated coverage, marking the phase
  `not_applicable` when structured self-registration is absent or role credential quotas are already satisfied.
- Preserve frozen authenticated identity and target scope for finding, objective, and finding-dependent validation
  tasks, with replay-only credential tools that reject undeclared credentials.
- Scope credential-tool catalogs and execution guidance in task prompt building, critique, revision, and execution to
  the frozen task metadata, preventing unauthorized access-control comparison tools from being suggested.
- Resolve active task target IDs to their canonical target values in credential tools while preserving task scope
  enforcement, and document the accepted target forms for agent callers.
- Follow controller-owned web baseline redirects within the assigned service and one observed external identity
  provider origin, while rejecting additional external redirect destinations.
- Normalize legacy document-scoped inventory target IDs before web inventory consolidation, convert compatible
  technology-inventory artifacts into canonical technology items, and prevent execution receipts from overriding
  inaccessible acceptance evidence.
- Run the React credential manager from the repository virtual environment or a Docker Python entrypoint, using the
  selected deployment mode and forwarding credential-store encryption keys to containers.
- Show credential scope, account and tenant labels, invalidation, lineage, status history, and queued rotation details
  in the credential manager without rendering secret payloads.
- Require exact secret material in recognized target-facing tool inputs for credential-use provenance, and require a
  successful use before authenticated findings can cite a credential.
- Redact bare generated password, TOTP, and email-MFA values at UI and trace-export boundaries.
- Restrict task-bound MFA challenge completion and blocking to the task that initiated the handoff.
- Require durable discovery, registration, or rotation evidence for operation-created credentials, and retain that
  evidence with their initial credential status events.
- Require durable, definitive authentication evidence before agents update credential validity, preventing generic
  request failures from incorrectly invalidating a credential.
- Record an initial, actor-attributed credential status event whenever a credential is stored, preserving complete
  lifecycle provenance for supplied, found, and registered credentials.
- Prefer reusable registered credentials for later operations while retaining an operation's own scoped credentials as
  the highest-priority selection candidates.
- Preserve unauthenticated-baseline, authenticated-comparison, self-registration, and IDOR credential rules in every
  controller-appended executor contract and deterministic workflow prompt fallback.
- Require store-backed IDOR specialist logins to use checked-out task-authentication credentials on the resolved
  target origin, preventing raw credential JSON from the agent workflow.
- Prevent workflow-agent IDOR calls from using legacy raw credential JSON; standalone and CLI compatibility remains.
- Require active, target-scoped credential checkout before an agent can request an MFA code or read a configured
  email-MFA mailbox, preventing unrelated tasks from accessing MFA factors.
- Reject credential imports and discovered credentials whose target is not an exact resolved operation target.
- Restrict credential metadata queries, IDOR comparison planning, status changes, and rotation to the active task's
  resolved target scope.
- Support `operation_scope: "current"` for credentials independently supplied to each operation.
- Render deterministic authentication context for every finding and redact unexpected credential identifiers from
  report output.

## v0.10.0

### Features

- Automatically discover intercepting proxies configured in HTTP_PROXY and HTTPS_PROXY and proxy assessment requests
  through it.
- Add a seven-day Qdrant semantic cache for successful web-search responses, shared across operations that use the
  same embedding model and conservatively reused only for high-confidence matches.
- Add `--reset-phases` continuation mode that selectively resets phases, archives prior tasks, and creates fresh task
  proposals from comma-separated phase IDs and ranges such as `3,5-`; existing finding-validation tasks are returned
  to pending so their bound independent verification resumes.
- Add `--reset-failed` continuation mode and interactive `continue ... reset-failed` support to retry partial-failure
  and blocked workflow tasks and phases while retaining their durable evidence.
- Add webcrack and shuji JavaScript reverse-engineering tools.
- Consolidate authoritative workflow state into `outputs/cyber_autoagent.db`.
- Replace semantic-memory backends with Qdrant 1.18, using `outputs/qdrant` by default or a configured service.
- Change memory modes to 'shared' and 'operation' for clarity.
- Format Markdown in the React `/docs` viewer and provide runtime repository links when local documentation is unavailable.
- Render report observations and execution-history/acceptance tables deterministically, reducing report model calls
  while preserving LLM synthesis for executive, finding, methodology, and next-step sections.
- Add a CTF-only artifact scanner that discovers braced and SHA-256/SHA-512-style flag candidates, creates opaque
  objective-validation references, and does not require the objective to repeat a flag format.
- Add deterministic report model execution metrics to Appendix A, including provider/model usage grouped by context
  window, input/output/cache tokens, cost, human-readable inference time, per-model efficiency, and total operation
  time; retain operation metadata, software version, and Git repository provenance in Footer.
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

- Block credential, payment-card, and high-confidence PII values before either web-search provider receives a query;
  expose both runtime providers through one stable `web_search` wrapper.
- Keep auto-generated finding-validation tasks focused on their assigned endpoint while retaining leaked credentials
  and external service URLs as response-data markers for deterministic evidence verification.
- Retain redacted structured shell inputs in controller tool outcomes so successful artifact-producing commands such
  as Katana can satisfy their task-local execution requirements after acceptance reconciliation.
- Source shell execution-receipt capabilities from the environment catalog while preserving its broader tool-selection
  capability taxonomy.
- Recognize capabilities and declared artifacts from compound Bash shell commands, so wrappers such as `cd` and
  `timeout` no longer hide task-local crawler execution evidence.
- Refine generated executor prompts to keep one controller-owned terminal acceptance protocol, require real registered
  tool calls, and preserve adaptive exploration while honoring controller recovery guidance.
- Preserve the wrapped Strands editor instructions in the relative-path editor wrapper so agents retain its
  command-specific calling guidance.
- Retain existing operation-local files named by successful tool inputs and outputs as canonical artifact evidence,
  so fresh recovery cycles receive durable Katana, browser, and other tool-produced artifacts.
- Classify missing typed task outputs separately from missing execution provenance, give the actor one generic
  output-only repair turn without an unavailable acceptance tool, and let the controller validate and replay the
  retained acceptance submission deterministically.
- Render a bounded active-phase objective for snapshot task creation and route-scoped acceptance criteria without
  mutating the stored operation plan, preventing controller-generated moving-scope rejections.
- Replay complete, repaired task-creator JSON submissions through the bound `create_tasks` tool when a model omits
  its required tool call.
- Resolve relative editor-tool paths against the process current directory before invoking the Strands editor.
- Preserve accepted artifact evidence in task-owned immutable paths, merge independent phase inventory snapshots with
  provenance before downstream fan-out, reject inventory manifests as proof for a single frozen inventory subject,
  and tolerate stale prompt-memory selectors while retaining valid context.
- Resolve procedure execution evidence deterministically from task-local tool outcomes and artifacts, removing the
  model-facing receipt call and replaying retained acceptance after one exact prerequisite repair when needed.
- Report incomplete `record_task_acceptance` submissions that are waiting on execution proof as tool errors while
  still retaining the pending acceptance payload for controller replay.
- Require controller execution receipts to match the frozen target or subject, preserve their artifacts in the
  immutable acceptance ledger, and contain rejected deterministic acceptance replays within the task workflow.
- Normalize crawler aliases in execution receipts and accept target-scoped, validated inventory manifests from
  built-in or MCP evidence producers without requiring a controller-known tool name.
- Reconcile retained acceptance submissions against completed same-cycle tool outcomes before scheduling an
  execution-evidence repair, preventing valid crawl and inventory evidence from being discarded as absent.
- Allow arbitrary URLs in shell commands while rejecting URLs that use a logical operation target ID as the hostname.
- Make merged phase inventories immutable and content-addressed while preserving complementary interaction metadata,
  stable item relationships, source provenance, and conflicting source values.
- Treat a shell command rejected for task-target scope as one bounded, in-session corrective failure with the concrete
  assigned target retained in the repair guidance.
- Format inline React TUI report previews as terminal-friendly Markdown.
- Emit explicit report, log, and artifacts paths so the React TUI populates the ARTIFACTS AND LOGS section reliably.
- Keep the React footer spinner task title within the live terminal width so long task names do not wrap off screen.
- Remove the obsolete unified-output toggle so all output uses the unified filesystem structure.
- Ground executive report narratives with explicitly labeled informational observations, resolve endpoint findings to
  registered targets, remove redundant observation metadata, and simplify task-history report tables.
- Include a unique reportable operational-tool list in methodology reports, including shell executable names while
  excluding bookkeeping and low-value shell utility names.
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
