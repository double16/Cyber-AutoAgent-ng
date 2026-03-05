"""
Swarm tool: run a small team of specialized agents under shared context.

Wraps Strands SDK Swarm to enable hypothesis-diverse parallel exploration when a single agent is stuck
or when multiple capability classes should be tested in parallel. Keep teams small and prompts task-focused.
"""

import logging
import traceback
from typing import Any, Dict, List, Optional, Callable

from strands import Agent, ToolContext, tool
from strands.multiagent import Swarm

from modules.config import get_config_manager
from modules.config.models.factory import get_model_timeout

logger = logging.getLogger(__name__)


def _create_custom_agents(
        agent_factory: Callable[..., Agent],
        agent_specs: List[Dict[str, Any]],
        parent_agent: Optional[Agent] = None,
) -> List[Agent]:
    """
    Create custom agents based on user specifications.

    Args:
        agent_specs: List of agent specification dictionaries
        parent_agent: Parent agent for inheriting system prompt and tools

    Returns:
        List[Agent]: Custom agent instances

    Raises:
        ValueError: If agent specifications are invalid
    """
    if not agent_specs:
        raise ValueError("At least one agent specification is required")

    agents = []
    used_names = set()

    for i, spec in enumerate(agent_specs):
        # Validate required fields
        if not isinstance(spec, dict):
            raise ValueError(f"Agent specification {i} must be a dictionary")

        # Get agent name with fallback
        agent_name = spec.get("name", f"agent_{i + 1}")

        # Ensure unique names
        if agent_name in used_names:
            original_name = agent_name
            counter = 1
            while agent_name in used_names:
                agent_name = f"{original_name}_{counter}"
                counter += 1
        used_names.add(agent_name)

        # Get system prompt with fallback
        system_prompt = spec.get("system_prompt")
        if not system_prompt:
            if parent_agent and hasattr(parent_agent, "system_prompt") and parent_agent.system_prompt:
                system_prompt = (
                    "You are a helpful AI assistant specializing in collaborative problem solving.\n\n"
                    f"Base Instructions:\n{parent_agent.system_prompt}"
                )
            else:
                system_prompt = "You are a helpful AI assistant specializing in collaborative problem solving."
        else:
            # Optionally append parent system prompt
            if (
                    parent_agent
                    and hasattr(parent_agent, "system_prompt")
                    and parent_agent.system_prompt
                    and spec.get("inherit_parent_prompt", False)
            ):
                system_prompt = f"{system_prompt}\n\nBase Instructions:\n{parent_agent.system_prompt}"

        # Configure agent tools
        agent_tools = spec.get("tools", [])
        if "shell" not in agent_tools:
            # shell is needed for tools the agent tries to execute that don't exist as tools but in the shell
            agent_tools.append("shell")
        if agent_tools and parent_agent and hasattr(parent_agent, "tool_registry"):
            # Filter tools to ensure they exist in parent agent's registry
            available_tools = parent_agent.tool_registry.registry.keys()
            filtered_tool_names = [tool for tool in agent_tools if tool in available_tools]
            if len(filtered_tool_names) != len(spec.get("tools", [])):
                missing_tools = set(spec.get("tools", [])) - set(filtered_tool_names)
                logger.warning(f"Agent '{agent_name}' missing tools: {missing_tools}")

            # Get actual tool objects from parent agent's registry
            agent_tools = [parent_agent.tool_registry.registry[tool_name] for tool_name in filtered_tool_names]

        # Create agent
        swarm_agent = agent_factory(
            name=agent_name,
            agent_type="swarm_agent",
            model_spec=spec,
            system_prompt=system_prompt,
            tools=agent_tools,
        )

        agents.append(swarm_agent)
        logger.debug(f"Created agent '{agent_name}' with {len(agent_tools or [])} tools")

    return agents


@tool(context=True)
def swarm(
        task: str,
        agents: List[Dict[str, Any]],
        max_handoffs: int = 20,
        max_iterations: int = 20,
        execution_timeout: float = 900.0,
        node_timeout: float = 300.0,
        repetitive_handoff_detection_window: int = 8,
        repetitive_handoff_min_unique_agents: int = 3,
        tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """Run a coordinated multi-agent swarm for parallel exploration.

    Call when:
    - You need parallel testing across DIFFERENT capability classes (e.g., auth vs injection vs logic), or
    - You are stuck after multiple pivots and need hypothesis-diverse exploration.

    Do NOT call for:
    - Single-thread work, minor payload variations, or tasks one tool can complete.

    How to use:
    - Provide a clear `task` with scope, objective, and stop conditions.
    - Use 2–3 agents max. Each agent MUST have a distinct approach and an explicit handoff trigger.
    - Each agent system_prompt should specify: focus area, expected output, and WHEN to handoff.
    - Handoff requirement: Agents MUST explicitly call `handoff_to_agent('name', 'context')`. Without handoffs, swarm degenerates to sequential execution.

    Failure diagnosis:
    - If 0 iterations / no progress: no handoffs or prompts too similar → rewrite prompts with explicit handoff triggers.

    Args:
        task: Swarm objective to execute.
        agents: List of agent specification dictionaries. Each dictionary can contain:
            - name (str): Agent name
            - system_prompt (str): Agent's system prompt defining its role and expertise
            - tools (List[str]): List of tool names available to this agent (optional)
            - model_provider (str): Model provider for this agent (optional, inherits from parent)
            - model_settings (Dict): Model configuration for this agent (optional)
        max_handoffs/max_iterations/execution_timeout/node_timeout: Safeguards (defaults enforced server-side).

    Returns:
        Dict with status and content summarizing agent contributions.
    """
    agent_factory = getattr(swarm, "agent_factory", None)
    assert agent_factory is not None
    swarm_agents: Optional[list[Agent]] = None
    agent = tool_context.agent if tool_context else None

    try:
        # Validate input
        if not agents:
            raise ValueError("At least one agent specification is required")

        if len(agents) > 10:
            logger.warning(f"Large team size ({len(agents)} agents) may impact performance")

        logger.info(f"Creating custom swarm with {len(agents)} agents")

        # Create custom agents from specifications
        swarm_agents = _create_custom_agents(
            agent_factory=agent_factory,
            agent_specs=agents,
            parent_agent=agent,
        )

        # adjust minimum timeouts based on agent timeout and rate limit
        rate_limit_config = get_config_manager().get_rate_limit_config()
        model_timeout = get_model_timeout(swarm_agents[0].model)
        # assume about 3 seconds per model request without limiting, so 20 requests per minute
        # NOTE: this is a really rough adjustment
        if rate_limit_config and rate_limit_config.rpm:
            rate_limit_scale = max(1.0, 20.0 / rate_limit_config.rpm)
        else:
            rate_limit_scale = 1.0

        # enforce minimum values to address bad LLM values
        max_handoffs = max(max_handoffs,  20)
        max_iterations = max(max_iterations, 20)
        repetitive_handoff_detection_window = max(repetitive_handoff_detection_window, 8)
        repetitive_handoff_min_unique_agents = max(repetitive_handoff_min_unique_agents, 3)
        if model_timeout and model_timeout > 300.0:
            execution_timeout = max(execution_timeout, model_timeout * 3, 900.0 * rate_limit_scale)
            node_timeout = max(node_timeout, model_timeout, 300.0 * rate_limit_scale)
        else:
            execution_timeout = max(execution_timeout, 900.0 * rate_limit_scale)
            node_timeout = max(node_timeout, 300.0 * rate_limit_scale)

        # Create SDK Swarm with configuration
        sdk_swarm = Swarm(
            nodes=swarm_agents,
            max_handoffs=max_handoffs,
            max_iterations=max_iterations,
            execution_timeout=execution_timeout,
            node_timeout=node_timeout,
            repetitive_handoff_detection_window=repetitive_handoff_detection_window,
            repetitive_handoff_min_unique_agents=repetitive_handoff_min_unique_agents,
        )

        logger.info(f"Starting swarm execution with task: {task[:1000]}, execution_timeout=%d, node_timeout=%d, max_handoffs=%d, max_iterations=%d",
                    execution_timeout, node_timeout, max_handoffs, max_iterations)

        # Execute the swarm
        result = sdk_swarm(task)

        # Extract and format results
        response_parts = []

        # Add execution summary
        response_parts.append("**Custom Agent Team Execution Complete**")
        response_parts.append(f"  **Status:** {result.status}")
        response_parts.append(f"  **Execution Time:** {result.execution_time}ms")
        response_parts.append(f"  **Team Size:** {len(swarm_agents)} agents")
        response_parts.append(f"  **Iterations:** {result.execution_count}")

        if hasattr(result, "node_history") and result.node_history:
            agent_chain = " → ".join([node.node_id for node in result.node_history])
            response_parts.append(f"🔗 **Collaboration Chain:** {agent_chain}")

        # Add individual agent results
        if hasattr(result, "results") and result.results:
            response_parts.append("\n** Individual Agent Contributions:**")
            for agent_name, node_result in result.results.items():
                if hasattr(node_result, "result") and hasattr(node_result.result, "content"):
                    agent_content = []
                    for content_block in node_result.result.content:
                        if hasattr(content_block, "text") and content_block.text:
                            agent_content.append(content_block.text)

                    if agent_content:
                        response_parts.append(f"\n**{agent_name.upper().replace('_', ' ')}:**")
                        response_parts.extend(agent_content)

        # Add final consolidated result
        if hasattr(result, "node_history") and result.node_history and hasattr(result, "results") and result.results:
            last_agent = result.node_history[-1].node_id
            if last_agent in result.results:
                last_result = result.results[last_agent]
                if hasattr(last_result, "result") and hasattr(last_result.result, "content"):
                    response_parts.append("\n** Final Team Result:**")
                    for content_block in last_result.result.content:
                        if hasattr(content_block, "text") and content_block.text:
                            response_parts.append(content_block.text)

        # Add resource usage metrics
        if hasattr(result, "accumulated_usage") and result.accumulated_usage:
            usage = result.accumulated_usage
            response_parts.append("\n** Team Resource Usage:**")
            response_parts.append(f"• Input tokens: {usage.get('inputTokens', 0):,}")
            response_parts.append(f"• Output tokens: {usage.get('outputTokens', 0):,}")
            response_parts.append(f"• Total tokens: {usage.get('totalTokens', 0):,}")

        final_response = "\n".join(response_parts)

        return {
            "status": "success",
            "content": [{"text": final_response}],
        }

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Custom swarm execution failed: {str(e)}\n{error_trace}")

        return {
            "status": "error",
            "content": [{"text": f"⚠️ Custom swarm execution failed: {str(e)}"}],
        }

    finally:
        if swarm_agents:
            for agent in swarm_agents:
                try:
                    agent.cleanup()
                except Exception as e:
                    logger.debug("Cleaning up swarm agent", exc_info=e)
