"""Coverage tests for TrainService pure column-demand helpers.

Restores per-file coverage after the multi-frame merge added column-demand
edge/error arms (`_string_list_config` validation; evaluation-strategy column
derivation) that the route-level and engine tests do not reach directly.
These are pure functions, so they are exercised by direct call.
"""

from __future__ import annotations

import pytest

from haute.modelling._evaluation import EvaluationConfig, generate_evaluation_plan
from haute.routes._train_service import (
    _build_training_feature_selection,
    _check_gpu_vram,
    _evaluation_preview_payload,
    _job_elapsed_seconds,
    _string_list_config,
    _training_metadata_reasons,
    _training_required_columns_by_node,
    _training_required_metadata_columns,
    _VramCheck,
)


class TestEvaluationPreviewPayload:
    def test_no_validation_omits_selection_bounds(self) -> None:
        config = EvaluationConfig.from_plain_data(
            {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "none"},
            }
        )
        plan = generate_evaluation_plan(
            config,
            source_sha256="a" * 64,
            row_count=4,
            task="regression",
        )

        assert _evaluation_preview_payload(plan) == {
            "schema_version": 1,
            "strategy": "random",
            "validation_method": "none",
            "development_rows": 4,
            "final_test_rows": 0,
            "validation_fit_count": 0,
        }

    def test_temporal_preview_requires_dates_and_omits_empty_test_range(self) -> None:
        config = EvaluationConfig.from_plain_data(
            {
                "schema_version": 1,
                "strategy": "temporal",
                "date_column": "as_of",
                "validation": {"method": "none"},
            }
        )
        dates = ["2024-03-01", "2024-01-01", "2024-02-01"]
        plan = generate_evaluation_plan(
            config,
            source_sha256="b" * 64,
            row_count=len(dates),
            task="regression",
            date_values=dates,
        )

        with pytest.raises(ValueError, match="requires exact date values"):
            _evaluation_preview_payload(plan)

        preview = _evaluation_preview_payload(plan, date_values=dates)
        assert preview["development_date_range"] == {
            "start": "2024-01-01",
            "end": "2024-03-01",
        }
        assert "final_test_date_range" not in preview


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
    def test_non_dict_evaluation_is_skipped(self) -> None:
        assert _training_required_metadata_columns({"target": "y", "evaluation": "nonsense"}) == {
            "y"
        }

    def test_temporal_evaluation_adds_date_column(self) -> None:
        cols = _training_required_metadata_columns(
            {
                "target": "y",
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "temporal",
                    "date_column": "asof",
                    "validation": {"method": "none"},
                },
            }
        )
        assert {"y", "asof"} <= cols

    def test_group_evaluation_adds_group_column(self) -> None:
        cols = _training_required_metadata_columns(
            {
                "target": "y",
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "group",
                    "group_column": "policy",
                    "seed": 42,
                    "validation": {"method": "none"},
                },
            }
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

    @pytest.mark.parametrize(
        ("evaluation", "column"),
        [
            (
                {
                    "schema_version": 1,
                    "strategy": "group",
                    "seed": 7,
                    "group_column": "household",
                    "validation": {
                        "method": "cross_validation",
                        "fold_count": 3,
                    },
                },
                "household",
            ),
            (
                {
                    "schema_version": 1,
                    "strategy": "temporal",
                    "date_column": "valuation_date",
                    "validation": {
                        "method": "cross_validation",
                        "fold_count": 3,
                        "window": "expanding",
                    },
                },
                "valuation_date",
            ),
        ],
    )
    def test_cross_validation_method_key_is_required_metadata(
        self, evaluation: dict[str, object], column: str
    ) -> None:
        cols = _training_required_metadata_columns({"target": "y", "evaluation": evaluation})

        assert cols == {"y", column}


class TestTrainingMetadataReasons:
    @pytest.mark.parametrize(
        ("evaluation", "column"),
        [
            (
                {
                    "schema_version": 1,
                    "strategy": "temporal",
                    "date_column": "asof",
                    "validation": {"method": "none"},
                },
                "asof",
            ),
            (
                {
                    "schema_version": 1,
                    "strategy": "group",
                    "group_column": "policy",
                    "seed": 42,
                    "validation": {"method": "none"},
                },
                "policy",
            ),
        ],
    )
    def test_evaluation_columns_are_explained(self, evaluation, column) -> None:
        reasons = _training_metadata_reasons({"target": "y", "evaluation": evaluation})

        assert reasons == {"y": "target", column: "evaluation"}

    def test_non_mapping_evaluation_is_ignored_and_role_precedence_is_stable(
        self,
    ) -> None:
        reasons = _training_metadata_reasons(
            {
                "target": "shared",
                "weight": "shared",
                "id_columns": ["shared"],
                "evaluation": "invalid",
            }
        )

        assert reasons == {"shared": "target"}

    def test_evaluation_key_respects_role_precedence(self) -> None:
        reasons = _training_metadata_reasons(
            {
                "target": "shared",
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "group",
                    "seed": 7,
                    "group_column": "shared",
                    "validation": {
                        "method": "cross_validation",
                        "fold_count": 3,
                    },
                },
            }
        )

        assert reasons == {"shared": "target"}

        reasons = _training_metadata_reasons(
            {
                "target": "y",
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "temporal",
                    "date_column": "asof",
                    "validation": {
                        "method": "cross_validation",
                        "fold_count": 3,
                        "window": "expanding",
                    },
                },
            }
        )
        assert reasons == {"y": "target", "asof": "evaluation"}


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
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "group",
                    "group_column": "household_id",
                    "seed": 42,
                    "validation": {"method": "single", "size": 0.2},
                },
                "exclude": ["ignored"],
            },
            [
                "feature_b",
                "target",
                "policy_id",
                "household_id",
                "feature_a",
                "ignored",
            ],
        )

        assert diagnostic.mode == "all_except"
        assert diagnostic.features.items == ["feature_b", "feature_a"]
        assert diagnostic.feature_count == 2
        assert (
            "household_id",
            "evaluation",
        ) in [(item.column, item.reason) for item in diagnostic.retained_metadata.items]

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
