"""Tests for haute._io — read_source and load_external_object."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._io import _load_cached, load_external_object, read_source

# ---------------------------------------------------------------------------
# read_source
# ---------------------------------------------------------------------------


class TestReadSourceParquet:
    def test_reads_parquet_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1, 2, 3]}).write_parquet(str(path))
        lf = read_source(str(path))
        assert isinstance(lf, pl.LazyFrame)
        result = lf.collect()
        assert result["a"].to_list() == [1, 2, 3]


class TestReadSourceCSV:
    def test_reads_csv_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.csv"
        pl.DataFrame({"col": [4, 5]}).write_csv(str(path))
        result = read_source(str(path)).collect()
        assert result["col"].to_list() == [4, 5]


class TestReadSourceJSON:
    def test_reads_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        pl.DataFrame({"name": ["alice", "bob"]}).write_json(str(path))
        result = read_source(str(path)).collect()
        assert result["name"].to_list() == ["alice", "bob"]


class TestReadSourceJSONL:
    def test_reads_ndjson_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        pl.DataFrame({"v": [10, 20]}).write_ndjson(str(path))
        result = read_source(str(path)).collect()
        assert result["v"].to_list() == [10, 20]


class TestReadSourceCaseInsensitive:
    """Extension matching must be case-insensitive (consistent with codegen)."""

    def test_uppercase_csv(self, tmp_path: Path) -> None:
        path = tmp_path / "DATA.CSV"
        pl.DataFrame({"a": [1]}).write_csv(str(path))
        lf = read_source(str(path))
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect()["a"].to_list() == [1]

    def test_uppercase_json(self, tmp_path: Path) -> None:
        path = tmp_path / "DATA.JSON"
        pl.DataFrame({"a": [1]}).write_json(str(path))
        lf = read_source(str(path))
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect()["a"].to_list() == [1]

    def test_uppercase_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "DATA.JSONL"
        pl.DataFrame({"a": [1]}).write_ndjson(str(path))
        lf = read_source(str(path))
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect()["a"].to_list() == [1]

    def test_uppercase_parquet(self, tmp_path: Path) -> None:
        path = tmp_path / "DATA.PARQUET"
        pl.DataFrame({"a": [1]}).write_parquet(str(path))
        lf = read_source(str(path))
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect()["a"].to_list() == [1]

    def test_mixed_case_json(self, tmp_path: Path) -> None:
        path = tmp_path / "data.Json"
        pl.DataFrame({"a": [1]}).write_json(str(path))
        lf = read_source(str(path))
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect()["a"].to_list() == [1]


class TestReadSourceErrors:
    def test_unsupported_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported file type: .xlsx"):
            read_source("/some/path/file.xlsx")

    def test_no_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported file type"):
            read_source("/some/path/noext")

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported file type: .txt"):
            read_source("data.txt")


# ---------------------------------------------------------------------------
# load_external_object
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure the object cache is empty before and after each test."""
    _load_cached.cache_clear()
    yield
    _load_cached.cache_clear()


def _object_cache_size() -> int:
    return _load_cached.cache_info().currsize


class TestLoadExternalObjectJSON:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_loads_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "model.json"
        data = {"weights": [1, 2, 3]}
        path.write_text(json.dumps(data))
        result = load_external_object(str(path), "json")
        assert result == data

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_json_caching(self, tmp_path: Path) -> None:
        path = tmp_path / "model.json"
        path.write_text('{"x": 1}')
        r1 = load_external_object(str(path), "json")
        r2 = load_external_object(str(path), "json")
        assert r1 == r2
        # Cache should contain exactly one entry
        assert _object_cache_size() == 1


class TestLoadExternalObjectPickle:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_delegates_to_safe_unpickle(self, tmp_path: Path) -> None:
        path = tmp_path / "model.pkl"
        path.write_bytes(b"fake")
        sentinel = object()
        with patch("haute._sandbox.safe_unpickle", return_value=sentinel) as mock_unpickle:
            result = load_external_object(str(path), "pickle")
        mock_unpickle.assert_called_once_with(str(path))
        assert result is sentinel


class TestLoadExternalObjectJoblib:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_delegates_to_safe_joblib_load(self, tmp_path: Path) -> None:
        path = tmp_path / "model.joblib"
        path.write_bytes(b"fake")
        sentinel = object()
        with patch("haute._sandbox.safe_joblib_load", return_value=sentinel) as mock_load:
            result = load_external_object(str(path), "joblib")
        mock_load.assert_called_once_with(str(path))
        assert result is sentinel


class TestLoadExternalObjectCatboost:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_classifier_by_default(self, tmp_path: Path) -> None:
        path = tmp_path / "model.cbm"
        path.write_bytes(b"fake")
        mock_model = MagicMock()
        with patch("catboost.CatBoostClassifier", return_value=mock_model):
            result = load_external_object(str(path), "catboost")
        mock_model.load_model.assert_called_once_with(str(path))
        assert result is mock_model

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_regressor_class(self, tmp_path: Path) -> None:
        path = tmp_path / "model.cbm"
        path.write_bytes(b"fake")
        mock_model = MagicMock()
        with patch("catboost.CatBoostRegressor", return_value=mock_model):
            result = load_external_object(str(path), "catboost", model_class="regressor")
        mock_model.load_model.assert_called_once_with(str(path))
        assert result is mock_model


class TestObjectCacheBehavior:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_cache_invalidated_on_mtime_change(self, tmp_path: Path) -> None:
        """Modifying the file changes the mtime, causing a cache miss."""
        import os
        import time

        path = tmp_path / "data.json"
        path.write_text('{"v": 1}')
        r1 = load_external_object(str(path), "json")
        assert r1 == {"v": 1}

        # Ensure mtime changes (filesystem granularity)
        time.sleep(0.05)
        path.write_text('{"v": 2}')
        os.utime(str(path), None)  # force mtime update
        r2 = load_external_object(str(path), "json")
        assert r2 == {"v": 2}

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_missing_file_uses_mtime_zero(self, tmp_path: Path) -> None:
        """When the file doesn't exist, mtime defaults to 0.0."""
        path = tmp_path / "missing.json"
        with pytest.raises(FileNotFoundError):
            load_external_object(str(path), "json")


class TestLoadExternalObjectUnsupportedType:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_unsupported_file_type_raises(self, tmp_path: Path) -> None:
        """Unsupported file_type raises ValueError."""
        path = tmp_path / "model.xyz"
        path.write_bytes(b"data")
        with pytest.raises(ValueError, match="Unsupported file_type"):
            load_external_object(str(path), "xyz")


class TestReadSourcePathTraversal:
    def test_path_traversal_blocked(self) -> None:
        """Paths containing '..' are rejected."""
        with pytest.raises(ValueError, match="not allowed"):
            read_source("/some/../etc/passwd.csv")

    def test_dotdot_in_middle_blocked(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            read_source("/data/../secrets/file.parquet")


class TestObjectCacheDifferentModelClass:
    """Cache keys include model_class — different model_class = cache miss."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_different_model_class_is_cache_miss(self, tmp_path: Path) -> None:
        path = tmp_path / "model.json"
        path.write_text('{"x": 1}')
        r1 = load_external_object(str(path), "json", model_class="classifier")
        r2 = load_external_object(str(path), "json", model_class="regressor")
        # Both calls load the same data, but cache has 2 entries (different keys)
        assert r1 == r2
        assert _object_cache_size() == 2

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_same_key_is_cache_hit(self, tmp_path: Path) -> None:
        path = tmp_path / "model.json"
        path.write_text('{"v": 42}')
        r1 = load_external_object(str(path), "json")
        r2 = load_external_object(str(path), "json")
        assert r1 is r2  # exact same object from cache
        assert _object_cache_size() == 1
