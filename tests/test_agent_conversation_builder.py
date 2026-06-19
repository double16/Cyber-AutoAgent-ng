from types import SimpleNamespace

from modules.agents.agent_conversation_builder import rebuild_agent_conversation


class FakePlan:
    def __init__(self, toon="plan toon"):
        self.toon = toon

    def to_toon(self):
        return self.toon


def _texts(agent):
    return [block["text"] for message in agent.messages for block in message["content"]]


def test_rebuild_without_plan_preserves_initial_prompt_and_requests_plan():
    agent = SimpleNamespace(messages=[{"role": "assistant", "content": [{"text": "old"}]}])

    current_message = rebuild_agent_conversation(
        agent,
        active_plan=None,
        active_task="",
        initial_prompt="Initial scope",
        memories="Recon note",
    )

    texts = _texts(agent)
    assert texts == [
        "Initial scope",
        "\n\n## MEMORY SNAPSHOT (work progress)\nRecon note",
    ]
    assert "create a strategic plan via store_plan()" in current_message


def test_rebuild_without_plan_drops_error_memory_snapshot():
    agent = SimpleNamespace(messages=[])

    rebuild_agent_conversation(
        agent,
        active_plan=None,
        active_task="",
        initial_prompt="Initial scope",
        memories="Error: memory backend unavailable",
    )

    assert _texts(agent) == ["Initial scope"]


def test_rebuild_with_active_plan_and_task_replaces_messages():
    agent = SimpleNamespace(messages=[{"role": "assistant", "content": [{"text": "old"}]}])

    current_message = rebuild_agent_conversation(
        agent,
        active_plan=FakePlan("phase rows"),
        active_task='task id=1 status="active"',
        memories="Found login",
    )

    assert _texts(agent) == [
        "\n\n## PLAN SNAPSHOT\nphase rows",
        "\n\n## MEMORY SNAPSHOT (work progress)\nFound login",
        'task id=1 status="active"',
    ]
    assert "Continue by executing the active task" in current_message


def test_rebuild_with_plan_without_active_task_moves_to_next_phase():
    agent = SimpleNamespace(messages=[])

    current_message = rebuild_agent_conversation(
        agent,
        active_plan=FakePlan(),
        active_task='task id=1 status="done"',
    )

    assert _texts(agent) == ["\n\n## PLAN SNAPSHOT\nplan toon"]
    assert "Move to next plan phase" in current_message
