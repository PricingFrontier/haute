"""Saving a quote keeps its values in store columns the form no longer holds (READ-05).

Spec: specs/workbench/high-level.md, the quote store. A column leaves the form, the SQL
Haute writes keeps it, and each quote's rows carry their values in it (the ``$kept`` field)
so a save writes them back. Each test runs on SQLite and on each SQL Server or PostgreSQL
server the environment names (tests/_store_servers.py).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import PricedOutputs, QuoteBook, QuoteValuesError, open_book
from haute._quote_store._rows import KEPT_KEY
from haute._workbench_form import FormSpec
from tests._store_servers import Target, backends, database
from tests.test_quote_book import ANA, FORM, HAUTE, RELEASE, Clock, document, op, values


def _without(form: FormSpec, removed: dict[str, set[str]]) -> FormSpec:
    """*form* with the named columns of each table, by its name, removed."""
    tables = []
    for table in form.data_schema.tables:
        drop = removed.get(table.name, set())
        tables.append(
            table.model_copy(update={"columns": [c for c in table.columns if c.name not in drop]})
        )
    schema = form.data_schema.model_copy(update={"tables": tables})
    return form.model_copy(update={"data_schema": schema})


#: The form after mileage left vehicles, telematics left policy and referred left premium.
NEW = _without(FORM, {"vehicles": {"mileage"}, "policy": {"telematics"}, "premium": {"referred"}})


@pytest.fixture(params=backends())
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    with database(request.param, tmp_path) as target:
        yield target


def _open(store: Target, form: FormSpec) -> QuoteBook:
    return open_book(
        store.url,
        form,
        environment=store.environment,
        haute_version=HAUTE,
        clock=Clock(),
        sign_in=store.sign_in,
    )


def _vehicles(*mileages: str | None) -> list[dict[str, Any]]:
    return [
        {"reg": f"REG{index}", "value": "1000", **({"mileage": m} if m else {})}
        for index, m in enumerate(mileages, 1)
    ]


@pytest.fixture
def before(store: Target) -> tuple[str, int]:
    """A quote saved on the old form, its three vehicles' mileages 100, 200 and 300."""
    store.make(FORM)
    with _open(store, FORM) as book:
        quote = book.create(
            operation=op(),
            actor=ANA,
            at=book.now(),
            reference="K-1",
            values=values(
                vehicles=_vehicles("100", "200", "300"),
                policy=[{"region": "north", "drivers": "2", "telematics": True}],
            ),
        ).quote
        book.save(
            quote.reference,
            quote.option,
            operation=op(),
            version=quote.version,
            actor=ANA,
            values=values(
                vehicles=_vehicles("100", "200", "300"),
                policy=[{"region": "north", "drivers": "2", "telematics": True}],
            ),
            priced=PricedOutputs(RELEASE, document()),
            at=book.now(),
        )
    store.make(NEW, base=FORM)
    return quote.reference, quote.option


def _save(book: QuoteBook, identity: tuple[str, int], rows: Any, *, priced: bool = True) -> Any:
    quote = book.get(*identity)
    return book.save(
        *identity,
        operation=op(),
        version=quote.version,
        actor=ANA,
        values=rows,
        priced=PricedOutputs(RELEASE, document()) if priced else None,
        at=book.now(),
    )


def _mileages(store: Target) -> list[tuple[Any, ...]]:
    return store.read("SELECT reg, mileage FROM vehicles ORDER BY quote_row")


def test_an_opened_quote_carries_its_kept_values_and_a_save_keeps_them(
    store: Target, before: tuple[str, int]
) -> None:
    with _open(store, NEW) as book:
        opened = book.read(*before)
        vehicles = opened.values["vehicles"]
        assert [json.loads(row[KEPT_KEY]) for row in vehicles] == [
            {"mileage": 100},
            {"mileage": 200},
            {"mileage": 300},
        ]
        assert json.loads(opened.values["policy"][0][KEPT_KEY]) == {"telematics": True}

        _save(book, before, opened.values)

    assert _mileages(store) == [("REG1", 100), ("REG2", 200), ("REG3", 300)]
    assert store.read("SELECT telematics FROM policy") in ([(True,)], [(1,)])


@pytest.mark.parametrize("deleted", [0, 1])
def test_deleting_a_row_keeps_each_remaining_rows_own_value(
    store: Target, before: tuple[str, int], deleted: int
) -> None:
    with _open(store, NEW) as book:
        rows = book.read(*before).values
        rows["vehicles"] = [row for i, row in enumerate(rows["vehicles"]) if i != deleted]
        saved = _save(book, before, rows)
        _save(book, before, saved.values)

    expected = [("REG1", 100), ("REG2", 200), ("REG3", 300)]
    del expected[deleted]
    assert _mileages(store) == expected


def test_an_inserted_row_holds_no_kept_value_and_the_others_keep_theirs(
    store: Target, before: tuple[str, int]
) -> None:
    with _open(store, NEW) as book:
        rows = book.read(*before).values
        rows["vehicles"].insert(1, {"reg": "NEW", "value": "500"})
        saved = _save(book, before, rows)
        _save(book, before, saved.values)

    assert _mileages(store) == [("REG1", 100), ("NEW", None), ("REG2", 200), ("REG3", 300)]


def test_a_row_left_holding_only_kept_values_stays_a_row(
    store: Target, before: tuple[str, int]
) -> None:
    """Its cells on the sheet emptied, a row still holds a value the form no longer shows,
    so a save keeps it: only deleting the row deletes that value."""
    with _open(store, NEW) as book:
        rows = book.read(*before).values
        rows["vehicles"][1] = {KEPT_KEY: rows["vehicles"][1][KEPT_KEY]}
        saved = _save(book, before, rows, priced=False)
        _save(book, before, saved.values, priced=False)

    assert _mileages(store) == [("REG1", 100), (None, 200), ("REG3", 300)]


def _preset(form: FormSpec, table: str, column: str, options: list[str]) -> FormSpec:
    """*form* with *table*'s *column* giving the table a row for each of *options*."""
    tables = [
        each.model_copy(
            update={
                "columns": [
                    c.model_copy(update={"options": options, "row_per_value": True})
                    if c.name == column
                    else c
                    for c in each.columns
                ]
            }
        )
        if each.name == table
        else each
        for each in form.data_schema.tables
    ]
    return form.model_copy(
        update={"data_schema": form.data_schema.model_copy(update={"tables": tables})}
    )


def test_a_row_moved_to_its_preset_place_takes_its_kept_value_with_it(
    store: Target, before: tuple[str, int]
) -> None:
    """A column giving the table a row for each of its values puts the rows in the values'
    order; each row's kept value follows it, not the place it was opened in."""
    reordered = _preset(NEW, "vehicles", "reg", ["REG2", "REG1", "REG3"])
    with _open(store, reordered) as book:
        saved = _save(book, before, book.read(*before).values, priced=False)
        _save(book, before, saved.values, priced=False)

    assert _mileages(store) == [("REG2", 200), ("REG1", 100), ("REG3", 300)]


def test_a_replayed_save_answers_the_rows_with_their_kept_values(
    store: Target, before: tuple[str, int]
) -> None:
    with _open(store, NEW) as book:
        rows = book.read(*before).values
        quote = book.get(*before)
        operation = op()
        first = book.save(
            *before,
            operation=operation,
            version=quote.version,
            actor=ANA,
            values=rows,
            priced=None,
            at=book.now(),
        )
        replayed = book.replayed(operation, ANA, "saved", *before)

    assert replayed is not None
    assert replayed.values == first.values
    assert json.loads(replayed.values["vehicles"][2][KEPT_KEY]) == {"mileage": 300}


@pytest.mark.parametrize(
    ("carried", "named"),
    [({"value": 5.0}, "value"), ({"colour": "red"}, "colour"), ({"mileage": "far"}, "mileage")],
)
def test_the_save_refuses_a_kept_value_for_a_form_column_an_unknown_column_or_a_misfit(
    store: Target, before: tuple[str, int], carried: dict[str, Any], named: str
) -> None:
    with _open(store, NEW) as book:
        rows = book.read(*before).values
        rows["vehicles"][0][KEPT_KEY] = json.dumps(carried)
        with pytest.raises(QuoteValuesError, match=named):
            _save(book, before, rows)
    assert _mileages(store) == [("REG1", 100), ("REG2", 200), ("REG3", 300)]


@pytest.mark.parametrize("priced", [True, False])
def test_an_output_column_the_form_dropped_is_cleared_by_the_next_save(
    store: Target, before: tuple[str, int], priced: bool
) -> None:
    assert store.read("SELECT referred FROM premium") in ([(False,)], [(0,)])
    with _open(store, NEW) as book:
        _save(book, before, book.read(*before).values, priced=priced)

    held = store.read("SELECT referred FROM premium")
    assert held == ([(None,)] if priced else [])
    assert _mileages(store) == [("REG1", 100), ("REG2", 200), ("REG3", 300)]
