"""Central place for configuring the logging subsystem."""

from __future__ import annotations

import contextvars
import logging
import socket  # For specific network exceptions
import sqlite3  # For specific database exceptions
import sys
import time
import uuid
from typing import Any

from logstash_async.constants import constants as logstash_constants  # For macOS patch
from logstash_async.handler import AsynchronousLogstashHandler, LogstashFormatter
from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore[reportPrivateImportUsage]

from lib.boilerplate.config import Config

current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
current_service_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "service_name", default=""
)
current_service_version: contextvars.ContextVar[str] = contextvars.ContextVar(
    "service_version", default=""
)
current_conversation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "conversation_id", default=""
)
current_interaction_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "interaction_id", default=""
)


class DefaultServiceDetails:
    """Process-level fallback for service metadata when a task loses logging context."""

    def __init__(self) -> None:
        self.name = ""
        self.version = ""


default_service_details = DefaultServiceDetails()
TRACE_ID_LENGTH = 8
TRACE_HEADER = "X-Trace-Id"
CONVERSATION_ID_HEADER = "X-Conversation-Id"
INTERACTION_ID_HEADER = "X-Interaction-Id"


def random_trace_id() -> str:
    """Generate a random trace id."""
    return uuid.uuid4().hex[:TRACE_ID_LENGTH]


def set_trace_id(trace_id: str) -> None:
    """Set the current trace id to given value."""
    current_trace_id.set(trace_id)


def set_service_details(service_name: str = "", service_version: str = "") -> None:
    """Set the current service name and service version."""
    normalized_service_name = service_name.strip("\"'")
    normalized_service_version = service_version.strip("\"'")

    default_service_details.name = normalized_service_name
    default_service_details.version = normalized_service_version

    current_service_name.set(normalized_service_name)
    current_service_version.set(normalized_service_version)


def set_conversation_id(conversation_id: str = "") -> None:
    """Set the current conversation id."""
    current_conversation_id.set(conversation_id)


def set_interaction_id(interaction_id: str = "") -> None:
    """Set the current interaction id."""
    current_interaction_id.set(interaction_id)


class LoggingConfig(Config):
    """Common configuration options shared by all managed resources."""

    json_format_logs: bool = False


class UTCFormatter(logging.Formatter):
    """Use UTC timezone for logged timestamps."""

    converter = time.gmtime


class TraceIdInjector(logging.Filter):
    """Inject the trace id of current request into log records emitted during the request handling."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Inject the trace id of current request."""
        trace_id = current_trace_id.get("")

        record.trace_id = trace_id

        return True  # Emit the logrecord


class ServiceInjector(logging.Filter):
    """Inject the service details of current request into log records emitted during the request handling."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Inject the trace id of current request."""
        service_name = current_service_name.get("") or default_service_details.name
        service_version = current_service_version.get("") or default_service_details.version

        record.service_name = service_name
        record.service_version = service_version
        record.application = service_name
        record.app_version = service_version
        return True  # Emit the logrecord


class ConversationInjector(logging.Filter):
    """Inject conversation and interaction IDs into log records emitted during request handling."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Inject conversation and interaction IDs into the log record."""
        conversation_id = current_conversation_id.get("")
        record.conversation_id = conversation_id
        record.ConversationId = conversation_id

        interaction_id = current_interaction_id.get("")
        record.interaction_id = interaction_id
        record.InteractionId = interaction_id
        return True  # Emit the logrecord


class LogColor:
    """Utilities for conditional highlighting of log records using ANSI colors."""

    def __init__(self, colorize: bool = True) -> None:  # noqa: FBT001, FBT002
        self.colorize = colorize

    @property
    def default(self) -> str:
        """Default ANSI terminal color."""
        return "\x1b[0m" if self.colorize else ""

    @property
    def grey(self) -> str:
        """Grey ANSI terminal color."""
        return "\x1b[90;02m" if self.colorize else ""

    @property
    def white(self) -> str:  # Only for the first and the last logrecord of a service
        """White ANSI terminal color."""
        return "\x1b[37;01m" if self.colorize else ""

    @property
    def green(self) -> str:  # Only for start and stop of managed resources
        """Green ANSI terminal color."""
        return "\x1b[32;01m" if self.colorize else ""

    @property
    def yellow(self) -> str:  # Only for warnings and trigger of graceful shutdown
        """Yellow ANSI terminal color."""
        return "\x1b[33;01m" if self.colorize else ""

    @property
    def red(self) -> str:  # Only for errors
        """Red ANSI terminal color."""
        return "\x1b[31;01m" if self.colorize else ""


class CustomJsonFormatter(JsonFormatter):
    """Custom JSON formatter that supports structured logs with trace IDs, prefixed/suffixed messages, and enriched metadata like filename and line number."""

    def __init__(self, *args: Any, prefix: str = "", suffix: str = "", **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(*args, **kwargs)
        self.prefix = prefix
        self.suffix = suffix

    def add_fields(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, log_data: dict[str, Any], record: logging.LogRecord, message_dict: dict[str, Any]
    ) -> None:
        """Enrich the log record with custom fields for structured logging.

        Adds trace ID, aligned log level, filename, line number, function name,
        and appends prefix/suffix to the message. Also formats the timestamp.
        """
        super().add_fields(log_data, record, message_dict)

        log_data["@timestamp"] = self.formatTime(record, self.datefmt) + "Z"
        log_data["message"] = f"{self.prefix}{record.getMessage()}{self.suffix}"
        log_data["log"] = {
            "level": record.levelname.lower(),
            "origin": {
                "file": {
                    "name": record.filename,
                    "line": record.lineno,
                },
                "function": record.funcName,
            },
            "logger": record.name,
        }

        log_data["trace"] = {"id": getattr(record, "trace_id", "-").strip()}
        log_data["process"] = {
            "pid": record.process,
        }
        log_data["service"] = {
            "name": getattr(record, "service_name", "").strip(),
            "version": getattr(record, "service_version", "").strip(),
        }
        # don't include trace_id, service_name, service_version at top-level
        log_data.pop("trace_id", None)
        log_data.pop("service_name", None)
        log_data.pop("service_version", None)


class AppLogstashFormatter(LogstashFormatter):
    """Custom Logstash formatter that injects a custom field."""

    def format(self, record: logging.LogRecord) -> str:
        """Inject the application name and version into the log record."""
        _service_name = getattr(record, "service_name", "") or default_service_details.name
        _service_version = getattr(record, "service_version", "") or default_service_details.version
        record.application = _service_name
        record.app_version = _service_version
        return super().format(record)


class LogstashConfig(Config):
    """Configuration specific to Logstash logging, used within the logging module."""

    logstash_enabled: bool = False
    logstash_host: str | None = None
    logstash_port: int | None = None
    logstash_database_path: str | None = (
        "logstash_cache.db"  # Path for SQLite cache, None to disable
    )


def configure_logger(logger_name: str | None = None, prefix: str = "", suffix: str = "") -> None:
    """Configure given logger."""
    logging_configs = LoggingConfig()
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    if logging_configs.json_format_logs:
        log_formatter = CustomJsonFormatter(
            prefix=prefix,
            suffix=suffix,
        )
    else:
        log_format = f"%(asctime)sZ [%(levelname)-7s] [%(trace_id)-{TRACE_ID_LENGTH}s] {prefix}%(message)s{suffix}"
        # ISO 8601 (Zulu time == UTC, seconds as int)
        log_formatter = UTCFormatter(
            fmt=log_format, datefmt="%Y-%m-%dT%H:%M:%S", defaults={"trace_id": ""}
        )

    handler.setFormatter(log_formatter)
    logger.handlers = [handler]
    logger.addFilter(TraceIdInjector())
    logger.addFilter(ServiceInjector())
    logger.addFilter(ConversationInjector())

    cfg = LogstashConfig()
    logstash_enabled = cfg.logstash_enabled
    logstash_host = cfg.logstash_host
    logstash_port = cfg.logstash_port

    if sys.platform == "darwin":
        # On macOS, the fcntl.ioctl call used by logstash-async to check
        # the socket buffer (termios.TIOCOUTQ) can cause OSError: [Errno 6] Device not configured.
        # Setting SOCKET_CLOSE_WAIT_TIMEOUT to 0 bypasses the problematic check.
        # See: https://github.com/eht16/python-logstash-async/issues/105 (or similar discussions)
        if logstash_constants.SOCKET_CLOSE_WAIT_TIMEOUT > 0:
            logging.getLogger("boilerplate.logging").info(
                f"macOS detected. Applying Logstash patch: Setting SOCKET_CLOSE_WAIT_TIMEOUT to 0 (was {logstash_constants.SOCKET_CLOSE_WAIT_TIMEOUT})."
            )
            logstash_constants.SOCKET_CLOSE_WAIT_TIMEOUT = 0

    if logstash_enabled and logstash_host and logstash_port:
        try:
            logstash_handler = AsynchronousLogstashHandler(
                host=logstash_host,
                port=logstash_port,
                database_path=cfg.logstash_database_path,
            )
            logstash_handler.setFormatter(AppLogstashFormatter(extra_prefix=""))
            logger.addHandler(logstash_handler)
            logging.getLogger("boilerplate.logging").info(
                f"Logstash logging configured for '{logger_name or 'root'}' logger, target: {logstash_host}:{logstash_port}"
            )
        except ValueError as e_val:
            # Pydantic handles int conversion for logstash_port. This ValueError is for other init issues.
            logging.getLogger("boilerplate.logging").error(
                f"ValueError during Logstash handler initialization for {logstash_host}:{logstash_port}. Error: {e_val}. Logstash handler NOT configured."
            )
        except (OSError, socket.gaierror, sqlite3.Error) as e_io_db:
            logging.getLogger("boilerplate.logging").error(
                f"Failed to initialize Logstash handler due to OS/network/DB error for '{logger_name or 'root'}': {e_io_db}. Logstash handler NOT configured."
            )
        except Exception as e_other:  # Catch any other unexpected exceptions
            logging.getLogger("boilerplate.logging").error(
                f"An UNEXPECTED error occurred while initializing Logstash handler for '{logger_name or 'root'}': {e_other}. Logstash handler NOT configured."
            )
            raise
    elif logstash_enabled and (not logstash_host or logstash_port is None):
        logging.getLogger("boilerplate.logging").warning(
            f"APP_LOGSTASH_ENABLED is true, but host ('{cfg.logstash_host}') or port ('{cfg.logstash_port}') is not correctly configured. Logstash handler NOT configured."
        )

    logger.propagate = False  # Used when configuring non-root logger (e.g. for Uvicorn)


def disable_logger(logger_name: str) -> None:
    """Disable given logger."""
    logger = logging.getLogger(logger_name)
    logger.handlers = []
    logger.propagate = False


def configure_logging() -> None:
    """Configure the logging subsystem -- run only once per interpreter."""
    configure_logger()  # Services use the root logger
