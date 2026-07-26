"""Focused tests for bounded Databricks snapshot acquisition."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from haute._databricks_io import (
    DatabricksConfigError,
    DatabricksSnapshotBuilder,
    FetchIntegrityError,
    _assert_no_rows_lost_after_retry,
    _canonical_table,
    _get_credentials,
    _iter_databricks_batches,
    _validate_select_clause,
)
from haute._execution_context import ExecutionProfile
from haute._source_cache import SourceCacheBuildContext, SourceCacheIdentity, SourceCacheStore


def test_get_credentials_normalises_host_and_uses_node_http_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example/")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    assert _get_credentials("/sql/warehouse") == ("workspace.example", "token", "/sql/warehouse")


@pytest.mark.parametrize("missing", ["DATABRICKS_HOST", "DATABRICKS_TOKEN"])
def test_get_credentials_reports_missing_values(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    monkeypatch.delenv(missing)
    with pytest.raises(DatabricksConfigError, match=missing):
        _get_credentials("/sql/warehouse")


def test_get_credentials_does_not_advertise_removed_http_path_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/ignored")

    with pytest.raises(DatabricksConfigError) as exc_info:
        _get_credentials(None)

    assert "DATABRICKS_HTTP_PATH" not in str(exc_info.value)
    assert "http_path on the Data Input node" in str(exc_info.value)


@pytest.mark.parametrize(
    "query", ["DELETE FROM x", "SELECT x;", "SELECT x -- comment", "SELECT x FROM y"]
)
def test_validate_select_clause_rejects_unsafe_sql(query: str) -> None:
    with pytest.raises(ValueError):
        _validate_select_clause(query)


def test_validate_select_clause_accepts_projection() -> None:
    _validate_select_clause(" SELECT policy_id, premium WHERE premium > 0 ")


def test_canonical_table_ignores_case_and_quotes() -> None:
    assert _canonical_table("`Catalog`.`Schema`.`Table`") == "catalog.schema.table"


def test_snapshot_builder_validates_config_and_delegates() -> None:
    builder = DatabricksSnapshotBuilder({"table": "cat.sch.tbl", "http_path": "/sql/wh"})
    context = MagicMock()
    with patch("haute._databricks_io._iter_databricks_batches", return_value=iter(())) as batches:
        assert list(builder.build(context)) == []
    batches.assert_called_once_with(
        table="cat.sch.tbl", http_path="/sql/wh", query=None, batch_size=100_000, context=context
    )


@pytest.mark.parametrize(
    "config", [{}, {"table": "bad", "http_path": "/sql/wh"}, {"table": "cat.sch.tbl"}]
)
def test_snapshot_builder_rejects_incomplete_config(config: dict[str, object]) -> None:
    with pytest.raises((ValueError, DatabricksConfigError)):
        DatabricksSnapshotBuilder(config)


class _Cursor:
    def __init__(self, batches: list[object]) -> None:
        self.batches = iter(batches)
        self.rownumber = 0
        self.executed: list[str] = []

    def execute(self, query: str) -> None:
        self.executed.append(query)

    def fetchmany_arrow(self, _batch_size: int) -> pa.Table:
        result = next(self.batches)
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, pa.Table)
        self.rownumber += result.num_rows
        return result

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _stream(cursor: _Cursor, context: MagicMock) -> list[pa.Table]:
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.__enter__.return_value = connection
    with (
        patch("databricks.sql.connect", return_value=connection),
        patch("haute._databricks_io._get_credentials", return_value=("host", "token", "/wh")),
        patch("haute._databricks_io.time.sleep"),
    ):
        return list(
            _iter_databricks_batches(
                table="cat.sch.tbl", http_path="/wh", query=None, batch_size=2, context=context
            )
        )


def test_iter_batches_streams_and_checks_cancellation() -> None:
    cursor = _Cursor([pa.table({"x": [1, 2]}), pa.table({"x": []})])
    context = MagicMock()
    batches = _stream(cursor, context)
    assert [batch.num_rows for batch in batches] == [2]
    assert cursor.executed == ["SELECT * FROM cat.sch.tbl"]
    assert context.checkpoint.call_count == 4


def test_iter_batches_checks_cancellation_before_connect_or_execute() -> None:
    cursor = _Cursor([pa.table({"x": [1]})])
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.__enter__.return_value = connection
    context = MagicMock()
    context.checkpoint.side_effect = RuntimeError("cancelled")

    with (
        patch("databricks.sql.connect", return_value=connection) as connect,
        patch("haute._databricks_io._get_credentials", return_value=("host", "token", "/wh")),
    ):
        with pytest.raises(RuntimeError, match="cancelled"):
            list(
                _iter_databricks_batches(
                    table="cat.sch.tbl",
                    http_path="/wh",
                    query=None,
                    batch_size=2,
                    context=context,
                )
            )

    connect.assert_not_called()
    assert cursor.executed == []


def test_iter_batches_retries_transient_failure() -> None:
    cursor = _Cursor([RuntimeError("transient"), pa.table({"x": [1]}), pa.table({"x": []})])
    # Simulate connector row accounting after the successful retry.
    cursor.rownumber = 0
    assert [batch.num_rows for batch in _stream(cursor, MagicMock())] == [1]


def test_iter_batches_rejects_schemaless_empty_result() -> None:
    with pytest.raises(FetchIntegrityError, match="no result schema"):
        _stream(_Cursor([pa.table({})]), MagicMock())


def test_retry_integrity_detects_lost_rows() -> None:
    with pytest.raises(FetchIntegrityError, match="lost rows"):
        _assert_no_rows_lost_after_retry(table="cat.sch.tbl", rows_received=3, rows_consumed=4)


def test_retry_integrity_accepts_matching_row_count() -> None:
    _assert_no_rows_lost_after_retry(table="cat.sch.tbl", rows_received=3, rows_consumed=3)


def test_snapshot_builder_publishes_through_source_cache_store(tmp_path: Path) -> None:
    cursor = _Cursor([pa.table({"x": [1, 2]}), pa.table({"x": []})])
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.__enter__.return_value = connection
    context = SourceCacheBuildContext(
        profile=ExecutionProfile.LAZY_SINK,
        build_class="bounded",
    )
    identity = SourceCacheIdentity(
        provider="databricks",
        descriptor={"http_path": "/wh", "table": "cat.sch.tbl"},
    )

    with (
        patch("databricks.sql.connect", return_value=connection),
        patch("haute._databricks_io._get_credentials", return_value=("host", "token", "/wh")),
    ):
        generation = SourceCacheStore(tmp_path).build(
            identity,
            DatabricksSnapshotBuilder({"table": "cat.sch.tbl", "http_path": "/wh"}),
            context=context,
        )

    assert generation.lazy_frame.collect().to_dicts() == [{"x": 1}, {"x": 2}]
