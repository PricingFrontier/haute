"""Tests for Databricks local parquet caching logic."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._databricks_io import (
    CACHE_DIR,
    CacheNotFoundError,
    _cache_path_for,
    _clear_fetch_progress,
    _set_fetch_progress,
    cache_info,
    cached_path,
    clear_cache,
    fetch_progress,
    read_cached_table,
)


class TestCachePath:
    def test_returns_none_when_no_cache(self, tmp_path: Path) -> None:
        assert cached_path("cat.sch.tbl", project_root=tmp_path) is None

    def test_returns_path_when_cached(self, tmp_path: Path) -> None:
        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"a": [1, 2]}).write_parquet(p)

        result = cached_path("cat.sch.tbl", project_root=tmp_path)
        assert result is not None
        assert result == p

    def test_cache_path_uses_underscores(self, tmp_path: Path) -> None:
        p = _cache_path_for("my_catalog.my_schema.my_table", project_root=tmp_path)
        assert p.name == "my_catalog_my_schema_my_table.parquet"
        assert p.parent.name == CACHE_DIR


class TestCacheInfo:
    def test_returns_none_when_no_cache(self, tmp_path: Path) -> None:
        assert cache_info("cat.sch.tbl", project_root=tmp_path) is None

    def test_returns_metadata_when_cached(self, tmp_path: Path) -> None:
        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}).write_parquet(p)

        info = cache_info("cat.sch.tbl", project_root=tmp_path)
        assert info is not None
        assert info["table"] == "cat.sch.tbl"
        assert info["row_count"] == 3
        assert info["column_count"] == 2
        assert info["size_bytes"] > 0
        assert info["fetched_at"] > 0
        assert "x" in info["columns"]
        assert "y" in info["columns"]


class TestClearCache:
    def test_returns_false_when_no_cache(self, tmp_path: Path) -> None:
        assert clear_cache("cat.sch.tbl", project_root=tmp_path) is False

    def test_deletes_cached_file(self, tmp_path: Path) -> None:
        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"a": [1]}).write_parquet(p)
        assert p.exists()

        assert clear_cache("cat.sch.tbl", project_root=tmp_path) is True
        assert not p.exists()
        assert cached_path("cat.sch.tbl", project_root=tmp_path) is None


class TestFetchProgress:
    @pytest.fixture(autouse=True)
    def _cleanup_progress(self):
        yield
        _clear_fetch_progress()

    def test_returns_none_when_no_active_fetch(self) -> None:
        assert fetch_progress("cat.sch.tbl") is None

    def test_returns_progress_when_active(self) -> None:
        _set_fetch_progress("cat.sch.tbl", {"rows": 500, "batches": 5, "elapsed": 1.2})

        result = fetch_progress("cat.sch.tbl")
        assert result is not None
        assert result["rows"] == 500
        assert result["batches"] == 5
        assert result["elapsed"] == 1.2


class TestReadCachedTable:
    def test_raises_when_not_cached(self, tmp_path: Path) -> None:
        with pytest.raises(CacheNotFoundError, match="not.*fetched"):
            read_cached_table("cat.sch.tbl", project_root=tmp_path)

    def test_reads_cached_parquet(self, tmp_path: Path) -> None:
        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"val": [10, 20, 30]}).write_parquet(p)

        lf = read_cached_table("cat.sch.tbl", project_root=tmp_path)
        assert isinstance(lf, pl.LazyFrame)
        df = lf.collect()
        assert len(df) == 3
        assert df["val"].to_list() == [10, 20, 30]


# ---------------------------------------------------------------------------
# Additional edge-case tests for cache_path_for
# ---------------------------------------------------------------------------


class TestCachePathForEdgeCases:
    def test_backtick_quoted_table(self, tmp_path: Path) -> None:
        p = _cache_path_for("`my-catalog`.`my-schema`.`my-table`", project_root=tmp_path)
        assert p.parent.name == CACHE_DIR
        assert p.suffix == ".parquet"

    def test_hyphenated_table_name(self, tmp_path: Path) -> None:
        p = _cache_path_for("cat-a.sch-b.tbl-c", project_root=tmp_path)
        assert p.name == "cat-a_sch-b_tbl-c.parquet"

    def test_result_is_within_cache_dir(self, tmp_path: Path) -> None:
        p = _cache_path_for("a.b.c", project_root=tmp_path)
        cache_dir = (tmp_path / CACHE_DIR).resolve()
        assert p.resolve().is_relative_to(cache_dir)


# ---------------------------------------------------------------------------
# Additional cache hit/miss and clear_cache edge cases
# ---------------------------------------------------------------------------


class TestCacheHitMissEdgeCases:
    def test_cached_path_returns_none_for_different_table(self, tmp_path: Path) -> None:
        """Caching table A does not create a cache hit for table B."""
        p = _cache_path_for("cat.sch.tblA", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"a": [1]}).write_parquet(p)

        assert cached_path("cat.sch.tblA", project_root=tmp_path) is not None
        assert cached_path("cat.sch.tblB", project_root=tmp_path) is None

    def test_clear_cache_then_read_raises(self, tmp_path: Path) -> None:
        """After clearing cache, reading the table should raise."""
        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"v": [1]}).write_parquet(p)

        assert clear_cache("cat.sch.tbl", project_root=tmp_path) is True
        with pytest.raises(CacheNotFoundError):
            read_cached_table("cat.sch.tbl", project_root=tmp_path)


class TestCacheInfoEdgeCases:
    def test_cache_info_has_correct_path(self, tmp_path: Path) -> None:
        """cache_info path matches the expected cache path."""
        p = _cache_path_for("cat.sch.tbl", project_root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1], "y": [2]}).write_parquet(p)

        info = cache_info("cat.sch.tbl", project_root=tmp_path)
        assert info is not None
        assert info["path"] == str(p)
