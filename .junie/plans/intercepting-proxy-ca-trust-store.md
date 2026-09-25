---
sessionId: session-260919-135848-1n9e
---

# Requirements

### Overview & Goals
When Cyber-AutoAgent runs behind an intercepting proxy (such as Burp Suite, OWASP ZAP, mitmproxy, or an enterprise security inspection proxy) configured via `HTTP_PROXY` or `HTTPS_PROXY`, outbound HTTPS requests from subtools, Python libraries, and system utilities can fail SSL verification unless the proxy's root CA certificate is installed into the system trust store.

The goal is to automatically detect when `HTTP_PROXY` or `HTTPS_PROXY` points to a host/port that negotiates TLS, extract the intercepting CA certificate, check if its fingerprint is already trusted, install it into the standard system trust store for Debian Linux (`/usr/local/share/ca-certificates/`) and RHEL/CentOS/Fedora Linux (`/etc/pki/ca-trust/source/anchors/`), and trigger the appropriate trust bundle update command (`update-ca-certificates` or `update-ca-trust extract`). When running on systems without a supported trust store (such as macOS or unsupported environments), the code must gracefully detect this and skip filesystem modifications without errors.

### Scope
#### In Scope
- Inspecting `HTTP_PROXY`, `HTTPS_PROXY` (and standard lowercase variants `http_proxy`, `https_proxy`).
- Parsing target host and port from proxy netloc strings regardless of scheme (`http://`, `https://`, or scheme-less `host:port`).
- Probing whether the target endpoint responds to TLS/SSL handshakes with a bounded timeout (non-blocking / non-hanging).
- Extracting the remote server certificate and computing its SHA-256 fingerprint.
- Detecting Debian trust store (`/usr/local/share/ca-certificates` + `update-ca-certificates`) and RHEL/Fedora/CentOS trust store (`/etc/pki/ca-trust/source/anchors` + `update-ca-trust`).
- Checking existing `.crt` / `.pem` files in the detected trust store(s) to determine if the certificate fingerprint is already present (idempotent behavior).
- Writing the certificate in PEM format into the active trust store directory and invoking the respective bundle update command (`update-ca-certificates` on Debian, `update-ca-trust extract` on RHEL).
- Handling permission errors, non-TLS proxies, unsupported platforms, and unreachable proxies gracefully with clean logging.
- Unit tests verifying netloc parsing, TLS probing, fingerprint comparison across Debian and RHEL, trust store updates, and fallback behavior.
- Documenting the new capability in `CHANGELOG.md`.

#### Out of Scope
- Modifying trust stores of non-Linux OSes (e.g. macOS Keychain, Windows Certificate Store).
- Generating self-signed proxy certificates or starting local intercepting proxy servers.

### User Stories
- **As a Security Engineer / Pentester**, I want Cyber-AutoAgent to automatically trust my intercepting proxy (e.g., Burp Suite / ZAP / mitmproxy) on Debian, Kali, or RHEL/CentOS/Fedora environments when `HTTP_PROXY` or `HTTPS_PROXY` is set, so that all underlying tools and agent requests function smoothly without SSL validation failures.
- **As a Developer on macOS or non-Debian/non-RHEL systems**, I want the proxy check to run safely without throwing exceptions or corrupting files when no supported CA directory is present.
- **As a DevOps Operator**, I want subsequent runs to be idempotent so certificate files are not duplicated and trust store update commands are only executed when a new certificate is actually installed.

### Functional Requirements
1. **Proxy Environment Inspection**:
   - Check `os.environ` for `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, and `https_proxy`.
   - Parse each unique proxy address to extract `(host, port)`. If no port is specified, default based on scheme (or default to port 8080 / 443).
2. **TLS Probing**:
   - Perform a non-blocking TCP socket connection and SSL handshake to `(host, port)` with a short timeout (e.g., 2–3 seconds).
   - If TLS handshake succeeds, retrieve the peer certificate in DER and PEM formats.
   - If the endpoint does not respond to TLS (e.g. plain HTTP proxy), connection fails, or times out, safely ignore and continue.
3. **Trust Store Detection (Debian & RHEL)**:
   - Check for Debian trust store: directory `/usr/local/share/ca-certificates` and command `update-ca-certificates`.
   - Check for RHEL/CentOS/Fedora trust store: directory `/etc/pki/ca-trust/source/anchors` and command `update-ca-trust`.
   - Support environments where either or both trust store types are present.
   - If neither is available, log an informational message and skip filesystem operations.
4. **Fingerprint Deduplication**:
   - Calculate the SHA-256 fingerprint of the probed certificate DER bytes.
   - Read all existing `.crt` and `.pem` files in the detected trust store directory and compute their SHA-256 fingerprints.
   - If the probed certificate's fingerprint matches any existing certificate in the store, skip installation and skip bundle update commands.
5. **Certificate Installation & Bundle Update**:
   - For Debian: write `/usr/local/share/ca-certificates/proxy_{host}_{port}_{fingerprint[:8]}.crt` and run `update-ca-certificates`.
   - For RHEL: write `/etc/pki/ca-trust/source/anchors/proxy_{host}_{port}_{fingerprint[:8]}.crt` and run `update-ca-trust extract` (or `update-ca-trust`).
   - Log the successful installation and certificate details (host, port, fingerprint, store type).
6. **Execution Point**:
   - Trigger this routine during startup in `src/cyberautoagent.py:main()` prior to initiating tool discovery and network operations.

### Non-Functional Requirements
- **Resilience / Fail-Safe**: Failures during proxy probing (e.g., unreachable proxy, permission denied on filesystem, malformed URL) must never crash or prevent agent startup.
- **Performance**: TLS probes must have a strict timeout (<= 3 seconds) to avoid delaying startup when an invalid proxy is specified.
- **Compliance & Coding Standards**: Must follow PEP 8 with 120-character line limit, double quotes for Python strings, and achieve >= 80% test coverage.

# Technical Design

### Current Implementation
- `src/cyberautoagent.py` initializes the CLI, parses arguments, configures logging via `setup_logging()`, runs `auto_setup()`, and starts the workflow controller.
- Currently, proxy environment variables (`HTTP_PROXY`, `HTTPS_PROXY`) are passed through standard Python/Docker environments, but no automatic trust store provisioning is performed.
- Debian/Kali containers (`docker/Dockerfile`) use `/usr/local/share/ca-certificates/` and `update-ca-certificates`.
- RHEL/CentOS/Fedora installations use `/etc/pki/ca-trust/source/anchors/` and `update-ca-trust`.

### Key Decisions
1. **Dedicated Module for Proxy Trust Management**:
   - *Approach*: Implement proxy discovery and certificate installation logic in `src/modules/utils/proxy.py`, exposing `configure_proxy_ca_certificates(logger=None)`.
   - *Rationale*: Keeps `cyberautoagent.py` clean, simplifies modular unit testing, and adheres to separation of concerns.
2. **Pluggable / Declarative Trust Store Handlers**:
   - *Approach*: Define a `TrustStoreConfig` dataclass / registry supporting both Debian (`/usr/local/share/ca-certificates`, `update-ca-certificates`) and RHEL (`/etc/pki/ca-trust/source/anchors`, `update-ca-trust extract`).
   - *Rationale*: Allows clean multi-distro discovery without hardcoding distribution-specific branching in multiple places.
3. **DER SHA-256 Fingerprint Matching**:
   - *Approach*: Compare `hashlib.sha256(cert_der).hexdigest().lower()`.
   - *Rationale*: Robust, deterministic, standard across cryptographic toolsets, and avoids issues with whitespace/comment differences in PEM files.
4. **Graceful Degradation on Unsupported OS / Permission Denial**:
   - *Approach*: Check `os.path.isdir(store.cert_dir)` and `shutil.which(store.update_bin)`. If none found or permission error occurs upon writing, catch `OSError`/`PermissionError` and log a warning.
   - *Rationale*: The agent runs in diverse environments (Debian, RHEL, macOS local development, restricted non-root containers).

### Proposed Changes
1. **`src/modules/utils/proxy.py` (New Module)**:
   - `TrustStoreConfig`:
     - Dataclass containing `name: str`, `cert_dir: Path`, `update_cmd: list[str]`, `supported_extensions: tuple[str, ...]`.
     - Standard definitions:
       - Debian: `name="debian"`, `cert_dir=Path("/usr/local/share/ca-certificates")`, `update_cmd=["update-ca-certificates"]`, `supported_extensions=(".crt", ".pem")`.
       - RHEL: `name="rhel"`, `cert_dir=Path("/etc/pki/ca-trust/source/anchors")`, `update_cmd=["update-ca-trust", "extract"]`, `supported_extensions=(".crt", ".pem")`.
   - `extract_proxy_endpoints(env_keys=("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")) -> list[tuple[str, int]]`:
     - Parses proxy strings using `urllib.parse.urlsplit`.
     - Handles schemes (`http://`, `https://`, no scheme), auth (`user:pass@host`), and default ports (443 if https, 8080 if http/no scheme).
   - `probe_tls_certificate(host: str, port: int, timeout: float = 3.0) -> tuple[bytes, str] | None`:
     - Establishes a TLS connection using an unverified `ssl.SSLContext()` to capture peer certificate bytes.
     - Converts DER bytes to PEM string with `ssl.DER_cert_to_PEM_cert()`.
     - Returns `(der_bytes, pem_str)` or `None` if non-TLS or failed.
   - `get_available_trust_stores() -> list[TrustStoreConfig]`:
     - Returns list of detected `TrustStoreConfig` instances where `cert_dir.is_dir()` and `shutil.which(update_cmd[0])` is found.
   - `get_installed_ca_fingerprints(cert_dir: Path, extensions: tuple[str, ...] = (".crt", ".pem")) -> set[str]`:
     - Reads all certificate files in `cert_dir`, extracts DER certificates via `ssl.PEM_cert_to_DER_cert()`, and computes SHA-256 fingerprints.
   - `install_ca_certificate_to_store(pem_cert: str, host: str, port: int, fingerprint: str, store: TrustStoreConfig) -> bool`:
     - Writes certificate to `store.cert_dir / f"proxy_{host}_{port}_{fingerprint[:8]}.crt"`.
     - Executes `store.update_cmd` via `subprocess.run`.
   - `configure_proxy_ca_certificates(logger: Any | None = None) -> list[str]`:
     - Main entry point tying all steps together, called during application startup.

2. **`src/cyberautoagent.py`**:
   - Import `configure_proxy_ca_certificates` from `modules.utils.proxy`.
   - Call `configure_proxy_ca_certificates(logger=logger)` inside `main()` right after logger initialization.

### Architecture Diagram
```mermaid
graph TD
    A[Agent Startup in cyberautoagent.py] --> B[Read HTTP_PROXY & HTTPS_PROXY]
    B --> C{Proxy Set?}
    C -- No --> Z[Continue Normal Startup]
    C -- Yes --> D[Extract Host & Port]
    D --> E[Probe TLS Handshake & Fetch Cert]
    E -- No TLS / Refused --> Z
    E -- TLS Responded --> F{Detect Available Trust Stores}
    F -- Debian Found --> G1[Check /usr/local/share/ca-certificates]
    F -- RHEL Found --> G2[Check /etc/pki/ca-trust/source/anchors]
    F -- None Found --> G3[Log Info / Skip OS Trust Store]
    G3 --> Z
    G1 --> H1{Fingerprint in Debian Store?}
    G2 --> H2{Fingerprint in RHEL Store?}
    H1 -- Yes --> I1[Skip Debian Store]
    H2 -- Yes --> I2[Skip RHEL Store]
    H1 -- No --> J1[Write .crt & Run update-ca-certificates]
    H2 -- No --> J2[Write .crt & Run update-ca-trust extract]
    J1 --> K[Log Trust Store Updated]
    J2 --> K
    I1 --> Z
    I2 --> Z
    K --> Z
```

### File Structure
- `src/modules/utils/proxy.py` (New): Proxy endpoint extraction, TLS probing, Debian & RHEL trust store management, certificate deduplication.
- `src/cyberautoagent.py` (Modified): Hook `configure_proxy_ca_certificates()` into `main()`.
- `tests/test_proxy_ca_certificates.py` (New): Unit tests for proxy extraction, TLS probes, fingerprinting, Debian & RHEL trust store updates, and error handling.
- `CHANGELOG.md` (Modified): Document the new proxy CA trust store integration for Debian and RHEL under `### Features`.

### Risks & Mitigations
- **Slow / Unresponsive Proxy Hosts**:
  - *Mitigation*: Strict 3-second socket timeout during TLS probing.
- **Permission Denied (Non-root user)**:
  - *Mitigation*: Catch `PermissionError` and `OSError` when accessing trust store directories or executing update commands, logging a helpful warning rather than raising an unhandled exception.
- **Differences in Bundle Update Commands across RHEL versions**:
  - *Mitigation*: Use `update-ca-trust extract` (compatible with RHEL 7, 8, 9, Fedora, CentOS Stream, Rocky, AlmaLinux) and fallback to `update-ca-trust`.

# Testing

### Validation Approach
Automated tests will validate the proxy certificate installation pipeline across all operational modes and edge cases using `pytest` and mock objects for socket, SSL, filesystem, and subprocess interactions.

### Key Scenarios
1. **Valid TLS Intercepting Proxy on Debian**:
   - Environment has `HTTPS_PROXY="https://127.0.0.1:8080"`.
   - TLS probe connects and receives a mock CA certificate.
   - Debian trust store `/usr/local/share/ca-certificates` exists and does not contain the fingerprint.
   - Assert certificate file is created and `update-ca-certificates` is executed.
2. **Valid TLS Intercepting Proxy on RHEL**:
   - Environment has `HTTP_PROXY="http://proxy.internal:8080"`.
   - TLS probe connects and receives a mock CA certificate.
   - RHEL trust store `/etc/pki/ca-trust/source/anchors` exists and does not contain the fingerprint.
   - Assert certificate file is created in `/etc/pki/ca-trust/source/anchors/` and `update-ca-trust extract` is executed.
3. **Certificate Fingerprint Already Installed (Idempotency)**:
   - Certificate file with matching SHA-256 fingerprint already exists in the target store.
   - Assert no new file is created and update commands are not run.
4. **Plain HTTP Proxy (Non-TLS)**:
   - Environment has `HTTP_PROXY="http://proxy.internal:3128"`.
   - TLS probe fails (SSLError or socket connection timeout).
   - Assert graceful exit with no filesystem writes or errors.
5. **Unsupported Operating System / Missing Directories**:
   - Neither Debian nor RHEL trust store directories or update commands are present (e.g. macOS).
   - Assert probe runs, logs informative message, and exits cleanly without errors.
6. **No Proxy Configured**:
   - Neither `HTTP_PROXY` nor `HTTPS_PROXY` is set.
   - Assert immediate return with 0 probes executed.

### Edge Cases
- **Scheme-less and Unusual Formats**:
  - `192.168.1.50:8080`, `http://user:password@proxy:8443`, `https://[::1]:8080` (IPv6).
- **Socket Timeouts & Network Failures**:
  - Unreachable proxy IPs or firewalled ports timing out after 3 seconds.
- **Permission Denied**:
  - Writing to `/usr/local/share/ca-certificates` or `/etc/pki/ca-trust/source/anchors` fails due to non-root permissions; verify warning log is emitted and process continues smoothly.

### Test Changes
- Add `tests/test_proxy_ca_certificates.py` covering:
  - `test_extract_proxy_endpoints` (schemes, formats, defaults, deduplication)
  - `test_probe_tls_certificate_success` & `test_probe_tls_certificate_non_tls`
  - `test_compute_cert_fingerprint`
  - `test_install_ca_certificate_debian` & `test_install_ca_certificate_rhel`
  - `test_deduplication_existing_fingerprint`
  - `test_unsupported_os_skip`
  - `test_permission_error_graceful_handling`
  - `test_configure_proxy_ca_certificates_end_to_end`
- Verification commands:
  - `UV_CACHE_DIR="$PWD/.uv-cache" uv run ruff check src tests`
  - `KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_proxy_ca_certificates.py -v`
  - `KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run coverage run -m pytest tests/test_proxy_ca_certificates.py`
  - `KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run coverage report`

# Delivery Steps

### ✓ Step 1: Implement proxy netloc extraction, TLS probing, and certificate fingerprinting
Core proxy netloc parsing, TLS probing, PEM certificate extraction, and SHA-256 fingerprint calculation utilities are implemented and tested.

- Create `src/modules/utils/proxy.py` with `extract_proxy_endpoints(env_vars: tuple[str, ...]) -> list[tuple[str, int]]` to normalize `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, and `https_proxy` URLs/netlocs (handling scheme presence/absence, credentials, default ports).
- Implement `probe_tls_certificate(host: str, port: int, timeout: float = 3.0) -> tuple[bytes, str] | None` using `ssl.create_default_context()` with `CERT_NONE` to fetch DER/PEM bytes and return `(der_bytes, pem_str)`.
- Implement `compute_cert_fingerprint(der_bytes: bytes) -> str` using `hashlib.sha256` to produce a normalized hex fingerprint string.
- Ensure socket timeouts and non-TLS connection failures (e.g. plain HTTP proxies, connection timeouts, refused connections) are handled gracefully without raising unhandled exceptions.

### ✓ Step 2: Implement Debian and RHEL trust store detection, certificate persistence, and bundle updates
Multi-distribution trust store inspection, fingerprint deduplication against existing certificates, certificate file writing, and trust bundle update commands are implemented.

- Define `TrustStoreConfig` supporting Debian (`/usr/local/share/ca-certificates`, `update-ca-certificates`) and RHEL/CentOS/Fedora (`/etc/pki/ca-trust/source/anchors`, `update-ca-trust extract`).
- Implement `get_available_trust_stores() -> list[TrustStoreConfig]` checking directory existence and executable availability in PATH.
- Implement `get_installed_ca_fingerprints(cert_dir: Path) -> set[str]` that iterates existing `.crt` and `.pem` files in the trust store, extracts DER representations, and checks if the SHA-256 fingerprint matches.
- Implement `install_ca_certificate_to_store(pem_cert: str, host: str, port: int, fingerprint: str, store: TrustStoreConfig) -> bool` to write `proxy_{host}_{port}_{fingerprint[:8]}.crt` safely and execute the corresponding update command via `subprocess.run`.
- Handle permission errors gracefully (e.g. when run without root privileges) with descriptive warning logs.

### ✓ Step 3: Integrate proxy trust store setup into agent startup lifecycle
Proxy certificate auto-configuration is executed during agent startup in `cyberautoagent.py` and emits clear logging.

- Implement entry function `configure_proxy_ca_certificates(logger: Any | None = None) -> list[str]` orchestrating extraction, TLS probing, store detection, deduplication, installation, and bundle refresh across detected trust stores.
- Call `configure_proxy_ca_certificates` early in `cyberautoagent.py:main()` (before tool auto-setup, external requests, and agent initialization).
- Log informational and warning messages detailing detected intercepting proxies, installed certificates, skipped existing certificates, and unsupported OS fallback behavior.

### ✓ Step 4: Add unit tests, verify test coverage, and update CHANGELOG.md
Comprehensive test suite covering Debian, RHEL, positive, negative, and edge-case scenarios is added with >=80% branch coverage, and CHANGELOG.md is updated.

- Create `tests/test_proxy_ca_certificates.py` testing netloc parsing (with/without scheme, ports, user authentication info), TLS probing, certificate fingerprint deduplication, Debian trust store installation, RHEL trust store installation, and unsupported OS skipping.
- Add mock tests for socket/SSL handshake responses (TLS proxy, plain HTTP proxy, network timeouts, connection refused).
- Add tests mocking `/usr/local/share/ca-certificates` and `/etc/pki/ca-trust/source/anchors` filesystem interactions and subprocess execution for `update-ca-certificates` and `update-ca-trust`.
- Update `CHANGELOG.md` under `### Features` documenting automatic Debian and RHEL trust store installation for intercepting proxies.
- Validate formatting and linting with `uv run ruff check` and execute test suite with `uv run pytest`.