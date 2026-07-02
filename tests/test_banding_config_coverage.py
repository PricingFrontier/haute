"""Tests for haute._banding_config — sidecar compaction validation."""

from __future__ import annotations

import math

import pytest

from haute._banding_config import (
    _compact_rule_map,
    _validate_map_value,
    compact_banding_config_for_sidecar,
)

# ---------------------------------------------------------------------------
# _validate_map_value
# ---------------------------------------------------------------------------


class TestValidateMapValue:
    def test_none_rejected(self):
        with pytest.raises(ValueError, match="must map to a non-empty value"):
            _validate_map_value(None, "rule")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="must map to a non-empty value"):
            _validate_map_value("", "rule")

    def test_non_scalar_rejected(self):
        with pytest.raises(ValueError, match="must map to a JSON scalar value"):
            _validate_map_value({"nested": 1}, "rule")

    def test_non_finite_float_rejected(self):
        with pytest.raises(ValueError, match="must map to a JSON scalar value"):
            _validate_map_value(math.inf, "rule")

    def test_scalar_accepted(self):
        # No exception for valid scalars.
        _validate_map_value("ok", "rule")
        _validate_map_value(3, "rule")
        _validate_map_value(1.5, "rule")
        _validate_map_value(True, "rule")


# ---------------------------------------------------------------------------
# _compact_rule_map (dict-shaped rules)
# ---------------------------------------------------------------------------


class TestCompactRuleMap:
    def test_categorical_empty_key_rejected(self):
        with pytest.raises(ValueError, match="categorical rule key must not be empty"):
            _compact_rule_map({"": "A"}, "categorical")

    def test_categorical_bad_value_rejected(self):
        with pytest.raises(ValueError, match="must map to a non-empty value"):
            _compact_rule_map({"x": None}, "categorical")

    def test_categorical_duplicate_key_rejected(self):
        # After str() coercion 1 and "1" collide.
        with pytest.raises(ValueError, match="duplicate categorical rule key"):
            _compact_rule_map({1: "A", "1": "B"}, "categorical")

    def test_categorical_valid_compacts(self):
        result = _compact_rule_map({"north": "A", "south": "B"}, "categorical")
        assert result == {"north": "A", "south": "B"}

    def test_breakpoints_bad_value_rejected(self):
        with pytest.raises(ValueError, match="must map to a non-empty value"):
            _compact_rule_map({"10": ""}, "breakpoints")

    def test_breakpoints_duplicate_key_rejected(self):
        with pytest.raises(ValueError, match="duplicate breakpoint rule key"):
            _compact_rule_map({10: "low", "10": "high"}, "breakpoints")

    def test_breakpoints_valid_compacts(self):
        result = _compact_rule_map({"10": "low", "20": "high"}, "breakpoints")
        assert result == {"10": "low", "20": "high"}

    def test_unknown_banding_type_rejected(self):
        with pytest.raises(ValueError, match="banding rules must be a list"):
            _compact_rule_map({"x": "A"}, "continuous")


# ---------------------------------------------------------------------------
# compact_banding_config_for_sidecar — row-array (list) rules + shape guards
# ---------------------------------------------------------------------------


def _compact_rules(banding, rules):
    config = {"factors": [{"banding": banding, "rules": rules}]}
    result = compact_banding_config_for_sidecar(config)
    return result["factors"][0]["rules"]


class TestCompactConfigRowRules:
    def test_categorical_rows_compact_to_map(self):
        rules = [
            {"value": "north", "assignment": "A"},
            {"value": "south", "assignment": "B"},
        ]
        assert _compact_rules("categorical", rules) == {"north": "A", "south": "B"}

    def test_breakpoint_rows_compact_to_map(self):
        rules = [
            {"boundary": "10", "label": "low"},
            {"boundary": "20", "label": "high"},
        ]
        assert _compact_rules("breakpoints", rules) == {"10": "low", "20": "high"}

    def test_breakpoint_allows_empty_key_row(self):
        rules = [
            {"boundary": "", "label": "catch-all"},
            {"boundary": "10", "label": "low"},
        ]
        assert _compact_rules("breakpoints", rules) == {"": "catch-all", "10": "low"}

    def test_categorical_rejects_empty_key_row(self):
        rules = [{"value": "", "assignment": "A"}]
        with pytest.raises(ValueError, match="categorical rules\\[0\\] requires value"):
            _compact_rules("categorical", rules)

    def test_row_must_be_object(self):
        with pytest.raises(ValueError, match="categorical rules\\[0\\] must be an object"):
            _compact_rules("categorical", ["not-a-dict"])

    def test_row_missing_key_field_rejected(self):
        rules = [{"assignment": "A"}]
        with pytest.raises(ValueError, match="categorical rules\\[0\\] requires value"):
            _compact_rules("categorical", rules)

    def test_row_missing_value_rejected(self):
        rules = [{"value": "north"}]
        with pytest.raises(ValueError, match="rule 'north' requires assignment"):
            _compact_rules("categorical", rules)

    def test_row_empty_value_rejected(self):
        rules = [{"value": "north", "assignment": ""}]
        with pytest.raises(ValueError, match="rule 'north' requires assignment"):
            _compact_rules("categorical", rules)

    def test_row_non_scalar_value_rejected(self):
        rules = [{"value": "north", "assignment": {"x": 1}}]
        with pytest.raises(ValueError, match="must map to a JSON scalar value"):
            _compact_rules("categorical", rules)

    def test_row_duplicate_key_rejected(self):
        rules = [
            {"value": "north", "assignment": "A"},
            {"value": "north", "assignment": "B"},
        ]
        with pytest.raises(ValueError, match="duplicate categorical rule key"):
            _compact_rules("categorical", rules)


# ---------------------------------------------------------------------------
# compact_banding_config_for_sidecar — config-level shapes and guards
# ---------------------------------------------------------------------------


class TestCompactConfigShapes:
    def test_dict_rules_route_through_map_compaction(self):
        config = {
            "factors": [{"banding": "categorical", "rules": {"north": "A"}}],
        }
        result = compact_banding_config_for_sidecar(config)
        assert result["factors"][0]["rules"] == {"north": "A"}

    def test_none_rules_become_empty_map(self):
        config = {"factors": [{"banding": "categorical"}]}
        result = compact_banding_config_for_sidecar(config)
        assert result["factors"][0]["rules"] == {}

    def test_non_compact_banding_type_left_untouched(self):
        config = {"factors": [{"banding": "continuous", "rules": [1, 2, 3]}]}
        result = compact_banding_config_for_sidecar(config)
        assert result["factors"][0]["rules"] == [1, 2, 3]

    def test_scalar_rules_rejected(self):
        config = {"factors": [{"banding": "categorical", "rules": 5}]}
        with pytest.raises(ValueError, match="banding rules must be a list"):
            compact_banding_config_for_sidecar(config)

    def test_no_factors_returns_config(self):
        config = {"other": 1}
        assert compact_banding_config_for_sidecar(config) == {"other": 1}

    def test_factors_must_be_list(self):
        with pytest.raises(ValueError, match="banding factors must be a list"):
            compact_banding_config_for_sidecar({"factors": {}})

    def test_factor_must_be_object(self):
        with pytest.raises(ValueError, match="banding factors\\[0\\] must be an object"):
            compact_banding_config_for_sidecar({"factors": ["x"]})
