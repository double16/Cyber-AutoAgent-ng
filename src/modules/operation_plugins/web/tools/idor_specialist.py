#!/usr/bin/env python3
"""
IDOR Specialist - Coordinated authorization / IDOR testing for a single endpoint.

- Mutate candidate object identifiers (query/body/path)
- Compare responses via status/length/similarity
- JSON structure and content similarity analysis
- Optional authorization replay using alt (low-priv/unauth) auth context

Returns:
- JSON only (no prose), intended for agents.
"""

import argparse
import json
import sys
import time
import random
import traceback
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple, Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import urllib3
from strands import tool, ToolContext

#
# import from advanced_payload_coordinator
#
import importlib.util
from pathlib import Path

module_path = Path(__file__).resolve().parent / "advanced_payload_coordinator.py"

spec = importlib.util.spec_from_file_location("advanced_payload_coordinator", str(module_path))
assert spec is not None and spec.loader is not None, "Failed to load advanced_payload_coordinator.py"
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)
advanced_parameter_discovery = exporter.advanced_parameter_discovery
apc_request_config = exporter.RequestConfig
#
#
#

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
    request_type: str = "query"
    auth_type: str = "basic"

    def inject_in_body(self) -> bool:
        return self.http_method.upper() in ["POST", "PUT", "PATCH", "DELETE"] or self.request_type in ["json",
                                                                                                       "graphql"]


@tool(context=True)
def idor_specialist(
        target_url: str,
        test_type: Literal["comprehensive", "idor", "authz_replay", "param_discovery"] = "comprehensive",
        parameters: Optional[str] = None,
        http_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET",
        cookies: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        alt_cookies: Optional[Dict[str, str]] = None,
        alt_headers: Optional[Dict[str, str]] = None,
        test_values: Optional[str] = None,
        login_url: Optional[str] = None,
        credentials: Optional[str] = None,
        login_method: Optional[Literal["GET", "POST"]] = None,
        num_range: Optional[str] = None,
        multi_credentials: Optional[str] = None,
        evasion: bool = False,
        request_type: Optional[Literal["query", "json", "graphql"]] = None,
        auth_type: Optional[Literal["basic", "oauth", "jwt"]] = None,
        tool_context: Optional[ToolContext] = None,
) -> str:
    """
    Coordinated IDOR (Insecure Direct Object Reference) / authorization testing against a single URL.

    When to call:
    - You have a concrete endpoint (URL) and want to detect IDOR / authorization bypasses.
    - Works with a single auth context (detects IDOR via parameter mutation) or two auth contexts
      (compares high-priv vs low-priv/unauth responses).
    - Use when you see numeric IDs, UUIDs, or usernames in query strings, JSON bodies, or URL paths.

    How to call:
    - target_url: full URL (include query string if known)
    - test_type: "idor", "authz_replay", "param_discovery", or "comprehensive" (default)
    - parameters: comma-separated parameter names to focus (optional). If omitted, auto-detect.
    - http_method: GET/POST/PUT/DELETE
    - cookies/headers: privileged (or current) context
    - alt_cookies/alt_headers: alternate low-priv or unauth context for replay comparisons (optional)
    - test_values: JSON list of custom values to test as payloads (optional)
    - login_url: URL for the login page (optional)
    - credentials: Login credentials in JSON format (e.g., '{"username": "admin", "password": "password"}') (optional)
    - login_method: HTTP method to use for login (default: POST) (optional)
    - num_range: "start-end" to seed numeric mutations, e.g. "1-1000" (optional)
    - multi_credentials: JSON list of multiple credentials for multi-user testing (optional)
    - evasion: Enable evasion techniques (e.g., jitter, UA rotation) (optional)
    - request_type: "query", "json", or "graphql" (optional)
    - auth_type: "basic", "oauth", or "jwt" (optional)

    Returns:
    - JSON. Key fields:
      - parameters_discovered: list of parameters to focus testing.
      - findings: per-test records (include tool, parameter, finding_type, signals, evidence).
      - vulnerabilities: subset of findings where vulnerable=true (primary signal).
      - intelligence: attack_vectors / bypass_techniques / exploitation_chains (routing hints).
      - recommendations: next-step action tags (agent routing, not remediation).
      - counts / errors: quick health + triage.
    """
    if not target_url:
        raise ValueError("target_url is required")
    if not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"

    verbose = tool_context is None

    test_type = (test_type or "comprehensive").lower().strip()
    if test_type not in ["idor", "authz_replay", "param_discovery", "comprehensive"]:
        test_type = "comprehensive"

    results: Dict[str, Any] = {
        "target": target_url,
        "test_type": test_type,
        "http_method": http_method,
        "parameters_provided": parameters,
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
            "fallback_notes": [],
        },
    }

    try:
        # Handle login and credentials
        final_cookies = cookies or {}
        final_headers = headers or {}
        final_alt_cookies = alt_cookies or {}
        final_alt_headers = alt_headers or {}

        multi_creds_list = []
        if multi_credentials:
            try:
                multi_creds_list = json.loads(multi_credentials)
            except Exception as e:
                raise ValueError(f"multi_credentials expected to be JSON: {e}")

        if login_url:
            # Use single credentials for main auth context if provided
            if credentials:
                try:
                    creds_dict = json.loads(credentials)
                except Exception as e:
                    raise ValueError(f"credentials expected to be JSON: {e}")
                try:
                    if verbose:
                        print(f"[*] Attempting login at {login_url}", file=sys.stderr)
                    c, h = _perform_login(
                        login_url=login_url,
                        credentials=creds_dict,
                        method=login_method or "POST",
                        auth_type=auth_type or "basic",
                        base_headers=headers,
                        verbose=verbose
                    )
                    if c is not None:
                        final_cookies.update(c)
                    if h is not None:
                        final_headers.update(h)
                except Exception as e:
                    raise ValueError(f"login failed: {e}")

            # Handle multi-credentials
            if multi_creds_list:
                for i, creds_dict in enumerate(multi_creds_list):
                    if verbose:
                        print("[*] Attempting alternate login", file=sys.stderr)
                    c, h = _perform_login(
                        login_url=login_url,
                        credentials=creds_dict,
                        method=login_method or "POST",
                        auth_type=auth_type or "basic",
                        base_headers=headers,
                        verbose=verbose
                    )
                    if c is not None and h is not None:
                        if i == 0 and not credentials:
                            # Use first one as primary if no single credentials provided
                            final_cookies.update(c)
                            final_headers.update(h)
                        elif i == 1 or (i == 0 and credentials):
                            # Use as alternate context
                            final_alt_cookies.update(c)
                            final_alt_headers.update(h)
                        # Note: currently idor_specialist only supports one alternate context
                        # for replay.

        rc = RequestConfig(
            target_url=target_url,
            http_method=http_method,
            cookies=final_cookies or None,
            headers=final_headers or None,
            alt_cookies=final_alt_cookies or None,
            alt_headers=final_alt_headers or None,
            request_type=request_type or "query",
            auth_type=auth_type or "basic",
        )
        # Param discovery (cheap: URL query + common ID keys)
        if not parameters or test_type == "param_discovery":
            if verbose:
                print(f"[*] Parameter discovery", file=sys.stderr)
            discovered = _idor_parameter_discovery(rc, parameters, test_type=test_type)
        else:
            discovered = [p.strip() for p in parameters.split(",") if p.strip()]
        results["parameters_discovered"] = discovered

        if verbose:
            print(f"[*] IDOR testing for parameters {discovered}", file=sys.stderr)

        # Primary engine: Python internal IDOR scan
        want_replay = test_type in ["authz_replay", "comprehensive"] and bool(rc.alt_cookies or rc.alt_headers)

        parsed_test_values = None
        if test_values:
            try:
                parsed_test_values = json.loads(test_values)
            except:
                pass

        if verbose:
            print(f"[*] Running IDOR scan for {discovered}", file=sys.stderr)

        findings = _python_idor_engine(
            rc,
            focus_params=discovered,
            num_range=num_range,
            do_authz_replay=want_replay,
            test_values=parsed_test_values,
            evasion=evasion,
            verbose=verbose
        )
        results["findings"].extend(findings)

        vulns = [x for x in results["findings"] if x.get("vulnerable", False)]
        results["vulnerabilities"] = vulns

        results["intelligence"] = _analyze_idor_intelligence(results, has_alt=bool(rc.alt_cookies or rc.alt_headers))
        results["recommendations"] = _generate_idor_recommendations(test_type, results)

        results["counts"] = {
            "parameters_discovered": len(results.get("parameters_discovered", [])),
            "findings": len(results.get("findings", [])),
            "vulnerabilities": len(results.get("vulnerabilities", [])),
        }

        if verbose:
            print(f"[*] IDOR findings: {results['counts']['findings']}", file=sys.stderr)

    except Exception as e:
        results["errors"].append(str(e))
        if verbose:
            print(f"[-] Error during IDOR scan: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    return json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True)


# ----------------------------
# Login / Auth Helpers
# ----------------------------

def _perform_login(
        login_url: str,
        credentials: Dict[str, Any],
        method: str = "POST",
        auth_type: str = "basic",
        base_headers: Optional[Dict[str, str]] = None,
        timeout: int = 15,
        verbose: bool = False
) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]]]:
    """
    Perform a login request to get session cookies or tokens.
    Returns (cookies, headers).
    """
    if verbose:
        print(f"[*] Attempting login at {login_url} ({auth_type})", file=sys.stderr)

    headers = dict(base_headers or {})
    try:
        req_kwargs = {
            "method": method.upper() if method else "POST",
            "url": login_url,
            "headers": headers,
            "timeout": timeout,
            "verify": False,
            "allow_redirects": True
        }

        if auth_type == "oauth" or auth_type == "jwt":
            req_kwargs["json"] = credentials
        else:
            req_kwargs["data"] = credentials

        resp = requests.request(**req_kwargs)

        if resp.status_code in (200, 302):
            new_cookies = resp.cookies.get_dict()
            new_headers = headers.copy()

            try:
                data = resp.json()
                if auth_type == "jwt" and "token" in data:
                    new_headers["Authorization"] = f"Bearer {data['token']}"
                elif auth_type == "oauth" and "access_token" in data:
                    new_headers["Authorization"] = f"Bearer {data['access_token']}"
            except Exception:
                pass

            if verbose:
                print(f"[+] Login successful at {login_url}", file=sys.stderr)
            return new_cookies, new_headers
        else:
            if verbose:
                print(f"[-] Login failed at {login_url} (status={resp.status_code})", file=sys.stderr)
    except Exception as e:
        if verbose:
            print(f"[-] Error during login at {login_url}: {e}", file=sys.stderr)

    return None, None


def _idor_parameter_discovery(
        request_config: RequestConfig,
        provided_params: Optional[str],
        test_type: str = "comprehensive",
) -> List[str]:
    # Extract query params from URL
    parsed = urlparse(request_config.target_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    url_params = list(qs.keys())

    # Path IDs are also part of the URL
    path_ids = _extract_path_ids(parsed.path)
    url_path_params = [f"(path_id_at_{idx})" for idx, _ in path_ids]

    all_url_params = set(url_params + url_path_params)

    if test_type not in ["param_discovery", "comprehensive"]:
        if all_url_params:
            # Prefer likely ID keys first (stable ordering)
            ordered = sorted(all_url_params, key=lambda x: (0 if x.lower() in _COMMON_ID_KEYS else 1, x.lower()))
            return ordered

    # Advanced discovery
    discovered = set(advanced_parameter_discovery(
        request_config=apc_request_config(
            target_url=request_config.target_url,
            http_method=request_config.http_method,
            cookies=request_config.cookies,
            headers=request_config.headers,
        ),
        provided_params=provided_params,
        tools=["arjun", "paramspider"],
    ))

    # Add path IDs discovered from URL
    for p in url_path_params:
        discovered.add(p)

    # If nothing, seed with common ID-ish keys (agent can prune later)
    if not discovered:
        discovered.update(sorted(_COMMON_ID_KEYS)[:10])

    # Prefer likely ID keys first (stable ordering)
    ordered = sorted(discovered, key=lambda x: (0 if x.lower() in _COMMON_ID_KEYS else 1, x.lower()))
    return ordered


def _signals_are_close(s1: Dict[str, Any], s2: Dict[str, Any]) -> bool:
    """
    Check if two signals are 'close' enough to be combined.
    """
    # Numerical signals: similarity, struct_similarity, content_similarity
    # Threshold: 0.1
    for k in ["similarity", "struct_similarity", "content_similarity"]:
        v1 = s1.get(k)
        v2 = s2.get(k)
        if v1 is not None and v2 is not None:
            if abs(v1 - v2) > 0.1:
                return False

    # Length signal
    # If mutated_len differs significantly, they might be different objects
    l1 = s1.get("mutated_len")
    l2 = s2.get("mutated_len")
    if l1 is not None and l2 is not None:
        divisor = min(l1, l2)
        if divisor == 0:
            if max(l1, l2) > 0:
                return False
        elif max(l1, l2) / divisor > 1.2:  # More than 20% difference
            return False

    return True


def _python_idor_engine(
        request_config: RequestConfig,
        focus_params: List[str],
        num_range: Optional[str],
        do_authz_replay: bool,
        test_values: Optional[List[Any]] = None,
        evasion: bool = False,
        verbose: bool = False
) -> List[Dict[str, Any]]:
    """
    Internal IDOR Engine:
    - Create an "authorized baseline" request (current cookies/headers) for the endpoint.
    - Mutate candidate identifiers in query/body/path and compare:
        - status change (e.g., 200 for other IDs)
        - similarity drop (indicates a different object)
        - JSON structure/content similarity
    - Optional authz replay: replay baseline + mutated as alt (low-priv/unauth) and see if alt matches authorized.
    """
    findings: List[Dict[str, Any]] = []

    method = request_config.http_method.upper()
    target = request_config.target_url

    # Identify candidate ID locations
    parsed = urlparse(target)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    candidate_params = _pick_candidate_params(qs, focus_params)

    path_ids = _extract_path_ids(parsed.path)

    # For JSON/GraphQL, we often want to strip the query string from the URL
    base_url_for_body = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
    # Convert qs to a simple dict for JSON
    qs_dict = {k: v[0] for k, v in qs.items()}

    # Build baseline authorized response
    if verbose:
        print(f"[*] Fetching baseline authorized response for {target}", file=sys.stderr)

    if request_config.request_type in ["json", "graphql"]:
        baseline_url = base_url_for_body
        baseline_params = qs_dict
    else:
        baseline_url = target
        baseline_params = None

    baseline = _send_request(
        request_config,
        url=baseline_url,
        method=method,
        params=baseline_params,
        data=None,
        use_alt=False,
        evasion=evasion
    )

    if baseline is None:
        return [
            {
                "vulnerable": False,
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

    # Prepare mutation values for query params
    mutations = test_values if test_values else _build_id_mutations(qs, path_id=None, num_range=num_range)
    if verbose:
        print(f"[*] Testing mutations: {mutations}", file=sys.stderr)

    # 1) Param mutations
    for p in candidate_params:
        orig_val = (qs.get(p, [""])[0] if p in qs else "")
        if verbose:
            print(f"[*] Testing mutations for parameter: {p}", file=sys.stderr)

        for mv in mutations:
            if mv is None:
                continue

            if request_config.request_type in ["json", "graphql"]:
                mutated_url = base_url_for_body
                mutated_params = qs_dict.copy()
                mutated_params[p] = mv
                resp_params, resp_data = mutated_params, None
            else:
                mutated_url = _add_or_replace_query_param(target, p, str(mv))
                resp_params, resp_data = None, None

            resp = _send_request(
                request_config,
                url=mutated_url,
                method=method,
                params=resp_params,
                data=resp_data,
                use_alt=False,
                evasion=evasion
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
                verbose=verbose,
            )
            if f:
                findings.append(f)

            # Optional authz replay: compare alt user
            if do_authz_replay:
                alt_resp = _send_request(
                    request_config,
                    url=mutated_url,
                    method=method,
                    params=resp_params,
                    data=resp_data,
                    use_alt=True,
                    evasion=evasion
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

    # 2) Path ID mutations
    for idx, pid_val in path_ids:
        path_key = f"(path_id_at_{idx})"
        if focus_params and path_key not in focus_params:
            continue
        if verbose:
            print(f"[*] Testing mutations for path index: {idx}", file=sys.stderr)

        # Prepare mutation values specific to this path ID
        mutations = _build_id_mutations(qs={}, path_id=pid_val, num_range=num_range)
        for mv in mutations:
            if mv is None or mv == pid_val:
                continue
            mutated_path = _replace_path_id(parsed.path, idx, str(mv))
            mutated_url = urlunparse((parsed.scheme, parsed.netloc, mutated_path, parsed.params, parsed.query, parsed.fragment))

            if request_config.request_type in ["json", "graphql"]:
                # If we are in JSON mode, we use base_url (no query string) 
                # but we still want the MUTATED path.
                mutated_url_no_qs = urlunparse((parsed.scheme, parsed.netloc, mutated_path, '', '', ''))
                mutated_url = mutated_url_no_qs
                resp_params, resp_data = qs_dict, None
            else:
                resp_params, resp_data = None, None

            resp = _send_request(
                request_config,
                url=mutated_url,
                method=method,
                params=resp_params,
                data=resp_data,
                use_alt=False,
                evasion=evasion
            )
            if resp is None:
                continue

            f = _evaluate_mutation(
                target_url=mutated_url,
                method=method,
                location="path",
                key=f"(path_id_at_{idx})",
                original=str(pid_val),
                mutated=str(mv),
                baseline_status=baseline_status,
                baseline_len=baseline_len,
                baseline_hash=baseline_hash,
                baseline_text=baseline_text,
                resp=resp,
                verbose=verbose,
            )
            if f:
                findings.append(f)

            if do_authz_replay:
                alt_resp = _send_request(
                    request_config,
                    url=mutated_url,
                    method=method,
                    params=resp_params,
                    data=resp_data,
                    use_alt=True,
                    evasion=evasion
                )
                replay_f = _evaluate_authz_replay(
                    url=mutated_url,
                    method=method,
                    key=f"(path_id_at_{idx})",
                    location="path",
                    resp_auth=resp,
                    resp_alt=alt_resp,
                )
                if replay_f:
                    findings.append(replay_f)

    # Combine similar findings where original values are equal
    combined: List[Dict[str, Any]] = []
    groups: Dict[Tuple, List[Dict[str, Any]]] = {}

    for f in findings:
        sig = f.get("signals", {})
        # Grouping key: everything except url and mutated_value
        # We also include some signals that MUST be identical
        key = (
            f.get("finding_type"),
            f.get("method"),
            f.get("parameter"),
            f.get("param_location"),
            f.get("original_value"),
            bool(f.get("vulnerable")),
            sig.get("mutated_status"),
            sig.get("baseline_status"),
            sig.get("auth_status"),
            sig.get("alt_status"),
        )
        if key not in groups:
            groups[key] = []
        groups[key].append(f)

    for group in groups.values():
        if not group:
            continue

        while group:
            base = group.pop(0)
            base_mutated_values = [base.get("mutated_value")]

            # Find others that are "close" to base
            to_combine = []
            remaining = []
            for other in group:
                if _signals_are_close(base.get("signals", {}), other.get("signals", {})):
                    to_combine.append(other)
                    base_mutated_values.append(other.get("mutated_value"))
                else:
                    remaining.append(other)

            # Combine them
            base["mutated_value"] = sorted(list(set(str(v) for v in base_mutated_values if v is not None)))

            # If we combined multiple, we might want to adjust the evidence/URL
            if len(base["mutated_value"]) > 1:
                base["url"] = f"{base['url']} (and {len(base['mutated_value']) - 1} other variants)"
                base["evidence"] = f"{base['evidence']} (+{len(base['mutated_value']) - 1} more matches)"

            combined.append(base)
            group = remaining

    return combined


def _compare_responses(baseline_text: str, test_text: str) -> Dict[str, float]:
    """
    Compare baseline and test responses for structure, content, and similarity.
    """

    def parse_json(text):
        try:
            return json.loads(text)
        except:
            return None

    baseline_data = parse_json(baseline_text)
    test_data = parse_json(test_text)

    # Text similarity (SequenceMatcher)
    text_sim = SequenceMatcher(None, baseline_text[:8000],
                               test_text[:8000]).ratio() if baseline_text or test_text else 0.0

    struct_sim = 0.0
    content_sim = 0.0

    if baseline_data and test_data:
        if isinstance(baseline_data, dict) and isinstance(test_data, dict):
            # Structure similarity
            keys1 = set(baseline_data.keys())
            keys2 = set(test_data.keys())
            if keys1 | keys2:
                struct_sim = len(keys1 & keys2) / len(keys1 | keys2)

            # Content similarity
            common_keys = keys1 & keys2
            if common_keys:
                matches = sum(1 for k in common_keys if baseline_data[k] == test_data[k])
                content_sim = matches / len(common_keys)
        elif isinstance(baseline_data, list) and isinstance(test_data, list):
            # For lists, just do basic length comparison as a heuristic
            if baseline_data or test_data:
                struct_sim = min(len(baseline_data), len(test_data)) / max(len(baseline_data), len(test_data), 1)
            content_sim = text_sim

    return {
        "text_similarity": text_sim,
        "structure_similarity": struct_sim,
        "content_similarity": content_sim
    }


def _send_request(
        request_config: RequestConfig,
        url: str,
        method: str,
        params: Optional[Dict[str, Any]],
        data: Optional[Dict[str, Any]],
        use_alt: bool,
        evasion: bool = False
) -> Optional[requests.Response]:
    try:
        headers = dict((request_config.alt_headers if use_alt else request_config.headers) or {})
        cookies = (request_config.alt_cookies if use_alt else request_config.cookies) or {}

        if evasion:
            # Jitter and UA rotation
            time.sleep(random.uniform(0.1, 0.5))
            headers["User-Agent"] = random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
            ])

        req_kwargs = {
            "method": method,
            "url": url,
            "headers": headers,
            "cookies": cookies,
            "timeout": request_config.timeout,
            "allow_redirects": True,
            "verify": False,
        }

        # Request Type logic from IDORChecker.py
        if request_config.request_type == "json":
            req_kwargs["json"] = params or data or {}
        elif request_config.request_type == "graphql":
            p = params or data or {}
            # Basic GraphQL wrapper for IDOR testing
            req_kwargs["json"] = {"query": f"{{ resource(id: \"{p.get('id', '')}\") {{ data }} }}"}
            req_kwargs["method"] = "POST"  # GraphQL is typically POST
        else:
            req_kwargs["params"] = params
            req_kwargs["data"] = data

        resp = requests.request(**req_kwargs)
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
        verbose: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Heuristic signals:
    - status is 200 when baseline is 200, but content differs materially (potential other object)
    - baseline 403/401 but mutated is 200 (classic unauthorized access)
    - JSON structure similarity high but content similarity low
    """
    text = resp.text or ""
    status = resp.status_code
    h = _hash_text(text)
    ln = len(text)

    # Comparison signals
    comp = _compare_responses(baseline_text, text)
    sim = comp["text_similarity"]
    struct_sim = comp["structure_similarity"]
    content_sim = comp["content_similarity"]

    # Basic “changed object” heuristic: same success status but substantially different body
    # If it's JSON, use struct/content sim
    if struct_sim > 0 or content_sim > 0:
        looks_like_other_object = (
                status in (200, 201) and baseline_status in (200, 201)
                and struct_sim > 0.8 and content_sim < 0.9 and h != baseline_hash
        )
    else:
        looks_like_other_object = (
                status in (200, 201) and baseline_status in (200, 201) and h != baseline_hash and sim < 0.85 and (
                ln / max(baseline_len, 1) < 0.7 or ln / max(baseline_len, 1) > 1.3)
        )

    # Access control break heuristic: baseline forbidden but mutated ok
    authz_break = baseline_status in (401, 403) and status in (200, 201)

    # If response is identical, ignore
    if h == baseline_hash and status == baseline_status:
        return None

    # Decide vulnerability vs candidate
    vulnerable = bool(authz_break or looks_like_other_object)

    if not vulnerable and status in (200, 201) and h != baseline_hash and sim < 0.92:
        finding_type = "idor_candidate"
    elif authz_break:
        finding_type = "authz_bypass_candidate"
    elif looks_like_other_object:
        finding_type = "idor_likely"
    else:
        return None

    evidence_bits = [
        f"baseline_status={baseline_status}",
        f"mutated_status={status}",
        f"similarity={round(sim, 3)}",
    ]
    if struct_sim > 0:
        evidence_bits.append(f"struct_sim={round(struct_sim, 3)}")
    if content_sim > 0:
        evidence_bits.append(f"content_sim={round(content_sim, 3)}")

    return {
        "vulnerable": vulnerable,
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
            "struct_similarity": struct_sim,
            "content_similarity": content_sim,
            "baseline_len": baseline_len,
            "mutated_len": ln,
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


def _extract_path_ids(path: str) -> List[Tuple[int, int]]:
    """
    Extract all numeric segments from the path and their indices.
    Returns list of (index, value).
    """
    try:
        parts = (path or "").split("/")
        results = []
        for i, p in enumerate(parts):
            if p.isdigit():
                results.append((i, int(p)))
        return results
    except Exception:
        return []


def _replace_path_id(path: str, index: int, new_value: str) -> str:
    parts = (path or "").split("/")
    if 0 <= index < len(parts):
        parts[index] = str(new_value)
    return "/".join(parts)


def _pick_candidate_params(qs: Dict[str, List[str]], focus_params: List[str]) -> List[str]:
    # if focus provided, use those first (filtered for query)
    if focus_params is not None:
        return [p for p in focus_params if p and not p.startswith("(path_id_at_")]

    # else pick keys that look ID-ish
    keys = list(qs.keys())
    if not keys:
        return sorted(list(_COMMON_ID_KEYS))[:8]

    likely = [k for k in keys if k and k.lower() in _COMMON_ID_KEYS]
    return likely or keys[:8]


def _default_test_values_from_url(url: str) -> List[Any]:
    """
    Seed engine with meaningful values:
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

        # From path
        path_ids = _extract_path_ids(parsed.path)
        for _, n in path_ids:
            for d in [-2, -1, 1, 2, 10]:
                if n + d >= 0:
                    vals.add(n + d)
    except Exception:
        pass
    return sorted(list(vals))[:50]


def _build_id_mutations(qs: Dict[str, List[str]], path_id: Optional[int] = None, num_range: Optional[str] = None) -> \
        List[Any]:
    muts: set[Any] = set()

    all_discovered_ids = []
    if path_id is not None:
        all_discovered_ids.append(path_id)
    for _, v in (qs or {}).items():
        if v and str(v[0]).isdigit():
            all_discovered_ids.append(int(v[0]))

    # Baseline-derived
    for n in all_discovered_ids:
        muts.update([n - 2, n - 1, n + 1, n + 2, n + 10, n + 100])

        # Dynamic range based on value magnitude
        if n > 100:
            # +/- 10%
            delta = max(1, n // 10)
            muts.update([n - delta, n + delta])
            # Some random-ish offsets if it's large
            if n > 1000:
                muts.update([n - 50, n + 50, n - 500, n + 500])

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
    parser.add_argument("--test-values", default=None, help='Custom test values in JSON format')
    parser.add_argument("--login-url", default=None, help="URL for login page")
    parser.add_argument("--credentials", default=None,
                        help='Login credentials JSON string, e.g. \"{\\"username\\":\\"admin\\",\\"password\\":\\"password\\"}\"')
    parser.add_argument("--login-method", default=None, help="HTTP method for login")
    parser.add_argument("--num-range", default=None, help='Numeric range "start-end", e.g. "1-1000"')
    parser.add_argument("--multi-credentials", default=None, help="JSON list of multiple credentials")
    parser.add_argument("--evasion", action="store_true", help="Enable evasion techniques")
    parser.add_argument("--request-type", choices=["query", "json", "graphql"], default=None,
                        help="Request type")
    parser.add_argument("--auth-type", choices=["basic", "oauth", "jwt"], default=None,
                        help="Authentication type")

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
            test_values=args.test_values,
            login_url=args.login_url,
            credentials=args.credentials,
            login_method=args.login_method,
            num_range=args.num_range,
            multi_credentials=args.multi_credentials,
            evasion=args.evasion,
            request_type=args.request_type,
            auth_type=args.auth_type,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
