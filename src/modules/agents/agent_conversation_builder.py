from typing import Optional

from strands import Agent
from strands.types.content import Message, ContentBlock

from modules.tools.memory import OperationPlan


def rebuild_agent_conversation(
        agent: Agent,
        active_plan: OperationPlan | None,
        active_task: str,
        initial_prompt: Optional[str] = None,
        memories: Optional[str] = None,
) -> str:
    current_message = ""
    # TODO: consider summarizing the memories to reduce content size and increase understanding
    if not active_plan:
        if initial_prompt:
            agent.messages[:] = [Message(role="user", content=[ContentBlock(text=initial_prompt)])]
        if memories:
            agent.messages.append(
                Message(role="user", content=[ContentBlock(text=f"\n\n## MEMORY SNAPSHOT (work progress)\n{memories}")])
            )
        current_message += f"**MANDATORY ACTION**: You have missed an important step, create a strategic plan via store_plan()."
    else:
        agent.messages[:] = [
            Message(role="user", content=[ContentBlock(text=f"\n\n## PLAN SNAPSHOT\n{active_plan.to_toon()}")])]
        if memories:
            agent.messages.append(
                Message(role="user", content=[ContentBlock(text=f"\n\n## MEMORY SNAPSHOT (work progress)\n{memories}")])
            )

        if 'status="active"' in active_task:
            agent.messages.append(Message(role="user", content=[ContentBlock(text=active_task)]))
            current_message += f"**MANDATORY ACTION**: There are tasks pending. Continue by executing the active task."
        elif active_plan:
            current_message += f"**MANDATORY ACTION**: Move to next plan phase if current phase criteria met."
    return current_message
