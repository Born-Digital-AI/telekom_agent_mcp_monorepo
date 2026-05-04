"""Boilerplate for a generic service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pydantic

from lib.boilerplate.exceptions import ConfigError

if TYPE_CHECKING:
    from typing import Any, Self

import pydantic_core
import pydantic_settings

ENV_PREFIX = "APP_"  # Environment variable names must start with this prefix


def env_variable_for_option(option_name: str) -> str:
    """Get the environment variable name for given configuration option."""
    return f"{ENV_PREFIX}{option_name.upper()}"  # Naming convention used by `pydantic_settings`


def get_default_config(config_class: type[Config]) -> dict[str, Any]:
    """Get the default configuration values for given configuration class."""
    default_config = {}

    for field_name, field in config_class.model_fields.items():
        if field.is_required():
            value = ""
        elif field.default_factory:
            value = field.default_factory()  # type: ignore[reportCallIssue] # Callable[[], Any]
        else:
            value = field.default

        default_config[env_variable_for_option(field_name)] = str(value)

    return default_config


class Config(pydantic_settings.BaseSettings):
    """Union of configuration options for a set of managed resources."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        try:
            # Take config options from kwargs and fill missing ones from env variables starting with ENV_PREFIX
            super().__init__(*args, **kwargs)
        # Pydantic error messages are not easily readable
        except pydantic_core.ValidationError as exception:
            errors_for_fields = [
                f"{'.'.join(str(option_name) for option_name in error['loc'])!r}: {error['msg']}"
                for error in exception.errors()
            ]

            errors_text = "\n".join(errors_for_fields)
            message = f"Configuration options for {exception.title!r} are invalid:\n{errors_text}"
            raise ConfigError(message) from None
        except pydantic_settings.SettingsError as exception:
            message = f"Configuration options for class {self.__class__.__name__!r} are invalid:\n{', '.join(exception.args)}"
            raise ConfigError(message) from None

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix=ENV_PREFIX, env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    def __repr__(self) -> str:
        return ", ".join(  # Define secrets with pydantic.Field(exclude=True) to avoid logging/serializing them
            f"{field_name}={'***' if field_info.exclude else repr(getattr(self, field_name))}"
            for field_name, field_info in type(self).model_fields.items()
        )

    @classmethod
    def from_generic_config(cls, config: Config) -> Self:
        """Create an instance of the config by copying its fields from a more generic config.

        Used for extracting a subset of config options used by a managed resource from a service-wide config.
        """
        kwargs = {field_name: getattr(config, field_name) for field_name in cls.model_fields}

        return cls(**kwargs)

    @pydantic.model_validator(mode="before")
    @classmethod
    def ignore_defaults(cls, data: Any) -> Any:  # noqa: ANN401
        """Ignore the default field values and enforce explicit setting of all fields."""
        if isinstance(data, dict) and data.get("_ignore_defaults"):
            data_without_defaults = dict.fromkeys(set(cls.model_fields) - set(data), None)
            data_without_defaults.update(data)
            data_without_defaults.pop("_ignore_defaults")

            return data_without_defaults

        return data
