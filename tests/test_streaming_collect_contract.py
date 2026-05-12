"""Regression tests for profiled streaming collect routing."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


BOUNDED_COLLECT_CALLERS = [
    Path("src/haute/_execute_lazy.py"),
    Path("src/haute/deploy/_scorer.py"),
    Path("src/haute/modelling/_training_job.py"),
    Path("src/haute/routes/_optimiser_service.py"),
    Path("src/haute/routes/optimiser.py"),
]

DIRECT_STREAMING_COLLECT = re.compile(
    r"\.collect\s*\([^)]*engine\s*=\s*['\"]streaming['\"]",
    re.DOTALL,
)

@pytest.mark.parametrize("relative_path", BOUNDED_COLLECT_CALLERS)
def test_bounded_callers_route_streaming_collect_through_helper(relative_path: Path) -> None:
    """Bounded backend paths should use streaming_collect, not bare Polars collect."""
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert DIRECT_STREAMING_COLLECT.search(source) is None
    assert "streaming_collect(" in source


def test_production_code_has_no_direct_streaming_collect_calls_outside_helper() -> None:
    """All production streaming collects route through the profiled helper."""
    offenders: list[str] = []
    for path in (ROOT / "src" / "haute").rglob("*.py"):
        relative = path.relative_to(ROOT)
        if relative == Path("src/haute/_polars_utils.py"):
            continue
        source = path.read_text(encoding="utf-8")
        if DIRECT_STREAMING_COLLECT.search(source):
            offenders.append(str(relative).replace("\\", "/"))

    assert offenders == []
