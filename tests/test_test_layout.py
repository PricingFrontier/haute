"""Test modules are named for the behaviour they cover and stay small enough to read.

A module named after the coverage campaign or fix wave that produced it cannot be
found by the behaviour it tests, and a module of many thousands of lines cannot be
read as one. Both rules hold for the backend suite under ``tests/`` and the
frontend's Vitest and Playwright files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._source_files import REPO_ROOT, source_files

pytestmark = pytest.mark.meta

#: The most lines one test module may hold.
MAX_TEST_MODULE_LINES = 5_000

#: Words that name how a module came about rather than what it tests.
_CAMPAIGN_NAME = re.compile(
    r"(?:^|[._])(?:coverage|gaps|fixes|bugfixes|uplift|review|dry|w\d+)(?=[._]|$)",
    re.IGNORECASE,
)

#: Modules whose name says what they test, though it contains one of those words.
BEHAVIOUR_NAMES = {
    "tests/test_check_changed_coverage.py": "tests scripts/check_changed_coverage.py",
    "tests/test_check_critical_coverage.py": "tests scripts/check_critical_coverage.py",
    "tests/test_meta_marker_coverage.py": "every repository scan carries the meta marker",
    "tests/test_workflow_coverage.py": "tests the workflow coverage ledger",
    "tests/test_apiinput_flat_output_dry_run.py": "the OUTPUT dry run of a flat API Input",
}


def _backend_test_modules() -> list[Path]:
    return [
        path
        for path in source_files(REPO_ROOT / "tests")
        if path.name.startswith("test_") and path.suffix == ".py"
    ]


def _frontend_test_modules() -> list[Path]:
    roots = (REPO_ROOT / "frontend" / "src", REPO_ROOT / "frontend" / "e2e")
    return [
        path
        for root in roots
        for path in source_files(root, suffix=None)
        if re.search(r"\.(?:test|spec)\.tsx?$", path.name)
    ]


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _stem(path: Path) -> str:
    return re.sub(r"\.(?:test|spec)\.tsx?$|\.py$", "", path.name)


def test_no_test_module_is_named_after_a_campaign_or_fix_wave() -> None:
    named = sorted(
        _relative(path)
        for path in [*_backend_test_modules(), *_frontend_test_modules()]
        if _CAMPAIGN_NAME.search(_stem(path)) and _relative(path) not in BEHAVIOUR_NAMES
    )

    assert not named, (
        "Name each test module for the behaviour or component it covers, or fold it "
        f"into the module that owns that behaviour: {named}"
    )


def test_every_behaviour_name_exception_still_exists() -> None:
    missing = sorted(name for name in BEHAVIOUR_NAMES if not (REPO_ROOT / name).is_file())

    assert not missing, f"Remove these from BEHAVIOUR_NAMES: {missing}"


def test_no_test_module_exceeds_the_line_ceiling() -> None:
    oversized = sorted(
        f"{_relative(path)} ({count} lines)"
        for path in [*_backend_test_modules(), *_frontend_test_modules()]
        if (count := len(path.read_bytes().splitlines())) > MAX_TEST_MODULE_LINES
    )

    assert not oversized, (
        f"Split these by behaviour into modules of at most {MAX_TEST_MODULE_LINES} "
        f"lines: {oversized}"
    )


@pytest.mark.parametrize(
    ("name", "campaign"),
    [
        ("test_trace_w4_fixes.py", True),
        ("test_expression_parser_coverage.py", True),
        ("usePipelineAPI.gaps.test.ts", True),
        ("test_dry_refactors.py", True),
        ("test_coverage_gaps.py", True),
        ("test_trace_waterfall.py", False),
        ("test_rating_step.py", False),
        ("usePipelineAPI.previewLifecycle.test.ts", False),
        ("test_draw_window.py", False),
    ],
)
def test_the_campaign_rule_reads_names_as_intended(name: str, campaign: bool) -> None:
    assert bool(_CAMPAIGN_NAME.search(_stem(Path(name)))) is campaign
