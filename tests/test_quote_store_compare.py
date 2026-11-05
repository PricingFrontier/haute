"""The schema pane's Compare (specs/workbench): a database's own catalogue read with the quotes
holding values in each table and column, the form compared with it by the form's names, renames
from the analyst's pairing alone, what the database holds beyond the form listed, what no
statement changes noted, and the SQL written from the tables as they are, which, run on that
database, leaves the app's check finding nothing, on every engine the store runs on.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import mssql

import haute._quote_store._catalogue as catalogue_module
from haute._quote_store import QuoteStoreEnvironmentError, StoreLocation, open_book, render_sql
from haute._quote_store._catalogue import IDENTITY, Catalogue, counting_statement, read_catalogue
from haute._quote_store._compare import Comparison, Rename, compare, read_store_catalogue
from haute._quote_store._migrate import PROVIDER_DIALECTS
from haute._quote_store._providers import PROVIDERS
from haute._quote_store._store import make_engine
from haute._workbench_form import FormSpec, with_quote_details
from tests._store_servers import Target, backends, configured, database

ENVIRONMENT = "dev"


def form(*tables: dict[str, Any]) -> FormSpec:
    return with_quote_details(
        FormSpec.model_validate(
            {
                "name": "motor",
                "schema": {"tables": list(tables)},
                "pages": [
                    {
                        "id": "p",
                        "title": "Sheet 1",
                        "width": 1280,
                        "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
                    }
                ],
            }
        )
    )


def column(name: str, type_: str = "str") -> dict[str, Any]:
    return {"name": name, "type": type_}


def table(name: str, *columns: dict[str, Any], rows: str = "one") -> dict[str, Any]:
    """A schema table of inputs."""
    return {"name": name, "rows": rows, "inputs": list(columns)}


RELEASED = form(table("policy", column("region"), column("drivers"), column("value", "float")))


@pytest.fixture(params=backends())
def target(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    with database(request.param, tmp_path) as made:
        yield made


@pytest.fixture(
    params=[pytest.param(name, marks=pytest.mark.store_servers) for name in configured()]
)
def server(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    """A database of the test's own on each server the run has."""
    with database(request.param, tmp_path) as made:
        yield made


def made_from_sql(target: Target, release: FormSpec) -> None:
    """The store's tables made from the SQL Haute writes from none, as their owners make them."""
    target.script(target.sql(release))


def quote(target: Target, release: FormSpec, reference: str, **policy: str) -> None:
    with open_book(
        target.url, release, environment=ENVIRONMENT, haute_version="1", sign_in=target.sign_in
    ) as book:
        book.create(
            operation=f"operation-{reference}",
            actor="ana",
            values={"policy": [dict(policy)]},
            at=book.now(),
            reference=reference,
        )


def catalogue(target: Target) -> Catalogue:
    engine = make_engine(target.url, sign_in=target.sign_in)
    try:
        with engine.connect() as connection:
            return read_catalogue(connection, count=True)
    finally:
        engine.dispose()


def compared(target: Target, release: FormSpec, **given: Any) -> Comparison:
    return compare(release, catalogue(target), provider=PROVIDERS[target.backend], **given)


def sql(target: Target, release: FormSpec, comparison: Comparison) -> str:
    return render_sql(comparison.plan, release, PROVIDER_DIALECTS[target.backend])


def kinds(comparison: Comparison) -> set[tuple[str | None, str | None, str]]:
    return {(each.table, each.column, each.kind) for each in comparison.differences}


def opens(target: Target, release: FormSpec) -> None:
    """The app's check finds nothing: the book opens."""
    open_book(
        target.url, release, environment=ENVIRONMENT, haute_version="1", sign_in=target.sign_in
    ).close()


def test_a_database_made_from_the_form_s_sql_matches_it(target: Target) -> None:
    made_from_sql(target, RELEASED)

    comparison = compared(target, RELEASED)

    assert comparison.matches
    assert comparison.extras == ()
    assert "nothing to change" in sql(target, RELEASED, comparison)


def _read_counting(target: Target) -> tuple[Catalogue, int]:
    """*target*'s catalogue, its quotes counted, and the statements reading it sent."""
    engine = make_engine(target.url, sign_in=target.sign_in)
    sent: list[str] = []
    try:
        with engine.connect() as connection:
            event.listen(
                connection, "before_cursor_execute", lambda *executing: sent.append(executing[2])
            )
            return read_catalogue(connection, count=True), len(sent)
    finally:
        engine.dispose()


def _statements_reading(target: Target) -> int:
    return _read_counting(target)[1]


def test_a_servers_tables_are_read_in_as_many_statements_however_many_it_holds(
    server: Target,
) -> None:
    # Each statement is a round trip to the server, and the Compare waits on every one: read
    # table by table, a store of fifteen tables took seconds on a database across a network.
    made_from_sql(server, RELEASED)
    few = _statements_reading(server)

    # Each holds the quote's identity, so its quotes are counted too.
    server.run(
        *(
            f"CREATE TABLE extra_{index} (quote_reference VARCHAR(80), quote_option BIGINT, v INT)"
            for index in range(5)
        )
    )

    assert _statements_reading(server) == few


def test_quotes_counted_a_few_tables_to_a_statement_are_those_counted_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A statement reads at most COUNTED_AT_ONCE values, under the engines' limit on a select's
    # columns: a form wider than that is counted in as many statements as it needs.
    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "A-1", region="north", drivers="2")
    quote(target, RELEASED, "A-2", region="south")
    at_once, statements = _read_counting(target)

    monkeypatch.setattr(catalogue_module, "COUNTED_AT_ONCE", 2)
    batched, more = _read_counting(target)

    assert batched == at_once
    assert more > statements
    policy = at_once.table("policy")
    assert policy is not None and policy.quotes == 2


def test_a_store_of_more_tables_than_a_join_takes_is_counted(tmp_path: Path) -> None:
    # Each table counted is one of its statement's join, and SQLite joins at most 64 tables.
    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    target.script(
        "".join(
            f"CREATE TABLE t{index} (quote_reference TEXT, quote_option INTEGER, v INTEGER);"
            for index in range(70)
        )
        + "INSERT INTO t69 VALUES ('A-1', 1, NULL), ('A-1', 2, 5), ('A-2', 1, 7);"
    )

    counted = {table.name: table for table in catalogue(target).tables}

    assert len(counted) == 70
    assert counted["t0"].quotes == 0
    last = counted["t69"]
    assert last.quotes == 3
    assert {column.name: column.quotes for column in last.columns}["v"] == 2


def test_tables_are_batched_within_each_limit_a_statement_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalogue_module, "COUNTED_AT_ONCE", 10)
    monkeypatch.setattr(catalogue_module, "TABLES_AT_ONCE", 3)
    widths = [2, 2, 2, 2, 6, 4, 15, 1, 1, 1, 1, 1]
    tables = [
        (f"t{index}", [*IDENTITY, *(f"c{at}" for at in range(width))])
        for index, width in enumerate(widths)
    ]

    batches = catalogue_module._batches(tables)

    assert [table for batch in batches for table in batch] == tables
    for batch in batches:
        assert len(batch) <= 3
        values = sum(1 + len(columns) - len(IDENTITY) for _, columns in batch)
        # A table wider than a statement's values is counted alone.
        assert values <= 10 or len(batch) == 1


def test_a_wide_forms_quotes_are_counted_binding_no_value() -> None:
    # SQL Server binds at most 2,100 values to a statement, and one statement counts many
    # tables' columns: the numbers it counts with are written into its SQL.
    tables = [(f"t{index}", [*IDENTITY, *(f"c{at}" for at in range(30))]) for index in range(40)]

    compiled = counting_statement(tables, "pricing").compile(dialect=mssql.dialect())

    assert compiled.params == {}


def test_the_quotes_holding_values_are_counted_by_table_and_column(target: Target) -> None:
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "A-1", region="north", drivers="2")
    quote(target, RELEASED, "A-2", region="south")

    policy = catalogue(target).table("policy")

    assert policy is not None
    assert policy.quotes == 2
    counted = {each.name: each.quotes for each in policy.columns}
    assert (counted["region"], counted["drivers"], counted["value"]) == (2, 1, 0)
    assert counted["quote_reference"] == 2


def test_the_sql_from_the_tables_as_they_are_brings_them_to_the_form_keeping_values(
    target: Target,
) -> None:
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "A-1", region="north", drivers="2", value="1.5")
    # The form renames region area, makes drivers an Integer, adds seats and drops value; the
    # database also holds a table the form never had. Unpaired, the renamed column is one the
    # database lacks beside one it keeps with its values (READ-23).
    changed = form(
        table("policy", column("area"), column("drivers", "int"), column("seats", "int"))
    )
    target.run(
        "CREATE TABLE old_policy (quote_reference VARCHAR(40), quote_option INTEGER, x INTEGER)"
    )

    comparison = compared(target, changed)

    assert {
        ("policy", "area", "missing"),
        ("policy", "drivers", "type"),
        ("policy", "seats", "missing"),
    } <= kinds(comparison)
    assert not any(each.kind == "renamed" for each in comparison.differences)
    typed = next(each for each in comparison.differences if each.kind == "type")
    assert "1 quote holds values in it" in typed.message
    extras = {(each.table, each.column, each.quotes) for each in comparison.extras}
    assert ("policy", "region", 1) in extras
    assert ("policy", "value", 1) in extras
    assert ("old_policy", None, 0) in extras
    written = sql(target, changed, comparison)
    assert "The form does not have policy.region (1 quote holds values in it)" in written
    assert "The form does not have policy.value (1 quote holds values in it)" in written
    if target.backend == "sqlite":
        # SQLite changes no column's type: its note says how, and the check still finds it.
        return
    target.script(written)
    opens(target, changed)
    assert target.read("SELECT region, area, drivers FROM policy") == [("north", None, 2)]


def test_a_column_the_analyst_pairs_is_renamed_keeping_its_values(target: Target) -> None:
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "A-1", region="north")
    paired = form(table("policy", column("zone"), column("drivers"), column("value", "float")))

    unpaired = compared(target, paired)
    assert ("policy", "zone", "missing") in kinds(unpaired)
    assert ("policy", "region", "policy", 1) in {
        (each.table, each.column, each.form_table, each.quotes) for each in unpaired.extras
    }

    comparison = compared(target, paired, renames=[Rename("policy", "zone", "region")])
    assert comparison.renames == (Rename("policy", "zone", "region"),)
    assert ("policy", "zone", "renamed") in kinds(comparison)
    target.script(sql(target, paired, comparison))
    opens(target, paired)
    assert target.read("SELECT zone FROM policy") == [("north",)]


def test_a_pairing_the_database_no_longer_allows_is_not_taken(target: Target) -> None:
    made_from_sql(target, RELEASED)
    paired = form(table("policy", column("zone"), column("drivers")))

    comparison = compared(target, paired, renames=[Rename("policy", "zone", "no_such")])

    assert comparison.renames == ()
    assert ("policy", "zone", "missing") in kinds(comparison)


def test_a_database_holding_no_store_takes_every_table_and_the_activity(target: Target) -> None:
    comparison = compared(target, RELEASED)

    assert (None, None, "missing") in kinds(comparison)
    assert ("policy", None, "missing") in kinds(comparison)
    target.script(sql(target, RELEASED, comparison))
    opens(target, RELEASED)


def test_what_no_statement_changes_is_marked_and_noted(tmp_path: Path) -> None:
    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    made_from_sql(target, RELEASED)
    target.run(
        "ALTER TABLE policy ADD COLUMN \"audit\" TEXT NOT NULL DEFAULT 'x'",
    )

    comparison = compared(target, RELEASED)

    assert not comparison.matches
    assert ("policy", None, "null") in kinds(comparison)
    written = sql(target, RELEASED, comparison)
    assert (
        "-- Not changed here: policy.audit is no column of the form's and takes no null" in written
    )


def test_a_database_whose_tables_cannot_be_read_is_said_naming_the_connection(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"this is no database " * 100)
    location = StoreLocation(
        ENVIRONMENT, f"sqlite:///{broken.as_posix()}", named_by="connections.toml [dev]"
    )

    with pytest.raises(QuoteStoreEnvironmentError) as refused:
        read_store_catalogue(location)

    assert str(refused.value) == (
        "The dev database's tables could not be read: check connections.toml [dev], and that "
        "the database lets its sign-in read them."
    )


def test_the_catalogue_holds_no_value_read(tmp_path: Path) -> None:
    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "SECRET-REF", region="a-secret-region")

    assert "a-secret-region" not in repr(catalogue(target))
    assert "SECRET-REF" not in repr(catalogue(target))
    raw = sqlite3.connect(tmp_path / "store.sqlite")
    try:
        assert raw.execute("SELECT region FROM policy").fetchall() == [("a-secret-region",)]
    finally:
        raw.close()


def test_a_sqlite_database_is_read_without_changing_it_or_making_one(tmp_path: Path) -> None:
    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    made_from_sql(target, RELEASED)
    raw = sqlite3.connect(tmp_path / "store.sqlite")
    try:
        raw.execute("PRAGMA journal_mode=WAL").fetchone()
    finally:
        raw.close()

    read_store_catalogue(StoreLocation(ENVIRONMENT, target.url, named_by="connections.toml [dev]"))

    raw = sqlite3.connect(tmp_path / "store.sqlite")
    try:
        # The store's own engine would have put it back to a rollback journal.
        assert raw.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        raw.close()
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(QuoteStoreEnvironmentError, match="could not be read"):
        read_store_catalogue(
            StoreLocation(
                ENVIRONMENT, f"sqlite:///{missing.as_posix()}", named_by="connections.toml [dev]"
            )
        )
    assert not missing.exists()


def test_an_entra_sign_in_refused_is_a_database_not_reached_naming_no_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from azure.core.exceptions import ClientAuthenticationError

    from haute._quote_store import SignIn, _providers

    class Refused:
        def get_token(self, *scopes: str) -> Any:
            raise ClientAuthenticationError("AADSTS50126: hidden-user@hidden-tenant cancelled")

    monkeypatch.setattr(_providers, "_analyst_credential", lambda: Refused())
    location = StoreLocation(
        ENVIRONMENT,
        "postgresql+psycopg://hidden-host.invalid/quotes",
        sign_in=SignIn("entra", username="pricing"),
        named_by="connections.toml [dev]",
    )

    with pytest.raises(QuoteStoreEnvironmentError) as refused:
        read_store_catalogue(location)

    said = str(refused.value)
    assert said.startswith(
        "The dev quote store's database could not be reached, or it refused the sign-in: check "
        "connections.toml [dev]."
    )
    assert "hidden" not in said


def test_a_store_table_lacking_an_identity_column_is_compared_not_made_again(
    tmp_path: Path,
) -> None:
    from haute._quote_store._compare import Extra

    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    made_from_sql(target, RELEASED)
    target.run(
        "DROP TABLE policy",
        "CREATE TABLE policy (quote_reference VARCHAR(80), region TEXT, drivers TEXT, value TEXT, "
        "legacy TEXT)",
        "INSERT INTO policy VALUES ('A-1', 'north', '2', '1.5', 'kept')",
        "CREATE TABLE unrelated (quote_reference VARCHAR(80), x INTEGER)",
    )

    read = catalogue(target).table("policy")
    assert read is not None and read.quotes is None
    comparison = compared(target, RELEASED)

    assert ("policy", None, "missing") not in kinds(comparison)
    assert ("policy", "quote_option", "missing") in kinds(comparison)
    assert comparison.extras == (Extra("policy", "legacy", "policy", None),)
    typed = next(each for each in comparison.differences if each.kind == "type")
    assert "quotes holding values not counted: table lacks quote identity" in typed.message
    written = sql(target, RELEASED, comparison)
    assert "CREATE TABLE policy" not in written
    assert (
        "The form does not have policy.legacy (quotes holding values not counted: table lacks "
        "quote identity)" in written
    )
