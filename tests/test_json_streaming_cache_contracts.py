"""High-signal contracts for JSON streaming and cache lifecycle behavior."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import polars as pl
import pytest

from haute._json_flatten import (
    JsonCacheCancelledError,
    JsonFlattenDataError,
    JsonFlattenSchemaError,
    _cancel_events,
    _clear_cancel_events,
    _flatten_raw_parquet,
    _is_cache_valid,
    _iter_byte_chunks,
    _json_cache_path,
    _jsonl_to_raw_parquet,
    _schema_fingerprint,
    build_json_cache,
    clear_json_cache,
    flatten,
    flatten_progress,
    infer_schema,
    json_cache_info,
    read_json_flat,
)


def _cache_artifacts(cache_path: Path) -> list[Path]:
    """Return the on-disk artifacts inside the working/<hash>/ cache dir."""
    raw_path = cache_path.with_suffix(".raw.parquet")
    return [
        cache_path,
        cache_path.parent / "meta.json",
        Path(str(cache_path) + ".tmp"),
        raw_path,
        Path(str(raw_path) + ".tmp"),
    ]


def test_iter_byte_chunks_preserves_complete_jsonl_records_across_boundaries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.jsonl"
    payload = b'{"i":0}\n{"i":1,"value":"longer"}\n{"i":2}'
    path.write_bytes(payload)

    chunks = list(_iter_byte_chunks(path, buffer_size=9))

    assert b"".join(chunks) == payload
    assert chunks[:-1]
    assert all(chunk.endswith(b"\n") for chunk in chunks[:-1])
    assert chunks[-1] == b'{"i":2}'


def test_jsonl_to_raw_parquet_rejects_malformed_jsonl_without_empty_success(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad.jsonl"
    dest = tmp_path / "raw.parquet"
    path.write_text('{"a":1}\nnot json\n{"a":2}\n', encoding="utf-8")

    with pytest.raises(JsonFlattenDataError, match="Invalid JSONL file"):
        _jsonl_to_raw_parquet(path, dest)

    assert not dest.exists()
    assert not Path(str(dest) + ".tmp").exists()


def test_blank_jsonl_still_builds_empty_raw_parquet(tmp_path: Path) -> None:
    path = tmp_path / "blank.jsonl"
    dest = tmp_path / "raw.parquet"
    path.write_text("\n  \n\t\n", encoding="utf-8")

    row_count = _jsonl_to_raw_parquet(path, dest)

    assert row_count == 0
    assert dest.exists()
    assert pl.read_parquet(dest).shape == (0, 0)


def test_read_json_flat_rejects_malformed_jsonl_and_leaves_no_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "bad.jsonl"
    data_file.write_text('{"a":1}\nnot json\n{"a":2}\n', encoding="utf-8")

    with pytest.raises(JsonFlattenDataError, match="Invalid JSONL file"):
        read_json_flat(str(data_file))

    cache_path = _json_cache_path(str(data_file))
    assert all(not artifact.exists() for artifact in _cache_artifacts(cache_path))


def test_read_json_flat_mixed_shape_jsonl_matches_python_flatten_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    records = [
        {"contact": "legacy"},
        {"contact": {"email": "a@example.com"}},
        {"events": ["legacy-note"]},
        {"events": [{"type": "renewal"}]},
    ]
    data_file = tmp_path / "mixed.jsonl"
    data_file.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    schema = infer_schema(records)

    df = read_json_flat(str(data_file)).collect()

    assert df.to_dicts() == [flatten(record, schema) for record in records]


def test_build_json_cache_mixed_shape_jsonl_matches_python_flatten_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    records = [
        {"contact": "legacy"},
        {"contact": {"email": "a@example.com"}},
        {"events": ["legacy-note"]},
        {"events": [{"type": "renewal"}]},
    ]
    data_file = tmp_path / "mixed.jsonl"
    data_file.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    schema = infer_schema(records)

    result = build_json_cache(str(data_file))
    df = pl.read_parquet(result["path"])

    assert result["row_count"] == len(records)
    assert df.to_dicts() == [flatten(record, schema) for record in records]
    cache_path = _json_cache_path(str(data_file))
    assert not cache_path.with_suffix(".raw.parquet").exists()


def test_explicit_schema_preserves_jsonl_fields_outside_polars_inference_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute._json_flatten._SCHEMA_SAMPLE_SIZE", 1)
    data_file = tmp_path / "late.jsonl"
    data_file.write_text(
        '{"id":1}\n{"id":2,"late":99}\n',
        encoding="utf-8",
    )

    df = read_json_flat(str(data_file), schema={"id": "int", "late": "int"}).collect()

    assert df.columns == ["id", "late"]
    assert df["id"].to_list() == [1, 2]
    assert df["late"].to_list() == [None, 99]


def test_explicit_schema_cache_contract_invalidates_same_path_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.jsonl"
    data_file.write_text('{"x":1,"y":2}\n', encoding="utf-8")

    assert read_json_flat(str(data_file), schema={"x": "int"}).collect().columns == ["x"]
    df = read_json_flat(str(data_file), schema={"x": "int", "y": "int"}).collect()

    assert df.columns == ["x", "y"]
    assert df["y"].to_list() == [2]

    meta_path = _json_cache_path(str(data_file)).parent / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta == {
        "schema_fingerprint": _schema_fingerprint({"x": "int", "y": "int"}),
        "schema_mode": "explicit",
    }


def test_explicit_schema_is_validated_before_jsonl_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.jsonl"
    data_file.write_text('{"a":{"b":1}}\n', encoding="utf-8")
    conversion_called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> int:
        nonlocal conversion_called
        conversion_called = True
        raise AssertionError("JSONL conversion should not run for invalid schema")

    monkeypatch.setattr("haute._json_flatten._jsonl_to_raw_parquet", fail_if_called)

    with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
        read_json_flat(str(data_file), schema={"a.b": "int"})

    assert conversion_called is False


def test_build_json_cache_cleans_progress_and_artifacts_when_schema_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.jsonl"
    data_file.write_text('{"a":{"b":1}}\n', encoding="utf-8")

    with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
        build_json_cache(str(data_file), schema={"a.b": "int"})

    assert flatten_progress(str(data_file)) is None
    assert str(data_file) not in _cancel_events
    cache_path = _json_cache_path(str(data_file))
    assert all(not artifact.exists() for artifact in _cache_artifacts(cache_path))


def test_is_cache_valid_requires_source_file_to_still_exist(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache_dir"
    cache_dir.mkdir()
    (cache_dir / "data.parquet").write_bytes(b"stale cache")

    assert not _is_cache_valid(cache_dir, tmp_path / "deleted.jsonl")


def test_json_cache_info_returns_none_for_stale_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.jsonl"
    data_file.write_text('{"x":1}\n', encoding="utf-8")
    assert read_json_flat(str(data_file), schema={"x": "int"}).collect().height == 1
    cache_path = _json_cache_path(str(data_file))

    os.utime(cache_path, (315532800.0, 315532800.0))
    data_file.write_text('{"x":2}\n', encoding="utf-8")

    assert json_cache_info(str(data_file)) is None


def test_read_json_flat_does_not_use_cache_after_source_is_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.jsonl"
    data_file.write_text(json.dumps({"x": 1}) + "\n", encoding="utf-8")
    assert read_json_flat(str(data_file), schema={"x": "int"}).collect()["x"].to_list() == [1]
    assert _json_cache_path(str(data_file)).exists()

    data_file.unlink()

    with pytest.raises(FileNotFoundError):
        read_json_flat(str(data_file), schema={"x": "int"})


def test_clear_json_cache_removes_parquet_and_sidecar_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.jsonl"
    cache_path = _json_cache_path(str(data_file))
    cache_path.parent.mkdir(parents=True)
    artifacts = _cache_artifacts(cache_path)
    for artifact in artifacts:
        artifact.write_bytes(b"generated")

    assert clear_json_cache(str(data_file)) is True
    assert all(not artifact.exists() for artifact in artifacts)
    assert clear_json_cache(str(data_file)) is False


def test_jsonl_conversion_obeys_pre_cancelled_event_without_artifacts(
    tmp_path: Path,
) -> None:
    data_file = tmp_path / "data.jsonl"
    data_file.write_text('{"x":1}\n', encoding="utf-8")
    dest = tmp_path / "raw.parquet"
    event = threading.Event()
    event.set()

    with pytest.raises(JsonCacheCancelledError):
        _jsonl_to_raw_parquet(data_file, dest, cancel_event=event)

    assert not dest.exists()
    assert not Path(str(dest) + ".tmp").exists()


def test_raw_flatten_obeys_pre_cancelled_event_without_artifacts(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "raw.parquet"
    dest = tmp_path / "flat.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(raw_path)
    event = threading.Event()
    event.set()

    with pytest.raises(JsonCacheCancelledError):
        _flatten_raw_parquet(raw_path, {"x": "int"}, dest, cancel_event=event)

    assert not dest.exists()
    assert not Path(str(dest) + ".tmp").exists()


@pytest.fixture(autouse=True)
def _cleanup_cancel_events() -> None:
    yield
    _clear_cancel_events()
