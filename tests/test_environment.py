import io
import json
import logging
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from modules.config.system import environment as mod
from modules.config.system import logger as logger_mod


def test_resolve_seclists_root_prefers_valid_configured_override(monkeypatch, tmp_path):
    configured_root = tmp_path / "custom-seclists"
    (configured_root / "Discovery").mkdir(parents=True)
    fallback_root = tmp_path / "fallback-seclists"
    (fallback_root / "Fuzzing").mkdir(parents=True)

    monkeypatch.setenv("CYBER_SECLISTS_DIR", str(configured_root))
    monkeypatch.setattr(mod, "_SECLISTS_ROOT_CANDIDATES", (fallback_root,))

    assert mod.resolve_seclists_root() == str(configured_root.resolve())


def test_resolve_seclists_root_uses_valid_known_location_when_override_is_invalid(monkeypatch, tmp_path):
    fallback_root = tmp_path / "known-seclists"
    (fallback_root / "Passwords").mkdir(parents=True)

    monkeypatch.setenv("CYBER_SECLISTS_DIR", str(tmp_path / "not-seclists"))
    monkeypatch.setattr(mod, "_SECLISTS_ROOT_CANDIDATES", (fallback_root,))

    assert mod.resolve_seclists_root() == str(fallback_root.resolve())


def test_resolve_seclists_root_returns_none_without_valid_location(monkeypatch, tmp_path):
    monkeypatch.delenv("CYBER_SECLISTS_DIR", raising=False)
    monkeypatch.setattr(mod, "_SECLISTS_ROOT_CANDIDATES", (tmp_path / "missing",))

    assert mod.resolve_seclists_root() is None


def test_configured_canaries_use_optional_structured_schema():
    config_path = mod.Path(mod.__file__).with_name("environment.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    canaries = [
        tool["canary"]
        for tool in config["cyber_tools"].values()
        if isinstance(tool, dict) and "canary" in tool
    ]
    assert canaries
    assert all(isinstance(canary, dict) and isinstance(canary.get("args"), list) for canary in canaries)


def test_shell_command_without_canary_is_available_unverified(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/scanner")

    health = mod.check_shell_command("scanner")

    assert health.state == "available_unverified"
    assert health.path == "/usr/bin/scanner"
    assert health.available is True


def test_shell_command_canary_uses_resolved_argv_without_shell(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/opt/tools/scanner")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=1, stdout="usage", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", run)
    health = mod.check_shell_command(
        "scanner",
        {"args": ["--help"], "timeout_seconds": 4, "accepted_exit_codes": [0, 1]},
    )

    assert health.state == "available_verified"
    assert calls == [
        (
            ["/opt/tools/scanner", "--help"],
            {"capture_output": True, "text": True, "timeout": 4, "check": False},
        )
    ]


def test_shell_command_canary_rejects_generic_dependency_failure(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/dirsearch")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="Traceback (most recent call last): ModuleNotFoundError: No module named 'dependency'",
        ),
    )

    health = mod.check_shell_command("dirsearch", {"args": ["-h"]})

    assert health.state == "broken"
    assert "ModuleNotFoundError" in health.reason


def test_shell_command_canary_timeout_and_invalid_config_are_broken(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/scanner")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("scanner", 5)),
    )

    assert mod.check_shell_command("scanner", {"args": ["--version"]}).reason == "canary timed out"
    assert mod.check_shell_command("scanner", "scanner --version").state == "broken"


@pytest.mark.parametrize(
    "canary",
    [
        {"args": "--help"},
        {"args": ["--help"], "timeout_seconds": 0},
        {"args": ["--help"], "accepted_exit_codes": []},
    ],
)
def test_shell_command_canary_rejects_invalid_structures(monkeypatch, canary):
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/scanner")

    health = mod.check_shell_command("scanner", canary)

    assert health.state == "broken"
    assert health.reason


def test_shell_command_missing_and_nonzero_canary_are_unavailable(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda command: None)
    assert mod.check_shell_command("missing").state == "missing"
    assert mod._get_shell_command_path("missing") is None

    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/scanner")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="bad arguments"),
    )
    health = mod.check_shell_command("scanner", {"args": ["--help"]})
    assert health.state == "broken"
    assert health.reason == "bad arguments"


def test_tee_output_writes_terminal_and_clean_log(tmp_path):
    terminal = io.StringIO()
    log_file = tmp_path / "session.log"
    tee = mod.TeeOutput(terminal, str(log_file))

    tee.write("\x1b[31mred\x1b[0m line\npartial")
    tee.write("\roverwritten")
    tee.write("\n")
    tee.close()
    tee.close()

    assert terminal.getvalue() == "\x1b[31mred\x1b[0m line\npartial\roverwritten\n"
    assert log_file.read_text() == "red line\noverwritten\n"


def test_tee_output_flush_and_file_like_methods(tmp_path):
    class Terminal(io.StringIO):
        def __init__(self):
            super().__init__()
            self.flushed = False

        def flush(self):
            self.flushed = True

        def fileno(self):
            return 12

        def isatty(self):
            return True

    terminal = Terminal()
    tee = mod.TeeOutput(terminal, str(tmp_path / "session.log"))

    tee.write("held")
    tee.flush()
    assert terminal.flushed is True
    assert tee.fileno() == 12
    assert tee.isatty() is True
    tee.close()
    assert (tmp_path / "session.log").read_text() == "held"


def test_auto_setup_discovers_available_and_unavailable_tools(monkeypatch, tmp_path, capsys):
    class FakePath:
        def __init__(self, path):
            self.path = str(path)

        def exists(self):
            return False

        def mkdir(self, exist_ok=False):
            return None

        def open(self, *args, **kwargs):
            return io.StringIO(
                "cyber_tools:\n"
                "  nmap:\n"
                "    description: Port scanner\n"
                "    command: nmap\n"
                "  missing:\n"
                "    description: Missing tool\n"
                "    command: missing-bin\n"
            )

        def with_name(self, name):
            return self

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "Path", FakePath)
    monkeypatch.setattr(mod.shutil, "which", lambda binary: "/usr/bin/nmap" if binary == "nmap" else None)
    monkeypatch.setattr(mod.os, "access", lambda *args: False)

    available = mod.auto_setup(skip_mem0_cleanup=True)

    assert available == ["nmap"]
    output = capsys.readouterr().out
    events = [
        json.loads(part.split("__CYBER_EVENT_END__", 1)[0])
        for part in output.split("__CYBER_EVENT__")[1:]
    ]
    assert [event["type"] for event in events] == [
        "tool_discovery_start",
        "tool_available",
        "tool_unavailable",
        "environment_ready",
    ]
    assert events[-1]["available_tools"] == ["nmap"]


def test_setup_logging_redirects_streams_and_registers_cleanup(monkeypatch, tmp_path):
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    registered = []
    initialized = []
    log_file = tmp_path / "cyber.log"

    monkeypatch.setattr(mod.atexit, "register", registered.append)
    monkeypatch.setattr(
        mod,
        "initialize_logger_factory",
        lambda **kwargs: initialized.append(kwargs),
    )

    logger = mod.setup_logging(str(log_file), verbose=True)
    try:
        assert isinstance(sys.stdout, mod.TeeOutput)
        assert isinstance(sys.stderr, mod.TeeOutput)
        assert registered
        assert initialized == [{"log_file": str(log_file), "verbose": True}]
        assert logger.name == "CyberAutoAgent"
        assert logger.level == logging.DEBUG
        assert all(
            handler.level == logging.INFO
            for handler in logger.handlers
            if isinstance(handler, logging.FileHandler)
        )
    finally:
        registered[0]()
        logger.handlers.clear()

    assert sys.stdout is sys.__stdout__
    assert sys.stderr is sys.__stderr__
    assert "CYBER-AUTOAGENT SESSION STARTED" in log_file.read_text()
    sys.stdout = original_stdout
    sys.stderr = original_stderr


def test_provider_payload_logging_requires_explicit_unsafe_flag(monkeypatch):
    monkeypatch.delenv("CYBER_UNSAFE_DIAGNOSTIC_LOGGING", raising=False)
    logger_mod.configure_provider_diagnostic_logging(enable_debug=True)

    assert logging.getLogger("openai._base_client").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("strands.models.litellm").getEffectiveLevel() == logging.WARNING

    monkeypatch.setenv("CYBER_UNSAFE_DIAGNOSTIC_LOGGING", "true")
    logger_mod.configure_provider_diagnostic_logging(enable_debug=True)

    assert logging.getLogger("openai._base_client").getEffectiveLevel() == logging.DEBUG
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.DEBUG
    assert logging.getLogger("strands.models.litellm").getEffectiveLevel() == logging.DEBUG

    logger_mod.configure_provider_diagnostic_logging(enable_debug=False)
