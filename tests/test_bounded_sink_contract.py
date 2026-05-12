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


def test_production_code_does_not_call_fallback_sink_outside_helper() -> None:
    """No production path should reintroduce safe_sink as a broad fallback."""
    offenders: list[str] = []
    for path in (ROOT / "src" / "haute").rglob("*.py"):
        relative = path.relative_to(ROOT)
        if relative == Path("src/haute/_polars_utils.py"):
            continue
        if "safe_sink(" in path.read_text(encoding="utf-8"):
            offenders.append(str(relative).replace("\\", "/"))

    assert offenders == []
