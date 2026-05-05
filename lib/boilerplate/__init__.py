"""Unified boilerplate for micro-services."""

from __future__ import annotations

from lib.boilerplate.config import ENV_PREFIX, Config, env_variable_for_option, get_default_config
from lib.boilerplate.exceptions import (
    ApplicationError,
    ConfigError,
    InfrastructureError,
    NotFoundError,
    ProcessingError,
)
from lib.boilerplate.logging import (
    INTERACTION_ID_HEADER,
    TRACE_HEADER,
    TRACE_ID_LENGTH,
    LogColor,
    configure_logger,
    configure_logging,
    current_interaction_id,
    current_trace_id,
    disable_logger,
    random_trace_id,
    set_interaction_id,
    set_trace_id,
)
from lib.boilerplate.metrics import Metrics
from lib.boilerplate.resource import (
    ManagedResource,
    ResourceConfig,
    scan_port,
)
from lib.boilerplate.service import (
    MAX_REPR_LENGTH,
    Service,
    ServiceConfig,
    create_service,
    get_k8s_manifest,
    get_service_and_config,
    running_service,
)

__all__ = [
    "ENV_PREFIX",
    "INTERACTION_ID_HEADER",
    "MAX_REPR_LENGTH",
    "TRACE_HEADER",
    "TRACE_ID_LENGTH",
    "ApplicationError",
    "Config",
    "ConfigError",
    "InfrastructureError",
    "LogColor",
    "ManagedResource",
    "Metrics",
    "NotFoundError",
    "ProcessingError",
    "ResourceConfig",
    "Service",
    "ServiceConfig",
    "configure_logger",
    "configure_logging",
    "create_service",
    "current_interaction_id",
    "current_trace_id",
    "disable_logger",
    "env_variable_for_option",
    "get_default_config",
    "get_k8s_manifest",
    "get_service_and_config",
    "random_trace_id",
    "running_service",
    "scan_port",
    "set_interaction_id",
    "set_trace_id",
]
