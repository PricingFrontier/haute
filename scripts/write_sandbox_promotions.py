"""Name the test modules that can join the write sandbox's strict set.

The monthly write-sandbox survey (.github/workflows/write-sandbox-survey.yml)
runs the suite with the sandbox forced strict and hands this script its JUnit
report. A module can join ``strict`` in tests/write_sandbox.toml when every
test of it that ran passed, at least one ran, and the layer-2 lint allows none
of its sites. The script lists those modules and fails while any remain, so the
lane stays red until a pass moves them. A module that passes strict but still
has allowlisted lint sites is reported, not counted: its flagged writes convert
first.

Run:  uv run python scripts/write_sandbox_promotions.py strict-survey.xml
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
RATCHET_PATH = REPO_ROOT / "tests" / "write_sandbox.toml"


class SurveyError(Exception):
    """A survey report or the ratchet table that cannot be read."""


@dataclass
class ModuleResult:
    ran: int = 0
    failed: int = 0


def _module(case: ElementTree.Element) -> str | None:
    """The test file a JUnit test case came from, by file name."""
    file = case.get("file")
    if file:
        return PurePosixPath(file.replace("\\", "/")).name
    parts = (case.get("classname") or "").split(".")
    if len(parts) >= 2 and parts[0] == "tests":
        return f"{parts[1]}.py"
    return None


def module_results(reports: Iterable[Path]) -> dict[str, ModuleResult]:
    results: dict[str, ModuleResult] = {}
    for report in reports:
        try:
            root = ElementTree.parse(report).getroot()
        except (OSError, ElementTree.ParseError) as exc:
            raise SurveyError(f"{report}: unreadable JUnit report: {exc}") from exc
        for case in root.iter("testcase"):
            module = _module(case)
            if module is None:
                continue
            result = results.setdefault(module, ModuleResult())
            if case.find("skipped") is not None:
                continue
            result.ran += 1
            if case.find("failure") is not None or case.find("error") is not None:
                result.failed += 1
    if not results:
        raise SurveyError("the survey reports hold no test cases")
    return results


def promotions(
    results: Mapping[str, ModuleResult], strict: Iterable[str], lint_files: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Modules that can join the strict set, and those that pass strict but
    still carry allowlisted lint sites."""
    already = set(strict)
    allowlisted = {PurePosixPath(rel).name for rel in lint_files}
    passing = sorted(
        module
        for module, result in results.items()
        if result.ran and not result.failed and module not in already
    )
    return (
        [module for module in passing if module not in allowlisted],
        [module for module in passing if module in allowlisted],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ratchet", type=Path, default=RATCHET_PATH)
    parser.add_argument("reports", nargs="+", type=Path, help="the strict survey's JUnit reports")
    args = parser.parse_args(argv)
    try:
        ratchet = tomllib.loads(args.ratchet.read_text(encoding="utf-8"))
        results = module_results(args.reports)
    except (OSError, tomllib.TOMLDecodeError, SurveyError) as exc:
        print(f"::error title=Write-sandbox survey::{exc}")
        return 2

    ready, blocked = promotions(results, ratchet["strict"], ratchet["lint"])
    lines = [f'    "{module}",' for module in ready]
    report = [
        f"{len(ready)} module(s) pass whole under the strict write sandbox and can join "
        "`strict` in tests/write_sandbox.toml:",
        *lines,
    ]
    if blocked:
        report.append(
            "Passing strict, but the lint still allowlists sites in them (convert those "
            f"writes first): {', '.join(blocked)}"
        )
    print("\n".join(report))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Write-sandbox survey\n\n```\n" + "\n".join(report) + "\n```\n")
    return 1 if ready else 0


if __name__ == "__main__":
    sys.exit(main())
