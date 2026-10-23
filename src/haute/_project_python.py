"""The project's Python: one version for local development, CI, and the deployed model.

A project declares its Python in ``.python-version`` (which uv reads first) or, failing
that, in ``[project].requires-python``. ``haute init`` writes ``.python-version`` when
the project has none, so uv's local environment, the generated CI jobs, a Databricks
model's conda environment and a container's base image all run the same version, at
the precision the project declares it: ``3.13`` matches any 3.13 release, ``3.13.11``
only that one.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

#: The lowest Python Haute runs on, and a project's version when it declares none.
DEFAULT_PYTHON = "3.11"

_MIN_MINOR = 11
# The newest minor a ``requires-python`` range is searched up to.
_NEWEST_MINOR = 30
_VERSION = re.compile(r"\s*(3\.(\d+)(?:\.\d+)?)\b")

# A released patch with an official ``python:<patch>-slim`` image, for a container's
# base image when the project names its minor only (the base image must pin a patch).
_CONTAINER_PATCH = {"3.11": "3.11.9", "3.12": "3.12.8", "3.13": "3.13.1", "3.14": "3.14.0"}


def pinned_python_version(project_dir: Path) -> str | None:
    """The Python ``.python-version`` pins, at the precision it is written in, if any."""
    pinned = project_dir / ".python-version"
    if not pinned.is_file():
        return None
    match = _VERSION.match(pinned.read_text(encoding="utf-8"))
    return match.group(1) if match and int(match.group(2)) >= _MIN_MINOR else None


def required_python(project_dir: Path) -> SpecifierSet | None:
    """``[project].requires-python`` as a specifier set, if the project states one."""
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
        requires = project.get("requires-python") if isinstance(project, dict) else None
        return SpecifierSet(requires) if isinstance(requires, str) else None
    except (tomllib.TOMLDecodeError, InvalidSpecifier, UnicodeDecodeError):
        return None


def declared_python_version(project_dir: Path) -> str | None:
    """The Python *project_dir* declares, or ``None`` when it declares none Haute runs on.

    ``.python-version`` decides first, as it does for uv, at the precision it is written
    in; then the lowest ``3.x`` from Haute's own floor that ``requires-python`` admits.
    """
    pinned = pinned_python_version(project_dir)
    if pinned is not None:
        return pinned
    allowed = required_python(project_dir)
    if allowed is None:
        return None
    for minor in range(_MIN_MINOR, _NEWEST_MINOR + 1):
        if allowed.contains(f"3.{minor}.0") or allowed.contains(f"3.{minor}.99"):
            return f"3.{minor}"
    return None


def project_python_version(project_dir: Path) -> str:
    """The Python *project_dir* runs on: the one it declares, else :data:`DEFAULT_PYTHON`."""
    return declared_python_version(project_dir) or DEFAULT_PYTHON


def python_version_matches(declared: str, actual: str) -> bool:
    """Whether *actual* is the *declared* Python, compared at *declared*'s precision."""
    want = declared.split(".")
    return actual.split(".")[: len(want)] == want


def running_python_version() -> str:
    """The interpreter running Haute, as ``major.minor.micro``."""
    return ".".join(str(part) for part in sys.version_info[:3])


def deployed_python_version(project_dir: Path) -> str:
    """The Python a deployed model runs: the one the deploy itself was validated on.

    ``.python-version``'s pin when the project has one (deploy refuses to run on any
    other, :func:`deploy_python_mismatch`); otherwise the running interpreter's
    ``major.minor``, the Python CI just tested and dry-run the pipeline on.
    """
    pinned = pinned_python_version(project_dir)
    if pinned is not None:
        return pinned
    return ".".join(str(part) for part in sys.version_info[:2])


def deploy_python_mismatch(project_dir: Path) -> str | None:
    """Why the running interpreter is not the project's Python, or ``None`` when it is."""
    running = running_python_version()
    pinned = pinned_python_version(project_dir)
    if pinned is not None:
        if python_version_matches(pinned, running):
            return None
        return (
            f"Haute is running on Python {running}, but the project pins Python {pinned} "
            "in .python-version. Run it on the pinned Python (uv does, through "
            "`uv run`) so the deployed model runs the Python the pipeline was tested on."
        )
    allowed = required_python(project_dir)
    if allowed is None or allowed.contains(running):
        return None
    return (
        f"Haute is running on Python {running}, which the project's requires-python "
        f"({allowed}) excludes. Run it on a Python the project allows."
    )


def container_base_image(version: str) -> str:
    """A patch-pinned ``python:<patch>-slim`` base image for the project's *version*."""
    patch = version if version.count(".") >= 2 else _CONTAINER_PATCH.get(version, f"{version}.0")
    return f"python:{patch}-slim"


def base_image_python_version(base_image: str) -> str | None:
    """The Python an official ``python`` image's tag names, or ``None`` for any other image."""
    match = re.search(r"(?:^|/)python:(3\.\d+(?:\.\d+)?)", base_image)
    return match.group(1) if match else None
