"""
Proxy CA Trust Store Utilities
==============================

Automated discovery of intercepting proxies from environment variables (HTTP_PROXY,
HTTPS_PROXY), TLS probing, certificate fingerprint deduplication, and installation
into system trust stores (Debian and RHEL/Fedora/CentOS).
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Standard environment variables for HTTP/HTTPS proxies
DEFAULT_PROXY_ENV_VARS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
)

# Default hostnames, IPs, and service names to bypass when configuring proxy exclusion for Langfuse
DEFAULT_LANGFUSE_NO_PROXY_HOSTS: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "langfuse-web",
    "cyber-langfuse",
    "::1",
)

# Regex pattern to match PEM certificate blocks
PEM_CERT_PATTERN = re.compile(
    r"-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----"
)

# Known HTTP endpoints provided by common intercepting proxies to export their CA certificate
# Format: tuple of (path, tuple of fallback Host header values)
KNOWN_HTTP_CERT_TARGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Burp Suite default CA cert endpoint
    ("/cert", ("burp",)),
    # OWASP ZAP default root CA cert API endpoints
    ("/OTHER/core/other/rootcert/", ("zap",)),
    ("/OTHER/core/other/rootcert", ("zap",)),
    # mitmproxy default CA cert endpoints
    ("/cert/pem", ("mitm.it",)),
    ("/cert/cer", ("mitm.it",)),
)


@dataclass(frozen=True)
class TrustStoreConfig:
    """Configuration for a Linux distribution trust store."""

    name: str
    cert_dir: Path
    update_cmd: list[str]
    supported_extensions: tuple[str, ...] = field(default=(".crt", ".pem"))
    fallback_update_cmd: list[str] | None = None


# Default supported trust stores: Debian and RHEL
DEBIAN_TRUST_STORE = TrustStoreConfig(
    name="debian",
    cert_dir=Path("/usr/local/share/ca-certificates"),
    update_cmd=["update-ca-certificates"],
    supported_extensions=(".crt", ".pem"),
)

RHEL_TRUST_STORE = TrustStoreConfig(
    name="rhel",
    cert_dir=Path("/etc/pki/ca-trust/source/anchors"),
    update_cmd=["update-ca-trust", "extract"],
    supported_extensions=(".crt", ".pem"),
    fallback_update_cmd=["update-ca-trust"],
)

KNOWN_TRUST_STORES: tuple[TrustStoreConfig, ...] = (
    DEBIAN_TRUST_STORE,
    RHEL_TRUST_STORE,
)


def extract_proxy_endpoints(
    env_vars: tuple[str, ...] = DEFAULT_PROXY_ENV_VARS,
    environ: dict[str, str] | None = None,
) -> list[tuple[str, int]]:
    """
    Extract unique (host, port) tuples from proxy environment variables.

    Handles full URLs with or without scheme, authentication credentials,
    and defaults port based on scheme (443 for https, 8080 for http/schemeless).
    """
    env = os.environ if environ is None else environ
    endpoints: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    for var_name in env_vars:
        raw_val = env.get(var_name, "").strip()
        if not raw_val:
            continue

        endpoint = _parse_proxy_string(raw_val)
        if endpoint and endpoint not in seen:
            seen.add(endpoint)
            endpoints.append(endpoint)

    return endpoints


def extract_langfuse_hosts(
    langfuse_host: str | None = None,
    environ: dict[str, str] | None = None,
) -> list[str]:
    """
    Extract hostnames, domain names, or IP addresses associated with Langfuse
    for inclusion in NO_PROXY / no_proxy environment variables.
    """
    env = os.environ if environ is None else environ
    hosts: list[str] = list(DEFAULT_LANGFUSE_NO_PROXY_HOSTS)

    # Check explicitly passed langfuse_host or from env
    target_host = langfuse_host or env.get("LANGFUSE_HOST") or env.get("LANGFUSE_BASE_URL")
    if target_host and target_host.strip():
        cleaned = target_host.strip()
        if "://" not in cleaned:
            cleaned = f"//{cleaned}"
        try:
            parsed = urllib.parse.urlsplit(cleaned)
            hostname = parsed.hostname
            if hostname:
                hostname = hostname.strip("[]").lower()
                if hostname not in hosts:
                    hosts.append(hostname)
                if parsed.port:
                    port_entry = f"{hostname}:{parsed.port}"
                    if port_entry not in hosts:
                        hosts.append(port_entry)
        except Exception:
            pass

    return hosts


def build_no_proxy_string(
    existing_no_proxy: str | None = None,
    additional_hosts: tuple[str, ...] | list[str] | None = None,
) -> str:
    """
    Combine existing NO_PROXY string with additional hosts, preserving order and deduplicating.
    """
    entries: list[str] = []
    seen: set[str] = set()

    if existing_no_proxy:
        for item in existing_no_proxy.split(","):
            cleaned = item.strip()
            if cleaned:
                key = cleaned.lower()
                if key not in seen:
                    seen.add(key)
                    entries.append(cleaned)

    if additional_hosts:
        for host in additional_hosts:
            cleaned = host.strip()
            if cleaned:
                key = cleaned.lower()
                if key not in seen:
                    seen.add(key)
                    entries.append(cleaned)

    return ",".join(entries)


def configure_langfuse_proxy_bypass(
    environ: dict[str, str] | None = None,
    langfuse_host: str | None = None,
    logger: Any | None = None,
) -> str:
    """
    Ensure Langfuse endpoints (and default localhost/docker hosts) are excluded
    from HTTP_PROXY and HTTPS_PROXY by updating NO_PROXY and no_proxy.

    Updates both NO_PROXY and no_proxy in the target environment dict (defaults to os.environ).
    Returns the updated NO_PROXY string.
    """
    env = os.environ if environ is None else environ
    langfuse_hosts = extract_langfuse_hosts(langfuse_host=langfuse_host, environ=env)

    # Check both NO_PROXY and no_proxy to gather any pre-existing entries
    existing_no = env.get("NO_PROXY") or env.get("no_proxy") or ""
    updated_no_proxy = build_no_proxy_string(
        existing_no_proxy=existing_no,
        additional_hosts=langfuse_hosts,
    )

    env["NO_PROXY"] = updated_no_proxy
    env["no_proxy"] = updated_no_proxy

    if logger:
        logger.debug("Configured Langfuse proxy bypass (NO_PROXY=%s)", updated_no_proxy)

    return updated_no_proxy


def _parse_proxy_string(proxy_str: str) -> tuple[str, int] | None:
    """Parse a proxy string into a (host, port) tuple."""
    cleaned = proxy_str.strip()
    if not cleaned:
        return None

    # Handle scheme-less URLs by normalizing with //
    if "://" not in cleaned:
        cleaned_url = f"//{cleaned}"
    else:
        cleaned_url = cleaned

    try:
        parsed = urllib.parse.urlsplit(cleaned_url)
    except Exception:
        return None

    host = parsed.hostname
    if not host:
        return None

    host = host.strip("[]").lower()
    port = parsed.port

    if port is None:
        scheme = parsed.scheme.lower() if parsed.scheme else ""
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 8080
        else:
            port = 8080

    return (host, port)


def compute_cert_fingerprint(der_bytes: bytes) -> str:
    """Calculate the normalized SHA-256 hex fingerprint of DER-encoded certificate bytes."""
    return hashlib.sha256(der_bytes).hexdigest().lower()


def is_valid_asn1_sequence(der_bytes: bytes) -> bool:
    """
    Verify that bytes start with a valid ASN.1 SEQUENCE structure for an X.509 Certificate.

    An X.509 certificate DER structure begins with an outer SEQUENCE (0x30)
    wrapping an inner TBSCertificate SEQUENCE (0x30).
    """
    if not der_bytes or len(der_bytes) < 32 or der_bytes[0] != 0x30:
        return False

    idx = 1
    if der_bytes[idx] & 0x80:
        num_len_bytes = der_bytes[idx] & 0x7F
        if num_len_bytes == 0 or num_len_bytes > 4 or idx + num_len_bytes >= len(der_bytes):
            return False
        idx += 1 + num_len_bytes
    else:
        idx += 1

    return idx < len(der_bytes) and der_bytes[idx] == 0x30


def parse_certificate_data(data: bytes) -> tuple[bytes, str] | None:
    """
    Parse raw response bytes into DER bytes and PEM certificate string.

    Supports both PEM text (with BEGIN/END CERTIFICATE boundaries) and raw binary DER format.
    Returns (der_bytes, pem_str) or None if parsing fails or data is not a valid certificate.
    """
    if not data or len(data) < 16:
        return None

    # 1. Attempt parsing as PEM text
    try:
        text = data.decode("utf-8", errors="ignore")
        pem_blocks = PEM_CERT_PATTERN.findall(text)
        if pem_blocks:
            pem_str = pem_blocks[0].strip() + "\n"
            der_bytes = ssl.PEM_cert_to_DER_cert(pem_str)
            if is_valid_asn1_sequence(der_bytes):
                return der_bytes, pem_str
    except Exception:
        pass

    # 2. Attempt parsing as binary DER certificate
    try:
        if is_valid_asn1_sequence(data):
            pem_str = ssl.DER_cert_to_PEM_cert(data)
            if pem_str and "-----BEGIN CERTIFICATE-----" in pem_str:
                return data, pem_str.strip() + "\n"
    except Exception:
        pass

    return None


def fetch_http_certificate(
    host: str,
    port: int,
    timeout: float = 3.0,
    targets: tuple[tuple[str, tuple[str, ...]], ...] = KNOWN_HTTP_CERT_TARGETS,
) -> tuple[bytes, str] | None:
    """
    Attempt to retrieve the proxy root CA certificate via known HTTP endpoints.

    Tries endpoints provided by proxies such as Burp Suite (/cert) and OWASP ZAP
    (/OTHER/core/other/rootcert/) before resorting to TLS probing.
    """
    for path, fallback_hosts in targets:
        host_headers = (f"{host}:{port}", host, *fallback_hosts)
        seen_hosts: set[str] = set()

        for host_header in host_headers:
            if host_header in seen_hosts:
                continue
            seen_hosts.add(host_header)

            conn: http.client.HTTPConnection | None = None
            try:
                conn = http.client.HTTPConnection(host, port, timeout=timeout)
                conn.request(
                    "GET",
                    path,
                    headers={
                        "Host": host_header,
                        "User-Agent": "CyberAutoAgent/1.0",
                        "Accept": "*/*",
                    },
                )
                resp = conn.getresponse()
                if resp.status == 200:
                    body = resp.read()
                    cert_pair = parse_certificate_data(body)
                    if cert_pair is not None:
                        return cert_pair
            except (ConnectionRefusedError, TimeoutError):
                return None
            except Exception:
                pass
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    return None


def probe_tls_certificate(
    host: str,
    port: int,
    timeout: float = 3.0,
) -> tuple[bytes, str] | None:
    """
    Probe the target endpoint for TLS support and retrieve its peer certificate.

    Returns (der_bytes, pem_string) if TLS handshake succeeds, or None if the endpoint
    is unreachable, times out, or does not negotiate TLS (e.g. plain HTTP).
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    sock: socket.socket | None = None
    ssl_sock: ssl.SSLSocket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        ssl_sock = context.wrap_socket(sock, server_hostname=host)
        ssl_sock.settimeout(timeout)

        der_bytes = ssl_sock.getpeercert(binary_form=True)
        if not der_bytes:
            return None

        pem_cert = ssl.DER_cert_to_PEM_cert(der_bytes)
        return der_bytes, pem_cert
    except (ssl.SSLError, TimeoutError, ConnectionRefusedError, OSError, Exception):
        return None
    finally:
        if ssl_sock is not None:
            try:
                ssl_sock.close()
            except Exception:
                pass
        elif sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def fetch_proxy_certificate(
    host: str,
    port: int,
    timeout: float = 3.0,
) -> tuple[bytes, str] | None:
    """
    Retrieve proxy CA certificate by first trying known HTTP paths (e.g., Burp Suite /cert,
    OWASP ZAP /OTHER/core/other/rootcert/) and falling back to TLS probing.
    """
    # 1. Try known HTTP certificate paths
    http_cert = fetch_http_certificate(host, port, timeout=timeout)
    if http_cert is not None:
        return http_cert

    # 2. Fallback to TLS probing if HTTP paths failed or did not return a certificate
    return probe_tls_certificate(host, port, timeout=timeout)


def get_available_trust_stores(
    stores: tuple[TrustStoreConfig, ...] = KNOWN_TRUST_STORES,
) -> list[TrustStoreConfig]:
    """Return the list of trust stores available and executable on the current system."""
    available: list[TrustStoreConfig] = []
    for store in stores:
        if store.cert_dir.is_dir() and shutil.which(store.update_cmd[0]) is not None:
            available.append(store)
    return available


def get_installed_ca_fingerprints(
    cert_dir: Path,
    extensions: tuple[str, ...] = (".crt", ".pem"),
) -> set[str]:
    """
    Extract SHA-256 fingerprints of all installed certificates in the specified directory.

    Scans files matching extensions and parses PEM certificate blocks or raw DER bytes.
    """
    fingerprints: set[str] = set()
    if not cert_dir.is_dir():
        return fingerprints

    try:
        entries = list(cert_dir.iterdir())
    except (OSError, PermissionError):
        return fingerprints

    for file_path in entries:
        if not file_path.is_file():
            continue
        if not any(file_path.name.lower().endswith(ext) for ext in extensions):
            continue

        try:
            content_bytes = file_path.read_bytes()
        except (OSError, PermissionError):
            continue

        # Try parsing PEM format
        try:
            content_text = content_bytes.decode("utf-8", errors="ignore")
            pem_blocks = PEM_CERT_PATTERN.findall(content_text)
            if pem_blocks:
                for block in pem_blocks:
                    try:
                        der = ssl.PEM_cert_to_DER_cert(block)
                        fingerprints.add(compute_cert_fingerprint(der))
                    except Exception:
                        pass
                continue
        except Exception:
            pass

        # Fallback: treat raw bytes as potential DER certificate
        if content_bytes:
            fingerprints.add(compute_cert_fingerprint(content_bytes))

    return fingerprints


def install_ca_certificate_to_store(
    pem_cert: str,
    host: str,
    port: int,
    fingerprint: str,
    store: TrustStoreConfig,
    log: Any | None = None,
    logger: Any | None = None,
) -> bool:
    """
    Write certificate to store directory and execute the bundle update command.

    Returns True if successfully written and updated, False otherwise.
    """
    active_logger = logger or log or logging.getLogger(__name__)
    safe_host = re.sub(r"[^a-zA-Z0-9_.-]", "_", host)
    cert_filename = f"proxy_{safe_host}_{port}_{fingerprint[:8]}.crt"
    cert_path = store.cert_dir / cert_filename

    try:
        # Ensure trailing newline in PEM content
        formatted_pem = pem_cert.strip() + "\n"
        cert_path.write_text(formatted_pem, encoding="utf-8")
    except (PermissionError, OSError) as err:
        active_logger.warning(
            "Failed to write proxy CA certificate to %s: %s",
            cert_path,
            err,
        )
        return False

    # Execute store update command
    commands_to_try = [store.update_cmd]
    if store.fallback_update_cmd:
        commands_to_try.append(store.fallback_update_cmd)

    update_succeeded = False
    last_error: Exception | None = None

    for cmd in commands_to_try:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                update_succeeded = True
                break
            else:
                active_logger.warning(
                    "Trust store command '%s' failed (code %d): %s %s",
                    " ".join(cmd),
                    result.returncode,
                    result.stdout.strip(),
                    result.stderr.strip(),
                )
        except (PermissionError, OSError, FileNotFoundError) as err:
            last_error = err
            active_logger.warning(
                "Error running trust store command '%s': %s",
                " ".join(cmd),
                err,
            )

    if not update_succeeded:
        active_logger.warning(
            "Certificate written to %s, but trust store update failed (%s)",
            cert_path,
            last_error or "non-zero return code",
        )
        return False

    active_logger.info(
        "Installed proxy CA certificate into %s trust store (%s)",
        store.name,
        cert_path,
    )
    return True


def configure_proxy_ca_certificates(
    log: Any | None = None,
    logger: Any | None = None,
    env_vars: tuple[str, ...] = DEFAULT_PROXY_ENV_VARS,
    environ: dict[str, str] | None = None,
    timeout: float = 3.0,
    stores: tuple[TrustStoreConfig, ...] = KNOWN_TRUST_STORES,
) -> list[str]:
    """
    Main entry point for proxy CA certificate configuration.

    Detects proxy endpoints from environment, probes for TLS certificates,
    checks if fingerprints are already installed in available Linux trust stores,
    and installs new certificates as needed. Also ensures Langfuse endpoints
    are excluded from proxying via NO_PROXY / no_proxy.

    Returns a list of installed certificate identifiers (e.g. ['debian:proxy.local:8080:<fp>']).
    """
    active_logger = logger or log or logging.getLogger(__name__)
    configure_langfuse_proxy_bypass(environ=environ, logger=active_logger)
    endpoints = extract_proxy_endpoints(env_vars=env_vars, environ=environ)
    if not endpoints:
        return []

    available_stores = get_available_trust_stores(stores=stores)
    installed_identifiers: list[str] = []

    for host, port in endpoints:
        probe_result = fetch_proxy_certificate(host, port, timeout=timeout)
        if probe_result is None:
            active_logger.debug(
                "Proxy endpoint %s:%d did not provide a CA certificate (HTTP endpoints and TLS probe failed); "
                "skipping CA trust store",
                host,
                port,
            )
            continue

        der_bytes, pem_cert = probe_result
        fingerprint = compute_cert_fingerprint(der_bytes)

        if not available_stores:
            active_logger.info(
                "Proxy CA certificate detected at %s:%d (fingerprint %s), but no supported system "
                "trust store (Debian/RHEL) is available on this platform; skipping installation",
                host,
                port,
                fingerprint[:16],
            )
            continue

        for store in available_stores:
            installed_fingerprints = get_installed_ca_fingerprints(
                store.cert_dir,
                extensions=store.supported_extensions,
            )

            if fingerprint in installed_fingerprints:
                active_logger.info(
                    "Proxy CA certificate for %s:%d is already trusted in %s trust store (fingerprint %s)",
                    host,
                    port,
                    store.name,
                    fingerprint[:16],
                )
                continue

            active_logger.info(
                "Installing new proxy CA certificate for %s:%d into %s trust store",
                host,
                port,
                store.name,
            )
            success = install_ca_certificate_to_store(
                pem_cert=pem_cert,
                host=host,
                port=port,
                fingerprint=fingerprint,
                store=store,
                log=active_logger,
            )
            if success:
                identifier = f"{store.name}:{host}:{port}:{fingerprint}"
                installed_identifiers.append(identifier)

    return installed_identifiers
