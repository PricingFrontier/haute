"""Regression tests for bounded-memory sink routing."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


BOUNDED_WRITE_CALLERS: dict[Path, tuple[str, ...]] = {
    Path("src/haute/executor.py"): ("write_polars_output(",),
    Path("src/haute/_execute_lazy.py"): ("write_parts(",),
    # The chunked writer's bounded sink is the hashed one (bounded_hashed_sink).
    Path("src/haute/_chunked_writes.py"): ("bounded_sink(", "bounded_hashed_sink("),
    Path("src/haute/_source_cache.py"): ("write_parts(",),
    Path("src/haute/routes/_node_data_service.py"): ("write_parts(",),
    Path("src/haute/modelling/_training_job.py"): ("bounded_sink(",),
    Path("src/haute/routes/_optimiser_service.py"): ("bounded_sink(",),
    # Training's write goes through the single-file chunked writer, which slices
    # where it can and falls back to bounded_sink itself (covered by the entry
    # for _chunked_writes.py above).
    Path("src/haute/routes/_training_preparation.py"): ("write_file(",),
    Path("src/haute/_codegen_builders.py"): ("@pipeline.data_output(config=",),
}


@pytest.mark.parametrize(
    "relative_path, writers",
    BOUNDED_WRITE_CALLERS.items(),
    ids=[path.as_posix() for path in BOUNDED_WRITE_CALLERS],
)
def test_bounded_memory_writers_use_the_canonical_bounded_abstraction(
    relative_path: Path, writers: tuple[str, ...]
) -> None:
    """Critical batch paths must use a bounded writer/provider abstraction."""
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert any(writer in source for writer in writers), (
        f"{relative_path.as_posix()} does not contain any accepted writer spelling: {writers}"
    )
