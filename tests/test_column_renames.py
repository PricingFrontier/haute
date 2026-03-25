"""Tests for _apply_column_renames and its interaction with _apply_selected_columns."""

from __future__ import annotations

import polars as pl
import pytest

from haute._execute_lazy import _apply_column_renames, _apply_selected_columns


class TestApplyColumnRenames:
    def test_basic_rename(self):
        df = pl.DataFrame({"a.b": [1], "c.d": [2]})
        config = {"column_renames": {"a.b": "ab", "c.d": "cd"}}
        result = _apply_column_renames(df, config)
        assert result.columns == ["ab", "cd"]

    def test_no_renames_config(self):
        df = pl.DataFrame({"a": [1]})
        result = _apply_column_renames(df, {})
        assert result.columns == ["a"]

    def test_empty_renames(self):
        df = pl.DataFrame({"a": [1]})
        result = _apply_column_renames(df, {"column_renames": {}})
        assert result.columns == ["a"]

    def test_nonexistent_column_skipped(self):
        df = pl.DataFrame({"a": [1]})
        config = {"column_renames": {"nonexistent": "b"}}
        result = _apply_column_renames(df, config)
        assert result.columns == ["a"]

    def test_same_name_skipped(self):
        df = pl.DataFrame({"a": [1]})
        config = {"column_renames": {"a": "a"}}
        result = _apply_column_renames(df, config)
        assert result.columns == ["a"]

    def test_works_with_lazyframe(self):
        lf = pl.LazyFrame({"x.y": [1]})
        config = {"column_renames": {"x.y": "xy"}}
        result = _apply_column_renames(lf, config)
        assert result.collect_schema().names() == ["xy"]


class TestRenameAndSelectInteraction:
    """Verify that select runs BEFORE rename so selected_columns uses original names."""

    def test_select_then_rename(self):
        df = pl.DataFrame({"a.b": [1], "c.d": [2], "e.f": [3]})
        config = {
            "selected_columns": ["a.b", "c.d"],  # original names
            "column_renames": {"a.b": "ab", "c.d": "cd"},
        }
        # Select first (keeps a.b and c.d), then rename
        selected = _apply_selected_columns(df, config)
        renamed = _apply_column_renames(selected, config)
        assert set(renamed.columns) == {"ab", "cd"}

    def test_renamed_column_not_lost(self):
        """Regression: if rename runs before select, renamed columns would be
        lost because selected_columns has the old names."""
        df = pl.DataFrame({"proposer.gender": [1], "vehicle.make": [2]})
        config = {
            "selected_columns": ["proposer.gender"],
            "column_renames": {"proposer.gender": "gender"},
        }
        selected = _apply_selected_columns(df, config)
        renamed = _apply_column_renames(selected, config)
        assert renamed.columns == ["gender"]
