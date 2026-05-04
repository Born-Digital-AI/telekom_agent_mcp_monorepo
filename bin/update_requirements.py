#!/usr/bin/env python

"""Automatically update the requirements.in for all libraries and services based on imports they make.

Beware, serves only as a helper and needs manual inspection before committing!
Run only with a clean Git workspace!

Some 3rd-party Python libraries registered a different name in PyPI from the imported package name.
There is no way how to convert the imported package name to the PyPI package name automatically.
"""

from __future__ import annotations

import ast
import pathlib
import sys

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.monorepo import get_library_names, get_service_names  # noqa: E402


def get_imports(module_path: str) -> set[str]:
    """Get all imports used in a Python module at given filesystem path."""
    imports = set()

    with pathlib.Path(module_path).open() as source_file:
        contents = source_file.read()

    for node in ast.walk(ast.parse(contents)):
        if isinstance(node, ast.Import):
            imports |= {name.name for name in node.names}
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    return imports


def split_imports(imports: set[str]) -> tuple[set[str], set[str]]:
    """Split imports for a Python module into first-party and third-party (and discard imports from stdlib)."""
    first_party = set()
    third_party = set()

    for module in imports:
        if module.startswith(("lib.", "svc.", "shared.")):
            first_party.add(module.split(".")[1])

        package = module.split(".")[0]
        if package not in sys.stdlib_module_names and package not in {"lib", "svc", "shared"}:
            third_party.add(package)

    return first_party, third_party


def prepare_requirements_in(
    first_party: set[str],
    third_party: set[str],
    is_service: bool,  # noqa: FBT001
) -> list[str]:
    """Prepare the contents of the requirements.in for given 1st and 3rd-party dependencies."""
    lines = ["# Direct local dependencies"]

    if is_service:
        lines.extend(
            f"-r ../../lib/{package}/requirements.in"
            for package in sorted(first_party)
            if package.startswith("lib.")
        )
        lines.extend(
            f"-r ../../shared/{package}/requirements.in"
            for package in sorted(first_party)
            if package.startswith("shared.")
        )
    else:
        lines.extend(f"-r ../{package}/requirements.in" for package in sorted(first_party))

    lines.append("\n# Direct 3rd-party dependencies")
    lines.extend(package.replace("_", "-") for package in sorted(third_party))

    return lines


def combine_imports(package_path: str) -> tuple[set[str], set[str]]:
    """Combine the 1st and 3rd-party imports from all modules in given package."""
    first_party = set()
    third_party = set()

    for path, _, filenames in pathlib.Path(package_path).walk():
        if "__pycache__" in str(path):
            continue

        for filename in filenames:
            if filename.endswith(".py"):
                first, third = split_imports(get_imports(str(path / filename)))
                first_party |= first - {str(path.stem)}
                third_party |= third - {str(path.stem)}

    return first_party, third_party


def update_library_requirements_in() -> None:
    """Overwrite the requirements.in for each internal library."""
    for package in get_library_names():
        first_party, third_party = combine_imports(f"lib/{package}")

        with pathlib.Path(f"lib/{package}/requirements.in").open("w") as requirements_in:
            for line in prepare_requirements_in(first_party, third_party, is_service=False):
                requirements_in.write(line)
                requirements_in.write("\n")


def update_service_requirements_in() -> None:
    """Overwrite the requirements.in for each service."""
    for package in get_service_names():
        first_party, third_party = combine_imports(f"svc/{package}")

        with pathlib.Path(f"svc/{package}/requirements.in").open("w") as requirements_in:
            for line in prepare_requirements_in(first_party, third_party, is_service=True):
                requirements_in.write(line)
                requirements_in.write("\n")


if __name__ == "__main__":
    update_library_requirements_in()
    update_service_requirements_in()
