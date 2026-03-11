from strands.types.exceptions import ContextWindowOverflowException
from modules.handlers.conversation_budget import (
    MappingConversationManager,
)

def test_reduce_context_summarizing_restores_first_and_protected_messages(monkeypatch):
    class DummyAgent:
        def __init__(self, messages):
            self.messages = messages
            self.name = "test-agent"

    messages = [
        # Must always be preserved: original user objective/prompt
        {
            "role": "user",
            "content": [{"text": "Scan target example.com and find auth issues"}],
        },
        # Noise that can be removed
        {
            "role": "assistant",
            "content": [{"text": "intermediate assistant message"}],
        },
        # Latest plan tool result, should be protected
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "mem0_get_plan",
                        "status": "success",
                        "content": [{"text": '{"phase":"recon"}'}],
                    }
                }
            ],
        },
        # Evidence-bearing message
        {
            "role": "assistant",
            "content": [{"text": "Observed file /tmp/findings.txt with possible login paths"}],
        },
        # active_task marker referencing the evidence above, should be protected
        {
            "role": "assistant",
            "content": [
                {
                    "text": (
                        '<active_task>{"task":{"evidence":["/tmp/findings.txt"]}}</active_task>'
                    )
                }
            ],
        },
        # More noise
        {
            "role": "user",
            "content": [{"text": "another message"}],
        },
        {
            "role": "user",
            "content": [{"text": "last message"}],
        },
    ]
    agent = DummyAgent(messages)

    manager = MappingConversationManager(
        window_size=4,
        preserve_first_messages=1,
        preserve_recent_messages=1,
    )

    # Keep the test focused on restore behavior
    monkeypatch.setattr(
        "modules.handlers.conversation_budget.safe_estimate_tokens",
        lambda *args, **kwargs: 1000,
    )
    monkeypatch.setattr(
        "modules.handlers.conversation_budget.get_model_id_from_agent",
        lambda *args, **kwargs: "test-model",
    )

    # Force sliding reduction to fail so summarizing fallback is used
    def fake_sliding_reduce_context(agent, e=None, **kwargs):
        raise ContextWindowOverflowException("force summarizing path")

    monkeypatch.setattr(manager._sliding, "reduce_context", fake_sliding_reduce_context)

    # Simulate summarizing reduction removing protected content incorrectly.
    # The MappingConversationManager restore logic should put it back.
    def fake_super_reduce_context(self, agent, e=None, **kwargs):
        agent.messages[:] = [
            {"role": "assistant", "content": [{"text": "summary of earlier conversation"}]},
            {"role": "user", "content": [{"text": "another message"}]},
        ]

    monkeypatch.setattr(
        "modules.handlers.conversation_budget.SummarizingConversationManager.reduce_context",
        fake_super_reduce_context,
    )

    manager.reduce_context(agent)

    rendered = [
        "\n".join(block.get("text", "") for block in msg.get("content", []) if isinstance(block, dict))
        for msg in agent.messages
    ]

    # First/original user prompt must be restored and remain at the front
    assert agent.messages[0]["role"] == "user"
    assert "Scan target example.com and find auth issues" in rendered[0]

    # active_task marker must be restored
    assert any("<active_task>" in text for text in rendered)

    # Evidence-referencing message must be restored
    assert any("/tmp/findings.txt" in text for text in rendered)

    # Latest plan tool result must be restored
    assert any(
        any(
            isinstance(block, dict)
            and "toolResult" in block
            and block["toolResult"].get("toolUseId") == "mem0_get_plan"
            for block in msg.get("content", [])
        )
        for msg in agent.messages
    )
