"""Error reporting using Sentry."""

from __future__ import annotations

import logging
import os

import pydantic

from lib.boilerplate.config import Config


class ErrorReportingConfig(Config):
    """Configuration of the error reporting."""

    sentry_dsn: str = pydantic.Field(default="", exclude=True)  # Disabled by default
    sentry_environment: str = ""
    git_commit: str = pydantic.Field(default=os.getenv("GIT_COMMIT", "local"))

    @property
    def error_reporting_enabled(self) -> bool:
        """Indicate if the error reporting is configured/enabled."""
        return bool(self.sentry_dsn and self.sentry_environment and self.git_commit)


def configure_error_reporting(config: ErrorReportingConfig | None = None) -> None:
    """Configure the error reporting to Sentry."""
    config = config or ErrorReportingConfig()
    logger = logging.getLogger()

    if config.error_reporting_enabled:
        import sentry_sdk  # noqa: PLC0415

        sentry_sdk.init(
            dsn=config.sentry_dsn,
            environment=config.sentry_environment,
            release=config.git_commit,
            enable_tracing=False,  # Sentry APM is disabled
            auto_enabling_integrations=False,  # Disable automatic integration detection to avoid OpenAI integration issues
            # Exithook blocks max 2s when flushing pending events to Sentry
        )

        logger.info(
            f"Error reporting using Sentry enabled, environment: {config.sentry_environment!r}"
        )
