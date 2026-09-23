"""``haute gpu-setup`` command.

Haute installs ``xgboost-cpu``; XGBoost GPU training needs the full
``xgboost`` build instead. The two distributions install the same ``xgboost``
package, so only one may be present: this command swaps one for the other at
the installed version, in the interpreter running Haute, and verifies the
result in a fresh process.

Split into:

* :class:`GpuSetupConfig` — the typed bag of CLI inputs.
* :func:`handle_gpu_setup` — the function that does the work.
* :func:`gpu_setup` — the thin ``@click.command`` entry point.

This module is the subprocess chokepoint for the package installer (``uv pip``
or ``pip``) and the fresh-interpreter build check; ``nvidia-smi`` stays in
:mod:`haute._host_memory`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

import click

from haute._host_memory import nvidia_gpu_name

CPU_DISTRIBUTION = "xgboost-cpu"
GPU_DISTRIBUTION = "xgboost"

Runner = Callable[[Sequence[str]], int]


@dataclass
class GpuSetupConfig:
    """Parsed inputs for the ``haute gpu-setup`` command."""

    check_only: bool
    to_cpu: bool
    assume_yes: bool


def installed_xgboost() -> tuple[str, str] | None:
    """The installed XGBoost distribution and its version, if exactly one is present."""
    found = []
    for distribution in (CPU_DISTRIBUTION, GPU_DISTRIBUTION):
        try:
            found.append((distribution, version(distribution)))
        except PackageNotFoundError:
            continue
    return found[0] if len(found) == 1 else None


def fresh_cuda_build() -> bool | None:
    """Whether a fresh interpreter's XGBoost has CUDA (``None`` if it cannot import)."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import xgboost; print(bool(xgboost.build_info().get('USE_CUDA')))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip().splitlines()[-1] == "True"


def installer_commands(remove: str, add: str) -> list[list[str]]:
    """Uninstall *remove* then install *add* into this interpreter.

    ``uv pip`` is used when ``uv`` is on the PATH (Haute projects are uv
    projects and their venvs may have no ``pip``); otherwise ``python -m pip``.
    Uninstalling first matters: both distributions own the same files.
    """
    uv = shutil.which("uv")
    if uv is not None:
        return [
            [uv, "pip", "uninstall", "--python", sys.executable, remove],
            [uv, "pip", "install", "--python", sys.executable, add],
        ]
    return [
        [sys.executable, "-m", "pip", "uninstall", "-y", remove],
        [sys.executable, "-m", "pip", "install", add],
    ]


def _run(command: Sequence[str]) -> int:
    return subprocess.run(list(command), check=False).returncode


def handle_gpu_setup(config: GpuSetupConfig, runner: Runner = _run) -> None:
    """Report, or swap, the XGBoost build this Haute installation uses."""
    installed = installed_xgboost()
    if installed is None:
        click.echo(
            "Error: expected exactly one of xgboost-cpu or xgboost to be installed; "
            "reinstall Haute (for example `uv sync`) and retry.",
            err=True,
        )
        raise SystemExit(1)
    distribution, installed_version = installed
    gpu_name = nvidia_gpu_name()
    click.echo(f"XGBoost: {distribution} {installed_version}")
    click.echo(f"NVIDIA GPU: {gpu_name or 'not found'}")

    if config.check_only:
        from haute.modelling._gpu import xgboost_gpu_status

        status = xgboost_gpu_status()
        click.echo(f"GPU training: {'available' if status.available else 'unavailable'}")
        click.echo(status.detail)
        return

    if sys.platform == "darwin":
        # macOS has no xgboost-cpu wheel and no CUDA build: Haute installs the
        # standard xgboost package there, so there is nothing to switch.
        click.echo(
            "Error: XGBoost has no CUDA build for macOS; GPU training needs an NVIDIA "
            "GPU on Linux or Windows.",
            err=True,
        )
        raise SystemExit(1)
    target = CPU_DISTRIBUTION if config.to_cpu else GPU_DISTRIBUTION
    if distribution == target:
        click.echo(f"{target} is already installed; nothing to change.")
        return
    if not config.to_cpu:
        if gpu_name is None:
            click.echo(
                "Error: no NVIDIA GPU was found (nvidia-smi is missing or reports none); "
                "install the NVIDIA driver first.",
                err=True,
            )
            raise SystemExit(1)

    requirement = f"{target}=={installed_version}"
    commands = installer_commands(distribution, requirement)
    click.echo(f"This replaces {distribution} with {requirement} in {sys.executable}:")
    for command in commands:
        click.echo("  " + " ".join(command))
    if not config.assume_yes and not click.confirm("Continue?", default=True):
        raise SystemExit(1)
    # If the install step fails after the uninstall, no XGBoost is left; this
    # reinstalls the build that was there before.
    restore = installer_commands(target, f"{distribution}=={installed_version}")[1]
    for command in commands:
        if runner(command) != 0:
            click.echo(
                f"Error: `{' '.join(command)}` failed. Restore the previous build with "
                f"`{' '.join(restore)}`.",
                err=True,
            )
            raise SystemExit(1)

    cuda = fresh_cuda_build()
    if cuda is None:
        click.echo("Error: XGBoost no longer imports after the change.", err=True)
        raise SystemExit(1)
    if cuda != (target == GPU_DISTRIBUTION):
        click.echo(
            f"Error: the installed XGBoost {'has' if cuda else 'lacks'} CUDA support, "
            "which is not what was requested.",
            err=True,
        )
        raise SystemExit(1)
    if target == GPU_DISTRIBUTION:
        click.echo(
            "XGBoost's CUDA build is installed. Restart `haute serve`, then turn on "
            "GPU training in an XGBoost node's Train pane. Re-syncing the project "
            "(`uv sync`) restores xgboost-cpu; run `haute gpu-setup` again afterwards."
        )
    else:
        click.echo("The default xgboost-cpu build is installed. Restart `haute serve`.")


@click.command("gpu-setup")
@click.option("--check", "check_only", is_flag=True, help="Report GPU readiness only.")
@click.option("--cpu", "to_cpu", is_flag=True, help="Switch back to the default xgboost-cpu.")
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Do not ask for confirmation.")
def gpu_setup(check_only: bool, to_cpu: bool, assume_yes: bool) -> None:
    """Install XGBoost's CUDA build so XGBoost nodes can train on an NVIDIA GPU."""
    handle_gpu_setup(GpuSetupConfig(check_only=check_only, to_cpu=to_cpu, assume_yes=assume_yes))
