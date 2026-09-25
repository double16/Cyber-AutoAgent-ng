"""
Unit tests for scanning tool proxy replay wrapper scripts
=========================================================

Tests the proxy replay wrappers for scanning tools (feroxbuster, ffuf, dirsearch).
Verifies that:
1. Proxy environment variables (HTTP_PROXY, HTTPS_PROXY, http_proxy, https_proxy)
   are unset before invoking the real binary.
2. Replay proxy arguments are automatically injected when an intercepting proxy is configured.
3. User-supplied replay arguments are not duplicated.
4. Arguments and exit codes are faithfully forwarded.
5. Helpful error messages are returned when the original binary is not found.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

MOCK_BIN_SCRIPT = """#!/usr/bin/env bash
# Record args and proxy env vars to JSON output on stdout
cat <<EOF
{
  "args": $(printf '%s\n' "$@" | jq -R . | jq -s .),
  "HTTP_PROXY": "${HTTP_PROXY:-}",
  "HTTPS_PROXY": "${HTTPS_PROXY:-}",
  "http_proxy": "${http_proxy:-}",
  "https_proxy": "${https_proxy:-}"
}
EOF
"""

# Alternative mock that doesn't rely on jq if jq isn't installed
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
def wrapper_env(tmp_path: Path):
    """
    Set up a sandbox directory with a wrapper script and a mock .orig binary.
    """
    docker_dir = Path(__file__).parent.parent / "docker"

    def _setup_tool(tool_name: str):
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


class TestFeroxbusterWrapper:
    """Tests for the feroxbuster proxy wrapper."""

    def test_no_proxy_passthrough(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("feroxbuster")
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "-w", "wordlist.txt"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == ["-u", "http://example.com", "-w", "wordlist.txt"]
        assert data["HTTP_PROXY"] is None

    def test_http_proxy_injects_replay_proxy(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("feroxbuster")
        env = {
            **os.environ,
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
        }

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "-w", "wordlist.txt"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == [
            "--replay-proxy",
            "http://127.0.0.1:8080",
            "-u",
            "http://example.com",
            "-w",
            "wordlist.txt",
        ]
        # Verify proxy variables were unset
        assert data["HTTP_PROXY"] is None
        assert data["HTTPS_PROXY"] is None

    def test_https_proxy_only(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("feroxbuster")
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        env["HTTPS_PROXY"] = "http://127.0.0.1:9090"

        result = subprocess.run(
            [str(tool_wrapper), "-u", "https://example.com"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == [
            "--replay-proxy",
            "http://127.0.0.1:9090",
            "-u",
            "https://example.com",
        ]
        assert data["HTTPS_PROXY"] is None

    def test_lowercase_http_proxy(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("feroxbuster")
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        env["http_proxy"] = "http://127.0.0.1:8080"

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == [
            "--replay-proxy",
            "http://127.0.0.1:8080",
            "-u",
            "http://example.com",
        ]
        assert data["http_proxy"] is None

    def test_existing_replay_proxy_arg_not_duplicated(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("feroxbuster")
        env = {**os.environ, "HTTP_PROXY": "http://127.0.0.1:8080"}

        # Case 1: --replay-proxy provided
        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "--replay-proxy", "http://custom:8080"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == ["-u", "http://example.com", "--replay-proxy", "http://custom:8080"]
        assert data["HTTP_PROXY"] is None

        # Case 2: -P provided
        result2 = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "-P", "http://custom:8080"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data2 = json.loads(result2.stdout)
        assert data2["args"] == ["-u", "http://example.com", "-P", "http://custom:8080"]

        # Case 3: --burp-replay provided
        result3 = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "--burp-replay"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data3 = json.loads(result3.stdout)
        assert data3["args"] == ["-u", "http://example.com", "--burp-replay"]

    def test_preserves_exit_code(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("feroxbuster")
        env = {**os.environ, "MOCK_EXIT_CODE": "42"}

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 42

    def test_missing_orig_binary_error(self, wrapper_env):
        tool_wrapper, mock_orig = wrapper_env("feroxbuster")
        mock_orig.unlink()

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "feroxbuster.orig not found" in result.stderr


class TestFfufWrapper:
    """Tests for the ffuf proxy wrapper."""

    def test_no_proxy_passthrough(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("ffuf")
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com/FUZZ", "-w", "wordlist.txt"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == ["-u", "http://example.com/FUZZ", "-w", "wordlist.txt"]
        assert data["HTTP_PROXY"] is None

    def test_http_proxy_injects_replay_proxy(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("ffuf")
        env = {
            **os.environ,
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
        }

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com/FUZZ", "-w", "wordlist.txt"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == [
            "-replay-proxy",
            "http://127.0.0.1:8080",
            "-u",
            "http://example.com/FUZZ",
            "-w",
            "wordlist.txt",
        ]
        assert data["HTTP_PROXY"] is None
        assert data["HTTPS_PROXY"] is None

    def test_existing_replay_proxy_arg_not_duplicated(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("ffuf")
        env = {**os.environ, "HTTP_PROXY": "http://127.0.0.1:8080"}

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com/FUZZ", "-replay-proxy", "http://custom:8080"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == ["-u", "http://example.com/FUZZ", "-replay-proxy", "http://custom:8080"]
        assert data["HTTP_PROXY"] is None

    def test_missing_orig_binary_error(self, wrapper_env):
        tool_wrapper, mock_orig = wrapper_env("ffuf")
        mock_orig.unlink()

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com/FUZZ"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "ffuf.orig not found" in result.stderr


class TestDirsearchWrapper:
    """Tests for the dirsearch proxy wrapper."""

    def test_no_proxy_passthrough(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("dirsearch")
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "-e", "php,html"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == ["-u", "http://example.com", "-e", "php,html"]
        assert data["HTTP_PROXY"] is None

    def test_http_proxy_injects_replay_proxy(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("dirsearch")
        env = {
            **os.environ,
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
        }

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "-e", "php,html"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == [
            "--replay-proxy",
            "http://127.0.0.1:8080",
            "-u",
            "http://example.com",
            "-e",
            "php,html",
        ]
        assert data["HTTP_PROXY"] is None
        assert data["HTTPS_PROXY"] is None

    def test_existing_replay_proxy_arg_not_duplicated(self, wrapper_env):
        tool_wrapper, _ = wrapper_env("dirsearch")
        env = {**os.environ, "HTTP_PROXY": "http://127.0.0.1:8080"}

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com", "--replay-proxy", "http://custom:8080"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["args"] == ["-u", "http://example.com", "--replay-proxy", "http://custom:8080"]
        assert data["HTTP_PROXY"] is None

    def test_missing_orig_binary_error(self, wrapper_env):
        tool_wrapper, mock_orig = wrapper_env("dirsearch")
        mock_orig.unlink()

        result = subprocess.run(
            [str(tool_wrapper), "-u", "http://example.com"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "dirsearch.orig not found" in result.stderr
