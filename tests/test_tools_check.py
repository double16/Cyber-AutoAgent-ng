import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


def _load_tools_check():
    path = Path(__file__).parents[1] / "docker" / "tools_check.py"
    spec = importlib.util.spec_from_file_location("docker_tools_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_optional_canary_absence_accepts_present_executable():
    module = _load_tools_check()

    assert module.probe_command("/usr/bin/scanner", None) is True


def test_canary_uses_argv_timeout_and_accepted_exit_codes(monkeypatch):
    module = _load_tools_check()
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=1, stdout="usage", stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)

    assert module.probe_command(
        "/usr/bin/scanner",
        {"args": ["--help"], "timeout_seconds": 3, "accepted_exit_codes": [0, 1]},
    )
    assert calls == [
        (
            ["/usr/bin/scanner", "--help"],
            {"capture_output": True, "text": True, "timeout": 3, "check": False},
        )
    ]


def test_canary_rejects_dependency_failure_and_timeout(monkeypatch):
    module = _load_tools_check()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="Traceback (most recent call last): ModuleNotFoundError: No module named dependency",
        ),
    )
    assert module.probe_command("/usr/bin/dirsearch", {"args": ["-h"]}) is False

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("scanner", 5)),
    )
    assert module.probe_command("/usr/bin/scanner", {"args": ["--version"]}) is False


def test_canary_rejects_legacy_and_invalid_structures():
    module = _load_tools_check()

    assert module.probe_command("/usr/bin/scanner", "scanner --help") is False
    assert module.probe_command("/usr/bin/scanner", {"args": "--help"}) is False
    assert module.probe_command("/usr/bin/scanner", {"args": [], "accepted_exit_codes": []}) is False


def _write_environment(tmp_path, tools):
    path = tmp_path / "src" / "modules" / "config" / "system"
    path.mkdir(parents=True)
    (path / "environment.yaml").write_text(yaml.safe_dump({"cyber_tools": tools}), encoding="utf-8")


def test_main_reports_required_missing_and_broken_tools(monkeypatch, tmp_path, capsys):
    module = _load_tools_check()
    _write_environment(
        tmp_path,
        {
            "missing": {},
            "broken": {"command": "broken-bin", "canary": {"args": ["--version"]}},
            "broken-fallback": {"preference": "fallback", "canary": {"args": ["--version"]}},
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.sys, "argv", ["tools_check.py"])
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda command: None if command == "missing" else f"/usr/bin/{command}",
    )
    monkeypatch.setattr(module, "probe_command", lambda path, canary, timeout: False)

    with pytest.raises(SystemExit, match="1"):
        module.main()

    error = capsys.readouterr().err
    assert "Missing tools" in error
    assert "Broken tools" in error


@pytest.mark.parametrize("argument", ["--help", "-h"])
def test_main_help_exits_successfully(monkeypatch, argument, capsys):
    module = _load_tools_check()
    monkeypatch.setattr(module.sys, "argv", ["tools_check.py", argument])

    with pytest.raises(SystemExit, match="0"):
        module.main()

    assert "usage:" in capsys.readouterr().out


def test_main_rejects_unknown_argument_and_missing_environment(monkeypatch, tmp_path, capsys):
    module = _load_tools_check()
    monkeypatch.setattr(module.sys, "argv", ["tools_check.py", "--unknown"])
    with pytest.raises(SystemExit, match="2"):
        module.main()
    assert "usage:" in capsys.readouterr().err

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.sys, "argv", ["tools_check.py"])
    with pytest.raises(SystemExit, match="1"):
        module.main()
    assert "environment.yaml not found" in capsys.readouterr().err
