"""Rewrite one lane's table in scripts/test_file_durations.json from JUnit reports.

The file holds one table per sharded lane, because coverage tracing slows some
modules far more than others. Each shard uploads a report written with
``-o junit_family=xunit1``, so every test case names its file: the coverage
shards as ``backend-junit-<K>``, the compatibility shards as
``backend-compat-junit-<python>-<K>``. Refresh both from one green run on main,
the compatibility table from one interpreter's shards (a test may appear once
per refresh):

    gh run download <run-id> --pattern "backend-junit-*" --dir .cache/junit/coverage
    uv run python scripts/refresh_test_durations.py --lane coverage .cache/junit/coverage
    gh run download <run-id> --pattern "backend-compat-junit-3.13-*" --dir .cache/junit/compat
    uv run python scripts/refresh_test_durations.py --lane compat .cache/junit/compat

A module's duration is the sum of its test cases' ``time`` (setup, call and
teardown), rounded to one decimal place. The other lanes' tables are kept. The
durations only balance the CI shards (tests/_ci_shards.py): a stale table can
unbalance them, but it never drops or duplicates a test.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

DURATIONS_VERSION = 2
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "test_file_durations.json"


class DurationsReportError(Exception):
    """Raised when the reports cannot yield a trustworthy durations table."""


def report_files(paths: Iterable[Path]) -> list[Path]:
    """Expand directories to the ``.xml`` files beneath them, in a stable order."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.xml")))
        else:
            files.append(path)
    return files


def module_durations(reports: Iterable[Path]) -> dict[str, float]:
    """Sum test-case times per module across the reports of one lane's shards."""
    totals: dict[str, float] = {}
    seen: set[tuple[str, str, str]] = set()
    for report in reports:
        try:
            root = ElementTree.parse(report).getroot()
        except (OSError, ElementTree.ParseError) as exc:
            raise DurationsReportError(f"Cannot read JUnit report {report}: {exc}") from exc
        for case in root.iter("testcase"):
            name = case.get("name", "")
            file = case.get("file")
            if not file:
                raise DurationsReportError(
                    f"{report}: test case {name!r} has no 'file' attribute; "
                    "write the reports with -o junit_family=xunit1"
                )
            module = file.replace("\\", "/")
            if not _is_module_path(module):
                raise DurationsReportError(
                    f"{report}: {file!r} is not a repository-relative path to a .py module"
                )
            identity = (module, case.get("classname", ""), name)
            if identity in seen:
                raise DurationsReportError(
                    f"{report}: test case {identity} appears twice; "
                    "pass each report of a single lane once"
                )
            seen.add(identity)
            try:
                seconds = float(case.get("time", ""))
            except ValueError as exc:
                raise DurationsReportError(
                    f"{report}: test case {identity} has no numeric 'time'"
                ) from exc
            if not math.isfinite(seconds) or seconds < 0:
                raise DurationsReportError(
                    f"{report}: test case {identity} has time {seconds}, not finite and >= 0"
                )
            totals[module] = totals.get(module, 0.0) + seconds
    if not totals:
        raise DurationsReportError("The reports contain no test cases")
    return totals


def existing_lanes(path: Path) -> dict[str, dict[str, float]]:
    """The lane tables already in *path*, or none when it does not exist yet."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DurationsReportError(f"Cannot read the durations file {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "lanes"}
        or payload["version"] != DURATIONS_VERSION
        or not isinstance(payload["lanes"], dict)
        or not all(isinstance(table, dict) for table in payload["lanes"].values())
    ):
        raise DurationsReportError(
            f"{path} is not a version-{DURATIONS_VERSION} durations file; "
            "refusing to overwrite the other lanes' tables"
        )
    lanes: dict[str, dict[str, float]] = payload["lanes"]
    return lanes


def render(lanes: dict[str, dict[str, float]]) -> str:
    tables = {
        lane: {module: round(seconds, 1) for module, seconds in sorted(durations.items())}
        for lane, durations in sorted(lanes.items())
    }
    return json.dumps({"version": DURATIONS_VERSION, "lanes": tables}, indent=2) + "\n"


def _is_module_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        path.as_posix() == value
        and not path.is_absolute()
        and ".." not in path.parts
        and path.suffix == ".py"
    )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "reports",
        nargs="+",
        type=Path,
        help="JUnit XML reports (xunit1 family), or directories holding them.",
    )
    parser.add_argument(
        "--lane",
        required=True,
        help="The lane whose table the reports refresh (coverage or compat).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Durations file to write (default: scripts/test_file_durations.json).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        lanes = existing_lanes(args.output)
        durations = module_durations(report_files(args.reports))
    except DurationsReportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    lanes[args.lane] = durations
    args.output.write_text(render(lanes), encoding="utf-8", newline="\n")
    print(
        f"Wrote {len(durations)} {args.lane} module durations "
        f"({sum(durations.values()):.0f}s) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
