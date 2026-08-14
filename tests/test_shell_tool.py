import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _load_shell_module():
    module_name = "src.modules.tools.shell"
    module_path = Path(__file__).resolve().parents[1] / "src" / "modules" / "tools" / "shell.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


shell_module = _load_shell_module()
CommandExecutor = shell_module.CommandExecutor
execute_commands = shell_module.execute_commands
execute_single_command = shell_module.execute_single_command
shell = shell_module.shell
scoped_shell_command_validator = shell_module.scoped_shell_command_validator
validate_command = shell_module.validate_command


def _completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _single_success(command="cmd", output="ok"):
    return [{"command": command, "exit_code": 0, "output": output, "error": "", "status": "success"}]


def test_command_executor_execute_uses_subprocess_without_pty():
    assert "pty" not in shell_module.__dict__

    with patch.object(shell_module.subprocess, "run", return_value=_completed(stdout="hello\n")) as run:
        exit_code, output, error = CommandExecutor(timeout=45).execute("printf hello", "/tmp")

    assert (exit_code, output, error) == (0, "hello\n", "")
    run.assert_called_once_with(
        "printf hello",
        cwd="/tmp",
        shell=True,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def test_command_executor_execute_reports_nonzero_stderr():
    with patch.object(
        shell_module.subprocess,
        "run",
        return_value=_completed(stderr="bad flag\n", returncode=7),
    ):
        exit_code, output, error = CommandExecutor(timeout=30).execute("tool --bad", "/tmp")

    assert exit_code == 7
    assert output == ""
    assert error == "bad flag\n"


def test_command_executor_execute_reports_timeout_with_partial_output():
    timeout = subprocess.TimeoutExpired(
        cmd="slow",
        timeout=30,
        output="partial stdout",
        stderr="partial stderr",
    )
    with patch.object(shell_module.subprocess, "run", side_effect=timeout):
        exit_code, output, error = CommandExecutor(timeout=30).execute("slow", "/tmp")

    assert exit_code == 124
    assert output == "partial stdout"
    assert "partial stderr" in error
    assert "Command timed out after 30 seconds" in error


def test_execute_single_command_preserves_command_options():
    result = execute_single_command({"command": "printf hi", "timeout": 60}, "/tmp", 30)

    assert result["command"] == "printf hi"
    assert result["status"] == "success"
    assert result["output"] == "hi"
    assert result["options"] == {"command": "printf hi", "timeout": 60}


def test_execute_single_command_validation_error_shape():
    result = execute_single_command({"not_command": "printf hi"}, "/tmp", 30)

    assert result["status"] == "error"
    assert result["exit_code"] == 1
    assert "Command object must contain" in result["error"]


def test_execute_commands_sequential_cd_context(tmp_path):
    start_dir = os.getcwd()
    result = execute_commands(["pwd", f"cd {shlex.quote(str(tmp_path))}", "pwd"], False, False, start_dir, 30)

    assert [item["status"] for item in result] == ["success", "success", "success"]
    assert result[0]["output"].strip() == start_dir
    assert result[2]["output"].strip() == str(tmp_path)


def test_execute_commands_parallel_runs_without_pty_and_preserves_input_order():
    result = execute_commands(["printf one", "printf two"], True, False, os.getcwd(), 30)

    assert [item["status"] for item in result] == ["success", "success"]
    assert [item["output"] for item in result] == ["one", "two"]


def test_execute_commands_parallel_reports_failures():
    result = execute_commands(["printf ok", "exit 7"], True, False, os.getcwd(), 30)

    assert result[0]["status"] == "success"
    assert result[1]["status"] == "error"
    assert result[1]["exit_code"] == 7


def test_shell_formats_success_response():
    with patch.object(shell_module, "execute_commands", return_value=_single_success("printf hi", "hi")) as run:
        result = shell("printf hi", work_dir="/tmp", timeout=60)

    run.assert_called_once_with(["printf hi"], False, False, "/tmp", 60)
    assert result["status"] == "success"
    assert "Total commands: 1" in result["content"][0]["text"]
    assert "Output: hi" in result["content"][1]["text"]


def test_shell_formats_error_response():
    failed = [{"command": "exit 7", "exit_code": 7, "output": "", "error": "", "status": "error"}]
    with patch.object(shell_module, "execute_commands", return_value=failed):
        result = shell("exit 7")

    assert result["status"] == "error"
    assert "Failed: 1" in result["content"][0]["text"]
    assert "Exit Code: 7" in result["content"][1]["text"]


def test_shell_scope_validator_rejects_before_command_execution():
    with scoped_shell_command_validator(lambda commands: "Use http://192.0.2.10:3001 instead."):
        with patch.object(shell_module, "execute_commands") as run:
            result = shell("curl -sS https://target-1/api/spawn")

    run.assert_not_called()
    assert result["status"] == "error"
    assert result["content"] == [{"text": "Use http://192.0.2.10:3001 instead."}]


def test_shell_timeout_normalization_and_clamping():
    with patch.object(shell_module, "execute_commands", return_value=_single_success()) as run:
        shell("ls", timeout=5000)
        assert run.call_args.args[4] == 30

        shell("ls", timeout=3000000)
        assert run.call_args.args[4] == 30

        shell("ls", timeout=1200)
        assert run.call_args.args[4] == 900

        shell("ls", timeout=10)
        assert run.call_args.args[4] == 30

        shell("ls", timeout=100)
        assert run.call_args.args[4] == 100


def test_shell_command_joining_heuristic(monkeypatch):
    command = ["ls", "-la", "/tmp"]

    def fake_system(cmd):
        if "which ls" in cmd:
            return 0
        if "which -la" in cmd:
            return 1
        return 1

    monkeypatch.setattr(shell_module.os, "system", fake_system)
    monkeypatch.setattr(shell_module.os.path, "isdir", lambda _path: False)
    monkeypatch.setattr(shell_module.os.path, "isfile", lambda _path: False)

    with patch.object(shell_module, "execute_commands", return_value=_single_success()) as run:
        shell(command)

    expected_command = " ".join(map(shlex.quote, command))
    assert run.call_args.args[0] == [expected_command]


def test_shell_multiple_independent_commands_are_not_joined(monkeypatch):
    monkeypatch.setattr(shell_module.os, "system", lambda _cmd: 0)

    with patch.object(shell_module, "execute_commands", return_value=_single_success()) as run:
        shell(["ls", "pwd"])

    assert run.call_args.args[0] == ["ls", "pwd"]


def test_shell_parses_json_array_command_string():
    with patch.object(shell_module, "execute_commands", return_value=_single_success()) as run:
        shell('["printf one", "printf two"]', parallel=True)

    assert run.call_args.args[0] == ["printf one", "printf two"]
    assert run.call_args.args[1] is True


def test_shell_rejects_removed_ignore_errors_argument():
    with pytest.raises(TypeError, match="ignore_errors"):
        shell("false", ignore_errors=True)


def test_shell_requires_command():
    result = shell(None)

    assert result == {"status": "error", "content": [{"text": "Command is required"}]}


def test_validate_command_rejects_unsupported_shape():
    with pytest.raises(ValueError, match="Command must be string or dict"):
        validate_command(["printf hi"])
