"""Coverage tests for TrainService pure column-demand helpers.

Restores per-file coverage after the multi-frame merge added column-demand
edge/error arms (`_string_list_config` validation; split-strategy column
derivation) that the route-level and engine tests do not reach directly.
These are pure functions, so they are exercised by direct call.
"""

from __future__ import annotations

import pytest

from haute.routes._train_service import (
    _check_gpu_vram,
    _job_elapsed_seconds,
    _string_list_config,
    _training_required_columns_by_node,
    _training_required_metadata_columns,
    _VramCheck,
)


class TestStringListConfig:
    def test_missing_key_returns_empty(self) -> None:
        assert _string_list_config({}, "id_columns") == []

    def test_non_list_raises(self) -> None:
        with pytest.raises(ValueError, match="id_columns must be a list"):
            _string_list_config({"id_columns": "policy_id"}, "id_columns")

    def test_empty_string_member_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _string_list_config({"id_columns": ["ok", ""]}, "id_columns")

    def test_non_string_member_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _string_list_config({"id_columns": [123]}, "id_columns")

    def test_dedupes_preserving_order(self) -> None:
        assert _string_list_config({"id_columns": ["a", "b", "a"]}, "id_columns") == ["a", "b"]


class TestTrainingRequiredMetadataColumns:
    def test_non_dict_split_is_skipped(self) -> None:
        assert _training_required_metadata_columns({"target": "y", "split": "nonsense"}) == {"y"}

    def test_temporal_split_adds_date_column(self) -> None:
        cols = _training_required_metadata_columns(
            {"target": "y", "split": {"strategy": "temporal", "date_column": "asof"}}
        )
        assert {"y", "asof"} <= cols

    def test_group_split_adds_group_column(self) -> None:
        cols = _training_required_metadata_columns(
            {"target": "y", "split": {"strategy": "group", "group_column": "policy"}}
        )
        assert {"y", "policy"} <= cols

    def test_aux_and_id_columns_included(self) -> None:
        cols = _training_required_metadata_columns(
            {
                "target": "y",
                "weight": "w",
                "offset": "o",
                "fold_column": "f",
                "id_columns": ["pid"],
            }
        )
        assert {"y", "w", "o", "f", "pid"} <= cols


class TestTrainingRequiredColumnsByNode:
    def test_returns_none_without_target(self) -> None:
        assert _training_required_columns_by_node("n", {"algorithm": "catboost"}) is None


class TestJobElapsedSeconds:
    def test_falls_back_to_elapsed_seconds_field(self) -> None:
        # No start_time → uses the recorded elapsed_seconds.
        assert _job_elapsed_seconds({"elapsed_seconds": 5.0}) == 5.0

    def test_falls_back_to_default_when_no_usable_fields(self) -> None:
        assert _job_elapsed_seconds({}, fallback=2.0) == 2.0
        # Non-numeric elapsed_seconds → the configured fallback.
        assert _job_elapsed_seconds({"elapsed_seconds": "bad"}, fallback=1.5) == 1.5


class TestCheckGpuVram:
    def test_zero_rows_or_columns_returns_default_check(self) -> None:
        assert isinstance(_check_gpu_vram(0, 5, {}), _VramCheck)
        assert isinstance(_check_gpu_vram(10, 0, {}), _VramCheck)
