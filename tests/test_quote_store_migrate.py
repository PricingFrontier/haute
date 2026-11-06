"""The quote store's schema changes (specs/workbench): a plan from one structure of the store's
tables to a form's, by name, and the SQL Haute writes from it for the people who own a
database, from an earlier form or from none, which keeps what the form removed, notes what it
cannot make, and is one transaction."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import _migrate, offline_plan, open_book, render_sql, store_url
from haute._quote_store._rows import KEPT_KEY
from haute._quote_store._schema import structure
from haute._workbench_form import FormSpec, with_quote_details

PROJECT = "motor"
TEST = "test"


def form(*tables: dict[str, Any]) -> FormSpec:
    return with_quote_details(
        FormSpec.model_validate(
            {
                "name": PROJECT,
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


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, **rest}


def table(name: str, *columns: dict[str, Any], rows: str = "one") -> dict[str, Any]:
    """A schema table of inputs."""
    return {"name": name, "rows": rows, "inputs": list(columns)}


POLICY = table("policy", column("region"), column("value", "float"))


@pytest.fixture
def url(tmp_path: Path) -> str:
    return store_url(tmp_path / "store.sqlite")


def sql(url: str, query: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        return raw.execute(query).fetchall()


def run(url: str, *statements: str) -> None:
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        raw.execute("PRAGMA foreign_keys=OFF")
        for statement in statements:
            raw.execute(statement)


def script_run(url: str, script: str) -> None:
    """*script* run on the file as its owners run it, one statement after another."""
    raw = sqlite3.connect(url.removeprefix("sqlite:///"))
    try:
        raw.executescript(script)
    finally:
        raw.close()


def columns(url: str, name: str) -> list[str]:
    return [row[1] for row in sql(url, f'PRAGMA table_info("{name}")')]


def quote(url: str, reference: str = "A-1", **policy: Any) -> None:
    """A quote's quote_details row and, given *policy*, its policy row, written as a report sees
    them."""
    run(
        url,
        "INSERT INTO quote_details (quote_reference, quote_option, stage, assignee, archived, "
        "created_date, created_by, modified_date, modified_by, quote_version, saved_at, saved_by) "
        f"VALUES ('{reference}', 1, 'draft', 'ana', 0, '2026-10-30', 'ana', '2026-10-30', 'ana', "
        "1, '2026-10-30 09:00:00', 'ana')",
    )
    if policy:
        names = ", ".join(policy)
        values = ", ".join(repr(value) for value in policy.values())
        run(
            url,
            f"INSERT INTO policy (quote_reference, quote_option, {names}) "
            f"VALUES ('{reference}', 1, {values})",
        )


def script(base: FormSpec | None, release: FormSpec, dialect: str = "sqlite") -> str:
    return render_sql(offline_plan(base, release), release, dialect)


# ── The plan ────────────────────────────────────────────────────────────


def test_a_plan_names_each_step_by_name_a_rename_one_kept_and_one_made() -> None:
    before = structure(form(POLICY, table("extras", column("notes"))))
    # region renamed area and seats added in policy, extras renamed additions, drivers new.
    after = structure(
        form(
            table("policy", column("area"), column("value", "float"), column("seats", "int")),
            table("additions", column("notes")),
            table("drivers", column("age", "int")),
        )
    )

    made = _migrate.plan(after, before, dict.fromkeys(_migrate.wanted_counts(after, before), 0))

    # A table or column is its name (READ-23): renamed, it is one the SQL keeps, values and
    # all, and one it makes.
    assert [(step.kind, step.table, step.column, step.was) for step in made.steps] == [
        ("add column", "policy", "area", None),
        ("add column", "policy", "seats", None),
        ("keep column", "policy", "region", None),
        ("create table", "additions", None, None),
        ("create table", "drivers", None, None),
        ("keep table", "extras", None, None),
    ]
    assert [(step.table_key, step.column_key) for step in made.steps] == [
        ("policy", "area"),
        ("policy", "seats"),
        ("policy", "region"),
        ("additions", None),
        ("drivers", None),
        ("extras", None),
    ]
    assert made.refusals == ()
    # The form's tables once it is carried out: nothing of what the form removed.
    assert made.recorded == after


# ── The SQL for a store ─────────────────────────────────────────────────


def test_the_sql_from_none_makes_every_table_and_the_activity(url: str) -> None:
    release = form(
        POLICY,
        table("vehicles", column("vehicle", "int", index="numbered"), column("reg"), rows="many"),
    )

    written = script(None, release)

    for name in ("quote_details", "policy", "vehicles", "haute_activity"):
        assert f"CREATE TABLE {name}" in written
    # Run, its tables fit the release's form: keys and references are as the app's check reads.
    script_run(url, written)
    assert {row[0] for row in sql(url, "SELECT name FROM sqlite_master WHERE type = 'table'")} == {
        "quote_details",
        "policy",
        "vehicles",
        "haute_activity",
    }
    open_book(url, release, environment=TEST, haute_version="1").close()


def test_the_sql_for_a_renamed_column_adds_it_and_keeps_the_old_with_its_values(url: str) -> None:
    base = form(table("policy", column("left"), column("right")))
    script_run(url, script(None, base))
    quote(url, left="L", right="R")
    # right renamed starboard, and a column added.
    release = form(table("policy", column("left"), column("starboard"), column("seats", "int")))

    written = script(base, release)

    # Nothing renamed, nothing dropped: the old column stays, its DROP commented out.
    assert "haute_tmp_" not in written
    assert [
        line for line in written.splitlines() if "DROP" in line and not line.startswith("--")
    ] == []
    assert '-- ALTER TABLE policy DROP COLUMN "right";' in written
    script_run(url, written)
    assert columns(url, "policy")[2:] == ["left", "right", "starboard", "seats"]
    assert sql(url, 'SELECT "left", "right", starboard, seats FROM policy') == [
        ("L", "R", None, None)
    ]
    open_book(url, release, environment=TEST, haute_version="1").close()


def test_a_retype_is_the_engines_alter_in_place_with_its_note() -> None:
    retyped = form(table("policy", column("region"), column("value", "int")))

    postgresql = script(form(POLICY), retyped, "postgresql")
    assert "-- policy.value changes from DOUBLE PRECISION to BIGINT" in postgresql
    assert "ALTER TABLE policy ALTER COLUMN value TYPE BIGINT USING value::BIGINT" in postgresql

    sqlserver = script(form(POLICY), retyped, "mssql")
    assert "-- policy.value changes from FLOAT(53) to BIGINT" in sqlserver
    assert "ALTER TABLE policy ALTER COLUMN value BIGINT NULL" in sqlserver

    # SQLite cannot change a column's type: the note says what to do, and no statement tries.
    sqlite = script(form(POLICY), retyped)
    assert "which SQLite cannot do\n-- in place" in sqlite
    assert "ALTER COLUMN" not in sqlite


@pytest.mark.parametrize("index_name", ["site", "location_name"])
def test_an_index_retyped_is_a_note_and_a_change_of_its_kind_no_step(index_name: str) -> None:
    numbered = form(
        table(
            "sites", column("site", "int", index="numbered"), column("value", "float"), rows="many"
        )
    )
    # Retyped, the index changes the key its rows are held by: a note, never an ALTER.
    retyped = form(
        table(
            "sites",
            column(index_name, "str", index="numbered"),
            column("value", "float"),
            rows="many",
        )
    )
    for dialect in ("postgresql", "mssql", "sqlite"):
        written = script(numbered, retyped, dialect)
        assert (
            f"-- sites has its index {index_name} change from an Integer to Text,\n"
            "-- which changes the key its rows are held by" in written
        )
        assert "ALTER COLUMN" not in written
        if index_name != "site":
            assert "location_name" in written and "haute_tmp_" in written
    # Typed instead of numbered: the key column and its SQL type stay, so nothing to write.
    typed = form(
        table("sites", column("site", "int", index="typed"), column("value", "float"), rows="many")
    )
    assert offline_plan(numbered, typed).steps == ()


def test_a_removed_table_or_column_is_a_drop_left_commented_out() -> None:
    base = form(POLICY, table("extras", column("notes")))

    written = script(base, form(table("policy", column("region"))), "postgresql")

    assert "-- The form does not have extras; drop it once nothing reads it:" in written
    assert "-- DROP TABLE extras;" in written
    assert "-- ALTER TABLE policy DROP COLUMN value;" in written
    assert [
        line for line in written.splitlines() if "DROP" in line and not line.startswith("--")
    ] == []


def test_a_refusal_naming_a_name_of_many_lines_stays_a_comment() -> None:
    hostile = "a\nDELETE FROM policy;\n--"
    base = form(table("policy", column(hostile)))
    # Renamed in its case alone: a new column taking the removed one's name, refused.
    release = form(table("policy", column(hostile.upper())))

    written = script(base, release, "postgresql")

    assert "-- Not written: the new column policy.A" in written
    assert "-- DELETE FROM POLICY;" in written
    assert "DELETE FROM POLICY;" not in [line.strip() for line in written.splitlines()]


def test_what_the_sql_cannot_make_is_a_note() -> None:
    base = form(
        POLICY,
        table("vehicles", column("vehicle", "int", index="numbered"), column("reg"), rows="many"),
    )

    # A table whose rows change is noted, never made again; a new column taking the name of
    # one the form removes, whatever its case, is not written, its refusal noted.
    release = form(
        table("policy", column("region"), column("Value", "float")),
        table("vehicles", column("reg"), rows="one"),
    )
    written = script(base, release, "postgresql")

    assert written.startswith(
        "-- Not written: the new column policy.Value takes the name of a column the form removes"
    )
    assert (
        "-- vehicles changes from a table of many rows per quote to a table of one row per "
        "quote,\n" in written
    )
    assert "CREATE TABLE vehicles" not in written


def test_the_sql_is_one_transaction_with_every_value_written_in(url: str) -> None:
    script_run(url, script(None, form(POLICY)))
    quote(url, region="north", value=1.5)
    renamed = form(table("policy", column("area"), column("value", "float")))
    written = script(form(POLICY), renamed)

    assert written.startswith("-- Run with sqlite3 -bail")
    assert "BEGIN;" in written and written.rstrip().endswith("COMMIT;")
    assert "?" not in written and "%(" not in written

    # A statement failing before the COMMIT leaves every step undone.
    failing = written.replace("COMMIT;", "SELECT * FROM haute_no_such_table;\nCOMMIT;")
    with pytest.raises(sqlite3.OperationalError, match="haute_no_such_table"):
        script_run(url, failing)
    assert columns(url, "policy") == ["quote_reference", "quote_option", "region", "value"]

    # Run whole, the tables fit the release: the app opens them, the quote's values kept, the
    # renamed column's under its old name.
    script_run(url, written)
    assert sql(url, "SELECT region, area FROM policy") == [("north", None)]
    with open_book(url, renamed, environment=TEST, haute_version="1") as book:
        row = book.read("A-1", 1).values["policy"][0]
        assert row["value"] == "1.5"
        assert json.loads(row[KEPT_KEY]) == {"region": "north"}


def test_a_form_the_tables_already_fit_has_nothing_to_change() -> None:
    assert script(form(POLICY), form(POLICY)) == (
        "-- The tables already fit the form: nothing to change.\n"
    )
