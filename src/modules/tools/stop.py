"""
Event loop control tool for Strands Agent.

The stop tool sets the 'stop_event_loop' flag in the request state,
which signals the Strands runtime to terminate the current cycle cleanly.
"""

import logging
from typing import Any

from strands.types.tools import ToolResult, ToolUse

from modules.tools import get_memory_client
from modules.tools.memory import active_task_message

# Initialize logging and set paths
logger = logging.getLogger(__name__)

TOOL_SPEC = {
    "name": "stop",
    "description": "Stops the current event loop",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional reason for stopping the event loop cycle",
                }
            },
        }
    },
}


def stop(tool: ToolUse, **kwargs: Any) -> ToolResult:
    """
    Stops the current event loop cycle.

    This module checks the plan and active tasks and rejects termination if they are in progress.

    How It Works:
    ------------
    1. The tool extracts the optional reason from the input
    2. It sets the 'stop_event_loop' flag in the request state to True
    3. It returns a success message with the provided reason
    4. The Strands runtime detects the flag and stops further cycle execution

    Common Usage Scenarios:
    ---------------------
    - Task completion: Stop processing once a specific goal is achieved
    - Error handling: Terminate gracefully when encountering unrecoverable errors
    - User requests: End the session when the user explicitly requests termination
    - Resource management: Stop processing to prevent excessive computation

    Args:
        tool: The tool use object containing the tool input parameters
            - reason: Optional string explaining why the event loop is being stopped
        **kwargs: Additional keyword arguments
            - request_state: Dictionary containing the current request state

    Returns:
        Dict containing status and response content in the format:
        {
            "toolUseId": "<tool_use_id>",
            "status": "success",
            "content": [{"text": "Event loop cycle stop requested. Reason: <reason>"}]
        }

    Notes:
        - This tool only stops the current event loop cycle, not the entire application
        - The stop is graceful, allowing current operations to complete
        - Always provide a meaningful reason for debugging and user feedback
        - The stop flag is only effective within the current request context
    """
    tool_use_id = tool["toolUseId"]
    tool_input = tool["input"]
    request_state = kwargs.get("request_state", {})
    agent = kwargs.get("agent", None)

    # Validate the plan and task status
    memory_client = get_memory_client(silent=True)
    plan = memory_client.get_active_plan()

    if plan and not plan.assessment_complete and \
            plan.current_phase != plan.total_phases and \
            agent and getattr(agent, 'callback_handler', None) and \
            hasattr(agent.callback_handler, 'current_step') and \
            hasattr(agent.callback_handler, 'max_steps'):
        current_step = agent.callback_handler.current_step
        max_steps = agent.callback_handler.max_steps
        active_task, *_ = memory_client.get_or_activate_next_task_in_phase(phase=plan.current_phase)

        phase_step_start = max_steps * (plan.current_phase - 1) // plan.total_phases
        if active_task and current_step < phase_step_start * 0.9:
            return {
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [
                    {
                        "text":
                            "**MANDATORY ACTION**: Continue by executing this active task:\n" + active_task_message(
                                active_task)
                    }
                ],
            }
        if active_task is None:
            # TODO: if the next phase has no tasks, instruct the agent to use current memories to create discovery tasks **FOR THE CURRENT PHASE**
            return {
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [
                    {
                        "text":
                            f"**MANDATORY ACTION**: The plan is not complete, move to phase {plan.current_phase + 1}."
                    }
                ],
            }

    # Set the stop flag
    request_state["stop_event_loop"] = True

    # Get optional reason
    reason = tool_input.get("reason", "No reason provided")

    logger.debug(f"Reason: {reason}")

    return {
        "toolUseId": tool_use_id,
        "status": "success",
        "content": [
            {
                "text":
                    f"Event loop cycle stop requested. Reason: {reason}"
            }
        ],
    }
