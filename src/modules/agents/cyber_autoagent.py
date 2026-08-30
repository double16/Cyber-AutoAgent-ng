#!/usr/bin/env python3
"""Agent creation and management for Cyber-AutoAgent."""
import fnmatch
import importlib.util
import json
import logging
import os
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional

from strands import Agent
from strands.hooks import HookProvider
from strands.tools.executors import ConcurrentToolExecutor

# These tools are modules, not functions, the following imports MUST import the module
from strands_tools import (
    environment,
    http_request,
)

# These tools have the @tool decorator, the function is to be imported
from strands_tools.editor import editor as strands_editor
from strands_tools.load_tool import load_tool
from strands_tools.sleep import sleep
from strands_tools.tavily import tavily_search

from modules import __version__, prompts
from modules.agents.factory import (
    AgentFactoryConfig,
    create_agent_with_stateful_retry,
    init_agent_factory,
    model_uses_server_side_state,
)
from modules.agents.fail_fast_tool_executor import FailFastSequentialToolExecutor
from modules.agents.patches import ToolUseIdHook
from modules.config import (
    AgentConfig,
    configure_sdk_logging,
    get_config_manager,
)
from modules.config.models.capabilities import (
    allows_reasoning_content_replay,
    get_capabilities,
)
from modules.config.models.factory import (
    require_prompt_token_limit,
    create_strands_model,
)
from modules.config.system.environment import resolve_seclists_root
from modules.config.system.logger import get_logger
from modules.handlers.agent_repair_hook import AgentRepairHook
from modules.handlers.conversation_budget import (
    PRESERVE_FIRST_DEFAULT,
    PRESERVE_LAST_DEFAULT,
    LargeToolResultMapper,
    MappingConversationManager,
    PromptBudgetHook,
    _ensure_prompt_within_budget,
    register_conversation_manager,
)
from modules.handlers.react import AgentEventHandler
from modules.handlers.tool_recovery import EvaluatorArtifactReadLimitHook, TaskFailureRecoveryHook
from modules.handlers.terminal_tool import TerminalToolHook
from modules.handlers.tool_repeat_guard import (
    DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH,
    DEFAULT_TOOL_REPEAT_THRESHOLD,
    ToolRepeatGuardHook,
    normalize_tool_repeat_max_cycle_length,
    normalize_tool_repeat_threshold,
)
from modules.handlers.tool_router import ToolRouterHook
from modules.handlers.utils import (
    get_tool_name,
    print_status,
    sanitize_target_name,
    tool_append_description,
    tool_rename,
)
from modules.tools import python_repl
from modules.tools.advanced_payload_coordinator import advanced_payload_coordinator
from modules.tools.auth_chain_analyzer import auth_chain_analyzer
from modules.tools.artifact import create_artifact_reader, resolve_tool_result_max_chars
from modules.tools.artifact_references import ArtifactReferenceInputNormalizationHook
from modules.tools.client_bundle_inventory import client_bundle_inventory
from modules.tools.editor import create_absolute_path_editor
from modules.tools.idor_specialist import idor_specialist
from modules.tools.recon_inventory_manifest import recon_output_to_inventory_manifest
from modules.tools.browser import (
    browser_evaluate_js,
    browser_get_cookies,
    browser_get_page_html,
    browser_goto_url,
    browser_observe_page,
    browser_perform_action,
    browser_set_headers,
    initialize_browser,
)
from modules.tools.channels import (
    channel_close,
    channel_create_forward,
    channel_create_reverse,
    channel_poll,
    channel_send,
    channel_status,
)
from modules.tools.mcp import (
    discover_mcp_tools,
)
from modules.tools.memory import (
    create_tasks,
    get_memory_client,
    initialize_memory_system,
    memory_list,
    memory_retrieve,
    record_finding_validation,
    record_objective_validation,
    set_memory_event_emitter,
    store_finding,
    store_objective_candidate,
    store_knowledge,
    store_observation,
    resolve_operation_targets,
)
from modules.tools.oast import (
    oast_clear_http_responses,
    oast_endpoints,
    oast_health,
    oast_poll,
    oast_register_http_response,
)
from modules.tools.shell import shell
from modules.tools.specialized_recon_orchestrator import specialized_recon_orchestrator
from modules.tools.swarm import swarm
from modules.tools.tool_catalog import (
    get_shell_command_alternatives,
    remove_shell_command,
    tool_catalog_wrapper,
)
from modules.tools.web_search import web_search

warnings.filterwarnings("ignore", category=DeprecationWarning)

logger = get_logger("Agents.CyberAutoAgent")

_SECLISTS_CONSUMER_TOOLS = {"dirb", "feroxbuster", "ffuf", "gobuster", "hydra", "ncrack", "wfuzz", "wpscan"}
_FAIL_FAST_TOOL_ROLES = {"task_evaluator", "plan_critic", "task_prompt_critic"}

# Backward compatibility: expose get_system_prompt from modules.prompts for legacy imports/tests
get_system_prompt = prompts.get_system_prompt


@dataclass
class AgentRuntimeResources:
    """Shared operation resources reused by agents created within one work loop."""

    config: AgentConfig
    operation_id: str
    server_config: Any
    config_manager: Any
    callback_handler: AgentEventHandler
    tools_list: List[Any]
    tool_executor: ConcurrentToolExecutor
    system_prompt_payload: Any
    system_prompt: str
    hooks: List[HookProvider]
    conversation_manager: MappingConversationManager
    sdk_context_manager: Optional[str]
    trace_attributes: Dict[str, Any]
    prompt_token_limit: int
    core_tools_list: List[Any] = field(default_factory=list)
    optional_tools_list: List[Any] = field(default_factory=list)
    quarantined_shell_commands: set[str] = field(default_factory=set)
    termination_policy: str = ""


def _tool_names(tools: List[Any]) -> set[str]:
    return {get_tool_name(tool) for tool in tools}


def _create_tool_repeat_guard(config_manager: Any, agent_logger: logging.Logger) -> Optional[ToolRepeatGuardHook]:
    """Build the configured repeat guard, or return None when disabled."""

    repeat_threshold = normalize_tool_repeat_threshold(
        config_manager.getenv_int(
            "CYBER_TOOL_REPEAT_THRESHOLD",
            DEFAULT_TOOL_REPEAT_THRESHOLD,
        )
    )
    if repeat_threshold == 0:
        agent_logger.info("Repeated tool-call guard disabled")
        return None

    repeat_max_cycle_length = normalize_tool_repeat_max_cycle_length(
        config_manager.getenv_int(
            "CYBER_TOOL_REPEAT_MAX_CYCLE_LENGTH",
            DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH,
        )
    )
    agent_logger.info(
        "Repeated tool-call guard threshold: %d; maximum cycle length: %d",
        repeat_threshold,
        repeat_max_cycle_length,
    )
    return ToolRepeatGuardHook(repeat_threshold, repeat_max_cycle_length)


def build_role_tools(
    runtime: AgentRuntimeResources,
    *,
    selected_optional_tool_names: Optional[List[str]] = None,
    include_create_tasks: bool = False,
) -> List[Any]:
    """Build a restricted tool list for a short-lived workflow agent."""

    selected_optional_tool_names = selected_optional_tool_names or []
    selected_optional_names = set(selected_optional_tool_names)
    tools = []
    core_tools = runtime.core_tools_list or runtime.tools_list
    for tool_item in core_tools:
        tool_name = get_tool_name(tool_item)
        if tool_name == "create_tasks" and not include_create_tasks:
            continue
        tools.append(tool_item)

    for tool_item in runtime.optional_tools_list:
        if get_tool_name(tool_item) in selected_optional_names:
            tools.append(tool_item)

    return tools


def create_agent_runtime_resources(
    target: str,
    objective: str,
    config: Optional[AgentConfig] = None,
) -> AgentRuntimeResources:
    """Initialize shared operation resources used by one or more agents."""

    # Enable comprehensive SDK logging for debugging
    configure_sdk_logging(enable_debug=True)

    # Use provided config or create default
    if config is None:
        config = AgentConfig(target=target, objective=objective)
    else:
        config.target = target
        config.objective = objective

    agent_logger = logging.getLogger("CyberAutoAgent")
    agent_logger.debug(
        "Creating agent for target: %s, objective: %s, provider: %s",
        config.target,
        config.objective,
        config.provider,
    )

    # Get configuration from ConfigManager
    config_manager = get_config_manager()
    config_manager.validate_requirements(config.provider)

    # Prepare overrides if user specified a model
    overrides = {}
    if config.model_id:
        # Override both LLM and memory LLM with the user-specified model
        overrides["model_id"] = config.model_id

    server_config = config_manager.get_server_config(config.provider, **overrides)

    # Get centralized region configuration
    if config.region_name is None:
        config.region_name = config_manager.get_default_region()

    # Use provided model_id or default
    if config.model_id is None:
        config.model_id = server_config.llm.model_id

    # Use provided operation_id or generate new one
    if not config.op_id:
        operation_id = f"OP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    else:
        operation_id = config.op_id

    # Keep environment and memory context aligned before any continuation reads occur.
    os.environ["CYBER_OPERATION_ID"] = operation_id

    # Configure memory system using centralized configuration
    operation_targets = resolve_operation_targets(config.target, config.objective)
    memory_config = config_manager.get_qdrant_memory_config(config.provider)
    memory_config["target_values"] = [target.value for target in operation_targets]
    memory_config["memory_mode"] = config.memory_mode
    has_existing_memories = False

    # Initialize memory system
    target_name = sanitize_target_name(config.target)

    # Ensure unified output directories (root + artifacts + tools) exist before any tools run
    paths: dict[str, str] = {}
    try:
        paths = config_manager.ensure_operation_output_dirs(
            config.provider, target_name, operation_id, module=config.module
        )
        print_status(
            f"Output directories ready: {paths.get('artifacts', '')}", "SUCCESS"
        )
    except Exception:
        # Non-fatal: proceed even if directory creation logs an error
        logger.debug("Failed to pre-create operation directories", exc_info=True)

    try:
        if paths:
            root_path = paths.get("root")
            artifacts_path = paths.get("artifacts")
            tools_path = paths.get("tools")
            if isinstance(root_path, str) and root_path:
                os.environ["CYBER_OPERATION_ROOT"] = root_path
            if isinstance(artifacts_path, str) and artifacts_path:
                os.environ["CYBER_ARTIFACTS_DIR"] = artifacts_path
            if isinstance(tools_path, str) and tools_path:
                os.environ["CYBER_TOOLS_DIR"] = tools_path
            if operation_id:
                os.environ["CYBER_OPERATION_ID"] = operation_id
            if target_name:
                os.environ["CYBER_TARGET_NAME"] = target_name

        # Fix python_repl race condition by disabling PTY mode
        os.environ["PYTHON_REPL_INTERACTIVE"] = "false"
    except Exception:
        logger.debug("Unable to set overlay environment context", exc_info=True)

    # Create agent with telemetry for token tracking
    prompt_token_limit = require_prompt_token_limit(
        config.provider, config.model_id
    )
    logger.info("Prompt token limit (input tokens): %d", prompt_token_limit)

    # Allow configurable truncation and externalization of large tool outputs via env var
    max_result_chars = resolve_tool_result_max_chars(
        prompt_token_limit,
        os.getenv("CYBER_TOOL_MAX_RESULT_CHARS"),
    )

    if max_result_chars < 4000:
        computed_artifact_threshold = max_result_chars
    else:
        computed_artifact_threshold = max(ceil(max_result_chars / 3), 2000)
    try:
        artifact_threshold = int(
            os.getenv("CYBER_TOOL_RESULT_ARTIFACT_THRESHOLD", str(computed_artifact_threshold))
        )
    except Exception:
        artifact_threshold = computed_artifact_threshold

    if artifact_threshold > max_result_chars:
        logger.warning("Artifact threshold %d > max tool result chars %d, tool result with size in [%d, %d] will be lost",
                       artifact_threshold, max_result_chars, max_result_chars, artifact_threshold)
    else:
        logger.info("Artifact threshold %d, max tool result chars %d", artifact_threshold, max_result_chars)

    # we're setting these globals because at the moment, these variables are used by other code
    global TOOL_COMPRESS_THRESHOLD, TOOL_COMPRESS_TRUNCATE
    # never compress output less than artifact_threshold, or we'll lose information
    TOOL_COMPRESS_THRESHOLD = min(10000, max(ceil(artifact_threshold*1.1), ceil(max_result_chars*0.5)))
    TOOL_COMPRESS_TRUNCATE = min(8000, max(artifact_threshold, ceil(max_result_chars*0.4)))

    initialize_browser(
        provider=config.provider,
        model=config.model_id,
        artifacts_dir=os.getenv("CYBER_ARTIFACTS_DIR"),
    )
    initialize_memory_system(
        {**memory_config, "prompt_token_limit": prompt_token_limit},
        operation_id,
        target_name,
        has_existing_memories,
        logical_target=config.target,
    )
    print_status(f"Memory system initialized for operation: {operation_id}", "SUCCESS")

    memory_client = get_memory_client(silent=True)

    # Get memory overview for system prompt enhancement and UI display
    try:
        memory_overview = memory_client.get_memory_overview()
        has_existing_memories = bool(memory_overview.get("has_memories"))
    except Exception as e:
        memory_overview = None
        agent_logger.debug("Could not get memory overview for system prompt: %s", str(e))

    tool_count = 0

    # Load module-specific tools and prepare for injection
    loaded_module_tools = []

    try:
        module_loader = prompts.get_module_loader()
        module_tool_paths, module_tool_allowlist = module_loader.discover_module_tools(config.module)

        if module_tool_paths:
            # Dynamically load each tool module
            for tool_path in module_tool_paths:
                try:
                    # Load the module
                    module_name = f"operation_plugin_tool_{Path(tool_path).stem}"
                    spec = importlib.util.spec_from_file_location(
                        module_name, tool_path
                    )
                    if spec and spec.loader:
                        tool_module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = tool_module
                        spec.loader.exec_module(tool_module)

                        # Find all @tool decorated functions
                        for attr_name in dir(tool_module):
                            attr = getattr(tool_module, attr_name)
                            if callable(attr) and hasattr(attr, "__wrapped__"):
                                # Check if this is a @tool decorated function
                                loaded_module_tools.append(attr)
                                agent_logger.debug("Found module tool: %s", attr_name)

                except Exception as e:
                    agent_logger.warning(
                        "Failed to load tool from %s: %s", tool_path, e
                    )

            tool_names = (
                [tool.__name__ for tool in loaded_module_tools]
                if loaded_module_tools
                else []
            )

            if tool_names:
                print_status(
                    f"Loaded {len(tool_names)} module-specific tools for '{config.module}': {', '.join(tool_names)}",
                    "SUCCESS",
                )
            else:
                # Fallback to just showing discovered tools
                tool_names = [Path(tool_path).stem for tool_path in module_tool_paths]
                print_status(
                    f"Discovered {len(module_tool_paths)} module-specific tools for '{config.module}' (will need load_tool)",
                    "INFO",
                )
            # Log module and tool discovery explicitly for validation
            try:
                agent_logger.info(
                    "CYBERAUTOAGENT: module='%s', tools_discovered=%d, tools='%s'",
                    config.module,
                    len(tool_names),
                    ", ".join(tool_names),
                )
            except Exception:
                pass

            # Create specific tool examples for system prompt
            tool_examples = []
            if loaded_module_tools:
                # Tools are pre-loaded
                for tool_name in tool_names:
                    tool_examples.append(
                        f"{tool_name}(...)"
                    )
            else:
                # Fallback to load_tool instructions using discovered absolute paths
                # This works in both local CLI and Docker since module_tool_paths are resolved in the current runtime
                for tool_path in module_tool_paths:
                    try:
                        abs_path = str(Path(tool_path).resolve())
                        tool_name = Path(tool_path).stem
                        tool_examples.append(
                            f'load_tool(path="{abs_path}", name="{tool_name}")'
                        )
                    except Exception:
                        # As a last resort, include a name-only hint
                        tool_name = Path(tool_path).stem
                        tool_examples.append(
                            f"# load_tool path resolution failed for {tool_name}"
                        )

            tool_count += len(tool_names)
        else:
            print_status(
                f"No module-specific tools found for '{config.module}'", "INFO"
            )
    except Exception as e:
        logger.warning("Error discovering module tools for '%s': %s", config.module, e)

    # Load MCP tools and prepare for injection
    mcp_tools = discover_mcp_tools(config)

    # Build additional environment context
    full_tools_context = ""
    available_tool_names = set(config.available_tools or [])
    seclists_root = (
        resolve_seclists_root() if available_tool_names.intersection(_SECLISTS_CONSUMER_TOOLS) else None
    )
    if config.bug_bounty_headers:
        marker_headers = "\n".join(
            f"- {name}: {value}" for name, value in sorted(config.bug_bounty_headers.items())
        )
        marker_context = f"""
## BUG BOUNTY TRAFFIC MARKERS

For all tools that make HTTP requests, include these bug bounty traffic HTTP headers:
{marker_headers}

"""
        full_tools_context = f"{full_tools_context}\n\n{marker_context}" if full_tools_context else marker_context

    if config.bug_bounty_headers:
        try:
            import asyncio

            asyncio.run(browser_set_headers(config.bug_bounty_headers))
            agent_logger.info(
                "Applied %d bug bounty marker header(s) to the browser context",
                len(config.bug_bounty_headers),
            )
        except Exception as e:
            agent_logger.warning("Unable to pre-apply bug bounty browser headers: %s", e)

    http_request_instructions = """
- Purpose: Deterministic HTTP(S) requests for web page and API testing (including GraphQL/REST)
- Validation: Save request/response transcript + negative/control case as artifacts, grep/sed to extract relevant data, store only file path in findings
- Interoperability: May be selected or used alongside `curl`; overlapping HTTP clients are permitted
- Managed endpoint keys are observations unless abuse/sensitive exposure demonstrated with artifacts
"""
    tool_append_description(http_request, http_request_instructions)

    python_repl_instructions = """
- Usage: Rapid PoC prototyping, batch multiple tests. NO TIMEOUT (avoid >600s operations)
- File writes: MUST use absolute paths from OPERATION ARTIFACTS DIRECTORY (relative paths write to operation root)
- Promotion trigger: POC works + logic needed >2 times → MUST promote via editor+load_tool to OPERATION TOOLS DIRECTORY
- Results: Store all outputs as artifacts with descriptive names

**editor + load_tool** (meta-tooling)
- Purpose: Promote working POCs to reusable tools | Novel exploits when existing tools insufficient
- Trigger: POC tested + works + pattern repeats >2 times → promote to tool (cost: create once vs rewrite each time)
- Workflow: editor(path in OPERATION TOOLS DIRECTORY, @tool decorator) → load_tool(name) → invoke
- Structure: @tool decorator, docstring, type hints | Location: tools/ subdirectory, NOT artifacts/
- Debug first: Error in tool? Fix via editor → load_tool → test. Create new only if incompatible.
- NOT for: Reports, documents, one-time scripts (use artifacts/ for those)
"""
    tool_append_description(python_repl, python_repl_instructions)

    # Always use original tools - event emission is handled by callback
    # The following are builtin_tools that can be selected by the module
    builtin_tools_list = [
        http_request,
        browser_set_headers,
        browser_goto_url,
        browser_get_page_html,
        browser_perform_action,
        browser_observe_page,
        browser_evaluate_js,
        browser_get_cookies,
        channel_create_forward,
        channel_create_reverse,
        channel_send,
        channel_poll,
        channel_status,
        channel_close,
        oast_health,
        oast_endpoints,
        oast_poll,
        oast_register_http_response,
        oast_clear_http_responses,
        specialized_recon_orchestrator,
        advanced_payload_coordinator,
        auth_chain_analyzer,
        idor_specialist,
        client_bundle_inventory,
    ]

    web_search_instructions = """
**Purpose**
  - external intel, OSINT, NVD/CVE, Exploit‑DB, vendor advisories, Shodan/Censys, VirusTotal; save request/response artifacts and cite them in Proof Packs.
  - NOT for: Do not run published proof-of-concepts, use for learning how to write own exploit
"""
    if os.getenv("TAVILY_API_KEY"):
        # rename to web_search so instructions can be consistent
        tool_rename(tavily_search, "web_search")
        tool_append_description(tavily_search, web_search_instructions)
        builtin_tools_list.append(tavily_search)
    else:
        tool_append_description(web_search, web_search_instructions)
        builtin_tools_list.append(web_search)


    logger.info(f"Built-in tools available for allow listing by module: {[get_tool_name(tool) for tool in builtin_tools_list]}")

    # Core tools are available to workflow workers unless a role narrows them further.
    # Plan/task mutation remains owned by Python workflow code; create_tasks is included only for roles that need it.
    editor = create_absolute_path_editor(strands_editor)
    core_tools_list = [
        swarm,
        shell,
        editor,
        load_tool,
        store_observation,
        store_knowledge,
        store_finding,
        record_finding_validation,
        store_objective_candidate,
        record_objective_validation,
        memory_retrieve,
        memory_list,
        create_artifact_reader(prompt_token_limit, max_output_chars=max_result_chars),
        recon_output_to_inventory_manifest,
        create_tasks,
        sleep,
        python_repl,
        environment,  # environment is referenced by other strands tools
    ]

    optional_tools_list = []

    if "module_tool_allowlist" in locals() and module_tool_allowlist is not None:
        for builtin_tool in builtin_tools_list:
            tool_name = get_tool_name(builtin_tool)
            if any(fnmatch.fnmatch(tool_name, tool_allowed) for tool_allowed in module_tool_allowlist):
                optional_tools_list.append(builtin_tool)
    else:
        optional_tools_list.extend(builtin_tools_list)

    tools_list = list(core_tools_list)
    tool_count += len(tools_list)
    # The tools below have already been counted. We cannot use `tool_count = len(tools_list)` because there may be unloaded tools

    # Inject module-specific tools if available
    if "loaded_module_tools" in locals() and loaded_module_tools:
        optional_tools_list.extend(loaded_module_tools)
        tools_list.extend(loaded_module_tools)
        agent_logger.info(
            "Injected %d module tools into agent", len(loaded_module_tools)
        )

    # Inject MCP tools if available
    if "mcp_tools" in locals() and mcp_tools:
        optional_tools_list.extend(mcp_tools)
        tools_list.extend(mcp_tools)
        agent_logger.info(
            "Injected %d MCP tools into agent", len(mcp_tools)
        )

    # Capability-based warning if tool calls are unsupported for this model
    try:
        caps = get_capabilities(config.provider, config.model_id or "")
        if not caps.supports_tools and tools_list:
            agent_logger.warning(
                "Model %s does not support tool calls; tools will be ignored.",
                config.model_id,
            )
            tool_count = 0
    except Exception:
        pass


    # Load module-specific execution prompt
    module_execution_prompt = None
    module_termination_policy = ""
    try:
        module_loader = prompts.get_module_loader()
        operation_root_path = paths.get("root") if paths else None
        module_execution_prompt = module_loader.load_module_execution_prompt(
            config.module, operation_root=operation_root_path
        )
        module_termination_policy = module_loader.load_module_termination_policy(
            config.module
        )
        if module_execution_prompt:
            print_status(
                f"Loaded module-specific execution prompt for '{config.module}'",
                "SUCCESS",
            )
        else:
            print_status(
                f"No module-specific execution prompt found for '{config.module}' - using default",
                "INFO",
            )
        # Emit explicit config log for module and execution prompt source
        exec_src = (
            getattr(module_loader, "last_loaded_execution_prompt_source", None)
            or "default (none found)"
        )
        termination_src = (
            getattr(module_loader, "last_loaded_termination_policy_source", None)
            or "default (none found)"
        )
        agent_logger.info(
            "CYBERAUTOAGENT: module='%s', execution_prompt_source='%s', termination_policy_source='%s'",
            config.module,
            exec_src,
            termination_src,
        )
    except Exception as e:
        logger.warning(
            "Error loading module execution prompt for '%s': %s", config.module, e
        )

    plan_snapshot = None
    plan_current_phase = None
    try:
        plan_snapshot = memory_client.get_active_plan(operation_id=operation_id)
        plan_current_phase = plan_snapshot.current_phase if plan_snapshot else None
    except Exception as e:
        logger.debug("Plan snapshot not available: %s", e)

    # Build system prompt using centralized prompt factory (memory-aware)
    system_prompt = prompts.get_system_prompt(
        target=config.target,
        objective=config.objective,
        operation_id=operation_id,
        budget=config.budget,
        provider=config.provider,
        has_existing_memories=has_existing_memories,
        memory_overview=memory_overview,
        tools_context=full_tools_context if full_tools_context else None,
        seclists_root=seclists_root,
        output_config={
            "base_dir": server_config.output.base_dir,
            "target_name": target_name,
            "operation_path": paths.get("root"),
            "artifacts_path": paths.get("artifacts"),
            "tools_path": paths.get("tools"),
        },
        plan_snapshot=plan_snapshot,
        plan_current_phase=plan_current_phase,
    )

    # If a module-specific execution prompt exists, append it to the system prompt
    if module_execution_prompt:
        system_prompt = (
            system_prompt
            + "\n\n## MODULE EXECUTION GUIDANCE\n"
            + module_execution_prompt.strip()
        )

    # Build SystemContentBlock[] to enable provider-side prompt caching where supported
    system_prompt_payload: Any
    try:
        from strands.types.content import SystemContentBlock

        # Bedrock with Anthropic models: use cachePoint (SDK converts to cache_control)
        if config.provider == "bedrock" and "anthropic.claude" in config.model_id:
            logger.info("Enabling prompt caching for Bedrock Anthropic model: %s", config.model_id)
            system_prompt_payload = [
                SystemContentBlock(text=system_prompt),
                SystemContentBlock(cachePoint={"type": "default"})
            ]
        # LiteLLM with Gemini: explicit caching support
        elif config.provider == "litellm" and "gemini" in config.model_id:
            logger.info("Enabling prompt caching for Gemini model: %s", config.model_id)
            system_prompt_payload = [
                SystemContentBlock(text=system_prompt),
                SystemContentBlock(cachePoint={"type": "default"})
            ]
        # Other providers: automatic caching (Azure, OpenAI, Grok) or no caching (Moonshot)
        # Both cases work with plain text - no special handling needed
        else:
            logger.debug("Using plain text system prompt for provider: %s, model: %s", config.provider, config.model_id)
            system_prompt_payload = system_prompt
    except Exception as e:
        # Fallback to plain text if SystemContentBlock not available
        logger.warning("Failed to create SystemContentBlock, falling back to plain text: %s", e)
        system_prompt_payload = system_prompt

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("system_prompt_payload %s", json.dumps(system_prompt_payload))

    # It works in both CLI and React modes
    from modules.handlers.react.agent_event_handler import AgentEventHandler

    # Set up output interception to prevent duplicate output
    # This must be done before creating the handler to ensure all stdout is captured
    if os.environ.get("CYBER_UI_MODE", "cli").lower() == "react":
        from modules.handlers.output_interceptor import setup_output_interception

        setup_output_interception()

    callback_handler = AgentEventHandler(
        operation_id=operation_id,
        provider_id=config.provider,
        model_id=config.model_id,
        specialist_model_id=server_config.swarm.llm.model_id,
        agent_name=f"Cyber-AutoAgent {config.op_id or operation_id}",
        agent_type="operation_controller",
        init_context={
            "objective": config.objective,
            "target": config.target,
            "module": config.module,
            "operation_mode": config.operation_mode,
            "provider": config.provider,
            "model": config.model_id,
            "region": config.region_name,
            "tools_available": tool_count,
            "memory": {
                "mode": config.memory_mode,
                "path": None if config_manager.getenv("QDRANT_URL") else os.path.join(server_config.output.base_dir, "qdrant"),
                "has_existing": has_existing_memories,
                "reused": has_existing_memories,
                "backend": "qdrant_service" if config_manager.getenv("QDRANT_URL") else "qdrant_local",
                **(
                    memory_overview
                    if memory_overview and isinstance(memory_overview, dict)
                    else {}
                ),
            },
            "budget": config.budget.to_ui_dict(),
            "observability": config_manager.getenv_bool("ENABLE_OBSERVABILITY", False),
            "ui_mode": config_manager.getenv("CYBER_UI_MODE", "cli").lower(),
        },
    )
    set_memory_event_emitter(callback_handler.emit_ui_event)

    sdk_context_manager = (config_manager.getenv("CYBER_SDK_CONTEXT_MANAGER", "false") or "false").strip().lower()
    if sdk_context_manager in {"", "0", "false", "none", "off", "disabled"}:
        sdk_context_manager = None
    elif sdk_context_manager not in {"auto", "agentic"}:
        agent_logger.warning(
            "Unsupported CYBER_SDK_CONTEXT_MANAGER=%r; using 'auto'",
            sdk_context_manager,
        )
        sdk_context_manager = "auto"

    # Create hooks for SDK lifecycle events (tool invocations, etc.)
    # These work alongside the callback handler to capture all events
    from modules.handlers.react.hooks import ReactHooks

    # Use the same emitter as the callback handler for consistency
    react_hooks = ReactHooks(
        emitter=callback_handler.emitter,
        operation_id=operation_id,
        agent_config=config,
        emit_tool_lifecycle=False,
    )

    tool_call_repair_hook = AgentRepairHook()

    artifact_reference_input_normalization_hook = ArtifactReferenceInputNormalizationHook()

    tool_repeat_guard_hook = _create_tool_repeat_guard(config_manager, agent_logger)

    prompt_budget_hook = PromptBudgetHook(_ensure_prompt_within_budget)

    tool_router_hook = ToolRouterHook(
        max_result_chars=max_result_chars,
        artifacts_dir=paths.get("artifacts"),
        artifact_threshold=artifact_threshold,
    ) if sdk_context_manager is None else None

    # hooks to include in agents, order is important
    hooks: List[HookProvider] = list(
        filter(
            bool,
            [
                tool_call_repair_hook,
                artifact_reference_input_normalization_hook,
                tool_repeat_guard_hook,
                tool_router_hook,
                react_hooks,
                prompt_budget_hook,
            ],
        )
    )
    subagent_hooks: List[HookProvider] = list(
        filter(
            bool,
            [
                tool_call_repair_hook,
                artifact_reference_input_normalization_hook,
                tool_repeat_guard_hook,
                tool_router_hook,
                react_hooks,
                prompt_budget_hook,
            ],
        )
    )

    # Update conversation window size and limits from SDK config
    try:
        if config_manager.getenv("CYBER_CONVERSATION_WINDOW"):
            window_size = max(10, config_manager.getenv_int("CYBER_CONVERSATION_WINDOW", 100))
        else:
            # base on prompt token limit
            if prompt_token_limit >= 400_000:
                window_size = 300
            elif prompt_token_limit >= 128_000:
                window_size = 200
            else:
                conversation_window = getattr(server_config.sdk, "conversation_window_size", None)
                window_size = (
                    int(conversation_window) if conversation_window is not None else 80
                )
    except (TypeError, ValueError):
        window_size = 80

    preserve_recent_messages=PRESERVE_LAST_DEFAULT
    preserve_first_messages=PRESERVE_FIRST_DEFAULT
    if not config_manager.getenv("CYBER_CONVERSATION_PRESERVE_LAST"):
        if prompt_token_limit <= 49_152:
            preserve_recent_messages = 2

    # Create and register conversation manager for all agents.
    # Use environment variables for preservation to enable effective pruning
    # Keep preserve_last low (5) to allow pruning: first (1) + last (5) = 6 preserved out of 120 window
    conversation_manager = MappingConversationManager(
        window_size=window_size,
        summary_ratio=0.3,
        preserve_recent_messages=preserve_recent_messages,
        preserve_first_messages=preserve_first_messages,
        tool_result_mapper=LargeToolResultMapper(
            # computed previously
            max_tool_chars=TOOL_COMPRESS_THRESHOLD,
            truncate_at=TOOL_COMPRESS_TRUNCATE
        ) if sdk_context_manager is None else None,
    )
    register_conversation_manager(conversation_manager)
    agent_logger.info(
        "Conversation manager created: window=%d, preserve_first=%d, preserve_last=%d, sdk_context_manager=%s",
        window_size,
        PRESERVE_FIRST_DEFAULT,
        PRESERVE_LAST_DEFAULT,
        sdk_context_manager or "disabled",
    )

    # Initialize concurrent tool executor for parallel execution
    tool_executor = ConcurrentToolExecutor()

    # Register toolUseId hook for patching toolUseId, must be last
    tool_use_id_hook = ToolUseIdHook()
    hooks.append(tool_use_id_hook)
    subagent_hooks.append(tool_use_id_hook)

    agent_logger.info(
        "HOOK REGISTRATION: will register %d hooks total (%d shared with sub-agents)",
        len(hooks), len(subagent_hooks)
    )

    trace_attributes = {
        # Core identification - session_id is the key for Langfuse trace naming
        "langfuse.session.id": operation_id,
        "langfuse.user.id": f"cyber-agent-{config.target}",
        # Human-readable name that Langfuse will pick up
        "name": f"Security Assessment - {config.target} - {operation_id}",
        # Tags for filtering and categorization
        "langfuse.tags": [
            "Cyber-AutoAgent",
            config.provider.upper(),
            operation_id,
        ],
        "langfuse.environment": config_manager.getenv(
            "DEPLOYMENT_ENV", "production"
        ),
        "langfuse.agent.type": "operation_controller",
        "langfuse.capabilities.swarm": True,
        # Standard OTEL attributes
        "session.id": operation_id,
        "user.id": f"cyber-agent-{config.target}",
        # Agent identification
        "agent.name": "Cyber-AutoAgent",
        "agent.version": __version__,
        "gen_ai.agent.name": "Cyber-AutoAgent",
        "gen_ai.system": "Cyber-AutoAgent",
        # Operation metadata
        "operation.id": operation_id,
        "operation.type": "security_assessment",
        "operation.start_time": datetime.now().isoformat(),
        "operation.budget.max_duration_minutes": config.budget.max_duration_minutes,
        "operation.budget.max_tokens": config.budget.max_tokens,
        "operation.budget.max_cost": config.budget.max_cost,
        # Target and objective
        "target.host": config.target,
        "objective.description": config.objective,
        # Model configuration
        "model.provider": config.provider,
        "model.id": config.model_id,
        "model.region": config.region_name
        if config.provider in ["bedrock", "litellm"]
        else "local",
        "gen_ai.request.model": config.model_id,
        # Memory configuration
        "memory.enabled": True,
        "memory.path": config_manager.getenv("QDRANT_URL") or os.path.join(server_config.output.base_dir, "qdrant"),
    }

    def create_subagent_callback_handler(
            name: str,
            agent_type: str,
            model_id: str = None,
            provider_id: str = None,
    ) -> AgentEventHandler:
        return AgentEventHandler(
            operation_id=operation_id,
            provider_id=provider_id or config.provider,
            model_id=model_id or server_config.swarm.llm.model_id,
            specialist_model_id=server_config.swarm.llm.model_id,
            emitter=callback_handler.emitter,
            init_context={"ui_mode": config_manager.getenv("CYBER_UI_MODE", "cli").lower()},
            coordinator=callback_handler.coordinator,
            agent_name=name,
            agent_type=agent_type,
            parent_agent_run_id=callback_handler.agent_run_id,
            emit_operation_init=False,
            start_metrics_thread=False,
        )

    # apply wrapper to provide agent_factory to any tool that has a parameter named such
    agent_factory_config = AgentFactoryConfig(
        hooks=subagent_hooks,
        callback_handler = callback_handler,
        callback_handler_factory=create_subagent_callback_handler,
        conversation_manager = conversation_manager,
        context_manager = sdk_context_manager,
        base_trace_attributes = trace_attributes,
    )
    init_agent_factory(agent_factory_config)

    # Register these in case something gets missed, strands will default to our config
    os.environ["STRANDS_PROVIDER"] = config.provider
    os.environ["STRANDS_MODEL_ID"] = config.model_id
    os.environ["STRANDS_MAX_TOKENS"] = str(server_config.llm.max_tokens)
    os.environ["STRANDS_TEMPERATURE"] = str(server_config.llm.temperature)
    os.environ["STRANDS_NON_INTERACTIVE"] = "true"
    os.environ["STRANDS_HTTP_ALLOW_INSECURE_SSL"] = "true"

    return AgentRuntimeResources(
        config=config,
        operation_id=operation_id,
        server_config=server_config,
        config_manager=config_manager,
        callback_handler=callback_handler,
        tools_list=tools_list,
        core_tools_list=core_tools_list,
        optional_tools_list=optional_tools_list,
        tool_executor=tool_executor,
        system_prompt_payload=system_prompt_payload,
        system_prompt=system_prompt,
        hooks=hooks,
        conversation_manager=conversation_manager,
        sdk_context_manager=sdk_context_manager,
        trace_attributes=trace_attributes,
        prompt_token_limit=prompt_token_limit,
        termination_policy=module_termination_policy.strip(),
    )


def create_agent(
    target: str,
    objective: str,
    config: Optional[AgentConfig] = None,
    runtime_resources: Optional[AgentRuntimeResources] = None,
    *,
    system_prompt: Optional[Any] = None,
    tools: Optional[List[Any]] = None,
    name: Optional[str] = None,
    agent_type: Optional[str] = None,
    include_tool_catalog: bool = True,
) -> Agent:
    """Create autonomous agent from shared runtime resources."""
    runtime = runtime_resources or create_agent_runtime_resources(target, objective, config)
    config = runtime.config
    resolved_system_prompt = system_prompt if system_prompt is not None else runtime.system_prompt_payload
    if isinstance(resolved_system_prompt, str):
        resolved_system_prompt = prompts.get_role_system_prompt(resolved_system_prompt, agent_type)

    agent_logger = logging.getLogger("CyberAutoAgent")
    agent_logger.debug("Creating autonomous agent")

    model = create_strands_model(config.provider, config.model_id, agent_type or "task_executor")

    trace_attributes = dict(runtime.trace_attributes)
    if agent_type:
        trace_attributes.update({
            "langfuse.agent.type": agent_type,
            "agent.role": agent_type,
            "agent.name": name or "Cyber-AutoAgent",
            "gen_ai.agent.name": name or "Cyber-AutoAgent",
        })

    if system_prompt:
        trace_attributes.update({
            "system_prompt": resolved_system_prompt,
        })

    callback_handler = runtime.callback_handler
    if agent_type and isinstance(runtime.callback_handler, AgentEventHandler):
        try:
            specialist_model_id = runtime.server_config.swarm.llm.model_id
        except Exception:
            specialist_model_id = config.model_id
        try:
            ui_mode = runtime.config_manager.getenv("CYBER_UI_MODE", "cli").lower()
        except Exception:
            ui_mode = os.getenv("CYBER_UI_MODE", "cli").lower()
        callback_handler = AgentEventHandler(
            operation_id=runtime.operation_id,
            provider_id=config.provider,
            model_id=config.model_id,
            specialist_model_id=specialist_model_id,
            emitter=runtime.callback_handler.emitter,
            init_context={"ui_mode": ui_mode},
            coordinator=runtime.callback_handler.coordinator,
            agent_name=name or f"Cyber-AutoAgent {agent_type}",
            agent_type=agent_type,
            parent_agent_run_id=runtime.callback_handler.agent_run_id,
            emit_operation_init=False,
            start_metrics_thread=False,
        )
    if getattr(callback_handler, "agent_run_id", None):
        trace_attributes["cyber.agent.run_id"] = str(callback_handler.agent_run_id)

    agent_hooks = list(runtime.hooks) if runtime.hooks else []
    failure_recovery_hook = None
    evaluator_artifact_read_limit_hook = None
    if agent_type == "task_evaluator":
        evaluator_artifact_read_limit_hook = EvaluatorArtifactReadLimitHook()
        agent_hooks.append(evaluator_artifact_read_limit_hook)
    if agent_type == "task_executor" and isinstance(callback_handler, AgentEventHandler):
        max_policy_violations = runtime.config_manager.getenv_int(
            "CYBER_TOOL_RECOVERY_MAX_POLICY_VIOLATIONS",
            2,
        )
        max_corrections = runtime.config_manager.getenv_int(
            "CYBER_TOOL_RECOVERY_MAX_CORRECTIONS",
            2,
        )
        if config.available_tools is None:
            config.available_tools = []

        def quarantine_shell_command(executable: str) -> List[str]:
            alternatives = get_shell_command_alternatives(executable, config.available_tools)
            remove_shell_command(config.available_tools, executable)
            agent_logger.warning(
                "Quarantined unavailable shell executable '%s'; alternatives=%s",
                executable,
                ",".join(alternatives),
            )
            return alternatives

        failure_recovery_hook = TaskFailureRecoveryHook(
            callback_handler.tool_outcome_journal,
            max_policy_violations=max_policy_violations,
            max_corrections=max_corrections,
            quarantine_callback=quarantine_shell_command,
            quarantined_executables=runtime.quarantined_shell_commands,
            efficiency_callback=callback_handler.record_efficiency_event,
        )
        agent_hooks.append(failure_recovery_hook)
    if agent_type in {"task_creator", "task_executor"}:
        agent_hooks.append(TerminalToolHook(agent_type))

    tool_executor = runtime.tool_executor
    if agent_type in _FAIL_FAST_TOOL_ROLES:
        tool_executor = FailFastSequentialToolExecutor()

    agent_kwargs = {
        "model": model,
        "name": name or f"Cyber-AutoAgent {config.op_id or runtime.operation_id}",
        "tools": tools if tools is not None else build_role_tools(runtime),
        "tool_executor": tool_executor,
        "system_prompt": resolved_system_prompt,
        "callback_handler": callback_handler,
        "hooks": agent_hooks or None,
        "load_tools_from_directory": tools is None,
        "trace_attributes": trace_attributes,
    }
    if model_uses_server_side_state(model):
        agent_logger.info(
            "Skipping local conversation manager for stateful model '%s'; "
            "conversation state is managed server-side.",
            config.model_id,
        )
    else:
        # Use proactive sliding + summarization fallback for stateless models.
        agent_kwargs["conversation_manager"] = runtime.conversation_manager
        if runtime.sdk_context_manager:
            # Let Strands add its context offloader/plugin support while our
            # project-specific conversation manager remains authoritative.
            agent_kwargs["context_manager"] = runtime.sdk_context_manager

    # Create agent (telemetry is handled globally by Strands SDK)
    agent = create_agent_with_stateful_retry(agent_kwargs, config.model_id, Agent)
    context_reduction_states = [
        state
        for tool in agent_kwargs["tools"]
        if isinstance((state := getattr(tool, "_cyber_context_reduction_state", None)), dict)
    ]
    if context_reduction_states:
        setattr(agent, "_cyber_context_reduction_states", context_reduction_states)
    # Allow reasoning deltas only when the provider/model supports them
    try:
        caps = get_capabilities(config.provider, config.model_id or "")
        setattr(
            agent,
            "_allow_reasoning_content",
            allows_reasoning_content_replay(
                config.provider,
                config.model_id or "",
                caps,
            ),
        )
    except Exception:
        setattr(agent, "_allow_reasoning_content", False)
    if runtime.prompt_token_limit:
        setattr(agent, "_prompt_token_limit", runtime.prompt_token_limit)
    try:
        setattr(agent, "_cyber_agent_name", agent_kwargs["name"])
        setattr(agent, "_cyber_agent_type", agent_type or getattr(callback_handler, "agent_type", "agent"))
        setattr(agent, "_cyber_callback_handler", callback_handler)
        if failure_recovery_hook is not None:
            setattr(agent, "_cyber_failure_recovery_hook", failure_recovery_hook)
        if evaluator_artifact_read_limit_hook is not None:
            setattr(agent, "_cyber_evaluator_artifact_read_limit_hook", evaluator_artifact_read_limit_hook)
        if getattr(callback_handler, "agent_run_id", None):
            setattr(agent, "_cyber_agent_run_id", callback_handler.agent_run_id)
        if getattr(callback_handler, "parent_agent_run_id", None):
            setattr(agent, "_cyber_parent_agent_run_id", callback_handler.parent_agent_run_id)
    except Exception:
        pass
    # Ensure legacy-compatible system prompt is directly accessible for tests
    try:
        setattr(agent, "system_prompt", runtime.system_prompt)
    except Exception:
        pass

    if include_tool_catalog and agent_kwargs["tools"]:
        agent.tool_registry.register_tool(tool_catalog_wrapper(agent, config.available_tools))

    agent_logger.debug("Agent initialized successfully")
    return agent
