#!/usr/bin/env python

"""Check/report the licenses of 3rd-party packages used by the services in monorepo."""

from __future__ import annotations

import argparse
import importlib.metadata
import pathlib
import re
import sys

import packaging.markers

monorepo_root_path: pathlib.Path = pathlib.Path().cwd()
sys.path.append(str(monorepo_root_path))

from lib.boilerplate import LogColor  # noqa: E402
from lib.monorepo import get_service_names  # noqa: E402

URL_REGEX = re.compile(r"https?://[^\s]+")
# Licenses which allow redistribution (when not modified) and allow use in SaaS
# Do not add AGPL/Affero GPL, it is incompatible with SaaS!
# Do not add GPL/LGPL -- add individual packages to the COMPATIBLE_PACKAGES instead
COMPATIBLE_LICENSES = {
    "MIT",
    "BSD",
    "Apache",
    "MPL",
    "ISC",
    "Python Software Foundation",
    "unlicense",
    "Public Domain",
}
# Packages with broken/non-standard license metadata but are safe to use
# or packages with license that requires providing a copy of that package (GPL/LGPL)
COMPATIBLE_PACKAGES = {
    "azure-cognitiveservices-speech",  # Proprietary but re-distributable
    "psycopg2-binary",  # LGPL, we have to offer its download
    "typing-extensions",  # PSF license but metadata shows as N/A
    "colorama",  # BSD license, only used on Windows
    "urllib3",  # MIT license but metadata shows as N/A
    "holidays",  # MIT license but metadata shows as N/A
    "cryptography",  # Apache 2.0 license but metadata sometimes shows as N/A
    "marisa-trie",  # MIT license but metadata shows as N/A
    "attrs",  # MIT license but metadata shows as N/A
    "jsonschema",  # MIT license but metadata shows as N/A
    "jsonschema-specifications",  # MIT license but metadata shows as N/A
    "referencing",  # MIT license but metadata shows as N/A
    "rpds-py",  # MIT license but metadata shows as N/A
    "sse-starlette",  # MIT license but metadata shows as N/A
    "cffi",  # MIT license but metadata shows as N/A
    "pyparsing",  # MIT license but metadata shows as N/A
    # Packages with N/A license metadata that are actually safe to use
    "alembic",  # MIT license but metadata shows as N/A
    "anyio",  # MIT license but metadata shows as N/A
    "click",  # BSD license but metadata shows as N/A
    "prometheus-client",  # Apache 2.0 license but metadata shows as N/A
    "regex",  # Apache 2.0 license but metadata shows as N/A
    "typing-inspection",  # MIT license but metadata shows as N/A
    "types-aiofiles",  # Apache license but metadata shows as N/A
    "setuptools",  # MIT license but metadata shows as N/A
    # Added per recent reports (metadata often N/A, licenses are permissive)
    "idna",  # BSD-like license (BSD-3-Clause), safe
    "MarkupSafe",  # BSD-3-Clause (Pallets), safe
    "pydantic",  # MIT license, safe
    "pydantic-core",  # MIT license, safe
    "httptools",  # MIT license, safe
    "uvicorn",  # BSD-3-Clause license but metadata shows as N/A
    "pywin32",  # PSF license, only used on Windows
    "google-api-core",  # Apache 2.0 license but metadata shows as N/A
    "fastapi",  # MIT license but metadata shows as N/A
    "python-dotenv",  # BSD-3-Clause license but metadata shows as N/A
    "starlette",  # BSD-3-Clause license but metadata shows as N/A
    "annotated-doc",  # MIT license but metadata shows as N/A
    "asyncpg",  # Apache 2.0 license but metadata shows as N/A
    "cachetools",  # MIT license but metadata shows as N/A
    "google-genai",  # Apache 2.0 license but metadata shows as N/A
    "packaging",  # Apache 2.0 / BSD-2-Clause dual-licensed but metadata shows as N/A
    "pycparser",  # BSD license but metadata shows as N/A
    "greenlet",  # MIT license but metadata shows as N/A
    "wrapt",  # BSD-2-Clause license but metadata shows as N/A
    "pyjwt",  # MIT license but metadata shows as N/A
    "argon2-cffi",  # MIT license but metadata shows as N/A
    "argon2-cffi-bindings",  # MIT license but metadata shows as N/A
    "numpy",  # BSD license but metadata shows as N/A
    "pyarrow",  # Apache 2.0 license but metadata shows as N/A
    "pypdf",  # BSD-3-Clause license but metadata shows as N/A
    "ujson",  # BSD license but metadata shows as N/A
    "websockets",  # BSD-3-Clause license but metadata shows as N/A
    "azure-core",  # MIT license but metadata shows as N/A
    "python-json-logger",  # BSD-2-Clause license but metadata shows as N/A
    "sentry-sdk",  # MIT license but metadata shows as N/A
    "jiter",  # MIT license but metadata shows as N/A
}

# Case-insensitive lookup for compatible packages
COMPATIBLE_PACKAGES_LOWER = {name.lower() for name in COMPATIBLE_PACKAGES}


def get_package_names(service_name: str) -> tuple[list[str], dict[str, str]]:
    """Get the 3rd-party packages used by given service and packages with markers not matching the current environment."""
    packages = []
    missing_packages = {}
    requirements_path = pathlib.Path("requirements") / f"{service_name}.txt"

    with requirements_path.open() as requirements_file:
        for raw_line in requirements_file:
            if "==" in raw_line:
                line = raw_line.replace(" \\\n", "")
                package_name, version_and_markers = line.split("==", maxsplit=1)
                # PEP 508 Environment Markers can be optionally added by uv pip compile --universal
                # e.g. "platform_system == 'Windows' or sys_platform == 'win32'"
                _package_version, *raw_package_markers = version_and_markers.split(";", maxsplit=1)

                if raw_package_markers:
                    package_markers = raw_package_markers[0].strip()

                    if not packaging.markers.Marker(package_markers).evaluate():
                        missing_packages[package_name] = package_markers
                        continue

                packages.append(package_name)

    return packages, missing_packages


def get_license(metadata: importlib.metadata.PackageMetadata) -> str:
    """Get the license of the package."""
    for classifier in metadata.get_all("Classifier", []):
        if "License" in classifier:
            parts = classifier.split("::")[1:]
            index = 1 if "OSI Approved" in parts[0] else 0

            license_name = " ".join(part.strip() for part in parts[index:])

            if license_name:  # Some packages use only "OSI Approved" without a license
                return license_name

    return metadata.get("License", "N/A")


def get_homepage(metadata: importlib.metadata.PackageMetadata) -> str:
    """Get the project homepage of the package."""
    homepage = metadata.get("Home-page")

    if homepage:
        return homepage

    for classifier in metadata.get_all("Project-URL", []):
        entry = classifier.lower().replace(",", "").strip()
        for key in ("homepage", "source", "repository", "github", "changelog", "bug tracker"):
            if key in entry:
                match = URL_REGEX.search(entry)
                if match:
                    return match.group()

    return "N/A"


def get_licenses(package_names: list[str]) -> dict[str, tuple[str, str, str]]:
    """Get a license report for given packages."""
    licenses = {}

    for package_name in package_names:
        # The right version of the package must be installed in the virtualenv
        try:
            distribution = importlib.metadata.distribution(package_name)
        except importlib.metadata.PackageNotFoundError:
            # Package not installed in current environment; skip gracefully so script can run locally
            continue

        package_license = get_license(distribution.metadata)
        package_homepage = get_homepage(distribution.metadata)

        licenses[package_name] = (distribution.version, package_license, package_homepage)

    return licenses


def get_unknown_licenses() -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Get licenses used by services in the monorepo which are not known to be safe for use."""
    unknown_licenses = {}
    missing_packages = {}

    for service_name in get_service_names():
        unknown_licenses_for_service = []
        packages, missing_packages = get_package_names(service_name)

        for package_name, license_data in get_licenses(packages).items():
            package_license = license_data[1]  # Package version and homepage are not needed

            for compatible_license in COMPATIBLE_LICENSES:
                # Package metadata use an inconsistent upper/lower case mix
                if compatible_license.lower() in package_license.lower():
                    break
            else:
                if package_name.lower() not in COMPATIBLE_PACKAGES_LOWER:
                    unknown_licenses_for_service.append((package_name, package_license))

            if unknown_licenses_for_service:
                unknown_licenses[service_name] = unknown_licenses_for_service

    return unknown_licenses, missing_packages


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check or print the licenses of 3rd-party packages used in the monorepo",
    )

    parser.add_argument(
        "--check-only",
        help="Only check for potentially incompatible licenses",
        action="store_true",
        dest="check_only",
    )

    parser.add_argument(
        "ignored",
        help="Filenames added by pre-commit hook, silently ignored",
        nargs="*",
    )  # Pre-commit hook provides a list of files modified in the Git commit but we don't use them

    args: argparse.Namespace = parser.parse_args()
    color = LogColor()

    if args.check_only:
        unknown_licenses, missing_packages = get_unknown_licenses()

        for missing_package, package_markers in missing_packages.items():
            print(
                f"{color.yellow}License of package {missing_package!r} could not be checked, is installed only for {package_markers!r}{color.default}"
            )

        if unknown_licenses:
            for service_name, unknown_licenses_for_service in unknown_licenses.items():
                for package_name, package_license in unknown_licenses_for_service:
                    print(
                        f"{color.red}{service_name}: Package {package_name!r} has unknown license: {package_license!r}{color.default}"
                    )

            print("If these licenses are safe to use, add them to `bin/report_licenses.py`")
            sys.exit(1)
    else:
        for service_name in get_service_names():
            print()
            print(f"--- {service_name} ---")
            print("Package name | version | license | homepage")
            print("---" * 15)

            packages, missing_packages = get_package_names(service_name)

            for package_name, (
                package_version,
                package_license,
                package_homepage,
            ) in get_licenses(packages).items():
                print(
                    f"{package_name} | {package_version} | {package_license} | {package_homepage}"
                )

            for missing_package, package_markers in missing_packages.items():
                print(
                    f"{color.yellow}License of package {missing_package!r} could not be checked, is installed only for {package_markers!r}{color.default}"
                )
