import unittest

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
from strands.types.tools import ToolUse

from modules.config import AgentConfig
from modules.handlers.react.hooks import ReactHooks, classify_tool_outcome


class RewriteSwarmArgsTests(unittest.TestCase):
    def __init__(self, methodName: str = ...):
        super().__init__(methodName)
        self.agent_config = AgentConfig(provider="ollama", model_id="llama3.2", target="unittest", objective="pass_the_test")
        self.hook = ReactHooks(agent_config=self.agent_config)

    def test_non_swarm_tool_is_ignored(self):
        original_tool_use = ToolUse(
            name="other_tool",
            input={
                "agents": [
                    {"name": "helper", "model_provider": "aws-bedrock", "model_settings": {"x": 1}}
                ]
            },
            toolUseId="1"
        )
        event = BeforeToolCallEvent(tool_use=original_tool_use, agent=None, selected_tool=None, invocation_state=None)

        self.hook._on_before_tool(event)

        # Nothing should have changed
        self.assertEqual(event.tool_use, original_tool_use)

    def test_raw_in_parsed_input_is_ignored(self):
        # _parse_tool_input returns a sentinel with "raw" -> method should return early
        original_tool_use = ToolUse(
            name="swarm",
            input=b'{"agents": [{"name": "helper", "model_provider": "aws-bedrock", "model_settings": {"x": 1}}]}',
            toolUseId="1"
        )
        event = BeforeToolCallEvent(tool_use=original_tool_use, agent=None, selected_tool=None, invocation_state=None)

        self.hook._on_before_tool(event)

        # Because parsed input had "raw", the original tool_use["input"] must be untouched
        self.assertEqual(event.tool_use, original_tool_use)

    def test_non_list_agents_is_ignored(self):
        # _parse_tool_input returns an "agents" value that is not a list -> early return
        original_tool_use = ToolUse(
            name="swarm",
            input={
                "agents": {
                    "name": "helper",
                    "model_provider": "aws-bedrock",
                    "model_settings": {"x": 1},
                }
            },
            toolUseId="1"
        )
        event = BeforeToolCallEvent(tool_use=original_tool_use, agent=None, selected_tool=None, invocation_state=None)

        self.hook._on_before_tool(event)

        # Since agents is not a list, input should be unchanged
        self.assertEqual(event.tool_use, original_tool_use)

    def test_model_fields_stripped_from_agent_dicts(self):
        tool_use = ToolUse(
            name="swarm",
            input={
                "agents": [
                    {
                        "name": "agent1",
                        "role": "helper",
                        "model_provider": "aws-bedrock",
                        "model_settings": {"model_id": "foo", "temperature": 0.7},
                    },
                    "string-agent",  # non-dict, should be preserved as-is
                    {
                        "name": "agent2",
                        "model_provider": "ollama",
                    }
                ]
            },
            toolUseId="1"
        )
        event = BeforeToolCallEvent(tool_use=tool_use, agent=None, selected_tool=None, invocation_state=None)

        self.hook._on_before_tool(event)

        # Expect model_* fields removed from dict agents, and non-dict preserved
        expected_agents = [
            {
                "name": "agent1",
                "role": "helper",
                "model_settings": {"temperature": 0.7},
            },
            "string-agent",
            {
                "name": "agent2",
            },
        ]
        self.assertIn("input", event.tool_use)
        self.assertEqual(event.tool_use["input"]["agents"], expected_agents)

    def test_missing_agents_list_is_safe(self):
        # No 'agents' key -> nothing should blow up, and no agents added
        tool_use = ToolUse(name="swarm", input={"foo": "bar"}, toolUseId="1")
        event = BeforeToolCallEvent(tool_use=tool_use, agent=None, selected_tool=None, invocation_state=None)

        self.hook._on_before_tool(event)

        # Input should still not have an 'agents' key
        self.assertNotIn("agents", event.tool_use["input"])

    def test_tool_outcome_classification_distinguishes_non_execution(self):
        validation_result = {
            "status": "error",
            "content": [{"text": "Error: Validation failed for input parameters: command required"}],
        }

        self.assertEqual(classify_tool_outcome({"status": "success"}), ("success", True))
        self.assertEqual(classify_tool_outcome({"status": "error"}), ("error", True))
        self.assertEqual(classify_tool_outcome(validation_result), ("validation_error", False))
        self.assertEqual(classify_tool_outcome(validation_result, "policy blocked"), ("blocked", False))

    def test_after_tool_annotates_and_emits_outcome_metadata(self):
        events = []
        hook = ReactHooks(emitter=type("Emitter", (), {"emit": lambda _, event: events.append(event)})())
        result = {"status": "error", "toolUseId": "one", "content": [{"text": "blocked"}]}
        event = AfterToolCallEvent(
            agent=None,
            selected_tool=None,
            tool_use={"name": "shell", "toolUseId": "one", "input": {"command": "curl target"}},
            invocation_state={},
            result=result,
            cancel_message="blocked by recovery",
        )

        hook._on_after_tool(event)

        self.assertEqual(result["_cyber_outcome"], "blocked")
        self.assertFalse(result["_cyber_executed"])
        completion = next(item for item in events if item["type"] == "tool_end")
        self.assertEqual(completion["outcome"], "blocked")
        self.assertFalse(completion["executed"])
