"""Tests for haute._io — read_source and load_external_object."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute._io import (
    _load_cached,
    build_data_source_adapter,
    load_external_object,
    read_data_source,
    read_source,
)
from haute.errors import BoundedMemoryUnsupportedError, SchemaMismatchError

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

    @pytest.mark.parametrize(
        "profile",
        [
            ExecutionProfile.LAZY_SINK,
            ExecutionProfile.TRAINING_PREP,
            ExecutionProfile.OPTIMISER_SETUP,
            ExecutionProfile.AUTO_RANGE,
            ExecutionProfile.DEPLOY_BATCH,
            ExecutionProfile.CHUNKED_MAP_REDUCE,
        ],
    )
    def test_bounded_profiles_require_declared_csv_schema(
        self,
        tmp_path: Path,
        profile: ExecutionProfile,
    ) -> None:
        path = tmp_path / "data.csv"
        pl.DataFrame({"quote_id": ["001"], "premium": [10.5]}).write_csv(path)

        with patch.object(pl, "scan_csv", wraps=pl.scan_csv) as scan_csv:
            with pytest.raises(BoundedMemoryUnsupportedError, match="CSV sources require"):
                read_source(path, profile=profile)

        scan_csv.assert_not_called()

    @pytest.mark.parametrize(
        "profile",
        [ExecutionProfile.PREVIEW_EAGER, ExecutionProfile.DEPLOY_LIVE],
    )
    def test_small_eager_profiles_can_infer_csv_schema(
        self,
        tmp_path: Path,
        profile: ExecutionProfile,
    ) -> None:
        path = tmp_path / "data.csv"
        pl.DataFrame({"col": [4, 5]}).write_csv(path)

        result = read_source(path, profile=profile).collect()

        assert result["col"].to_list() == [4, 5]

    def test_bounded_profile_accepts_declared_csv_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "data.csv"
        path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        result = read_source(
            path,
            profile=ExecutionProfile.AUTO_RANGE,
            schema_overrides={"quote_id": "String", "premium": "Float64"},
        ).collect()

        assert result["quote_id"].to_list() == ["001"]

    def test_bounded_profile_requires_declared_dtype_for_every_unprojected_csv_column(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "data.csv"
        path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        with pytest.raises(BoundedMemoryUnsupportedError, match="premium"):
            read_source(
                path,
                profile=ExecutionProfile.LAZY_SINK,
                schema_overrides={"quote_id": "String"},
            )

    def test_bounded_profile_projection_requires_declared_dtype_for_projected_csv_column(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "data.csv"
        path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        with pytest.raises(BoundedMemoryUnsupportedError, match="premium"):
            read_source(
                path,
                profile=ExecutionProfile.LAZY_SINK,
                columns=["premium"],
            )


class TestReadSourceJSON:
    def test_reads_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        pl.DataFrame({"name": ["alice", "bob"]}).write_json(str(path))
        result = read_source(str(path)).collect()
        assert result["name"].to_list() == ["alice", "bob"]

    def test_bounded_profile_rejects_plain_json_before_eager_read(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "data.json"
        pl.DataFrame({"name": ["alice"]}).write_json(str(path))

        with patch.object(pl, "read_json", wraps=pl.read_json) as read_json:
            with pytest.raises(BoundedMemoryUnsupportedError, match="Plain JSON"):
                read_source(str(path), profile=ExecutionProfile.LAZY_SINK)

        read_json.assert_not_called()

    def test_deploy_live_can_read_plain_json(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        pl.DataFrame({"name": ["alice"]}).write_json(str(path))

        result = read_source(str(path), profile=ExecutionProfile.DEPLOY_LIVE).collect()

        assert result["name"].to_list() == ["alice"]


class TestReadSourceJSONL:
    def test_reads_ndjson_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        pl.DataFrame({"v": [10, 20]}).write_ndjson(str(path))
        result = read_source(str(path)).collect()
        assert result["v"].to_list() == [10, 20]


class TestReadSourceProjectionAndSchema:
    def test_parquet_projection_pushes_into_scan_plan(self, tmp_path: Path) -> None:
        path = tmp_path / "wide.parquet"
        pl.DataFrame({"a": [1], "b": [2], "c": [3]}).write_parquet(path)

        lf = read_source(path, columns=["a", "c"])

        assert lf.collect_schema().names() == ["a", "c"]
        assert "PROJECT 2/3 COLUMNS" in lf.explain()

    def test_csv_schema_overrides_are_applied(self, tmp_path: Path) -> None:
        path = tmp_path / "quoted.csv"
        path.write_text("quote_id,premium\n001,10.5\n002,11.5\n", encoding="utf-8")

        lf = read_source(
            path,
            schema_overrides={"quote_id": "String", "premium": "Float64"},
        )

        schema = lf.collect_schema()
        assert schema["quote_id"] == pl.String
        assert schema["premium"] == pl.Float64
        assert lf.collect()["quote_id"].to_list() == ["001", "002"]

    def test_csv_schema_declaration_missing_column_fails_loudly(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "quoted.csv"
        path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        with pytest.raises(SchemaMismatchError, match="Declared source schema mismatch"):
            read_source(path, schema_overrides={"missing": "String"})

    def test_ndjson_schema_declaration_missing_column_fails_loudly(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "source.jsonl"
        pl.DataFrame({"premium": [10.5]}).write_ndjson(path)

        with pytest.raises(SchemaMismatchError, match="Declared source schema mismatch"):
            read_source(path, schema_overrides={"missing": "String"})

    def test_invalid_declared_dtype_fails_loudly(self, tmp_path: Path) -> None:
        path = tmp_path / "data.csv"
        pl.DataFrame({"a": [1]}).write_csv(path)

        with pytest.raises(SchemaMismatchError, match="Unsupported declared source dtype"):
            read_source(path, schema_overrides={"a": "NotAType"})

    def test_parquet_schema_declarations_validate_without_replacing_schema(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "source.parquet"
        pl.DataFrame(
            {"quote_id": ["001"], "premium": [10.5], "unused": [99]},
        ).write_parquet(path)

        lf = read_source(path, schema_overrides={"quote_id": "String"})

        assert lf.collect_schema().names() == ["quote_id", "premium", "unused"]
        assert lf.collect()["quote_id"].to_list() == ["001"]

    def test_parquet_schema_declaration_mismatch_fails_loudly(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "source.parquet"
        pl.DataFrame({"premium": [10.5]}).write_parquet(path)

        with pytest.raises(SchemaMismatchError, match="Declared source schema mismatch"):
            read_source(path, schema_overrides={"premium": "String"})

    def test_json_schema_declaration_mismatch_fails_loudly(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "source.json"
        pl.DataFrame({"premium": [10.5]}).write_json(path)

        with pytest.raises(SchemaMismatchError, match="Declared source schema mismatch"):
            read_source(path, schema_overrides={"premium": "String"})


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


# ---------------------------------------------------------------------------
# Data source adaptors
# ---------------------------------------------------------------------------


class TestDataSourceAdapterFlatFile:
    def test_defaults_to_flat_file_source_type(self, tmp_path: Path) -> None:
        path = tmp_path / "source.parquet"
        pl.DataFrame({"policy_id": [101, 102]}).write_parquet(path)

        adapter = build_data_source_adapter({"path": str(path)})

        assert adapter.source_type == "flat_file"
        assert adapter.location == str(path)
        assert adapter.read().collect()["policy_id"].to_list() == [101, 102]

    def test_read_data_source_uses_the_same_adapter_boundary(self, tmp_path: Path) -> None:
        path = tmp_path / "source.csv"
        pl.DataFrame({"premium": [10.5, 12.0]}).write_csv(path)

        result = read_data_source({"sourceType": "flat_file", "path": str(path)}).collect()

        assert result["premium"].to_list() == [10.5, 12.0]

    def test_read_data_source_forwards_projection_and_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "source.csv"
        path.write_text("quote_id,premium,unused\n001,10.5,x\n", encoding="utf-8")

        lf = read_data_source(
            {
                "sourceType": "flat_file",
                "path": str(path),
                "schema_overrides": {"quote_id": "String", "premium": "Float64"},
            },
            profile=ExecutionProfile.LAZY_SINK,
            columns=["quote_id", "premium"],
        )

        assert lf.collect_schema().names() == ["quote_id", "premium"]
        result = lf.collect()
        assert result["quote_id"].to_list() == ["001"]

    @pytest.mark.parametrize(
        "schema_key",
        ["schema_overrides", "dtypes", "column_dtypes", "schema"],
    )
    def test_read_data_source_accepts_all_declared_dtype_config_keys(
        self,
        tmp_path: Path,
        schema_key: str,
    ) -> None:
        path = tmp_path / "source.csv"
        path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        result = read_data_source(
            {
                "sourceType": "flat_file",
                "path": str(path),
                schema_key: {"quote_id": "String", "premium": "Float64"},
            },
            profile=ExecutionProfile.AUTO_RANGE,
        ).collect()

        assert result.schema["quote_id"] == pl.String
        assert result["quote_id"].to_list() == ["001"]

    def test_read_data_source_rejects_bounded_csv_without_dtypes(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "source.csv"
        path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        with pytest.raises(BoundedMemoryUnsupportedError, match="CSV sources require"):
            read_data_source(
                {
                    "sourceType": "flat_file",
                    "path": str(path),
                    "expected_columns": ["quote_id", "premium"],
                },
                profile=ExecutionProfile.AUTO_RANGE,
            )

    def test_source_config_is_not_mutated(self, tmp_path: Path) -> None:
        path = tmp_path / "source.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(path)
        config = {"sourceType": "flat_file", "path": str(path), "code": "df = df"}

        build_data_source_adapter(config)

        assert config == {"sourceType": "flat_file", "path": str(path), "code": "df = df"}

    def test_flat_file_requires_path(self) -> None:
        with pytest.raises(ValueError, match="flat_file.*path"):
            build_data_source_adapter({"sourceType": "flat_file", "path": ""})


class TestDataSourceAdapterDatabricks:
    def test_databricks_delegates_to_cached_table_reader(self) -> None:
        sentinel = pl.DataFrame({"x": [1]}).lazy()

        with patch("haute._databricks_io.read_cached_table", return_value=sentinel) as read_cached:
            adapter = build_data_source_adapter(
                {"sourceType": "databricks", "table": "cat.sch.policies"}
            )
            result = adapter.read()

        assert adapter.source_type == "databricks"
        assert adapter.location == "cat.sch.policies"
        assert result is sentinel
        read_cached.assert_called_once_with("cat.sch.policies")

    def test_databricks_requires_table(self) -> None:
        with pytest.raises(ValueError, match="databricks.*table"):
            build_data_source_adapter({"sourceType": "databricks", "table": ""})


class TestDataSourceAdapterErrors:
    def test_unknown_source_type_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="Unsupported data source type"):
            build_data_source_adapter({"sourceType": "warehouse", "path": "data.parquet"})


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
