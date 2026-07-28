"""Provider dispatch and shared-snapshot integration for Data Input."""

from __future__ import annotations

import gc
import sqlite3
from contextlib import closing
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._database_io import DatabaseConfigError
from haute._execution_context import ExecutionProfile
from haute._input_providers import (
    build_input_snapshot,
    resolve_data_input,
    source_cache_identity,
    source_signature,
)
from haute._polars_io_registry import PolarsIoConfigError
from haute._source_cache import SourceCacheStore


def test_file_snapshot_is_offline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id,value\n1,a\n2,b\n", encoding="utf-8")
    config = {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": "input.csv",
        "arguments": {"schema": {"id": "int64", "value": "str"}},
    }
    expected = pl.DataFrame({"id": [1, 2], "value": ["a", "b"]})
    store = SourceCacheStore(tmp_path)
    build_input_snapshot(
        config,
        store=store,
        base_dir=tmp_path,
        profile=ExecutionProfile.LAZY_SINK,
    )

    source.unlink()
    cached = resolve_data_input(
        config,
        store=store,
        base_dir=tmp_path,
    ).collect()
    assert_frame_equal(cached, expected)


def test_missing_snapshot_is_reported_as_a_config_error(tmp_path: Path) -> None:
    config = {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": "input.csv",
        "arguments": {"schema": {"id": "int64"}},
    }

    with pytest.raises(PolarsIoConfigError, match="^input_snapshot_missing:"):
        resolve_data_input(config, store=SourceCacheStore(tmp_path), base_dir=tmp_path)


def test_direct_parquet_reads_the_anchored_source_without_a_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "input.parquet"
    pl.DataFrame({"id": [1]}).write_parquet(source)
    config = {
        "inputType": "file",
        "format": "parquet",
        "path": "input.parquet",
    }

    result = resolve_data_input(
        config,
        store=SourceCacheStore(tmp_path),
        base_dir=tmp_path,
    ).collect()
    assert result.to_dicts() == [{"id": 1}]
    with pytest.raises(PolarsIoConfigError, match="does not support snapshot builds"):
        build_input_snapshot(config, store=SourceCacheStore(tmp_path), base_dir=tmp_path)


def test_eager_only_file_snapshot_requires_admitted_eager_profile(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    source.write_text('[{"id": 1}]', encoding="utf-8")
    config = {
        "inputType": "file",
        "format": "json",
        "mode": "read",
        "path": "input.json",
    }
    store = SourceCacheStore(tmp_path)

    with pytest.raises(PolarsIoConfigError, match="admitted-eager"):
        build_input_snapshot(
            config,
            store=store,
            base_dir=tmp_path,
            profile=ExecutionProfile.LAZY_SINK,
        )

    build_input_snapshot(
        config,
        store=store,
        base_dir=tmp_path,
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    assert resolve_data_input(config, store=store, base_dir=tmp_path).collect()["id"].to_list() == [
        1
    ]


def test_inline_snapshot_builds_resolves_and_redacts_records(tmp_path: Path) -> None:
    config = {
        "inputType": "inline",
        "format": "records",
        "records": [{"customer_secret": "top-secret-value", "id": 1}],
    }
    changed_records = {
        **config,
        "records": [{"customer_secret": "top-secret-value", "id": 2}],
    }
    identity = source_cache_identity(config)
    assert identity.digest != source_cache_identity(changed_records).digest
    assert b"customer_secret" not in identity.canonical_bytes
    assert b"top-secret-value" not in identity.canonical_bytes

    store = SourceCacheStore(tmp_path)
    build_input_snapshot(config, store=store)
    assert resolve_data_input(config, store=store).collect()["id"].to_list() == [1]


def test_database_uses_bounded_sqlite_snapshot_and_cached_read_is_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "pricing.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE policies (id INTEGER, value TEXT)")
        connection.executemany(
            "INSERT INTO policies VALUES (?, ?)",
            [(1, "a"), (2, "b"), (3, "c")],
        )
        connection.commit()
    monkeypatch.setenv("HAUTE_TEST_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    config = {
        "inputType": "database",
        "format": "database",
        "connection": "HAUTE_TEST_DATABASE_URL",
        "query": "SELECT id, value FROM policies ORDER BY id",
        "arguments": {"batch_size": 1},
    }
    store = SourceCacheStore(tmp_path)
    build_input_snapshot(config, store=store, base_dir=tmp_path)

    database.unlink()
    out = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    assert out.to_dicts() == [
        {"id": 1, "value": "a"},
        {"id": 2, "value": "b"},
        {"id": 3, "value": "c"},
    ]


def test_empty_database_query_publishes_schema_bearing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "empty.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE policies (id INTEGER, value TEXT)")
        connection.commit()
    monkeypatch.setenv("HAUTE_TEST_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    config = {
        "inputType": "database",
        "format": "database",
        "connection": "HAUTE_TEST_DATABASE_URL",
        "query": "SELECT id, value FROM policies",
    }
    store = SourceCacheStore(tmp_path)

    generation = build_input_snapshot(config, store=store, base_dir=tmp_path)
    out = resolve_data_input(config, store=store, base_dir=tmp_path).collect()

    assert generation.metadata.row_count == 0
    assert out.height == 0
    assert out.columns == ["id", "value"]
    assert out.schema == {"id": pl.Int64, "value": pl.String}


def test_database_snapshot_uses_one_declared_schema_across_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "mixed.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE policies (id INTEGER, value TEXT)")
        connection.executemany(
            "INSERT INTO policies VALUES (?, ?)",
            [(1, None), (2, "rated")],
        )
        connection.commit()
    monkeypatch.setenv("HAUTE_TEST_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    config = {
        "inputType": "database",
        "format": "database",
        "connection": "HAUTE_TEST_DATABASE_URL",
        "query": "SELECT id, value FROM policies ORDER BY id",
        "arguments": {"batch_size": 1},
    }
    store = SourceCacheStore(tmp_path)

    build_input_snapshot(config, store=store, base_dir=tmp_path)

    out = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    assert out.schema == {"id": pl.Int64, "value": pl.String}
    assert out.to_dicts() == [{"id": 1, "value": None}, {"id": 2, "value": "rated"}]


def test_database_snapshot_rejects_missing_sqlite_without_creating_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing.sqlite"
    config = {
        "inputType": "database",
        "format": "database",
        "uri": f"sqlite:///{database.as_posix()}",
        "query": "SELECT id FROM policies",
    }

    with pytest.raises(DatabaseConfigError, match="does not exist"):
        build_input_snapshot(config, store=SourceCacheStore(tmp_path), base_dir=tmp_path)

    assert not database.exists()


def test_database_snapshot_uses_runtime_storage_class_for_datetime(
    tmp_path: Path,
) -> None:
    database = tmp_path / "datetime.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE events (occurred_at DATETIME)")
        connection.executemany("INSERT INTO events VALUES (?)", [(1_700_000_000,), (None,)])
        connection.commit()
    config = {
        "inputType": "database",
        "format": "database",
        "uri": f"sqlite:///{database.as_posix()}",
        "query": "SELECT occurred_at FROM events",
        "arguments": {"batch_size": 1},
    }
    store = SourceCacheStore(tmp_path)

    build_input_snapshot(config, store=store, base_dir=tmp_path)

    out = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    assert out.schema == {"occurred_at": pl.Int64}
    assert out["occurred_at"].to_list() == [1_700_000_000, None]


def test_database_snapshot_rejects_incompatible_runtime_storage_classes_before_output(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mixed-affinity.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE values_table (value INTEGER)")
        connection.executemany("INSERT INTO values_table VALUES (?)", [(1,), ("not-an-int",)])
        connection.commit()
    config = {
        "inputType": "database",
        "format": "database",
        "uri": f"sqlite:///{database.as_posix()}",
        "query": "SELECT value FROM values_table",
        "arguments": {"batch_size": 1},
    }
    store = SourceCacheStore(tmp_path)

    with pytest.raises(DatabaseConfigError, match="incompatible SQLite storage classes"):
        build_input_snapshot(config, store=store, base_dir=tmp_path)

    assert store.status(source_cache_identity(config, base_dir=tmp_path)).state == "missing"


def test_empty_database_expression_fails_with_typed_schema_error(
    tmp_path: Path,
) -> None:
    database = tmp_path / "empty-expression.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE values_table (value INTEGER)")
        connection.commit()
    config = {
        "inputType": "database",
        "format": "database",
        "uri": f"sqlite:///{database.as_posix()}",
        "query": "SELECT value + 1 AS next_value FROM values_table",
    }

    with pytest.raises(DatabaseConfigError, match="cannot prove a stable Arrow type"):
        build_input_snapshot(config, store=SourceCacheStore(tmp_path), base_dir=tmp_path)


def test_raw_sqlite_uri_is_resolved_relative_to_pipeline_base(tmp_path: Path) -> None:
    database = tmp_path / "pricing.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE policies (id INTEGER)")
        connection.execute("INSERT INTO policies VALUES (7)")
        connection.commit()
    config = {
        "inputType": "database",
        "format": "database",
        "uri": "sqlite:///pricing.sqlite",
        "query": "SELECT id FROM policies",
    }
    store = SourceCacheStore(tmp_path)

    build_input_snapshot(config, store=store, base_dir=tmp_path)

    assert resolve_data_input(config, store=store, base_dir=tmp_path).collect().to_dicts() == [
        {"id": 7}
    ]


def test_non_snapshot_execution_is_rejected_before_connector_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_TEST_DATABASE_URL", "sqlite:///does-not-matter.sqlite")
    with pytest.raises(PolarsIoConfigError, match="input_snapshot_missing"):
        resolve_data_input(
            {
                "inputType": "database",
                "format": "database",
                "connection": "HAUTE_TEST_DATABASE_URL",
                "query": "SELECT 1",
            }
        )


def test_databricks_identity_is_query_distinct_and_contains_no_resolved_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_TOKEN", "top-secret-token")
    base = {
        "inputType": "databricks",
        "http_path": "/sql/1.0/warehouses/abc",
        "table": "main.pricing.policies",
    }
    whole_table = source_cache_identity(base)
    filtered = source_cache_identity({**base, "query": "SELECT id"})

    assert whole_table.digest != filtered.digest
    assert b"top-secret-token" not in whole_table.canonical_bytes
    assert whole_table.payload["descriptor"]["token_ref"] == "DATABRICKS_TOKEN"


def test_databricks_identity_excludes_fetch_batch_size() -> None:
    base = {
        "inputType": "databricks",
        "http_path": "/sql/1.0/warehouses/abc",
        "table": "main.pricing.policies",
    }

    small_batches = source_cache_identity({**base, "arguments": {"batch_size": 1}})
    large_batches = source_cache_identity({**base, "arguments": {"batch_size": 100_000}})

    assert small_batches.digest == large_batches.digest
    assert "arguments" not in small_batches.payload["descriptor"]


def test_identity_excludes_post_snapshot_code(tmp_path: Path) -> None:
    common = {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": "data/input.csv",
        "arguments": {"schema": {"id": "int64"}},
    }
    head = source_cache_identity(
        {**common, "code": "df = df.head(1)"},
        base_dir=tmp_path,
    )
    tail = source_cache_identity(
        {**common, "code": "df = df.tail(1)"},
        base_dir=tmp_path,
    )
    assert head.digest == tail.digest
    assert "code" not in head.payload["descriptor"]
    assert "cacheMode" not in head.payload["descriptor"]


def test_lakehouse_freshness_is_unknown_without_a_provider_version_token(
    tmp_path: Path,
) -> None:
    config = {
        "inputType": "lakehouse",
        "format": "delta",
        "mode": "scan",
        "path": "lake/policies",
    }

    assert source_signature(config, base_dir=tmp_path) is None


def test_snapshot_reader_lease_survives_refresh_until_execution_context_closes(
    tmp_path: Path,
) -> None:
    from haute._execution_context import ExecutionContext

    source = tmp_path / "input.ndjson"
    pl.DataFrame({"id": [1]}).write_ndjson(source)
    config = {
        "inputType": "file",
        "format": "ndjson",
        "mode": "scan",
        "path": "input.ndjson",
    }
    store = SourceCacheStore(tmp_path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    context = ExecutionContext(
        operation="snapshot-lease-test",
        profile=ExecutionProfile.LAZY_SINK,
    )

    with context.stage("resolve"):
        leased_frame = resolve_data_input(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [2]}).write_ndjson(source)
    second = build_input_snapshot(
        config,
        store=store,
        base_dir=tmp_path,
        refresh=True,
    )

    assert second.generation_id != first.generation_id
    assert first.data_path.exists()
    assert leased_frame.collect()["id"].to_list() == [1]
    context.release_admission()
    assert not first.data_path.parent.exists()


def test_derived_snapshot_plan_retains_lease_without_execution_context(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.ndjson"
    pl.DataFrame({"id": [1]}).write_ndjson(source)
    config = {
        "inputType": "file",
        "format": "ndjson",
        "mode": "scan",
        "path": "input.ndjson",
    }
    store = SourceCacheStore(tmp_path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)

    derived = resolve_data_input(config, store=store, base_dir=tmp_path).select("id")
    gc.collect()
    pl.DataFrame({"id": [2]}).write_ndjson(source)
    build_input_snapshot(config, store=store, base_dir=tmp_path, refresh=True)

    assert first.data_path.exists()
    assert derived.collect()["id"].to_list() == [1]

    del derived
    gc.collect()
    assert not first.data_path.parent.exists()
