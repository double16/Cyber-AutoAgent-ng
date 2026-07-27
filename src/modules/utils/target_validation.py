"""Deterministic preflight validation for executable operation targets."""

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, Optional, Sequence
from urllib.parse import urlsplit

from modules.tools.memory import OperationTarget


PreflightStatus = Literal["pass", "fail", "skip"]


@dataclass(frozen=True)
class TargetValidationResult:
    """The complete preflight result emitted for one executable target."""

    target_id: str
    target: str
    target_type: str
    status: PreflightStatus
    checks: tuple[str, ...]
    reason: str = ""
    resolved_addresses: tuple[str, ...] = ()

    def to_event(self, operation_id: str) -> dict[str, Any]:
        """Build the stable event wire shape for frontends and logs."""

        return {
            "type": "preflight_check",
            "operation_id": operation_id,
            "target_id": self.target_id,
            "target": self.target,
            "target_type": self.target_type,
            "status": self.status,
            "checks": list(self.checks),
            "reason": self.reason,
            "resolved_addresses": list(self.resolved_addresses),
        }


@dataclass(frozen=True)
class _NetworkEndpoint:
    host: str
    port: Optional[int]


class TargetValidator:
    """Validate targets using the host operating system's resolver and routing."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 3.0,
        resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        access_checker: Callable[[str, int], bool] = os.access,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.resolver = resolver
        self.socket_factory = socket_factory
        self.access_checker = access_checker

    def validate(self, target: Any) -> TargetValidationResult:
        """Validate one target, returning a result instead of raising probe errors."""

        target_id = str(getattr(target, "target_id", ""))
        value = str(getattr(target, "value", ""))
        target_type = str(getattr(target, "type", ""))
        if target_type == "filesystem":
            return self._validate_filesystem(target_id, value, target_type)
        if target_type == "network_range":
            return self._validate_network_range(target_id, value, target_type)
        if target_type == "network":
            return self._validate_network(target_id, value, target_type)
        return TargetValidationResult(
            target_id=target_id,
            target=value,
            target_type=target_type,
            status="skip",
            checks=(),
            reason=f"target type {target_type or '<empty>'!r} cannot be checked",
        )

    def _validate_filesystem(
        self,
        target_id: str,
        value: str,
        target_type: str,
    ) -> TargetValidationResult:
        checks = ("filesystem_exists", "filesystem_readable")
        if not os.path.exists(value):
            return TargetValidationResult(
                target_id, value, target_type, "fail", checks, "filesystem path does not exist"
            )
        if not self.access_checker(value, os.R_OK):
            return TargetValidationResult(
                target_id, value, target_type, "fail", checks, "filesystem path is not readable"
            )
        return TargetValidationResult(target_id, value, target_type, "pass", checks)

    def _validate_network_range(
        self,
        target_id: str,
        value: str,
        target_type: str,
    ) -> TargetValidationResult:
        checks = ("route",)
        try:
            network = ipaddress.ip_network(value, strict=False)
            destination = network.network_address if network.num_addresses == 1 else next(network.hosts())
            self._check_route(destination)
        except (OSError, ValueError, StopIteration) as error:
            return TargetValidationResult(
                target_id, value, target_type, "fail", checks, f"network is not routable: {error}"
            )
        return TargetValidationResult(target_id, value, target_type, "pass", checks)

    def _validate_network(
        self,
        target_id: str,
        value: str,
        target_type: str,
    ) -> TargetValidationResult:
        checks: list[str] = []
        resolved_addresses: tuple[str, ...] = ()
        try:
            endpoint = self._parse_endpoint(value)
            numeric_address = self._numeric_address(endpoint.host)
            address_info: Sequence[tuple[Any, ...]] = ()
            if numeric_address is None:
                checks.append("resolve")
                address_info = self.resolver(
                    endpoint.host,
                    endpoint.port,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                )
                resolved_addresses = self._resolved_addresses(address_info)
                if not resolved_addresses:
                    raise OSError("system resolver returned no IP addresses")
            else:
                checks.append("route")
                resolved_addresses = (str(numeric_address),)
                self._check_route(numeric_address)

            if endpoint.port is not None:
                checks.append("tcp_connect")
                if not address_info:
                    address_info = self.resolver(
                        endpoint.host,
                        endpoint.port,
                        socket.AF_UNSPEC,
                        socket.SOCK_STREAM,
                    )
                self._check_tcp(address_info)
        except (OSError, ValueError) as error:
            return TargetValidationResult(
                target_id,
                value,
                target_type,
                "fail",
                tuple(checks),
                str(error),
                resolved_addresses,
            )
        return TargetValidationResult(
            target_id,
            value,
            target_type,
            "pass",
            tuple(checks),
            resolved_addresses=resolved_addresses,
        )

    @staticmethod
    def _parse_endpoint(value: str) -> _NetworkEndpoint:
        text = value.strip()
        if not text:
            raise ValueError("network target is empty")
        try:
            ipaddress.ip_address(text)
            return _NetworkEndpoint(host=text, port=None)
        except ValueError:
            pass
        parsed = urlsplit(text if "://" in text else f"//{text}")
        host = parsed.hostname
        if not host:
            raise ValueError("network target does not contain a host")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError(f"network target has an invalid port: {error}") from error
        return _NetworkEndpoint(host=host, port=port)

    @staticmethod
    def _numeric_address(host: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            return None

    @staticmethod
    def _resolved_addresses(address_info: Sequence[tuple[Any, ...]]) -> tuple[str, ...]:
        addresses = []
        for info in address_info:
            sockaddr = info[4]
            address = str(sockaddr[0])
            if address not in addresses:
                addresses.append(address)
        return tuple(addresses)

    def _check_route(self, destination: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        family = socket.AF_INET6 if destination.version == 6 else socket.AF_INET
        probe = self.socket_factory(family, socket.SOCK_DGRAM)
        try:
            probe.settimeout(self.timeout_seconds)
            sockaddr: tuple[Any, ...]
            if family == socket.AF_INET6:
                sockaddr = (str(destination), 9, 0, 0)
            else:
                sockaddr = (str(destination), 9)
            probe.connect(sockaddr)
        finally:
            probe.close()

    def _check_tcp(self, address_info: Sequence[tuple[Any, ...]]) -> None:
        failures: list[str] = []
        for family, socktype, protocol, _, sockaddr in address_info:
            connection = self.socket_factory(family, socktype, protocol)
            try:
                connection.settimeout(self.timeout_seconds)
                connection.connect(sockaddr)
                return
            except OSError as error:
                failures.append(str(error))
            finally:
                connection.close()
        detail = failures[-1] if failures else "system resolver returned no TCP addresses"
        raise OSError(f"TCP connection failed: {detail}")


def validate_operation_targets(
    targets: Iterable[OperationTarget],
    *,
    validator: Optional[TargetValidator] = None,
) -> list[TargetValidationResult]:
    """Validate every resolved target without stopping at the first failure."""

    active_validator = validator or TargetValidator()
    return [active_validator.validate(target) for target in targets]
