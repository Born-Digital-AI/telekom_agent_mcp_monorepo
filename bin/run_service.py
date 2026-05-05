#!/usr/bin/env python

"""Run a service with given name (via env variable in Docker or command-line argument otherwise).

This script must be run with working directory set to the monorepo root, so it can import from all
libraries/services (by appending the monorepo root to sys.path).

The service name passed from the Dockerfile must use an environment variable -- CMD with exec form
doesn't expand ARG or ENV variables. Shell form cannot be used to correctly propagate UNIX signals
to child Python processes and to correctly reap them when they become a zombie.

The service name passed as a command-line argument is used only when running the service locally.

Optionally the command-line can override values of selected configuration options.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

# This script is not located in the monorepo root but in the `bin` folder
# so the automatic addition to sys.path doesn't allow imports from `lib` and `svc`

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.boilerplate import (  # noqa: E402
    configure_logging,
    running_service,
)
from lib.monorepo import get_service_names  # noqa: E402


async def run_service(
    service_name: str,
    *,
    ignore_defaults: bool = False,
    **config_overrides: str,
) -> None:
    """Run a service with given name and configuration options."""
    configure_logging()

    async with running_service(
        service_name, ignore_defaults=ignore_defaults, **config_overrides
    ) as service:
        service.register_signal_handlers()
        service.set_process_title()
        await service.run_forever()  # Until Ctrl+C or SIGINT


if __name__ == "__main__":
    service_name = os.environ.get("SERVICE_NAME", "")  # When running in Docker

    parser = argparse.ArgumentParser(
        description="Run a service with given name -- or name provided via env variable SERVICE_NAME",
    )

    if not service_name:
        parser.add_argument(
            "service",
            help="Name of the service",
            choices=get_service_names(),
        )  # When running directly from code, without Docker

    parser.add_argument(
        "-o",
        help="Configuration option in format 'key=value', can be provided multiple times",
        action="append",
        type=lambda key_eq_value: key_eq_value.split("=", 1),  # Value can contain the equal sign
        dest="key_eq_value",
    )

    parser.add_argument(
        "--ignore-defaults",
        help="Force all configuration options to be explicitly set (regardless if a default value exists)",
        action="store_true",
        dest="ignore_defaults",
    )

    args: argparse.Namespace = parser.parse_args()
    config_overrides = {key.strip(): value.strip() for key, value in args.key_eq_value or []}
    service_name = service_name or args.service
    os.environ["SERVICE_NAME"] = service_name

    main_coroutine = run_service(
        service_name, ignore_defaults=args.ignore_defaults, **config_overrides
    )
    asyncio.run(main_coroutine)
