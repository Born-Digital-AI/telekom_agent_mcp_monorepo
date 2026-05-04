"""Definition of a generic managed resource."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Self

from lib.boilerplate.config import Config
from lib.boilerplate.exceptions import InfrastructureError
from lib.boilerplate.metrics import Metrics

if TYPE_CHECKING:
    from types import TracebackType


def scan_port(host: str, port: int) -> None:
    """Check if given host listens on given port."""
    scanned_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    scanned_socket.settimeout(1)

    try:
        scanned_socket.connect((host, port))  # CONNECT scan
        scanned_socket.shutdown(socket.SHUT_RDWR)  # Send FIN/EOF
    except (socket.gaierror, ConnectionRefusedError):
        message = f"Remote host {host!r} is not listening at port {port}"

        raise InfrastructureError(message) from None
    finally:
        scanned_socket.close()


class ResourceConfig(Config):
    """Common configuration options shared by all managed resources."""

    colorize_logs: bool = True


class ManagedResource[ConcreteConfig: ResourceConfig]:
    """Asyncio-compatible resource with a lifecycle managed by a service."""

    def __init__(self, config: ConcreteConfig, metrics: Metrics | None = None) -> None:
        self.config = config

        # Injected from the service or dummy for tests
        self.metrics = metrics or Metrics(store_observations=False)

    def __repr__(self) -> str:
        return repr(self.__class__.__name__)

    async def __aenter__(
        self,
    ) -> Self:  # Used in tests and database migrations (but not by the services)
        """Provide async context which starts and stops the resource (before_ready has to be called manually)."""
        await self.start()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the resource."""
        await self.stop()

    async def start(self) -> None:  # To be overridden in child classes
        """Initialize the resource and make a connectivity check."""

    async def stop(self) -> None:  # May be overridden in child classes
        """Gracefully disconnect the resource."""

    async def before_ready(self) -> None:  # May be overridden in child classes
        """Perform slow initialization of the resource."""
