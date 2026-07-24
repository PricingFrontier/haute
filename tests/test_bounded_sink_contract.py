"""Regression tests for bounded-memory sink routing."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


BOUNDED_WRITE_CALLERS = {
    Path("src/haute/executor.py"): "write_polars_output(",
    Path("src/haute/_execute_lazy.py"): "bounded_sink(",
    Path("src/haute/modelling/_training_job.py"): "bounded_sink(",
    Path("src/haute/routes/_optimiser_service.py"): "bounded_sink(",
    Path("src/haute/routes/_train_service.py"): "bounded_sink(",
    Path("src/haute/_codegen_builders.py"): "@pipeline.data_output(config=",
}


@pytest.mark.parametrize("relative_path, writer", BOUNDED_WRITE_CALLERS.items())
def test_bounded_memory_writers_use_the_canonical_bounded_abstraction(
    relative_path: Path, writer: str
) -> None:
    """Critical batch paths must use a bounded writer/provider abstraction."""
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "safe_sink(" not in source
    assert writer in source


def test_removed_fallback_sink_symbols_and_call_sites_stay_absent() -> None:
    """The deprecated broad fallback helpers must not return."""
    offenders: list[str] = []
    for path in (ROOT / "src" / "haute").rglob("*.py"):
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        if "safe_sink" in source or "best_effort_sink" in source:
            offenders.append(str(relative).replace("\\", "/"))

    assert offenders == []
