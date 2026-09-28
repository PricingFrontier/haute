"""Refuse a release whose version is not new, for the Release workflow.

The Release workflow publishes the version ``pyproject.toml`` declares on
``main``; bumping it is an ordinary pull-request change. This check refuses a
version PyPI already has, or one that is not newer than every release there, so
an unbumped ``main`` cannot be released twice. On success it writes the version
to the GitHub output file so later jobs can tag and name the release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path

_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_BUMP_HINT = (
    "Bump [project] version in pyproject.toml in a pull request "
    "(for example `uv version --bump patch`), merge it, then run Release again."
)


class ReleaseVersionError(Exception):
    """The declared version cannot be released."""


def _parts(version: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(version)
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def declared_version(pyproject: Path) -> str:
    """The ``[project] version`` of *pyproject*."""
    with pyproject.open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def released_versions(pypi_json: Path | None) -> list[str]:
    """The versions PyPI's JSON API lists; none for a project never published."""
    if pypi_json is None:
        return []
    document = json.loads(pypi_json.read_text(encoding="utf-8"))
    return sorted(document["releases"])


def check_release_version(version: str, released: Iterable[str]) -> str:
    """Return *version* when it is a new ``X.Y.Z`` release, else raise.

    Releases PyPI has in another form (a pre-release, say) cannot collide with
    an ``X.Y.Z`` version and are not compared.
    """
    parts = _parts(version)
    if parts is None:
        raise ReleaseVersionError(
            f"pyproject.toml declares version {version!r}; a release must be X.Y.Z."
        )
    published = set(released)
    if version in published:
        raise ReleaseVersionError(f"haute {version} is already on PyPI. {_BUMP_HINT}")
    comparable = {parsed: text for text in published if (parsed := _parts(text)) is not None}
    if comparable and parts <= max(comparable):
        latest = comparable[max(comparable)]
        raise ReleaseVersionError(
            f"haute {version} is not newer than {latest}, the latest release on PyPI. {_BUMP_HINT}"
        )
    return version


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument(
        "--pypi-json",
        type=Path,
        help="PyPI's JSON API response for the project; omit when it has never been published",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        required=True,
        help="file the version is appended to as `version=X.Y.Z`",
    )
    args = parser.parse_args(argv)
    try:
        version = check_release_version(
            declared_version(args.pyproject), released_versions(args.pypi_json)
        )
    except ReleaseVersionError as exc:
        print(f"::error::{exc}")
        return 1
    with args.github_output.open("a", encoding="utf-8") as stream:
        stream.write(f"version={version}\n")
    print(f"Releasing haute {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
