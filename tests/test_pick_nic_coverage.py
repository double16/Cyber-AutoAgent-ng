import socket
from types import SimpleNamespace

import pytest

from modules.utils import pick_nic as mod


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
