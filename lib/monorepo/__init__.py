"""Utilities for traversing the filesystem of the monorepo."""

# Not part of the `lib.boilerplate` to avoid the 3rd-party dependencies
# Used both by scripts in `bin` and inside the GitHub Actions workflows

from __future__ import annotations

import pathlib
import re

DEFAULT_BASE_IMAGE = "base-debian"
REQUIREMENTS_REGEX = re.compile("^ *-r .*shared/([a-zA-Z_1-9]+)/")


def get_service_names() -> set[str]:
    """Get the names of services."""
    return {
        path.name
        for path in pathlib.Path("svc").iterdir()
        # Ignore __pycache__ and empty folders created by Git checkout of another branch
        if path.is_dir() and path.name[0] != "_" and (path / "__init__.py").exists()
    }


def get_library_names() -> set[str]:
    """Get the names of internal libraries (subdirectories of lib/)."""
    return {
        path.name
        for path in pathlib.Path("lib").iterdir()
        # Ignore __pycache__ and empty folders created by Git checkout of another branch
        if path.is_dir() and path.name[0] != "_" and (path / "__init__.py").exists()
    }


def get_base_images() -> set[str]:
    """Get names of base Docker images (Dockerfile names without the file extension)."""
    return {path.stem for path in pathlib.Path("docker").glob("base-*.Dockerfile")}


def get_base_image(service_name: str) -> str:
    """Get the name of base Docker image for given service's path."""
    # The file contains the name of the base image (e.g. base-ffmpeg)
    docker_image_path = pathlib.Path("svc") / service_name / "base_image"

    if docker_image_path.exists():
        with docker_image_path.open() as base_image:
            return base_image.readline().strip()

    return DEFAULT_BASE_IMAGE


def get_shared_folder(service_name: str) -> str:
    """Get the relative path to shared folder for given service's path."""
    requirements_path = pathlib.Path("svc") / service_name / "requirements.in"

    if not requirements_path.exists():
        return ""

    with requirements_path.open() as requirements_in:
        for line in requirements_in:
            match = REQUIREMENTS_REGEX.match(line)

            if match:
                return match[1]

    return ""  # The service doesn't use a shared folder


def get_service_version(service_name: str) -> str:
    """Get the version of a service from its VERSION file.

    Returns '0.0.0' if the VERSION file doesn't exist.
    """
    version_path = pathlib.Path("svc") / service_name / "VERSION"

    if version_path.exists():
        with version_path.open() as version_file:
            return version_file.readline().strip()

    return "0.0.0"
