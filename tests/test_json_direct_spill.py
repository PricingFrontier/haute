"""Regression coverage for uncached JSON/JSONL runtime spills."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pyarrow.parquet as pq
import pytest

import haute._json_shred as shred_mod
from haute._api_input_schema import ApiInputSchemaError
from haute._json_flatten import _json_cache_dir


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    shred_mod._cleanup_direct_spill_dirs()
    yield
    shred_mod._cleanup_direct_spill_dirs()


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
        shred_mod.load_v2_api_source(str(data), config)


def test_root_array_parser_accepts_empty_input_and_whitespace_array(tmp_path: Path) -> None:
    blank = tmp_path / "blank.json"
    blank.write_bytes(b" \n\t")
    assert list(shred_mod._iter_records(blank)) == []
    empty = tmp_path / "empty.json"
    empty.write_bytes(b" [ ] \r\n")
    assert list(shred_mod._iter_records(empty)) == []


def test_cold_direct_spill_does_not_hash_source_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.jsonl"
    data.write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    monkeypatch.setattr(
        shred_mod,
        "_data_file_signature",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("an absent cache must not trigger a full source hash")
        ),
    )

    result = shred_mod.load_v2_api_source(str(data), config)

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

    out = shred_mod.load_v2_api_source(str(data), config)

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
        shred_mod.load_v2_api_source(str(data), config)


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
        shred_mod.load_v2_api_source(str(data), config)


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
    original_flush = shred_mod._DirectSpillBundle.flush

    def _count_flush(bundle: Any) -> None:
        nonlocal flushes
        flushes += 1
        original_flush(bundle)

    monkeypatch.setattr(shred_mod._DirectSpillBundle, "flush", _count_flush)
    out = shred_mod.load_v2_api_source(str(data), config)

    assert all(isinstance(frame, pl.LazyFrame) for frame in out.values())
    assert flushes > 2
    assert out["root"].collect().to_dict(as_series=False) == {"id": list(range(5))}
    assert out["items"].collect().to_dict(as_series=False) == {
        "value": [0, 1, 10, 11, 20, 21, 30, 31, 40, 41]
    }
    assert not _json_cache_dir(str(data), "working").exists()
    assert not _json_cache_dir(str(data), "committed").exists()


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
    original_flush = shred_mod._DirectSpillBundle.flush

    def _observe_flush(bundle: Any) -> None:
        observed_buffers.append((bundle.buffered_rows, bundle.buffered_bytes))
        original_flush(bundle)

    monkeypatch.setattr(shred_mod._DirectSpillBundle, "flush", _observe_flush)
    out = shred_mod.load_v2_api_source(str(data), config)

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

    out = shred_mod.load_v2_api_source(
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
    original = shred_mod._new_direct_spill_dir

    def _capture(cache_dir: Path) -> Path:
        path = original(cache_dir)
        created.append(path)
        return path

    monkeypatch.setattr(shred_mod, "_new_direct_spill_dir", _capture)
    with pytest.raises(ApiInputSchemaError):
        shred_mod.load_v2_api_source(str(data), config)
    assert created and not created[0].exists()


@pytest.mark.parametrize("failure", ["writer construction", "writer write"])
def test_direct_spill_writer_failures_clean_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    created: list[Path] = []
    original_new_dir = shred_mod._new_direct_spill_dir

    def _capture(cache_dir: Path) -> Path:
        path = original_new_dir(cache_dir)
        created.append(path)
        return path

    monkeypatch.setattr(shred_mod, "_new_direct_spill_dir", _capture)
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
        shred_mod.load_v2_api_source(str(data), config)
    assert created and not created[0].exists()


def test_direct_spill_creation_preserves_primary_error_when_owner_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        shred_mod,
        "_runtime_disk_budget_transaction",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        shred_mod,
        "_ensure_runtime_owner_metadata",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid owner metadata")),
    )
    monkeypatch.setattr(
        shred_mod,
        "_remove_empty_runtime_owner_dir",
        lambda _path: (_ for _ in ()).throw(OSError("cleanup denied")),
    )

    with pytest.raises(ValueError, match="invalid owner metadata"):
        shred_mod._new_direct_spill_dir(tmp_path / "cache")


def test_direct_spill_constructor_notes_writer_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:]", "second", [_col("value", "$[:].value")]),
        ]
    }
    table_specs = shred_mod._emitting_table_specs(config)
    calls = 0

    class _FirstWriter:
        def close(self) -> None:
            raise OSError("first writer would not close")

    def _writer(*_args: Any, **_kwargs: Any) -> _FirstWriter:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("second writer construction failed")
        return _FirstWriter()

    monkeypatch.setattr(pq, "ParquetWriter", _writer)
    monkeypatch.setattr(
        shred_mod,
        "_runtime_disk_budget_transaction",
        lambda *_args, **_kwargs: nullcontext(),
    )
    real_release = shred_mod._release_direct_spill_dir

    def _failing_release(path: Path) -> None:
        real_release(path)
        raise OSError("spill directory cleanup failed")

    monkeypatch.setattr(shred_mod, "_release_direct_spill_dir", _failing_release)

    with pytest.raises(ValueError, match="second writer construction failed") as raised:
        shred_mod._DirectSpillBundle(tmp_path / "cache", table_specs)

    notes = getattr(raised.value, "__notes__", [])
    assert any("first writer would not close" in note for note in notes)
    assert any("spill directory cleanup failed" in note for note in notes)


def test_direct_shred_notes_writer_cleanup_failure_on_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    table_specs = shred_mod._emitting_table_specs(config)
    spill_dir = tmp_path / "spill"
    spill_dir.mkdir()
    shred_mod._DIRECT_SPILL_DIRS.add(spill_dir)

    class _FailingBundle:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.spill_dir = spill_dir

        def emit(self, _label: str, _row: dict[str, Any]) -> None:
            pytest.fail("the primary shred failure should happen before row emission")

        def close(self) -> None:
            raise OSError("writer cleanup failed")

    monkeypatch.setattr(shred_mod, "_DirectSpillBundle", _FailingBundle)
    monkeypatch.setattr(
        shred_mod,
        "shred_to_buffers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("primary shred failure")),
    )
    real_release = shred_mod._release_direct_spill_dir

    def _failing_release(path: Path) -> None:
        real_release(path)
        raise OSError("spill directory cleanup failed")

    monkeypatch.setattr(shred_mod, "_release_direct_spill_dir", _failing_release)

    with pytest.raises(ValueError, match="primary shred failure") as raised:
        shred_mod._shred_data_file_to_direct_spill(
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
    original_new_dir = shred_mod._new_direct_spill_dir

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

    monkeypatch.setattr(shred_mod, "_new_direct_spill_dir", _capture)
    monkeypatch.setattr(shred_mod, "current_execution_context", lambda: _FailingContext())

    with pytest.raises(RuntimeError, match="cannot register cleanup"):
        shred_mod.load_v2_api_source(str(data), config)
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
    monkeypatch.setattr(shred_mod, "current_execution_context", lambda: context)
    shred_mod.load_v2_api_source(str(data), config)
    assert len(context.cleanups) == 1
    managed_path = next(iter(shred_mod._DIRECT_SPILL_DIRS))
    context.cleanups[0]()
    assert not managed_path.exists()

    monkeypatch.setattr(shred_mod, "current_execution_context", lambda: None)
    shred_mod.load_v2_api_source(str(data), config)
    unmanaged_path = next(iter(shred_mod._DIRECT_SPILL_DIRS))
    monkeypatch.setattr(shred_mod.os, "getpid", lambda: shred_mod._DIRECT_SPILL_PROCESS_ID + 1)
    shred_mod._cleanup_direct_spill_dirs()
    assert unmanaged_path.exists()
    monkeypatch.undo()
    shred_mod._cleanup_direct_spill_dirs()
    assert not unmanaged_path.exists()


def test_direct_spill_close_preserves_first_writer_error_and_notes_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = object.__new__(shred_mod._DirectSpillBundle)
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
        shred_mod, "_runtime_disk_budget_transaction", lambda *_args, **_kwargs: nullcontext()
    )

    with pytest.raises(OSError, match="first failure") as raised:
        bundle.close()

    assert bundle.writers == {}
    assert any("second failure" in note for note in getattr(raised.value, "__notes__", []))


def test_direct_spill_fork_reset_and_cleanup_failures_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "spills"
    spill = parent / "bundle"
    spill.mkdir(parents=True)
    shred_mod._DIRECT_SPILL_DIRS.add(spill)
    original_rmtree = shred_mod.shutil.rmtree

    def fail_parent(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == parent:
            raise OSError("busy")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shred_mod.shutil, "rmtree", fail_parent)
    shred_mod._cleanup_direct_spill_dirs()
    assert not spill.exists()

    shred_mod._DIRECT_SPILL_DIRS.add(tmp_path / "inherited")
    monkeypatch.setattr(shred_mod.os, "getpid", lambda: shred_mod._DIRECT_SPILL_PROCESS_ID + 1)
    monkeypatch.setattr(shred_mod, "_runtime_disk_budget_transaction", lambda *_args: nullcontext())
    fresh = shred_mod._new_direct_spill_dir(tmp_path / "cache")
    assert fresh in shred_mod._DIRECT_SPILL_DIRS
    assert tmp_path / "inherited" not in shred_mod._DIRECT_SPILL_DIRS


def test_direct_spill_release_tolerates_vanished_owner_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spill_dir = tmp_path / "owner" / "spill"
    spill_dir.mkdir(parents=True)
    shred_mod._DIRECT_SPILL_DIRS.add(spill_dir)
    monkeypatch.setattr(
        shred_mod,
        "_remove_empty_runtime_owner_dir",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("owner vanished")),
    )

    shred_mod._release_direct_spill_dir(spill_dir)

    assert spill_dir not in shred_mod._DIRECT_SPILL_DIRS
    assert not spill_dir.exists()
