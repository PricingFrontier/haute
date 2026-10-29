"""Fail the coverage gate when the test write-sandbox census grows.

Each coverage shard records every write its observe-mode tests made outside
their sandbox (tests/_write_sandbox.py) and uploads the record. This script
reads the shards' records, puts each write in a class by where it landed, and
compares the classes with the ``[census]`` table of tests/write_sandbox.toml:

* a class the table does not list fails: a new kind of escape;
* a listed class no shard recorded fails too, so the table only shrinks.

A class is a root the shard recorded and the first path component below it
(``repo:.haute_cache``, ``home:.cache``, ``runner-temp:_runner_file_commands``), or
``other:`` and the first two components of the path. pytest's assertion-rewrite
bytecode (``__pycache__/*.pyc.<pid>``) is the harness's write, not a test's: it
is reported but never gated, because which test happens to trigger it depends
on import order and worker scheduling, not on what any test does.

Run:  uv run python scripts/check_write_sandbox_census.py census-shard1.json ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RATCHET_PATH = REPO_ROOT / "tests" / "write_sandbox.toml"
CENSUS_VERSION = 1

_HARNESS = (
    ("pytest assertion-rewrite bytecode", re.compile(r"(?:^|/)__pycache__/[^/]+\.pyc\.\d+$")),
)
_TITLE = "Write-sandbox census"


class CensusError(Exception):
    """A census record or the ratchet table is missing or malformed."""


@dataclass
class ClassReport:
    """The writes one class holds and the tests that made them."""

    writes: int = 0
    tests: set[str] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)

    def add(self, path: str, nodeid: str) -> None:
        self.writes += 1
        self.tests.add(nodeid)
        if len(self.examples) < 3 and path not in self.examples:
            self.examples.append(path)


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def harness_kind(path: str) -> str | None:
    """The harness write *path* is, or ``None`` for a test's own write."""
    target = _posix(path)
    for kind, pattern in _HARNESS:
        if pattern.search(target):
            return kind
    return None


def classify(path: str, roots: Mapping[str, str]) -> str:
    """The class of one recorded write: the deepest root holding it and the first
    component below that root, else ``other:`` and its first two components."""
    target = _posix(path)
    best: tuple[int, str, str] | None = None
    for name, root in roots.items():
        prefix = _posix(root).rstrip("/")
        if target.startswith(prefix + "/") and (best is None or len(prefix) > best[0]):
            best = (len(prefix), name, target[len(prefix) + 1 :])
    if best is not None:
        _, name, below = best
        return f"{name}:{below.split('/', 1)[0]}"
    parts = [part for part in target.split("/") if part]
    return "other:/" + "/".join(parts[:2])


def load_listed(path: Path) -> dict[str, str]:
    """The ``[census]`` table: each listed class and the reason it still happens."""
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8")).get("census")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CensusError(f"{path}: unreadable ratchet table: {exc}") from exc
    if not isinstance(table, dict):
        raise CensusError(f"{path}: no [census] table")
    listed: dict[str, str] = {}
    for name, entry in table.items():
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(reason, str) or not reason.strip():
            raise CensusError(f"{path}: census class {name!r} gives no reason")
        listed[name] = reason
    return listed


def load_record(path: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """One shard's roots and its writes as ``(path, test)`` pairs."""
    if not path.is_file():
        raise CensusError(f"{path}: no census record; every coverage shard must upload one")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CensusError(f"{path}: unreadable census record: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != CENSUS_VERSION:
        raise CensusError(f"{path}: not a version-{CENSUS_VERSION} census record")
    roots, rows = data.get("roots"), data.get("writes")
    if not isinstance(roots, dict) or not all(isinstance(v, str) for v in roots.values()):
        raise CensusError(f"{path}: census record without its roots")
    if not isinstance(rows, list):
        raise CensusError(f"{path}: census record without its writes")
    writes: list[tuple[str, str]] = []
    for row in rows:
        written, nodeid = (row.get("path"), row.get("nodeid")) if isinstance(row, dict) else (0, 0)
        if not isinstance(written, str) or not isinstance(nodeid, str):
            raise CensusError(f"{path}: census write without its path and test: {row!r}")
        writes.append((written, nodeid))
    return roots, writes


def tally(
    records: Sequence[tuple[Mapping[str, str], Sequence[tuple[str, str]]]],
) -> tuple[dict[str, ClassReport], dict[str, ClassReport]]:
    """The gated classes, and the harness's writes by kind."""
    classes: dict[str, ClassReport] = {}
    harness: dict[str, ClassReport] = {}
    for roots, writes in records:
        for written, nodeid in writes:
            kind = harness_kind(written)
            if kind is None:
                classes.setdefault(classify(written, roots), ClassReport()).add(written, nodeid)
            else:
                harness.setdefault(kind, ClassReport()).add(written, nodeid)
    return classes, harness


def _escape(message: str) -> str:
    """A workflow command's message; `%`, CR and LF are reserved in its grammar."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def summary_markdown(
    classes: Mapping[str, ClassReport],
    harness: Mapping[str, ClassReport],
    listed: Mapping[str, str],
) -> str:
    lines = [
        "### Write-sandbox census",
        "",
        "| Class | Status | Writes | Tests | Example |",
        "|---|---|---:|---:|---|",
    ]
    for name in sorted(set(classes) | set(listed)):
        report = classes.get(name, ClassReport())
        status = (
            "**new**" if name not in listed else "listed" if report.writes else "**not recorded**"
        )
        example = f"`{report.examples[0]}`" if report.examples else ""
        lines.append(f"| `{name}` | {status} | {report.writes} | {len(report.tests)} | {example} |")
    for kind in sorted(harness):
        report = harness[kind]
        example = f"`{report.examples[0]}`" if report.examples else ""
        lines.append(f"| {kind} | harness | {report.writes} | {len(report.tests)} | {example} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ratchet", type=Path, default=RATCHET_PATH)
    parser.add_argument("census", nargs="+", type=Path, help="one record per coverage shard")
    args = parser.parse_args(argv)
    try:
        listed = load_listed(args.ratchet)
        classes, harness = tally([load_record(path) for path in args.census])
    except CensusError as exc:
        print(f"::error title={_TITLE}::{_escape(str(exc))}")
        return 1

    new = sorted(set(classes) - set(listed))
    stale = sorted(set(listed) - set(classes))
    for name in new:
        report = classes[name]
        print(
            f"::error title={_TITLE}::"
            + _escape(
                f"A new class of write outside the test sandbox: {name} "
                f"({report.writes} writes, e.g. {report.examples[0]} from "
                f"{sorted(report.tests)[0]}). Derive the test's path from haute_scratch, or "
                "list the class with its reason under [census] in tests/write_sandbox.toml."
            )
        )
    for name in stale:
        print(
            f"::error title={_TITLE}::"
            + _escape(
                f"No coverage shard recorded a write in {name}; delete it from [census] "
                "in tests/write_sandbox.toml so the census cannot grow back into it."
            )
        )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(summary_markdown(classes, harness, listed))
    if new or stale:
        return 1
    recorded = sum(report.writes for report in classes.values())
    print(
        f"Write-sandbox census holds: {recorded} writes in {len(classes)} listed classes, "
        f"{sum(report.writes for report in harness.values())} harness writes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
