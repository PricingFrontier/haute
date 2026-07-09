"""Tests for haute._databricks_io — credential resolution and fetch_and_cache.

The existing test_databricks_cache.py covers cache_path, cache_info,
clear_cache, fetch_progress, and read_cached_table.  This file focuses on
_get_credentials() and the fetch_and_cache() flow with a mock Databricks
connector.
"""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pyarrow as pa
import pytest

from haute._databricks_io import (
    CACHE_DIR,
    DatabricksConfigError,
    _clear_fetch_progress,
    _set_fetch_progress,
    fetch_and_cache,
    fetch_progress,
)

# ---------------------------------------------------------------------------
# _get_credentials
# ---------------------------------------------------------------------------


class TestGetCredentials:
    """Credential resolution from env vars and http_path argument."""

    def test_resolves_all_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "https://my-host.cloud.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-abc123")
        monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")

        host, token, http_path = _get_credentials()
        assert host == "my-host.cloud.databricks.com"
        assert token == "dapi-abc123"
        assert http_path == "/sql/1.0/warehouses/abc"

    def test_http_path_arg_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "host.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/env-path")

        _, _, http_path = _get_credentials(http_path="/arg-path")
        assert http_path == "/arg-path"

    def test_strips_https_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.com/")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/path")

        host, _, _ = _get_credentials()
        assert host == "myhost.com"

    def test_strips_http_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "http://myhost.com/")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/path")

        host, _, _ = _get_credentials()
        assert host == "myhost.com"

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "myhost.com/")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        host, _, _ = _get_credentials(http_path="/p")
        assert host == "myhost.com"

    def test_raises_when_host_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        with pytest.raises(DatabricksConfigError, match="DATABRICKS_HOST"):
            _get_credentials(http_path="/p")

    def test_raises_when_token_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        with pytest.raises(DatabricksConfigError, match="DATABRICKS_TOKEN"):
            _get_credentials(http_path="/p")

    def test_raises_when_http_path_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        monkeypatch.delenv("DATABRICKS_HTTP_PATH", raising=False)

        with pytest.raises(DatabricksConfigError, match="http_path"):
            _get_credentials()

    def test_raises_with_multiple_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_HTTP_PATH", raising=False)

        with pytest.raises(DatabricksConfigError) as exc_info:
            _get_credentials()
        msg = str(exc_info.value)
        assert "DATABRICKS_HOST" in msg
        assert "DATABRICKS_TOKEN" in msg
        assert "http_path" in msg

    def test_bare_host_no_protocol_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._databricks_io import _get_credentials

        monkeypatch.setenv("DATABRICKS_HOST", "myhost.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        host, _, _ = _get_credentials(http_path="/p")
        assert host == "myhost.com"


# ---------------------------------------------------------------------------
# fetch_and_cache (with mocked Databricks connector)
# ---------------------------------------------------------------------------


def _make_arrow_batch(rows: int = 10) -> pa.Table:
    """Create a small PyArrow table to simulate a Databricks fetch batch."""
    return pa.table(
        {
            "id": list(range(rows)),
            "value": [float(i) * 1.5 for i in range(rows)],
        }
    )


def _empty_batch_like(batch: pa.Table) -> pa.Table:
    """Create an empty Arrow table with the same schema as *batch*."""
    return batch.schema.empty_table()


def _mock_dbsql_module(connect_fn: MagicMock) -> MagicMock:
    """Build a mock ``databricks.sql`` module with the given connect function.

    Because ``fetch_and_cache`` uses ``from databricks import sql as dbsql``
    as a local import, we must inject the mock into ``sys.modules`` so the
    import statement resolves to our mock.
    """
    mock_sql = MagicMock()
    mock_sql.connect = connect_fn
    mock_databricks = MagicMock()
    mock_databricks.sql = mock_sql
    return mock_databricks, mock_sql


def _wrap_cursor_in_conn(cursor: MagicMock) -> MagicMock:
    """Wrap a mock cursor in a context-manager-shaped mock connection."""
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def _build_mock_connector(batches: list[pa.Table]) -> tuple[MagicMock, MagicMock]:
    """Build a mock connector returning *batches* then an empty batch.

    The cursor keeps an honest ``rownumber`` (rows consumed from the result
    set), mirroring the real connector's DBAPI accounting.

    Returns (mock_databricks_module, mock_sql_module) for injection
    into sys.modules.
    """
    empty = _empty_batch_like(batches[0])
    cursor = MagicMock()
    cursor.rownumber = 0
    batch_iter = iter(batches + [empty])

    def _next_batch(size: int) -> pa.Table:
        batch = next(batch_iter)
        cursor.rownumber += batch.num_rows
        return batch

    cursor.fetchmany_arrow = MagicMock(side_effect=_next_batch)

    conn = _wrap_cursor_in_conn(cursor)
    mock_connect = MagicMock(return_value=conn)
    mock_databricks, mock_sql = _mock_dbsql_module(mock_connect)
    return mock_databricks, mock_sql, cursor


class TestFetchAndCache:
    """Tests for the full fetch_and_cache flow with mocked connector."""

    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def test_writes_parquet_and_returns_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        batch = _make_arrow_batch(5)
        mock_db, mock_sql, _ = _build_mock_connector([batch])

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            result = fetch_and_cache(
                "catalog.schema.my_table",
                http_path="/sql/1.0/wh/abc",
                project_root=tmp_path,
            )

        assert result["table"] == "catalog.schema.my_table"
        assert result["row_count"] == 5
        assert result["column_count"] == 2
        assert result["fetch_seconds"] >= 0
        assert result["size_bytes"] > 0
        assert "id" in result["columns"]
        assert "value" in result["columns"]
        # File should exist on disk
        expected_path = tmp_path / CACHE_DIR / "catalog_schema_my_table.parquet"
        assert expected_path.exists()

    def test_rejects_invalid_table_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        with pytest.raises(ValueError, match="Invalid table name"):
            fetch_and_cache(
                "DROP TABLE students; --",
                http_path="/path",
                project_root=tmp_path,
            )

    def test_rejects_single_part_table_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        with pytest.raises(ValueError, match="Invalid table name"):
            fetch_and_cache(
                "just_a_table",
                http_path="/path",
                project_root=tmp_path,
            )

    def test_clears_progress_after_fetch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        batch = _make_arrow_batch(3)
        mock_db, mock_sql, _ = _build_mock_connector([batch])

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                project_root=tmp_path,
            )

        # Progress should be cleared after successful fetch
        assert fetch_progress("cat.sch.tbl") is None

    def test_progress_cleared_on_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("connection failed")

        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        mock_connect = MagicMock(return_value=conn)
        mock_db, mock_sql = _mock_dbsql_module(mock_connect)

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            with pytest.raises(RuntimeError, match="connection failed"):
                fetch_and_cache(
                    "cat.sch.tbl",
                    http_path="/path",
                    project_root=tmp_path,
                )

        # Progress should be cleaned up even after error
        assert fetch_progress("cat.sch.tbl") is None

    def test_no_temp_file_on_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("boom")

        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        mock_connect = MagicMock(return_value=conn)
        mock_db, mock_sql = _mock_dbsql_module(mock_connect)

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            with pytest.raises(RuntimeError):
                fetch_and_cache(
                    "cat.sch.tbl",
                    http_path="/path",
                    project_root=tmp_path,
                )

        # Verify no temp files leaked — unconditional, not guarded by exists()
        assert not any(tmp_path.rglob("*.tmp")), (
            f"Temp file left behind: {list(tmp_path.rglob('*.tmp'))}"
        )

    def test_uses_custom_query(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        batch = _make_arrow_batch(2)
        mock_db, mock_sql, cursor = _build_mock_connector([batch])

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                query="SELECT id, value",
                project_root=tmp_path,
            )

        # Verify the SQL query passed to execute
        cursor.execute.assert_called_once()
        executed_sql = cursor.execute.call_args[0][0]
        assert "SELECT id, value FROM cat.sch.tbl" == executed_sql

    def test_multiple_batches(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        batch1 = _make_arrow_batch(3)
        batch2 = _make_arrow_batch(4)
        mock_db, mock_sql, _ = _build_mock_connector([batch1, batch2])

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            result = fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                project_root=tmp_path,
            )

        assert result["row_count"] == 7  # 3 + 4


# ---------------------------------------------------------------------------
# Thread-safety of fetch progress helpers
# ---------------------------------------------------------------------------


class TestFetchProgressThreadSafety:
    """Verify concurrent progress updates don't lose data."""

    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def test_concurrent_set_and_read(self) -> None:
        errors: list[str] = []
        iterations = 100

        def writer(table: str) -> None:
            for i in range(iterations):
                _set_fetch_progress(table, {"rows": i})

        def reader(table: str) -> None:
            for _ in range(iterations):
                progress = fetch_progress(table)
                if progress is not None:
                    if "rows" not in progress:
                        errors.append(f"Missing 'rows' key in {progress}")

        t1 = threading.Thread(target=writer, args=("t1",))
        t2 = threading.Thread(target=reader, args=("t1",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == [], f"Thread safety errors: {errors}"


# ---------------------------------------------------------------------------
# SQL injection prevention (_validate_select_clause)
# ---------------------------------------------------------------------------


class TestValidateSelectClause:
    """Validate that _validate_select_clause blocks dangerous SQL."""

    def test_valid_select_star(self) -> None:
        from haute._databricks_io import _validate_select_clause

        _validate_select_clause("SELECT *")

    def test_valid_select_columns(self) -> None:
        from haute._databricks_io import _validate_select_clause

        _validate_select_clause("SELECT a, b, c")

    def test_valid_select_with_where(self) -> None:
        from haute._databricks_io import _validate_select_clause

        _validate_select_clause("SELECT a, b WHERE a > 10")

    def test_valid_select_case_insensitive(self) -> None:
        from haute._databricks_io import _validate_select_clause

        _validate_select_clause("select a, b")

    def test_rejects_non_select(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="must start with SELECT"):
            _validate_select_clause("DROP TABLE students; --")

    def test_rejects_semicolon(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="semicolons"):
            _validate_select_clause("SELECT *; DROP TABLE students")

    def test_rejects_drop(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*DROP"):
            _validate_select_clause("SELECT * FROM t WHERE DROP = 1")

    def test_rejects_union(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*UNION"):
            _validate_select_clause("SELECT * UNION ALL SELECT * FROM secret")

    def test_rejects_delete(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*DELETE"):
            _validate_select_clause("SELECT 1 WHERE DELETE FROM x")

    def test_rejects_insert(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*INSERT"):
            _validate_select_clause("SELECT 1 WHERE INSERT INTO x")

    def test_rejects_update(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*UPDATE"):
            _validate_select_clause("SELECT 1 WHERE UPDATE x SET a=1")

    def test_rejects_alter(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*ALTER"):
            _validate_select_clause("SELECT 1 WHERE ALTER TABLE x")

    def test_rejects_truncate(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*TRUNCATE"):
            _validate_select_clause("SELECT 1 WHERE TRUNCATE TABLE x")

    def test_rejects_exec(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*EXEC"):
            _validate_select_clause("SELECT 1 WHERE EXEC sp_evil")

    def test_rejects_create(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*CREATE"):
            _validate_select_clause("SELECT 1 WHERE CREATE TABLE x")

    def test_rejects_grant(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*GRANT"):
            _validate_select_clause("SELECT 1 WHERE GRANT ALL")

    def test_rejects_revoke(self) -> None:
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword.*REVOKE"):
            _validate_select_clause("SELECT 1 WHERE REVOKE ALL")

    def test_fetch_rejects_dangerous_query(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fetch_and_cache validates query before executing SQL."""
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        with pytest.raises(ValueError, match="semicolons"):
            fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                query="SELECT *; DROP TABLE students",
                project_root=tmp_path,
            )

    def test_fetch_rejects_non_select_query(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fetch_and_cache rejects queries that don't start with SELECT."""
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        with pytest.raises(ValueError, match="must start with SELECT"):
            fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                query="DROP TABLE students",
                project_root=tmp_path,
            )


# ---------------------------------------------------------------------------
# Path traversal prevention (_cache_path_for)
# ---------------------------------------------------------------------------


class TestCachePathTraversal:
    """Verify _cache_path_for blocks path traversal attacks."""

    def test_slashes_replaced(self, tmp_path: Path) -> None:
        from haute._databricks_io import _cache_path_for

        p = _cache_path_for("foo/bar/baz", project_root=tmp_path)
        assert "foo_bar_baz.parquet" == p.name

    def test_backslashes_replaced(self, tmp_path: Path) -> None:
        from haute._databricks_io import _cache_path_for

        p = _cache_path_for("foo\\bar\\baz", project_root=tmp_path)
        assert "foo_bar_baz.parquet" == p.name

    def test_dots_replaced(self, tmp_path: Path) -> None:
        from haute._databricks_io import _cache_path_for

        p = _cache_path_for("cat.schema.table", project_root=tmp_path)
        assert "cat_schema_table.parquet" == p.name

    def test_traversal_with_dotdot_blocked(self, tmp_path: Path) -> None:
        """Dots in table name are replaced, so ../../etc/passwd becomes
        safe; but even if they weren't, the is_relative_to check catches it."""
        from haute._databricks_io import _cache_path_for

        # After replacement, ".." becomes "__" so it stays in cache_dir
        p = _cache_path_for("foo/../../../etc/passwd", project_root=tmp_path)
        cache_dir = (tmp_path / ".haute_cache").resolve()
        assert p.resolve().is_relative_to(cache_dir)

    def test_mixed_separators(self, tmp_path: Path) -> None:
        from haute._databricks_io import _cache_path_for

        p = _cache_path_for("a/b\\c.d", project_root=tmp_path)
        assert p.name == "a_b_c_d.parquet"


# ---------------------------------------------------------------------------
# Cache identity canonicalisation (_cache_path_for)
# ---------------------------------------------------------------------------


class TestCachePathCanonicalisation:
    """One Databricks table → one cache file, whatever the spelling.

    Databricks resolves unquoted identifiers case-insensitively, and backtick
    quoting is spelling rather than identity — so case/backtick variants of a
    single table must derive a single cache path, or the table double-caches
    on a case-sensitive deploy filesystem and clear-cache under one spelling
    misses the file written under another.
    """

    def test_case_variant_spellings_share_one_path(self, tmp_path: Path) -> None:
        from haute._databricks_io import _cache_path_for

        base = _cache_path_for("mycat.sch.tbl", project_root=tmp_path)
        assert _cache_path_for("MyCat.Sch.Tbl", project_root=tmp_path) == base
        assert _cache_path_for("MYCAT.SCH.TBL", project_root=tmp_path) == base

    def test_backtick_quoted_spelling_shares_the_path(self, tmp_path: Path) -> None:
        from haute._databricks_io import _cache_path_for

        base = _cache_path_for("mycat.sch.tbl", project_root=tmp_path)
        assert _cache_path_for("`MyCat`.`Sch`.`Tbl`", project_root=tmp_path) == base

    def test_distinct_tables_keep_distinct_paths(self, tmp_path: Path) -> None:
        from haute._databricks_io import _cache_path_for

        a = _cache_path_for("cat.sch.alpha", project_root=tmp_path)
        b = _cache_path_for("cat.sch.beta", project_root=tmp_path)
        assert a != b


# ---------------------------------------------------------------------------
# Gap: EXECUTE IMMEDIATE bypass — only EXEC is blocked, not EXECUTE
# Production failure: attacker runs arbitrary SQL via EXECUTE IMMEDIATE
# ---------------------------------------------------------------------------


class TestExecuteImmediateBypass:
    """EXECUTE IMMEDIATE is a Databricks SQL command that runs dynamic SQL.
    The current regex blocks EXEC but EXECUTE IMMEDIATE slips through because
    \\bEXEC\\b matches 'EXEC' as a whole word — 'EXECUTE' also starts with
    EXEC but the word boundary after EXEC fails since 'U' follows.

    Actually \\bEXEC\\b will NOT match inside 'EXECUTE' because the 'U'
    after 'EXEC' is a word char, so \\b does not fire. This is the gap.
    """

    def test_execute_immediate_not_blocked(self) -> None:
        """Demonstrates that EXECUTE IMMEDIATE bypasses validation today.

        Production risk: a GUI user could craft a query like
        'SELECT 1 WHERE 1=1 EXECUTE IMMEDIATE ...' and it would pass
        validation. The \\bEXEC\\b regex does NOT match 'EXECUTE' because
        the U after EXEC is a word character, preventing the \\b boundary.

        Note: we must avoid other blocked keywords in the test payload
        so only EXECUTE is tested.
        """
        from haute._databricks_io import _validate_select_clause

        # This SHOULD raise but currently does NOT — documenting the gap.
        # When the fix lands (adding EXECUTE to _DANGEROUS_SQL_RE), flip
        # this test to assert it raises.
        try:
            _validate_select_clause("SELECT 1 WHERE 1=1 EXECUTE IMMEDIATE 'SELECT 2'")
            # If we get here, the gap is still open
            pytest.xfail(
                "EXECUTE IMMEDIATE is not blocked by _validate_select_clause — "
                "add EXECUTE to _DANGEROUS_SQL_RE"
            )
        except ValueError:
            pass  # Fixed — EXECUTE is now blocked

    def test_exec_is_blocked(self) -> None:
        """Baseline: EXEC (without UTE) IS already blocked."""
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 WHERE EXEC sp_evil")


# ---------------------------------------------------------------------------
# Gap: Subqueries in SELECT clause — FROM not in _DANGEROUS_SQL_RE
# Production failure: attacker reads unauthorized tables via subquery
# ---------------------------------------------------------------------------


class TestSubqueryBypass:
    """A query like 'SELECT * FROM (SELECT * FROM secret_table)' passes
    validation because FROM is not a dangerous keyword. After composition
    it becomes 'SELECT * FROM (SELECT * FROM secret_table) FROM cat.sch.tbl'
    which, depending on the SQL engine, may execute the inner FROM first.
    """

    def test_select_from_subquery_not_blocked(self) -> None:
        """Demonstrates that FROM-based subqueries bypass validation.

        Production risk: user reads from a table they weren't granted
        access to by embedding a FROM clause in the SELECT portion.
        """
        from haute._databricks_io import _validate_select_clause

        try:
            _validate_select_clause(
                "SELECT * FROM (SELECT secret_col FROM other_catalog.other_schema.secrets)"
            )
            pytest.xfail(
                "Subquery with FROM in SELECT clause is not blocked — "
                "consider adding FROM to _DANGEROUS_SQL_RE or restricting "
                "the query to column-list-only syntax"
            )
        except ValueError:
            pass  # Fixed


# ---------------------------------------------------------------------------
# Gap: SQL comments can neutralize the appended FROM clause
# Production failure: attacker comments out FROM {table}, controlling the
# full query
# ---------------------------------------------------------------------------


class TestSQLCommentInjection:
    """The composed query is '{select_clause} FROM {table}'.
    If select_clause ends with '--', the FROM {table} is commented out,
    letting the attacker control which table is actually queried.
    """

    def test_line_comment_neutralizes_from(self) -> None:
        """'SELECT * FROM secrets --' would produce:
        'SELECT * FROM secrets -- FROM cat.sch.tbl'
        The '-- FROM ...' is a comment, so only 'SELECT * FROM secrets' runs.

        Production risk: complete bypass of table-level access control.
        """
        from haute._databricks_io import _validate_select_clause

        try:
            _validate_select_clause("SELECT * FROM secrets --")
            pytest.xfail(
                "SQL line comment (--) in query is not blocked — "
                "attacker can comment out the appended FROM clause"
            )
        except ValueError:
            pass  # Fixed

    def test_block_comment_neutralizes_from(self) -> None:
        """'SELECT * FROM secrets /*' would produce:
        'SELECT * FROM secrets /* FROM cat.sch.tbl'
        The '/* FROM ...' is an unclosed block comment — most SQL engines
        treat it as commenting out the rest.

        Production risk: same as line comment — full query control.
        """
        from haute._databricks_io import _validate_select_clause

        try:
            _validate_select_clause("SELECT * FROM secrets /*")
            pytest.xfail(
                "SQL block comment (/*) in query is not blocked — "
                "attacker can comment out the appended FROM clause"
            )
        except ValueError:
            pass  # Fixed

    def test_inline_block_comment_hides_keyword(self) -> None:
        """Block comments are now explicitly rejected by the /* check,
        regardless of content. A query like 'SELECT 1 /*UNION*/ FROM
        secrets' is blocked because it contains '/*'.

        This is stricter than the old keyword-only check and prevents
        attackers from using comments to hide SQL structure.
        """
        from haute._databricks_io import _validate_select_clause

        # The /* check fires before the keyword regex
        with pytest.raises(ValueError, match="block comment"):
            _validate_select_clause("SELECT 1 /*UNION*/ FROM secrets")


# ---------------------------------------------------------------------------
# Gap: Window functions, CTEs (WITH), and LATERAL VIEW not blocked
# Production failure: CTE allows arbitrary multi-statement-like behavior
# ---------------------------------------------------------------------------


class TestAdvancedSQLConstructsBypass:
    """WITH (CTE) and LATERAL VIEW are powerful SQL constructs that
    can be used to compose complex queries bypassing the simple
    select-clause model.
    """

    def test_cte_with_clause_not_blocked(self) -> None:
        """'WITH tmp AS (SELECT ...) SELECT ...' doesn't start with SELECT,
        so it IS rejected by the startswith check. But what about
        'SELECT * WHERE EXISTS (WITH ...)' or similar embeddings?

        Actually, CTEs must be at the top, so the startswith check does
        catch the common case. This test confirms that.
        """
        from haute._databricks_io import _validate_select_clause

        with pytest.raises(ValueError, match="must start with SELECT"):
            _validate_select_clause("WITH tmp AS (SELECT 1) SELECT * FROM tmp")

    def test_lateral_view_not_blocked(self) -> None:
        """LATERAL VIEW is a Spark/Databricks SQL construct for table-generating
        functions. It could be used to expand data from unexpected sources.

        Production risk: data exfiltration via LATERAL VIEW EXPLODE on
        a crafted array, or LATERAL VIEW OUTER that modifies result shape.
        """
        from haute._databricks_io import _validate_select_clause

        try:
            _validate_select_clause("SELECT col1 LATERAL VIEW EXPLODE(array(1,2,3)) t AS val")
            pytest.xfail("LATERAL VIEW is not blocked — consider adding it to _DANGEROUS_SQL_RE")
        except ValueError:
            pass  # Fixed

    def test_window_function_allowed(self) -> None:
        """Window functions (OVER, PARTITION BY) are read-only analytics
        and should be ALLOWED. This test ensures we don't over-block.
        """
        from haute._databricks_io import _validate_select_clause

        # Should NOT raise — window functions are safe read-only operations
        _validate_select_clause(
            "SELECT id, ROW_NUMBER() OVER (PARTITION BY category ORDER BY price)"
        )


# ---------------------------------------------------------------------------
# Gap: Unicode table names
# Production failure: Unicode confusables or invisible chars in table names
# could bypass regex or cause unexpected file paths
# ---------------------------------------------------------------------------


class TestUnicodeTableNames:
    """The _TABLE_NAME_RE uses \\w which in Python matches Unicode word chars.
    This means non-ASCII letters pass the table name check.
    """

    def test_unicode_letters_in_table_name(self, tmp_path: Path) -> None:
        """Table names with accented or CJK characters pass \\w matching.

        Production risk: if Databricks doesn't support these chars, the
        query will fail at runtime with a confusing error. If it does
        support them, the cache filename may collide or cause filesystem
        issues on Windows.
        """
        from haute._databricks_io import _TABLE_NAME_RE, _cache_path_for

        # Unicode letters match \w in Python
        assert _TABLE_NAME_RE.match("catálogo.esquema.tabl\u00e9")

        # Verify cache path doesn't break
        p = _cache_path_for("catálogo.esquema.tabl\u00e9", project_root=tmp_path)
        assert p.name == "catálogo_esquema_tabl\u00e9.parquet"

    def test_invisible_unicode_chars_in_table_name(self) -> None:
        """Zero-width spaces and other invisible Unicode chars should be
        rejected to prevent confusable table names.

        Production risk: two table names that look identical but differ
        by invisible chars could point to different tables/cache files.
        """
        from haute._databricks_io import _TABLE_NAME_RE

        # Zero-width space (U+200B) is NOT a \\w char, so this should fail
        result = _TABLE_NAME_RE.match("cat\u200b.sch.tbl")
        assert result is None, "Table name with zero-width space should be rejected"

    def test_homoglyph_table_names_produce_distinct_cache(self, tmp_path: Path) -> None:
        """Two table names using confusable chars (e.g. Latin 'a' vs Cyrillic 'а')
        must produce distinct cache files, not silently collide.

        Production risk: attacker creates table with homoglyph name that
        shadows a legitimate table's cache.
        """
        from haute._databricks_io import _cache_path_for

        p_latin = _cache_path_for("cat.sch.data", project_root=tmp_path)
        # Cyrillic 'а' (U+0430) looks like Latin 'a' (U+0061)
        p_cyrillic = _cache_path_for("c\u0430t.sch.d\u0430ta", project_root=tmp_path)

        assert p_latin != p_cyrillic, "Homoglyph table names must map to distinct cache paths"


# ---------------------------------------------------------------------------
# Gap: Network timeout during fetch — no test for transient network errors
# Production failure: mid-fetch timeout leaves system in bad state
# ---------------------------------------------------------------------------


class TestNetworkTimeoutDuringFetch:
    """Test that transient errors during batch fetching are handled correctly
    with retry logic and proper cleanup.
    """

    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def test_transient_error_retried_then_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fetchmany_arrow fails twice then succeeds — retry logic recovers.

        Production risk: without retries, a single transient network blip
        causes the entire fetch to fail, wasting minutes of work.
        """
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        # Disable actual sleep during retries
        monkeypatch.setattr("time.sleep", lambda _: None)

        batch = _make_arrow_batch(5)
        empty = _empty_batch_like(batch)

        # First two calls fail BEFORE consuming anything (rownumber unmoved),
        # third succeeds, fourth returns empty. A clean retry like this must
        # never trip the row-loss integrity check.
        call_count = 0

        def fetch_with_failures(size: int) -> pa.Table:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("network timeout")
            if call_count == 3:
                cursor.rownumber += batch.num_rows
                return batch
            return empty

        cursor = MagicMock()
        cursor.rownumber = 0
        cursor.fetchmany_arrow = MagicMock(side_effect=fetch_with_failures)
        conn = _wrap_cursor_in_conn(cursor)

        mock_connect = MagicMock(return_value=conn)
        mock_db, mock_sql = _mock_dbsql_module(mock_connect)

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            result = fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                project_root=tmp_path,
            )

        assert result["row_count"] == 5
        # 2 failures + 1 success + 1 empty = 4 calls
        assert call_count == 4

    def test_all_retries_exhausted_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All retry attempts fail — error is raised and cleanup happens.

        Production risk: without proper cleanup after exhausted retries,
        temp files and progress state could leak.
        """
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        monkeypatch.setattr("time.sleep", lambda _: None)

        cursor = MagicMock()
        cursor.fetchmany_arrow = MagicMock(side_effect=ConnectionError("persistent timeout"))

        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        mock_connect = MagicMock(return_value=conn)
        mock_db, mock_sql = _mock_dbsql_module(mock_connect)

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            with pytest.raises(ConnectionError, match="persistent timeout"):
                fetch_and_cache(
                    "cat.sch.tbl",
                    http_path="/path",
                    project_root=tmp_path,
                )

        # Progress must be cleaned up
        assert fetch_progress("cat.sch.tbl") is None
        # No temp files left
        assert not any(tmp_path.rglob("*.tmp"))


# ---------------------------------------------------------------------------
# Gap: Cache file corruption — partially-written parquet
# Production failure: corrupt cache causes silent data errors or crashes
# ---------------------------------------------------------------------------


class TestCacheFileCorruption:
    """Test that corrupted cache files are handled gracefully."""

    def test_corrupt_parquet_raises_on_read(self, tmp_path: Path) -> None:
        """A partially-written or corrupted parquet file should raise a
        clear error, not silently return wrong data.

        Production risk: if the process is killed mid-write (OOM, power loss),
        the .parquet file could be truncated. On next pipeline run,
        read_cached_table must fail clearly, not return partial data.
        """
        from haute._databricks_io import _cache_path_for, read_cached_table

        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write garbage bytes that look nothing like a parquet file
        p.write_bytes(b"NOT_A_PARQUET_FILE_\x00\x01\x02")

        with pytest.raises(Exception):
            # polars.scan_parquet will raise on invalid parquet
            read_cached_table("cat.sch.tbl", project_root=tmp_path).collect()

    def test_truncated_parquet_raises(self, tmp_path: Path) -> None:
        """Parquet file with valid header but truncated data should fail.

        Production risk: half-written file from interrupted network transfer.
        """
        from haute._databricks_io import _cache_path_for, read_cached_table

        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Write a valid parquet file, then truncate it
        import polars as pl

        pl.DataFrame({"a": list(range(1000))}).write_parquet(p)
        full_size = p.stat().st_size

        # Truncate to half the file
        data = p.read_bytes()
        p.write_bytes(data[: full_size // 2])

        with pytest.raises(Exception):
            read_cached_table("cat.sch.tbl", project_root=tmp_path).collect()

    def test_zero_byte_parquet_raises(self, tmp_path: Path) -> None:
        """Empty (0-byte) cache file must fail, not return empty DataFrame.

        Production risk: race condition where file is created but no data
        is written yet.
        """
        from haute._databricks_io import _cache_path_for, read_cached_table

        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")

        with pytest.raises(Exception):
            read_cached_table("cat.sch.tbl", project_root=tmp_path).collect()


# ---------------------------------------------------------------------------
# Gap: Atomic rename failure on Windows — Path.rename() fails if target exists
# Production failure: second fetch of same table fails on Windows because
# .rename() doesn't overwrite on Windows (unlike POSIX)
# ---------------------------------------------------------------------------


class TestAtomicRenameOnWindows:
    """On Windows, Path.rename() raises FileExistsError if the target exists.
    On POSIX, rename atomically replaces the target. This means re-fetching
    a table (where the .parquet file already exists) may fail on Windows.
    """

    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def test_refetch_overwrites_existing_cache(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fetching a table twice should succeed — the second fetch must
        overwrite the first cache file.

        Production risk: on Windows, the second fetch fails with
        FileExistsError, leaving the user unable to refresh stale data.
        Fix: use Path.replace() instead of Path.rename() in fetch_and_cache.
        """
        import sys

        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        batch = _make_arrow_batch(5)
        mock_db, mock_sql, _ = _build_mock_connector([batch])

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            # First fetch
            fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                project_root=tmp_path,
            )

        # Re-build connector for second fetch
        batch2 = _make_arrow_batch(8)
        mock_db2, mock_sql2, _ = _build_mock_connector([batch2])

        with patch.dict("sys.modules", {"databricks": mock_db2, "databricks.sql": mock_sql2}):
            # Second fetch — must not fail even though target exists
            try:
                result2 = fetch_and_cache(
                    "cat.sch.tbl",
                    http_path="/path",
                    project_root=tmp_path,
                )
                assert result2["row_count"] == 8
            except FileExistsError:
                if sys.platform == "win32":
                    pytest.xfail(
                        "Path.rename() fails on Windows when target exists — "
                        "use Path.replace() instead in fetch_and_cache"
                    )
                else:
                    raise

    def test_rename_failure_simulated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulate Path.rename() raising FileExistsError to verify the
        code handles it (or document that it doesn't).

        Production risk: Windows-specific failure that won't show up in
        Linux CI.
        """
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        batch = _make_arrow_batch(3)
        mock_db, mock_sql, _ = _build_mock_connector([batch])

        original_rename = Path.rename

        call_count = 0

        def failing_rename(self_path: Path, target: Path) -> Path:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileExistsError(f"Cannot rename: target exists: {target}")
            return original_rename(self_path, target)

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            with patch.object(Path, "rename", failing_rename):
                # This test documents whether the current code handles
                # rename failure. If it raises, the gap is confirmed.
                try:
                    fetch_and_cache(
                        "cat.sch.tbl",
                        http_path="/path",
                        project_root=tmp_path,
                    )
                    # If we get here, the code handles it
                except FileExistsError:
                    pytest.xfail(
                        "Path.rename() fails on Windows when target exists — "
                        "use Path.replace() instead for atomic cross-platform rename"
                    )

        # Either way, no temp files should remain
        assert not any(tmp_path.rglob("*.tmp"))


# ---------------------------------------------------------------------------
# Gap: Very large fetch with batch progress tracking
# Production failure: progress state incorrect or stale during multi-batch fetch
# ---------------------------------------------------------------------------


class TestLargeFetchBatchProgress:
    """Test that progress tracking correctly reflects state across
    multiple batches during a large fetch.
    """

    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def test_multi_batch_progress_updates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify progress dict updates correctly across 5 batches.

        Production risk: if progress tracking is wrong, the UI shows
        stale/incorrect row counts, and users can't tell if a large
        fetch is still making progress or stuck.
        """
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

        num_batches = 5
        rows_per_batch = 100
        batches = [_make_arrow_batch(rows_per_batch) for _ in range(num_batches)]
        mock_db, mock_sql, _ = _build_mock_connector(batches)

        # Capture progress snapshots during fetch by hooking fetchmany_arrow
        progress_snapshots: list[dict[str, object] | None] = []
        original_fetch_progress = fetch_progress

        # We'll read progress after each batch write by wrapping the
        # cursor's fetchmany_arrow to also capture progress
        empty = _empty_batch_like(batches[0])
        batch_iter = iter(batches + [empty])
        call_idx = 0

        def fetching_with_progress_capture(size: int) -> pa.Table:
            nonlocal call_idx
            # Capture progress BEFORE returning next batch
            if call_idx > 0:
                snap = original_fetch_progress("cat.sch.big_table")
                progress_snapshots.append(snap)
            call_idx += 1
            return next(batch_iter)

        cursor = MagicMock()
        cursor.fetchmany_arrow = MagicMock(side_effect=fetching_with_progress_capture)

        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        mock_connect = MagicMock(return_value=conn)
        mock_db_mod, mock_sql_mod = _mock_dbsql_module(mock_connect)

        with patch.dict("sys.modules", {"databricks": mock_db_mod, "databricks.sql": mock_sql_mod}):
            result = fetch_and_cache(
                "cat.sch.big_table",
                http_path="/path",
                project_root=tmp_path,
            )

        # Total rows: 5 batches * 100 rows = 500
        assert result["row_count"] == 500

        # We should have captured progress at batches 1-5 (before the empty return)
        assert len(progress_snapshots) == num_batches

        # Progress should be monotonically increasing
        for i, snap in enumerate(progress_snapshots):
            assert snap is not None, f"Progress was None at batch {i + 1}"
            assert snap["rows"] == (i + 1) * rows_per_batch
            assert snap["batches"] == i + 1
            assert isinstance(snap["elapsed"], float)
            assert snap["elapsed"] >= 0

        # After fetch completes, progress is cleared
        assert fetch_progress("cat.sch.big_table") is None


# ---------------------------------------------------------------------------
# 4a.8(1): a mid-fetch retry must never silently truncate the cached table.
#
# databricks-sql-connector's fetchmany_arrow consumes result chunks and
# advances the cursor position (`rownumber` == ResultSet._next_row_index)
# BEFORE a network failure inside `_fill_results_buffer` can raise. A retry
# of the failed call then continues from the advanced position, silently
# dropping every row the failed attempt had already consumed.
# ---------------------------------------------------------------------------


class TestRetryRowLossFailsLoud:
    """Once any retry happened, written rows must equal cursor-consumed rows."""

    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def _run_fetch(
        self,
        cursor: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, object]:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        monkeypatch.setattr("time.sleep", lambda _: None)

        conn = _wrap_cursor_in_conn(cursor)
        mock_connect = MagicMock(return_value=conn)
        mock_db, mock_sql = _mock_dbsql_module(mock_connect)
        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            return fetch_and_cache(
                "cat.sch.tbl",
                http_path="/path",
                project_root=tmp_path,
            )

    def test_retry_that_skipped_a_chunk_fails_loud(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Failed attempt consumed a chunk; the retry returns the NEXT chunk.

        Production risk at HEAD: the fetch "succeeds" with 14 of 21 rows and
        the cache silently misses a whole 7-row chunk from the middle of the
        table.
        """
        from haute._databricks_io import FetchIntegrityError

        batch_a = _make_arrow_batch(5)
        batch_b = _make_arrow_batch(7)  # consumed by the failed attempt, lost
        batch_c = _make_arrow_batch(9)
        empty = _empty_batch_like(batch_a)

        cursor = MagicMock()
        cursor.rownumber = 0
        call_count = 0

        def fetchmany(size: int) -> pa.Table:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                cursor.rownumber += batch_a.num_rows
                return batch_a
            if call_count == 2:
                # The connector consumed batch_b internally, then the
                # network died before the call could return it.
                cursor.rownumber += batch_b.num_rows
                raise ConnectionError("cloud fetch link download failed")
            if call_count == 3:
                cursor.rownumber += batch_c.num_rows
                return batch_c
            return empty

        cursor.fetchmany_arrow = MagicMock(side_effect=fetchmany)

        with pytest.raises(FetchIntegrityError, match="lost rows during a retry") as exc_info:
            self._run_fetch(cursor, tmp_path, monkeypatch)

        message = str(exc_info.value)
        assert "cat.sch.tbl" in message
        assert "21" in message  # rows the cursor consumed
        assert "14" in message  # rows actually received locally
        # Nothing cached, nothing leaked, progress cleared.
        assert not (tmp_path / CACHE_DIR / "cat_sch_tbl.parquet").exists()
        assert not any(tmp_path.rglob("*.tmp"))
        assert fetch_progress("cat.sch.tbl") is None

    def test_retry_that_consumed_to_stream_end_fails_loud(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Failed attempt consumed the tail; the retry sees an empty stream.

        This is the nastiest shape: at HEAD the loop breaks on the empty
        batch and caches a TRUNCATED table that looks complete.
        """
        from haute._databricks_io import FetchIntegrityError

        batch_a = _make_arrow_batch(5)
        batch_b = _make_arrow_batch(7)  # the tail, consumed and lost
        empty = _empty_batch_like(batch_a)

        cursor = MagicMock()
        cursor.rownumber = 0
        call_count = 0

        def fetchmany(size: int) -> pa.Table:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                cursor.rownumber += batch_a.num_rows
                return batch_a
            if call_count == 2:
                cursor.rownumber += batch_b.num_rows
                raise ConnectionError("connection reset mid-batch")
            return empty

        cursor.fetchmany_arrow = MagicMock(side_effect=fetchmany)

        with pytest.raises(FetchIntegrityError, match="lost rows during a retry") as exc_info:
            self._run_fetch(cursor, tmp_path, monkeypatch)

        message = str(exc_info.value)
        assert "cat.sch.tbl" in message
        assert "12" in message  # consumed
        assert "5" in message  # received
        assert not (tmp_path / CACHE_DIR / "cat_sch_tbl.parquet").exists()
        assert not any(tmp_path.rglob("*.tmp"))
        assert fetch_progress("cat.sch.tbl") is None


# ---------------------------------------------------------------------------
# 4a.8(2): concurrent fetches of the SAME table must not share one tmp path.
#
# At HEAD both fetches write `<table>.parquet.tmp`, so two ParquetWriters
# interleave into one file (corrupt cache) or collide on Windows file locks.
# Each fetch must get a unique tmp file and atomically replace the cache.
# ---------------------------------------------------------------------------


class TestConcurrentSameTableFetch:
    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def test_concurrent_fetches_produce_a_coherent_cache(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two threads fetch one table; the cache is never corrupted.

        The barrier inside the second fetchmany_arrow call holds BOTH
        fetches at a point where their parquet writers are open, forcing
        the tmp-file overlap deterministically (W2.10 pattern).

        Contract (matches the established atomic-replace discipline in
        haute._file_ops): each fetch stages to its own unique tmp file, so
        the cache file is always a complete parquet from exactly one fetch.
        On POSIX both replaces succeed (last wins). On Windows, two replaces
        racing for the same destination may fail ONE fetch loudly with an
        OSError — never silently, and never leaving a torn file.
        """
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        # Keep any unexpected retry path fast instead of real backoff sleeps.
        monkeypatch.setattr("time.sleep", lambda _: None)

        barrier = threading.Barrier(2)

        def _make_conn(row_count: int) -> MagicMock:
            batch = _make_arrow_batch(row_count)
            empty = _empty_batch_like(batch)
            cursor = MagicMock()
            cursor.rownumber = 0
            call_count = 0

            def fetchmany(size: int) -> pa.Table:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    cursor.rownumber += batch.num_rows
                    return batch
                # Both fetches have an open writer on their tmp file here.
                barrier.wait(timeout=10)
                return empty

            cursor.fetchmany_arrow = MagicMock(side_effect=fetchmany)
            return _wrap_cursor_in_conn(cursor)

        conns: queue.Queue[MagicMock] = queue.Queue()
        conns.put(_make_conn(5))
        conns.put(_make_conn(8))
        mock_connect = MagicMock(side_effect=lambda **kwargs: conns.get_nowait())
        mock_db, mock_sql = _mock_dbsql_module(mock_connect)

        errors: list[BaseException] = []
        results: list[dict[str, object]] = []

        def _run() -> None:
            try:
                results.append(
                    fetch_and_cache(
                        "cat.sch.tbl",
                        http_path="/path",
                        project_root=tmp_path,
                    )
                )
            except BaseException as exc:  # surfaced below
                errors.append(exc)

        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            threads = [threading.Thread(target=_run) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "fetch threads deadlocked"

        assert len(errors) + len(results) == 2
        if sys.platform == "win32":
            # Same-destination replace contention may fail one fetch loudly.
            assert len(errors) <= 1, f"more than one fetch failed: {errors}"
            assert all(isinstance(exc, OSError) for exc in errors), (
                f"loser must fail with a loud OSError, got: {errors}"
            )
        else:
            assert errors == [], f"concurrent fetches failed: {errors}"
        assert len(results) >= 1
        completed_heights = {r["row_count"] for r in results}
        assert completed_heights <= {5, 8}
        # Whichever fetch won, the cache must be a complete, readable
        # parquet from exactly one COMPLETED fetch — never interleaved.
        final = pl.read_parquet(tmp_path / CACHE_DIR / "cat_sch_tbl.parquet")
        assert final.height in completed_heights
        assert final.columns == ["id", "value"]
        assert not any(tmp_path.rglob("*.tmp")), (
            f"tmp files leaked: {list(tmp_path.rglob('*.tmp'))}"
        )
        assert fetch_progress("cat.sch.tbl") is None


# ---------------------------------------------------------------------------
# 4a.8(3): a zero-row fetch must cache the REAL result schema, not all-string.
#
# The connector materializes every fetchmany_arrow result — including the
# empty terminator — against the query's result manifest schema, so the
# terminating batch carries the authoritative column types.
# ---------------------------------------------------------------------------


class TestZeroRowFetchSchema:
    @pytest.fixture(autouse=True)
    def _cleanup_progress(self) -> None:
        yield
        _clear_fetch_progress()

    def _run_fetch(
        self,
        cursor: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, object]:
        monkeypatch.setenv("DATABRICKS_HOST", "host.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
        conn = _wrap_cursor_in_conn(cursor)
        mock_connect = MagicMock(return_value=conn)
        mock_db, mock_sql = _mock_dbsql_module(mock_connect)
        with patch.dict("sys.modules", {"databricks": mock_db, "databricks.sql": mock_sql}):
            return fetch_and_cache(
                "cat.sch.empty",
                http_path="/path",
                project_root=tmp_path,
            )

    def test_zero_row_fetch_preserves_real_result_schema(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty table fetch caches int/float/timestamp columns, not strings.

        Production risk at HEAD: the cache is rebuilt from cursor.description
        with every column typed pa.string(), so the first pipeline run
        against an empty table sees a wrong all-Utf8 schema and downstream
        numeric expressions fail (or worse, silently compare strings).
        """
        result_schema = pa.schema(
            [("id", pa.int64()), ("value", pa.float64()), ("ts", pa.timestamp("us"))]
        )
        cursor = MagicMock()
        cursor.rownumber = 0
        cursor.fetchmany_arrow = MagicMock(return_value=result_schema.empty_table())
        # Realistic DBAPI description (HEAD's all-string rebuild reads this).
        cursor.description = [("id", "int"), ("value", "double"), ("ts", "timestamp")]

        result = self._run_fetch(cursor, tmp_path, monkeypatch)

        assert result["row_count"] == 0
        cached = pl.read_parquet(str(result["path"]))
        assert cached.height == 0
        assert dict(cached.schema) == {
            "id": pl.Int64,
            "value": pl.Float64,
            "ts": pl.Datetime(time_unit="us"),
        }

    def test_zero_row_fetch_with_schemaless_terminator_fails_loudly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A zero-column terminator means the transport gave us no schema.

        No legal SELECT produces zero columns, so caching anything here
        would fabricate a table shape. Refuse loudly; the fetch is
        retry-able.
        """
        from haute._databricks_io import FetchIntegrityError, cached_path

        cursor = MagicMock()
        cursor.rownumber = 0
        cursor.fetchmany_arrow = MagicMock(return_value=pa.table({}))
        cursor.description = None

        with pytest.raises(FetchIntegrityError, match="no result schema"):
            self._run_fetch(cursor, tmp_path, monkeypatch)

        assert cached_path("cat.sch.empty", tmp_path) is None
        assert not any(tmp_path.rglob("*.tmp"))
        assert fetch_progress("cat.sch.empty") is None
