"""The Polars settings every process Haute starts runs under.

Polars 2 spills an operation's state to disk once its own allocations pass 80%
of the machine's memory, into a directory outside Haute's storage, and an
out-of-disk abort fails every later query in the process. On Polars 2.0 a
spill budget bought no dependable saving in resident memory and cost several
times the time, and a worker capped below the host never reaches the default
threshold, so Haute keeps spilling off and owns the directory anyway: anything
that does spill lands in the project's runtime storage, where Haute removes a
finished process's files. The same settings load an Arrow extension column
Polars does not know as its storage type, which loses nothing.

Polars reads ``POLARS_UNKNOWN_EXTENSION_TYPE_BEHAVIOR`` only when it is first
imported, so the CLI applies these settings before anything imports Polars;
the spilling settings are reloaded if Polars is already loaded. A process
Haute does not own (a user's own ``Pipeline.run``) keeps its own settings.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path, PurePath
from typing import TypeVar

from haute._process_memory import process_is_alive

_PathT = TypeVar("_PathT", bound=PurePath)

POLARS_SPILL_DIRNAME = "polars-spill"
SPILL_DIR_ENV = "POLARS_OOC_SPILL_DIR"

# Spilling starts when Polars' own allocations pass the smaller of the MB value
# and the fraction times total memory, so only a fraction this large turns it
# off; the MB value alone does not, and an MB value of 0 spills everything.
_SPILL_OFF_FRACTION = "1000000"
_SPILL_OFF_MB = "1000000000"
# Should anything spill regardless, a small disk budget makes Polars abort the
# query instead of filling the disk.
_SPILL_DISK_BUDGET_MB = "1024"


def polars_spill_root(project_root: _PathT) -> _PathT:
    """Where Polars spills inside a project's runtime storage (one folder per process)."""
    return project_root / ".haute_cache" / POLARS_SPILL_DIRNAME


def polars_process_environment(spill_root: PurePath) -> dict[str, str]:
    """The environment that keeps spilling off and inside ``spill_root``."""
    return {
        "POLARS_OOC_MEMORY_BUDGET_FRACTION": _SPILL_OFF_FRACTION,
        "POLARS_OOC_MEMORY_BUDGET_MB": _SPILL_OFF_MB,
        "POLARS_OOC_DISK_BUDGET_MB": _SPILL_DISK_BUDGET_MB,
        SPILL_DIR_ENV: str(spill_root),
        "POLARS_UNKNOWN_EXTENSION_TYPE_BEHAVIOR": "load_as_storage",
    }


def apply_polars_process_environment(spill_root: Path) -> dict[str, str]:
    """Put Haute's Polars settings into this process's environment and return them.

    A child process inherits them. Applied before Polars is imported, every
    setting takes effect; applied after, Polars reloads the spilling settings,
    but keeps the extension behaviour it read at import.
    """
    environment = polars_process_environment(spill_root)
    if "polars" in sys.modules:
        import polars as pl

        pl.Config.load(json.dumps({"environment": environment}))
    else:
        os.environ.update(environment)
    return environment


def release_polars_spill_dir(pid: int) -> None:
    """Remove the spill folder Polars named after a process that has ended."""
    spill_root = os.environ.get(SPILL_DIR_ENV)
    if not spill_root:
        return
    folder = Path(spill_root) / str(pid)
    if folder.is_dir():
        shutil.rmtree(folder)


def reap_polars_spill_dirs(spill_root: Path) -> None:
    """Remove the spill folders of processes that are no longer running."""
    if not spill_root.is_dir():
        return
    for folder in spill_root.iterdir():
        if folder.is_dir() and folder.name.isdecimal() and not process_is_alive(int(folder.name)):
            shutil.rmtree(folder)
