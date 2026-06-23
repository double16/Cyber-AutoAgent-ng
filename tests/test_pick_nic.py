from __future__ import annotations

import socket
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

import modules.utils.pick_nic as mod


def test_pick_local_addr_loopback_ipv4():
    ip, fam = mod.pick_local_addr("127.0.0.1", 53)
    assert ip == "127.0.0.1"
    assert fam == socket.AF_INET


def test_pick_local_addr_invalid_destination_raises():
    # getaddrinfo should fail for an invalid IP literal
    with pytest.raises((socket.gaierror, OSError)):
        mod.pick_local_addr("256.256.256.256", 53)


def test_pick_local_addr_loopback_ipv6_if_available():
    try:
        ip, fam = mod.pick_local_addr("::1", 53)
    except (socket.gaierror, OSError):
        pytest.skip("IPv6 loopback not available on this system")

    assert ip == "::1"
    assert fam == socket.AF_INET6


def test_map_ip_to_interfaces_returns_empty_when_psutil_unavailable(monkeypatch):
    monkeypatch.setattr(mod, "psutil", None)
    assert mod.map_ip_to_interfaces("127.0.0.1", socket.AF_INET) == []


def test_map_ip_to_interfaces_returns_empty_when_no_interface_matches(monkeypatch):
    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(mod, "psutil", psutil)

    # TEST-NET-3, should not be assigned locally
    assert mod.map_ip_to_interfaces("203.0.113.123", socket.AF_INET) == []


def test_map_ip_to_interfaces_real_loopback_mapping(monkeypatch):
    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(mod, "psutil", psutil)

    ip, fam = mod.pick_local_addr("127.0.0.1", 53)
    ifnames = mod.map_ip_to_interfaces(ip, fam)

    # Should map to some loopback interface name (lo, lo0, Loopback Pseudo-Interface, etc.)
    assert isinstance(ifnames, list)
    assert len(ifnames) >= 1

    # Sanity: returned names exist in psutil's interface list
    all_ifaces = set(psutil.net_if_addrs().keys())
    assert set(ifnames).issubset(all_ifaces)



def test_pick_local_addr_returns_first_connectable_address(monkeypatch):
    sockets = []

    class FakeSocket:
        def __init__(self, family, socktype):
            self.family = family
            sockets.append(self)

        def connect(self, sockaddr):
            if sockaddr[0] == "bad":
                raise OSError("no route")

        def getsockname(self):
            return ("192.0.2.10", 12345)

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        mod.socket,
        "getaddrinfo",
        lambda *args: [
            (socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("bad", 53)),
            (socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("good", 53)),
        ],
    )
    monkeypatch.setattr(mod.socket, "socket", FakeSocket)

    assert mod.pick_local_addr("example.test") == ("192.0.2.10", socket.AF_INET)
    assert all(sock.closed for sock in sockets)


def test_pick_local_addr_raises_when_all_addresses_fail(monkeypatch):
    class FakeSocket:
        def connect(self, sockaddr):
            raise OSError("blocked")

        def close(self):
            pass

    monkeypatch.setattr(
        mod.socket,
        "getaddrinfo",
        lambda *args: [(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("bad", 53))],
    )
    monkeypatch.setattr(mod.socket, "socket", lambda *args: FakeSocket())

    with pytest.raises(OSError, match="Could not determine local address"):
        mod.pick_local_addr("example.test")


def test_map_ip_to_interfaces_handles_psutil_absent_and_matches(monkeypatch):
    monkeypatch.setattr(mod, "psutil", None)
    assert mod.map_ip_to_interfaces("192.0.2.10", socket.AF_INET) == []

    monkeypatch.setattr(
        mod,
        "psutil",
        SimpleNamespace(
            net_if_addrs=lambda: {
                "en1": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.10")],
                "en0": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.10")],
                "lo0": [SimpleNamespace(family=socket.AF_INET6, address="::1")],
            }
        ),
    )

    assert mod.map_ip_to_interfaces("192.0.2.10", socket.AF_INET) == ["en0", "en1"]
    assert mod.map_ip_to_interfaces("::1", socket.AF_INET6) == ["lo0"]


def test_pick_nic_error_and_default_paths(monkeypatch):
    class FakeSocket:
        family = 2

        def __init__(self, *_):
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def connect(self, _dest):
            raise OSError("no route")

        def close(self):
            self.closed = True

    monkeypatch.setattr(mod.socket, "getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 0, "", ("1.2.3.4", 53))])
    monkeypatch.setattr(mod.socket, "socket", FakeSocket)
    with pytest.raises(OSError):
        mod.pick_local_addr("1.2.3.4", 53)

    class GoodSocket:
        def settimeout(self, _timeout):
            pass

        def connect(self, _dest):
            pass

        def getsockname(self):
            return ("10.0.0.2", 12345)

        def close(self):
            pass

    monkeypatch.setattr(mod.socket, "socket", lambda *_args: GoodSocket())
    assert mod.pick_local_addr("1.2.3.4", 53) == ("10.0.0.2", 2)

    monkeypatch.setattr(
        mod,
        "psutil",
        SimpleNamespace(
            net_if_addrs=lambda: {
                "eth0": [SimpleNamespace(family=mod.socket.AF_INET, address="10.0.0.2")],
                "lo": [SimpleNamespace(family=mod.socket.AF_INET, address="127.0.0.1")],
            }
        ),
    )
    assert mod.map_ip_to_interfaces("10.0.0.2", mod.socket.AF_INET) == ["eth0"]
    monkeypatch.setattr(mod, "psutil", None)
    assert mod.map_ip_to_interfaces("10.0.0.2", mod.socket.AF_INET) == []


def test_pick_nic_main_outputs_interface_states(monkeypatch, capsys):
    monkeypatch.setattr(mod, "pick_local_addr", Mock(return_value=("fe80::1", mod.socket.AF_INET6)))
    monkeypatch.setattr(mod, "map_ip_to_interfaces", Mock(return_value=["en0", "utun0"]))
    monkeypatch.setattr(mod.argparse._sys, "argv", ["pick-nic", "example.com", "--port", "443"])

    mod.main()

    output = capsys.readouterr().out
    assert "Address family  : IPv6" in output
    assert "Interface       : en0, utun0" in output

