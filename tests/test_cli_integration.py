#!/usr/bin/env python3

import argparse
import json
import os
import signal
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch, AsyncMock

import pytest
from strands.types.exceptions import MaxTokensReachedException

# Add src to path for imports


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cyberautoagent


class TestCLIArguments:
    """Test command-line argument parsing"""

    def test_required_arguments(self):
        """Test that required arguments are parsed correctly"""
        with patch(
            "sys.argv",
            [
                "cyberautoagent.py",
                "--target",
                "test.com",
                "--objective",
                "test objective",
            ],
        ):
            # Mock the setup and execution parts
            with (
                patch("cyberautoagent.setup_logging"),
                patch("cyberautoagent.auto_setup", return_value=[]),
                patch("cyberautoagent.create_agent", return_value=(Mock(), Mock())),
                patch("cyberautoagent.get_initial_prompt"),
                patch("cyberautoagent.print_banner"),
                patch("cyberautoagent.print_section"),
                patch("cyberautoagent.print_status"),
            ):
                # Parse arguments without executing main
                parser = argparse.ArgumentParser()
                parser.add_argument("--objective", type=str, required=True)
                parser.add_argument("--target", type=str, required=True)
                parser.add_argument("--iterations", type=int, default=100)
                parser.add_argument("--verbose", action="store_true")
                parser.add_argument("--model", type=str)
                parser.add_argument("--region", type=str, default="us-east-1")
                parser.add_argument("--server", type=str, choices=["remote", "local"], default="remote")
                parser.add_argument("--confirmations", action="store_true")

                args = parser.parse_args(["--target", "test.com", "--objective", "test objective"])

                assert args.target == "test.com"
                assert args.objective == "test objective"
                assert args.server == "remote"  # default
                assert args.iterations == 100  # default
                assert not args.verbose  # default
                assert not args.confirmations  # default

    def test_server_argument_choices(self):
        """Test that --server argument accepts only valid choices"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--server", type=str, choices=["remote", "local"], default="remote")

        # Valid choices should work
        args = parser.parse_args(["--server", "local"])
        assert args.server == "local"

        args = parser.parse_args(["--server", "remote"])
        assert args.server == "remote"

        # Invalid choice should raise error
        with pytest.raises(SystemExit):
            parser.parse_args(["--server", "invalid"])

    def test_optional_arguments(self):
        """Test optional argument parsing"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--iterations", type=int, default=100)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--model", type=str)
        parser.add_argument("--region", type=str, default="us-east-1")
        parser.add_argument("--server", type=str, choices=["remote", "local"], default="remote")
        parser.add_argument("--confirmations", action="store_true")

        args = parser.parse_args(
            [
                "--target",
                "test.com",
                "--objective",
                "test objective",
                "--server",
                "local",
                "--iterations",
                "50",
                "--verbose",
                "--model",
                "custom-model",
                "--region",
                "us-west-2",
                "--confirmations",
            ]
        )

        assert args.target == "test.com"
        assert args.objective == "test objective"
        assert args.server == "local"
        assert args.iterations == 50
        assert args.verbose is True
        assert args.model == "custom-model"
        assert args.region == "us-west-2"
        assert args.confirmations is True

    def test_new_output_arguments(self):
        """Test that new output configuration arguments are properly parsed"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--output-dir", type=str)
        parser.add_argument("--keep-memory", action="store_true", default=True)

        args = parser.parse_args(
            [
                "--target",
                "test.com",
                "--objective",
                "test objective",
                "--output-dir",
                "/custom/output",
            ]
        )

        assert args.target == "test.com"
        assert args.objective == "test objective"
        assert args.output_dir == "/custom/output"
        assert args.keep_memory is True  # Default is now True


class TestMainFunction:
    """Test main function execution flow"""

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--provider",
            "bedrock",
        ],
    )
    def test_main_remote_flow(
        self,
        mock_print_status,
        mock_print_section,
        mock_print_banner,
        mock_get_prompt,
        mock_create_agent,
        mock_auto_setup,
        mock_setup_logging,
    ):
        """Test main function execution with remote server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.steps = 5
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_steps": 5,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.get_evidence_summary.return_value = []
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = (mock_agent, mock_handler)
        mock_auto_setup.return_value = ["nmap", "nikto"]
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return immediately
        mock_agent.return_value = "Agent response"

        # This should not raise any exceptions
        try:
            cyberautoagent.main()
        except SystemExit as e:
            # main() calls sys.exit(0) on success, which is expected
            assert e.code in [None, 0]

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--provider",
            "ollama",
        ],
    )
    def test_main_local_flow(
        self,
        mock_print_status,
        mock_print_section,
        mock_print_banner,
        mock_get_prompt,
        mock_create_agent,
        mock_auto_setup,
        mock_setup_logging,
    ):
        """Test main function execution with local server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.steps = 5
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_steps": 5,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.get_evidence_summary.return_value = []
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = (mock_agent, mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        try:
            cyberautoagent.main()
        except SystemExit as e:
            # main() calls sys.exit(0) on success, which is expected
            assert e.code in [None, 0]

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--provider",
            "ollama",
            "--continue",
        ],
    )
    @pytest.mark.skip(reason="Need more mocks")
    def test_main_local_flow_continue(
            self,
            mock_print_status,
            mock_print_section,
            mock_print_banner,
            mock_get_prompt,
            mock_create_agent,
            mock_auto_setup,
            mock_setup_logging,
    ):
        """Test main function execution with local server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.steps = 5
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_steps": 5,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.get_evidence_summary.return_value = []
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = (mock_agent, mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        try:
            cyberautoagent.main()
        except SystemExit as e:
            # main() calls sys.exit(0) on success, which is expected
            assert e.code in [None, 0]

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--provider",
            "ollama",
            "--report",
        ],
    )
    @pytest.mark.skip(reason="Need more mocks")
    def test_main_local_flow_report(
            self,
            mock_print_status,
            mock_print_section,
            mock_print_banner,
            mock_get_prompt,
            mock_create_agent,
            mock_auto_setup,
            mock_setup_logging,
    ):
        """Test main function execution with local server"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.steps = 5
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_steps": 5,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.get_evidence_summary.return_value = []
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = (mock_agent, mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        try:
            cyberautoagent.main()
        except SystemExit as e:
            # main() calls sys.exit(0) on success, which is expected
            assert e.code in [None, 0]

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        ["cyberautoagent.py", "--target", "test.com", "--objective", "test objective"],
    )
    def test_main_create_agent_failure(self, mock_print_status, mock_create_agent, mock_auto_setup, mock_setup_logging):
        """Test main function when create_agent fails"""

        mock_create_agent.side_effect = Exception("Agent creation failed")
        mock_auto_setup.return_value = []

        with pytest.raises(SystemExit) as exc_info:
            cyberautoagent.main()

        assert exc_info.value.code == 1

    @patch("cyberautoagent.setup_logging")
    @patch("cyberautoagent.auto_setup")
    @patch("cyberautoagent.create_agent")
    @patch("cyberautoagent.get_initial_prompt")
    @patch("cyberautoagent.print_banner")
    @patch("cyberautoagent.print_section")
    @patch("cyberautoagent.print_status")
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test objective",
            "--provider",
            "ollama",
            "--mcp-enabled",
            "--mcp-conns",
            """[{"id":"mcp1","transport":"streamable-http","server_url":"http://127.0.0.1:8000/mcp"}]""",
        ],
    )
    def test_main_local_mcp_flow(
            self,
            mock_print_status,
            mock_print_section,
            mock_print_banner,
            mock_get_prompt,
            mock_create_agent,
            mock_auto_setup,
            mock_setup_logging,
    ):
        """Test main function execution with local server and an MCP"""

        # Setup mocks
        mock_agent = Mock()
        mock_handler = Mock()
        mock_handler.steps = 5
        mock_handler.has_reached_limit.return_value = False
        mock_handler.get_summary.return_value = {
            "total_steps": 5,
            "tools_created": 2,
            "evidence_collected": 3,
            "memory_operations": 4,
            "capability_expansion": ["tool1", "tool2"],
        }
        mock_handler.get_evidence_summary.return_value = []
        mock_handler.tool_counts.return_value = {}
        mock_handler.tool_counts.values.return_value = []

        mock_create_agent.return_value = (mock_agent, mock_handler)
        mock_auto_setup.return_value = []
        mock_get_prompt.return_value = "test prompt"

        # Mock agent execution to return normally, then trigger completion
        mock_agent.return_value = "Agent response"

        try:
            cyberautoagent.main()
        except SystemExit as e:
            # main() calls sys.exit(0) on success, which is expected
            assert e.code in [None, 0]


class TestEnvironmentVariables:
    """Test environment variable handling"""

    @patch.dict(os.environ, {}, clear=True)
    @patch(
        "sys.argv",
        [
            "cyberautoagent.py",
            "--target",
            "test.com",
            "--objective",
            "test",
            "--confirmations",
        ],
    )
    def test_confirmations_flag_sets_env_var(self):
        """Test that --confirmations flag properly manages environment variables"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--confirmations", action="store_true")

        args = parser.parse_args(["--target", "test.com", "--objective", "test", "--confirmations"])

        # Simulate the environment variable logic from main()
        if not args.confirmations:
            os.environ["BYPASS_TOOL_CONSENT"] = "true"
        else:
            os.environ.pop("BYPASS_TOOL_CONSENT", None)

        # With --confirmations, the env var should not be set
        assert "BYPASS_TOOL_CONSENT" not in os.environ

    @patch.dict(os.environ, {}, clear=True)
    @patch("sys.argv", ["cyberautoagent.py", "--target", "test.com", "--objective", "test"])
    def test_no_confirmations_flag_sets_env_var(self):
        """Test that without --confirmations flag, environment variable is set"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--objective", type=str, required=True)
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--confirmations", action="store_true")

        args = parser.parse_args(["--target", "test.com", "--objective", "test"])

        # Simulate the environment variable logic from main()
        if not args.confirmations:
            os.environ["BYPASS_TOOL_CONSENT"] = "true"
        else:
            os.environ.pop("BYPASS_TOOL_CONSENT", None)

        # Without --confirmations, the env var should be set
        assert os.environ["BYPASS_TOOL_CONSENT"] == "true"



def test_cli_helpers_signal_and_workspace_markers(monkeypatch, tmp_path):
    logs = []
    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: False)
    monkeypatch.setattr(cyberautoagent.requests, "get", Mock(return_value=SimpleNamespace(status_code=200)))
    assert cyberautoagent.detect_deployment_mode() == "compose"

    logger = SimpleNamespace(info=lambda *args: logs.append(args), debug=lambda *args: logs.append(args))
    monkeypatch.setattr(cyberautoagent, "detect_deployment_mode", lambda: "cli")
    monkeypatch.setattr(cyberautoagent, "StrandsTelemetry", lambda: SimpleNamespace(setup_otlp_exporter=Mock()))
    telemetry = cyberautoagent.setup_telemetry(logger)
    assert telemetry is not None

    cyberautoagent.setup_langfuse_connection(logger, "cli")
    assert cyberautoagent.os.environ["OTEL_SERVICE_NAME"] == "cyber-autoagent"

    monkeypatch.setattr(cyberautoagent.traceback, "extract_stack", lambda: [SimpleNamespace(filename="swarm.py", name="run")])
    monkeypatch.setattr(cyberautoagent.threading, "Thread", lambda **_kwargs: SimpleNamespace(start=Mock()))
    with pytest.raises(KeyboardInterrupt):
        cyberautoagent.signal_handler(signal.SIGINT, None)


def test_cli_deployment_telemetry_and_signal_variants(monkeypatch):
    logger = SimpleNamespace(info=Mock(), debug=Mock())

    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: True)
    monkeypatch.setattr(cyberautoagent.requests, "get", Mock(side_effect=RuntimeError("down")))
    assert cyberautoagent.detect_deployment_mode() == "container"

    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: False)
    assert cyberautoagent.detect_deployment_mode() == "cli"

    telemetry = SimpleNamespace(setup_otlp_exporter=Mock())
    monkeypatch.setattr(cyberautoagent, "StrandsTelemetry", lambda: telemetry)
    monkeypatch.setattr(cyberautoagent, "detect_deployment_mode", lambda: "compose")
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("CYBER_UI_MODE", "cli")
    assert cyberautoagent.setup_telemetry(logger) is telemetry
    telemetry.setup_otlp_exporter.assert_called_once()

    monkeypatch.setattr(cyberautoagent, "is_docker", lambda: True)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    cyberautoagent.setup_langfuse_connection(logger, "container")
    assert cyberautoagent.os.environ["OTEL_EXPORTER_OTLP_HEADERS"].startswith("Authorization=Basic")

    monkeypatch.setattr(cyberautoagent.traceback, "extract_stack", lambda: [SimpleNamespace(filename="main.py", name="main")])
    thread = SimpleNamespace(start=Mock())
    monkeypatch.setattr(cyberautoagent.threading, "Thread", Mock(return_value=thread))
    with pytest.raises(KeyboardInterrupt):
        cyberautoagent.signal_handler(signal.SIGTERM, None)
    thread.start.assert_not_called()

    with pytest.raises(KeyboardInterrupt):
        cyberautoagent.signal_handler(999, None)


def test_cli_main_runs_mocked_react_operation(monkeypatch, tmp_path):
    class Provider:
        value = "ollama"

    server_config = SimpleNamespace(
        memory=SimpleNamespace(llm=SimpleNamespace(provider=Provider(), model_id="mem-llm")),
        embedding=SimpleNamespace(model_id="embed"),
        output=SimpleNamespace(base_dir=str(tmp_path)),
        llm=SimpleNamespace(model_id="llama", temperature=0.1, max_tokens=128, top_p=None),
    )
    config_manager = SimpleNamespace(
        get_server_config=Mock(return_value=server_config),
        get_mcp_config=Mock(return_value=SimpleNamespace(enabled=False, connections=[])),
        get_provider=Mock(return_value="ollama"),
        get_default_region=Mock(return_value="us-west-2"),
    )

    class FakeCallback:
        def __init__(self):
            self.current_step = 0
            self.max_steps = 2
            self.tool_counts = {}
            self.pending_step_header = False
            self._emitted_any_reasoning = False
            self.stop_tool_used = False
            self.termination_reason = None

        def process_metrics(self, metrics):
            self.metrics = metrics.accumulated_usage

        def should_stop(self):
            return self.current_step >= 1

        def has_reached_limit(self):
            return False

        def get_summary(self):
            return {
                "total_steps": self.current_step,
                "tools_created": 0,
                "evidence_collected": 0,
                "memory_operations": 0,
                "capability_expansion": [],
            }

        def get_evidence_summary(self):
            return []

        def ensure_report_generated(self, *_args):
            self.report_generated = True

        def trigger_evaluation_on_completion(self):
            self.evaluation_triggered = True

        def wait_for_evaluation_completion(self, timeout):
            self.evaluation_timeout = timeout

        def emit_termination(self, reason, message):
            self.termination_reason = reason
            self.termination_message = message

    callback = FakeCallback()

    class FakeAgent:
        def __init__(self):
            self.messages = []
            self.model = SimpleNamespace()
            self.cleanup = Mock()

        def __call__(self, message):
            self.last_message = message
            return SimpleNamespace(
                metrics=SimpleNamespace(accumulated_usage={"inputTokens": 1, "outputTokens": 2})
            )

    fake_agent = FakeAgent()

    monkeypatch.setenv("CYBER_UI_MODE", "react")
    monkeypatch.setenv("CYBERAGENT_NO_BANNER", "1")
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "test", "--iterations", "2", "--provider", "ollama"])
    monkeypatch.setattr(cyberautoagent.signal, "signal", Mock())
    monkeypatch.setattr(cyberautoagent, "ensure_workspace_marker_files", Mock())
    monkeypatch.setattr(cyberautoagent, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(cyberautoagent, "get_output_path", lambda target, op_id, subdir, base_dir: str(tmp_path / target / op_id))
    monkeypatch.setattr(cyberautoagent, "setup_logging", lambda **_kwargs: SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock(), exception=Mock(), error=Mock()))
    monkeypatch.setattr(cyberautoagent, "setup_telemetry", Mock(return_value=SimpleNamespace()))
    monkeypatch.setattr("modules.config.system.logger.configure_sdk_logging", Mock())
    monkeypatch.setattr(cyberautoagent.atexit, "register", Mock())
    monkeypatch.setattr(cyberautoagent, "configure_model_rate_limits", Mock())
    monkeypatch.setattr(cyberautoagent, "auto_setup", Mock(return_value=["shell"]))
    monkeypatch.setattr(cyberautoagent, "create_agent", Mock(return_value=(fake_agent, callback)))
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "strip_reflection_snapshot_messages", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())
    monkeypatch.setattr(cyberautoagent.browser, "close_browser", Mock())
    monkeypatch.setattr(cyberautoagent, "channel_close_all", AsyncMock(return_value={"closed": 0}))
    monkeypatch.setattr(cyberautoagent, "close_oast_providers", AsyncMock(return_value=None))
    monkeypatch.setattr(cyberautoagent, "flush_traces", Mock())
    monkeypatch.setattr(cyberautoagent, "get_model_timeout", Mock(return_value=300))
    monkeypatch.setattr(cyberautoagent, "is_docker", Mock(return_value=False))
    monkeypatch.setattr(cyberautoagent, "interrupted", False)

    cyberautoagent.main()

    assert fake_agent.last_message.startswith("Conduct security assessment")
    assert fake_agent.cleanup.called
    assert callback.report_generated is True
    assert os.environ["CYBER_OPERATION_ID"].startswith("OP_")


def _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback):
    class Provider:
        value = "ollama"

    server_config = SimpleNamespace(
        memory=SimpleNamespace(llm=SimpleNamespace(provider=Provider(), model_id="mem-llm")),
        embedding=SimpleNamespace(model_id="embed"),
        output=SimpleNamespace(base_dir=str(tmp_path)),
        llm=SimpleNamespace(model_id="llama", temperature=0.1, max_tokens=128, top_p=None),
    )
    config_manager = SimpleNamespace(
        get_server_config=Mock(return_value=server_config),
        get_mcp_config=Mock(return_value=SimpleNamespace(enabled=False, connections=[])),
        get_provider=Mock(return_value="ollama"),
        get_default_region=Mock(return_value="us-west-2"),
    )
    monkeypatch.setenv("CYBER_UI_MODE", "react")
    monkeypatch.setenv("CYBERAGENT_NO_BANNER", "1")
    monkeypatch.setattr(cyberautoagent.signal, "signal", Mock())
    monkeypatch.setattr(cyberautoagent, "ensure_workspace_marker_files", Mock())
    monkeypatch.setattr(cyberautoagent, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(cyberautoagent, "get_output_path", lambda target, op_id, subdir, base_dir=None: str(tmp_path / target / op_id))
    monkeypatch.setattr(cyberautoagent, "setup_logging", lambda **_kwargs: SimpleNamespace(info=Mock(), debug=Mock(), warning=Mock(), exception=Mock(), error=Mock()))
    monkeypatch.setattr(cyberautoagent, "setup_telemetry", Mock(return_value=SimpleNamespace()))
    monkeypatch.setattr("modules.config.system.logger.configure_sdk_logging", Mock())
    monkeypatch.setattr(cyberautoagent.atexit, "register", Mock())
    monkeypatch.setattr(cyberautoagent, "configure_model_rate_limits", Mock())
    monkeypatch.setattr(cyberautoagent, "auto_setup", Mock(return_value=["shell"]))
    monkeypatch.setattr(cyberautoagent, "create_agent", Mock(return_value=(fake_agent, callback)))
    monkeypatch.setattr(cyberautoagent, "print_status", Mock())
    monkeypatch.setattr(cyberautoagent, "strip_reflection_snapshot_messages", Mock())
    monkeypatch.setattr(cyberautoagent, "_ensure_prompt_within_budget", Mock())
    monkeypatch.setattr(cyberautoagent.browser, "close_browser", Mock())
    monkeypatch.setattr(cyberautoagent, "channel_close_all", AsyncMock(return_value={"closed": 0}))
    monkeypatch.setattr(cyberautoagent, "close_oast_providers", AsyncMock(return_value=None))
    monkeypatch.setattr(cyberautoagent, "flush_traces", Mock())
    monkeypatch.setattr(cyberautoagent, "get_model_timeout", Mock(return_value=300))
    monkeypatch.setattr(cyberautoagent, "is_docker", Mock(return_value=False))
    monkeypatch.setattr(cyberautoagent, "interrupted", False)
    return config_manager


class CliCallback:
    def __init__(self):
        self.current_step = 0
        self.max_steps = 2
        self.tool_counts = {}
        self.pending_step_header = False
        self._emitted_any_reasoning = False
        self.stop_tool_used = False
        self.termination_reason = None
        self.ensure_report_generated = Mock()
        self.trigger_evaluation_on_completion = Mock()
        self.wait_for_evaluation_completion = Mock()
        self.emit_termination = Mock()

    def process_metrics(self, _metrics):
        pass

    def should_stop(self):
        return True

    def has_reached_limit(self):
        return False

    def get_summary(self):
        return {
            "total_steps": self.current_step,
            "tools_created": 0,
            "evidence_collected": 0,
            "memory_operations": 0,
            "capability_expansion": [],
        }

    def get_evidence_summary(self):
        return []


def test_cli_service_mode_idle_interrupt_returns(monkeypatch):
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--service-mode"])
    monkeypatch.setattr(cyberautoagent.signal, "signal", Mock())
    monkeypatch.setattr(cyberautoagent, "ensure_workspace_marker_files", Mock())
    monkeypatch.setattr(cyberautoagent.time, "sleep", Mock(side_effect=KeyboardInterrupt))

    cyberautoagent.main()

    assert cyberautoagent.ensure_workspace_marker_files.call_count >= 1


def test_cli_main_report_mode_uses_latest_operation(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = SimpleNamespace(messages=[], model=SimpleNamespace(), cleanup=Mock())
    _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    monkeypatch.setenv("CYBER_BUG_BOUNTY_HEADERS", '{"X-Env":"yes"}')
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "report", "--provider", "ollama", "--report", "--bug-bounty-header", "X-Test=1"])
    monkeypatch.setattr(cyberautoagent, "get_default_base_dir", lambda: str(tmp_path))

    class DirEntry:
        def __init__(self, name):
            self.name = name

        def is_dir(self):
            return True

    monkeypatch.setattr(cyberautoagent.os, "scandir", lambda _path: [DirEntry("OP_20260101_000000"), DirEntry("OP_20260102_000000")])

    cyberautoagent.main()

    assert os.environ["CYBER_OPERATION_ID"] == "OP_20260102_000000"
    assert json.loads(os.environ["CYBER_BUG_BOUNTY_HEADERS"]) == {"X-Test": "1"}
    fake_agent.cleanup.assert_called_once()


def test_cli_main_service_mode_with_params_auto_runs(monkeypatch, tmp_path):
    callback = CliCallback()
    fake_agent = SimpleNamespace(messages=[], model=SimpleNamespace(), cleanup=Mock())
    _patch_cli_common(monkeypatch, tmp_path, fake_agent, callback)
    monkeypatch.setattr(
        cyberautoagent.sys,
        "argv",
        ["cyberautoagent", "--service-mode", "--target", "example.com", "--objective", "run", "--provider", "ollama"],
    )

    cyberautoagent.main()

    fake_agent.cleanup.assert_called_once()


def test_cli_main_handles_max_tokens_exception(monkeypatch, tmp_path):
    callback = CliCallback()

    class TokenAgent:
        messages = []
        model = SimpleNamespace()
        cleanup = Mock()

        def __call__(self, _message):
            raise MaxTokensReachedException("max_tokens")

    agent = TokenAgent()
    _patch_cli_common(monkeypatch, tmp_path, agent, callback)
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "test", "--iterations", "2", "--provider", "ollama"])

    cyberautoagent.main()

    callback.emit_termination.assert_called_with("max_tokens", "Model token limit reached. Switching to final report.")
    callback.ensure_report_generated.assert_called()
    agent.cleanup.assert_called_once()


def test_cli_main_actionless_loop_redirects_and_stalls(monkeypatch, tmp_path):
    callback = CliCallback()
    callback.should_stop = Mock(return_value=False)
    callback.has_reached_limit = Mock(return_value=False)

    class QuietAgent:
        def __init__(self):
            self.messages = [
                {"role": "user", "content": [{"text": "initial"}]},
                {"role": "assistant", "content": [{"text": "thinking"}]},
                {"role": "assistant", "content": [{"text": "still thinking"}]},
                {"role": "assistant", "content": [{"text": "done"}]},
            ]
            self.model = SimpleNamespace()
            self.cleanup = Mock()
            self.calls = []

        def __call__(self, message):
            self.calls.append(message)
            return SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={}))

    agent = QuietAgent()
    _patch_cli_common(monkeypatch, tmp_path, agent, callback)
    monkeypatch.setattr(cyberautoagent.sys, "argv", ["cyberautoagent", "--target", "example.com", "--objective", "test", "--iterations", "8", "--provider", "ollama"])
    monkeypatch.setattr(cyberautoagent, "get_reflection_snapshot", Mock(return_value="reflect"))
    monkeypatch.setattr(cyberautoagent, "get_memory_client", Mock(return_value=SimpleNamespace(get_active_plan=Mock(return_value=None))))
    monkeypatch.setattr(cyberautoagent, "get_active_task", Mock(return_value=""))
    monkeypatch.setattr(cyberautoagent, "mem0_list", Mock(return_value=""))
    monkeypatch.setattr(cyberautoagent, "rebuild_agent_conversation", Mock(return_value="rebuilt context"))

    cyberautoagent.main()

    assert len(agent.calls) >= 3
    assert any("MANDATORY ACTION" in message for message in agent.calls)
    callback.emit_termination.assert_called_with("stalled", "No actions taken after 3 attempts")
    agent.cleanup.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
