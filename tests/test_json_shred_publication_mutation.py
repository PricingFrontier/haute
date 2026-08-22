"""Small mutation witnesses for JSON-shred publication and cache loading."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    _cache,
    _inference,
    _publication,
    _records,
    _shred,
    _source_proof,
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


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.json"
    path.write_text(json.dumps([{"id": 1, "other": 2}, {"id": 3, "other": 4}]))
    return path


def _prepared(data: Path, cache: Path, staging: Path, config: dict[str, Any]) -> Any:
    fingerprint = _shred._v2_fingerprint(config)
    signature = {"proof": "same"}
    summary = {
        "schema_mode": "v2",
        "schema_fingerprint": fingerprint,
        "tables": [],
        "data_file": signature,
        "skipped": {"records": 0, "rows_by_table": {}},
    }
    return _cache.PreparedPerPortCacheBuild(
        data_path=str(data),
        cache_dir=str(cache),
        staging_dir=str(staging),
        schema_fingerprint=fingerprint,
        data_file_signature=signature,
        summary=summary,
    )


def test_validated_staging_dir_requires_exact_private_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "nested" / "cache"
    valid = cache.with_name(f"cache.build-tmp-{'a' * 32}")
    monkeypatch.setattr(_publication, "_assert_cache_path_ancestors_plain", lambda _path: None)
    assert _publication._validated_build_staging_dir(cache, valid) == valid.resolve()
    invalid = (
        tmp_path / f"cache.build-tmp-{'a' * 32}",
        cache.with_name(f"wrong.build-tmp-{'a' * 32}"),
        cache.with_name(f"cache.build-tmp-{'a' * 31}"),
        cache.with_name(f"cache.build-tmp-{'a' * 33}"),
        cache.with_name(f"cache.build-tmp-{'g' * 32}"),
    )
    for candidate in invalid:
        with pytest.raises(ValueError):
            _publication._validated_build_staging_dir(cache, candidate)


def test_validate_prepared_rejects_changed_fingerprint_by_both_lexical_directions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, cache, _unused, config = _source(tmp_path), tmp_path / "cache", None, _config()
    staging = cache.with_name(f"cache.build-tmp-{'a' * 32}")
    prepared = _prepared(data, cache, staging, config)
    monkeypatch.setattr(
        _source_proof, "_data_file_signature", lambda _path: prepared.data_file_signature
    )
    for fingerprint in ("0" * 64, "f" * 64):
        altered = replace(prepared, schema_fingerprint=fingerprint)
        with pytest.raises(ValueError, match="fingerprint"):
            _cache._validate_prepared_cache(altered, config)


def test_validate_prepared_requires_exact_artifact_set_and_plain_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, config = _source(tmp_path), _config()
    cache = tmp_path / "cache"
    staging = tmp_path / f"cache.build-tmp-{'b' * 32}"
    staging.mkdir()
    prepared = _prepared(data, cache, staging, config)
    monkeypatch.setattr(
        _source_proof, "_data_file_signature", lambda _path: prepared.data_file_signature
    )
    monkeypatch.setattr(_cache, "_cache_bundle_failure_in_place", lambda *_args: None)
    meta = {**prepared.summary, "data_file": prepared.data_file_signature}
    monkeypatch.setattr(_cache, "_read_per_port_cache_meta_unlocked", lambda _path: meta)
    for names in (("meta.json",), ("meta.json", "root.parquet", "extra")):
        for child in tuple(staging.iterdir()):
            child.unlink()
        for name in names:
            (staging / name).write_bytes(b"x")
        with pytest.raises(RuntimeError, match="artifacts"):
            _cache._validate_prepared_cache(prepared, config)
    for child in tuple(staging.iterdir()):
        child.unlink()
    (staging / "meta.json").write_bytes(b"x")
    (staging / "root.parquet").write_bytes(b"x")
    validated_cache, validated_staging, validated_meta = _cache._validate_prepared_cache(
        prepared, config
    )
    assert (validated_cache, validated_staging, validated_meta) == (cache, staging, meta)
    monkeypatch.setattr(
        _publication,
        "_is_reparse_point",
        lambda stat_result: stat_result.st_size == 1,
    )
    with pytest.raises(RuntimeError, match="plain regular"):
        _cache._validate_prepared_cache(prepared, config)


def test_prepare_dispatches_parallel_only_for_multiple_ranges_and_rechecks_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, config, cache = _source(tmp_path), _config(), tmp_path / "a" / "b" / "cache"
    calls: list[tuple[str, Any]] = []
    signature = {"proof": "same"}
    monkeypatch.setattr(
        _source_proof,
        "_data_file_signature",
        lambda _path, **kw: calls.append(("sig", kw)) or signature,
    )
    monkeypatch.setattr(_cache, "_cache_is_valid_under_external_lock", lambda *_a, **_k: False)
    monkeypatch.setattr(_cache, "_cache_manifest_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(
        _writer,
        "_write_tables_in_parallel",
        lambda *_a: calls.append(("parallel", None)) or ([], _records.ShredSkipStats()),
    )
    monkeypatch.setattr(
        _writer,
        "_write_tables_streaming",
        lambda *_a: calls.append(("stream", None)) or ([], _records.ShredSkipStats()),
    )
    for ranges, expected in ([(0, 1), (1, 2)], "parallel"), ([(0, 1)], "stream"):
        calls.clear()
        monkeypatch.setattr(_records, "_should_shred_in_parallel", lambda _path: True)
        monkeypatch.setattr(_records, "_jsonl_byte_ranges", lambda *_a, ranges=ranges: ranges)
        staging = cache.with_name(f"cache.build-tmp-{'a' * 31}{len(ranges)}")
        result = _cache.prepare_per_port_cache(data, config, cache, staging_dir=staging)
        assert any(name == expected for name, _value in calls)
        assert calls == [
            ("sig", {"rebind_persisted_proofs": False}),
            (expected, None),
            ("sig", {"rebind_persisted_proofs": False}),
        ]
        assert Path(result.staging_dir or "").parent.exists()
        _cache._remove_prepared_staging(Path(result.staging_dir or ""))


def test_commit_requires_lock_and_logs_only_actual_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, cache, staging, config = (
        _source(tmp_path),
        tmp_path / "cache",
        tmp_path / "stage",
        _config(),
    )
    prepared = _prepared(data, cache, staging, config)
    with pytest.raises(RuntimeError, match="parent-owned"):
        _cache.commit_prepared_per_port_cache(prepared, config)
    meta = prepared.summary
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(_cache, "_validate_prepared_cache", lambda *_a: (cache, staging, meta))
    monkeypatch.setattr(_publication, "_swap_dir_into_place", lambda *_a: None)
    monkeypatch.setattr(
        _cache.logger, "warning", lambda event, **fields: events.append((event, fields))
    )
    monkeypatch.setattr(
        _cache.logger, "info", lambda event, **fields: events.append((event, fields))
    )
    with _publication.per_port_cache_publication_lock(cache):
        summary = _cache.commit_prepared_per_port_cache(prepared, config)
    assert summary["skipped"] == {"records": 0, "rows_by_table": {}}
    assert [event for event, _fields in events if event == "json_shred_records_skipped"] == []
    built = next(fields for event, fields in events if event == "json_shred_built")
    assert built["fingerprint"] == prepared.schema_fingerprint[:8]

    events.clear()
    rows_only = {**meta, "skipped": {"rows_by_table": {"root": 2}}}
    monkeypatch.setattr(_cache, "_validate_prepared_cache", lambda *_a: (cache, staging, rows_only))
    with _publication.per_port_cache_publication_lock(cache):
        _cache.commit_prepared_per_port_cache(prepared, config)
    warning = next(fields for event, fields in events if event == "json_shred_records_skipped")
    assert warning["skipped_records"] == 0
    assert warning["skipped_rows_by_table"] == {"root": 2}


def test_unlocked_validity_rejects_lexical_meta_mismatches_before_source_or_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, cache = _config(), tmp_path / "cache"
    with monkeypatch.context() as malformed_config:
        malformed_config.setattr(
            _shred,
            "_v2_fingerprint",
            lambda _cfg: (_ for _ in ()).throw(ApiInputSchemaError("malformed")),
        )
        assert not _cache._is_per_port_cache_valid_unlocked(
            cache, config, data_path="x", data_file_signature={}
        )

    fingerprint = _shred._v2_fingerprint(config)
    meta = {"schema_mode": "v2", "schema_fingerprint": fingerprint}
    monkeypatch.setattr(_cache, "_read_per_port_cache_meta_unlocked", lambda _path: meta)
    monkeypatch.setattr(
        _source_proof,
        "_data_file_signature",
        lambda _path: (_ for _ in ()).throw(AssertionError("too late")),
    )
    for key, values in (
        ("schema_mode", ("v1", "v3")),
        ("schema_fingerprint", ("0" * 64, "f" * 64)),
    ):
        for value in values:
            meta[key] = value
            assert not _cache._is_per_port_cache_valid_unlocked(
                cache, config, data_path="x", data_file_signature=None
            )
        meta[key] = "v2" if key == "schema_mode" else fingerprint
    calls: list[bool] = []
    monkeypatch.setattr(_cache, "_cache_meta_matches_config_and_source", lambda *_a, **_k: True)
    monkeypatch.setattr(_shred, "_emitting_table_specs", lambda _cfg: ())
    monkeypatch.setattr(
        _cache,
        "_probe_cache_bundle",
        lambda *_a, **kw: calls.append(kw["retain_snapshots"]) or ({}, None),
    )
    assert _cache._is_per_port_cache_valid_unlocked(
        cache, config, data_path="x", data_file_signature={}
    )
    assert calls == [False]


def test_load_projection_cache_plausibility_and_probe_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, config = _source(tmp_path), _config(columns=("id", "other"))
    import haute._json_flatten as flatten

    monkeypatch.setattr(
        flatten,
        "_json_cache_dir",
        lambda _data, layer: tmp_path / layer,
    )
    # Empty logical projection keeps the first declared physical carrier and rows.
    direct_calls: list[Any] = []
    monkeypatch.setattr(_cache, "_read_per_port_cache_meta_unlocked", lambda _path: None)
    monkeypatch.setattr(
        _writer,
        "_shred_data_file_to_direct_spill",
        lambda _p, _c, specs, _d: (
            direct_calls.append(specs)
            or ({"root": pl.LazyFrame({"id": [1, 3]})}, _records.ShredSkipStats())
        ),
    )
    frame = _cache.load_v2_api_source(str(data), config, port_columns={"root": frozenset()})[
        "root"
    ].collect()
    assert direct_calls[0][0].columns[0][0] == "id"
    assert frame.height == 2
    _cache.load_v2_api_source(str(data), config, port_columns={"root": frozenset({"other"})})
    assert direct_calls[1][0].columns[0][0] == "other"

    fingerprint = _shred._v2_fingerprint(config)
    meta = {"schema_mode": "v2", "schema_fingerprint": fingerprint}
    hashes = 0
    monkeypatch.setattr(_cache, "_read_per_port_cache_meta_unlocked", lambda _path: meta)

    def unexpected_signature(_path: Path) -> dict[str, str]:
        nonlocal hashes
        hashes += 1
        return {"proof": "unexpected"}

    monkeypatch.setattr(_source_proof, "_data_file_signature", unexpected_signature)
    # Lower/higher plausible-metadata misses never hash; exact candidates hash once.
    for key, value in (
        ("schema_mode", "v1"),
        ("schema_mode", "v3"),
        ("schema_fingerprint", "0" * 64),
        ("schema_fingerprint", "f" * 64),
    ):
        meta[key] = value
        monkeypatch.setattr(
            _writer,
            "_shred_data_file_to_direct_spill",
            lambda *_a: ({"root": pl.LazyFrame({"id": []})}, _records.ShredSkipStats()),
        )
        _cache.load_v2_api_source(str(data), config)
        meta[key] = "v2" if key == "schema_mode" else fingerprint
    assert hashes == 0

    def signature(_path: Path) -> dict[str, str]:
        nonlocal hashes
        hashes += 1
        return {"proof": "same"}

    monkeypatch.setattr(_source_proof, "_data_file_signature", signature)
    matches = 0

    def metadata_matches(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal matches
        matches += 1
        return matches == 2

    monkeypatch.setattr(_cache, "_cache_meta_matches_config_and_source", metadata_matches)
    probe_retain: list[bool] = []
    monkeypatch.setattr(
        _cache,
        "_probe_cache_bundle",
        lambda *_a, **kw: (
            probe_retain.append(kw["retain_snapshots"])
            or ({"root": pl.LazyFrame({"id": [1]})}, None)
        ),
    )
    assert _cache.load_v2_api_source(str(data), config)["root"].collect().height == 1
    assert hashes == 1 and matches == 2 and probe_retain == [True]


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

    summaries, _stats = _writer._write_tables_in_parallel(
        tmp_path / "source.jsonl", config, specs, staging, [(0, 1)]
    )

    assert len(summaries) == 1 and summaries[0]["row_count"] == 1
    assert shutdowns == [(True, True)]
    complete = next(fields for event, fields in events if event == "json_shred_parallel_complete")
    assert complete["duration_seconds"] == 2.0


def test_parallel_inference_merges_in_order_shuts_down_and_logs_elapsed_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _inference._InferenceState(), _inference._InferenceState()
    first.walk({"value": 1})
    second.walk({"value": 2.5})
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

    merged = _inference._infer_jsonl_in_parallel(tmp_path / "source.jsonl", [(0, 1), (1, 2)])

    expected = _inference._InferenceState()
    expected.walk({"value": 1})
    expected.walk({"value": 2.5})
    assert _inference._assemble_inference_schema(merged) == _inference._assemble_inference_schema(
        expected
    )
    assert shutdowns == [(True, True)]
    complete = next(
        fields for event, fields in events if event == "json_schema_infer_parallel_complete"
    )
    assert complete["duration_seconds"] == 3.0


def test_swap_into_absent_live_dir_removes_staging_when_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no live generation to restore, a failed publish rename must still
    remove the staged build instead of leaking it beside the cache."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "root.parquet").write_bytes(b"payload")
    live = tmp_path / "live"

    def _reject_rename(_source: Path, _target: Path) -> None:
        raise PermissionError("publish window closed")

    monkeypatch.setattr(_publication, "_rename_dir_with_retry", _reject_rename)

    with pytest.raises(PermissionError, match="publish window closed"):
        _publication._swap_dir_into_place(staging, live)

    assert not staging.exists()
    assert not live.exists()


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
