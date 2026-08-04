"""Parallel shred: byte-range splitting and serial-equivalence.

The parallel path exists only to go faster, so its contract is that it is
INDISTINGUISHABLE from the serial path — same rows, same order, same skip
accounting, same manifest, same failures. These tests force it on with tiny
thresholds (real files large enough to trigger it naturally would be far too
slow to keep in the suite) and compare the two paths directly.

The thresholds are read in the PARENT only — ranges are computed there and
passed to workers as arguments — so monkeypatching them reaches the whole
mechanism.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute import _json_shred
from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    _jsonl_byte_ranges,
    _raise_chunk_error,
    _shred_chunk,
    build_per_port_cache,
    infer_v2_schema_from_data,
    load_per_port_cache,
)


def _write_jsonl(path: Path, records: list[Any]) -> Path:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


def _force_parallel(monkeypatch: pytest.MonkeyPatch, chunk_bytes: int = 200) -> None:
    monkeypatch.setattr(_json_shred, "_PARALLEL_MIN_BYTES", 1)
    monkeypatch.setattr(_json_shred, "_PARALLEL_CHUNK_BYTES", chunk_bytes)


def _records(n: int) -> list[dict[str, Any]]:
    """Nested, ragged records: root scalars, a nullable 1-1 object, a child
    array of varying length, and a scalar array — so the comparison covers
    ancestor distribution and child tables, not just flat columns."""
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "id": i,
                "premium": i * 1.5,
                "addr": None if i % 3 == 0 else {"city": f"city{i}", "postcode": f"P{i}"},
                "claims": [{"amt": i * 10 + j} for j in range(i % 4)],
                "tags": [f"t{i}", "shared"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Byte-range splitting
# ---------------------------------------------------------------------------


def test_byte_ranges_tile_the_file_exactly(tmp_path: Path) -> None:
    """Ranges must be contiguous, gapless and cover every byte — anything else
    silently loses or duplicates records."""
    p = _write_jsonl(tmp_path / "d.jsonl", _records(200))
    size = p.stat().st_size
    ranges = _jsonl_byte_ranges(p, 256)

    assert len(ranges) > 1, "test data must actually split"
    assert ranges[0][0] == 0
    assert ranges[-1][1] == size
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
        assert prev_end == next_start
    assert all(start < end for start, end in ranges)


def test_byte_ranges_never_split_a_record(tmp_path: Path) -> None:
    """Every boundary must land immediately after a newline, so each range
    holds only whole lines."""
    p = _write_jsonl(tmp_path / "d.jsonl", _records(200))
    raw = p.read_bytes()
    for start, _end in _jsonl_byte_ranges(p, 256)[1:]:
        assert raw[start - 1 : start] == b"\n"


def test_byte_ranges_read_back_every_record_in_order(tmp_path: Path) -> None:
    p = _write_jsonl(tmp_path / "d.jsonl", _records(200))
    raw = p.read_bytes()
    seen = [
        json.loads(line)
        for start, end in _jsonl_byte_ranges(p, 256)
        for line in raw[start:end].splitlines()
        if line.strip()
    ]
    assert seen == _records(200)


def test_byte_ranges_of_small_or_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert _jsonl_byte_ranges(empty, 256) == []

    small = _write_jsonl(tmp_path / "small.jsonl", _records(2))
    assert _jsonl_byte_ranges(small, 1 << 20) == [(0, small.stat().st_size)]


# ---------------------------------------------------------------------------
# Parallel inference — complete-schema equivalence
# ---------------------------------------------------------------------------


def test_parallel_inference_matches_serial_with_late_schema_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every range contributes to one deterministic, complete schema.

    The fields and widening below first occur well beyond an early head sample;
    equality of the entire payload also pins first-observation column ordering.
    """
    records: list[dict[str, Any]] = [
        {
            "id": i,
            "premium": i,
            "profile": {"name": f"driver-{i}"},
            "tags": ["base"],
        }
        for i in range(300)
    ]
    records[25]["first_null_only"] = None
    records[125]["late_flag"] = True
    records[190]["premium"] = 190.5
    records[250]["policy"] = {"events": [{"code": "renewal"}]}
    records[275]["second_null_only"] = None
    src = _write_jsonl(tmp_path / "late.jsonl", [*records, 42])
    serial = infer_v2_schema_from_data(src)

    _force_parallel(monkeypatch, chunk_bytes=400)

    def reject_serial_dispatch(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("large JSONL inference unexpectedly used the serial iterator")

    monkeypatch.setattr(_json_shred, "_iter_records_for_inference", reject_serial_dispatch)

    assert infer_v2_schema_from_data(src) == serial


def test_explicitly_sampled_inference_stays_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parallel dispatch must never turn an explicit bound into a full scan."""
    src = _write_jsonl(tmp_path / "sampled.jsonl", _records(50))
    _force_parallel(monkeypatch, chunk_bytes=100)

    def reject_range_scan(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("bounded inference unexpectedly partitioned the whole file")

    monkeypatch.setattr(_json_shred, "_jsonl_byte_ranges", reject_range_scan)

    schema = infer_v2_schema_from_data(src, sample_size=2)

    assert schema["tables"]


def test_parallel_inference_preserves_late_schema_error_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late invalid key remains the exact same public contract failure."""
    records = [{"id": i} for i in range(300)]
    records[250]["not.addressable"] = "late"
    src = _write_jsonl(tmp_path / "bad-key.jsonl", records)

    with pytest.raises(ApiInputSchemaError) as serial_exc:
        infer_v2_schema_from_data(src)

    _force_parallel(monkeypatch, chunk_bytes=300)

    def reject_serial_dispatch(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("large JSONL inference unexpectedly used the serial iterator")

    monkeypatch.setattr(_json_shred, "_iter_records_for_inference", reject_serial_dispatch)
    with pytest.raises(ApiInputSchemaError) as parallel_exc:
        infer_v2_schema_from_data(src)

    assert parallel_exc.value.message == serial_exc.value.message
    assert parallel_exc.value.context == serial_exc.value.context
    assert str(parallel_exc.value) == str(serial_exc.value)


def test_parallel_inference_preserves_late_json_error_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed tail data reports the same parser detail as a serial scan."""
    src = tmp_path / "bad-tail.jsonl"
    src.write_text(
        "\n".join([*(json.dumps({"id": i}) for i in range(300)), "{bad"]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(orjson.JSONDecodeError) as serial_exc:
        infer_v2_schema_from_data(src)

    _force_parallel(monkeypatch, chunk_bytes=300)

    def reject_serial_dispatch(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("large JSONL inference unexpectedly used the serial iterator")

    monkeypatch.setattr(_json_shred, "_iter_records_for_inference", reject_serial_dispatch)
    with pytest.raises(orjson.JSONDecodeError) as parallel_exc:
        infer_v2_schema_from_data(src)

    assert parallel_exc.value.msg == serial_exc.value.msg
    assert parallel_exc.value.doc == serial_exc.value.doc
    assert parallel_exc.value.pos == serial_exc.value.pos
    assert str(parallel_exc.value) == str(serial_exc.value)


def test_parallel_inference_rejects_a_source_changed_during_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ranges from different source generations must never be merged."""
    src = _write_jsonl(tmp_path / "changing.jsonl", _records(100))
    _force_parallel(monkeypatch, chunk_bytes=100)

    def mutate_source(
        data_path: Path, _ranges: list[tuple[int, int]]
    ) -> _json_shred._InferenceState:
        with data_path.open("ab") as output:
            output.write(b'{"late": true}\n')
        return _json_shred._InferenceState()

    monkeypatch.setattr(_json_shred, "_infer_jsonl_in_parallel", mutate_source)

    with pytest.raises(ApiInputSchemaError) as excinfo:
        infer_v2_schema_from_data(src)

    assert "changed while its schema was inferred" in excinfo.value.message
    assert excinfo.value.context == {"path": str(src)}


def test_parallel_inference_runs_off_the_main_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The HTTP route starts inference inside Starlette's worker thread."""
    from concurrent.futures import ThreadPoolExecutor

    src = _write_jsonl(tmp_path / "threaded.jsonl", _records(300))
    _force_parallel(monkeypatch, chunk_bytes=300)

    with ThreadPoolExecutor(max_workers=1) as thread_pool:
        schema = thread_pool.submit(infer_v2_schema_from_data, src).result()

    assert schema["tables"]


# ---------------------------------------------------------------------------
# Serial equivalence — the whole contract
# ---------------------------------------------------------------------------


def _build(path: Path, cache: Path) -> tuple[dict[str, Any], dict[str, pl.DataFrame]]:
    schema = infer_v2_schema_from_data(path)
    for table in schema["tables"]:
        table["emit"] = True
    summary = build_per_port_cache(path, schema, cache)
    frames = {label: lf.collect() for label, lf in load_per_port_cache(cache, schema).items()}
    return summary, frames


def test_parallel_build_matches_serial_build_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same rows, same ORDER, same manifest. Row order matters: parts are
    concatenated in chunk order, and a pool that returned out of order would
    scramble it while keeping every count identical."""
    records = _records(300)
    serial_src = _write_jsonl(tmp_path / "serial.jsonl", records)
    serial_summary, serial_frames = _build(serial_src, tmp_path / "serial_cache")

    _force_parallel(monkeypatch)
    parallel_src = _write_jsonl(tmp_path / "parallel.jsonl", records)
    parallel_summary, parallel_frames = _build(parallel_src, tmp_path / "parallel_cache")

    assert set(parallel_frames) == set(serial_frames)
    for label, serial_frame in serial_frames.items():
        assert parallel_frames[label].equals(serial_frame), f"{label} differs"

    assert parallel_summary["skipped"] == serial_summary["skipped"]
    serial_tables = {t["label"]: t for t in serial_summary["tables"]}
    for entry in parallel_summary["tables"]:
        counterpart = serial_tables[entry["label"]]
        assert entry["row_count"] == counterpart["row_count"]
        assert entry["column_count"] == counterpart["column_count"]
        assert entry["columns"] == counterpart["columns"]


def test_parallel_build_counts_skipped_records_like_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-object lines are skipped records; the count must survive being
    summed across chunks rather than lost with the worker that saw them."""
    lines = []
    for i in range(120):
        lines.append(json.dumps({"id": i, "addr": None, "claims": [], "tags": []}))
        lines.append("42")  # a valid JSON scalar — a skipped record, not an error
    src = tmp_path / "mixed.jsonl"
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")

    serial_summary, serial_frames = _build(src, tmp_path / "serial_cache")
    assert serial_summary["skipped"]["records"] == 120

    _force_parallel(monkeypatch)
    parallel_summary, parallel_frames = _build(src, tmp_path / "parallel_cache")

    assert parallel_summary["skipped"] == serial_summary["skipped"]
    for label, serial_frame in serial_frames.items():
        assert parallel_frames[label].equals(serial_frame)


def test_parallel_build_handles_blank_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank lines are formatting: never records, never skips — including when
    a chunk boundary lands on one."""
    body = "\n\n".join(
        json.dumps({"id": i, "addr": None, "claims": [], "tags": []}) for i in range(150)
    )
    src = tmp_path / "blanks.jsonl"
    src.write_text(body + "\n", encoding="utf-8")

    serial_summary, serial_frames = _build(src, tmp_path / "serial_cache")

    _force_parallel(monkeypatch)
    parallel_summary, parallel_frames = _build(src, tmp_path / "parallel_cache")

    assert parallel_summary["skipped"]["records"] == 0
    assert parallel_summary["skipped"] == serial_summary["skipped"]
    for label, serial_frame in serial_frames.items():
        assert parallel_frames[label].equals(serial_frame)


def test_parallel_worker_type_mismatch_raises_with_the_column_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared-vs-actual mismatch is raised inside a worker. It must reach
    the caller as the same typed, column-naming error the serial path gives —
    the exception is rebuilt in the parent, not pickled."""
    records = [{"premium": 1.5} for _ in range(200)]
    records[190] = {"premium": "not-a-number"}
    src = _write_jsonl(tmp_path / "bad.jsonl", records)
    schema = {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "premium",
                        "path": "$[:].premium",
                        "type": "float",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }

    with pytest.raises(ApiInputSchemaError) as serial_exc:
        build_per_port_cache(src, schema, tmp_path / "serial_cache")

    _force_parallel(monkeypatch)
    with pytest.raises(ApiInputSchemaError) as parallel_exc:
        build_per_port_cache(src, schema, tmp_path / "parallel_cache")

    assert parallel_exc.value.message == serial_exc.value.message
    assert parallel_exc.value.context == serial_exc.value.context
    assert str(parallel_exc.value) == str(serial_exc.value)


def test_parallel_json_decode_error_matches_serial_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON error reconstruction preserves parser evidence and public detail."""
    src = tmp_path / "bad-json.jsonl"
    src.write_text(
        "\n".join([*(json.dumps({"id": i}) for i in range(200)), "{bad"]) + "\n",
        encoding="utf-8",
    )
    schema = {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "id",
                        "path": "$[:].id",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }

    with pytest.raises(orjson.JSONDecodeError) as serial_exc:
        build_per_port_cache(src, schema, tmp_path / "serial_cache")

    _force_parallel(monkeypatch)
    with pytest.raises(orjson.JSONDecodeError) as parallel_exc:
        build_per_port_cache(src, schema, tmp_path / "parallel_cache")

    assert parallel_exc.value.msg == serial_exc.value.msg
    assert parallel_exc.value.doc == serial_exc.value.doc
    assert parallel_exc.value.pos == serial_exc.value.pos
    assert str(parallel_exc.value) == str(serial_exc.value)


def test_parallel_missing_source_remains_file_not_found(tmp_path: Path) -> None:
    """Expected filesystem failures must not be collapsed into RuntimeError."""
    schema = {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "id",
                        "path": "$[:].id",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError) as serial_exc:
        missing.open("rb")

    result = _shred_chunk((str(missing), 0, 1, 0, schema, str(tmp_path)))

    with pytest.raises(FileNotFoundError) as parallel_exc:
        _raise_chunk_error(result)

    assert str(parallel_exc.value) == str(serial_exc.value)


def test_worker_does_not_disguise_process_control_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker envelope is for data failures, not ``BaseException`` control flow."""

    class WorkerStop(BaseException):
        pass

    src = _write_jsonl(tmp_path / "data.jsonl", [{"id": 1}])
    schema = infer_v2_schema_from_data(src)
    for table in schema["tables"]:
        table["emit"] = True

    def interrupt_read(*_args: object, **_kwargs: object) -> Any:
        raise WorkerStop

    monkeypatch.setattr(_json_shred, "_iter_range_records", interrupt_read)

    with pytest.raises(WorkerStop):
        _shred_chunk((str(src), 0, src.stat().st_size, 0, schema, str(tmp_path)))


def test_failed_parallel_build_leaves_no_staging_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arrow parts are written into the staging directory; a failure must clear
    them along with it rather than leaking parts next to a live cache."""
    records = [{"premium": 1.5} for _ in range(200)]
    records[150] = {"premium": "not-a-number"}
    src = _write_jsonl(tmp_path / "bad.jsonl", records)
    schema = {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "premium",
                        "path": "$[:].premium",
                        "type": "float",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    cache = tmp_path / "cache"

    _force_parallel(monkeypatch)
    with pytest.raises(ApiInputSchemaError):
        build_per_port_cache(src, schema, cache)

    assert list(tmp_path.glob("**/*.arrow")) == []
    assert list(tmp_path.glob("**/*build-tmp*")) == []


def test_parallel_build_runs_off_the_main_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build route reaches this through ``run_in_threadpool``, so the pool
    is started from a worker thread, not the main one. Pinned because a start
    method that only worked on the main thread would pass every other test here
    and fail in the server."""
    from concurrent.futures import ThreadPoolExecutor

    src = _write_jsonl(tmp_path / "d.jsonl", _records(300))
    schema = infer_v2_schema_from_data(src)
    for table in schema["tables"]:
        table["emit"] = True

    _force_parallel(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        summary = pool.submit(build_per_port_cache, src, schema, tmp_path / "cache").result()

    root_label = next(t["label"] for t in schema["tables"] if t["path"] == "$[:]")
    row_counts = {t["label"]: t["row_count"] for t in summary["tables"]}
    assert row_counts[root_label] == 300
