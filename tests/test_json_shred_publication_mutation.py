"""Small mutation witnesses for parallel JSON-shred assembly and inference."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._json_shred import (
    _inference,
    _shred,
    _writer,
)


def _config(*, columns: tuple[str, ...] = ("id",)) -> dict[str, Any]:
    return {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": name,
                        "path": f"$[:].{name}",
                        "type": "int",
                        "selected": True,
                        "levels": None,
                    }
                    for name in columns
                ],
            }
        ]
    }


def _install_static_process_pool(
    monkeypatch: pytest.MonkeyPatch,
    results: list[Any],
    shutdowns: list[tuple[bool, bool]],
) -> None:
    class StaticPool:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def map(self, _function: object, tasks: object):
            assert len(list(tasks)) == len(results)
            return iter(results)

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            shutdowns.append((wait, cancel_futures))

    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", StaticPool)


def test_parallel_assembly_rejects_an_underreported_part_row_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    specs = _shred._emitting_table_specs(config)
    part = tmp_path / "part.parquet"
    pl.DataFrame({"id": [1]}).write_parquet(part)
    result = _writer._ChunkResult(
        index=0,
        record_count=1,
        skipped_records=0,
        skipped_rows_by_table={},
        row_counts={},
        part_paths={"root": str(part)},
    )
    _install_static_process_pool(monkeypatch, [result], [])
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(RuntimeError, match="row-count mismatch"):
        _writer._write_tables_in_parallel(
            tmp_path / "source.jsonl", config, specs, staging, [(0, 1)]
        )


def test_parallel_assembly_tolerates_consumed_part_disappearance_and_logs_elapsed_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyarrow.parquet as pq

    config = _config()
    specs = _shred._emitting_table_specs(config)
    part = tmp_path / "part.parquet"
    pl.DataFrame({"id": [1]}).write_parquet(part)
    result = _writer._ChunkResult(
        index=0,
        record_count=1,
        skipped_records=0,
        skipped_rows_by_table={},
        row_counts={"root": 1},
        part_paths={"root": str(part)},
    )
    shutdowns: list[tuple[bool, bool]] = []
    _install_static_process_pool(monkeypatch, [result], shutdowns)
    original_parquet_file = pq.ParquetFile

    class VanishingPart:
        def __init__(self) -> None:
            self.inner = original_parquet_file(part)
            self.num_row_groups = self.inner.num_row_groups

        def __enter__(self) -> VanishingPart:
            return self

        def __exit__(self, *_args: object) -> None:
            self.inner.close()
            part.unlink()

        def read_row_group(self, index: int) -> Any:
            return self.inner.read_row_group(index)

    monkeypatch.setattr(
        pq,
        "ParquetFile",
        lambda path: VanishingPart() if Path(path) == part else original_parquet_file(path),
    )
    clock = iter((10.0, 12.0))
    monkeypatch.setattr(time, "perf_counter", lambda: next(clock))
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        _writer.logger, "info", lambda event, **fields: events.append((event, fields))
    )
    staging = tmp_path / "staging"
    staging.mkdir()

    _writer._write_tables_in_parallel(tmp_path / "source.jsonl", config, specs, staging, [(0, 1)])

    assert pl.read_parquet(staging / "root.parquet")["id"].to_list() == [1]
    assert shutdowns == [(True, True)]
    complete = next(fields for event, fields in events if event == "json_shred_parallel_complete")
    assert complete["duration_seconds"] == 2.0


def test_parallel_inference_merges_in_order_shuts_down_and_logs_elapsed_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix_record = {"prefix": True}
    first_record = {"value": 1, "first": True}
    second_record = {"value": 2.5, "second": True}
    lines = [
        (json.dumps(record) + "\n").encode()
        for record in (prefix_record, first_record, second_record)
    ]
    source = tmp_path / "source.jsonl"
    source.write_bytes(b"".join(lines))
    boundary = len(lines[0]) + len(lines[1])
    ranges = [(0, boundary), (boundary, source.stat().st_size)]
    monkeypatch.setattr(_inference._inference_filter, "_INITIAL_SAMPLE_RECORDS", 1)
    first, second = _inference._InferenceState(), _inference._InferenceState()
    first.walk(first_record)
    second.walk(second_record)
    results = [
        _inference._InferenceChunkResult(index=0, state=first),
        _inference._InferenceChunkResult(index=1, state=second),
    ]
    shutdowns: list[tuple[bool, bool]] = []
    _install_static_process_pool(monkeypatch, results, shutdowns)
    clock = iter((20.0, 23.0))
    monkeypatch.setattr(time, "perf_counter", lambda: next(clock))
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        _inference.logger, "info", lambda event, **fields: events.append((event, fields))
    )

    merged = _inference._infer_jsonl_in_parallel(source, ranges)

    expected = _inference._InferenceState()
    expected.walk(prefix_record)
    expected.walk(first_record)
    expected.walk(second_record)
    assert _inference._assemble_inference_schema(merged) == _inference._assemble_inference_schema(
        expected
    )
    assert shutdowns == [(True, True)]
    complete = next(
        fields for event, fields in events if event == "json_schema_infer_parallel_complete"
    )
    assert complete["duration_seconds"] == 3.0


def test_parallel_inference_rejects_a_result_with_neither_state_nor_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text('{"id": 1}\n', encoding="utf-8")
    empty = _inference._InferenceChunkResult(index=0)
    shutdowns: list[tuple[bool, bool]] = []
    _install_static_process_pool(monkeypatch, [empty], shutdowns)

    with pytest.raises(RuntimeError, match="chunk 0 returned no state"):
        _inference._infer_jsonl_in_parallel(source, [(0, 1)])

    assert shutdowns == [(True, True)]
