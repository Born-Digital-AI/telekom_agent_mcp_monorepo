#!/usr/bin/env python

"""Print a GitHub Actions JSON matrix for running a CI job for all services in the monorepo.

The matrix contains Docker build arguments for all services/their base images.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.monorepo import get_base_image, get_service_names, get_shared_folder  # noqa: E402

DEFAULT_SERVICE_IMAGE = "service"

# See https://docs.github.com/en/actions/learn-github-actions/expressions#fromjson
# and https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs


def get_service_matrix(service_names: set[str]) -> dict[str, list[dict[str, str]]]:
    """Format the Docker build arguments for all services into GitHub Actions matrix format."""
    return {
        "include": [
            {
                "service_name": service_name,
                "base_image": get_base_image(service_name),  # For custom binary dependencies
                "shared_folder": get_shared_folder(service_name),
                # Python identifiers use underscores (to disambiguate from minus operator)
                # but Docker/Kubernetes uses dashes (to be valid DNS names)
                "repository_name": service_name.replace("_", "-"),
            }
            for service_name in service_names
        ]
    }


def get_base_image_matrix(service_names: set[str]) -> dict[str, list[dict[str, str]]]:
    """Format the Docker build arguments for all base Docker images used by the services into GitHub Actions matrix format."""
    base_images = {get_base_image(service_name) for service_name in service_names}

    return {"include": [{"base_image": base_image} for base_image in base_images]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create GitHub Actions JSON matrix for all services/base images in the monorepo"
    )
    parser.add_argument(
        "--base-images",
        help="Generate the matrix only for base images",
        action="store_true",
        dest="base_images",
    )
    args: argparse.Namespace = parser.parse_args()

    service_names = get_service_names()
    matrix = (
        get_base_image_matrix(service_names)
        if args.base_images
        else get_service_matrix(service_names)
    )

    print(json.dumps(matrix))
