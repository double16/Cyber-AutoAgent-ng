# Cyber-AutoAgent-ng Changelog

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
- fix broken tool calling (ollama, gemini) in report, validation_specialist agents
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

