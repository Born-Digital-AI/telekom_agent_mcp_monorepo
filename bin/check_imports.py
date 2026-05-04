#!/usr/bin/env python

"""Check that all libraries and services obey the import rules."""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.monorepo import get_library_names, get_service_names  # noqa: E402


def get_first_party_imports(module_path: pathlib.Path) -> tuple[set[str], set[str]]:
    """Get imports from `lib` and `svc` used in a Python module at given filesystem path."""
    imports = set()

    with pathlib.Path(module_path).open() as source_file:
        contents = source_file.read()

    for node in ast.walk(ast.parse(contents)):
        if isinstance(node, ast.Import):
            imports |= {name.name for name in node.names}
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    return {module for module in imports if module.startswith("lib.")}, {
        module for module in imports if module.startswith("svc.")
    }


def get_forbidden_monorepo() -> list[str]:
    """Get all imports in the monorepo that cross the monorepo layers in a forbidden way."""
    forbidden_imports: list[str] = []
    service_paths = {pathlib.Path(service_name) for service_name in get_service_names()}
    library_paths = {pathlib.Path(service_name) for service_name in get_library_names()}

    for package_path in library_paths | service_paths:
        for path, _, file_names in pathlib.Path(package_path).walk():
            if "__pycache__" in str(path):
                continue

            for file_name in file_names:
                if file_name.endswith(".py") and not file_name.startswith("tests/"):
                    _, svc_imports = get_first_party_imports(path / file_name)

                    for service_name in svc_imports:
                        # Allow imports within the same service (service name is 2nd component of the path/filename)
                        if path.parts[1] != service_name.split(".")[1]:
                            forbidden_imports.append(  # noqa: PERF401
                                f"'{path / file_name!s}' cannot import from service {service_name!r}"
                            )

    return forbidden_imports


def get_forbidden_files(files: list[str]) -> list[str]:
    """Get imports in given files that cross the monorepo layers in a forbidden way."""
    forbidden_imports: list[str] = []

    for file_name in files:
        file_path = pathlib.Path(file_name)

        if file_name.endswith(".py") and not file_name.startswith("tests/"):
            _, svc_imports = get_first_party_imports(file_path)

            for service_name in svc_imports:
                # Allow imports within the same service (service name is 2nd component of the path/filename)
                if pathlib.Path(file_name).parts[1] != service_name.split(".")[1]:
                    forbidden_imports.append(  # noqa: PERF401
                        f"'{file_name!s}' cannot import from service {service_name!r}"
                    )

    return forbidden_imports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check the direction of imports in the monorepo",
    )
    parser.add_argument(
        "files",
        help="Limit the check to given files",  # Used by a pre-commit hook
        nargs="*",
    )
    args: argparse.Namespace = parser.parse_args()

    forbidden_imports = get_forbidden_files(args.files) if args.files else get_forbidden_monorepo()

    print("\n".join(forbidden_imports))

    if forbidden_imports:
        sys.exit(1)
