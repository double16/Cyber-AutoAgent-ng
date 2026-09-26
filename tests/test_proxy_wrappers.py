"""
Unit tests for scanning and security tool proxy replay / proxy wrapper scripts
=============================================================================

Tests the proxy wrappers for all CLI tools in docker/ that support proxy options.
Verifies that:
1. Proxy environment variables (HTTP_PROXY, HTTPS_PROXY, http_proxy, https_proxy)
   are unset before invoking the real binary.
2. Replay proxy / target proxy / standard proxy arguments are automatically injected
   when an intercepting proxy is configured.
3. User-supplied proxy arguments are not duplicated.
4. Arguments and exit codes are faithfully forwarded.
5. Helpful error messages are returned when the original binary is not found.
"""

import json
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

MOCK_PYTHON_SCRIPT = """#!/usr/bin/env python3
import sys
import json
import os

data = {
    "args": sys.argv[1:],
    "HTTP_PROXY": os.environ.get("HTTP_PROXY"),
    "HTTPS_PROXY": os.environ.get("HTTPS_PROXY"),
    "http_proxy": os.environ.get("http_proxy"),
    "https_proxy": os.environ.get("https_proxy"),
}
print(json.dumps(data))
if os.environ.get("MOCK_EXIT_CODE"):
    sys.exit(int(os.environ["MOCK_EXIT_CODE"]))
"""


@pytest.fixture
def wrapper_env(tmp_path: Path) -> Callable[[str], tuple[Path, Path]]:
    """
    Set up a sandbox directory with a wrapper script and a mock .orig binary.
    """
    docker_dir = Path(__file__).parent.parent / "docker" / "wrappers"

    def _setup_tool(tool_name: str) -> tuple[Path, Path]:
        wrapper_source = docker_dir / tool_name
        assert wrapper_source.exists(), f"Wrapper script {wrapper_source} must exist"

        tool_wrapper = tmp_path / tool_name
        tool_wrapper.write_text(wrapper_source.read_text())
        tool_wrapper.chmod(tool_wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        mock_orig = tmp_path / f"{tool_name}.orig"
        mock_orig.write_text(MOCK_PYTHON_SCRIPT)
        mock_orig.chmod(mock_orig.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        return tool_wrapper, mock_orig

    return _setup_tool


ALL_WRAPPERS = [
    "feroxbuster",
    "ffuf",
    "dirsearch",
    "wpscan",
    "katana",
    "nuclei",
    "httpx",
    "gobuster",
    "sqlmap",
    "commix",
    "nikto",
    "wafw00f",
    "wapiti",
    "wfuzz",
    "joomscan",
    "paramspider",
    "gospider",
    "dalfox",
    "sstimap",
    "lfimap",
    "clairvoyance",
    "subfinder",
    "cewl",
    "ncrack",
    "testssl",
    "arjun",
]


@pytest.mark.parametrize("tool_name", ALL_WRAPPERS)
def test_no_proxy_passthrough(wrapper_env: Callable[[str], tuple[Path, Path]], tool_name: str):
    """Verify that when no proxy environment variable is set, args are passed directly."""
    tool_wrapper, _ = wrapper_env(tool_name)
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}

    result = subprocess.run(
        [str(tool_wrapper), "--target", "http://example.com", "-v"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["args"] == ["--target", "http://example.com", "-v"]
    assert data["HTTP_PROXY"] is None
    assert data["HTTPS_PROXY"] is None


@pytest.mark.parametrize("tool_name", ALL_WRAPPERS)
def test_missing_orig_binary_error(wrapper_env: Callable[[str], tuple[Path, Path]], tool_name: str):
    """Verify that an informative error is displayed when the .orig binary is missing."""
    tool_wrapper, mock_orig = wrapper_env(tool_name)
    mock_orig.unlink()

    # Pass a non-existent explicit path override via REAL_BIN_NAME or test with isolated directory
    # Note: If .orig exists in /usr/bin or /usr/local/bin from a built container, we verify by pointing to a mock non-existent tool
    dummy_wrapper = tool_wrapper.parent / "nonexistent_tool"
    # Replace tool name in script content to nonexistent_tool
    wrapper_text = tool_wrapper.read_text().replace(f"{tool_name}.orig", "nonexistent_tool.orig")
    dummy_wrapper.write_text(wrapper_text)
    dummy_wrapper.chmod(tool_wrapper.stat().st_mode)

    result = subprocess.run(
        ["/bin/bash", str(dummy_wrapper), "--target", "http://example.com"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "nonexistent_tool.orig not found" in result.stderr


@pytest.mark.parametrize("tool_name", ALL_WRAPPERS)
def test_exit_code_preservation(wrapper_env: Callable[[str], tuple[Path, Path]], tool_name: str):
    """Verify that return exit code is propagated faithfully."""
    tool_wrapper, _ = wrapper_env(tool_name)
    env = {**os.environ, "MOCK_EXIT_CODE": "42"}

    result = subprocess.run(
        [str(tool_wrapper), "--target", "http://example.com"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 42


# Tool-specific proxy injection expectations
TOOL_PROXY_CASES: list[tuple[str, list[str], list[str], list[str], list[str]]] = [
    # (tool_name, base_args, expected_injected_args, user_proxy_args, expected_user_args)
    (
        "feroxbuster",
        ["-u", "http://example.com"],
        ["--replay-proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "--replay-proxy", "http://custom:8080"],
        ["-u", "http://example.com", "--replay-proxy", "http://custom:8080"],
    ),
    (
        "ffuf",
        ["-u", "http://example.com/FUZZ"],
        ["-replay-proxy", "http://127.0.0.1:8080", "-u", "http://example.com/FUZZ"],
        ["-u", "http://example.com/FUZZ", "-replay-proxy", "http://custom:8080"],
        ["-u", "http://example.com/FUZZ", "-replay-proxy", "http://custom:8080"],
    ),
    (
        "dirsearch",
        ["-u", "http://example.com"],
        ["--replay-proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "--replay-proxy", "http://custom:8080"],
        ["-u", "http://example.com", "--replay-proxy", "http://custom:8080"],
    ),
    (
        "wpscan",
        ["--url", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "--proxy-target-only", "--url", "http://example.com"],
        ["--url", "http://example.com", "--proxy", "http://custom:8080"],
        ["--url", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "katana",
        ["-u", "http://example.com"],
        ["-proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "-proxy", "http://custom:8080"],
        ["-u", "http://example.com", "-proxy", "http://custom:8080"],
    ),
    (
        "nuclei",
        ["-u", "http://example.com"],
        ["-proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "-proxy", "http://custom:8080"],
        ["-u", "http://example.com", "-proxy", "http://custom:8080"],
    ),
    (
        "httpx",
        ["-u", "http://example.com"],
        ["-proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "-proxy", "http://custom:8080"],
        ["-u", "http://example.com", "-proxy", "http://custom:8080"],
    ),
    (
        "gobuster",
        ["dir", "-u", "http://example.com", "-w", "wordlist.txt"],
        ["dir", "-u", "http://example.com", "-w", "wordlist.txt", "--proxy", "http://127.0.0.1:8080"],
        ["dir", "-u", "http://example.com", "--proxy", "http://custom:8080"],
        ["dir", "-u", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "sqlmap",
        ["-u", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "--proxy", "http://custom:8080"],
        ["-u", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "commix",
        ["--url", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "--url", "http://example.com"],
        ["--url", "http://example.com", "--proxy", "http://custom:8080"],
        ["--url", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "nikto",
        ["-h", "http://example.com"],
        ["-useproxy", "http://127.0.0.1:8080", "-h", "http://example.com"],
        ["-h", "http://example.com", "-useproxy", "http://custom:8080"],
        ["-h", "http://example.com", "-useproxy", "http://custom:8080"],
    ),
    (
        "wafw00f",
        ["http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "http://example.com"],
        ["http://example.com", "--proxy", "http://custom:8080"],
        ["http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "wapiti",
        ["-u", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "-p", "http://custom:8080"],
        ["-u", "http://example.com", "-p", "http://custom:8080"],
    ),
    (
        "wfuzz",
        ["http://example.com/FUZZ"],
        ["-p", "127.0.0.1:8080:HTTP", "http://example.com/FUZZ"],
        ["http://example.com/FUZZ", "-p", "custom:8080:HTTP"],
        ["http://example.com/FUZZ", "-p", "custom:8080:HTTP"],
    ),
    (
        "joomscan",
        ["-u", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "--proxy", "http://custom:8080"],
        ["-u", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "paramspider",
        ["-d", "example.com"],
        ["--proxy", "http://127.0.0.1:8080", "-d", "example.com"],
        ["-d", "example.com", "--proxy", "http://custom:8080"],
        ["-d", "example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "gospider",
        ["-s", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "-s", "http://example.com"],
        ["-s", "http://example.com", "--proxy", "http://custom:8080"],
        ["-s", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "dalfox",
        ["url", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "url", "http://example.com"],
        ["url", "http://example.com", "--proxy", "http://custom:8080"],
        ["url", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "sstimap",
        ["-u", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "-p", "http://custom:8080"],
        ["-u", "http://example.com", "-p", "http://custom:8080"],
    ),
    (
        "lfimap",
        ["-U", "http://example.com"],
        ["--proxy", "http://127.0.0.1:8080", "-U", "http://example.com"],
        ["-U", "http://example.com", "--proxy", "http://custom:8080"],
        ["-U", "http://example.com", "--proxy", "http://custom:8080"],
    ),
    (
        "clairvoyance",
        ["http://example.com/graphql"],
        ["--proxy", "http://127.0.0.1:8080", "http://example.com/graphql"],
        ["http://example.com/graphql", "-x", "http://custom:8080"],
        ["http://example.com/graphql", "-x", "http://custom:8080"],
    ),
    (
        "subfinder",
        ["-d", "example.com"],
        ["-proxy", "http://127.0.0.1:8080", "-d", "example.com"],
        ["-d", "example.com", "-proxy", "http://custom:8080"],
        ["-d", "example.com", "-proxy", "http://custom:8080"],
    ),
    (
        "cewl",
        ["http://example.com"],
        ["--proxy_host", "127.0.0.1", "--proxy_port", "8080", "http://example.com"],
        ["http://example.com", "--proxy_host", "custom", "--proxy_port", "9090"],
        ["http://example.com", "--proxy_host", "custom", "--proxy_port", "9090"],
    ),
    (
        "ncrack",
        ["--user", "admin", "127.0.0.1"],
        ["--proxy", "http://127.0.0.1:8080", "--user", "admin", "127.0.0.1"],
        ["--user", "admin", "127.0.0.1", "--proxy", "http://custom:8080"],
        ["--user", "admin", "127.0.0.1", "--proxy", "http://custom:8080"],
    ),
    (
        "testssl",
        ["http://example.com"],
        ["--proxy", "127.0.0.1:8080", "http://example.com"],
        ["http://example.com", "--proxy", "custom:8080"],
        ["http://example.com", "--proxy", "custom:8080"],
    ),
    (
        "arjun",
        ["-u", "http://example.com"],
        ["-oB", "127.0.0.1:8080", "-u", "http://example.com"],
        ["-u", "http://example.com", "-oB", "custom:8080"],
        ["-u", "http://example.com", "-oB", "custom:8080"],
    ),
]


@pytest.mark.parametrize(
    "tool_name,base_args,expected_injected_args,user_proxy_args,expected_user_args",
    TOOL_PROXY_CASES,
)
def test_proxy_injection_and_passthrough(
    wrapper_env: Callable[[str], tuple[Path, Path]],
    tool_name: str,
    base_args: list[str],
    expected_injected_args: list[str],
    user_proxy_args: list[str],
    expected_user_args: list[str],
):
    """Verify proxy injection when http_proxy is set, and non-duplication when user provides proxy."""
    tool_wrapper, _ = wrapper_env(tool_name)
    env = {
        **os.environ,
        "http_proxy": "http://127.0.0.1:8080",
        "https_proxy": "http://127.0.0.1:8080",
    }

    # Case 1: Injects proxy automatically
    result = subprocess.run(
        [str(tool_wrapper), *base_args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["args"] == expected_injected_args
    assert data["http_proxy"] is None
    assert data["https_proxy"] is None

    # Case 2: Does not duplicate user-supplied proxy argument
    result2 = subprocess.run(
        [str(tool_wrapper), *user_proxy_args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    data2 = json.loads(result2.stdout)
    assert data2["args"] == expected_user_args
    assert data2["http_proxy"] is None
    assert data2["https_proxy"] is None


def test_resolve_request_proxies():
    from modules.utils.proxy import resolve_request_proxies

    assert resolve_request_proxies({}) is None
    assert resolve_request_proxies({"HTTP_PROXY": "127.0.0.1:8080"}) == {
        "http": "http://127.0.0.1:8080",
        "https": "http://127.0.0.1:8080",
    }
    assert resolve_request_proxies({"HTTPS_PROXY": "http://10.0.0.1:8443"}) == {
        "https": "http://10.0.0.1:8443",
    }
    assert resolve_request_proxies(
        {"http_proxy": "http://127.0.0.1:8080", "https_proxy": "https://127.0.0.1:8443"}
    ) == {
        "http": "http://127.0.0.1:8080",
        "https": "https://127.0.0.1:8443",
    }
    assert resolve_request_proxies({"ALL_PROXY": "socks5://127.0.0.1:1080"}) == {
        "http": "socks5://127.0.0.1:1080",
        "https": "socks5://127.0.0.1:1080",
    }
