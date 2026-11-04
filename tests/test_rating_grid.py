"""A rating table is written as a grid (READ-26).

Spec: specs/rating/high-level.md, the rating sidecar's grid. ``factors`` lists the factors,
``columns`` the last factor's levels, and ``rows`` one line per combination of the others:
their levels, then a rate per column, ``null`` where no entry is.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute._config_io import load_node_config, node_config_json
from haute._rating import _apply_rating_table
from haute._rating_step_config import (
    GRID_CONVERSION,
    compact_rating_config_for_sidecar,
    entries_from_grid,
    grid_from_entries,
    normalise_rating_step_config,
    refuse_levels_equal_under_factor_dtypes,
)
from haute._types import NodeType
from haute._validation_error import ConfigSettingError


def _round_trip(tmp_path: Path, config: dict) -> tuple[str, dict]:
    text = node_config_json(NodeType.RATING_STEP, config, node_label="rate")
    sidecar = tmp_path / "config" / "rating_step" / "rate.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_bytes(text.encode("utf-8"))
    return text, load_node_config("config/rating_step/rate.json", base_dir=tmp_path)


@pytest.mark.parametrize(
    "table",
    [
        {
            "factors": ["region"],
            "output_column": "region_factor",
            "entries": [{"region": "north", "value": 1.1}, {"region": "south", "value": 0}],
        },
        {
            "factors": ["age", "region"],
            "output_column": "f",
            "on_missing": "neutral",
            "default_value": 1.0,
            "factor_dtypes": {"age": {"kind": "Int64"}},
            "entries": [
                {"age": 25, "region": "north", "value": 1.2},
                {"age": 25, "region": "south", "value": 1.3},
                {"age": "25", "region": "north", "value": 1.4},
            ],
        },
        {
            # The editor holds a default as typed, text; it reads back as that text.
            "factors": ["region"],
            "output_column": "h",
            "default_value": "1",
            "entries": [{"region": "north", "value": 1.1}],
        },
        {
            "factors": ["a", "b", "c"],
            "output_column": "g",
            "entries": [
                {"a": "x", "b": 1, "c": True, "value": 2},
                {"a": "y", "b": 2, "c": False, "value": 3},
            ],
        },
    ],
    ids=["one-factor", "two-factor-sparse-typed", "typed-default", "three-factor"],
)
def test_a_table_round_trips_through_its_grid(tmp_path: Path, table: dict) -> None:
    config = {"tables": [table]}
    _text, loaded = _round_trip(tmp_path, config)
    expected = normalise_rating_step_config(config)["tables"][0]
    got = loaded["tables"][0]
    assert {k: v for k, v in got.items() if k != "entries"} == {
        k: v for k, v in expected.items() if k != "entries"
    }
    key = lambda entry: json.dumps(entry, sort_keys=True)  # noqa: E731
    assert sorted(got["entries"], key=key) == sorted(expected["entries"], key=key)


def test_the_grid_writes_a_row_a_line_and_a_missing_cell_as_null() -> None:
    table = {
        "factors": ["band", "region"],
        "output_column": "f",
        "entries": [
            {"band": "a", "region": "north", "value": 1.0},
            {"band": "a", "region": "south", "value": 0},
            {"band": "b", "region": "north", "value": 2.0},
        ],
    }
    text = node_config_json(NodeType.RATING_STEP, {"tables": [table]}, node_label="rate")
    assert '"columns": ["north", "south"]' in text
    assert '["a", 1.0, 0]' in text
    assert '["b", 2.0, null]' in text
    grid = grid_from_entries(table)
    assert entries_from_grid(grid, 0)["entries"] == table["entries"]


def test_changing_one_rate_changes_one_line() -> None:
    entries = [
        {"band": band, "region": region, "value": 1.0}
        for band in ("a", "b", "c", "d")
        for region in ("n", "s", "e", "w")
    ]
    table = {"factors": ["band", "region"], "output_column": "f", "entries": entries}
    before = node_config_json(NodeType.RATING_STEP, {"tables": [table]}, node_label="r")
    entries[5] = {**entries[5], "value": 0.25}
    after = node_config_json(NodeType.RATING_STEP, {"tables": [table]}, node_label="r")
    changed = [b for b, a in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
    assert len(changed) == 1


def test_a_missing_cell_misses_and_a_zero_is_a_rate() -> None:
    grid = {
        "factors": ["band", "region"],
        "output_column": "f",
        "default_value": 9.0,
        "columns": ["north", "south"],
        "rows": [["a", 0, None]],
    }
    table = entries_from_grid(grid, 0)
    frame = pl.DataFrame({"band": ["a", "a"], "region": ["north", "south"]}).lazy()
    assert _apply_rating_table(frame, table).collect()["f"].to_list() == [0.0, 9.0]


def test_a_sidecar_in_the_row_array_form_is_refused_naming_the_conversion(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "config" / "rating_step" / "rate.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(
        b'{"tables": [{"factors": ["k"], "output_column": "f", '
        b'"entries": [{"k": "a", "value": 1}]}]}'
    )
    with pytest.raises(ConfigSettingError, match=GRID_CONVERSION):
        load_node_config("config/rating_step/rate.json", base_dir=tmp_path)


def test_a_row_of_the_wrong_width_is_refused() -> None:
    grid = {"factors": ["a", "b"], "columns": ["x", "y"], "rows": [["p", 1]]}
    with pytest.raises(ConfigSettingError, match="1 level"):
        entries_from_grid(grid, 0)


@pytest.mark.parametrize(
    ("grid", "message"),
    [
        ({"factors": ["a"], "columns": ["x"]}, "has columns but no rows"),
        ({"factors": ["a"], "rows": "p"}, "rows must be a list"),
        ({"factors": ["a"], "columns": ["x"], "rows": []}, "one factor"),
        ({"factors": ["a"], "rows": [["p"]]}, "a level and its rate"),
        ({"factors": ["a", "b"], "rows": [["p", 1]]}, "columns must list the levels"),
    ],
    ids=["columns-no-rows", "rows-not-a-list", "one-factor-columns", "short-row", "no-columns"],
)
def test_a_grid_of_the_wrong_shape_is_refused(grid: dict, message: str) -> None:
    with pytest.raises(ConfigSettingError, match=message):
        entries_from_grid(grid, 0)


def test_a_one_factor_grid_reads_a_null_rate_as_no_entry() -> None:
    grid = {"factors": ["a"], "rows": [["p", None], ["q", 2]]}
    assert entries_from_grid(grid, 0)["entries"] == [{"a": "q", "value": 2}]


def test_a_config_with_no_tables_list_is_written_as_it_is() -> None:
    config = {"code": ""}
    assert compact_rating_config_for_sidecar(config) is config


def test_the_save_leaves_a_level_its_type_cannot_hold_to_the_run() -> None:
    """The save refuses levels equal under factor_dtypes; a level the type cannot convert is
    not a duplicate, so the save passes it and the run reports it."""
    table = {
        "factors": ["age"],
        "factor_dtypes": {"age": {"kind": "Int64"}},
        "output_column": "f",
        "entries": [{"age": "not a number", "value": 1.0}, {"age": "25", "value": 2.0}],
    }
    refuse_levels_equal_under_factor_dtypes({"tables": [table]})
