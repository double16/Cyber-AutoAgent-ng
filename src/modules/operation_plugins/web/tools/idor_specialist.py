#!/usr/bin/env python3
"""
IDOR Specialist - Coordinated authorization / IDOR testing for a single endpoint.

Primary engine:
- IDOR-Forge (https://github.com/errorfiathck/IDOR-Forge)

Fallback engine:
- Lightweight Python IDOR heuristics:
  - Mutate candidate object identifiers (query/body/path)
  - Compare responses via status/length/similarity + sensitive keyword detection
  - Optional authorization replay using alt (low-priv/unauth) auth context

Returns:
- JSON only (no prose), intended for agents.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from subprocess import DEVNULL
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import urllib3
from strands import tool

# Test cases:
# XBEN-001-24
# XBEN-002-24
# XBEN-003-24
# XBEN-005-24
# XBEN-006-24
# XBEN-007-24
# XBEN-021-24
# XBEN-027-24
# XBEN-043-24
# XBEN-058-24
# XBEN-069-24
# XBEN-090-24
# XBEN-099-24
# XBEN-103-24

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

IDOR_FORGE_REPO_DIR = os.environ.get("IDOR_FORGE_DIR", "/opt/idor-forge")

_RE_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_RE_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

# Common ID-like parameter names (horizontal + vertical IDOR entry points)
_COMMON_ID_KEYS = {
    "id",
    "user",
    "user_id",
    "userid",
    "uid",
    "account",
    "account_id",
    "acct",
    "customer",
    "customer_id",
    "member",
    "member_id",
    "order",
    "order_id",
    "invoice",
    "invoice_id",
    "file",
    "file_id",
    "document",
    "document_id",
    "resource",
    "resource_id",
    "profile",
    "profile_id",
    "project",
    "project_id",
    "org",
    "org_id",
    "tenant",
    "tenant_id",
    "workspace",
    "workspace_id",
}

_DEFAULT_SENSITIVE_KEYWORDS = [
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "ssn",
    "social security",
    "credit card",
    "ccv",
    "pan",
    "dob",
    "date of birth",
    "email",
    "address",
    "phone",
]


def _b64(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, bytes):
        raw = x
    else:
        raw = str(x).encode("utf-8", errors="ignore")
    return base64.b64encode(raw).decode("ascii")


def _coerce_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="ignore")
    return str(x)


@dataclass
class RequestConfig:
    target_url: str
    http_method: str = "GET"
    cookies: Optional[Dict[str, str]] = None
    headers: Optional[Dict[str, str]] = None
    # Optional “low privilege / unauth” replay context
    alt_cookies: Optional[Dict[str, str]] = None
    alt_headers: Optional[Dict[str, str]] = None
    timeout: int = 15

    def inject_in_body(self) -> bool:
        return self.http_method.upper() in ["POST", "PUT", "PATCH", "DELETE"]


@tool
def idor_specialist(
        target_url: str,
        test_type: str = "comprehensive",
        parameters: str = None,
        http_method: str = "GET",
        cookies: Dict[str, str] = None,
        headers: Dict[str, str] = None,
        alt_cookies: Dict[str, str] = None,
        alt_headers: Dict[str, str] = None,
        sensitive_keywords: str = None,
        num_range: str = None,
) -> str:
    """
    Coordinated IDOR / authorization testing against a single URL.

    When to call:
    - You have a concrete endpoint and want to detect IDOR / access-control breaks quickly:
      horizontal (other users’ objects) and vertical (admin-only objects).
    - Especially useful when you can provide an alternate auth context (alt_cookies/alt_headers)
      to replay requests as a low-priv or unauth user.

    How to call:
    - target_url: full URL (include query string if known)
    - parameters: comma-separated parameter names to focus (optional). If omitted, auto-detect.
    - http_method: GET/POST/PUT/DELETE
    - cookies/headers: privileged (or current) context
    - alt_cookies/alt_headers: alternate low-priv or unauth context for replay comparisons (optional)
    - sensitive_keywords: JSON list string, e.g. '["password","email"]' (optional)
    - num_range: "start-end" to seed numeric mutations, e.g. "1-1000" (optional)

    Returns:
    - JSON (no prose), intended for agents.
    """
    if not target_url:
        raise ValueError("target_url is required")
    if not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"

    test_type = (test_type or "comprehensive").lower().strip()
    if test_type not in ["idor", "authz_replay", "param_discovery", "comprehensive"]:
        test_type = "comprehensive"

    sk = _DEFAULT_SENSITIVE_KEYWORDS
    if sensitive_keywords:
        try:
            parsed = json.loads(sensitive_keywords)
            if isinstance(parsed, list) and parsed:
                sk = [str(x) for x in parsed if str(x).strip()]
        except Exception:
            pass

    rc = RequestConfig(
        target_url=target_url,
        http_method=http_method,
        cookies=cookies,
        headers=headers,
        alt_cookies=alt_cookies,
        alt_headers=alt_headers,
    )

    results: Dict[str, Any] = {
        "target": target_url,
        "test_type": test_type,
        "http_method": rc.http_method,
        "parameters_provided": parameters,
        "tools": {"available": [], "failed": []},
        "parameters_discovered": [],
        "findings": [],
        "vulnerabilities": [],
        "intelligence": {
            "attack_vectors": [],
            "bypass_techniques": [],
            "exploitation_chains": [],
            "signals": {},
        },
        "recommendations": [],
        "counts": {},
        "errors": [],
        "evidence": {
            "idor_forge_stdout_b64": "",
            "idor_forge_stderr_b64": "",
            "fallback_notes": [],
        },
    }

    try:
        tools_setup = _setup_idor_tools()
        results["tools"] = tools_setup

        # Param discovery (cheap: URL query + common ID keys)
        if not parameters or test_type == "param_discovery":
            discovered = _idor_parameter_discovery(rc, parameters)
        else:
            discovered = [p.strip() for p in parameters.split(",") if p.strip()]
        results["parameters_discovered"] = discovered

        # Primary: IDOR-Forge (when available)
        forge_findings: List[Dict[str, Any]] = []
        if "idor-forge" in tools_setup["tools"] and test_type in ["idor", "comprehensive", "param_discovery", "authz_replay"]:
            ff, stdout_b64, stderr_b64 = _run_idor_forge(
                rc,
                tools_setup["tools"]["idor-forge"],
                scan_all_params=(not parameters),
                focus_params=discovered,
                sensitive_keywords=sk,
                num_range=num_range,
            )
            results["evidence"]["idor_forge_stdout_b64"] = stdout_b64
            results["evidence"]["idor_forge_stderr_b64"] = stderr_b64
            forge_findings = ff or []

            # Normalize + add
            for f in forge_findings:
                f.setdefault("tool", "idor-forge")
                f.setdefault("vulnerable", bool(f.get("vulnerable", False)))
                f.setdefault("url", target_url)
                f.setdefault("method", rc.http_method)
                results["findings"].append(f)

        # Fallback: Python heuristic scan (always runs if:
        #  - forge missing OR forge returned no findings OR test_type requests replay)
        want_replay = test_type in ["authz_replay", "comprehensive"] and (rc.alt_cookies or rc.alt_headers)
        if (not forge_findings) or want_replay or ("idor-forge" not in tools_setup["tools"]):
            py_findings = _python_idor_fallback(
                rc,
                focus_params=discovered,
                sensitive_keywords=sk,
                num_range=num_range,
                do_authz_replay=want_replay,
            )
            results["findings"].extend(py_findings)
            results["evidence"]["fallback_notes"].append(
                "python_fallback_ran=true"
            )

        vulns = [x for x in results["findings"] if x.get("vulnerable", False)]
        results["vulnerabilities"] = vulns

        results["intelligence"] = _analyze_idor_intelligence(results, has_alt=bool(rc.alt_cookies or rc.alt_headers))
        results["recommendations"] = _generate_idor_recommendations(test_type, results)

        results["counts"] = {
            "parameters_discovered": len(results.get("parameters_discovered", [])),
            "findings": len(results.get("findings", [])),
            "vulnerabilities": len(results.get("vulnerabilities", [])),
            "tools_available": len(results.get("tools", {}).get("tools", [])),
            "tools_failed": len(results.get("tools", {}).get("failed", [])),
        }

    except Exception as e:
        results["errors"].append(str(e))

    return json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True)


# ----------------------------
# Tool setup / discovery
# ----------------------------

def _setup_idor_tools() -> Dict[str, Any]:
    """
    Setup IDOR tooling.

    We treat IDOR-Forge as "available" if we can obtain its repo and locate IDOR-Forge.py.
    We install deps via requirements.txt into the current environment (pip3).
    """
    tools_status = {"tools": {}, "failed": []}

    # 1. Check if idor-forge is in PATH
    forge_path = shutil.which("idor-forge")
    if forge_path:
        tools_status["tools"]["idor-forge"] = [forge_path]
        return tools_status

    # 2. Attempt to fetch IDOR-Forge into a stable local path
    repo_dir = IDOR_FORGE_REPO_DIR
    script_path = os.path.join(repo_dir, "IDOR-Forge.py")
    reqs_path = os.path.join(repo_dir, "requirements.txt")

    if shutil.which("python3") is None:
        tools_status["failed"].extend(["idor-forge", "python3"])
        return tools_status

    try:
        for cmd in ["git", "pip3"]:
            if shutil.which(cmd) is None:
                tools_status["failed"].append(cmd)
        if tools_status["failed"]:
            tools_status["failed"].append("idor-forge")
            return tools_status

        if not os.path.isdir(repo_dir) or not os.path.isfile(script_path):
            subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/errorfiathck/IDOR-Forge.git", repo_dir],
                stdin=DEVNULL,
                capture_output=True,
                check=True,
                timeout=120,
            )

        if os.path.isfile(script_path):
            # Best-effort deps install
            if os.path.isfile(reqs_path):
                subprocess.run(
                    ["pip3", "install", "-r", reqs_path],
                    stdin=DEVNULL,
                    capture_output=True,
                    check=True,
                    timeout=180,
                )
            tools_status["tools"]["idor-forge"] = ["python3", script_path]
        else:
            tools_status["failed"].append("idor-forge")
    except Exception:
        tools_status["failed"].append("idor-forge")

    return tools_status


def _idor_parameter_discovery(request_config: RequestConfig, provided_params: Optional[str]) -> List[str]:
    discovered: set[str] = set()

    # Provided explicit list
    if provided_params:
        for p in str(provided_params).split(","):
            p = p.strip()
            if p:
                discovered.add(p)

    # From URL query
    try:
        parsed = urlparse(request_config.target_url)
        if parsed.query:
            qs = parse_qs(parsed.query)
            for k in qs.keys():
                if k:
                    discovered.add(k)
    except Exception:
        pass

    # If nothing, seed with common ID-ish keys (agent can prune later)
    if not discovered:
        discovered.update(sorted(_COMMON_ID_KEYS)[:10])

    # Prefer likely ID keys first (stable ordering)
    ordered = sorted(discovered, key=lambda x: (0 if x.lower() in _COMMON_ID_KEYS else 1, x.lower()))
    return ordered


# ----------------------------
# Primary engine: IDOR-Forge
# ----------------------------

def _run_idor_forge(
        request_config: RequestConfig,
        tool_cmd: List[str],
        scan_all_params: bool,
        focus_params: List[str],
        sensitive_keywords: List[str],
        num_range: Optional[str],
) -> Tuple[List[Dict[str, Any]], str, str]:
    """
    Run IDOR-Forge and parse its JSON output if possible.

    IDOR-Forge supports:
    - -u/--url, -p/--parameters, -m/--method, -d/--delay, -o/--output, --output-format json
    - --headers JSON, --test-values JSON, --sensitive-keywords JSON, --num-range start-end
    - plus newer flags in repo code such as --request-type, --auth-type, etc. (we keep it minimal/stable)
    """
    if not tool_cmd:
        return [], "", _b64("idor-forge tool command not provided")

    out_json_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="idor_forge_", suffix=".json", delete=False) as f:
            out_json_path = f.name

        # Build headers JSON for forge
        hdrs = request_config.headers or {}
        if request_config.cookies:
            # Some tools accept cookies in headers; forge primarily uses requests session,
            # but the CLI shows headers support only. Encode cookies into header for best compatibility.
            cookie_header = "; ".join([f"{k}={v}" for k, v in request_config.cookies.items()])
            if cookie_header:
                hdrs = dict(hdrs)
                hdrs.setdefault("Cookie", cookie_header)

        cmd = tool_cmd + [
            "-u",
            request_config.target_url,
            "-m",
            request_config.http_method.upper(),
            "-o",
            out_json_path,
            "--output-format",
            "json",
            "-d",
            "0.1",
            "--headers",
            json.dumps(hdrs or {}),
            "--sensitive-keywords",
            json.dumps(sensitive_keywords or []),
            "--test-values",
            json.dumps(_default_test_values_from_url(request_config.target_url)),
        ]

        if scan_all_params:
            cmd.append("-p")

        if num_range:
            cmd.extend(["--num-range", str(num_range)])

        # Run
        p = subprocess.run(cmd, capture_output=True, text=True, stdin=DEVNULL, timeout=420)
        stdout = _coerce_str(p.stdout)
        stderr = _coerce_str(p.stderr)

        # Prefer file output
        findings = _parse_idor_forge_json_file(out_json_path, focus_params=focus_params)

        # If empty, try to salvage JSON-looking stdout
        if not findings and stdout:
            findings = _parse_idor_forge_json_text(stdout, focus_params=focus_params)

        # Normalize shape
        normed = []
        for f in findings:
            normed.append(_normalize_idor_finding(f, tool="idor-forge"))

        return normed, _b64(stdout), _b64(stderr)

    except subprocess.TimeoutExpired as e:
        return [], _b64(_coerce_str(getattr(e, "stdout", ""))), _b64(_coerce_str(getattr(e, "stderr", "")))
    except Exception as e:
        return [], "", _b64(str(e))
    finally:
        if out_json_path and os.path.exists(out_json_path):
            try:
                os.unlink(out_json_path)
            except Exception:
                pass


def _parse_idor_forge_json_file(path: str, focus_params: List[str]) -> List[Dict[str, Any]]:
    if not path or not os.path.isfile(path) or os.stat(path).st_size <= 0:
        return []
    try:
        with open(path, "rb") as f:
            data = json.loads(f.read())

        # IDOR-Forge output schemas vary by version; tolerate a few shapes:
        # - list of results
        # - dict with "results"/"findings"
        candidates: List[Dict[str, Any]] = []
        if isinstance(data, list):
            candidates = [x for x in data if isinstance(x, dict)]
        elif isinstance(data, dict):
            for k in ["results", "findings", "vulnerabilities", "issues", "data"]:
                if k in data and isinstance(data[k], list):
                    candidates = [x for x in data[k] if isinstance(x, dict)]
                    break

        # Focus filter (if entries include param)
        if focus_params:
            fp = set([p.lower() for p in focus_params if p])
            filtered = []
            for c in candidates:
                param = str(c.get("parameter") or c.get("param") or "").strip()
                if not param or param.lower() in fp:
                    filtered.append(c)
            return filtered

        return candidates
    except Exception:
        return []


def _parse_idor_forge_json_text(text: str, focus_params: List[str]) -> List[Dict[str, Any]]:
    if not text:
        return []
    text = _RE_ANSI_ESCAPE.sub("", text)
    m = _RE_JSON_OBJECT.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        if isinstance(data, list):
            out = [x for x in data if isinstance(x, dict)]
        elif isinstance(data, dict):
            out = []
            for k in ["results", "findings", "vulnerabilities", "issues", "data"]:
                if k in data and isinstance(data[k], list):
                    out = [x for x in data[k] if isinstance(x, dict)]
                    break
        else:
            out = []
        if focus_params:
            fp = set([p.lower() for p in focus_params if p])
            filtered = []
            for c in out:
                param = str(c.get("parameter") or c.get("param") or "").strip()
                if not param or param.lower() in fp:
                    filtered.append(c)
            return filtered
        return out
    except Exception:
        return []


# ----------------------------
# Fallback engine: Python heuristics
# ----------------------------

def _python_idor_fallback(
        request_config: RequestConfig,
        focus_params: List[str],
        sensitive_keywords: List[str],
        num_range: Optional[str],
        do_authz_replay: bool,
) -> List[Dict[str, Any]]:
    """
    Simple, high-signal fallback:
    - Create an "authorized baseline" request (current cookies/headers) for the endpoint.
    - Mutate candidate identifiers in query/body/path and compare:
        - status change (e.g., 200 for other IDs)
        - similarity drop (indicates a different object)
        - sensitive keywords presence
    - Optional authz replay: replay baseline + mutated as alt (low-priv/unauth) and see if alt matches authorized.
    """
    findings: List[Dict[str, Any]] = []

    method = request_config.http_method.upper()
    target = request_config.target_url

    # Identify candidate ID locations
    parsed = urlparse(target)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    candidate_params = _pick_candidate_params(qs, focus_params)

    path_id = _extract_trailing_numeric_path_id(parsed.path)

    # Build baseline authorized response
    baseline = _send_request(
        request_config,
        url=target,
        method=method,
        params=None,
        data=None,
        use_alt=False,
    )

    if baseline is None:
        return [
            {
                "vulnerable": False,
                "tool": "python_fallback",
                "finding_type": "baseline_failed",
                "url": target,
                "method": method,
                "evidence": "Could not fetch baseline response with provided auth context",
            }
        ]

    baseline_text = baseline.text or ""
    baseline_hash = _hash_text(baseline_text)
    baseline_len = len(baseline_text)
    baseline_status = baseline.status_code

    # Prepare mutation values
    mutations = _build_id_mutations(qs, path_id=path_id, num_range=num_range)

    # 1) Param mutations
    for p in candidate_params:
        orig_val = (qs.get(p, [""])[0] if p in qs else "")
        for mv in mutations:
            if mv is None:
                continue
            mutated_url = _add_or_replace_query_param(target, p, str(mv))
            resp = _send_request(
                request_config,
                url=mutated_url,
                method=method,
                params=None,
                data=None,
                use_alt=False,
            )
            if resp is None:
                continue

            f = _evaluate_mutation(
                target_url=mutated_url,
                method=method,
                location="query",
                key=p,
                original=str(orig_val),
                mutated=str(mv),
                baseline_status=baseline_status,
                baseline_len=baseline_len,
                baseline_hash=baseline_hash,
                baseline_text=baseline_text,
                resp=resp,
                sensitive_keywords=sensitive_keywords,
            )
            if f:
                findings.append(f)

            # Optional authz replay: compare alt user
            if do_authz_replay:
                alt_resp = _send_request(
                    request_config,
                    url=mutated_url,
                    method=method,
                    params=None,
                    data=None,
                    use_alt=True,
                )
                replay_f = _evaluate_authz_replay(
                    url=mutated_url,
                    method=method,
                    key=p,
                    location="query",
                    resp_auth=resp,
                    resp_alt=alt_resp,
                )
                if replay_f:
                    findings.append(replay_f)

    # 2) Path ID mutation (e.g., /users/123)
    if path_id is not None:
        for mv in mutations:
            if mv is None:
                continue
            mutated_path = _replace_trailing_numeric_path_id(parsed.path, str(mv))
            mutated_url = urlunparse((parsed.scheme, parsed.netloc, mutated_path, parsed.params, parsed.query, parsed.fragment))
            resp = _send_request(
                request_config,
                url=mutated_url,
                method=method,
                params=None,
                data=None,
                use_alt=False,
            )
            if resp is None:
                continue

            f = _evaluate_mutation(
                target_url=mutated_url,
                method=method,
                location="path",
                key="(path_id)",
                original=str(path_id),
                mutated=str(mv),
                baseline_status=baseline_status,
                baseline_len=baseline_len,
                baseline_hash=baseline_hash,
                baseline_text=baseline_text,
                resp=resp,
                sensitive_keywords=sensitive_keywords,
            )
            if f:
                findings.append(f)

            if do_authz_replay:
                alt_resp = _send_request(
                    request_config,
                    url=mutated_url,
                    method=method,
                    params=None,
                    data=None,
                    use_alt=True,
                )
                replay_f = _evaluate_authz_replay(
                    url=mutated_url,
                    method=method,
                    key="(path_id)",
                    location="path",
                    resp_auth=resp,
                    resp_alt=alt_resp,
                )
                if replay_f:
                    findings.append(replay_f)

    # De-dupe (url+key+mutated+type)
    dedup: List[Dict[str, Any]] = []
    seen = set()
    for f in findings:
        k = (
            f.get("url"),
            f.get("parameter"),
            f.get("mutated_value"),
            f.get("finding_type"),
            bool(f.get("vulnerable", False)),
        )
        if k not in seen:
            dedup.append(f)
            seen.add(k)
    return dedup


def _send_request(
        request_config: RequestConfig,
        url: str,
        method: str,
        params: Optional[Dict[str, Any]],
        data: Optional[Dict[str, Any]],
        use_alt: bool,
) -> Optional[requests.Response]:
    try:
        headers = (request_config.alt_headers if use_alt else request_config.headers) or {}
        cookies = (request_config.alt_cookies if use_alt else request_config.cookies) or {}

        # If caller passed params/data, honor; otherwise rely on URL encoding already done.
        resp = requests.request(
            method=method,
            url=url,
            params=params,
            data=data,
            headers=headers,
            cookies=cookies,
            timeout=request_config.timeout,
            allow_redirects=True,
            verify=False,
        )
        return resp
    except Exception:
        return None


def _evaluate_mutation(
        target_url: str,
        method: str,
        location: str,
        key: str,
        original: str,
        mutated: str,
        baseline_status: int,
        baseline_len: int,
        baseline_hash: str,
        baseline_text: str,
        resp: requests.Response,
        sensitive_keywords: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Heuristic signals:
    - status is 200 when baseline is 200, but content differs materially (potential other object)
    - baseline 403/401 but mutated is 200 (classic unauthorized access)
    - sensitive keywords appear in mutated response (possible data exposure)
    """
    text = resp.text or ""
    status = resp.status_code
    h = _hash_text(text)
    ln = len(text)

    # Similarity (noise-tolerant)
    sim = SequenceMatcher(None, baseline_text[:8000], text[:8000]).ratio() if baseline_text or text else 0.0

    # keyword presence
    lower = text.lower()
    kw_hits = [k for k in sensitive_keywords if k and str(k).lower() in lower]
    kw_hit = bool(kw_hits)

    # Basic “changed object” heuristic: same success status but substantially different body
    looks_like_other_object = (
            status in (200, 201) and baseline_status in (200, 201) and h != baseline_hash and sim < 0.85 and (ln / max(baseline_len, 1) < 0.7 or ln / max(baseline_len, 1) > 1.3)
    )

    # Access control break heuristic: baseline forbidden but mutated ok
    authz_break = baseline_status in (401, 403) and status in (200, 201)

    # If response is identical, ignore
    if h == baseline_hash and status == baseline_status:
        return None

    # Decide vulnerability vs candidate
    vulnerable = bool(authz_break or looks_like_other_object or (kw_hit and status in (200, 201)))

    if not vulnerable and status in (200, 201) and h != baseline_hash and sim < 0.92:
        finding_type = "idor_candidate"
    elif authz_break:
        finding_type = "authz_bypass_candidate"
    elif looks_like_other_object:
        finding_type = "idor_likely"
    elif kw_hit:
        finding_type = "sensitive_data_signal"
    else:
        return None

    evidence_bits = [
        f"baseline_status={baseline_status}",
        f"mutated_status={status}",
        f"similarity={round(sim, 3)}",
        f"len_ratio={round(ln / max(baseline_len, 1), 3)}",
    ]
    if kw_hits:
        evidence_bits.append(f"keywords={kw_hits[:5]}")

    return {
        "vulnerable": vulnerable,
        "tool": "python_fallback",
        "finding_type": finding_type,
        "url": target_url,
        "method": method,
        "parameter": key,
        "param_location": location,
        "original_value": original,
        "mutated_value": mutated,
        "signals": {
            "baseline_status": baseline_status,
            "mutated_status": status,
            "similarity": sim,
            "baseline_len": baseline_len,
            "mutated_len": ln,
            "keyword_hits": kw_hits[:10],
        },
        "evidence": "; ".join(evidence_bits),
    }


def _evaluate_authz_replay(
        url: str,
        method: str,
        key: str,
        location: str,
        resp_auth: Optional[requests.Response],
        resp_alt: Optional[requests.Response],
) -> Optional[Dict[str, Any]]:
    """
    Authorization replay signal:
    - alt user gets same (or near-same) response as auth user for a request expected to be restricted
    """
    if resp_auth is None or resp_alt is None:
        return None

    a_txt = resp_auth.text or ""
    b_txt = resp_alt.text or ""

    # Status parity plus high similarity is suspicious for restricted objects
    sim = SequenceMatcher(None, a_txt[:8000], b_txt[:8000]).ratio() if (a_txt or b_txt) else 0.0
    status_same = resp_auth.status_code == resp_alt.status_code

    # If both are errors, ignore
    if resp_auth.status_code in (401, 403) and resp_alt.status_code in (401, 403):
        return None

    if status_same and sim >= 0.95 and resp_alt.status_code in (200, 201):
        return {
            "vulnerable": True,
            "tool": "python_fallback",
            "finding_type": "authz_replay_match",
            "url": url,
            "method": method,
            "parameter": key,
            "param_location": location,
            "signals": {
                "auth_status": resp_auth.status_code,
                "alt_status": resp_alt.status_code,
                "similarity": sim,
            },
            "evidence": f"alt response matches auth response (status={resp_alt.status_code}, similarity={round(sim,3)})",
        }

    # If alt succeeded but auth failed, also interesting (weird role inversion)
    if resp_auth.status_code in (401, 403) and resp_alt.status_code in (200, 201):
        return {
            "vulnerable": False,
            "tool": "python_fallback",
            "finding_type": "role_inversion_signal",
            "url": url,
            "method": method,
            "parameter": key,
            "param_location": location,
            "signals": {
                "auth_status": resp_auth.status_code,
                "alt_status": resp_alt.status_code,
                "similarity": sim,
            },
            "evidence": "alt succeeded where auth context did not (check which identity is actually privileged)",
        }

    return None


# ----------------------------
# Intelligence / Recommendations
# ----------------------------

def _analyze_idor_intelligence(results: Dict[str, Any], has_alt: bool) -> Dict[str, Any]:
    vulns = [r for r in results.get("findings", []) if r.get("vulnerable", False)]
    signals = {
        "has_alt_auth_context": has_alt,
        "has_confirmed_vulns": bool(vulns),
        "vuln_types": {},
    }
    vectors: set[str] = set()
    bypass: set[str] = set()
    chains: List[str] = []

    for v in vulns:
        t = str(v.get("finding_type", "unknown"))
        signals["vuln_types"][t] = signals["vuln_types"].get(t, 0) + 1
        vectors.add("idor")
        if "authz" in t:
            vectors.add("authz_bypass")

    # Heuristic bypass hints
    if has_alt:
        bypass.add("session_role_swap")
    if results.get("parameters_discovered"):
        bypass.add("object_id_enumeration")

    if "authz_bypass_candidate" in signals["vuln_types"] or "authz_replay_match" in signals["vuln_types"]:
        chains.append("authz_bypass=>data_access")
    if "idor_likely" in signals["vuln_types"]:
        chains.append("idor=>horizontal_data_access")

    return {
        "attack_vectors": sorted(list(vectors)),
        "bypass_techniques": sorted(list(bypass)),
        "exploitation_chains": chains,
        "signals": signals,
    }


def _generate_idor_recommendations(test_type: str, results: Dict[str, Any]) -> List[str]:
    recs: List[str] = []
    vulns = [r for r in results.get("findings", []) if r.get("vulnerable", False)]
    has_alt = bool(results.get("intelligence", {}).get("signals", {}).get("has_alt_auth_context"))

    if test_type == "param_discovery" and results.get("parameters_discovered"):
        return recs

    if not vulns:
        recs.extend(
            [
                "provide_low_priv_context_for_replay",
                "expand_object_id_mutations_range",
                "test_adjacent_endpoints_same_object_ids",
                "try_put_delete_on_object_endpoints",
                "capture_burp_traffic_and_run_matrix_testing",
            ]
        )
        return recs

    recs.extend(
        [
            "capture_repro_steps",
            "minimize_to_single_object_id_poc",
            "confirm_horizontal_vs_vertical_scope",
        ]
    )

    if has_alt:
        recs.append("confirm_with_role_matrix_requests")
    else:
        recs.append("add_alt_auth_context_and_replay")

    recs.extend(
        [
            "enumerate_object_id_space_safely",
            "attempt_bulk_object_access_validation",
            "check_for_indirect_object_refs_and_uuid_tokens",
        ]
    )

    # De-dupe preserving order
    out: List[str] = []
    seen: set[str] = set()
    for r in recs:
        if r not in seen:
            out.append(r)
            seen.add(r)
    return out


# ----------------------------
# Small helpers
# ----------------------------

def _hash_text(s: str) -> str:
    # fast-ish stable hash without importing hashlib everywhere
    return str(hash(s))


def _add_or_replace_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs[key] = [value]
    new_query = urlencode(qs, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _extract_trailing_numeric_path_id(path: str) -> Optional[int]:
    try:
        parts = [p for p in (path or "").split("/") if p]
        if not parts:
            return None
        last = parts[-1]
        if last.isdigit():
            return int(last)
        return None
    except Exception:
        return None


def _replace_trailing_numeric_path_id(path: str, new_value: str) -> str:
    parts = (path or "").split("/")
    if not parts:
        return path
    # Replace last non-empty segment if numeric
    idx = None
    for i in range(len(parts) - 1, -1, -1):
        if parts[i]:
            idx = i
            break
    if idx is None:
        return path
    if parts[idx].isdigit():
        parts[idx] = str(new_value)
    return "/".join(parts)


def _pick_candidate_params(qs: Dict[str, List[str]], focus_params: List[str]) -> List[str]:
    # if focus provided, use those first
    fp = [p for p in (focus_params or []) if p]
    if fp:
        return fp

    # else pick keys that look ID-ish
    keys = list(qs.keys())
    if not keys:
        return sorted(list(_COMMON_ID_KEYS))[:8]

    likely = [k for k in keys if k and k.lower() in _COMMON_ID_KEYS]
    return likely or keys[:8]


def _default_test_values_from_url(url: str) -> List[Any]:
    """
    Seed IDOR-Forge with meaningful values:
    - If URL already contains numeric ids, include +/- 1
    - Otherwise use a small safe set
    """
    vals: set[Any] = {0, 1, 2, 3, 4, 5, 9, 10, 11, 42, 99, 100, 101, 1337}
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for _, v in qs.items():
            if not v:
                continue
            sv = str(v[0])
            if sv.isdigit():
                n = int(sv)
                for d in [-2, -1, 1, 2, 10]:
                    if n + d >= 0:
                        vals.add(n + d)
    except Exception:
        pass
    return sorted(list(vals))[:50]


def _build_id_mutations(qs: Dict[str, List[str]], path_id: Optional[int], num_range: Optional[str]) -> List[Any]:
    muts: set[Any] = set()

    # Baseline-derived
    for _, v in (qs or {}).items():
        if v and str(v[0]).isdigit():
            n = int(v[0])
            muts.update([n - 2, n - 1, n + 1, n + 2, n + 10, n + 100])
    if path_id is not None:
        muts.update([path_id - 2, path_id - 1, path_id + 1, path_id + 2, path_id + 10, path_id + 100])

    # Range-derived
    if num_range and "-" in str(num_range):
        try:
            a, b = str(num_range).split("-", 1)
            start, end = int(a.strip()), int(b.strip())
            # sample evenly (avoid huge loops)
            if start <= end:
                muts.update([start, start + 1, start + 2, end - 2, end - 1, end])
                mid = (start + end) // 2
                muts.update([mid - 1, mid, mid + 1])
        except Exception:
            pass

    # Generic
    muts.update([0, 1, 2, 3, 4, 5, 9, 10, 11, 42, 99, 100, 101, 1337, 9999, 10000])

    # sanitize (no negatives)
    out = sorted([m for m in muts if isinstance(m, int) and m >= 0])
    return out[:60]


def _normalize_idor_finding(f: Dict[str, Any], tool: str) -> Dict[str, Any]:
    """
    Normalize external-tool findings into a stable schema.
    """
    if not isinstance(f, dict):
        return {"tool": tool, "vulnerable": False, "raw_b64": _b64(f)}

    param = f.get("parameter") or f.get("param") or f.get("key") or ""
    vul = bool(f.get("vulnerable", False))

    return {
        "vulnerable": vul,
        "tool": tool,
        "finding_type": f.get("finding_type") or f.get("type") or ("idor_likely" if vul else "scan_result"),
        "url": f.get("url") or f.get("target") or "",
        "method": f.get("method") or "",
        "parameter": str(param) if param is not None else "",
        "param_location": f.get("param_location") or f.get("location") or "",
        "original_value": f.get("original_value") or "",
        "mutated_value": f.get("mutated_value") or "",
        "signals": f.get("signals") if isinstance(f.get("signals"), dict) else {},
        "evidence": f.get("evidence") or f.get("description") or "",
        "raw_b64": _b64(json.dumps(f, ensure_ascii=False)),
    }


# ----------------------------
# CLI entrypoint
# ----------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Run the IDOR Specialist tool against a target URL")
    parser.add_argument("target_url", help="Target URL (with or without scheme)")
    parser.add_argument("--test-type", dest="test_type", default="comprehensive",
                        choices=["idor", "authz_replay", "param_discovery", "comprehensive"])
    parser.add_argument("--parameters", default=None, help="Comma-separated list of parameter names to focus")
    parser.add_argument("--method", dest="http_method", default="GET", help="HTTP method (default: GET)")
    parser.add_argument("--header", dest="headers", action="append", default=None, help="Header 'Name: value' (authn must be repeatable)")
    parser.add_argument("--cookie", dest="cookies", action="append", default=None, help="Cookie 'name=value' (authn must be repeatable)")
    parser.add_argument("--alt-header", dest="alt_headers", action="append", default=None, help="Alt header 'Name: value' (authn must be repeatable)")
    parser.add_argument("--alt-cookie", dest="alt_cookies", action="append", default=None, help="Alt cookie 'name=value' (authn must be repeatable)")
    parser.add_argument("--sensitive-keywords", default=None, help='JSON list string, e.g. \'["password","email"]\'')
    parser.add_argument("--num-range", default=None, help='Numeric range "start-end", e.g. "1-1000"')

    args = parser.parse_args()

    def _parse_headers(items: Optional[List[str]]) -> Optional[Dict[str, str]]:
        if not items:
            return None
        out: Dict[str, str] = {}
        for item in items:
            if not item or ":" not in item:
                continue
            name, value = item.split(":", 1)
            name, value = name.strip(), value.strip()
            if name:
                out[name] = value
        return out or None

    def _parse_cookies(items: Optional[List[str]]) -> Optional[Dict[str, str]]:
        if not items:
            return None
        out: Dict[str, str] = {}
        for item in items:
            if not item or "=" not in item:
                continue
            name, value = item.split("=", 1)
            name, value = name.strip(), value.strip()
            if name:
                out[name] = value
        return out or None

    headers = _parse_headers(args.headers)
    cookies = _parse_cookies(args.cookies)
    alt_headers = _parse_headers(args.alt_headers)
    alt_cookies = _parse_cookies(args.alt_cookies)

    print(
        idor_specialist(
            args.target_url,
            test_type=args.test_type,
            parameters=args.parameters,
            http_method=args.http_method,
            headers=headers,
            cookies=cookies,
            alt_headers=alt_headers,
            alt_cookies=alt_cookies,
            sensitive_keywords=args.sensitive_keywords,
            num_range=args.num_range,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
