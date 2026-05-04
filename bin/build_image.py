#!/usr/bin/env python

"""Build and tag a Docker image for a service (or a base image for the services)."""

# The build is performed only locally, pushing to Docker Hub has to be done manually using:
# docker login -u $DOCKER_HUB_USERNAME -p $DOCKER_HUB_TOKEN
# docker push borndigitalaibot/<service name>:tag

# OS X on Arm CPU: Force the target platform to be different than physical HW:
# export DOCKER_DEFAULT_PLATFORM=linux/amd64

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.monorepo import (  # noqa: E402
    get_base_image,
    get_base_images,
    get_service_names,
    get_shared_folder,
)


def get_git_commit() -> str:
    """Get the current Git commit."""
    completed_process = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], stdout=subprocess.PIPE, check=False
    )

    return completed_process.stdout.decode("utf-8").strip()  # Without the trailing newline


def build_image(service_or_base: str, git_commit: str, *, no_cache: bool) -> None:
    """Build and tag given Docker image."""
    build_args = [f"git_commit={git_commit}"]
    dockerfile = f"docker/{service_or_base}.Dockerfile"

    if service_or_base not in get_base_images():
        dockerfile = "docker/service.Dockerfile"
        build_args.append(f"service_name={service_or_base}")

        base_image = get_base_image(service_or_base)
        build_args.append(f"base_image={base_image}")

        shared_folder = get_shared_folder(service_or_base)
        build_args.append(f"shared_folder={shared_folder}")

    commandline_args = ["docker", "build"]

    if no_cache:
        commandline_args.append("--no-cache")

    commandline_args.extend(
        [
            "-t",
            # Python identifiers use underscores (to disambiguate from minus operator) but Docker/Kubernetes uses dashes (to be valid DNS names)
            f"borndigitalaibot/{service_or_base.replace('_', '-')}",
            "-f",
            dockerfile,
            # Add "--progress=plain" when troubleshooting
        ]
    )

    for build_arg in build_args:
        commandline_args.extend(["--build-arg", build_arg])

    commandline_args.append(".")

    subprocess.run(
        commandline_args,
        stdout=subprocess.PIPE,  # Stderr is not needed
        check=True,  # Raise on non-zero return code
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build a Docker image for given service",
    )
    parser.add_argument(
        "service_or_base",
        help="Name of service or base image",
        choices=get_base_images() | get_service_names(),
    )
    parser.add_argument(
        "git_commit",
        help="Git commit used for building the image",
        nargs="?",
        default=get_git_commit(),
    )
    parser.add_argument(
        "--no-cache", help="Ignore the cached Docker layers", action="store_true", dest="no_cache"
    )
    args: argparse.Namespace = parser.parse_args()

    os.environ["DOCKER_BUILDKIT"] = "1"  # Use the BuildKit backend

    try:
        build_image(args.service_or_base, args.git_commit, no_cache=args.no_cache)
    except subprocess.CalledProcessError as error:
        print(error)
        sys.exit(1)
