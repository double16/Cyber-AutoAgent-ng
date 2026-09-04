# User Guide

Cyber-AutoAgent is an autonomous security assessment tool with a React terminal interface and a Python command-line runner. Use either interface only
against systems you own or are explicitly authorized to assess.

## Prerequisites

| Requirement | Purpose |
|---|---|
| Node.js 22+ | React terminal interface |
| Python 3.12+ and `uv` | Python runner and development workflows |
| Docker | Containerized execution modes |
| Provider credentials or a running Ollama instance | Model access |
| Written authorization | Legal and operational scope |

**Legal Notice:** Only test systems you own or have explicit written permission to assess. Unauthorized testing is illegal. Users assume full responsibility for legal and ethical use.

## React terminal

```bash
cd src/modules/interfaces/react
npm install
npm run build
npm start
```

The first launch guides Docker setup, deployment mode, and provider configuration. The React CLI supports interactive
commands such as `/config`, `/module`, `/setup`, `/health`, `continue`, and `report`.

### React CLI options

The executable is `cyber-react` after the React package is built, or `npm start --` during development. Supported
options include:

| Option | Description |
|---|---|
| `--target, -t` | Target system or network |
| `--objective, -o` | Assessment objective |
| `--module, -m` | Module name; defaults to `web` |
| `--max-duration` | Duration budget in minutes |
| `--max-tokens` / `--max-cost` | Optional token and cost budgets |
| `--auto-run` | Start an assessment without the interactive UI |
| `--auto-approve` | Skip interactive tool confirmations |
| `--memory-mode` | `operation` (current operation only) or `shared` (same target across operations) |
| `--provider` / `--model` / `--region` | Model configuration |
| `--continue` / `--report` | Continue or regenerate the latest operation, optionally by ID |
| `--reset-failed` | With `--continue`, retry all partial-failure and blocked tasks and phases |
| `--deployment-mode` | `local-cli`, `single-container`, or `full-stack` |
| `--mcp-enabled` / `--mcp-conns` | Enable and configure MCP servers |
| `--headless` / `--recording` / `--debug, -d` | Output and diagnostic modes |

The React help text is the authoritative list for optional flags. Provider availability depends on the Python
configuration and installed credentials; current Python provider choices are `bedrock`, `ollama`, `litellm`, and
`gemini`.

## Python command line

Run the Python entry point with `uv`:

```bash
uv run python src/cyberautoagent.py \
  --target "https://example.com" \
  --objective "Authorized web security assessment" \
  --module web \
  --provider bedrock
```

The Python CLI requires `--target` and `--objective` for a new operation unless `--service-mode` is used. Its module
default is `web`; its provider choices are `bedrock`, `ollama`, `litellm`, and `gemini`.

### Python CLI options

| Option | Description |
|---|---|
| `--module` | Operation module; defaults to `web` |
| `--target` / `--objective` | Required inputs for a new operation |
| `--service-mode` | Run without a target/objective |
| `--max-duration` / `--max-tokens` / `--max-cost` | Operation budgets |
| `--provider` / `--model` / `--region` | Model configuration |
| `--confirmations` | Enable confirmation prompts |
| `--memory-path` / `--memory-mode` / `--keep-memory` | Memory configuration |
| `--output-dir` | Output directory override |
| `--continue` / `--report` | Continue or regenerate an operation |
| `--reset-failed` | With `--continue`, reset partial-failure and blocked work before retrying the operation |
| `--eval-rubric` | Enable evaluation with the selected rubric |
| `--mcp-enabled` / `--mcp-conns` | Enable and configure MCP servers |
| `--bug-bounty-header NAME=VALUE` | Add an authorized request header; repeatable |
| `--verbose` / `--heap-monitor` | Diagnostics |

The Python parser does not provide the React short aliases for these options.

### Retrying failed work

Normal continuation resumes only pending or active work. To retry the tasks and phases that ended in
`partial_failure` or `blocked`, add `--reset-failed`; durable artifacts, acceptance records, and recovery context
remain intact:

```bash
# Retry failed work in the latest operation for this target
uv run python src/cyberautoagent.py --target example.com --objective "via environment" --continue --reset-failed

# Retry failed work in one specific operation
uv run python src/cyberautoagent.py --target example.com --objective "via environment" \
  --continue OP_20260904_120000 --reset-failed
```

In the interactive React terminal, use `continue [operation_id] reset-failed`; the operation ID and
`reset-failed` argument may be supplied in either order.

## Deployment modes

The first-run setup wizard asks which environment you want to use. You can select a different environment later with
`/setup`. The wizard shows friendly names; the values in configuration files and command-line options are shown in
parentheses below.

| Setup choice | How it runs | Requirements | Choose it when |
|---|---|---|---|
| **Python / Local CLI** (`local-cli`) | Runs the agent directly in a local Python process. | Python 3.12+, `uv`, and direct access to your model provider. | You want the smallest setup, local tools, or a development environment. |
| **Single Container** (`single-container`) | Runs the core agent inside an isolated Docker container. | Docker Desktop or another compatible container runtime. | You want a self-contained assessment environment with the agent and its security tools isolated from the host. |
| **Full Stack** (`full-stack`) | Runs the agent and supporting services with Docker Compose. | Docker Compose and more disk, memory, and startup time than the other modes. | You want the complete platform with observability, evaluation, service networking, databases, caching, and storage. |

The Full Stack option may be shown as **Enterprise Stack** during setup. Observability and automatic evaluation are
enabled by default for the full stack; local Python and single-container modes use lighter defaults and do not start
the built-in supporting service stack. Provider credentials or a running local Ollama server are still required in
every mode.

To select a mode when starting the React terminal, use its configuration value:

```bash
cyber-react --deployment-mode local-cli
cyber-react --deployment-mode single-container
cyber-react --deployment-mode full-stack
```

See `docs/deployment.md` for configuration details and troubleshooting when Docker, Compose, or provider connections
are unavailable.

## Configuration

The React configuration editor stores settings in `~/.cyber-autoagent/config.json`. Environment variables and CLI
options are also supported. CLI values take precedence over saved configuration for the same setting.

Common provider configuration includes:

```bash
# Bedrock
export AWS_REGION=us-east-1

# Ollama
ollama serve
ollama pull qwen3.6:27b

# LiteLLM-compatible providers
export OPENAI_API_KEY=your_key

# Gemini
export GEMINI_API_KEY=your_key
```

See `docs/deployment.md` and `src/modules/config/README.md` for environment-variable details. Do not commit
credentials to configuration files.

## Operation modules

Bundled modules are `web`, `web_recon`, `ctf`, `threat_emulation`, `context_navigator`, and `code_security`. Module
selection is available in both interfaces. See [`operation_plugins.md`](operation_plugins.md) for module manifests,
prompt inheritance, and custom-tool development.

## Memory and outputs

`operation` memory mode limits retrieval to the current target and operation; `shared` reuses memories from prior
operations with the same exact target value. Reports and logs are written beneath the configured output directory,
normally `outputs/<target>/<operation-id>/`.

## MCP configuration

The React configuration editor and CLI options accept MCP connection data. The JSON value supplied to
`--mcp-conns` or `CYBER_MCP_CONNECTIONS` is an array of connection objects. Keep credentials in environment-backed
headers or command values rather than committing secrets.

## Docker management

From the repository root, use the repository compose file:

```bash
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs -f
docker compose -f docker/docker-compose.yml down
```

## Target preflight

Before a new assessment, the runner resolves executable targets and performs the applicable route, TCP, filesystem, or
resolver check. Each target produces a `PREFLIGHT PASS`, `PREFLIGHT FAIL`, or `PREFLIGHT SKIP` event. A failed preflight
stops the assessment before agents and tools start.

## Troubleshooting

| Problem | Check |
|---|---|
| React interface will not start | Confirm Node.js 22+, reinstall dependencies, and run `npm run build` |
| Docker execution fails | Run `docker info` and inspect the compose service logs |
| Ollama requests fail | Start Ollama and verify the configured model is installed |
| Bedrock requests fail | Verify AWS credentials and `AWS_REGION` |
| Configuration is invalid | Review `~/.cyber-autoagent/config.json` and the active provider settings |
| Assessment is rejected before starting | Review the target preflight event and authorization scope |
