#!/usr/bin/env python

"""Re-pin PIP packages used by libraries/services to latest versions and update the virtual environment.

Expects that `uv` (https://github.com/astral-sh/uv) is installed and available via PATH.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

PYTHON_VERSION = "3.12"

# Do not import anything except the standard library


def upgrade_uv() -> None:
    """Upgrade the `uv` package manager."""
    download = subprocess.run(
        [
            "curl",
            "-LsSf",
            "https://astral.sh/uv/install.sh",
        ],
        capture_output=True,
        check=True,  # Raise on non-zero return code
    )
    subprocess.run(["sh"], input=download.stdout, check=True)


def generate_requirements_in() -> None:
    """Combine direct dependencies of all libraries and services in the monorepo."""
    path = pathlib.Path("requirements") / "requirements.in"

    with path.open("w") as requirements_in:
        requirements_in.writelines(
            [
                "# Union of all 3rd-party dependencies of all libraries/services in the monorepo\n",
                "# This file is auto-generated using `bin/upgrade_packages.py`, do not modify manually!\n\n",
                "# All internal libraries\n",
            ],
        )
        requirements_in.writelines(
            [
                f"-r ../{lib_path.as_posix()}\n"
                for lib_path in pathlib.Path("lib").glob("*/requirements.in")
            ],
        )
        requirements_in.writelines(
            [
                f"-r ../{lib_path.as_posix()}\n"
                for lib_path in pathlib.Path("shared").glob("*/requirements.in")
            ],
        )
        requirements_in.write("\n# All internal services\n")
        requirements_in.writelines(
            [
                f"-r ../{svc_path.as_posix()}\n"
                for svc_path in pathlib.Path("svc").glob("*/requirements.in")
            ],
        )


def generate_constraints() -> None:
    """Pin transitive dependencies compatible with all libraries/services in the monorepo."""
    path = pathlib.Path("requirements") / "constraints.txt"

    with path.open("w") as constraints:
        constraints.writelines(
            [
                "# Pinned transitive dependencies of all services and libraries\n",
                "# This file is auto-generated using `bin/upgrade_packages.py`, do not modify manually!\n\n",
            ],
        )
        completed_process = subprocess.run(
            [
                "uv",
                "pip",
                "compile",
                "--generate-hashes",  # Needed when constraints.txt are used as requirements.txt for local development
                "--emit-index-url",
                "--universal",
                pathlib.Path("requirements") / "requirements.in",
            ],
            stdout=subprocess.PIPE,  # Stderr is not needed
            check=True,  # Raise on non-zero return code
        )
        constraints.write(completed_process.stdout.decode("utf-8"))


def generate_requirements(folder: pathlib.Path) -> None:
    """Pin transitive dependencies of given library/service according to monorepo-wide constraints."""
    path = pathlib.Path("requirements") / f"{folder.name}.txt"

    with path.open("w") as requirements:
        purpose = "library" if folder.parent.name in ("lib", "shared") else "service"
        requirements.writelines(
            [
                f"# Pinned transitive dependencies for {purpose} `{folder.name}`\n",
                "# This file is auto-generated using `bin/upgrade_packages.py`, do not modify manually!\n\n",
            ],
        )

        completed_process = subprocess.run(
            [
                "uv",
                "pip",
                "compile",
                "--generate-hashes",
                "--emit-index-url",
                "--universal",
                "-c",
                pathlib.Path("requirements") / "constraints.txt",
                folder / "requirements.in",
            ],
            stdout=subprocess.PIPE,  # Stderr is not needed
            check=True,  # Raise on non-zero return code
        )
        requirements.write(completed_process.stdout.decode("utf-8"))


def populate_virtualenv() -> None:
    """Populate the virtualenv by pinned packages for all libraries/services in the monorepo."""
    subprocess.run(
        [
            "uv",
            "venv",
            "-p",
            PYTHON_VERSION,
            "--clear",
        ],
        stdout=subprocess.PIPE,  # Stderr is not needed
        check=True,  # Raise on non-zero return code
    )  # Create/rewrite the .venv by empty virtual environment

    constraints_file = pathlib.Path("requirements") / "constraints.txt"
    extra_index_urls_args = []
    with constraints_file.open() as file:
        for line in file:
            if line.startswith("--extra-index-url"):
                extra_index_urls_args.extend(line.split(" "))

    subprocess.run(
        [
            "uv",
            "pip",
            "sync",
            "--require-hashes",
            *extra_index_urls_args,
            constraints_file,
        ],
        stdout=subprocess.PIPE,  # Stderr is not needed
        check=True,  # Raise on non-zero return code
    )  # Install all packages to the virtual environment

    # Development-only packages are intentionally not pinned unless there is a reason for it
    # As a result, they are not part of the constraints and their hashes are not checked
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "-r",
            "requirements-dev.in",
            "-c",
            pathlib.Path("requirements") / "constraints.txt",
        ],
        stdout=subprocess.PIPE,  # Stderr is not needed
        check=True,  # Raise on non-zero return code
    )  # Install development-only packages to the virtual environment


def upgrade_precommit() -> None:
    """Upgrade the pre-commit hooks."""
    subprocess.run(
        [
            "pre-commit",
            "autoupdate",
        ],
        stdout=subprocess.PIPE,  # Stderr is not needed
        check=True,  # Raise on non-zero return code
    )  # Rewrite the hook versions in .pre-commit-config.yaml


def check_vulnerabilities() -> None:
    """Check for known vulnerabilities in the virtual environment (using the PyPA's advisory database)."""
    subprocess.run(
        [
            "pip-audit",
            "--ignore-vuln",
            "GHSA-4xh5-x5gv-qwph",  # pip 25.2 vulnerability - pip itself, not affecting runtime dependencies
            "--ignore-vuln",
            "CVE-2026-0994",  # pymilvus 2.3.7 vulnerability - vulnerable JSON-to-protobuf parsing path is not used (gRPC only).
        ],
        stdout=subprocess.PIPE,  # Stderr is not needed
        check=True,  # Raise on non-zero return code
    )  # Non-zero exit code if any vulnerabilities were found


if __name__ == "__main__":
    try:
        print("Upgrading the `uv` package manager")
        upgrade_uv()

        print("Generating the monorepo-wide `requirements.in`")
        generate_requirements_in()

        print("Upgrading the monorepo-wide `requirements/constraints.txt`:")
        generate_constraints()

        for top_folder in ("lib", "svc", "shared"):
            for path in pathlib.Path(top_folder).glob("*/requirements.in"):
                print(f"Generating the `requirements/{path.parent.name}.txt`:")
                generate_requirements(path.parent)

        print("Populating the `.venv` by upgraded packages:")
        populate_virtualenv()

        print("Upgrading the pre-commit hooks:")
        upgrade_precommit()

        print("Checking for known vulnerabilities in the packages")
        check_vulnerabilities()
    except subprocess.CalledProcessError as error:
        print(error)
        sys.exit(1)
