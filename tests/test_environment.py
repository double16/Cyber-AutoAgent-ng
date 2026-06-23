import io
import json
import logging
import sys

from modules.config.system import environment as mod


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
    finally:
        registered[0]()
        logger.handlers.clear()

    assert sys.stdout is sys.__stdout__
    assert sys.stderr is sys.__stderr__
    assert "CYBER-AUTOAGENT SESSION STARTED" in log_file.read_text()
    sys.stdout = original_stdout
    sys.stderr = original_stderr
