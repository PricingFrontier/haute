"""Name the browser tests a Playwright JSON report records as flaky.

A flaky test failed and then passed on a retry. CI retries browser tests, so on
a pull request a flaky test leaves the job green; this lists each one in the job
summary and as a warning annotation instead. On a push to main
``failOnFlakyTests`` (frontend/playwright.config.ts) makes the run itself red.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def flaky_tests(report: dict[str, Any]) -> list[str]:
    """Each flaky test's full title, its describe blocks first, with its project."""
    titles: list[str] = []

    def walk(suite: dict[str, Any], path: list[str]) -> None:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                if test.get("status") == "flaky":
                    title = " › ".join([*path, spec["title"]])
                    titles.append(f"{title} [{test.get('projectName', '')}]")
        for child in suite.get("suites", []):
            walk(child, [*path, child["title"]])

    suites = report.get("suites")
    if not isinstance(suites, list):
        raise ValueError("the report has no list of suites: is it Playwright's JSON report?")
    for suite in suites:
        walk(suite, [suite.get("title") or suite["file"]])
    return titles


def summary(titles: Sequence[str]) -> str:
    if not titles:
        # Flakiness only: a test that failed outright is the run's own red step.
        return "### Flaky browser tests\n\nNone: no browser test passed only on a retry.\n"
    lines = "".join(f"- {title}\n" for title in titles)
    return (
        "### Flaky browser tests\n\n"
        f"{len(titles)} browser test(s) passed only on a retry:\n\n{lines}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="Playwright JSON report")
    parser.add_argument("--summary", type=Path, required=True, help="Markdown file to append to")
    args = parser.parse_args(argv)
    if not args.report.is_file():
        print(f"No Playwright JSON report at {args.report}: the browser run did not finish.")
        return 1
    titles = flaky_tests(json.loads(args.report.read_text(encoding="utf-8")))
    for title in titles:
        print(f"::warning title=Flaky browser test::{title} passed only on a retry")
    with args.summary.open("a", encoding="utf-8") as handle:
        handle.write(summary(titles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
