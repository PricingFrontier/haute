"""Install each built distribution into a fresh environment and smoke-check it there.

The one definition of the package smoke: CI's ``package-smoke`` job, the Release
workflow's ``build`` job and a local run (docs/CI_MIRROR.md) all call it, so a step
one of them needs cannot be missing from another.

The wheel and the sdist in ``--dist`` each go into a new virtual environment,
installed as pip installs by default, every module compiled (``--compile-bytecode``:
the assistant's examples must load beside the caches). Then
``scripts/package_smoke_check.py`` and ``haute --help`` run with that environment's
interpreter. The ``uv pip install`` resolves haute's published dependencies against
live PyPI with no lockfile, as a user's install does.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

_CHECK = Path(__file__).with_name("package_smoke_check.py")


def distributions(dist: Path) -> dict[str, Path]:
    """The one wheel and the one sdist in *dist*; anything else is refused, naming it."""
    found = {"wheel": sorted(dist.glob("*.whl")), "sdist": sorted(dist.glob("*.tar.gz"))}
    for kind, paths in found.items():
        if len(paths) != 1:
            names = [path.name for path in paths]
            raise SystemExit(f"{dist} must hold exactly one {kind}, and holds {names}")
    return {kind: paths[0] for kind, paths in found.items()}


def install_command(python: Path, distribution: Path) -> list[str]:
    """Install *distribution* into *python*'s environment as pip does: every module compiled."""
    return [
        "uv",
        "pip",
        "install",
        "--compile-bytecode",
        "--python",
        str(python),
        str(distribution),
    ]


def _executables(environment: Path) -> tuple[Path, Path]:
    """The environment's interpreter and its ``haute`` command."""
    if os.name == "nt":
        return environment / "Scripts" / "python.exe", environment / "Scripts" / "haute.exe"
    return environment / "bin" / "python", environment / "bin" / "haute"


def smoke(distribution: Path, environment: Path, python_version: str) -> None:
    """Install *distribution* into a new environment at *environment* and check it."""
    subprocess.run(["uv", "venv", str(environment), "--python", python_version], check=True)
    python, haute = _executables(environment)
    subprocess.run(install_command(python, distribution), check=True)
    subprocess.run([str(python), str(_CHECK)], check=True)
    subprocess.run([str(haute), "--help"], check=True, stdout=subprocess.DEVNULL)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dist", type=Path, required=True, help="folder holding one wheel and one sdist"
    )
    parser.add_argument(
        "--python",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
        help="interpreter version for the environments (default: this one's)",
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(
        prefix="haute-package-smoke-", ignore_cleanup_errors=True
    ) as scratch:
        for kind, distribution in distributions(args.dist).items():
            print(f"package smoke: {kind} {distribution.name}", flush=True)
            smoke(distribution, Path(scratch) / kind, args.python)
    print("package install smoke ok")


if __name__ == "__main__":
    main()
