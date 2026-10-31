"""The quote store's schema changes (specs/workbench): one planner bringing a database's tables to
a release's form by stable id; in a hosted environment nothing is dropped and nothing holding a
value is changed, a removed table or column kept and listed with its DROP; in dev every change
is made, what goes named; each step says what reverses it, and a plan only a restore reverses
needs a restore point."""

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
    plan_store,
    prepare_store,
    render_sql,
    render_text,
    store_url,
)
from haute._quote_store._schema import live, structure
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


def migrate(url: str, *tables: dict[str, Any], restore_point: str | None = None) -> Any:
    return prepare_store(
        url, form(*tables), project=PROJECT, environment=TEST, restore_point=restore_point
    )


def kinds(made: Any) -> list[tuple[str, str]]:
    return [
        (step.kind, step.table if step.column is None else f"{step.table}.{step.column}")
        for step in made.steps
    ]


# ── The plan ────────────────────────────────────────────────────────────


def test_a_plan_names_each_step_by_stable_id_with_what_reverses_it() -> None:
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

    made = _migrate.plan(after, before, {("t_extras", None): 0}, "hosted")

    assert [
        (step.kind, step.table, step.column, step.was, step.rollback) for step in made.steps
    ] == [
        ("rename table", "policy_info", None, "policy", "a migrate back"),
        ("rename column", "policy_info", "area", "region", "a migrate back"),
        ("add column", "policy_info", "seats", None, "a migrate back"),
        ("create table", "drivers", None, None, "a migrate back"),
        ("keep table", "extras", None, None, "nothing"),
    ]
    assert made.refusals == () and not made.provisional
    # What haute_store records after it: the form's tables, and the one kept beside them.
    assert [(each["name"], each["kept"]) for each in made.recorded["tables"]] == [
        ("quote_details", False),
        ("policy_info", False),
        ("drivers", False),
        ("extras", True),
    ]
    assert live(made.recorded) == after


def test_an_empty_database_takes_every_table_and_a_second_migrate_changes_nothing(
    url: str,
) -> None:
    made = migrate(url, POLICY)

    assert kinds(made) == [("create table", "quote_details"), ("create table", "policy")]
    assert migrate(url, POLICY).steps == ()


# ── Renames ─────────────────────────────────────────────────────────────


def test_a_change_of_case_and_a_swap_of_two_names_keep_every_value(url: str) -> None:
    migrate(url, table("policy", column("left", id_="c_a"), column("right", id_="c_b")))
    quote(url, left="L", right="R")

    migrate(
        url, table("Policy", column("right", id_="c_a"), column("left", id_="c_b"), id_="t_policy")
    )

    assert columns(url, "Policy")[2:] == ["right", "left"]
    assert sql(url, 'SELECT "right", "left" FROM "Policy"') == [("L", "R")]


def test_a_rename_migrated_back_restores_the_previous_releases_names(url: str) -> None:
    migrate(url, POLICY)
    quote(url, region="north")
    renamed = table("policy", column("area", id_="c_region"), column("value", "float"))

    migrate(url, renamed)
    back = migrate(url, POLICY)

    assert kinds(back) == [("rename column", "policy.region")]
    assert sql(url, "SELECT region FROM policy") == [("north",)]


# ── What a hosted environment keeps ─────────────────────────────────────


def test_a_removed_column_is_kept_listed_with_its_quotes_and_its_drop(url: str) -> None:
    migrate(url, POLICY)
    quote(url, region="north", value=1.5)
    quote(url, "B-1", region="south")

    made = migrate(url, table("policy", column("region")))

    assert kinds(made) == [("keep column", "policy.value")]
    [kept] = made.steps
    assert (kept.quotes, kept.rollback) == (1, "nothing")
    text = render_text(made)
    assert "keep column policy.value, 1 quote holding values" in text
    assert "ALTER TABLE policy DROP COLUMN value;" in text
    # The column stays, unused, its values with it.
    assert "value" in columns(url, "policy")
    assert sql(url, "SELECT value FROM policy WHERE quote_reference = 'A-1'") == [(1.5,)]


def test_a_kept_column_is_left_null_by_a_later_save_and_brought_back_as_it_stood(
    url: str,
) -> None:
    migrate(url, POLICY)
    quote(url, region="north", value=1.5)
    quote(url, "B-1", region="south", value=2.5)
    narrower = form(table("policy", column("region")))
    migrate(url, table("policy", column("region")))

    with open_book(url, narrower, project=PROJECT, environment=TEST, haute_version="1") as book:
        saved = book.get("B-1", 1)
        book.save(
            "B-1",
            1,
            operation="operation-000001",
            version=saved.version,
            actor="ana",
            values={"t_policy": [{"c_region": "west"}]},
            priced=None,
            at=book.now(),
        )
    assert sql(url, "SELECT quote_reference, value FROM policy ORDER BY quote_reference") == [
        ("A-1", 1.5),
        ("B-1", None),
    ]

    back = migrate(url, POLICY)

    assert kinds(back) == [("bring back column", "policy.value")]
    assert sql(url, "SELECT value FROM policy WHERE quote_reference = 'A-1'") == [(1.5,)]


def test_a_removed_table_is_kept_with_every_quotes_rows_and_brought_back(url: str) -> None:
    extras = table("extras", column("notes"))
    migrate(url, POLICY, extras)
    quote(url, region="north")
    run(url, "INSERT INTO extras (quote_reference, quote_option, notes) VALUES ('A-1', 1, 'n')")

    kept = migrate(url, POLICY)
    assert kinds(kept) == [("keep table", "extras")]
    assert "DROP TABLE extras;" in render_text(kept)
    # A later migrate keeps listing it.
    assert kinds(migrate(url, POLICY)) == [("keep table", "extras")]

    back = migrate(url, POLICY, extras)

    assert kinds(back) == [("bring back table", "extras")]
    assert sql(url, "SELECT notes FROM extras") == [("n",)]


def test_a_kept_table_brought_back_counts_its_columns_before_retyping_one(url: str) -> None:
    extras = table("extras", column("notes"))
    migrate(url, POLICY, extras)
    quote(url, region="north")
    run(url, "INSERT INTO extras (quote_reference, quote_option, notes) VALUES ('A-1', 1, 'n')")
    migrate(url, POLICY)

    # Back under its id, its populated column a whole number now: refused, the value kept.
    with pytest.raises(MigrationRefusedError, match="extras.notes holds values in 1 quote"):
        migrate(url, POLICY, table("extras", column("notes", "int")), restore_point="backup-1")
    assert sql(url, "SELECT notes FROM extras") == [("n",)]


def test_a_new_column_or_table_taking_a_kept_ones_name_is_refused(url: str) -> None:
    migrate(url, POLICY, table("extras", column("notes")))
    migrate(url, table("policy", column("region")))

    for tables, named in (
        (
            (table("policy", column("region"), column("value", "float", id_="c_other")),),
            "column policy.value",
        ),
        ((POLICY, table("extras", column("x"), id_="t_other")), "table extras"),
    ):
        with pytest.raises(MigrationRefusedError, match=f"new {named}") as refused:
            migrate(url, *tables)
        assert "takes the name of" in refused.value.refusals[0]


def test_a_rename_onto_a_name_a_kept_column_or_table_holds_is_refused(url: str) -> None:
    migrate(url, POLICY, table("extras", column("notes")))

    # region renamed to value while the form removes value, which the store keeps under its
    # name: the rename could never be made.
    onto_column = form(table("policy", column("value", id_="c_region")))
    with pytest.raises(MigrationRefusedError, match="policy.region, renamed value, takes the name"):
        migrate(url, table("policy", column("value", id_="c_region")))
    assert plan_store(url, onto_column, project=PROJECT, environment=TEST).refusals
    # policy renamed to extras while the form removes extras, kept the same way.
    with pytest.raises(MigrationRefusedError, match="the table policy, renamed extras, takes"):
        migrate(url, {**POLICY, "name": "extras"})
    assert columns(url, "policy") == ["quote_reference", "quote_option", "region", "value"]


def test_a_kept_column_the_sql_side_dropped_is_forgotten_and_its_name_free(url: str) -> None:
    migrate(url, POLICY)
    migrate(url, table("policy", column("region")))
    run(url, "ALTER TABLE policy DROP COLUMN value")

    made = migrate(url, table("policy", column("region"), column("value", "int", id_="c_new")))

    assert kinds(made) == [("add column", "policy.value")]


def test_a_column_the_form_has_that_the_sql_side_dropped_is_refused_naming_it(url: str) -> None:
    migrate(url, POLICY)
    run(url, "ALTER TABLE policy DROP COLUMN value")

    with pytest.raises(MigrationRefusedError, match="policy.value, which the form has, is missing"):
        migrate(url, POLICY)


# ── Changes of what holds no value ──────────────────────────────────────


def test_a_retype_of_an_empty_column_is_made_and_of_a_populated_one_refused(url: str) -> None:
    migrate(url, POLICY)
    quote(url, region="north")
    retyped = table("policy", column("region"), column("value", "int"))

    made = migrate(url, retyped, restore_point="backup-2026-10-30")

    assert kinds(made) == [("retype column", "policy.value")]
    assert made.steps[0].rollback == "a restore"
    assert sql(url, "SELECT type FROM pragma_table_info('policy') WHERE name = 'value'") == [
        ("BIGINT",)
    ]
    assert sql(url, "SELECT region FROM policy") == [("north",)]
    run(url, "UPDATE policy SET value = 3")
    with pytest.raises(MigrationRefusedError, match="policy.value holds values in 1 quote"):
        migrate(url, POLICY, restore_point="backup-2026-10-31")


def test_a_one_row_table_made_many_row_is_made_empty_and_refused_populated(url: str) -> None:
    migrate(url, POLICY)
    many = table("policy", column("region"), column("value", "float"), rows="many")

    made = migrate(url, many, restore_point="backup")
    assert kinds(made) == [("reshape table", "policy")]
    assert "quote_row" in columns(url, "policy")

    quote(url)
    run(url, "INSERT INTO policy (quote_reference, quote_option, quote_row) VALUES ('A-1', 1, 1)")
    with pytest.raises(MigrationRefusedError, match="policy holds rows of 1 quote"):
        migrate(url, POLICY, restore_point="backup")


def test_a_plan_only_a_restore_reverses_needs_a_restore_point_which_is_recorded(url: str) -> None:
    migrate(url, POLICY)
    retyped = table("policy", column("region"), column("value", "int"))

    with pytest.raises(MigrationRefusedError, match="--restore-point NAME") as refused:
        migrate(url, retyped)
    assert refused.value.refusals == ["retype column policy.value"]
    assert "value" in columns(url, "policy")

    migrate(url, retyped, restore_point="pitr-2026-10-30T09:00Z")
    assert sql(url, "SELECT restore_point FROM haute_store") == [("pitr-2026-10-30T09:00Z",)]


def test_a_migrate_failing_part_way_leaves_nothing_changed(
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


# ── Dev ─────────────────────────────────────────────────────────────────


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


def test_a_store_of_an_earlier_format_is_built_afresh_in_dev_and_refused_hosted(url: str) -> None:
    prepare_store(url, form(POLICY), project=PROJECT)
    run(url, "UPDATE haute_store SET schema = replace(schema, '\"format\": 2', '\"format\": 1')")

    made = prepare_store(url, form(POLICY), project=PROJECT)

    assert kinds(made)[0][0] == "rebuild store"
    open_book(url, form(POLICY), project=PROJECT, haute_version="1").close()

    migrate(url.replace("store.sqlite", "hosted.sqlite"), POLICY)
    hosted = url.replace("store.sqlite", "hosted.sqlite")
    run(hosted, "UPDATE haute_store SET schema = replace(schema, '\"format\": 2', '\"format\": 1')")
    with pytest.raises(MigrationRefusedError, match="keeps other columns of its own"):
        migrate(hosted, POLICY)


# ── Offline ─────────────────────────────────────────────────────────────


def test_an_offline_plan_is_provisional_each_step_on_the_data_conditional() -> None:
    base = form(POLICY, table("extras", column("notes")))
    release = form(table("policy", column("region"), column("value", "int")))

    made = offline_plan(base, release)

    assert made.provisional
    assert [(step.kind, step.conditional) for step in made.steps] == [
        ("retype column", True),
        ("keep table", False),
    ]
    text = render_text(made)
    assert text.startswith("Provisional plan")
    assert "retype column policy.value if it holds no value, else refused" in text
    assert "DROP TABLE extras;" in text


def _script(made: Any, release_form: FormSpec, dialect: str = "sqlite") -> str:
    return render_sql(
        made,
        release_form,
        dialect,
        project=PROJECT,
        environment=TEST,
        release="r2",
        haute_version="1",
        restore_point=None,
    )


def test_a_plan_is_printed_as_one_transaction_a_dba_runs_whole_or_not_at_all(url: str) -> None:
    migrate(url, POLICY)
    quote(url, region="north", value=1.5)
    renamed = form(table("policy", column("area", id_="c_region"), column("value", "float")))
    script = _script(plan_store(url, renamed, project=PROJECT, environment=TEST), renamed)

    # Every value written in, the whole of it one transaction.
    assert script.startswith("-- Run with sqlite3 -bail")
    assert "BEGIN;" in script and script.rstrip().endswith("COMMIT;")
    assert "?" not in script and "%(" not in script
    assert "UPDATE haute_store SET release='r2'" in script

    # A statement failing before the COMMIT leaves every step undone.
    failing = script.replace("COMMIT;", "SELECT * FROM haute_no_such_table;\nCOMMIT;")
    raw = sqlite3.connect(url.removeprefix("sqlite:///"))
    try:
        with pytest.raises(sqlite3.OperationalError, match="haute_no_such_table"):
            raw.executescript(failing)
    finally:
        raw.close()
    assert columns(url, "policy") == ["quote_reference", "quote_option", "region", "value"]

    # Run whole, the store is the release's: the app opens it, the quote's values kept.
    raw = sqlite3.connect(url.removeprefix("sqlite:///"))
    try:
        raw.executescript(script)
    finally:
        raw.close()
    with open_book(url, renamed, project=PROJECT, environment=TEST, haute_version="1") as book:
        assert book.read("A-1", 1).values["t_policy"][0]["c_region"] == "north"


def test_a_plan_a_script_could_not_apply_safely_is_never_printed_as_sql(url: str) -> None:
    migrate(url, POLICY)
    # A retype is made only while the column is empty, which only a migrate checks.
    retyped = form(table("policy", column("region"), column("value", "int")))
    with pytest.raises(
        MigrationRefusedError, match="retype column policy.value. Apply them with haute app migrate"
    ):
        _script(plan_store(url, retyped, project=PROJECT, environment=TEST), retyped)
    # Offline, every such step is conditional: refused alike.
    with pytest.raises(MigrationRefusedError, match="retype column policy.value"):
        _script(offline_plan(form(POLICY), retyped), retyped, "postgresql")
    # A plan refusing a step has no SQL at all.
    quote(url, region="north", value=1.5)
    populated = plan_store(url, retyped, project=PROJECT, environment=TEST)
    assert populated.refusals
    with pytest.raises(MigrationRefusedError, match="The plan refuses steps, so it has no SQL"):
        _script(populated, retyped)
    # Nothing was applied.
    assert "haute_tmp" not in " ".join(columns(url, "policy"))
