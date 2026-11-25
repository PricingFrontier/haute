"""Which files an assistant example bundle holds: the one rule its loader and the build share.

A bundle's manifest names every file the bundle is. What an interpreter or an installer
writes beside those files is not part of the bundle: pip compiles each ``.py`` file it
installs into a ``__pycache__`` folder beside it by default, and every bundle holds a
``pipeline.py``. Any other file the manifest does not name is undeclared, and both the
loader (`haute.assistant._assets`) and the wheel's build hook (``hatch_build.py``) refuse
the bundle naming it.

The build hook loads this file by path, before Haute or its dependencies are installed, so
it imports nothing but the standard library.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

#: The folder name CPython writes compiled modules into.
INTERPRETER_CACHE_DIR = "__pycache__"
#: The suffixes of a compiled module written beside its source.
COMPILED_SUFFIXES = (".pyc", ".pyo")
MANIFEST_NAME = "manifest.json"


class BundleEntry(Protocol):
    """A `pathlib.Path` or an `importlib.resources` Traversable: both walk a bundle."""

    @property
    def name(self) -> str: ...

    def is_file(self) -> bool: ...

    def is_dir(self) -> bool: ...

    def iterdir(self) -> Iterator[BundleEntry]: ...


def is_interpreter_output(entry: BundleEntry) -> bool:
    """Whether *entry* is a compiled module or its cache folder, never part of a bundle."""

    if entry.is_dir():
        return entry.name == INTERPRETER_CACHE_DIR
    return entry.name.endswith(COMPILED_SUFFIXES)


def bundle_files(root: BundleEntry, prefix: str = "") -> tuple[str, ...]:
    """Every file under *root*, as a POSIX path relative to it, interpreter output left out."""

    paths: list[str] = []
    for child in root.iterdir():
        if is_interpreter_output(child):
            continue
        path = f"{prefix}{child.name}"
        if child.is_file():
            paths.append(path)
        elif child.is_dir():
            paths.extend(bundle_files(child, prefix=f"{path}/"))
    return tuple(paths)


def declared_files(manifest: object) -> frozenset[str]:
    """The files a bundle's parsed *manifest* names: its own and each resource's path."""

    resources = manifest.get("resources") if isinstance(manifest, dict) else None
    if not isinstance(resources, list):
        raise RuntimeError("An assistant example manifest must list its resources.")
    paths = {MANIFEST_NAME}
    for item in resources:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str):
            raise RuntimeError("An assistant example manifest resource must name its path.")
        paths.add(path.replace("\\", "/"))
    return frozenset(paths)


def undeclared_files(root: BundleEntry, manifest: object) -> list[str]:
    """The files under bundle *root* its parsed *manifest* does not name, sorted."""

    return sorted(set(bundle_files(root)) - declared_files(manifest))


__all__ = [
    "COMPILED_SUFFIXES",
    "INTERPRETER_CACHE_DIR",
    "MANIFEST_NAME",
    "bundle_files",
    "declared_files",
    "is_interpreter_output",
    "undeclared_files",
]
