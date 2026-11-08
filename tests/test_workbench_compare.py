"""The comparison of two answers of the Workbench Output (specs/workbench), which
``haute app impact`` and the test quotes share: the fields compared, a value's move, rows
matched by each release's index whatever its name, and why two answers no longer compare."""

from __future__ import annotations

from typing import Any

import pytest

from haute._workbench_compare import (
    Move,
    SharedFields,
    SharedTable,
    compare_quote,
    form_fields,
    moved,
    shared_fields,
)


def _table(
    name: str,
    columns: list[str],
    *,
    rows: str = "one",
    index: str | None = None,
    kind: str = "typed",
    type_: str = "str",
) -> dict[str, Any]:
    """A table of outputs alone as a form document holds it: a many-row one's index first
    among its outputs, *index* by name, getting its values as *kind* says."""
    outputs = [{"name": column, "type": "float"} for column in columns]
    if index is not None:
        outputs = [{"name": index, "type": type_, "index": kind}, *outputs]
    return {"name": name, "rows": rows, "outputs": outputs}


def _form(*tables: dict[str, Any]) -> dict[str, Any]:
    policy = {
        "name": "policy_details",
        "rows": "one",
        "inputs": [{"name": "exposure", "type": "float"}],
    }
    return {"version": 1, "name": "x", "schema": {"tables": [policy, *tables]}}


def _compare(fields: SharedFields, before: Any, after: Any, **options: Any) -> Any:
    return compare_quote(fields, before, after, sides=("on production", "on staging"), **options)


def test_shared_fields_are_those_both_releases_name_with_the_same_rows() -> None:
    production = _form(
        _table("pricing_output", ["premium", "commission"]),
        _table("schedule", ["rate"], rows="many", index="item"),
        _table("legacy", ["x"]),
        _table("bands", ["price"], rows="many", index="band"),
        _table("layers", ["cost"], rows="many", index="layer", kind="numbered", type_="int"),
        _table("one_to_many", ["v"]),
        _table("many_to_one", ["v"], rows="many", index="line"),
    )
    candidate = _form(
        _table("pricing_output", ["premium", "fee"]),
        _table("schedule", ["rate", "excess"], rows="many", index="item"),
        # Renamed: its rows are matched by the index whatever its name in each release.
        _table("bands", ["price"], rows="many", index="band_name"),
        # The index now typed: rows keyed otherwise in each release cannot be matched.
        _table("layers", ["cost"], rows="many", index="layer", type_="int"),
        _table("one_to_many", ["v"], rows="many", index="line"),
        _table("many_to_one", ["v"]),
        _table("extras", ["y"]),
    )

    fields = shared_fields(production, candidate)

    assert fields.tables == (
        SharedTable("pricing_output", False, ("premium",)),
        SharedTable("schedule", True, ("item", "rate"), index="item", was_index="item"),
        SharedTable("bands", True, ("band_name", "price"), index="band_name", was_index="band"),
        SharedTable(
            "layers",
            True,
            ("layer", "cost"),
            index="layer",
            was_index="layer",
            unmatched="layers's index is Integer, numbered in one release and Integer, typed in "
            "the other, so its rows cannot be matched",
        ),
    )
    # A renamed index is neither added nor removed: it is paired.
    assert fields.added == ("extras", "pricing_output.fee", "schedule.excess")
    assert fields.removed == ("legacy", "pricing_output.commission")
    assert fields.reshaped == ("one_to_many", "many_to_one")


def test_a_reshaped_table_is_listed_once_and_never_compared() -> None:
    fields = shared_fields(
        _form(_table("covers", ["price"], rows="many", index="cover")),
        _form(_table("covers", ["price"])),
    )
    # A many-row answer holding one row is still not compared against a one-row table.
    compared = _compare(fields, {"covers": [{"price": 1.0}]}, {"covers": [{"price": 2.0}]})

    assert compared.moves == () and compared.why is None
    assert fields.reshaped == ("covers",)


def test_a_renamed_indexs_former_name_is_an_added_output_not_a_shared_index() -> None:
    before = _form(_table("items", ["premium"], rows="many", index="item_number"))
    after = _form(_table("items", ["item_number", "premium"], rows="many", index="line_number"))
    fields = shared_fields(before, after)
    assert fields.added == ("items.item_number",)
    assert fields.removed == ()
    assert fields.tables[0].columns == ("line_number", "premium")
    reverse = shared_fields(after, before)
    assert reverse.added == ()
    assert reverse.removed == ("items.item_number",)
    compared = _compare(
        fields,
        {"items": [{"item_number": "A", "premium": 100}]},
        {"items": [{"line_number": "A", "item_number": 7, "premium": 150}]},
    )
    assert compared.moves == (Move("items", "line_number=A", "premium", 100, 150),)


ONE = SharedFields((SharedTable("pricing_output", False, ("premium", "decline")),))
KEYED = SharedFields(
    (SharedTable("schedule", True, ("item", "rate"), index="item", was_index="item"),)
)


def test_a_one_row_table_compares_column_by_column() -> None:
    compared = _compare(
        ONE,
        {"pricing_output": [{"premium": 100.0, "decline": False}]},
        {"pricing_output": [{"premium": 110.0, "decline": False}], "other": [{"x": 1}]},
    )

    assert compared.moves == (Move("pricing_output", None, "premium", 100.0, 110.0),)


def test_a_one_row_table_one_side_leaves_out_is_empty() -> None:
    compared = _compare(ONE, {}, {"pricing_output": [{"premium": 1.0}]})

    assert compared.moves == (Move("pricing_output", None, "premium", None, 1.0),)


def test_a_many_row_table_matches_rows_by_its_index_and_names_rows_added_and_removed() -> None:
    compared = _compare(
        KEYED,
        {"schedule": [{"item": "a", "rate": 1.0}, {"item": "b", "rate": 2.0}]},
        {"schedule": [{"item": "c", "rate": 3.0}, {"item": "a", "rate": 1.5}]},
    )

    assert compared.moves == (
        Move("schedule", "item=a", "rate", 1.0, 1.5),
        Move("schedule", "item=b", None, True, False),
        Move("schedule", "item=c", None, False, True),
    )


def test_a_renamed_index_matches_each_releases_rows_by_its_value_in_any_order() -> None:
    renamed = SharedFields(
        (SharedTable("bands", True, ("band_name", "price"), index="band_name", was_index="band"),)
    )

    compared = _compare(
        renamed,
        {"bands": [{"band": "A", "price": 100.0}, {"band": "B", "price": 200.0}]},
        {"bands": [{"band_name": "B", "price": 200.0}, {"band_name": "A", "price": 150.0}]},
    )

    # The rows are told apart by their index, so a reordered answer moves only what changed.
    assert compared.moves == (Move("bands", "band_name=A", "price", 100.0, 150.0),)


def test_a_numbered_index_matches_rows_by_their_places_so_swapped_values_move() -> None:
    numbered = SharedFields(
        (SharedTable("layers", True, ("row", "cost"), index="row", was_index="row", numbered=True),)
    )

    compared = _compare(
        numbered,
        {"layers": [{"row": 1, "cost": 10.0}, {"row": 2, "cost": 20.0}]},
        {"layers": [{"row": 1, "cost": 20.0}, {"row": 2, "cost": 10.0}]},
    )

    assert compared.moves == (
        Move("layers", "row=1", "cost", 10.0, 20.0),
        Move("layers", "row=2", "cost", 20.0, 10.0),
    )


def test_indexes_getting_their_values_otherwise_no_longer_compare() -> None:
    why = "layers's index is Integer, numbered in one release and Integer, typed in the other"
    unmatched = SharedFields(
        (
            SharedTable(
                "layers", True, ("layer", "cost"), index="layer", was_index="layer", unmatched=why
            ),
        )
    )

    compared = _compare(unmatched, {"layers": [{"layer": 1}]}, {"layers": [{"layer": 1}]})

    assert (compared.moves, compared.why) == ((), why)


def test_a_numeric_key_matches_whether_it_is_written_whole_or_decimal() -> None:
    compared = _compare(
        KEYED,
        {"schedule": [{"item": 1, "rate": 100.0}, {"item": True, "rate": 1.0}]},
        {"schedule": [{"item": 1.0, "rate": 125.0}, {"item": 2, "rate": 1.0}]},
    )

    assert compared.moves == (
        Move("schedule", "item=1", "rate", 100.0, 125.0),
        # True is no number: its row is not the row keyed 1.
        Move("schedule", "item=True", None, True, False),
        Move("schedule", "item=2", None, False, True),
    )


@pytest.mark.parametrize(
    ("before", "after", "why"),
    [
        (
            [{"item": "a", "rate": 1.0}, {"item": "a", "rate": 2.0}],
            [{"item": "a", "rate": 1.0}],
            "schedule on production holds the key item=a twice",
        ),
        (
            [{"item": "a", "rate": 1.0}],
            [{"rate": 1.0}],
            "a row of schedule on staging leaves its key item empty",
        ),
    ],
    ids=["key-twice", "key-empty"],
)
def test_rows_that_cannot_be_matched_no_longer_compare(
    before: list[Any], after: list[Any], why: str
) -> None:
    compared = _compare(KEYED, {"schedule": before}, {"schedule": after})

    assert (compared.moves, compared.why) == ((), why)


def test_rows_of_different_counts_are_rows_added_or_removed() -> None:
    rows = [{"item": "a", "rate": 1.0}, {"item": "b", "rate": 2.0}]

    compared = _compare(KEYED, {"schedule": rows}, {"schedule": rows[:1]})

    assert compared.moves == (Move("schedule", "item=b", None, True, False),)


@pytest.mark.parametrize(
    ("before", "after", "has_moved"),
    [
        (100.0, 100.0 + 100.0 * 1e-9, False),
        (100.0, 100.0 + 100.0 * 1e-7, True),
        (100, 100.0, False),
        (0, 0.0, False),
        (0, 1e-12, True),
        (True, 1, True),
        (False, 0, True),
        (None, 0, True),
        (None, None, False),
        ("NY", "NY", False),
        ("NY", "CA", True),
        (-50.0, -50.0, False),
    ],
)
def test_a_value_moves_by_the_rule(before: Any, after: Any, has_moved: bool) -> None:
    assert moved(before, after) is has_moved


def test_one_forms_fields_are_its_output_tables_with_their_indexes() -> None:
    fields = form_fields(
        _form(
            _table("pricing_output", ["premium"]),
            _table("schedule", ["rate"], rows="many", index="item"),
            _table("layers", ["cost"], rows="many", index="row", kind="numbered", type_="int"),
        )
    )

    # A one-row table is matched by nothing: its one row is its row.
    assert fields.tables == (
        SharedTable("pricing_output", False, ("premium",)),
        SharedTable("schedule", True, ("item", "rate"), index="item", was_index="item"),
        SharedTable("layers", True, ("row", "cost"), index="row", was_index="row", numbered=True),
    )
    assert (fields.added, fields.removed, fields.reshaped) == ((), (), ())


def test_an_input_tables_fields_are_its_answers_keyed_by_its_index() -> None:
    identity = [{"name": "quote_reference", "type": "str"}, {"name": "quote_option", "type": "int"}]
    fields = form_fields(
        _form(
            # Keyed by its numbered index, as written with the identity at its first block's
            # head.
            {
                "name": "equipment",
                "rows": "many",
                "inputs": [
                    *identity,
                    {"name": "item_number", "type": "int", "index": "numbered"},
                    {"name": "value", "type": "float"},
                ],
                "outputs": [{"name": "premium", "type": "float"}],
            },
            # Keyed by its index of values, as a form another Haute wrote may hold it,
            # without the identity.
            {
                "name": "perils",
                "rows": "many",
                "inputs": [
                    {
                        "name": "peril",
                        "type": "str",
                        "options": ["Fire", "Flood"],
                        "index": "values",
                    },
                    {"name": "limit", "type": "float"},
                ],
                "outputs": [{"name": "peril_premium", "type": "float"}],
            },
            # A one-row table of both blocks: its one row is its row.
            {
                "name": "pricing",
                "rows": "one",
                "inputs": [{"name": "discount", "type": "float"}],
                "outputs": [{"name": "charged", "type": "float"}],
            },
            # Inputs alone: nothing answered.
            {"name": "drivers", "rows": "one", "inputs": [{"name": "age", "type": "int"}]},
        )
    )

    # Each answers the identity it holds, its index and its outputs, matched by its index.
    assert fields.tables == (
        SharedTable(
            "equipment",
            True,
            ("quote_reference", "quote_option", "item_number", "premium"),
            index="item_number",
            was_index="item_number",
        ),
        SharedTable("perils", True, ("peril", "peril_premium"), index="peril", was_index="peril"),
        SharedTable("pricing", False, ("charged",)),
    )


@pytest.mark.parametrize(
    ("after", "tolerance", "has_moved"),
    [
        (100.5, 0.01, False),
        (101.5, 0.01, True),
        (100.0000001, None, False),
        (100.001, None, True),
        (100.0, 0.0, False),
    ],
)
def test_a_tolerance_widens_how_far_a_number_may_move(
    after: float, tolerance: float | None, has_moved: bool
) -> None:
    assert moved(100.0, after, tolerance) is has_moved
    compared = _compare(
        SharedFields((SharedTable("pricing_output", False, ("premium",)),)),
        {"pricing_output": [{"premium": 100.0}]},
        {"pricing_output": [{"premium": after}]},
        tolerance=tolerance,
    )
    assert bool(compared.moves) is has_moved


def test_a_reason_names_each_side_as_it_is_given() -> None:
    compared = compare_quote(
        SharedFields((SharedTable("schedule", True, ("item",), index="item", was_index="item"),)),
        {"schedule": [{"item": "a"}, {"item": "a"}]},
        {"schedule": []},
        sides=("in expected", "in the new answer"),
    )

    assert compared.why == "schedule in expected holds the key item=a twice"


@pytest.mark.parametrize("which", ["before", "after"])
def test_a_one_row_table_answering_several_rows_no_longer_compares(which: str) -> None:
    one = SharedFields((SharedTable("pricing_output", False, ("premium",)),))
    two = {"pricing_output": [{"premium": 100.0}, {"premium": 200.0}]}
    single = {"pricing_output": [{"premium": 100.0}]}

    compared = _compare(
        one, two if which == "before" else single, two if which == "after" else single
    )

    side = "on production" if which == "before" else "on staging"
    assert compared.why == f"pricing_output has 2 rows {side} though it has one row per quote"
    assert compared.moves == ()
