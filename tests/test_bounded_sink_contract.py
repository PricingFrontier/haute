"""Regression tests for bounded-memory sink routing."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


BOUNDED_SINK_CALLERS = [
    Path("src/haute/executor.py"),
    Path("src/haute/_execute_lazy.py"),
    Path("src/haute/modelling/_training_job.py"),
    Path("src/haute/routes/_optimiser_service.py"),
    Path("src/haute/routes/_train_service.py"),
    Path("src/haute/_codegen_builders.py"),
]


@pytest.mark.parametrize("relative_path", BOUNDED_SINK_CALLERS)
def test_bounded_memory_callers_do_not_use_fallback_sink(relative_path: Path) -> None:
    """Critical batch paths should route to bounded_sink, not safe_sink."""
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "safe_sink(" not in source
    assert "bounded_sink(" in source


def test_removed_fallback_sink_symbols_and_call_sites_stay_absent() -> None:
    """The deprecated broad fallback helpers must not return."""
    offenders: list[str] = []
    for path in (ROOT / "src" / "haute").rglob("*.py"):
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        if "safe_sink" in source or "best_effort_sink" in source:
            offenders.append(str(relative).replace("\\", "/"))

    assert offenders == []
