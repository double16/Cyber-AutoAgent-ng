"""
Unit tests for Proxy CA Trust Store Utilities
============================================
"""

import hashlib
import os
import ssl
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from modules.utils.proxy import (
    DEFAULT_LANGFUSE_NO_PROXY_HOSTS,
    TrustStoreConfig,
    _parse_proxy_string,
    build_no_proxy_string,
    compute_cert_fingerprint,
    configure_langfuse_proxy_bypass,
    configure_proxy_ca_certificates,
    ensure_proxy_variables,
    extract_langfuse_hosts,
    extract_proxy_endpoints,
    fetch_http_certificate,
    fetch_proxy_certificate,
    get_available_trust_stores,
    get_installed_ca_fingerprints,
    install_ca_certificate_to_store,
    parse_certificate_data,
    probe_tls_certificate,
)

# Standard sample self-signed DER and PEM certificate for testing
SAMPLE_DER_BYTES = b"\x30\x82\x01\x00\x30\x82\x00\xfa" + b"\x00" * 250
SAMPLE_PEM_CERT = ssl.DER_cert_to_PEM_cert(SAMPLE_DER_BYTES)
SAMPLE_FINGERPRINT = hashlib.sha256(SAMPLE_DER_BYTES).hexdigest().lower()


class TestProxyEndpointExtraction:
    """Test extracting and normalizing proxy endpoints."""

    def test_extract_proxy_endpoints_empty_env(self):
        endpoints = extract_proxy_endpoints(environ={})
        assert endpoints == []

    def test_extract_proxy_endpoints_standard_schemes(self):
        environ = {
            "HTTP_PROXY": "http://proxy.internal:8080",
            "HTTPS_PROXY": "https://secure-proxy.internal:8443",
        }
        endpoints = extract_proxy_endpoints(environ=environ)
        assert endpoints == [
            ("proxy.internal", 8080),
            ("secure-proxy.internal", 8443),
        ]

    def test_extract_proxy_endpoints_schemeless(self):
        environ = {
            "HTTP_PROXY": "127.0.0.1:8080",
            "HTTPS_PROXY": "10.0.0.1:9090",
        }
        endpoints = extract_proxy_endpoints(environ=environ)
        assert endpoints == [
            ("127.0.0.1", 8080),
            ("10.0.0.1", 9090),
        ]

    def test_extract_proxy_endpoints_with_auth(self):
        environ = {
            "HTTP_PROXY": "http://user:secret_pass@proxy.corp:8888",
        }
        endpoints = extract_proxy_endpoints(environ=environ)
        assert endpoints == [("proxy.corp", 8888)]

    def test_extract_proxy_endpoints_default_ports(self):
        environ = {
            "HTTP_PROXY": "http://plain-proxy.corp",
            "HTTPS_PROXY": "https://secure-proxy.corp",
        }
        endpoints = extract_proxy_endpoints(environ=environ)
        assert endpoints == [
            ("plain-proxy.corp", 8080),
            ("secure-proxy.corp", 443),
        ]

    def test_extract_proxy_endpoints_deduplication(self):
        environ = {
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "http_proxy": "127.0.0.1:8080",
            "https_proxy": "http://127.0.0.1:8080/",
        }
        endpoints = extract_proxy_endpoints(environ=environ)
        assert endpoints == [("127.0.0.1", 8080)]

    def test_parse_proxy_string_edge_cases(self):
        assert _parse_proxy_string("") is None
        assert _parse_proxy_string("   ") is None
        assert _parse_proxy_string("http://") is None
        assert _parse_proxy_string("://invalid") is None
        assert _parse_proxy_string("[::1]:8080") == ("::1", 8080)


class TestCertificateParsing:
    """Test parsing of certificate data from raw bytes (PEM and DER)."""

    def test_parse_certificate_data_pem(self):
        result = parse_certificate_data(SAMPLE_PEM_CERT.encode("utf-8"))
        assert result is not None
        der_bytes, pem_str = result
        assert der_bytes == SAMPLE_DER_BYTES
        assert "-----BEGIN CERTIFICATE-----" in pem_str

    def test_parse_certificate_data_der(self):
        result = parse_certificate_data(SAMPLE_DER_BYTES)
        assert result is not None
        der_bytes, pem_str = result
        assert der_bytes == SAMPLE_DER_BYTES
        assert "-----BEGIN CERTIFICATE-----" in pem_str

    def test_parse_certificate_data_invalid(self):
        assert parse_certificate_data(b"<html><body>404 Not Found</body></html>") is None
        assert parse_certificate_data(b"random binary bytes 1234567890") is None

    def test_parse_certificate_data_empty_and_short(self):
        assert parse_certificate_data(b"") is None
        assert parse_certificate_data(b"short") is None


class TestHttpCertificateEndpoints:
    """Test fetching CA certificates via known HTTP proxy endpoints."""

    @patch("http.client.HTTPConnection")
    def test_fetch_http_certificate_burp_der_success(self, mock_http_conn_cls):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = SAMPLE_DER_BYTES

        mock_conn.getresponse.return_value = mock_resp
        mock_http_conn_cls.return_value = mock_conn

        result = fetch_http_certificate("127.0.0.1", 8080)
        assert result is not None
        der_bytes, pem_str = result
        assert der_bytes == SAMPLE_DER_BYTES
        assert "-----BEGIN CERTIFICATE-----" in pem_str

        # Verify it requested /cert
        mock_conn.request.assert_called_with(
            "GET",
            "/cert",
            headers={
                "Host": "127.0.0.1:8080",
                "User-Agent": "CyberAutoAgent/1.0",
                "Accept": "*/*",
            },
        )
        mock_conn.close.assert_called()

    @patch("http.client.HTTPConnection")
    def test_fetch_http_certificate_zap_rootcert_success(self, mock_http_conn_cls):
        mock_conn = MagicMock()
        mock_http_conn_cls.return_value = mock_conn

        # Burp /cert returns 404, then ZAP /OTHER/core/other/rootcert/ returns 200 with PEM
        def mock_request(method, path, headers=None):
            pass

        def mock_getresponse():
            # Get the path requested
            last_call = mock_conn.request.call_args
            path_called = last_call[0][1]
            resp = MagicMock()
            if path_called == "/OTHER/core/other/rootcert/":
                resp.status = 200
                resp.read.return_value = SAMPLE_PEM_CERT.encode("utf-8")
            else:
                resp.status = 404
                resp.read.return_value = b"Not Found"
            return resp

        mock_conn.request.side_effect = mock_request
        mock_conn.getresponse.side_effect = mock_getresponse

        result = fetch_http_certificate("127.0.0.1", 8080)
        assert result is not None
        der_bytes, pem_str = result
        assert der_bytes == SAMPLE_DER_BYTES
        assert "-----BEGIN CERTIFICATE-----" in pem_str

    @patch("http.client.HTTPConnection")
    def test_fetch_http_certificate_mitmproxy_success(self, mock_http_conn_cls):
        mock_conn = MagicMock()
        mock_http_conn_cls.return_value = mock_conn

        def mock_getresponse():
            last_call = mock_conn.request.call_args
            path_called = last_call[0][1]
            resp = MagicMock()
            if path_called == "/cert/pem":
                resp.status = 200
                resp.read.return_value = SAMPLE_PEM_CERT.encode("utf-8")
            else:
                resp.status = 404
                resp.read.return_value = b"Not Found"
            return resp

        mock_conn.getresponse.side_effect = mock_getresponse

        result = fetch_http_certificate("127.0.0.1", 8080)
        assert result is not None
        der_bytes, pem_str = result
        assert der_bytes == SAMPLE_DER_BYTES
        assert "-----BEGIN CERTIFICATE-----" in pem_str

    @patch("http.client.HTTPConnection")
    def test_fetch_http_certificate_all_fail(self, mock_http_conn_cls):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.read.return_value = b"Not Found"
        mock_conn.getresponse.return_value = mock_resp
        mock_http_conn_cls.return_value = mock_conn

        result = fetch_http_certificate("127.0.0.1", 8080)
        assert result is None

    @patch("http.client.HTTPConnection", side_effect=ConnectionRefusedError("Connection refused"))
    def test_fetch_http_certificate_connection_refused(self, _mock_conn_cls):
        result = fetch_http_certificate("127.0.0.1", 8080)
        assert result is None


class TestFetchProxyCertificate:
    """Test composite proxy certificate fetching with HTTP first and TLS fallback."""

    @patch("modules.utils.proxy.fetch_http_certificate")
    @patch("modules.utils.proxy.probe_tls_certificate")
    def test_fetch_proxy_certificate_prefers_http(self, mock_tls_probe, mock_http_fetch):
        mock_http_fetch.return_value = (SAMPLE_DER_BYTES, SAMPLE_PEM_CERT)

        result = fetch_proxy_certificate("127.0.0.1", 8080)
        assert result == (SAMPLE_DER_BYTES, SAMPLE_PEM_CERT)
        mock_http_fetch.assert_called_once_with("127.0.0.1", 8080, timeout=3.0)
        mock_tls_probe.assert_not_called()

    @patch("modules.utils.proxy.fetch_http_certificate")
    @patch("modules.utils.proxy.probe_tls_certificate")
    def test_fetch_proxy_certificate_falls_back_to_tls(self, mock_tls_probe, mock_http_fetch):
        mock_http_fetch.return_value = None
        mock_tls_probe.return_value = (SAMPLE_DER_BYTES, SAMPLE_PEM_CERT)

        result = fetch_proxy_certificate("127.0.0.1", 8080)
        assert result == (SAMPLE_DER_BYTES, SAMPLE_PEM_CERT)
        mock_http_fetch.assert_called_once_with("127.0.0.1", 8080, timeout=3.0)
        mock_tls_probe.assert_called_once_with("127.0.0.1", 8080, timeout=3.0)

    @patch("modules.utils.proxy.fetch_http_certificate")
    @patch("modules.utils.proxy.probe_tls_certificate")
    def test_fetch_proxy_certificate_all_fail(self, mock_tls_probe, mock_http_fetch):
        mock_http_fetch.return_value = None
        mock_tls_probe.return_value = None

        result = fetch_proxy_certificate("127.0.0.1", 8080)
        assert result is None
        mock_http_fetch.assert_called_once()
        mock_tls_probe.assert_called_once()


class TestFingerprintAndProbing:
    """Test certificate fingerprint calculation and TLS socket probing."""

    def test_compute_cert_fingerprint(self):
        raw_bytes = b"sample certificate binary content"
        expected = hashlib.sha256(raw_bytes).hexdigest().lower()
        assert compute_cert_fingerprint(raw_bytes) == expected

    @patch("socket.create_connection")
    @patch("ssl.create_default_context")
    def test_probe_tls_certificate_success(self, mock_ssl_context, mock_create_conn):
        mock_sock = MagicMock()
        mock_create_conn.return_value = mock_sock

        mock_ssl_sock = MagicMock()
        mock_ssl_sock.getpeercert.return_value = SAMPLE_DER_BYTES

        mock_context_instance = MagicMock()
        mock_context_instance.wrap_socket.return_value = mock_ssl_sock
        mock_ssl_context.return_value = mock_context_instance

        result = probe_tls_certificate("127.0.0.1", 8080)
        assert result is not None
        der_bytes, pem_str = result
        assert der_bytes == SAMPLE_DER_BYTES
        assert "-----BEGIN CERTIFICATE-----" in pem_str
        mock_ssl_sock.close.assert_called()

    @patch("socket.create_connection")
    @patch("ssl.create_default_context")
    def test_probe_tls_certificate_empty_peercert(self, mock_ssl_context, mock_create_conn):
        mock_sock = MagicMock()
        mock_create_conn.return_value = mock_sock

        mock_ssl_sock = MagicMock()
        mock_ssl_sock.getpeercert.return_value = b""

        mock_context_instance = MagicMock()
        mock_context_instance.wrap_socket.return_value = mock_ssl_sock
        mock_ssl_context.return_value = mock_context_instance

        result = probe_tls_certificate("127.0.0.1", 8080)
        assert result is None
        mock_ssl_sock.close.assert_called()

    @patch("socket.create_connection", side_effect=TimeoutError("Connection timed out"))
    def test_probe_tls_certificate_timeout(self, _mock_conn):
        result = probe_tls_certificate("127.0.0.1", 8080, timeout=1.0)
        assert result is None

    @patch("socket.create_connection", side_effect=ConnectionRefusedError("Connection refused"))
    def test_probe_tls_certificate_connection_refused(self, _mock_conn):
        result = probe_tls_certificate("127.0.0.1", 8080)
        assert result is None

    @patch("socket.create_connection")
    @patch("ssl.create_default_context")
    def test_probe_tls_certificate_ssl_error(self, mock_ssl_context, mock_create_conn):
        mock_sock = MagicMock()
        mock_create_conn.return_value = mock_sock

        mock_context_instance = MagicMock()
        mock_context_instance.wrap_socket.side_effect = ssl.SSLError("Handshake failure (plain HTTP)")
        mock_ssl_context.return_value = mock_context_instance

        result = probe_tls_certificate("127.0.0.1", 8080)
        assert result is None
        mock_sock.close.assert_called()


class TestTrustStoreManagement:
    """Test trust store detection, fingerprint scanning, and certificate installation."""

    def test_get_available_trust_stores_detection(self, tmp_path, monkeypatch):
        debian_dir = tmp_path / "debian_ca"
        debian_dir.mkdir()
        rhel_dir = tmp_path / "rhel_ca"
        rhel_dir.mkdir()

        store_debian = TrustStoreConfig(
            name="debian",
            cert_dir=debian_dir,
            update_cmd=["update-ca-certificates"],
        )
        store_rhel = TrustStoreConfig(
            name="rhel",
            cert_dir=rhel_dir,
            update_cmd=["update-ca-trust", "extract"],
        )

        monkeypatch.setattr(
            "shutil.which",
            lambda cmd: f"/usr/sbin/{cmd}" if cmd in {"update-ca-certificates", "update-ca-trust"} else None,
        )

        available = get_available_trust_stores(stores=(store_debian, store_rhel))
        assert len(available) == 2
        assert {s.name for s in available} == {"debian", "rhel"}

    def test_get_available_trust_stores_missing_binary(self, tmp_path, monkeypatch):
        debian_dir = tmp_path / "debian_ca"
        debian_dir.mkdir()

        store_debian = TrustStoreConfig(
            name="debian",
            cert_dir=debian_dir,
            update_cmd=["update-ca-certificates"],
        )

        monkeypatch.setattr("shutil.which", lambda _cmd: None)

        available = get_available_trust_stores(stores=(store_debian,))
        assert available == []

    def test_get_available_trust_stores_missing_dir(self, tmp_path, monkeypatch):
        non_existent_dir = tmp_path / "non_existent"

        store_debian = TrustStoreConfig(
            name="debian",
            cert_dir=non_existent_dir,
            update_cmd=["update-ca-certificates"],
        )

        monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/sbin/update-ca-certificates")

        available = get_available_trust_stores(stores=(store_debian,))
        assert available == []

    def test_get_installed_ca_fingerprints(self, tmp_path):
        cert_dir = tmp_path / "ca-certificates"
        cert_dir.mkdir()

        # Write valid PEM cert
        cert1_file = cert_dir / "proxy_burp.crt"
        cert1_file.write_text(SAMPLE_PEM_CERT, encoding="utf-8")

        # Write raw DER file
        raw_der = b"dummy_raw_der_bytes_content"
        cert2_file = cert_dir / "custom.pem"
        cert2_file.write_bytes(raw_der)

        # Write non-cert file
        txt_file = cert_dir / "notes.txt"
        txt_file.write_text("not a cert", encoding="utf-8")

        fingerprints = get_installed_ca_fingerprints(cert_dir)
        assert SAMPLE_FINGERPRINT in fingerprints
        assert hashlib.sha256(raw_der).hexdigest().lower() in fingerprints

    def test_get_installed_ca_fingerprints_nonexistent_or_permission_error(self, tmp_path):
        non_existent = tmp_path / "does_not_exist"
        assert get_installed_ca_fingerprints(non_existent) == set()

        cert_dir = tmp_path / "ca-certs"
        cert_dir.mkdir()
        with patch.object(Path, "iterdir", side_effect=PermissionError("Permission denied")):
            assert get_installed_ca_fingerprints(cert_dir) == set()

    def test_install_ca_certificate_to_store_success(self, tmp_path):
        cert_dir = tmp_path / "ca-certificates"
        cert_dir.mkdir()

        store = TrustStoreConfig(
            name="debian",
            cert_dir=cert_dir,
            update_cmd=["update-ca-certificates"],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["update-ca-certificates"],
                returncode=0,
                stdout="1 added, 0 removed; done.",
                stderr="",
            )

            success = install_ca_certificate_to_store(
                pem_cert=SAMPLE_PEM_CERT,
                host="127.0.0.1",
                port=8080,
                fingerprint=SAMPLE_FINGERPRINT,
                store=store,
            )

            assert success is True
            expected_file = cert_dir / f"proxy_127.0.0.1_8080_{SAMPLE_FINGERPRINT[:8]}.crt"
            assert expected_file.is_file()
            assert expected_file.read_text(encoding="utf-8") == SAMPLE_PEM_CERT.strip() + "\n"
            mock_run.assert_called_once_with(
                ["update-ca-certificates"],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_install_ca_certificate_to_store_rhel_fallback(self, tmp_path):
        cert_dir = tmp_path / "anchors"
        cert_dir.mkdir()

        store = TrustStoreConfig(
            name="rhel",
            cert_dir=cert_dir,
            update_cmd=["update-ca-trust", "extract"],
            fallback_update_cmd=["update-ca-trust"],
        )

        with patch("subprocess.run") as mock_run:
            # First command fails, fallback succeeds
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["update-ca-trust", "extract"],
                    returncode=1,
                    stdout="",
                    stderr="unknown option extract",
                ),
                subprocess.CompletedProcess(
                    args=["update-ca-trust"],
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
            ]

            success = install_ca_certificate_to_store(
                pem_cert=SAMPLE_PEM_CERT,
                host="proxy.corp",
                port=8443,
                fingerprint=SAMPLE_FINGERPRINT,
                store=store,
            )

            assert success is True
            assert mock_run.call_count == 2

    def test_install_ca_certificate_to_store_write_failure(self, tmp_path):
        cert_dir = tmp_path / "ca-certificates"
        cert_dir.mkdir()

        store = TrustStoreConfig(
            name="debian",
            cert_dir=cert_dir,
            update_cmd=["update-ca-certificates"],
        )

        with patch.object(Path, "write_text", side_effect=PermissionError("Permission denied")):
            success = install_ca_certificate_to_store(
                pem_cert=SAMPLE_PEM_CERT,
                host="127.0.0.1",
                port=8080,
                fingerprint=SAMPLE_FINGERPRINT,
                store=store,
            )
            assert success is False

    def test_install_ca_certificate_to_store_update_cmd_failure(self, tmp_path):
        cert_dir = tmp_path / "ca-certificates"
        cert_dir.mkdir()

        store = TrustStoreConfig(
            name="debian",
            cert_dir=cert_dir,
            update_cmd=["update-ca-certificates"],
        )

        with patch("subprocess.run", side_effect=FileNotFoundError("Command not found")):
            success = install_ca_certificate_to_store(
                pem_cert=SAMPLE_PEM_CERT,
                host="127.0.0.1",
                port=8080,
                fingerprint=SAMPLE_FINGERPRINT,
                store=store,
            )
            assert success is False


class TestConfigureProxyCaCertificatesEndToEnd:
    """Test full workflow of proxy CA certificate configuration."""

    def test_configure_no_proxy_env(self):
        installed = configure_proxy_ca_certificates(environ={})
        assert installed == []

    @patch("modules.utils.proxy.fetch_proxy_certificate")
    def test_configure_proxy_non_tls_and_no_http_cert(self, mock_fetch):
        mock_fetch.return_value = None
        environ = {"HTTP_PROXY": "http://127.0.0.1:8080"}

        installed = configure_proxy_ca_certificates(environ=environ)
        assert installed == []
        mock_fetch.assert_called_once_with("127.0.0.1", 8080, timeout=3.0)

    @patch("modules.utils.proxy.fetch_proxy_certificate")
    def test_configure_proxy_no_available_stores(self, mock_fetch, tmp_path):
        mock_fetch.return_value = (SAMPLE_DER_BYTES, SAMPLE_PEM_CERT)
        environ = {"HTTPS_PROXY": "https://127.0.0.1:8080"}

        # Empty stores
        installed = configure_proxy_ca_certificates(
            environ=environ,
            stores=(),
        )
        assert installed == []

    @patch("modules.utils.proxy.fetch_proxy_certificate")
    def test_configure_proxy_already_trusted(self, mock_fetch, tmp_path, monkeypatch):
        mock_fetch.return_value = (SAMPLE_DER_BYTES, SAMPLE_PEM_CERT)
        cert_dir = tmp_path / "ca-certificates"
        cert_dir.mkdir()

        # Seed the store with the cert already
        (cert_dir / "existing.crt").write_text(SAMPLE_PEM_CERT, encoding="utf-8")

        store = TrustStoreConfig(
            name="debian",
            cert_dir=cert_dir,
            update_cmd=["update-ca-certificates"],
        )

        monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/sbin/update-ca-certificates")
        environ = {"HTTPS_PROXY": "https://127.0.0.1:8080"}

        with patch("modules.utils.proxy.install_ca_certificate_to_store") as mock_install:
            installed = configure_proxy_ca_certificates(
                environ=environ,
                stores=(store,),
            )
            assert installed == []
            mock_install.assert_not_called()

    @patch("modules.utils.proxy.fetch_proxy_certificate")
    def test_configure_proxy_install_debian_and_rhel(self, mock_fetch, tmp_path, monkeypatch):
        mock_fetch.return_value = (SAMPLE_DER_BYTES, SAMPLE_PEM_CERT)
        debian_dir = tmp_path / "debian_ca"
        debian_dir.mkdir()
        rhel_dir = tmp_path / "rhel_ca"
        rhel_dir.mkdir()

        store_debian = TrustStoreConfig(
            name="debian",
            cert_dir=debian_dir,
            update_cmd=["update-ca-certificates"],
        )
        store_rhel = TrustStoreConfig(
            name="rhel",
            cert_dir=rhel_dir,
            update_cmd=["update-ca-trust", "extract"],
        )

        monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/update-tool")
        environ = {"HTTP_PROXY": "http://burp.local:8080"}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["update-tool"],
                returncode=0,
                stdout="done",
                stderr="",
            )

            installed = configure_proxy_ca_certificates(
                environ=environ,
                stores=(store_debian, store_rhel),
            )

            assert len(installed) == 2
            assert installed[0] == f"debian:burp.local:8080:{SAMPLE_FINGERPRINT}"
            assert installed[1] == f"rhel:burp.local:8080:{SAMPLE_FINGERPRINT}"

            assert (debian_dir / f"proxy_burp.local_8080_{SAMPLE_FINGERPRINT[:8]}.crt").is_file()
            assert (rhel_dir / f"proxy_burp.local_8080_{SAMPLE_FINGERPRINT[:8]}.crt").is_file()


class TestAgentIntegration:
    """Test integration of proxy configuration into agent initialization."""

    def test_cyberautoagent_imports_proxy_configure(self):
        import cyberautoagent

        assert hasattr(cyberautoagent, "configure_proxy_ca_certificates")
        assert callable(cyberautoagent.configure_proxy_ca_certificates)
        assert hasattr(cyberautoagent, "configure_langfuse_proxy_bypass")
        assert callable(cyberautoagent.configure_langfuse_proxy_bypass)


class TestLangfuseProxyBypass:
    """Test excluding Langfuse endpoints from intercepting proxy configurations."""

    def test_extract_langfuse_hosts_default(self):
        hosts = extract_langfuse_hosts(environ={})
        for default_host in DEFAULT_LANGFUSE_NO_PROXY_HOSTS:
            assert default_host in hosts

    def test_extract_langfuse_hosts_from_arg(self):
        hosts = extract_langfuse_hosts(langfuse_host="http://custom-langfuse.internal:3000")
        assert "custom-langfuse.internal" in hosts
        assert "custom-langfuse.internal:3000" in hosts
        assert "localhost" in hosts

    def test_extract_langfuse_hosts_from_env_host(self):
        environ = {"LANGFUSE_HOST": "https://langfuse.mycompany.com:8443"}
        hosts = extract_langfuse_hosts(environ=environ)
        assert "langfuse.mycompany.com" in hosts
        assert "langfuse.mycompany.com:8443" in hosts

    def test_extract_langfuse_hosts_from_env_base_url(self):
        environ = {"LANGFUSE_BASE_URL": "http://192.168.1.100:3000"}
        hosts = extract_langfuse_hosts(environ=environ)
        assert "192.168.1.100" in hosts
        assert "192.168.1.100:3000" in hosts

    def test_extract_langfuse_hosts_schemeless_and_ipv6(self):
        hosts = extract_langfuse_hosts(langfuse_host="my-host:3000")
        assert "my-host" in hosts
        assert "my-host:3000" in hosts

        hosts_ipv6 = extract_langfuse_hosts(langfuse_host="http://[::1]:3000")
        assert "::1" in hosts_ipv6
        assert "::1:3000" in hosts_ipv6

    def test_build_no_proxy_string(self):
        assert build_no_proxy_string() == ""
        assert build_no_proxy_string(existing_no_proxy="") == ""

        combined = build_no_proxy_string(
            existing_no_proxy="localhost, 10.0.0.1",
            additional_hosts=["10.0.0.1", "langfuse-web", "LOCALHOST"],
        )
        assert combined == "localhost,10.0.0.1,langfuse-web"

    def test_configure_langfuse_proxy_bypass_env_dict(self):
        env = {
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "LANGFUSE_HOST": "http://langfuse-web:3000",
        }
        res = configure_langfuse_proxy_bypass(environ=env)
        assert "NO_PROXY" in env
        assert "no_proxy" in env
        assert env["NO_PROXY"] == env["no_proxy"]
        assert "localhost" in env["NO_PROXY"]
        assert "127.0.0.1" in env["NO_PROXY"]
        assert "langfuse-web" in env["NO_PROXY"]
        assert "langfuse-web:3000" in env["NO_PROXY"]
        assert res == env["NO_PROXY"]

    def test_configure_langfuse_proxy_bypass_preserves_existing(self):
        env = {
            "NO_PROXY": "internal.service.net,10.0.0.5",
            "LANGFUSE_HOST": "https://cloud.langfuse.com",
        }
        configure_langfuse_proxy_bypass(environ=env)
        assert env["NO_PROXY"].startswith("internal.service.net,10.0.0.5")
        assert "cloud.langfuse.com" in env["NO_PROXY"]

    def test_configure_proxy_ca_certificates_updates_no_proxy(self):
        env = {
            "HTTP_PROXY": "http://127.0.0.1:8080",
        }
        with patch("modules.utils.proxy.fetch_proxy_certificate", return_value=None):
            configure_proxy_ca_certificates(environ=env)
        assert "NO_PROXY" in env
        assert "no_proxy" in env
        assert "localhost" in env["NO_PROXY"]

    def test_cyberautoagent_is_langfuse_available_sets_bypass(self, monkeypatch):
        import cyberautoagent

        env = {"LANGFUSE_HOST": "http://custom-langfuse:3000"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(cyberautoagent.requests, "get", lambda *args, **kwargs: MagicMock(status_code=200))

        assert cyberautoagent.is_langfuse_available() is True
        assert "custom-langfuse" in os.environ.get("NO_PROXY", "")

    def test_cyberautoagent_setup_langfuse_connection_sets_bypass(self, monkeypatch):
        import cyberautoagent

        monkeypatch.setenv("LANGFUSE_HOST", "http://custom-langfuse:3000")
        logger_mock = MagicMock()
        cyberautoagent.setup_langfuse_connection(logger_mock, "compose")
        assert "custom-langfuse" in os.environ.get("NO_PROXY", "")

    def test_prompts_factory_sets_bypass(self, monkeypatch):
        from modules.prompts import factory

        monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
        monkeypatch.setenv("ENABLE_LANGFUSE_PROMPTS", "true")
        monkeypatch.setenv("LANGFUSE_HOST", "http://prompts-langfuse:3000")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"name": "test"}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            factory._lf_get_prompt("test", "latest")
            assert "prompts-langfuse" in os.environ.get("NO_PROXY", "")


class TestEnsureProxyVariables:
    """Test ensuring proxy environment variables have matching upper and lower case entries."""

    PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")

    def _clear_proxy_env(self, monkeypatch):
        for key in self.PROXY_KEYS:
            monkeypatch.delenv(key, raising=False)
            monkeypatch.delenv(key.upper(), raising=False)

    def test_ensure_proxy_variables_from_lowercase(self, monkeypatch):
        """When lowercase proxy variables are set, uppercase ones are created."""
        self._clear_proxy_env(monkeypatch)
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:8080")
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:8443")
        monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1080")
        monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")

        ensure_proxy_variables()

        assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:8080"
        assert os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:8443"
        assert os.environ.get("ALL_PROXY") == "socks5://127.0.0.1:1080"
        assert os.environ.get("NO_PROXY") == "localhost,127.0.0.1"

    def test_ensure_proxy_variables_from_uppercase(self, monkeypatch):
        """When uppercase proxy variables are set, lowercase ones are created."""
        self._clear_proxy_env(monkeypatch)
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp:8080")
        monkeypatch.setenv("HTTPS_PROXY", "https://proxy.corp:8443")
        monkeypatch.setenv("ALL_PROXY", "socks5://proxy.corp:1080")
        monkeypatch.setenv("NO_PROXY", "internal.corp")

        ensure_proxy_variables()

        assert os.environ.get("http_proxy") == "http://proxy.corp:8080"
        assert os.environ.get("https_proxy") == "https://proxy.corp:8443"
        assert os.environ.get("all_proxy") == "socks5://proxy.corp:1080"  # noqa: SIM112
        assert os.environ.get("no_proxy") == "internal.corp"

    def test_ensure_proxy_variables_both_set_syncs_lowercase_to_uppercase(self, monkeypatch):
        """When both lowercase and uppercase are set, lowercase value takes precedence and syncs to upper."""
        self._clear_proxy_env(monkeypatch)
        monkeypatch.setenv("http_proxy", "http://primary:8080")
        monkeypatch.setenv("HTTP_PROXY", "http://stale:8080")

        ensure_proxy_variables()

        assert os.environ.get("http_proxy") == "http://primary:8080"
        assert os.environ.get("HTTP_PROXY") == "http://primary:8080"

    def test_ensure_proxy_variables_no_vars_set(self, monkeypatch):
        """When no proxy variables exist, none are created."""
        self._clear_proxy_env(monkeypatch)

        ensure_proxy_variables()

        for key in self.PROXY_KEYS:
            assert key not in os.environ
            assert key.upper() not in os.environ

    def test_ensure_proxy_variables_mixed(self, monkeypatch):
        """When some are uppercase and some lowercase, both cases are synchronized."""
        self._clear_proxy_env(monkeypatch)
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:8080")
        monkeypatch.setenv("HTTPS_PROXY", "https://127.0.0.1:8443")

        ensure_proxy_variables()

        assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:8080"
        assert os.environ.get("http_proxy") == "http://127.0.0.1:8080"
        assert os.environ.get("HTTPS_PROXY") == "https://127.0.0.1:8443"
        assert os.environ.get("https_proxy") == "https://127.0.0.1:8443"
        assert "all_proxy" not in os.environ
        assert "ALL_PROXY" not in os.environ
        assert "no_proxy" not in os.environ
        assert "NO_PROXY" not in os.environ

    def test_ensure_proxy_variables_empty_string(self, monkeypatch):
        """Empty string proxy variable is propagated correctly."""
        self._clear_proxy_env(monkeypatch)
        monkeypatch.setenv("all_proxy", "")

        ensure_proxy_variables()

        assert os.environ.get("ALL_PROXY") == ""
        assert os.environ.get("all_proxy") == ""  # noqa: SIM112

    def test_cyberautoagent_exports_ensure_proxy_variables(self):
        """Verify cyberautoagent module exports ensure_proxy_variables."""
        import cyberautoagent

        assert hasattr(cyberautoagent, "ensure_proxy_variables")
        assert callable(cyberautoagent.ensure_proxy_variables)
