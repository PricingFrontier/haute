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
from haute._quote_store import (
    QuoteStoreEnvironmentError,
    QuoteStoreMismatchError,
    StoreLocation,
    open_book,
    render_sql,
)
from haute._quote_store._catalogue import IDENTITY, Catalogue, counting_statement, read_catalogue
from haute._quote_store._compare import (
    Comparison,
    Rename,
    additions_plan,
    answered_by,
    compare,
    read_store_catalogue,
)
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


def exported(target: Target, release: FormSpec, **given: Any) -> str:
    """The Workbench's Export SQL for *release* on *target*'s tables, as the schema pane's
    route writes it: the additions and the type changes alone."""
    read = catalogue(target)
    comparison = compare(release, read, provider=PROVIDERS[target.backend], **given)
    return render_sql(
        additions_plan(release, read, comparison),
        release,
        PROVIDER_DIALECTS[target.backend],
        additions_only=True,
    )


def kinds(comparison: Comparison) -> set[tuple[str | None, str | None, str]]:
    return {(each.table, each.column, each.kind) for each in comparison.differences}


def opens(target: Target, release: FormSpec) -> None:
    """The app's check finds nothing: the book opens, a quote's use refused naming what does
    not fit otherwise (:class:`QuoteStoreMismatchError`)."""
    with open_book(
        target.url, release, environment=ENVIRONMENT, haute_version="1", sign_in=target.sign_in
    ) as book:
        refused = book.mismatch("quote")
    if refused is not None:
        raise refused


def test_a_database_made_from_the_form_s_sql_matches_it(target: Target) -> None:
    made_from_sql(target, RELEASED)

    comparison = compared(target, RELEASED)

    assert comparison.matches
    assert comparison.extras == ()
    assert "nothing to change" in sql(target, RELEASED, comparison)


def test_review_distinguishes_generated_type_changes_from_manual_key_migrations(
    target: Target,
) -> None:
    from haute._quote_store._review import review_plan

    made_from_sql(target, RELEASED)
    retyped = form(table("policy", column("region"), column("drivers"), column("value", "int")))
    comparison = compared(target, retyped)
    review = review_plan(comparison.plan, retyped, PROVIDERS[target.backend])
    change = next(each for each in review if each.kind == "retype column")
    assert change.table == "policy" and change.column == "value"
    assert "Integer" in change.description
    if target.backend == "sqlite":
        assert change.disposition == "manual"
        assert "this SQL does not perform it" in change.description
        assert "ALTER TABLE" not in sql(target, retyped, comparison)
    else:
        assert change.disposition == "sql"
        assert "Review value conversion" in change.description
        assert "ALTER TABLE" in sql(target, retyped, comparison)

    many = form(
        table(
            "policy",
            column("region"),
            column("drivers"),
            column("value", "float"),
            {**column("row_number", "int"), "index": "numbered"},
            rows="many",
        )
    )
    reshaped = compared(target, many)
    changes = review_plan(reshaped.plan, many, PROVIDERS[target.backend])
    assert len(changes) == 1
    assert changes[0].kind == "reshape table" and changes[0].disposition == "manual"
    assert "migrate its rows" in changes[0].description
    assert "ALTER TABLE" not in sql(target, many, reshaped)


def test_review_includes_every_table_for_an_empty_database() -> None:
    from haute._quote_store._review import review_plan

    comparison = compare(RELEASED, Catalogue(tables=()), provider=PROVIDERS["sqlite"])
    changes = review_plan(comparison.plan, RELEASED, PROVIDERS["sqlite"])
    assert {each.table for each in changes} == {"quote_details", "policy", "haute_activity"}
    assert all(each.kind == "create table" and each.disposition == "sql" for each in changes)


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


#: A Decimal column made single precision by hand, on each engine.
_MADE_SINGLE = {
    "sqlite": (
        "ALTER TABLE policy DROP COLUMN value",
        "ALTER TABLE policy ADD COLUMN value REAL",
    ),
    "postgresql": ("ALTER TABLE policy ALTER COLUMN value TYPE REAL",),
    "sqlserver": ("ALTER TABLE policy ALTER COLUMN value REAL NULL",),
}


def test_a_single_precision_decimal_is_named_and_the_sql_widens_it(target: Target) -> None:
    made_from_sql(target, RELEASED)
    target.run(*_MADE_SINGLE[target.backend])

    comparison = compared(target, RELEASED)

    if target.backend == "sqlite":
        # SQLite keeps every REAL as a double: nothing is lost, and nothing differs.
        assert comparison.matches
    else:
        assert ("policy", "value", "type") in kinds(comparison)
        with pytest.raises(QuoteStoreMismatchError, match="policy.value is REAL, which keeps"):
            opens(target, RELEASED)
        target.script(sql(target, RELEASED, comparison))
    opens(target, RELEASED)
    quote(target, RELEASED, "A-1", value="16777217")
    assert target.read("SELECT value FROM policy") == [(16777217.0,)]


#: A Decimal column its owners made an Integer that takes no null, on each server.
_MADE_WHOLE_REQUIRED = {
    "postgresql": (
        "ALTER TABLE policy ALTER COLUMN value TYPE BIGINT",
        "ALTER TABLE policy ALTER COLUMN value SET NOT NULL",
    ),
    "sqlserver": ("ALTER TABLE policy ALTER COLUMN value BIGINT NOT NULL",),
}


def test_a_retyped_column_that_takes_no_null_keeps_taking_none(server: Target) -> None:
    """WB-64: the SQL changes the column's type and nothing else, as its note says."""
    made_from_sql(server, RELEASED)
    server.run(*_MADE_WHOLE_REQUIRED[server.backend])
    comparison = compared(server, RELEASED)
    written = sql(server, RELEASED, comparison)
    assert "Not changed here: policy.value takes no null" in written

    server.script(written)

    held = catalogue(server).table("policy")
    assert held is not None
    value = held.column("value")
    assert value is not None and value.kind == "float" and value.nullable is False


def test_the_export_converts_a_column_of_another_type_keeping_its_values(target: Target) -> None:
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "A-1", drivers="2")
    retyped = form(
        table("policy", column("region"), column("drivers", "int"), column("value", "float"))
    )

    written = exported(target, retyped)

    if target.backend == "sqlite":
        # SQLite cannot change a column's type in place: a note says how, and nothing tries.
        assert "which SQLite cannot do\n-- in place" in written
        assert "ALTER COLUMN" not in written
        return
    assert "ALTER COLUMN drivers" in written
    target.script(written)
    opens(target, retyped)
    assert target.read("SELECT drivers FROM policy") == [(2,)]


def test_the_export_converts_a_number_column_to_text_the_app_opens(server: Target) -> None:
    """To Text, the column takes the text type and comparison Haute keeps (SQL Server's
    collation among them), so the app's check finds nothing after it."""
    made_from_sql(server, RELEASED)
    quote(server, RELEASED, "A-1", value="12.5")
    retyped = form(table("policy", column("region"), column("drivers"), column("value")))

    written = exported(server, retyped)

    assert "ALTER COLUMN value" in written
    server.script(written)
    opens(server, retyped)
    assert server.read("SELECT value FROM policy") == [("12.5",)]


def test_a_value_the_export_cannot_convert_stops_it_changing_nothing(server: Target) -> None:
    made_from_sql(server, RELEASED)
    quote(server, RELEASED, "A-1", drivers="n/a")
    retyped = form(
        table("cover", column("excess", "float")),
        table(
            "policy",
            column("region"),
            column("seats", "int"),
            column("drivers", "int"),
            column("value", "float"),
        ),
    )
    written = exported(server, retyped)
    # A table and a column are made before the conversion that fails, and undone with it.
    converted = written.index("ALTER COLUMN drivers")
    assert written.index("CREATE TABLE cover") < converted
    assert written.index("ALTER TABLE policy ADD") < converted

    with pytest.raises(Exception, match=r"(?i)invalid input syntax|error converting"):
        server.script(written)

    held = catalogue(server)
    assert held.table("cover") is None
    policy = held.table("policy")
    assert policy is not None
    assert policy.column("seats") is None
    drivers = policy.column("drivers")
    assert drivers is not None and drivers.kind == "string"
    assert server.read("SELECT drivers FROM policy") == [("n/a",)]


def test_the_export_converts_no_identity_column(server: Target) -> None:
    """The identity keeps the store's type, whatever the database holds it as: the SQL says
    it takes no change, and changes nothing of it."""
    made_from_sql(server, RELEASED)
    if server.backend == "postgresql":
        server.run("ALTER TABLE policy ALTER COLUMN quote_option TYPE INTEGER")
    else:
        # SQL Server changes no column of a key or a reference: those go first.
        (refers,) = server.read(
            "SELECT name FROM sys.foreign_keys WHERE parent_object_id = OBJECT_ID('policy')"
        )
        (keyed,) = server.read(
            "SELECT name FROM sys.key_constraints WHERE type = 'PK' "
            "AND parent_object_id = OBJECT_ID('policy')"
        )
        server.run(
            f"ALTER TABLE policy DROP CONSTRAINT {refers[0]}",
            f"ALTER TABLE policy DROP CONSTRAINT {keyed[0]}",
            "ALTER TABLE policy ALTER COLUMN quote_option INT NOT NULL",
        )
    comparison = compared(server, RELEASED)
    assert ("policy", "quote_option", "type") in kinds(comparison)
    assert "policy.quote_option takes no change of its type" in comparison.plan.refusals

    written = exported(server, RELEASED)

    assert "ALTER COLUMN quote_option" not in written


def test_the_export_converts_no_column_the_analyst_pairs(target: Target) -> None:
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "A-1", drivers="2")
    paired = form(
        table("policy", column("region"), column("seats", "int"), column("value", "float"))
    )
    pairing = Rename("policy", "seats", "drivers")
    comparison = compared(target, paired, renames=[pairing])
    assert {("policy", "seats", "renamed"), ("policy", "seats", "type")} <= kinds(comparison)

    written = exported(target, paired, renames=[pairing])

    # seats is added afresh at its type, drivers left as it is with its values.
    assert "ALTER COLUMN" not in written and "changes from" not in written
    target.script(written)
    assert target.read("SELECT drivers, seats FROM policy") == [("2", None)]


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


def _vehicles(index: str) -> dict[str, Any]:
    """A many-row input table keyed by its index, named *index*."""
    return table(
        "vehicles", {"name": index, "type": "int", "index": "numbered"}, column("reg"), rows="many"
    )


def test_a_renamed_index_is_paired_with_the_databases_key_column_unasked(target: Target) -> None:
    before = form(_vehicles("row_number"))
    made_from_sql(target, before)
    quote(target, before, "A-1")
    target.run(
        "INSERT INTO vehicles (quote_reference, quote_option, row_number, reg) "
        "VALUES ('A-1', 1, 1, 'AB12'), ('A-1', 1, 2, 'CD34')"
    )
    renamed = form(_vehicles("vehicle_no"))

    comparison = compared(target, renamed)

    # Paired by what it is, marked renamed, and the key compared under the form's names: no
    # key difference, no column missing, and no extra. The analyst paired nothing.
    assert kinds(comparison) == {("vehicles", "vehicle_no", "renamed")}
    assert comparison.extras == ()
    assert comparison.renames == ()
    assert [step.kind for step in comparison.plan.steps] == ["rename column"]
    target.script(sql(target, renamed, comparison))
    opens(target, renamed)
    assert target.read("SELECT vehicle_no, reg FROM vehicles ORDER BY vehicle_no") == [
        (1, "AB12"),
        (2, "CD34"),
    ]


def test_the_export_renames_the_key_column_for_a_renamed_index(target: Target) -> None:
    before = form(_vehicles("row_number"))
    made_from_sql(target, before)
    quote(target, before, "A-1")
    target.run(
        "INSERT INTO vehicles (quote_reference, quote_option, row_number, reg) "
        "VALUES ('A-1', 1, 1, 'AB12'), ('A-1', 1, 2, 'CD34')"
    )
    renamed = form(_vehicles("vehicle_no"))

    written = exported(target, renamed)

    # The key column renamed in place, keeping its values and its place in the key: no column
    # added beside it.
    assert "ADD" not in written
    target.script(written)
    held = catalogue(target).table("vehicles")
    assert held is not None and held.primary_key == (*IDENTITY, "vehicle_no")
    assert compared(target, renamed).matches
    opens(target, renamed)
    assert target.read("SELECT vehicle_no, reg FROM vehicles ORDER BY vehicle_no") == [
        (1, "AB12"),
        (2, "CD34"),
    ]


#: A quote's policy with two many-row tables, one numbered, one with a row per peril.
KEYED = form(
    table("policy", column("region")),
    table(
        "sites",
        {"name": "site", "type": "int", "index": "numbered"},
        column("cover", "float"),
        rows="many",
    ),
    table(
        "perils",
        {
            "name": "peril",
            "type": "str",
            "index": "values",
            "options": ["fire", "flood"],
            "options_source": {"file": "perils.csv", "column": "peril"},
        },
        column("excess", "float"),
        rows="many",
    ),
)


def _kept_by_row_number(target: Target, rows: dict[str, list[str]]) -> None:
    """:data:`KEYED`'s tables made, a quote A-1 saved, and each many-row table made again as a
    store keyed by its row number made it before 2026-11-05: keyed by ``quote_row``, its index
    a column taking a null (an index of values one of ordinary Text), holding *rows*, each the
    values of its form's columns then its quote_row."""
    made_from_sql(target, KEYED)
    quote(target, KEYED, "A-1", region="north")
    held = catalogue(target)
    policy = held.table("policy")
    assert policy is not None
    text = policy.column("region")
    assert text is not None
    for name, values in rows.items():
        made = held.table(name)
        assert made is not None
        typed = {column.name: column.type for column in made.columns}
        index, other = (column for column in made.columns if column.name not in IDENTITY)
        index_type = text.type if index.kind == "string" else index.type
        target.run(f"DROP TABLE {name}")
        target.run(
            f"CREATE TABLE {name} (quote_reference {typed['quote_reference']} NOT NULL, "
            f"quote_option {typed['quote_option']} NOT NULL, {index.name} {index_type} NULL, "
            f"{other.name} {other.type} NULL, quote_row {typed['quote_option']} NOT NULL, "
            "PRIMARY KEY (quote_reference, quote_option, quote_row), "
            "FOREIGN KEY (quote_reference, quote_option) "
            "REFERENCES quote_details (quote_reference, quote_option))",
            *(f"INSERT INTO {name} VALUES ('A-1', 1, {each})" for each in values),
        )


def test_the_export_keys_a_table_kept_by_its_row_number_by_its_index(target: Target) -> None:
    _kept_by_row_number(
        target,
        {
            "sites": ["NULL, 7.5, 1", "NULL, 8.5, 2"],
            "perils": ["'fire', 1.5, 1", "'flood', 2.5, 2"],
        },
    )
    read = catalogue(target)
    comparison = compare(KEYED, read, provider=PROVIDERS[target.backend])
    assert {("sites", "site", "renamed"), ("perils", "peril", "renamed")} <= kinds(comparison)
    # The index renames the comparison's plan refuses, the old key's column holding the
    # index's name, and what it notes of that key, are what the export answers.
    answered = answered_by(comparison, additions_plan(KEYED, read, comparison))
    assert len(comparison.plan.refusals) == 2
    assert set(comparison.plan.refusals) <= answered
    assert set(comparison.plan.notes) <= answered

    written = exported(target, KEYED)

    if target.backend == "sqlite":
        # SQLite cannot change a table's key in place: a note for each, and nothing tries.
        assert written.count("SQLite cannot change a\n-- table's key in place") == 2
        assert "DROP CONSTRAINT" not in written and "ALTER COLUMN" not in written
        return
    # The numbered index takes the old key's row numbers; one of values keeps its own.
    assert "UPDATE sites SET site = quote_row WHERE site IS NULL" in written
    assert "UPDATE perils" not in written
    target.script(written)
    held = catalogue(target)
    for name, index in (("sites", "site"), ("perils", "peril")):
        made = held.table(name)
        assert made is not None and made.primary_key == (*IDENTITY, index)
        old = made.column("quote_row")
        assert old is not None and old.nullable
    assert target.read("SELECT site, cover, quote_row FROM sites ORDER BY site") == [
        (1, 7.5, 1),
        (2, 8.5, 2),
    ]
    assert target.read("SELECT peril, excess FROM perils ORDER BY peril") == [
        ("fire", 1.5),
        ("flood", 2.5),
    ]
    assert compared(target, KEYED).matches
    opens(target, KEYED)
    # A quote saved now writes its rows with no quote_row, which takes the null.
    with open_book(
        target.url, KEYED, environment=ENVIRONMENT, haute_version="1", sign_in=target.sign_in
    ) as book:
        book.create(
            operation="operation-B-1",
            actor="ana",
            values={
                "policy": [{"region": "south"}],
                "sites": [{"cover": "3"}],
                "perils": [{"peril": "flood", "excess": "4"}],
            },
            at=book.now(),
            reference="B-1",
        )
    assert target.read(
        "SELECT site, cover, quote_row FROM sites WHERE quote_reference = 'B-1'"
    ) == [(1, 3.0, None)]
    assert ("flood", 4.0) in target.read(
        "SELECT peril, excess FROM perils WHERE quote_reference = 'B-1'"
    )


def test_an_index_value_missing_stops_the_export_changing_nothing(server: Target) -> None:
    _kept_by_row_number(server, {"perils": ["NULL, 1.5, 1"]})
    written = exported(server, KEYED)
    assert "DROP CONSTRAINT" in written

    with pytest.raises(Exception, match=r"(?i)null"):
        server.script(written)

    made = catalogue(server).table("perils")
    assert made is not None and made.primary_key == (*IDENTITY, "quote_row")
    assert server.read("SELECT peril, excess, quote_row FROM perils") == [(None, 1.5, 1)]


def test_an_index_value_too_long_for_the_index_stops_the_export_changing_nothing(
    server: Target,
) -> None:
    # Longer than either server's index holds (VARCHAR(255), NVARCHAR(366)): PostgreSQL's cast
    # to VARCHAR(255) would cut it short and key the table by what was left.
    long = "x" * 400
    _kept_by_row_number(server, {"perils": [f"'{long}', 1.5, 1"]})
    written = exported(server, KEYED)
    assert "DROP CONSTRAINT" in written

    with pytest.raises(Exception, match=r"(?i)too long|truncated"):
        server.script(written)

    made = catalogue(server).table("perils")
    assert made is not None and made.primary_key == (*IDENTITY, "quote_row")
    assert server.read("SELECT peril, quote_row FROM perils") == [(long, 1)]


def _vehicles_of(index: str, other: str) -> dict[str, Any]:
    """A many-row input table keyed by its index *index*, with an Integer *other*."""
    return table(
        "vehicles",
        {"name": index, "type": "int", "index": "numbered"},
        column(other, "int"),
        rows="many",
    )


@pytest.mark.parametrize("index_name", ["row_number", "vehicle_name"])
def test_live_comparison_renders_an_index_type_change_with_its_previous_column(
    target: Target, index_name: str
) -> None:
    made_from_sql(target, form(_vehicles("row_number")))
    changed = form(
        table(
            "vehicles",
            {"name": index_name, "type": "str", "index": "typed"},
            column("reg"),
            rows="many",
        )
    )
    comparison = compared(target, changed)

    written = sql(target, changed, comparison)

    assert f"vehicles has its index {index_name} change from an Integer to Text" in written
    assert "make the table again as the form has it" in written
    assert "ALTER COLUMN" not in written


def test_an_index_paired_first_frees_its_old_name_for_a_new_column(target: Target) -> None:
    before = form(_vehicles_of("row_number", "seats"))
    made_from_sql(target, before)
    reused = form(
        table(
            "vehicles",
            {"name": "vehicle_no", "type": "int", "index": "numbered"},
            column("seats", "int"),
            column("row_number"),
            rows="many",
        )
    )

    comparison = compared(target, reused)

    # The database's key column is the index, whatever the form now names it: no column of
    # the form takes it by name.
    assert kinds(comparison) == {
        ("vehicles", "vehicle_no", "renamed"),
        ("vehicles", "row_number", "missing"),
    }
    assert comparison.plan.refusals == ()
    target.script(sql(target, reused, comparison))
    opens(target, reused)


def test_the_export_frees_a_renamed_keys_name_for_a_new_column(target: Target) -> None:
    before = form(_vehicles_of("row_number", "seats"))
    made_from_sql(target, before)
    reused = form(
        table(
            "vehicles",
            {"name": "vehicle_no", "type": "int", "index": "numbered"},
            column("seats", "int"),
            column("row_number"),
            rows="many",
        )
    )

    written = exported(target, reused)

    # The key column is renamed for the index, then row_number added under the name it freed.
    assert "already exists" not in written
    assert written.lower().index("rename") < written.index("ALTER TABLE vehicles ADD")
    target.script(written)
    opens(target, reused)


def test_an_index_renamed_onto_another_columns_name_is_refused(target: Target) -> None:
    before = form(_vehicles_of("row_number", "item_number"))
    made_from_sql(target, before)
    swapped = form(_vehicles_of("item_number", "row_number"))

    comparison = compared(target, swapped)

    # Paired by what it is, the index's rename onto a name the database's other column holds
    # is refused, and so is a column added under the name the index keeps: the SQL changes
    # nothing but keep the database's column the form no longer names, and runs.
    assert ("vehicles", "item_number", "renamed") in kinds(comparison)
    assert [step.kind for step in comparison.plan.steps] == ["keep column"]
    assert len(comparison.plan.refusals) == 2
    target.script(sql(target, swapped, comparison))
    assert target.read("SELECT row_number, item_number FROM vehicles") == []


def test_a_numbered_index_whose_column_holds_values_moves_no_key(target: Target) -> None:
    before = form(_vehicles_of("row_number", "item_number"))
    made_from_sql(target, before)
    quote(target, before, "A-1")
    target.run(
        "INSERT INTO vehicles (quote_reference, quote_option, row_number, item_number) "
        "VALUES ('A-1', 1, 1, 42), ('A-1', 1, 2, 7)"
    )
    swapped = form(_vehicles_of("item_number", "row_number"))
    read = catalogue(target)
    comparison = compare(swapped, read, provider=PROVIDERS[target.backend])

    additions = additions_plan(swapped, read, comparison)

    # 42 and 7 are no rows' numbers: the swap stays refused, and the export keys nothing.
    assert not any(step.kind in ("rekey table", "rename key") for step in additions.steps)
    assert comparison.plan.refusals
    assert not answered_by(comparison, additions)


def test_a_tables_rows_are_read_from_its_key(target: Target) -> None:
    # Keyed by the identity alone, the database's table is one row a quote; by one more
    # column, many, whatever that column is named.
    made_from_sql(target, form(table("vehicles", column("reg"))))
    assert ("vehicles", None, "rows") in kinds(compared(target, form(_vehicles("row_number"))))


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


@pytest.mark.parametrize("replacement", [None, "TEXT NOT NULL DEFAULT ''"])
def test_internal_column_repairs_identify_the_field_and_keep_manual_sql_notes(
    tmp_path: Path, replacement: str | None
) -> None:
    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    made_from_sql(target, RELEASED)
    assert compared(target, RELEASED).matches
    target.run("ALTER TABLE quote_details DROP COLUMN note_at")
    if replacement is not None:
        target.run(f"ALTER TABLE quote_details ADD COLUMN note_at {replacement}")

    comparison = compared(target, RELEASED)

    assert not comparison.matches
    assert len(comparison.differences) == (1 if replacement is None else 2)
    assert kinds(comparison) == {("quote_details", "note_at", "store")}
    assert not comparison.plan.steps
    written = sql(target, RELEASED, comparison)
    for difference in comparison.differences:
        assert difference.message in comparison.plan.notes
        assert f"-- Not changed here: {difference.message}" in written


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
