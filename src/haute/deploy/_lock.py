"""One resolver for every deployment's dependency lock.

A deployed pipeline installs a lock rather than resolving at its target's build: the
container image, the Databricks model and the hosted app all install every distribution
pinned by version and hash, resolved here by ``uv pip compile`` for the target's Python
and platform, so two builds of one bundle install the same runtime and a deploying host on
another platform (Windows, macOS) never decides what Linux installs. Only wheels are
accepted: a dependency without one for the target fails the lock naming it, rather than
being built from source on the target. Index URLs are recorded without their credentials,
which never ship; the hashes pin every file, wherever the target installs it from. The
hosted app's bundle script (``databricks_app/scripts/build_bundle.sh``) locks its runtime
with the same ``uv pip compile`` options.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name

from haute.errors import DeployError

#: The platform every deployment runs on: a scoring image is built for it, and Databricks
#: Model Serving and Databricks Apps run on it.
TARGET_PLATFORM = "x86_64-manylinux_2_28"
#: The platform ``docker build`` builds a scoring image for, the one its lock is resolved for.
DOCKER_PLATFORM = "linux/amd64"

_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")
_INDEX = re.compile(r"^--(?P<option>(?:extra-)?index-url) (?P<url>\S+)\s*$")
_CREDENTIALS = re.compile(r"://[^/@\s]*@")


def _canonical(name: str) -> str:
    """A distribution name as installers compare it (PEP 503)."""
    return str(canonicalize_name(name))


@dataclass(frozen=True)
class DependencyLock:
    """A resolved dependency closure and how it was resolved."""

    requirements: str
    """The lock as pip reads it: every distribution pinned by version and hash."""
    python_version: str
    platform: str
    resolver: str
    roots: tuple[str, ...]
    indexes: tuple[str, ...]
    """The indexes resolved from, credentials removed."""

    @property
    def pins(self) -> dict[str, str]:
        """Each locked distribution's version, by its canonical name."""
        pins: dict[str, str] = {}
        for line in self.requirements.splitlines():
            match = _PIN.match(line)
            if match:
                pins[_canonical(match["name"])] = match["version"]
        return pins

    def provenance(self) -> dict[str, Any]:
        """What the lock was resolved for and with, as a bundle records it beside the lock."""
        return {
            "python_version": self.python_version,
            "platform": self.platform,
            "resolver": self.resolver,
            "only_binary": True,
            "roots": list(self.roots),
            "indexes": list(self.indexes),
            "pins": self.pins,
        }


def uv_command() -> list[str]:
    """The ``uv`` executable to resolve with; a host without one is refused."""
    uv = shutil.which("uv")
    if uv is None:
        raise DeployError(
            "Locking a deployment's dependencies needs uv, which is not on PATH. Install uv "
            "(https://docs.astral.sh/uv/), as the generated CI workflows do, and deploy again."
        )
    return [uv]


def lock_requirements(
    roots: Sequence[str] | Path,
    *,
    python_version: str,
    platform: str = TARGET_PLATFORM,
    no_deps: bool = False,
    extras: Sequence[str] = (),
    constraints: Sequence[str] = (),
) -> DependencyLock:
    """Resolve *roots* for *python_version* on *platform*: wheels only, hashed.

    *roots* are requirement strings, or a requirements or ``pyproject.toml`` file (with
    *extras*). *no_deps* locks the roots alone, for a distribution installed without its
    dependencies. *constraints* bound any distribution the closure holds without adding
    one: a scoring image's, haute's own ranges, since haute is installed beside the lock.
    Raises :class:`DeployError` naming the target when the closure cannot be resolved
    from wheels (a dependency with none for it, a version the index lacks).
    """
    command = uv_command()
    with tempfile.TemporaryDirectory(prefix="haute_lock_") as scratch:
        root_names: tuple[str, ...]
        if isinstance(roots, Path):
            source, root_names = roots, (str(roots.name),)
        else:
            source = Path(scratch) / "roots.in"
            source.write_text("".join(f"{root}\n" for root in roots), encoding="utf-8")
            root_names = tuple(roots)
        constraint_file = Path(scratch) / "constraints.txt"
        constraint_file.write_text("".join(f"{line}\n" for line in constraints), encoding="utf-8")
        output = Path(scratch) / "requirements.txt"
        arguments = [
            "pip",
            "compile",
            str(source),
            "--python-version",
            python_version,
            "--python-platform",
            platform,
            "--generate-hashes",
            "--only-binary",
            ":all:",
            "--emit-index-url",
            "--no-header",
            "--quiet",
            *(["--no-deps"] if no_deps else []),
            *(argument for extra in extras for argument in ("--extra", extra)),
            *(["--constraint", str(constraint_file)] if constraints else []),
            "--output-file",
            str(output),
        ]
        result = subprocess.run(
            [*command, *arguments], capture_output=True, text=True, encoding="utf-8"
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            raise DeployError(
                f"Could not lock the deployment's dependencies for CPython {python_version} "
                f"on {platform}: every dependency must resolve to a wheel for it, pinned by "
                f"hash.\n{detail}",
                python_version=python_version,
                platform=platform,
            )
        compiled = output.read_text(encoding="utf-8")
    version = subprocess.run(
        [*command, "--version"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()
    requirements, indexes = _without_index_credentials(compiled)
    return DependencyLock(
        requirements=requirements,
        python_version=python_version,
        platform=platform,
        resolver=version,
        roots=root_names,
        indexes=indexes,
    )


def _without_index_credentials(compiled: str) -> tuple[str, tuple[str, ...]]:
    """*compiled*'s index options as provenance comments, their credentials removed: the
    hashes pin every file, so the target installs from its own index, and a credential must
    never ship in a bundle."""
    lines: list[str] = []
    indexes: list[str] = []
    for line in compiled.splitlines():
        match = _INDEX.match(line)
        if match is None:
            lines.append(line)
            continue
        url = _CREDENTIALS.sub("://", match["url"])
        indexes.append(url)
        lines.append(f"# resolved from --{match['option']} {url}")
    return "\n".join(lines) + "\n", tuple(indexes)
