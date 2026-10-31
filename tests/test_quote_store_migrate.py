"""The quote store's schema changes (specs/workbench): dev's planner bringing the analyst's
SQLite store to a release's form by stable id, every change made and what goes named; and the
SQL Haute writes for the people who own a hosted database, from an earlier form or from none,
which keeps what the form removed, notes what it cannot make, and is one transaction."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import (
    MigrationRefusedError,
    _migrate,
    offline_plan,
    open_book,
    prepare_store,
    render_sql,
    store_url,
)
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


def migrate(url: str, *tables: dict[str, Any]) -> Any:
    return prepare_store(url, form(*tables), project=PROJECT)


def kinds(made: Any) -> list[tuple[str, str]]:
    return [
        (step.kind, step.table if step.column is None else f"{step.table}.{step.column}")
        for step in made.steps
    ]


def script(base: FormSpec | None, release: FormSpec, dialect: str = "sqlite") -> str:
    return render_sql(offline_plan(base, release), release, dialect)


# ── Dev's plan ──────────────────────────────────────────────────────────


def test_dev_s_plan_names_each_step_by_stable_id() -> None:
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

    made = _migrate.plan(after, before, {("t_extras", None): 0}, "dev")

    assert [(step.kind, step.table, step.column, step.was) for step in made.steps] == [
        ("rename table", "policy_info", None, "policy"),
        ("rename column", "policy_info", "area", "region"),
        ("add column", "policy_info", "seats", None),
        ("create table", "drivers", None, None),
        ("drop table", "extras", None, None),
    ]
    assert made.refusals == () and made.mode == "dev"
    # What haute_store records after it: the form's tables, nothing of what went.
    assert made.recorded == after


def test_an_empty_dev_store_takes_every_table_and_a_second_prepare_changes_nothing(
    url: str,
) -> None:
    made = migrate(url, POLICY)

    assert kinds(made) == [("create table", "quote_details"), ("create table", "policy")]
    assert migrate(url, POLICY).steps == ()


def test_a_change_of_case_and_a_swap_of_two_names_keep_every_value(url: str) -> None:
    migrate(url, table("policy", column("left", id_="c_a"), column("right", id_="c_b")))
    quote(url, left="L", right="R")

    migrate(
        url, table("Policy", column("right", id_="c_a"), column("left", id_="c_b"), id_="t_policy")
    )

    assert columns(url, "Policy")[2:] == ["right", "left"]
    assert sql(url, 'SELECT "right", "left" FROM "Policy"') == [("L", "R")]


def test_a_column_the_form_has_that_the_sql_side_dropped_is_refused_naming_it(url: str) -> None:
    migrate(url, POLICY)
    run(url, "ALTER TABLE policy DROP COLUMN value")

    with pytest.raises(MigrationRefusedError, match="policy.value, which the form has, is missing"):
        migrate(url, POLICY)


def test_a_plan_failing_part_way_leaves_nothing_changed(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(url, POLICY)
    quote(url, region="north")

    def fails(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the record could not be written")

    monkeypatch.setattr(_migrate, "_record", fails)
    with pytest.raises(RuntimeError):
        migrate(url, table("policy", column("area", id_="c_region"), column("seats", "int")))

    assert columns(url, "policy")[2:] == ["region", "value"]
    assert sql(url, "SELECT region FROM policy") == [("north",)]


def test_dev_drops_what_the_form_removed_and_rebuilds_what_changed_naming_each(url: str) -> None:
    extras = table("extras", column("notes"))
    prepare_store(url, form(POLICY, extras), project=PROJECT)
    quote(url, region="north", value=1.5)
    run(url, "INSERT INTO extras (quote_reference, quote_option, notes) VALUES ('A-1', 1, 'n')")
    changed = table("policy", column("region", "int"), rows="many")

    made = prepare_store(url, form(changed), project=PROJECT)

    assert kinds(made) == [("rebuild table", "policy"), ("drop table", "extras")]
    assert made.dropped == ["policy", "extras"]
    assert "extras" not in [row[0] for row in sql(url, "SELECT name FROM sqlite_master")]
    assert sql(url, "SELECT * FROM policy") == []

    # A populated column retyped is built again, its values gone, and one removed dropped.
    prepare_store(
        url,
        form(table("policy", column("region", "int"), column("x"), rows="many")),
        project=PROJECT,
    )
    run(
        url,
        "INSERT INTO policy (quote_reference, quote_option, quote_row, x) "
        "VALUES ('A-1', 1, 1, 'v')",
    )
    made = prepare_store(
        url, form(table("policy", column("x", "int"), rows="many")), project=PROJECT
    )
    assert kinds(made) == [("rebuild column", "policy.x"), ("drop column", "policy.region")]
    # Only x held a value, so only x is named.
    assert made.dropped == []
    assert made.cleared == ["policy.x"]


def test_dev_renames_and_retypes_a_populated_column_in_one_release(url: str) -> None:
    prepare_store(url, form(POLICY), project=PROJECT)
    quote(url, region="north", value=1.5)

    # The column holding a value renamed and made a whole number at once: built again.
    made = prepare_store(
        url,
        form(table("policy", column("area", "int", id_="c_region"), column("value", "float"))),
        project=PROJECT,
    )

    assert ("rebuild column", "policy.area") in kinds(made)
    assert made.cleared == ["policy.area"]
    assert columns(url, "policy") == ["quote_reference", "quote_option", "value", "area"]
    assert sql(url, "SELECT value, area FROM policy") == [(1.5, None)]


def test_a_dev_store_of_an_earlier_format_is_built_afresh(url: str) -> None:
    prepare_store(url, form(POLICY), project=PROJECT)
    run(url, "UPDATE haute_store SET schema = replace(schema, '\"format\": 2', '\"format\": 1')")

    made = prepare_store(url, form(POLICY), project=PROJECT)

    assert kinds(made)[0][0] == "rebuild store"
    open_book(url, form(POLICY), project=PROJECT, haute_version="1").close()


# ── The SQL for a hosted store ──────────────────────────────────────────


def test_the_sql_from_none_makes_every_table_and_the_activity_and_no_record(url: str) -> None:
    release = form(POLICY, table("vehicles", column("reg"), rows="many"))

    written = script(None, release)

    for name in ("quote_details", "policy", "vehicles", "haute_activity"):
        assert f"CREATE TABLE {name}" in written
    assert "haute_store" not in written
    # Run, its tables fit the release's form: keys and references are as the app's check reads.
    script_run(url, written)
    assert "haute_store" not in [row[0] for row in sql(url, "SELECT name FROM sqlite_master")]
    open_book(url, release, project=PROJECT, environment=TEST, haute_version="1").close()


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
    open_book(url, release, project=PROJECT, environment=TEST, haute_version="1").close()


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
    assert "which SQLite cannot do in place" in sqlite
    assert "ALTER COLUMN" not in sqlite


def test_a_removed_table_or_column_is_a_drop_left_commented_out() -> None:
    base = form(POLICY, table("extras", column("notes")))

    written = script(base, form(table("policy", column("region"))), "postgresql")

    assert "-- The form no longer has extras; drop it once nothing reads it:" in written
    assert "-- DROP TABLE extras;" in written
    assert "-- ALTER TABLE policy DROP COLUMN value;" in written
    assert [
        line for line in written.splitlines() if "DROP" in line and not line.startswith("--")
    ] == []


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
    with open_book(url, renamed, project=PROJECT, environment=TEST, haute_version="1") as book:
        assert book.read("A-1", 1).values["t_policy"][0]["c_region"] == "north"


def test_a_form_the_tables_already_fit_has_nothing_to_change() -> None:
    assert script(form(POLICY), form(POLICY)) == (
        "-- The tables already fit the form: nothing to change.\n"
    )


def test_dev_s_plan_is_never_written_as_sql(url: str) -> None:
    made = migrate(url, POLICY)

    with pytest.raises(ValueError, match="Only a script is written as SQL"):
        render_sql(made, form(POLICY), "sqlite")
