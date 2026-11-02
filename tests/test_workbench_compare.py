"""The comparison of two answers of the Workbench Output (specs/workbench), which
``haute app impact`` and the test quotes share: the fields compared, a value's move, rows
matched by key or by position, and why two answers no longer compare."""

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
    name: str, columns: list[str], *, rows: str = "one", keys: tuple[str, ...] = ()
) -> dict[str, Any]:
    """An output table as a form document holds it."""
    return {
        "id": f"t_{name}",
        "name": name,
        "role": "output",
        "rows": rows,
        "columns": [
            {"id": f"c_{column}", "name": column, "type": "float", "key": column in keys}
            for column in columns
        ],
    }


def _form(*tables: dict[str, Any]) -> dict[str, Any]:
    policy = {
        "id": "t1",
        "name": "policy_details",
        "role": "input",
        "rows": "one",
        "columns": [{"id": "c_exposure", "name": "exposure", "type": "float"}],
    }
    return {"version": 1, "name": "x", "schema": {"tables": [policy, *tables]}}


def _compare(fields: SharedFields, before: Any, after: Any, **options: Any) -> Any:
    return compare_quote(fields, before, after, sides=("on production", "on staging"), **options)


def test_shared_fields_are_those_both_releases_name_with_the_same_rows() -> None:
    production = _form(
        _table("pricing_output", ["premium", "commission"]),
        _table("schedule", ["item", "rate"], rows="many", keys=("item",)),
        _table("legacy", ["x"]),
        _table("covers", ["cover", "price"], rows="many", keys=("cover",)),
        _table("one_to_many", ["v"]),
        _table("many_to_one", ["v"], rows="many"),
    )
    candidate = _form(
        _table("pricing_output", ["premium", "fee"]),
        _table("schedule", ["item", "rate", "excess"], rows="many", keys=("item", "rate")),
        _table("covers", ["cover", "price"], rows="many"),
        _table("one_to_many", ["v"], rows="many"),
        _table("many_to_one", ["v"]),
        _table("extras", ["y"]),
    )

    fields = shared_fields(production, candidate)

    assert fields.tables == (
        SharedTable("pricing_output", False, ("premium",), ()),
        # A key marked on one side alone is no key.
        SharedTable("schedule", True, ("item", "rate"), ("item",)),
        SharedTable("covers", True, ("cover", "price"), ()),
    )
    assert fields.added == ("extras", "pricing_output.fee", "schedule.excess")
    assert fields.removed == ("legacy", "pricing_output.commission")
    assert fields.reshaped == ("one_to_many", "many_to_one")


def test_a_reshaped_table_is_listed_once_and_never_compared() -> None:
    fields = shared_fields(
        _form(_table("covers", ["price"], rows="many")), _form(_table("covers", ["price"]))
    )
    # A many-row answer holding one row is still not compared against a one-row table.
    compared = _compare(fields, {"covers": [{"price": 1.0}]}, {"covers": [{"price": 2.0}]})

    assert compared.moves == () and compared.why is None
    assert fields.reshaped == ("covers",)


ONE = SharedFields((SharedTable("pricing_output", False, ("premium", "decline"), ()),))
KEYED = SharedFields((SharedTable("schedule", True, ("item", "rate"), ("item",)),))
PLACED = SharedFields((SharedTable("schedule", True, ("item", "rate"), ()),))


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


def test_a_many_row_table_matches_rows_by_key_and_names_rows_added_and_removed() -> None:
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


def test_rows_without_a_shared_key_compare_by_position_when_counts_match() -> None:
    rows = [{"item": "a", "rate": 1.0}, {"item": "b", "rate": 2.0}]

    compared = _compare(
        PLACED, {"schedule": rows}, {"schedule": [rows[0], {"item": "b", "rate": 2.5}]}
    )
    unequal = _compare(PLACED, {"schedule": rows}, {"schedule": rows[:1]})

    assert compared.moves == (Move("schedule", "row 2", "rate", 2.0, 2.5),)
    assert unequal.why == (
        "schedule has 2 rows on production and 1 on staging, and no key matches them"
    )


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


def test_one_forms_fields_are_its_output_tables_with_their_keys() -> None:
    fields = form_fields(
        _form(
            _table("pricing_output", ["premium"], keys=("premium",)),
            _table("schedule", ["item", "rate"], rows="many", keys=("item",)),
        )
    )

    # A one-row table is matched by nothing: its one row is its row.
    assert fields.tables == (
        SharedTable("pricing_output", False, ("premium",), ()),
        SharedTable("schedule", True, ("item", "rate"), ("item",)),
    )
    assert (fields.added, fields.removed, fields.reshaped) == ((), (), ())


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
        SharedFields((SharedTable("pricing_output", False, ("premium",), ()),)),
        {"pricing_output": [{"premium": 100.0}]},
        {"pricing_output": [{"premium": after}]},
        tolerance=tolerance,
    )
    assert bool(compared.moves) is has_moved


def test_a_reason_names_each_side_as_it_is_given() -> None:
    compared = compare_quote(
        SharedFields((SharedTable("schedule", True, ("item",), ("item",)),)),
        {"schedule": [{"item": "a"}, {"item": "a"}]},
        {"schedule": []},
        sides=("in expected", "in the new answer"),
    )

    assert compared.why == "schedule in expected holds the key item=a twice"


@pytest.mark.parametrize("which", ["before", "after"])
def test_a_one_row_table_answering_several_rows_no_longer_compares(which: str) -> None:
    one = SharedFields((SharedTable("pricing_output", False, ("premium",), ()),))
    two = {"pricing_output": [{"premium": 100.0}, {"premium": 200.0}]}
    single = {"pricing_output": [{"premium": 100.0}]}

    compared = _compare(
        one, two if which == "before" else single, two if which == "after" else single
    )

    side = "on production" if which == "before" else "on staging"
    assert compared.why == f"pricing_output has 2 rows {side} though it has one row per quote"
    assert compared.moves == ()
