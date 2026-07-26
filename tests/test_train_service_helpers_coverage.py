"""Coverage tests for TrainService pure column-demand helpers.

Restores per-file coverage after the multi-frame merge added column-demand
edge/error arms (`_string_list_config` validation; split-strategy column
derivation) that the route-level and engine tests do not reach directly.
These are pure functions, so they are exercised by direct call.
"""

from __future__ import annotations

import pytest

from haute.routes._train_service import (
    _build_training_feature_selection,
    _check_gpu_vram,
    _job_elapsed_seconds,
    _string_list_config,
    _training_metadata_reasons,
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


class TestTrainingMetadataReasons:
    @pytest.mark.parametrize(
        ("split", "column"),
        [
            ({"strategy": "temporal", "date_column": "asof"}, "asof"),
            ({"strategy": "group", "group_column": "policy"}, "policy"),
        ],
    )
    def test_split_columns_are_explained(self, split, column) -> None:
        reasons = _training_metadata_reasons({"target": "y", "split": split})

        assert reasons == {"y": "target", column: "split"}

    def test_non_mapping_split_is_ignored_and_role_precedence_is_stable(self) -> None:
        reasons = _training_metadata_reasons(
            {
                "target": "shared",
                "weight": "shared",
                "id_columns": ["shared"],
                "split": "invalid",
            }
        )

        assert reasons == {"shared": "target"}


class TestTrainingRequiredColumnsByNode:
    def test_returns_none_without_target(self) -> None:
        assert _training_required_columns_by_node("n", {"algorithm": "catboost"}) is None


class TestTrainingFeatureSelection:
    @pytest.mark.parametrize(
        ("schema", "message"),
        [
            (["target", ""], "non-empty column names"),
            (["target", 1], "non-empty column names"),
            (["target", "feature", "feature"], "duplicate column names"),
        ],
    )
    def test_invalid_schema_metadata_fails_before_selection(self, schema, message) -> None:
        with pytest.raises(ValueError, match=message):
            _build_training_feature_selection(
                {"algorithm": "catboost", "target": "target"},
                schema,
            )

    def test_explicit_features_preserve_config_order_and_explain_every_other_column(self) -> None:
        diagnostic = _build_training_feature_selection(
            {
                "algorithm": "catboost",
                "target": "target",
                "weight": "weight",
                "feature_columns": ["feature_b", "feature_a"],
                "exclude": ["ignored"],
            },
            ["target", "weight", "feature_a", "feature_b", "ignored", "unselected"],
        )

        assert diagnostic.mode == "explicit"
        assert diagnostic.feature_count == 2
        assert diagnostic.features.items == ["feature_b", "feature_a"]
        assert [(item.column, item.reason) for item in diagnostic.retained_metadata.items] == [
            ("target", "target"),
            ("weight", "weight"),
        ]
        assert [(item.column, item.reason) for item in diagnostic.excluded_columns.items] == [
            ("target", "target"),
            ("weight", "weight"),
            ("ignored", "configured_exclusion"),
            ("unselected", "not_selected"),
        ]

    def test_all_except_features_preserve_schema_order(self) -> None:
        diagnostic = _build_training_feature_selection(
            {
                "algorithm": "catboost",
                "target": "target",
                "id_columns": ["policy_id"],
                "exclude": ["ignored"],
            },
            ["feature_b", "target", "policy_id", "feature_a", "ignored"],
        )

        assert diagnostic.mode == "all_except"
        assert diagnostic.features.items == ["feature_b", "feature_a"]
        assert diagnostic.feature_count == 2

    def test_glm_terms_follow_schema_order_and_missing_columns_fail_before_execution(self) -> None:
        diagnostic = _build_training_feature_selection(
            {
                "algorithm": "glm",
                "target": "target",
                "terms": {"feature_a": {}, "feature_b": {}},
            },
            ["feature_b", "target", "feature_a", "unused"],
        )

        assert diagnostic.mode == "glm_terms"
        assert diagnostic.features.items == ["feature_b", "feature_a"]
        assert diagnostic.excluded_columns.items[-1].reason == "not_in_formula"

        with pytest.raises(ValueError, match="Configured feature column.*missing"):
            _build_training_feature_selection(
                {
                    "algorithm": "catboost",
                    "target": "target",
                    "feature_columns": ["missing"],
                },
                ["target", "feature"],
            )

        with pytest.raises(ValueError, match="GLM terms reference columns.*missing"):
            _build_training_feature_selection(
                {
                    "algorithm": "glm",
                    "target": "target",
                    "terms": {"missing": {}},
                },
                ["target", "feature"],
            )

    def test_empty_feature_set_fails_and_high_cardinality_detail_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="No feature columns remaining"):
            _build_training_feature_selection(
                {"algorithm": "catboost", "target": "target"},
                ["target"],
            )

        diagnostic = _build_training_feature_selection(
            {
                "algorithm": "catboost",
                "target": "target",
                "feature_columns": ["feature"],
            },
            ["target", "feature", *(f"unused_{index:03d}" for index in range(140))],
        )
        assert diagnostic.detail_state == "truncated"
        assert diagnostic.excluded_columns.state == "truncated"
        assert diagnostic.excluded_columns.total_count == 141
        assert len(diagnostic.excluded_columns.items) == 128


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
