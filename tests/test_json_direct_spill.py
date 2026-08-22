"""Regression coverage for uncached JSON/JSONL runtime spills."""

from __future__ import annotations

import json
import os
import shutil
import xml.etree.ElementTree as ET
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pyarrow.parquet as pq
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_flatten import _json_cache_dir
from haute._json_shred import _cache, _records, _runtime_storage, _shred, _source_proof, _writer


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _runtime_storage._cleanup_direct_spill_dirs()
    yield
    _runtime_storage._cleanup_direct_spill_dirs()


def _col(name: str, path: str, type_token: str = "int") -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": type_token,
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


def _table(path: str, label: str, columns: list[dict[str, Any]]) -> dict[str, Any]:
    return {"path": path, "label": label, "emit": True, "row_id_column": None, "columns": columns}


def test_root_array_streams_without_read_bytes_and_validates_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}, {"id": 2}] trailing', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    original = Path.read_bytes

    def _no_source_read_bytes(path: Path) -> bytes:
        if path == data:
            raise AssertionError("root JSON arrays must not materialise through Path.read_bytes")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", _no_source_read_bytes)
    with pytest.raises(Exception, match="trailing"):
        _cache.load_v2_api_source(str(data), config)


def test_root_array_parser_accepts_empty_input_and_whitespace_array(tmp_path: Path) -> None:
    blank = tmp_path / "blank.json"
    blank.write_bytes(b" \n\t")
    assert list(_records._iter_records(blank)) == []
    empty = tmp_path / "empty.json"
    empty.write_bytes(b" [ ] \r\n")
    assert list(_records._iter_records(empty)) == []


def test_root_array_value_counts_quote_and_nested_delimiters_at_the_exact_limit() -> None:
    """The incremental reader must account for every structural byte.

    This deliberately exercises the quote, nested-open, and nested-close
    append paths with a limit exactly equal to the encoded element; a smaller
    limit is rejected by the same reader in the record-limit tests above.
    """
    encoded = b'{"note":"x","nested":[1]}'
    remaining = iter(bytes((byte,)) for byte in encoded[1:] + b",")

    value, delimiter = _records._read_root_array_value(
        encoded[:1], lambda: next(remaining, b""), lambda: len(encoded), max_bytes=len(encoded)
    )

    assert value == encoded
    assert delimiter == b","


@pytest.mark.parametrize(
    ("first", "remainder"),
    [
        pytest.param(b"x", b'"', id="quote-after-token"),
        pytest.param(b"x", b"{", id="nested-open-after-token"),
        pytest.param(b"{", b"}", id="nested-close"),
    ],
)
def test_root_array_value_rejects_each_structural_append_beyond_limit(
    first: bytes,
    remainder: bytes,
) -> None:
    source = iter(bytes((byte,)) for byte in remainder)

    with pytest.raises(ApiInputSchemaError, match="JSON array element exceeds"):
        _records._read_root_array_value(
            first,
            lambda: next(source, b""),
            lambda: 1,
            max_bytes=1,
        )


def test_bounded_writer_rejects_unknown_labels_flushes_before_arrow_and_requires_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct Arrow writes cannot overtake buffered JSON rows or forge tables."""
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    spec = _shred._emitting_table_specs(config)
    monkeypatch.setenv("HAUTE_JSON_DIRECT_SPILL_MAX_ROWS", "2")
    writer = _writer._BoundedParquetRowGroupWriter(tmp_path, spec)
    try:
        with pytest.raises(RuntimeError, match="unknown table 'other'"):
            writer.emit("other", {"id": 1})
        with pytest.raises(RuntimeError, match="unknown table 'other'"):
            writer.write_arrow_table("other", pl.DataFrame({"id": [1]}).to_arrow())

        writer.emit("root", {"id": 1})
        with pytest.raises(RuntimeError, match="must be closed before summarising"):
            writer.table_summaries()
        with pytest.raises(RuntimeError, match="contains 3 rows; configured maximum is 2"):
            writer.write_arrow_table("root", pl.DataFrame({"id": [2, 3, 4]}).to_arrow())

        writer.write_arrow_table("root", pl.DataFrame({"id": [2]}).to_arrow())
        assert writer.buffered_rows == 0
        assert writer.row_counts == {"root": 2}
    finally:
        writer.close()

    assert pq.read_table(tmp_path / "root.parquet").to_pydict() == {"id": [1, 2]}


def test_streaming_cache_writer_preserves_primary_failure_and_cleanup_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    specs = _shred._emitting_table_specs(config)

    class FailingCloseWriter:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def emit(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("source failure must happen before emission")

        def close(self) -> None:
            raise OSError("writer close failed")

    monkeypatch.setattr(_writer, "_BoundedParquetRowGroupWriter", FailingCloseWriter)
    monkeypatch.setattr(
        _records,
        "_iter_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("source failed")),
    )

    with pytest.raises(RuntimeError, match="source failed") as exc_info:
        _writer._write_tables_streaming(tmp_path / "source.json", config, specs, tmp_path)

    assert exc_info.value.__notes__ == ["bounded cache writer cleanup failed: writer close failed"]


def test_cold_direct_spill_does_not_hash_source_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.jsonl"
    data.write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    monkeypatch.setattr(
        _source_proof,
        "_data_file_signature",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("an absent cache must not trigger a full source hash")
        ),
    )

    result = _cache.load_v2_api_source(str(data), config)

    assert result["root"].collect().to_dict(as_series=False) == {"id": [1, 2]}


def test_root_array_streaming_preserves_nested_values_and_escaped_delimiters(
    tmp_path: Path,
) -> None:
    data = tmp_path / "records.json"
    data.write_text(
        json.dumps(
            [
                {"id": 1, "note": 'quoted "[]{}" text', "nested": [{"items": [1, 2]}]},
                {"id": 2, "note": 'a \\ escaped bracket ] and quote \\"', "nested": []},
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id"), _col("note", "$[:].note", "str")])
        ]
    }

    out = _cache.load_v2_api_source(str(data), config)

    assert out["root"].collect().to_dict(as_series=False) == {
        "id": [1, 2],
        "note": ['quoted "[]{}" text', 'a \\ escaped bracket ] and quote \\"'],
    }


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("[] trailing", "trailing"),
        ('[{"id": 1},]', "trailing comma"),
        ('[{"id": 1} {"id": 2}]', "unexpected"),
        ('[{"id": 1}', "end of data"),
        ('[{"id": 1}] unexpected', "trailing"),
    ],
)
def test_root_array_streaming_rejects_malformed_documents(
    tmp_path: Path, contents: str, message: str
) -> None:
    data = tmp_path / "records.json"
    data.write_text(contents, encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    with pytest.raises(orjson.JSONDecodeError, match=message):
        _cache.load_v2_api_source(str(data), config)


@pytest.mark.parametrize(
    ("suffix", "contents"),
    [
        (".json", '{"value":"' + "x" * 96 + '"}'),
        (".json", '[{"value":"' + "x" * 96 + '"}]'),
        (".jsonl", '{"value":"' + "x" * 96 + '"}\n'),
    ],
)
def test_structured_json_records_fail_before_exceeding_hard_record_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    contents: str,
) -> None:
    data = tmp_path / f"oversized{suffix}"
    data.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES", "64")

    with pytest.raises(
        ApiInputSchemaError,
        match="exceeds HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES=64",
    ):
        list(_records._iter_records(data))


@pytest.mark.parametrize("value", ["invalid", "0", "-1"])
def test_structured_record_limit_rejects_invalid_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    monkeypatch.setenv("HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES", value)

    with pytest.raises(
        RuntimeError,
        match="HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES must be a positive integer",
    ):
        list(_records._iter_records(data))


@pytest.mark.parametrize(
    "name",
    ["HAUTE_JSON_DIRECT_SPILL_MAX_ROWS", "HAUTE_JSON_DIRECT_SPILL_MAX_BYTES"],
)
@pytest.mark.parametrize("value", ["invalid", "0", "-1"])
def test_direct_spill_rejects_invalid_or_non_positive_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=rf"{name} must be a positive integer"):
        _cache.load_v2_api_source(str(data), config)


def test_direct_spill_flushes_aggregate_bound_and_preserves_nested_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text(
        json.dumps(
            [{"id": i, "items": [{"value": i * 10}, {"value": i * 10 + 1}]} for i in range(5)]
        ),
        encoding="utf-8",
    )
    config = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].items[:]", "items", [_col("value", "$[:].items[:].value")]),
        ]
    }
    monkeypatch.setenv("HAUTE_JSON_DIRECT_SPILL_MAX_ROWS", "2")
    flushes = 0
    original_flush = _writer._DirectSpillBundle.flush

    def _count_flush(bundle: Any) -> None:
        nonlocal flushes
        flushes += 1
        original_flush(bundle)

    monkeypatch.setattr(_writer._DirectSpillBundle, "flush", _count_flush)
    out = _cache.load_v2_api_source(str(data), config)

    assert all(isinstance(frame, pl.LazyFrame) for frame in out.values())
    assert flushes > 2
    assert out["root"].collect().to_dict(as_series=False) == {"id": list(range(5))}
    assert out["items"].collect().to_dict(as_series=False) == {
        "value": [0, 1, 10, 11, 20, 21, 30, 31, 40, 41]
    }
    assert not _json_cache_dir(str(data), "working").exists()
    assert not _json_cache_dir(str(data), "committed").exists()


def test_persistent_cache_uses_shared_aggregate_bounded_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text(
        json.dumps(
            [{"id": i, "items": [{"value": i * 2}, {"value": i * 2 + 1}]} for i in range(8)]
        ),
        encoding="utf-8",
    )
    config = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].items[:]", "items", [_col("value", "$[:].items[:].value")]),
        ]
    }
    monkeypatch.setenv("HAUTE_JSON_DIRECT_SPILL_MAX_ROWS", "3")
    flushes: list[tuple[int, int]] = []
    writer_type = _writer._BoundedParquetRowGroupWriter
    original_flush = writer_type.flush

    def _observe_flush(writer: Any) -> None:
        flushes.append((writer.buffered_rows, writer.buffered_bytes))
        original_flush(writer)

    monkeypatch.setattr(writer_type, "flush", _observe_flush)
    cache_dir = tmp_path / "cache"
    _cache.build_per_port_cache(data, config, cache_dir)
    out = _cache.load_per_port_cache(cache_dir, config)

    assert len([rows for rows, _bytes in flushes if rows]) > 2
    assert all(rows <= 3 for rows, _bytes in flushes if rows)
    assert out["root"].collect().get_column("id").to_list() == list(range(8))
    assert out["items"].collect().get_column("value").to_list() == list(range(16))


def test_direct_spill_enforces_aggregate_buffer_limit_across_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text(
        json.dumps([{"id": 1, "items": [{"value": "10"}, {"value": "x" * 30}]}]),
        encoding="utf-8",
    )
    config = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].items[:]", "items", [_col("value", "$[:].items[:].value", "str")]),
        ]
    }
    monkeypatch.setenv("HAUTE_JSON_DIRECT_SPILL_MAX_ROWS", "100")
    monkeypatch.setenv("HAUTE_JSON_DIRECT_SPILL_MAX_BYTES", "22")
    observed_buffers: list[tuple[int, int]] = []
    original_flush = _writer._DirectSpillBundle.flush

    def _observe_flush(bundle: Any) -> None:
        observed_buffers.append((bundle.buffered_rows, bundle.buffered_bytes))
        original_flush(bundle)

    monkeypatch.setattr(_writer._DirectSpillBundle, "flush", _observe_flush)
    out = _cache.load_v2_api_source(str(data), config)

    assert any(rows == 2 for rows, _ in observed_buffers)
    assert all(buffered_bytes <= 22 or rows == 1 for rows, buffered_bytes in observed_buffers)
    assert out["root"].collect().to_dict(as_series=False) == {"id": [1]}
    assert out["items"].collect().to_dict(as_series=False) == {"value": ["10", "x" * 30]}


def test_direct_spill_keeps_projected_carrier_and_empty_table_schema(tmp_path: Path) -> None:
    data = tmp_path / "records.jsonl"
    data.write_text('{"id": 1, "items": []}\n', encoding="utf-8")
    config = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id"), _col("name", "$[:].name", "str")]),
            _table("$[:].items[:]", "items", [_col("value", "$[:].items[:].value")]),
        ]
    }

    out = _cache.load_v2_api_source(
        str(data), config, port_columns={"root": frozenset(), "items": None}
    )

    assert out["root"].collect().to_dict(as_series=False) == {"id": [1]}
    assert out["items"].collect_schema().names() == ["value"]
    assert out["items"].collect().height == 0


def test_direct_spill_type_failure_cleans_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": "not-an-int"}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    created: list[Path] = []
    original = _runtime_storage._new_direct_spill_dir

    def _capture(cache_dir: Path) -> Path:
        path = original(cache_dir)
        created.append(path)
        return path

    monkeypatch.setattr(_runtime_storage, "_new_direct_spill_dir", _capture)
    with pytest.raises(ApiInputSchemaError):
        _cache.load_v2_api_source(str(data), config)
    assert created and not created[0].exists()


@pytest.mark.parametrize("failure", ["writer construction", "writer write"])
def test_direct_spill_writer_failures_clean_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    created: list[Path] = []
    original_new_dir = _runtime_storage._new_direct_spill_dir

    def _capture(cache_dir: Path) -> Path:
        path = original_new_dir(cache_dir)
        created.append(path)
        return path

    monkeypatch.setattr(_runtime_storage, "_new_direct_spill_dir", _capture)
    if failure == "writer construction":

        def _fail_construction(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise OSError("cannot create parquet writer")

        monkeypatch.setattr(pq, "ParquetWriter", _fail_construction)
    else:
        original_writer = pq.ParquetWriter

        class _FailingWriter:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self._writer = original_writer(*args, **kwargs)

            def write_table(self, table: Any) -> None:
                del table
                raise OSError("cannot write parquet row group")

            def close(self) -> None:
                self._writer.close()

        monkeypatch.setattr(pq, "ParquetWriter", _FailingWriter)

    with pytest.raises(OSError, match="parquet"):
        _cache.load_v2_api_source(str(data), config)
    assert created and not created[0].exists()


def test_direct_spill_creation_preserves_primary_error_when_owner_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _runtime_storage,
        "_runtime_disk_budget_transaction",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        _runtime_storage,
        "_ensure_runtime_owner_metadata",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid owner metadata")),
    )
    monkeypatch.setattr(
        _runtime_storage,
        "_remove_empty_runtime_owner_dir",
        lambda _path: (_ for _ in ()).throw(OSError("cleanup denied")),
    )

    with pytest.raises(ValueError, match="invalid owner metadata") as raised:
        _runtime_storage._new_direct_spill_dir(tmp_path / "cache")

    assert any(
        "direct spill owner cleanup failed: cleanup denied" in note
        for note in getattr(raised.value, "__notes__", [])
    )


def test_direct_spill_creation_notes_staging_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_root = tmp_path / "runtime-root"

    @contextmanager
    def fail_after_creation(*_args: Any, **_kwargs: Any):
        yield
        raise ValueError("budget commit failed")

    original_rmtree = shutil.rmtree

    def fail_spill_cleanup(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.parent.name == _runtime_storage._DIRECT_SPILL_PROCESS_TOKEN:
            raise OSError("spill cleanup denied")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        _runtime_storage, "_runtime_storage_root_for_cache", lambda _path: cache_root
    )
    monkeypatch.setattr(_runtime_storage, "_runtime_disk_budget_transaction", fail_after_creation)
    monkeypatch.setattr(shutil, "rmtree", fail_spill_cleanup)

    with pytest.raises(ValueError, match="budget commit failed") as raised:
        _runtime_storage._new_direct_spill_dir(tmp_path / "cache")

    assert any(
        "direct spill staging cleanup failed: spill cleanup denied" in note
        for note in getattr(raised.value, "__notes__", [])
    )
    monkeypatch.setattr(shutil, "rmtree", original_rmtree)
    original_rmtree(cache_root)


def test_direct_spill_creation_tolerates_staging_cleanup_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_root = tmp_path / "runtime-root"

    @contextmanager
    def fail_after_creation(*_args: Any, **_kwargs: Any):
        yield
        raise ValueError("budget commit failed")

    original_rmtree = shutil.rmtree

    def remove_then_report_missing(path: Path, *args: Any, **kwargs: Any) -> None:
        original_rmtree(path, *args, **kwargs)
        raise FileNotFoundError(path)

    monkeypatch.setattr(
        _runtime_storage, "_runtime_storage_root_for_cache", lambda _path: cache_root
    )
    monkeypatch.setattr(_runtime_storage, "_runtime_disk_budget_transaction", fail_after_creation)
    monkeypatch.setattr(shutil, "rmtree", remove_then_report_missing)

    with pytest.raises(ValueError, match="budget commit failed") as raised:
        _runtime_storage._new_direct_spill_dir(tmp_path / "cache")

    assert not getattr(raised.value, "__notes__", [])
    owner_dir = (
        cache_root
        / _runtime_storage._DIRECT_SPILL_DIRNAME
        / _runtime_storage._DIRECT_SPILL_PROCESS_TOKEN
    )
    assert not owner_dir.exists()
    original_rmtree(cache_root)


def test_direct_spill_constructor_notes_writer_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:]", "second", [_col("value", "$[:].value")]),
        ]
    }
    table_specs = _shred._emitting_table_specs(config)
    calls = 0

    class _FirstWriter:
        def close(self) -> None:
            raise OSError("first writer would not close")

    def _writer_factory(*_args: Any, **_kwargs: Any) -> _FirstWriter:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("second writer construction failed")
        return _FirstWriter()

    monkeypatch.setattr(pq, "ParquetWriter", _writer_factory)
    monkeypatch.setattr(
        _runtime_storage,
        "_runtime_disk_budget_transaction",
        lambda *_args, **_kwargs: nullcontext(),
    )
    real_release = _runtime_storage._release_direct_spill_dir

    def _failing_release(path: Path) -> None:
        real_release(path)
        raise OSError("spill directory cleanup failed")

    monkeypatch.setattr(_runtime_storage, "_release_direct_spill_dir", _failing_release)

    with pytest.raises(ValueError, match="second writer construction failed") as raised:
        _writer._DirectSpillBundle(tmp_path / "cache", table_specs)

    notes = getattr(raised.value, "__notes__", [])
    assert any("first writer would not close" in note for note in notes)
    assert any("spill directory cleanup failed" in note for note in notes)


def test_direct_shred_notes_writer_cleanup_failure_on_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    table_specs = _shred._emitting_table_specs(config)
    spill_dir = tmp_path / "spill"
    spill_dir.mkdir()
    _runtime_storage._DIRECT_SPILL_DIRS.add(spill_dir)

    class _FailingBundle:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.spill_dir = spill_dir

        def emit(self, _label: str, _row: dict[str, Any]) -> None:
            pytest.fail("the primary shred failure should happen before row emission")

        def close(self) -> None:
            raise OSError("writer cleanup failed")

    monkeypatch.setattr(_writer, "_DirectSpillBundle", _FailingBundle)
    monkeypatch.setattr(
        _shred,
        "shred_to_buffers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("primary shred failure")),
    )
    real_release = _runtime_storage._release_direct_spill_dir

    def _failing_release(path: Path) -> None:
        real_release(path)
        raise OSError("spill directory cleanup failed")

    monkeypatch.setattr(_runtime_storage, "_release_direct_spill_dir", _failing_release)

    with pytest.raises(ValueError, match="primary shred failure") as raised:
        _writer._shred_data_file_to_direct_spill(
            data,
            config,
            table_specs,
            tmp_path / "cache",
        )

    notes = getattr(raised.value, "__notes__", [])
    assert any("writer cleanup failed" in note for note in notes)
    assert any("spill directory cleanup failed" in note for note in notes)
    assert not spill_dir.exists()


def test_direct_spill_cleanup_registration_failure_removes_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    created: list[Path] = []
    original_new_dir = _runtime_storage._new_direct_spill_dir

    def _capture(cache_dir: Path) -> Path:
        path = original_new_dir(cache_dir)
        created.append(path)
        return path

    class _FailingContext:
        def add_cleanup(self, callback: Any) -> None:
            del callback
            raise RuntimeError("cannot register cleanup")

        def checkpoint(self, *, label: str) -> None:
            del label

        def record_cache_proof_miss(self, reason: Any) -> None:
            del reason

    monkeypatch.setattr(_runtime_storage, "_new_direct_spill_dir", _capture)
    monkeypatch.setattr(_writer, "current_execution_context", lambda: _FailingContext())

    with pytest.raises(RuntimeError, match="cannot register cleanup"):
        _cache.load_v2_api_source(str(data), config)
    assert created and not created[0].exists()


def test_direct_spill_managed_cleanup_and_unmanaged_fork_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    class _Context:
        def __init__(self) -> None:
            self.cleanups: list[Any] = []

        def add_cleanup(self, callback: Any) -> None:
            self.cleanups.append(callback)

        def checkpoint(self, *, label: str) -> None:
            del label

        def record_cache_proof_miss(self, reason: Any) -> None:
            del reason

        def record_cache_direct_fallback(self) -> None:
            pass

    context = _Context()
    monkeypatch.setattr(_writer, "current_execution_context", lambda: context)
    _cache.load_v2_api_source(str(data), config)
    assert len(context.cleanups) == 1
    managed_path = next(iter(_runtime_storage._DIRECT_SPILL_DIRS))
    context.cleanups[0]()
    assert not managed_path.exists()

    monkeypatch.setattr(_writer, "current_execution_context", lambda: None)
    _cache.load_v2_api_source(str(data), config)
    unmanaged_path = next(iter(_runtime_storage._DIRECT_SPILL_DIRS))
    monkeypatch.setattr(os, "getpid", lambda: _runtime_storage._DIRECT_SPILL_PROCESS_ID + 1)
    _runtime_storage._cleanup_direct_spill_dirs()
    assert unmanaged_path.exists()
    monkeypatch.undo()
    _runtime_storage._cleanup_direct_spill_dirs()
    assert not unmanaged_path.exists()


def test_direct_spill_close_preserves_first_writer_error_and_notes_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = object.__new__(_writer._DirectSpillBundle)
    bundle.cache_root = tmp_path

    class FailingWriter:
        def __init__(self, message: str) -> None:
            self.message = message

        def close(self) -> None:
            raise OSError(self.message)

    bundle.writers = {
        "first": FailingWriter("first failure"),
        "second": FailingWriter("second failure"),
    }
    monkeypatch.setattr(
        _runtime_storage,
        "_runtime_disk_budget_transaction",
        lambda *_args, **_kwargs: nullcontext(),
    )

    with pytest.raises(OSError, match="first failure") as raised:
        bundle.close()

    assert bundle.writers == {}
    assert any("second failure" in note for note in getattr(raised.value, "__notes__", []))


def test_direct_spill_fork_reset_and_cleanup_failures_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_process_id = _runtime_storage._DIRECT_SPILL_PROCESS_ID
    original_process_token = _runtime_storage._DIRECT_SPILL_PROCESS_TOKEN
    original_lock = _runtime_storage._DIRECT_SPILL_LOCK
    parent = tmp_path / "spills"
    spill = parent / "bundle"
    spill.mkdir(parents=True)
    _runtime_storage._DIRECT_SPILL_DIRS.add(spill)
    original_rmtree = shutil.rmtree
    warnings: list[tuple[str, dict[str, Any]]] = []

    def fail_parent(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == parent:
            raise OSError("busy")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_parent)
    monkeypatch.setattr(
        _runtime_storage.logger,
        "warning",
        lambda event, **fields: warnings.append((event, fields)),
    )
    _runtime_storage._cleanup_direct_spill_dirs()
    assert not spill.exists()
    assert warnings == [
        (
            "json_direct_spill_owner_cleanup_failed",
            {"path": str(parent), "error": "OSError('busy')"},
        )
    ]

    _runtime_storage._DIRECT_SPILL_DIRS.add(tmp_path / "inherited")
    monkeypatch.setattr(os, "getpid", lambda: _runtime_storage._DIRECT_SPILL_PROCESS_ID + 1)
    monkeypatch.setattr(
        _runtime_storage, "_runtime_disk_budget_transaction", lambda *_args: nullcontext()
    )
    fresh = _runtime_storage._new_direct_spill_dir(tmp_path / "cache")
    assert fresh in _runtime_storage._DIRECT_SPILL_DIRS
    assert tmp_path / "inherited" not in _runtime_storage._DIRECT_SPILL_DIRS
    _runtime_storage._release_direct_spill_dir(fresh)
    _runtime_storage._DIRECT_SPILL_PROCESS_ID = original_process_id
    _runtime_storage._DIRECT_SPILL_PROCESS_TOKEN = original_process_token
    _runtime_storage._DIRECT_SPILL_LOCK = original_lock


def test_direct_spill_orderly_cleanup_reports_bundle_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = tmp_path / "owner"
    spill = owner / "spill"
    spill.mkdir(parents=True)
    _runtime_storage._DIRECT_SPILL_DIRS.add(spill)
    original_rmtree = shutil.rmtree
    warnings: list[tuple[str, dict[str, Any]]] = []

    def fail_spill(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == spill:
            raise OSError("bundle busy")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_spill)
    monkeypatch.setattr(
        _runtime_storage.logger,
        "warning",
        lambda event, **fields: warnings.append((event, fields)),
    )

    _runtime_storage._cleanup_direct_spill_dirs()

    assert warnings == [
        (
            "json_direct_spill_cleanup_failed",
            {"path": str(spill), "error": "OSError('bundle busy')"},
        )
    ]
    assert not owner.exists()


def test_direct_spill_orderly_cleanup_tolerates_already_vanished_paths(tmp_path: Path) -> None:
    missing = tmp_path / "owner" / "missing"
    _runtime_storage._DIRECT_SPILL_DIRS.add(missing)

    _runtime_storage._cleanup_direct_spill_dirs()

    assert missing not in _runtime_storage._DIRECT_SPILL_DIRS


def test_direct_spill_release_tolerates_vanished_owner_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spill_dir = tmp_path / "owner" / "spill"
    spill_dir.mkdir(parents=True)
    _runtime_storage._DIRECT_SPILL_DIRS.add(spill_dir)
    monkeypatch.setattr(
        _runtime_storage,
        "_remove_empty_runtime_owner_dir",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("owner vanished")),
    )

    _runtime_storage._release_direct_spill_dir(spill_dir)

    assert spill_dir not in _runtime_storage._DIRECT_SPILL_DIRS
    assert not spill_dir.exists()


def test_xml_bounded_parser_rejects_oversize_invalid_and_empty_documents(tmp_path: Path) -> None:
    oversized = tmp_path / "large.xml"
    oversized.write_text("<root>0123456789</root>", encoding="utf-8")
    with pytest.raises(ApiInputSchemaError, match="exceeds"):
        _records._parse_bounded_xml_root(oversized, 5)

    invalid = tmp_path / "invalid.xml"
    invalid.write_text("<root>", encoding="utf-8")
    with pytest.raises(ApiInputSchemaError, match="Invalid XML"):
        _records._parse_bounded_xml_root(invalid, 100)

    empty = tmp_path / "empty.xml"
    empty.write_bytes(b"")
    with pytest.raises(ApiInputSchemaError, match="Invalid XML"):
        _records._parse_bounded_xml_root(empty, 100)


def test_repeated_xml_emission_rejects_shape_drift_and_malformed_tail(tmp_path: Path) -> None:
    scalar_record = tmp_path / "scalar-record.xml"
    scalar_record.write_text("<root><row>scalar</row></root>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="shape changed"):
        list(_records._iter_repeated_xml_records(scalar_record, 1_000))

    malformed = tmp_path / "malformed-repeated.xml"
    malformed.write_text("<root><row><id>1</id></row>", encoding="utf-8")
    with pytest.raises(ApiInputSchemaError, match="Invalid XML"):
        list(_records._iter_repeated_xml_records(malformed, 1_000))


def test_xml_root_invariant_and_missing_document_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "records.xml"
    source.write_text("<root><row><id>1</id></row></root>", encoding="utf-8")
    root = ET.fromstring("<root />")
    assert _records._require_xml_root(root) is root
    with pytest.raises(RuntimeError, match="direct child before the document root"):
        _records._require_xml_root(None)

    monkeypatch.setattr(_records, "_read_xml_events", lambda _parser: iter(()))
    with pytest.raises(ApiInputSchemaError, match="no document element"):
        _records._parse_bounded_xml_root(source, 1_000)


def test_root_array_value_scanner_covers_string_nested_and_scalar_terminators() -> None:
    def scanner(payload: bytes, position: int) -> tuple[bytes, bytes]:
        source = iter(payload)
        first = bytes((next(source),))
        return _records._read_root_array_value(
            first,
            lambda: bytes((next(source, 0),)),
            current_pos=lambda: position,
            max_bytes=20,
        )

    value, delimiter = scanner(b'"x",', 1)
    assert value == b'"x"' and delimiter == b","
    value, delimiter = scanner(b'{"a":[1]},', 2)
    assert value == b'{"a":[1]}' and delimiter == b","
    value, delimiter = scanner(b"123]", 3)
    assert value == b"123" and delimiter == b"]"

    for payload in (b'"xx",', b"{xx},", b"[xx],", b"xx]"):
        with pytest.raises(ApiInputSchemaError, match="JSON array element exceeds"):
            first = payload[:1]
            rest = iter(payload[1:])
            _records._read_root_array_value(
                first,
                lambda: bytes((next(rest, 0),)),
                current_pos=lambda: 4,
                max_bytes=1,
            )


def test_runtime_snapshot_release_ignores_nonempty_owner_cleanup_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "owner" / "snapshot.parquet"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"snapshot")
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[snapshot] = 1
    monkeypatch.setattr(
        _runtime_storage,
        "_remove_empty_runtime_owner_dir",
        lambda _path: (_ for _ in ()).throw(OSError("still occupied")),
    )

    _runtime_storage._release_runtime_snapshot(snapshot)

    assert not snapshot.exists()
    assert snapshot not in _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES


def test_unpinned_runtime_snapshot_ignores_owner_cleanup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "owner" / "snapshot.parquet"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"snapshot")
    monkeypatch.setattr(
        _runtime_storage,
        "_remove_empty_runtime_owner_dir",
        lambda _path: (_ for _ in ()).throw(OSError("occupied")),
    )

    _runtime_storage._remove_unpinned_runtime_snapshot(snapshot)

    assert not snapshot.exists()


def test_xml_record_size_validation_fails_closed() -> None:
    with pytest.raises(ApiInputSchemaError, match="XML record exceeds"):
        _records._validate_xml_record_value_size({"value": "x" * 100}, 10)
