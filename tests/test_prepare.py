"""Tests for haute.clean_columns — column preparation from dot-notation."""

from __future__ import annotations

import polars as pl
import pytest

from haute.prepare import (
    _build_rename_map,
    _detect_arrays,
    _detect_boolean_groups,
    _singularise,
    clean_columns,
)
from haute._json_flatten import schema_columns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_SCHEMA: dict = {
    "proposer": {
        "first_name": "str",
        "gender": "str",
        "licence": {
            "licence_type": "str",
            "licence_held_years": "int",
        },
        "claims": {
            "$max": 3,
            "$items": {
                "claim_date": "str",
                "claim_type": "str",
                "amount_paid": "float",
            },
        },
    },
    "additional_drivers": {
        "$max": 2,
        "$items": {
            "first_name": "str",
            "gender": "str",
            "licence": {
                "licence_type": "str",
            },
        },
    },
    "vehicle": {
        "make": "str",
        "model": "str",
        "number_of_seats": "int",
        "security": {
            "alarm": "str",
            "immobiliser": "str",
        },
    },
    "address": {
        "postcode": "str",
        "city": "str",
    },
    "policy_details": {
        "cover_type": "str",
    },
    "add_ons": {
        "breakdown_cover": {
            "selected": "bool",
            "level": "str",
        },
        "legal_expenses": {
            "selected": "bool",
            "level": "str",
        },
    },
}


def _make_lazy(schema: dict) -> pl.LazyFrame:
    """Build a LazyFrame with all columns from the schema, filled with nulls."""
    cols = schema_columns(schema)
    data = {c: [None] for c in cols}
    return pl.LazyFrame(data)


@pytest.fixture
def simple_lf():
    return _make_lazy(SIMPLE_SCHEMA)


# ---------------------------------------------------------------------------
# Singularisation
# ---------------------------------------------------------------------------


class TestSingularise:
    def test_basic_s(self):
        assert _singularise("drivers") == "driver"

    def test_ies(self):
        assert _singularise("policies") == "policy"

    def test_sses(self):
        assert _singularise("addresses") == "address"

    def test_ches(self):
        assert _singularise("matches") == "match"

    def test_irregular(self):
        assert _singularise("children") == "child"
        assert _singularise("buses") == "bus"

    def test_uncountable(self):
        assert _singularise("series") == "series"
        assert _singularise("species") == "species"
        assert _singularise("news") == "news"
        assert _singularise("status") == "status"


# ---------------------------------------------------------------------------
# Pattern detection from column names
# ---------------------------------------------------------------------------


class TestDetectArrays:
    def test_simple_array(self):
        cols = [
            "additional_drivers.1.gender",
            "additional_drivers.2.gender",
            "additional_drivers.1.name",
            "additional_drivers.2.name",
        ]
        arrays = _detect_arrays(cols)
        assert "additional_drivers" in arrays
        assert len(arrays["additional_drivers"]) == 2  # 2 slots

    def test_nested_array(self):
        cols = [
            "proposer.claims.1.date",
            "proposer.claims.2.date",
            "proposer.claims.1.amount",
            "proposer.claims.2.amount",
        ]
        arrays = _detect_arrays(cols)
        assert "proposer.claims" in arrays

    def test_no_arrays(self):
        cols = ["proposer.gender", "vehicle.make"]
        arrays = _detect_arrays(cols)
        assert arrays == {}


class TestDetectBooleanGroups:
    def test_selected_pattern(self):
        cols = [
            "add_ons.breakdown_cover.selected",
            "add_ons.legal_expenses.selected",
            "add_ons.breakdown_cover.level",
            "add_ons.legal_expenses.level",
        ]
        groups = _detect_boolean_groups(cols)
        assert "add_ons" in groups
        assert len(groups["add_ons"]) == 2

    def test_active_pattern(self):
        cols = ["discounts.volume.active", "discounts.loyalty.active", "discounts.seasonal.active"]
        groups = _detect_boolean_groups(cols)
        assert "discounts" in groups
        assert len(groups["discounts"]) == 3

    def test_no_groups(self):
        cols = ["proposer.gender", "vehicle.make"]
        groups = _detect_boolean_groups(cols)
        assert groups == {}

    def test_single_child_not_group(self):
        cols = ["add_ons.breakdown_cover.selected"]
        groups = _detect_boolean_groups(cols)
        assert groups == {}


# ---------------------------------------------------------------------------
# Mechanical rename
# ---------------------------------------------------------------------------


class TestMechanicalRename:
    def test_simple_dot_replacement(self):
        cols = ["proposer.gender", "vehicle.make", "address.postcode"]
        rmap = _build_rename_map(cols)
        assert rmap == {
            "proposer.gender": "proposer_gender",
            "vehicle.make": "vehicle_make",
            "address.postcode": "address_postcode",
        }

    def test_deep_nesting(self):
        cols = ["proposer.licence.licence_type"]
        rmap = _build_rename_map(cols)
        assert rmap["proposer.licence.licence_type"] == "proposer_licence_licence_type"

    def test_array_columns(self):
        cols = ["additional_drivers.1.gender"]
        rmap = _build_rename_map(cols)
        assert rmap["additional_drivers.1.gender"] == "additional_drivers_1_gender"

    def test_no_dots_unchanged(self):
        rmap = _build_rename_map(["already_clean"])
        assert rmap == {}


# ---------------------------------------------------------------------------
# Rename overrides
# ---------------------------------------------------------------------------


class TestRenameOverrides:
    def test_override_by_source(self, simple_lf: pl.LazyFrame):
        result = clean_columns(simple_lf, rename={"address.postcode": "postcode"})
        cols = result.collect_schema().names()
        assert "postcode" in cols
        assert "address_postcode" not in cols

    def test_override_by_underscore_name(self, simple_lf: pl.LazyFrame):
        result = clean_columns(simple_lf, rename={"address_postcode": "postcode"})
        cols = result.collect_schema().names()
        assert "postcode" in cols

    def test_collision_raises(self):
        lf = pl.LazyFrame({"a.x": [1], "b.x": [2]})
        with pytest.raises(ValueError, match="collision"):
            clean_columns(lf, rename={"a.x": "same", "b.x": "same"})


# ---------------------------------------------------------------------------
# Polars chaining
# ---------------------------------------------------------------------------


class TestPolarsChaining:
    def test_drop_after(self, simple_lf: pl.LazyFrame):
        result = clean_columns(simple_lf).drop("address_postcode")
        cols = result.collect_schema().names()
        assert "address_postcode" not in cols

    def test_select_after(self, simple_lf: pl.LazyFrame):
        result = clean_columns(simple_lf).select(["address_postcode", "vehicle_make"])
        cols = result.collect_schema().names()
        assert cols == ["address_postcode", "vehicle_make"]


# ---------------------------------------------------------------------------
# max_array_expand
# ---------------------------------------------------------------------------


class TestMaxArrayExpand:
    def test_large_array_drops_slots(self):
        # Build a df with 20 slots
        data = {f"items.{i}.name": [None] for i in range(1, 21)}
        data.update({f"items.{i}.value": [None] for i in range(1, 21)})
        lf = pl.LazyFrame(data)
        result = clean_columns(lf, max_array_expand=5)
        cols = result.collect_schema().names()
        assert not any("items_1" in c for c in cols)
        assert "number_of_items" in cols
        assert "has_item" in cols

    def test_small_array_keeps_slots(self):
        data = {f"items.{i}.name": [None] for i in range(1, 4)}
        lf = pl.LazyFrame(data)
        result = clean_columns(lf, max_array_expand=10)
        cols = result.collect_schema().names()
        assert "items_1_name" in cols


# ---------------------------------------------------------------------------
# Array counting
# ---------------------------------------------------------------------------


class TestArrayCounting:
    def test_top_level_array(self, simple_lf: pl.LazyFrame):
        result = clean_columns(simple_lf)
        cols = result.collect_schema().names()
        assert "number_of_additional_drivers" in cols
        assert "has_additional_driver" in cols

    def test_nested_array_in_object(self, simple_lf: pl.LazyFrame):
        result = clean_columns(simple_lf)
        cols = result.collect_schema().names()
        assert "number_of_proposer_claims" in cols
        assert "has_proposer_claim" in cols


# ---------------------------------------------------------------------------
# Boolean group counting
# ---------------------------------------------------------------------------


class TestBooleanGroupCounting:
    def test_selected_pattern(self):
        data = {
            "add_ons.cover_a.selected": [True],
            "add_ons.cover_a.level": ["basic"],
            "add_ons.cover_b.selected": [False],
            "add_ons.cover_b.level": [None],
        }
        lf = pl.LazyFrame(data)
        result = clean_columns(lf).collect()
        assert result["number_of_add_ons"][0] == 1

    def test_active_pattern(self):
        data = {
            "discounts.volume.active": [True],
            "discounts.loyalty.active": [True],
            "discounts.seasonal.active": [False],
        }
        lf = pl.LazyFrame(data)
        result = clean_columns(lf).collect()
        assert result["number_of_discounts"][0] == 2

    def test_included_pattern(self):
        data = {
            "covers.buildings.included": [True],
            "covers.contents.included": [True],
        }
        lf = pl.LazyFrame(data)
        result = clean_columns(lf).collect()
        assert result["number_of_covers"][0] == 2


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_no_dots_remain(self, simple_lf: pl.LazyFrame):
        result = clean_columns(simple_lf)
        cols = result.collect_schema().names()
        assert not any("." in c for c in cols)

    def test_importable(self):
        import haute

        assert callable(haute.clean_columns)

    def test_no_dot_columns(self):
        """DataFrames without dots pass through unchanged."""
        lf = pl.LazyFrame({"a": [1], "b": [2]})
        result = clean_columns(lf)
        assert result.collect_schema().names() == ["a", "b"]

    def test_no_schema_needed(self, simple_lf: pl.LazyFrame):
        """clean_columns works without any schema argument."""
        result = clean_columns(simple_lf)
        cols = result.collect_schema().names()
        # Renamed + counts generated, all from column names alone
        assert "additional_drivers_1_gender" in cols
        assert "number_of_additional_drivers" in cols
        assert "has_additional_driver" in cols
