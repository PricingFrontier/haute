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

    assert writer in source
