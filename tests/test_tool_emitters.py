from modules.handlers.react.tool_emitters import ToolEventEmitter


def test_http_request_emits_request_start_for_url():
    events = []
    emitter = ToolEventEmitter(events.append)

    emitter.emit_tool_specific_events(
        "http_request", {"method": "POST", "url": "https://example.test/login"}
    )

    assert events == [
        {
            "type": "http_request_start",
            "method": "POST",
            "url": "https://example.test/login",
        }
    ]


def test_swarm_operation_emits_rich_agent_details_and_defaults():
    events = []
    emitter = ToolEventEmitter(events.append)

    emitter.emit_tool_specific_events(
        "swarm",
        {
            "task": "map auth",
            "agents": [
                {
                    "name": "recon",
                    "system_prompt": "find endpoints",
                    "tools": ["http_request", 123],
                    "model_provider": "bedrock",
                    "model_settings": {"model_id": "claude"},
                },
                "validator",
                {"name": "broken", "tools": "not-a-list", "model_settings": "bad"},
            ],
        },
    )

    [event] = events
    assert event["type"] == "swarm_start"
    assert event["task"] == "map auth"
    assert event["agent_count"] == 3
    assert event["agent_names"] == ["recon", "validator", "broken"]
    assert event["agent_details"][0] == {
        "name": "recon",
        "system_prompt": "find endpoints",
        "tools": ["http_request", "123"],
        "model_provider": "bedrock",
        "model_id": "claude",
    }
    assert event["agent_details"][1]["model_id"] == "default"
    assert event["agent_details"][2]["tools"] == []
    assert event["max_handoffs"] == 20


def test_swarm_operation_skips_empty_payload():
    events = []
    emitter = ToolEventEmitter(events.append)

    emitter.emit_tool_specific_events("swarm", {"agents": [], "task": ""})

    assert events == []


def test_python_repl_emits_line_count_and_truncated_preview():
    events = []
    emitter = ToolEventEmitter(events.append)
    code = "print('a')\n" + ("x" * 120)

    emitter.emit_tool_specific_events("python_repl", {"code": code})

    assert events == [
        {
            "type": "code_execution",
            "language": "python",
            "lines": 2,
            "preview": code[:100] + "...",
        }
    ]


def test_report_and_think_emit_metadata():
    events = []
    emitter = ToolEventEmitter(events.append)

    emitter.emit_tool_specific_events(
        "generate_security_report",
        {"target": "api.example.test", "report_type": "executive"},
    )
    emitter.emit_tool_specific_events("think", {"content": "investigate tokens"})
    emitter.emit_tool_specific_events("think", "x" * 120)

    assert events[0] == {
        "type": "metadata",
        "content": {"target": "api.example.test", "type": "executive"},
    }
    assert events[1] == {
        "type": "metadata",
        "content": {"thinking": "investigate tokens"},
    }
    assert events[2] == {
        "type": "metadata",
        "content": {"thinking": ("x" * 100) + "..."},
    }


def test_unknown_and_complete_swarm_tools_emit_nothing():
    events = []
    emitter = ToolEventEmitter(events.append)

    emitter.emit_tool_specific_events("unknown_tool", {"anything": True})
    emitter.emit_tool_specific_events("complete_swarm_task", {"result": "done"})

    assert events == []
