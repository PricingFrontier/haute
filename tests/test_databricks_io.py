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
    _connection_settings,
    _iter_databricks_batches,
    _validate_select_clause,
)
from haute._execution_context import ExecutionProfile
from haute._source_cache import SourceCacheBuildContext, SourceCacheIdentity, SourceCacheStore


@pytest.fixture()
def _no_ambient_databricks_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("_no_ambient_databricks_env")
def test_connection_settings_normalises_host_and_uses_node_http_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example/")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    assert _connection_settings("/sql/warehouse") == (
        "workspace.example",
        {"access_token": "token"},
        "/sql/warehouse",
    )


@pytest.mark.usefixtures("_no_ambient_databricks_env")
@pytest.mark.parametrize("missing", ["DATABRICKS_HOST", "DATABRICKS_TOKEN"])
def test_connection_settings_reports_missing_values(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    monkeypatch.delenv(missing)
    with pytest.raises(DatabricksConfigError, match=missing):
        _connection_settings("/sql/warehouse")


@pytest.mark.usefixtures("_no_ambient_databricks_env")
def test_connection_settings_does_not_advertise_removed_http_path_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/ignored")

    with pytest.raises(DatabricksConfigError) as exc_info:
        _connection_settings(None)

    assert "DATABRICKS_HTTP_PATH" not in str(exc_info.value)
    assert "http_path on the Data Input node" in str(exc_info.value)


@pytest.mark.usefixtures("_no_ambient_databricks_env")
def test_connection_settings_falls_back_to_service_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")

    host, auth_kwargs, http_path = _connection_settings("/sql/warehouse")

    assert host == "workspace.example"
    assert http_path == "/sql/warehouse"
    assert "access_token" not in auth_kwargs
    assert callable(auth_kwargs["credentials_provider"])


@pytest.mark.usefixtures("_no_ambient_databricks_env")
def test_connection_settings_prefers_explicit_token_over_service_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")

    _, auth_kwargs, _ = _connection_settings("/sql/warehouse")

    assert auth_kwargs == {"access_token": "token"}


@pytest.mark.usefixtures("_no_ambient_databricks_env")
def test_connection_settings_rejects_incomplete_service_principal_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-client")

    with pytest.raises(DatabricksConfigError) as exc_info:
        _connection_settings("/sql/warehouse")

    message = str(exc_info.value)
    assert "DATABRICKS_TOKEN" in message
    assert "DATABRICKS_CLIENT_SECRET" in message
    assert "sp-client" not in message


@pytest.mark.usefixtures("_no_ambient_databricks_env")
def test_service_principal_provider_builds_sdk_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import types

    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")

    recorded: dict[str, object] = {}
    sentinel = object()

    def fake_config(**kwargs: object) -> object:
        recorded.update(kwargs)
        return "config"

    stub = types.ModuleType("databricks.sdk.core")
    stub.Config = fake_config  # type: ignore[attr-defined]
    stub.oauth_service_principal = lambda config: sentinel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "databricks.sdk.core", stub)

    _, auth_kwargs, _ = _connection_settings("/sql/warehouse")
    assert auth_kwargs["credentials_provider"]() is sentinel
    assert recorded == {
        "host": "https://workspace.example",
        "client_id": "sp-client",
        "client_secret": "sp-secret",
    }


@pytest.mark.usefixtures("_no_ambient_databricks_env")
def test_service_principal_provider_reports_missing_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")
    # A None entry makes `from databricks.sdk.core import …` raise ImportError
    # deterministically, whether or not the SDK is installed locally.
    monkeypatch.setitem(sys.modules, "databricks.sdk.core", None)

    _, auth_kwargs, _ = _connection_settings("/sql/warehouse")
    with pytest.raises(DatabricksConfigError, match=r"haute\[databricks\]"):
        auth_kwargs["credentials_provider"]()


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
        patch(
            "haute._databricks_io._connection_settings",
            return_value=("host", {"access_token": "token"}, "/wh"),
        ),
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
        patch(
            "haute._databricks_io._connection_settings",
            return_value=("host", {"access_token": "token"}, "/wh"),
        ),
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
        _assert_no_rows_lost_after_retry(
            table="cat.sch.tbl", rows_received=3, rows_consumed=4, rows_consumed_previously=None
        )


def test_retry_integrity_accepts_matching_row_count() -> None:
    _assert_no_rows_lost_after_retry(
        table="cat.sch.tbl", rows_received=3, rows_consumed=3, rows_consumed_previously=None
    )
    _assert_no_rows_lost_after_retry(
        table="cat.sch.tbl", rows_received=5, rows_consumed=5, rows_consumed_previously=3
    )


def test_retry_integrity_fails_closed_on_a_missing_counter() -> None:
    """``None`` is not a matching count: an unsupported counter never passes."""
    with pytest.raises(FetchIntegrityError, match="lost rows"):
        _assert_no_rows_lost_after_retry(
            table="cat.sch.tbl", rows_received=3, rows_consumed=None, rows_consumed_previously=3
        )


def test_retry_integrity_rejects_a_counter_that_goes_backwards() -> None:
    """A counter going backwards is a semantic change, reported as such and
    before the equality check, so it cannot be misread as lost rows or pass
    by coincidence."""
    with pytest.raises(FetchIntegrityError, match="went backwards from 4 to 1"):
        _assert_no_rows_lost_after_retry(
            table="cat.sch.tbl", rows_received=1, rows_consumed=1, rows_consumed_previously=4
        )


def test_iter_batches_tracks_cursor_position_across_batches_after_a_retry() -> None:
    class _ResettingCursor(_Cursor):
        """Counter that matches the first post-retry batch, then resets."""

        def fetchmany_arrow(self, batch_size: int) -> pa.Table:
            result = super().fetchmany_arrow(batch_size)
            if self.rownumber == 3:
                self.rownumber = 1
            return result

    cursor = _ResettingCursor(
        [RuntimeError("transient"), pa.table({"x": [1, 2]}), pa.table({"x": [3]})]
    )
    with pytest.raises(FetchIntegrityError, match="went backwards from 2 to 1"):
        _stream(cursor, MagicMock())


def test_real_databricks_cursor_exposes_rownumber() -> None:
    """The retry guard reads ``cursor.rownumber``; ask the real connector class.

    The fake above restates the assumption by construction, so only the
    installed ``databricks.sql.client.Cursor`` can say whether it still holds.
    Presence is checked on the class: the property is meaningless before
    ``execute`` and needs a live connection to instantiate.
    """
    try:
        from databricks.sql.client import Cursor
    except ModuleNotFoundError as exc:
        if exc.name not in {"databricks", "databricks.sql", "databricks.sql.client"}:
            raise
        pytest.skip(
            "databricks-sql-connector is not installed; the Cursor.rownumber contract "
            "is unverified in this environment"
        )

    assert hasattr(Cursor, "rownumber"), (
        "databricks.sql.client.Cursor no longer exposes rownumber: the fetch-retry "
        "integrity guard in haute._databricks_io reads it, and will now fail closed on "
        "every retried fetch. Find the connector's replacement row-position attribute."
    )


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
        patch(
            "haute._databricks_io._connection_settings",
            return_value=("host", {"access_token": "token"}, "/wh"),
        ),
    ):
        generation = SourceCacheStore(tmp_path).build(
            identity,
            DatabricksSnapshotBuilder({"table": "cat.sch.tbl", "http_path": "/wh"}),
            context=context,
        )

    assert generation.lazy_frame.collect().to_dicts() == [{"x": 1}, {"x": 2}]
