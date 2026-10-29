"""File sharding for the CI full-suite lanes: ``pytest tests/ --shard=K/N --shard-lane=L``.

A sharded lane partitions test *modules*. Each module belongs to exactly one
shard, and every other shard ignores it before it is imported, so the shards of
a lane together collect exactly what an unsharded run collects. The recorded
per-module durations in ``scripts/test_file_durations.json`` only balance the
shards, one table per lane (``--shard-lane`` names it), because coverage
tracing slows some modules far more than others: recorded modules that still
exist go longest-first onto the least-loaded shard, and any other module goes
to ``crc32(path) % N + 1``. A stale or incomplete table can unbalance the
shards, but it can never drop or duplicate a test.

``tests/conftest.py`` adds the options and registers :class:`ShardSelection`
in every pytest process, including each xdist worker, since workers are the
processes that collect.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

DURATIONS_VERSION = 2
DEFAULT_DURATIONS_PATH = PurePosixPath("scripts/test_file_durations.json")

_SHARD_PATTERN = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")


@dataclass(frozen=True)
class ShardSpec:
    """Shard ``index`` (1-based) of ``count``."""

    index: int
    count: int

    @classmethod
    def parse(cls, value: str) -> ShardSpec:
        match = _SHARD_PATTERN.fullmatch(value)
        if match is None or int(match.group(1)) > int(match.group(2)):
            raise argparse.ArgumentTypeError(f"expected K/N with 1 <= K <= N, got {value!r}")
        return cls(int(match.group(1)), int(match.group(2)))


def load_durations(path: Path, lane: str) -> dict[str, float]:
    """Read *lane*'s table from a version-2 durations file, rejecting anything malformed."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read test durations from {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "lanes"}
        or payload["version"] != DURATIONS_VERSION
        or not isinstance(payload["lanes"], dict)
        or not all(isinstance(table, dict) for table in payload["lanes"].values())
    ):
        raise ValueError(
            f"{path} must be a JSON object with exactly 'version' ({DURATIONS_VERSION}) "
            "and 'lanes' (lane -> module path -> seconds)"
        )
    if lane not in payload["lanes"]:
        raise ValueError(
            f"{path} has no durations for lane {lane!r}; it records {sorted(payload['lanes'])}"
        )
    durations: dict[str, float] = {}
    for module, seconds in payload["lanes"][lane].items():
        if not _is_module_path(module):
            raise ValueError(
                f"{path}: {module!r} is not a repository-relative POSIX path to a .py module"
            )
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int | float)
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError(f"{path}: duration for {module} must be finite and >= 0")
        durations[module] = float(seconds)
    return durations


def assign_modules(durations: Mapping[str, float], count: int) -> dict[str, int]:
    """Place each module longest-first on the least-loaded shard (ties: path, lower shard)."""
    loads = [0.0] * count
    assignment: dict[str, int] = {}
    for module, seconds in sorted(durations.items(), key=lambda item: (-item[1], item[0])):
        shard = min(range(count), key=lambda candidate: (loads[candidate], candidate))
        loads[shard] += seconds
        assignment[module] = shard + 1
    return assignment


def unrecorded_shard(module: str, count: int) -> int:
    """Stable shard for a module without a recorded duration."""
    return zlib.crc32(module.encode("utf-8")) % count + 1


class ShardSelection:
    """Pytest plugin that ignores every test module assigned to another shard."""

    def __init__(
        self,
        spec: ShardSpec,
        durations: Mapping[str, float],
        rootpath: Path,
        python_files: Sequence[str],
    ) -> None:
        separated = [pattern for pattern in python_files if "/" in pattern or "\\" in pattern]
        if separated:
            raise pytest.UsageError(
                f"--shard matches python_files by file name; unsupported patterns: {separated}"
            )
        present = {module: s for module, s in durations.items() if (rootpath / module).is_file()}
        self.spec = spec
        self._rootpath = rootpath
        self._patterns = tuple(python_files)
        self._assignment = assign_modules(present, spec.count)
        mine = [module for module, shard in self._assignment.items() if shard == spec.index]
        self._recorded_modules = len(mine)
        self._recorded_seconds = sum(present[module] for module in mine)

    def shard_of(self, module: str) -> int:
        """The 1-based shard a repository-relative POSIX module path belongs to."""
        return self._assignment.get(module) or unrecorded_shard(module, self.spec.count)

    def pytest_ignore_collect(self, collection_path: Path) -> bool | None:
        if collection_path.suffix != ".py" or not any(
            fnmatch.fnmatch(collection_path.name, pattern) for pattern in self._patterns
        ):
            return None
        try:
            module = collection_path.relative_to(self._rootpath).as_posix()
        except ValueError as exc:
            raise pytest.UsageError(
                f"--shard cannot place {collection_path}: it is outside the rootdir "
                f"{self._rootpath}"
            ) from exc
        # None, not False: a module in this shard stays subject to every other ignore rule.
        return None if self.shard_of(module) == self.spec.index else True

    def pytest_report_header(self) -> str:
        return (
            f"shard {self.spec.index}/{self.spec.count}: {self._recorded_modules} recorded "
            f"test modules ({self._recorded_seconds:.0f}s recorded), plus any unrecorded "
            "module that hashes here"
        )


def add_options(parser: pytest.Parser) -> None:
    group = parser.getgroup("haute-ci-shards", "CI file sharding (tests/_ci_shards.py)")
    group.addoption(
        "--shard",
        type=ShardSpec.parse,
        default=None,
        metavar="K/N",
        help="Collect only the test modules assigned to shard K of N.",
    )
    group.addoption(
        "--shard-lane",
        default=None,
        metavar="LANE",
        help="The lane whose recorded durations balance the shards (coverage or compat).",
    )
    group.addoption(
        "--shard-durations",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"Per-module durations that balance the shards (default: {DEFAULT_DURATIONS_PATH}).",
    )


def register(config: pytest.Config) -> None:
    """Register the selection for ``--shard``; a no-op for unsharded runs."""
    spec: ShardSpec | None = config.getoption("shard")
    lane: str | None = config.getoption("shard_lane")
    durations_option: Path | None = config.getoption("shard_durations")
    if spec is None:
        for option, value in (("--shard-lane", lane), ("--shard-durations", durations_option)):
            if value is not None:
                raise pytest.UsageError(f"{option} requires --shard")
        return
    if lane is None:
        raise pytest.UsageError("--shard requires --shard-lane, the durations table to balance by")
    if durations_option is None:
        durations_path = config.rootpath / DEFAULT_DURATIONS_PATH
    else:
        durations_path = config.invocation_params.dir / durations_option
    try:
        durations = load_durations(durations_path, lane)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    selection = ShardSelection(spec, durations, config.rootpath, config.getini("python_files"))
    config.pluginmanager.register(selection, "haute-ci-shard")


def _is_module_path(value: object) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        path.as_posix() == value
        and not path.is_absolute()
        and ".." not in path.parts
        and path.suffix == ".py"
    )
