"""Detect which services need version bumps based on changed files.

This script analyzes git diff to determine which services have been modified,
either directly or through changes to their dependencies (shared folders, lib modules).

Usage:
    bin/detect_changed_services.py                    # Compare HEAD~1 to HEAD
    bin/detect_changed_services.py --base main        # Compare main to HEAD
    bin/detect_changed_services.py --base abc123      # Compare specific commit to HEAD
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.monorepo import get_service_names, get_shared_folder  # noqa: E402

REQUIREMENTS_REGEX = re.compile(r"^\s*-r\s+\.\./\.\./(?:lib|shared)/([a-zA-Z_0-9]+)/")


def get_changed_files(base_ref: str = "HEAD~1") -> set[str]:
    """Get list of files changed between base_ref and HEAD."""
    result = subprocess.run(
        ["git", "diff", "--name-only", base_ref, "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    files = result.stdout.strip().split("\n")
    return {f for f in files if f}  # Filter empty strings


def get_service_dependencies(service_name: str) -> set[str]:
    """Get all dependency paths for a service (shared folders and lib modules).

    Returns path prefixes that, if changed, should trigger a version bump.
    """
    deps = {f"svc/{service_name}/"}

    # Add shared folder dependency
    shared = get_shared_folder(service_name)
    if shared:
        deps.add(f"shared/{shared}/")

    # Parse requirements.in for additional lib dependencies
    requirements_path = pathlib.Path("svc") / service_name / "requirements.in"
    if requirements_path.exists():
        with requirements_path.open() as f:
            for line in f:
                match = REQUIREMENTS_REGEX.match(line)
                if match:
                    dep_name = match.group(1)
                    # Determine if it's lib or shared
                    if (pathlib.Path("lib") / dep_name).exists():
                        deps.add(f"lib/{dep_name}/")
                    elif (pathlib.Path("shared") / dep_name).exists():
                        deps.add(f"shared/{dep_name}/")

    return deps


def detect_changed_services(base_ref: str = "HEAD~1") -> list[str]:
    """Detect which services have changes that require a version bump.

    A service needs a version bump if any of these paths changed:
    - svc/{service_name}/
    - shared/{shared_folder}/ (if the service uses a shared folder)
    - lib/{lib_module}/ (if the service depends on a lib module)
    """
    try:
        changed_files = get_changed_files(base_ref)
    except subprocess.CalledProcessError:
        # If git diff fails (e.g., no commits), return empty list
        return []

    if not changed_files:
        return []

    changed_services = []

    for service_name in sorted(get_service_names()):
        deps = get_service_dependencies(service_name)

        for dep_path in deps:
            if any(f.startswith(dep_path) for f in changed_files):
                changed_services.append(service_name)
                break

    return changed_services


def main() -> None:
    """Run the CLI."""
    parser = argparse.ArgumentParser(
        description="Detect which services need version bumps based on changed files"
    )
    parser.add_argument(
        "--base",
        default="HEAD~1",
        help="Base ref to compare against (default: HEAD~1)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print verbose output to stderr",
    )
    args = parser.parse_args()

    changed_services = detect_changed_services(args.base)

    if args.verbose:
        if changed_services:
            print(f"Changed services: {', '.join(changed_services)}", file=sys.stderr)
        else:
            print("No services changed", file=sys.stderr)

    # Output JSON array for GitHub Actions matrix
    print(json.dumps(changed_services))


if __name__ == "__main__":
    main()
