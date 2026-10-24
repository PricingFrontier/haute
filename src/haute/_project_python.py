"""The project's Python: one version for local development, CI, and the deployed model.

A project declares its Python in ``.python-version`` (which uv reads first) or, failing
that, in ``[project].requires-python``. ``haute init`` writes ``.python-version`` when
the project has none, so uv's local environment, the generated CI jobs, a Databricks
model's conda environment and a container's base image all run the same version, at
the precision the project declares it: ``3.13`` matches any 3.13 release, ``3.13.11``
only that one.

A version is written at minor precision only when every release of that minor is one
the project admits; a ``requires-python`` that admits part of a minor (``>=3.13.5``,
``==3.12.8``) yields its lowest admitted release instead, so a runtime that takes any
patch of the minor can never land outside the constraint. A declaration Haute cannot
read, or one the other contradicts, is an error, never treated as absent.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from haute.errors import ConfigError

#: The lowest Python Haute runs on, and a project's version when it declares none.
DEFAULT_PYTHON = "3.11"

_MIN_MINOR = 11
# The newest minor, and the patches of each, a ``requires-python`` is searched over.
_NEWEST_MINOR = 30
_PATCHES = range(100)
_VERSION = re.compile(r"3\.(\d+)(?:\.\d+)?")

# A released patch with an official ``python:<patch>-slim`` image, for a container's
# base image when the project names its minor only (the base image must pin a patch,
# and a minor-precision version admits every patch of its minor).
_CONTAINER_PATCH = {"3.11": "3.11.9", "3.12": "3.12.8", "3.13": "3.13.1", "3.14": "3.14.0"}


class ProjectPythonError(ConfigError):
    """The project's Python declaration is unreadable, unsupported, or self-contradictory."""


def admits(allowed: SpecifierSet, version: str) -> bool:
    """Whether *allowed* admits every release *version* names (all of a bare minor's)."""
    if version.count(".") >= 2:
        return allowed.contains(version)
    return all(allowed.contains(f"{version}.{patch}") for patch in _PATCHES)


def pinned_python_version(project_dir: Path) -> str | None:
    """The Python ``.python-version`` pins, at the precision it is written in, if any."""
    pinned = project_dir / ".python-version"
    if not pinned.is_file():
        return None
    text = pinned.read_text(encoding="utf-8").strip()
    match = _VERSION.fullmatch(text)
    if match is None:
        raise ProjectPythonError(
            f".python-version holds {text!r}; Haute reads one CPython version, written "
            "like 3.13 or 3.13.1."
        )
    if int(match.group(1)) < _MIN_MINOR:
        raise ProjectPythonError(
            f".python-version pins Python {text}; Haute runs on Python {DEFAULT_PYTHON} or newer."
        )
    return text


def required_python(project_dir: Path) -> SpecifierSet | None:
    """``[project].requires-python`` as a specifier set, if the project states one."""
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProjectPythonError(f"pyproject.toml is not valid TOML: {exc}") from exc
    project = document.get("project")
    requires = project.get("requires-python") if isinstance(project, dict) else None
    if requires is None:
        return None
    try:
        if not isinstance(requires, str):
            raise InvalidSpecifier(repr(requires))
        return SpecifierSet(requires)
    except InvalidSpecifier as exc:
        raise ProjectPythonError(
            f"pyproject.toml's requires-python {requires!r} is not a version specifier "
            "(write one like '>=3.11')."
        ) from exc


def _lowest_admitted(allowed: SpecifierSet) -> str:
    for minor in range(_MIN_MINOR, _NEWEST_MINOR + 1):
        patches = [patch for patch in _PATCHES if allowed.contains(f"3.{minor}.{patch}")]
        if len(patches) == len(_PATCHES):
            return f"3.{minor}"
        if patches:
            return f"3.{minor}.{patches[0]}"
    raise ProjectPythonError(
        f"pyproject.toml's requires-python ({allowed}) admits no Python Haute runs on "
        f"(Python {DEFAULT_PYTHON} or newer)."
    )


def declared_python_version(project_dir: Path) -> str | None:
    """The Python *project_dir* declares, or ``None`` when it declares none.

    ``.python-version`` decides first, as it does for uv, at the precision it is written
    in, and must lie inside ``requires-python``; otherwise the lowest version from
    Haute's own floor that ``requires-python`` admits.
    """
    pinned = pinned_python_version(project_dir)
    allowed = required_python(project_dir)
    if pinned is not None:
        if allowed is not None and not admits(allowed, pinned):
            excluded = "it" if pinned.count(".") >= 2 else "some of its releases; pin a patch"
            raise ProjectPythonError(
                f".python-version pins Python {pinned}, but pyproject.toml's "
                f"requires-python ({allowed}) excludes {excluded}."
            )
        return pinned
    if allowed is None:
        return None
    return _lowest_admitted(allowed)


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
    other, :func:`deploy_python_mismatch`). Otherwise the running interpreter, the
    Python CI just tested and dry-run the pipeline on: its ``major.minor`` when
    ``requires-python`` admits every release of that minor, else its exact release.
    """
    pinned = pinned_python_version(project_dir)
    if pinned is not None:
        return pinned
    running_minor = ".".join(str(part) for part in sys.version_info[:2])
    allowed = required_python(project_dir)
    if allowed is None or admits(allowed, running_minor):
        return running_minor
    return running_python_version()


def deploy_python_mismatch(project_dir: Path) -> str | None:
    """Why the running interpreter is not the project's Python, or ``None`` when it is.

    An unreadable or self-contradictory declaration raises :class:`ProjectPythonError`.
    """
    running = running_python_version()
    declared_python_version(project_dir)  # Refuses an unreadable or contradictory declaration.
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
