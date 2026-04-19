"""Tests for _extract_column_refs -- stale column reference detection."""

from __future__ import annotations

from haute.executor import _extract_column_refs


class TestEmptyAndMinimalConfigs:
    """Configs with no column references should return an empty set."""

    def test_empty_config(self) -> None:
        assert _extract_column_refs({}) == set()

    def test_config_with_unrelated_keys(self) -> None:
        assert _extract_column_refs({"iterations": 1000, "learning_rate": 0.05}) == set()

    def test_none_values_for_list_keys(self) -> None:
        """None in place of a list should not crash."""
        assert _extract_column_refs({"selected_columns": None, "exclude": None}) == set()

    def test_empty_lists(self) -> None:
        assert _extract_column_refs({"selected_columns": [], "exclude": []}) == set()

    def test_empty_strings(self) -> None:
        """Empty string values should be ignored, not added to the set."""
        assert _extract_column_refs({"target": "", "weight": "", "offset": ""}) == set()


class TestSelectedColumns:
    """The selected_columns key is used by any node type."""

    def test_basic(self) -> None:
        config = {"selected_columns": ["quote_id", "premium", "age"]}
        assert _extract_column_refs(config) == {"quote_id", "premium", "age"}

    def test_non_string_entries_skipped(self) -> None:
        config = {"selected_columns": ["valid", 42, None, True, "also_valid"]}
        assert _extract_column_refs(config) == {"valid", "also_valid"}


class TestModellingConfig:
    """Modelling nodes store target, weight, offset, and exclude."""

    def test_target_only(self) -> None:
        config = {"target": "loss_ratio"}
        assert _extract_column_refs(config) == {"loss_ratio"}

    def test_target_weight_offset(self) -> None:
        config = {"target": "claim_count", "weight": "exposure", "offset": "log_exposure"}
        assert _extract_column_refs(config) == {"claim_count", "exposure", "log_exposure"}

    def test_exclude_list(self) -> None:
        config = {"exclude": ["quote_id", "policy_number", "postcode"]}
        assert _extract_column_refs(config) == {"quote_id", "policy_number", "postcode"}

    def test_full_modelling_config(self) -> None:
        config = {
            "target": "sale_flag",
            "weight": "exposure",
            "exclude": ["quote_id", "name"],
            "selected_columns": ["sale_flag", "exposure", "age", "quote_id", "name"],
        }
        refs = _extract_column_refs(config)
        assert refs == {"sale_flag", "exposure", "quote_id", "name", "age"}

    def test_exclude_with_non_string_entries(self) -> None:
        config = {"exclude": ["valid_col", 123, None]}
        assert _extract_column_refs(config) == {"valid_col"}


class TestBandingConfig:
    """Banding nodes store factors as a list of dicts with 'column' keys."""

    def test_basic_factors(self) -> None:
        config = {
            "factors": [
                {"column": "driver_age", "outputColumn": "age_band"},
                {"column": "vehicle_value", "outputColumn": "value_band"},
            ]
        }
        refs = _extract_column_refs(config)
        assert "driver_age" in refs
        assert "vehicle_value" in refs
        # outputColumn is created, not read -- should NOT be in refs
        assert "age_band" not in refs
        assert "value_band" not in refs

    def test_factor_with_missing_column_key(self) -> None:
        config = {"factors": [{"outputColumn": "band"}]}
        assert _extract_column_refs(config) == set()

    def test_malformed_factor_entries(self) -> None:
        """Non-dict entries in factors should be skipped gracefully."""
        config = {"factors": ["not_a_dict", 42, None, {"column": "valid"}]}
        assert _extract_column_refs(config) == {"valid"}


class TestRatingStepConfig:
    """Rating step nodes store tables as a list of dicts with 'factors' keys."""

    def test_basic_tables(self) -> None:
        config = {
            "tables": [
                {"factors": ["age_band", "region"], "outputColumn": "base_rate"},
            ]
        }
        refs = _extract_column_refs(config)
        assert refs == {"age_band", "region"}

    def test_multiple_tables(self) -> None:
        config = {
            "tables": [
                {"factors": ["age_band"]},
                {"factors": ["region", "cover_type"]},
            ]
        }
        assert _extract_column_refs(config) == {"age_band", "region", "cover_type"}

    def test_malformed_table_entries(self) -> None:
        config = {"tables": ["not_a_dict", {"factors": ["valid"]}, None]}
        assert _extract_column_refs(config) == {"valid"}

    def test_non_string_factors_skipped(self) -> None:
        config = {"tables": [{"factors": ["valid", 42, None]}]}
        assert _extract_column_refs(config) == {"valid"}


class TestOutputColumnExclusion:
    """Output columns (created by the node) should be excluded from refs."""

    def test_output_column_excluded(self) -> None:
        config = {
            "selected_columns": ["input_col", "prediction"],
            "output_column": "prediction",
        }
        refs = _extract_column_refs(config)
        assert "input_col" in refs
        assert "prediction" not in refs

    def test_outputColumn_camelCase_excluded(self) -> None:
        config = {
            "factors": [{"column": "age", "outputColumn": "age_band"}],
            "outputColumn": "combined_rate",
        }
        refs = _extract_column_refs(config)
        assert "age" in refs
        assert "combined_rate" not in refs

    def test_output_column_not_present(self) -> None:
        """When output_column is absent, nothing extra is discarded."""
        config = {"selected_columns": ["a", "b"]}
        assert _extract_column_refs(config) == {"a", "b"}

    def test_none_output_column(self) -> None:
        """None for output_column should not crash discard."""
        config = {"selected_columns": ["a"], "output_column": None}
        assert _extract_column_refs(config) == {"a"}


class TestCombinedConfigs:
    """Configs that combine multiple reference sources."""

    def test_all_sources_combined(self) -> None:
        config = {
            "selected_columns": ["col_a"],
            "target": "col_b",
            "exclude": ["col_c"],
            "factors": [{"column": "col_d"}],
            "tables": [{"factors": ["col_e"]}],
            "output_column": "col_f",
        }
        refs = _extract_column_refs(config)
        assert refs == {"col_a", "col_b", "col_c", "col_d", "col_e"}
        assert "col_f" not in refs

    def test_deduplication(self) -> None:
        """Same column referenced in multiple places should appear once."""
        config = {
            "selected_columns": ["age"],
            "target": "age",
            "exclude": ["age"],
        }
        assert _extract_column_refs(config) == {"age"}
