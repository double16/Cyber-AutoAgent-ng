import pytest
from unittest.mock import MagicMock, patch
import shlex
from src.modules.tools.shell import shell


@pytest.fixture
def mock_shell_original():
    with patch("src.modules.tools.shell.shell_original") as mock:
        yield mock


@pytest.fixture
def mock_os_system():
    with patch("src.modules.tools.shell.os.system") as mock:
        yield mock


@pytest.fixture
def mock_os_path_isdir():
    with patch("src.modules.tools.shell.os.path.isdir") as mock:
        yield mock


@pytest.fixture
def mock_os_path_isfile():
    with patch("src.modules.tools.shell.os.path.isfile") as mock:
        yield mock


@pytest.fixture
def mock_os_access():
    with patch("src.modules.tools.shell.os.access") as mock:
        yield mock


def test_shell_single_command(mock_shell_original):
    command = "ls -la"
    shell(command)
    mock_shell_original.assert_called_once_with(
        command=command,
        parallel=False,
        ignore_errors=False,
        timeout=None,
        work_dir=None,
        non_interactive=True
    )


def test_shell_multiple_independent_commands(mock_shell_original, mock_os_system):
    # If the first and second commands are both known, it's treated as a list of independent commands
    command = ["ls", "pwd"]
    mock_os_system.return_value = 0  # 'which ls' and 'which pwd' both succeed

    shell(command)

    # It should not have joined them
    mock_shell_original.assert_called_once_with(
        command=command,
        parallel=False,
        ignore_errors=False,
        timeout=None,
        work_dir=None,
        non_interactive=True
    )


def test_shell_command_joining_heuristic(mock_shell_original, mock_os_system, mock_os_path_isdir, mock_os_path_isfile):
    # If first is known, but second is not known, it joins them
    command = ["ls", "-la", "/tmp"]

    def side_effect(cmd):
        if "which ls" in cmd:
            return 0
        if "which -la" in cmd:
            return 1
        return 1

    mock_os_system.side_effect = side_effect
    mock_os_path_isdir.return_value = False
    mock_os_path_isfile.return_value = False

    shell(command)

    expected_command = " ".join(map(shlex.quote, command))
    mock_shell_original.assert_called_once_with(
        command=expected_command,
        parallel=False,
        ignore_errors=False,
        timeout=None,
        work_dir=None,
        non_interactive=True
    )


def test_shell_timeout_normalization_and_clamping(mock_shell_original):
    # Test large timeout (> 2000)
    shell("ls", timeout=5000)  # 5000 // 1000 = 5, then clamped to [30, 900] -> 30
    mock_shell_original.assert_called_with(
        command="ls",
        parallel=False,
        ignore_errors=False,
        timeout=30,
        work_dir=None,
        non_interactive=True
    )

    # Test large timeout that takes multiple divisions
    shell("ls", timeout=3000000)  # 3000 -> 3, clamped -> 30
    mock_shell_original.assert_called_with(
        command="ls",
        parallel=False,
        ignore_errors=False,
        timeout=30,
        work_dir=None,
        non_interactive=True
    )

    # Test clamping high
    shell("ls", timeout=1200)  # 1200 clamped to 900
    mock_shell_original.assert_called_with(
        command="ls",
        parallel=False,
        ignore_errors=False,
        timeout=900,
        work_dir=None,
        non_interactive=True
    )

    # Test clamping low
    shell("ls", timeout=10)  # 10 clamped to 30
    mock_shell_original.assert_called_with(
        command="ls",
        parallel=False,
        ignore_errors=False,
        timeout=30,
        work_dir=None,
        non_interactive=True
    )

    # Test sane timeout
    shell("ls", timeout=100)  # 100 stays 100
    mock_shell_original.assert_called_with(
        command="ls",
        parallel=False,
        ignore_errors=False,
        timeout=100,
        work_dir=None,
        non_interactive=True
    )

    # Test list of objects (should not be joined by heuristic as they are not strings)
    command_objs = [{"command": "ls"}, {"command": "pwd"}]
    shell(command_objs)
    mock_shell_original.assert_called_with(
        command=command_objs,
        parallel=False,
        ignore_errors=False,
        timeout=None,
        work_dir=None,
        non_interactive=True
    )


def test_shell_command_joining_heuristic_edge_cases(mock_shell_original, mock_os_system, mock_os_path_isdir,
                                                    mock_os_path_isfile, mock_os_access):
    # Test second item is a directory -> SHOULD BE JOINED
    command = ["ls", "/tmp"]

    def side_effect(cmd):
        if "which ls" in cmd:
            return 0
        return 1

    mock_os_system.side_effect = side_effect
    mock_os_path_isdir.return_value = True
    shell(command)
    # The heuristic joins them if the second element is NOT a known command.
    # In this case, /tmp is a directory, so is_second_cmd_known is False.
    expected_command = " ".join(map(shlex.quote, command))
    mock_shell_original.assert_called_with(
        command=expected_command,
        parallel=False,
        ignore_errors=False,
        timeout=None,
        work_dir=None,
        non_interactive=True
    )

    # Test second item is a file but not executable -> SHOULD BE JOINED
    command = ["ls", "file.txt"]
    mock_os_path_isdir.return_value = False
    mock_os_path_isfile.return_value = True
    mock_os_access.return_value = False  # not executable
    shell(command)
    expected_command = " ".join(map(shlex.quote, command))
    mock_shell_original.assert_called_with(
        command=expected_command,
        parallel=False,
        ignore_errors=False,
        timeout=None,
        work_dir=None,
        non_interactive=True
    )

    # Test second item is NEITHER a command NOR a file/dir -> SHOULD BE JOINED
    command = ["ls", "--flag"]
    mock_os_path_isdir.return_value = False
    mock_os_path_isfile.return_value = False
    mock_os_system.side_effect = side_effect  # only 'ls' is known
    shell(command)
    expected_command = " ".join(map(shlex.quote, command))
    mock_shell_original.assert_called_with(
        command=expected_command,
        parallel=False,
        ignore_errors=False,
        timeout=None,
        work_dir=None,
        non_interactive=True
    )


def test_shell_pass_arguments(mock_shell_original):
    shell("ls", parallel=True, ignore_errors=True, work_dir="/tmp")
    mock_shell_original.assert_called_once_with(
        command="ls",
        parallel=True,
        ignore_errors=True,
        timeout=None,
        work_dir="/tmp",
        non_interactive=True
    )
