import os
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import cyberautoagent
from modules.tools.memory import OperationTarget
from modules.utils.target_validation import TargetValidator, validate_operation_targets


class FakeSocket:
    def __init__(self, *, error=None):
        self.error = error
        self.timeout = None
        self.destination = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, destination):
        self.destination = destination
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


class SocketFactory:
    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.calls = []
        self.sockets = []

    def __call__(self, family, socktype, protocol=0):
        self.calls.append((family, socktype, protocol))
        error = self.errors.pop(0) if self.errors else None
        created = FakeSocket(error=error)
        self.sockets.append(created)
        return created


def address_info(host="192.0.2.10", port=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))]


def test_hostname_uses_system_resolver_and_reports_addresses():
    resolver = Mock(return_value=address_info())
    validator = TargetValidator(resolver=resolver)

    result = validator.validate(OperationTarget("target-1", "internal.test", "network"))

    assert result.status == "pass"
    assert result.checks == ("resolve",)
    assert result.resolved_addresses == ("192.0.2.10",)
    resolver.assert_called_once_with("internal.test", None, socket.AF_UNSPEC, socket.SOCK_STREAM)


def test_hostname_resolution_failure_is_reported():
    validator = TargetValidator(resolver=Mock(side_effect=socket.gaierror("not found")))

    result = validator.validate(OperationTarget("target-1", "missing.test", "network"))

    assert result.status == "fail"
    assert result.checks == ("resolve",)
    assert "not found" in result.reason


def test_ip_address_requires_route():
    factory = SocketFactory()
    validator = TargetValidator(socket_factory=factory)

    result = validator.validate(OperationTarget("target-1", "192.0.2.8", "network"))

    assert result.status == "pass"
    assert result.checks == ("route",)
    assert factory.calls == [(socket.AF_INET, socket.SOCK_DGRAM, 0)]
    assert factory.sockets[0].destination == ("192.0.2.8", 9)


def test_ip_route_failure_is_reported():
    validator = TargetValidator(socket_factory=SocketFactory([OSError("no route")]))

    result = validator.validate(OperationTarget("target-1", "2001:db8::10", "network"))

    assert result.status == "fail"
    assert result.checks == ("route",)
    assert "no route" in result.reason


def test_cidr_performs_only_route_check():
    resolver = Mock()
    factory = SocketFactory()
    validator = TargetValidator(resolver=resolver, socket_factory=factory)

    result = validator.validate(OperationTarget("target-1", "192.0.2.0/24", "network_range"))

    assert result.status == "pass"
    assert result.checks == ("route",)
    resolver.assert_not_called()
    assert factory.calls == [(socket.AF_INET, socket.SOCK_DGRAM, 0)]


def test_explicit_tcp_service_connects_after_resolution():
    resolver = Mock(return_value=address_info(port=8443))
    factory = SocketFactory()
    validator = TargetValidator(resolver=resolver, socket_factory=factory)

    result = validator.validate(OperationTarget("target-1", "https://service.test:8443/login", "network"))

    assert result.status == "pass"
    assert result.checks == ("resolve", "tcp_connect")
    assert factory.calls == [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)]
    assert factory.sockets[0].destination == ("192.0.2.10", 8443)


def test_explicit_tcp_service_reports_refusal():
    resolver = Mock(return_value=address_info(port=443))
    validator = TargetValidator(
        resolver=resolver,
        socket_factory=SocketFactory([ConnectionRefusedError("refused")]),
    )

    result = validator.validate(OperationTarget("target-1", "service.test:443", "network"))

    assert result.status == "fail"
    assert result.checks == ("resolve", "tcp_connect")
    assert "TCP connection failed" in result.reason
    assert "refused" in result.reason


def test_explicit_tcp_service_reports_timeout():
    resolver = Mock(return_value=address_info(port=443))
    validator = TargetValidator(
        resolver=resolver,
        socket_factory=SocketFactory([TimeoutError("timed out")]),
    )

    result = validator.validate(OperationTarget("target-1", "service.test:443", "network"))

    assert result.status == "fail"
    assert "timed out" in result.reason


def test_invalid_explicit_port_is_reported_without_connecting():
    factory = SocketFactory()
    validator = TargetValidator(socket_factory=factory)

    result = validator.validate(OperationTarget("target-1", "service.test:70000", "network"))

    assert result.status == "fail"
    assert "invalid port" in result.reason
    assert factory.calls == []


def test_url_without_explicit_port_does_not_guess_tcp_service():
    resolver = Mock(return_value=address_info())
    factory = SocketFactory()
    validator = TargetValidator(resolver=resolver, socket_factory=factory)

    result = validator.validate(OperationTarget("target-1", "https://service.test/login", "network"))

    assert result.status == "pass"
    assert result.checks == ("resolve",)
    assert factory.calls == []


def test_filesystem_path_must_exist_and_be_readable(tmp_path):
    readable = tmp_path / "target.txt"
    readable.write_text("target", encoding="utf-8")

    passing = TargetValidator().validate(OperationTarget("target-1", str(readable), "filesystem"))
    missing = TargetValidator().validate(OperationTarget("target-2", str(tmp_path / "missing"), "filesystem"))
    unreadable = TargetValidator(access_checker=lambda _path, mode: mode != os.R_OK).validate(
        OperationTarget("target-3", str(readable), "filesystem")
    )

    assert passing.status == "pass"
    assert missing.status == "fail"
    assert "does not exist" in missing.reason
    assert unreadable.status == "fail"
    assert "not readable" in unreadable.reason


def test_unsupported_target_type_is_skipped_without_probes():
    resolver = Mock()
    factory = SocketFactory()
    target = SimpleNamespace(target_id="target-1", value="opaque:value", type="opaque")

    result = TargetValidator(resolver=resolver, socket_factory=factory).validate(target)

    assert result.status == "skip"
    assert result.checks == ()
    assert "cannot be checked" in result.reason
    resolver.assert_not_called()
    assert factory.calls == []


def test_validation_collects_all_results_and_event_has_canonical_status(tmp_path):
    existing = tmp_path / "target"
    existing.mkdir()
    targets = [
        OperationTarget("target-1", str(existing), "filesystem"),
        OperationTarget("target-2", str(tmp_path / "missing"), "filesystem"),
    ]

    results = validate_operation_targets(targets)
    events = [result.to_event("OP_TEST") for result in results]

    assert [result.status for result in results] == ["pass", "fail"]
    assert [event["status"] for event in events] == ["pass", "fail"]
    assert all(event["type"] == "preflight_check" for event in events)
    assert all(event["operation_id"] == "OP_TEST" for event in events)


def test_run_target_preflight_emits_and_logs_each_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    emitter = Mock()
    logger = SimpleNamespace(info=Mock(), error=Mock())

    targets, results = cyberautoagent.run_target_preflight(
        logical_target=str(target),
        objective="Review the authorized files",
        operation_id="OP_TEST",
        logger=logger,
        emitter=emitter,
    )

    assert len(targets) == 1
    assert [result.status for result in results] == ["pass"]
    emitted = emitter.emit.call_args.args[0]
    assert emitted["type"] == "preflight_check"
    assert emitted["status"] == "pass"
    assert emitted["target"] == str(target)
    logger.info.assert_called_once()
    logger.error.assert_not_called()
