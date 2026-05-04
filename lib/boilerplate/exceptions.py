"""Common exceptions used by services and managed resources."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for application-related exceptions."""


class ConfigError(ApplicationError):
    """Configuration of a managed resource or service is invalid."""


class InfrastructureError(ApplicationError):
    """Mandatory remote infrastructure component is unreachable."""


class ProcessingError(ApplicationError):
    """Processing of a handler failed (e.g. dynamic validation against DB or refused remote call)."""


class NotFoundError(ApplicationError):
    """Requested entity not found (e.g. in the database)."""
