"""Boilerplate for a generic service."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import os
import signal
import sys
import typing
from typing import TYPE_CHECKING

import pydantic
import setproctitle
import yaml

from lib.boilerplate.config import Config
from lib.boilerplate.exceptions import ProcessingError
from lib.boilerplate.logging import LogColor, random_trace_id, set_service_details, set_trace_id
from lib.boilerplate.metrics import Metrics

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import FrameType, TracebackType
    from typing import Any, Literal, Self

    from lib.boilerplate.resource import ManagedResource


MAX_REPR_LENGTH = 256  # [characters] Truncation threshold for logged requests and responses
WAKEUP_FREQUENCY = 10  # How many times per second to check for graceful shutdown while sleeping


def get_service_and_config(service_name: str) -> tuple[type[Service], type[ServiceConfig]]:
    """Get a concrete configuration class for a service with given name.

    Resolves the config class by walking ``__orig_bases__`` for the parameterised
    generic ``Service[ConcreteConfig]`` (or ``MCPService[ConcreteConfig]``).
    This avoids forcing each concrete service to redeclare ``__init__`` — without
    a redeclaration, ``typing.get_type_hints(cls.__init__)`` would fail to resolve
    the unbound TypeVar ``ConcreteConfig``.
    """
    service_module = importlib.import_module(f"svc.{service_name}")
    service_class = service_module.SERVICE_CLASS

    for base in getattr(service_class, "__orig_bases__", ()):
        for arg in typing.get_args(base):
            if isinstance(arg, type) and issubclass(arg, ServiceConfig):
                return service_class, arg

    msg = (
        f"Cannot resolve config class for {service_class!r}. "
        f"The service must inherit from a parameterised base, "
        f"e.g. `class MyService(MCPService[MyConfig])`."
    )
    raise TypeError(msg)


def create_service(name: str, **config_overrides: Any) -> Service:  # noqa: ANN401
    """Dynamically configure and create a service with given name."""
    service_class, config_class = get_service_and_config(name)
    config = config_class(**config_overrides)

    return service_class(config)


@contextlib.asynccontextmanager
async def running_service(
    name: str,
    *,
    ignore_defaults: bool = False,
    **config_overrides: str,
) -> AsyncGenerator[Service[Any], Any]:
    """Dynamically create, configure and start a service with given name.

    Optionally set the configuration options via kwargs.
    """
    config_options = dict(config_overrides)  # Make a copy

    if ignore_defaults:
        config_options["_ignore_defaults"] = "1"  # The value doesn't matter

    async with create_service(name, **config_options) as service:
        yield service


def get_k8s_manifest(service: Service, **kwargs) -> str:
    """Get Kubernetes manifest for Kubernetes resources used by given service."""
    manifests = "---\n".join(
        yaml.dump(resource, Dumper=NoAliasDumper, sort_keys=False)
        for resource in service.get_k8s_resources(**kwargs)
    )

    return f"---\n# Service {service.NAME!r}\n\n{manifests}"


class NoAliasDumper(yaml.SafeDumper):
    """YAML dumper that doesn't use YAML aliases for repeated contents."""

    def ignore_aliases(self, data: Any) -> Literal[True]:  # noqa: ANN401, ARG002
        """Ignore the YAML aliases regardless of the data type."""
        # `SafeRepresenter` used by `SafeDumper` ignores only None, tuple, str, bytes, bool, int, float
        return True


class ServiceConfig(Config):
    """Common configuration options shared by all services."""

    colorize_logs: bool = True
    collect_metrics: bool = True  # Disable when Prometheus is not scraping (to avoid OOM)
    git_commit: str = pydantic.Field(default=os.getenv("GIT_COMMIT", "local"))


class Service[ConcreteConfig: ServiceConfig]:
    """Service with managed lifecycle."""

    # Docker naming convention/DNS names (lowercase letters and dashes)
    NAME: typing.ClassVar[str]
    TEAM: typing.ClassVar[str]

    DOCKER_IMAGE_REPOSITORY: typing.ClassVar[str] = "borndigitalaibot"
    PULL_SECRET: typing.ClassVar[str] = "regcred"  # noqa: RUF100, S105
    PROBE_PERIOD: typing.ClassVar[int] = 10  # Period of Kubernetes probes [s]

    CPU_REQUEST: typing.ClassVar[str]
    MEMORY_REQUEST: typing.ClassVar[str]
    CPU_LIMIT: typing.ClassVar[str]
    MEMORY_LIMIT: typing.ClassVar[str]

    ProcessingError = ProcessingError  # For convenient access from domain-specific services

    def __init__(self, config: ConcreteConfig) -> None:
        self.config = config

        self.logger = logging.getLogger()
        self.color = LogColor(self.config.colorize_logs)

        self.managed_resources: list[ManagedResource] = []
        self.shutdown_requested = False
        self._ready = False

        self.metrics = Metrics(self.config.collect_metrics)
        self.register_metrics()

        self.register_resources()

    def __repr__(self) -> str:
        return repr(self.__class__.__name__)

    async def __aenter__(self) -> Self:
        self._log_starting()
        await self.start_resources()

        self._log_initializing()
        await self.initialize_resources()

        # Liveness/readiness probes must run in a thread started from a resource
        await self.before_ready()
        self._log_initialized()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:  # For tests using pytest-asyncio
        self._log_stopping(exc_type)
        await self.stop_resources()

        self._log_stopped()

        return True  # Do not re-raise the exception

    def _log_starting(self) -> None:
        self.logger.info(
            f"{self.color.white}*** Service {self!r}, version {self.config.git_commit!r} is starting ***{self.color.default}"
        )
        self.logger.info(f"Config: {self.config.model_dump()}")
        self.logger.info(f"Metrics: {self.metrics!r}")

        set_trace_id(random_trace_id())
        # Use the K8s/deployment-friendly NAME (e.g. "mcp-telekom-identity") for the
        # `application` field in log records — that's what operators query in Kibana.
        set_service_details(self.NAME, self.config.git_commit)
        self.logger.info(
            f"{self.color.green}Service {self!r} is starting the resources{self.color.default}"
        )

    def _log_initializing(self) -> None:
        self.logger.info(
            f"{self.color.green}Service {self!r} started all resources and is initializing{self.color.default}"
        )

    def _log_initialized(self) -> None:
        self.logger.info(
            f"{self.color.green}Service {self!r} is initialized and can serve traffic{self.color.default}"
        )
        set_trace_id("")
        self._ready = True

    def _log_stopping(self, exc_type: type[BaseException] | None) -> None:
        if exc_type:
            self.logger.exception(
                f"{self.color.red}Service {self!r} raised unhandled exception:{self.color.default}"
            )

        set_trace_id(random_trace_id())
        self.logger.info(
            f"{self.color.green}Service {self!r} is stopping the resources{self.color.default}"
        )

    def _log_stopped(self) -> None:
        self.logger.info(
            f"{self.color.green}Service {self!r} stopped all resources{self.color.default}"
        )
        set_trace_id("")

        self.logger.info(
            f"{self.color.white}*** Service {self!r} gracefully shut down ***{self.color.default}"
        )

    def is_ready(self) -> bool:
        """Indicate if the service is ready for serving the incoming requests."""
        return self._ready

    def set_process_title(self) -> None:
        """Show the name of the current service in the process list."""
        setproctitle.setproctitle(f"Service {self!r}, version {self.config.git_commit!r}")

    async def before_ready(self) -> None:  # To be overridden in child classes
        """Perform long initialization before handling the first incoming request."""

    def register_metrics(self) -> None:  # To be overridden in child classes
        """Register Prometheus metrics provided by the service."""

    def register_resources(self) -> None:  # To be overridden in child classes
        """Register all resources, so the service can manage their lifecycle."""

    def register_resource[ConcreteResource: ManagedResource](
        self,
        resource_class: type[ConcreteResource],
    ) -> ConcreteResource:
        """Create instance of given resource and start managing its lifecycle."""
        resource_config_class = typing.get_type_hints(
            resource_class.__init__, localns={"Metrics": Metrics}
        )["config"]
        resource_config = resource_config_class.from_generic_config(self.config)
        resource = resource_class(resource_config, self.metrics)

        self.managed_resources.append(resource)

        return resource

    def replace_resource[ConcreteResource: ManagedResource](
        self, original_resource_class: type[ManagedResource], resource_class: type[ConcreteResource]
    ) -> ConcreteResource:
        """Replace the original managed resource with instance of given resource class."""
        for index, resource in enumerate(self.managed_resources):
            if isinstance(resource, original_resource_class):
                del self.managed_resources[index]

        return self.register_resource(resource_class)

    async def start_resources(self) -> None:
        """Start all resources managed by the service."""
        for resource in self.managed_resources:
            await resource.start()
            self.logger.info(f"Resource {resource!r} started: {resource.config!r}")

    async def initialize_resources(self) -> None:
        """Initialize all resources managed by the service."""
        for resource in self.managed_resources:
            await resource.before_ready()
            self.logger.info(f"Resource {resource!r} initialized")

    async def stop_resources(self) -> None:
        """Gracefully stop all resources managed by the service."""
        for resource in self.managed_resources:
            await resource.stop()
            self.logger.info(f"Resource {resource!r} stopped")

    async def run_forever(self) -> None:  # To be overridden in child classes
        """Run the service until graceful shutdown."""
        while not self.shutdown_requested:  # noqa: ASYNC110
            await asyncio.sleep(0.1)

    def request_shutdown(
        self,
        signal_number: int | None = None,  # noqa: ARG002
        current_stack_frame: FrameType | None = None,  # noqa: ARG002
    ) -> None:
        """Signal handler that requests a graceful shutdown of the service."""
        # Use low-level API to avoid a race condition between signal handler running in a background thread
        # implemented in C and regular Python code, both trying to call a non-reentrant method `logging.Logger.emit()`
        os.write(
            sys.stdout.fileno(),
            f"{self.color.yellow}Graceful shutdown of service {self!r} requested{self.color.default}".encode(),
        )
        self.shutdown_requested = True

    async def sleep(self, duration: float, *, log: bool = True) -> None:
        """Sleep for given duration [s] without slowing down the graceful shutdown."""
        if not duration:
            return

        if log:
            self.logger.info(f"Sleeping for {duration}s")

        if duration <= 1 / WAKEUP_FREQUENCY:
            await asyncio.sleep(duration)
            return

        for _ in range(int(duration) * WAKEUP_FREQUENCY):
            if self.shutdown_requested:
                break

            await asyncio.sleep(1 / WAKEUP_FREQUENCY)

    def register_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown of the service."""
        signal.signal(signal.SIGINT, self.request_shutdown)  # Ctrl+C
        signal.signal(signal.SIGTERM, self.request_shutdown)  # Docker/Kubernetes

    def get_k8s_resources(self, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ARG002, ANN401
        """Get the definition of Kubernetes resources used by given service."""
        return []
