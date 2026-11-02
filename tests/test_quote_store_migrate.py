"""The quote store's schema changes (specs/workbench): a plan from one structure of the store's
tables to a form's, by stable id, and the SQL Haute writes from it for the people who own a
database, from an earlier form or from none, which keeps what the form removed, notes what it
cannot make, and is one transaction."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import _migrate, offline_plan, open_book, render_sql, store_url
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


def column(name: str, type_: str = "str", id_: str | None = None, **rest: Any) -> dict[str, Any]:
    return {"id": id_ or f"c_{name}", "name": name, "type": type_, **rest}


def table(
    name: str, *columns: dict[str, Any], rows: str = "one", id_: str | None = None
) -> dict[str, Any]:
    return {"id": id_ or f"t_{name}", "name": name, "rows": rows, "columns": list(columns)}


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


def test_a_plan_names_each_step_by_stable_id() -> None:
    before = structure(form(POLICY, table("extras", column("notes"))))
    after = structure(
        form(
            table(
                "policy_info",
                column("area", id_="c_region"),
                column("value", "float"),
                column("seats", "int"),
                id_="t_policy",
            ),
            table("drivers", column("age", "int")),
        )
    )

    made = _migrate.plan(after, before, dict.fromkeys(_migrate.wanted_counts(after, before), 0))

    assert [(step.kind, step.table, step.column, step.was) for step in made.steps] == [
        ("rename table", "policy_info", None, "policy"),
        ("rename column", "policy_info", "area", "region"),
        ("add column", "policy_info", "seats", None),
        ("create table", "drivers", None, None),
        ("keep table", "extras", None, None),
    ]
    assert made.refusals == ()
    # The form's tables once it is carried out: nothing of what the form removed.
    assert made.recorded == after


# ── The SQL for a store ─────────────────────────────────────────────────


def test_the_sql_from_none_makes_every_table_and_the_activity(url: str) -> None:
    release = form(POLICY, table("vehicles", column("reg"), rows="many"))

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


def test_the_sql_for_a_change_renames_and_adds_keeping_every_value(url: str) -> None:
    base = form(table("policy", column("left", id_="c_a"), column("right", id_="c_b")))
    script_run(url, script(None, base))
    quote(url, left="L", right="R")
    # A swap of two names, a change of the table's case and a column added.
    release = form(
        table(
            "Policy",
            column("right", id_="c_a"),
            column("left", id_="c_b"),
            column("seats", "int"),
            id_="t_policy",
        )
    )

    written = script(base, release)

    # Renamed through temporary names, never dropped and added again.
    assert "haute_tmp_" in written
    assert "DROP" not in written
    script_run(url, written)
    assert columns(url, "Policy")[2:] == ["right", "left", "seats"]
    assert sql(url, 'SELECT "right", "left", seats FROM "Policy"') == [("L", "R", None)]
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


def test_a_removed_table_or_column_is_a_drop_left_commented_out() -> None:
    base = form(POLICY, table("extras", column("notes")))

    written = script(base, form(table("policy", column("region"))), "postgresql")

    assert "-- The form does not have extras; drop it once nothing reads it:" in written
    assert "-- DROP TABLE extras;" in written
    assert "-- ALTER TABLE policy DROP COLUMN value;" in written
    assert [
        line for line in written.splitlines() if "DROP" in line and not line.startswith("--")
    ] == []


def test_a_removed_column_s_drop_names_its_table_as_the_renames_leave_it() -> None:
    # Two tables swap names while one loses a column: its DROP names the table under its new
    # name, never the other table now holding its old one.
    base = form(POLICY, table("cover", column("level")))
    release = form(
        table("cover", column("region"), id_="t_policy"),
        table("policy", column("level"), id_="t_cover"),
    )

    written = script(base, release, "postgresql")

    assert "-- ALTER TABLE cover DROP COLUMN value;" in written
    assert "DROP COLUMN value;" not in written.replace("-- ALTER TABLE cover DROP COLUMN", "")


def test_a_refusal_naming_a_name_of_many_lines_stays_a_comment() -> None:
    hostile = "a\nDELETE FROM policy;\n--"
    base = form(table("policy", column(hostile)))
    release = form(table("policy", column(hostile, id_="c_other")))

    written = script(base, release, "postgresql")

    assert "-- Not written: the new column policy.a" in written
    assert "-- DELETE FROM policy;" in written
    assert "DELETE FROM policy;" not in [line.strip() for line in written.splitlines()]


def test_what_the_sql_cannot_make_is_a_note() -> None:
    base = form(POLICY, table("vehicles", column("reg"), rows="many"))

    # A table whose rows change is noted, never made again; a new column taking the name of
    # one the form removes is not written, its refusal noted.
    release = form(
        table("policy", column("region"), column("value", "float", id_="c_other")),
        table("vehicles", column("reg"), rows="one"),
    )
    written = script(base, release, "postgresql")

    assert written.startswith(
        "-- Not written: the new column policy.value takes the name of a column the form removes"
    )
    assert (
        "-- vehicles changes from an input table of many rows per quote to an input table of "
        in written
    )
    assert "CREATE TABLE vehicles" not in written


def test_the_sql_is_one_transaction_with_every_value_written_in(url: str) -> None:
    script_run(url, script(None, form(POLICY)))
    quote(url, region="north", value=1.5)
    renamed = form(table("policy", column("area", id_="c_region"), column("value", "float")))
    written = script(form(POLICY), renamed)

    assert written.startswith("-- Run with sqlite3 -bail")
    assert "BEGIN;" in written and written.rstrip().endswith("COMMIT;")
    assert "?" not in written and "%(" not in written

    # A statement failing before the COMMIT leaves every step undone.
    failing = written.replace("COMMIT;", "SELECT * FROM haute_no_such_table;\nCOMMIT;")
    with pytest.raises(sqlite3.OperationalError, match="haute_no_such_table"):
        script_run(url, failing)
    assert columns(url, "policy") == ["quote_reference", "quote_option", "region", "value"]

    # Run whole, the tables fit the release: the app opens them, the quote's values kept.
    script_run(url, written)
    with open_book(url, renamed, environment=TEST, haute_version="1") as book:
        assert book.read("A-1", 1).values["t_policy"][0]["c_region"] == "north"


def test_a_form_the_tables_already_fit_has_nothing_to_change() -> None:
    assert script(form(POLICY), form(POLICY)) == (
        "-- The tables already fit the form: nothing to change.\n"
    )
