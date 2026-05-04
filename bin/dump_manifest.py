#!/usr/bin/env python

"""Dump the Kubernetes YAML manifest for given service (e.g. for deploying it via `kubectl apply -f`)."""

from __future__ import annotations

import argparse
import pathlib
import sys

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.boilerplate import (  # noqa: E402
    create_service,
    get_k8s_manifest,
)
from lib.monorepo import get_service_names  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dump the Kubernetes YAML manifest for a service with given name",
    )

    parser.add_argument(
        "service",
        help="Name of the service",
        choices=get_service_names(),
    )

    parser.add_argument(
        "namespace",
        help="Kubernetes namespace",
        nargs="?",
        default="development",
    )

    parser.add_argument(
        "-o",
        help="Configurable option for the Kubernetes manifest in format 'key=value', can be provided multiple times",
        action="append",
        type=lambda key_eq_value: key_eq_value.split("=", 1),  # Value can contain the equal sign
        dest="key_eq_value",
    )

    args: argparse.Namespace = parser.parse_args()
    options = dict(args.key_eq_value or [])
    manifest = get_k8s_manifest(
        create_service(args.service, **options), namespace=args.namespace, **options
    )

    print(manifest)
