"""The quote store's database (specs/workbench): made to hold the form's tables, built again
in dev when the form changes, refused when it holds another form's, another project's or an
earlier Haute's store, and opened only when it holds exactly the form's tables."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, text

from haute._quote_store import (
    QuoteStoreMismatchError,
    open_book,
    prepare_store,
    store_url,
)
from haute._quote_store._store import make_engine, writing
from haute._workbench_form import FormSpec, with_quote_details

PROJECT = "motor"


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
    return {"id": f"c_{name}", "name": name, "type": type_, **rest}


POLICY = {"id": "t_policy", "name": "policy", "rows": "one", "columns": [column("region")]}
VEHICLES = {
    "id": "t_vehicles",
    "name": "vehicles",
    "rows": "many",
    "columns": [column("reg", key=True), column("value", "float")],
}


@pytest.fixture
def url(tmp_path: Path) -> str:
    return store_url(tmp_path / "store.sqlite")


def tables(url: str) -> set[str]:
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def rows(url: str, table: str) -> list[tuple[Any, ...]]:
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(f'SELECT * FROM "{table}"'))]
    finally:
        engine.dispose()


def add_quote(url: str, *, vehicles: int = 0) -> None:
    """A quote's rows written straight into the tables, as a report would see them."""
    engine = make_engine(url)
    try:
        with writing(engine) as connection:
            connection.execute(
                text(
                    "INSERT INTO quote_details (quote_reference, quote_option, stage, assignee, "
                    "archived, created_date, created_by, modified_date, modified_by, "
                    "quote_version, saved_at, saved_by) VALUES ('A-1', 1, 'draft', 'ana', 0, "
                    "'2026-10-30', 'ana', '2026-10-30', 'ana', 1, '2026-10-30 09:00:00', 'ana')"
                )
            )
            for number in range(1, vehicles + 1):
                connection.execute(
                    text(
                        "INSERT INTO vehicles (quote_reference, quote_option, quote_row, reg) "
                        f"VALUES ('A-1', 1, {number}, 'R{number}')"
                    )
                )
    finally:
        engine.dispose()


def test_an_empty_database_takes_the_forms_tables_and_haute_s_two(url: str) -> None:
    assert prepare_store(url, form(POLICY, VEHICLES), project=PROJECT) == []

    assert tables(url) == {"quote_details", "policy", "vehicles", "haute_store", "haute_activity"}
    [record] = rows(url, "haute_store")
    assert record[1:3] == (PROJECT, "dev")
    # Opened for the same form, project and environment, it is the book's.
    open_book(url, form(POLICY, VEHICLES), project=PROJECT, haute_version="1.0").close()


def test_preparing_again_for_the_same_form_changes_nothing(url: str) -> None:
    prepare_store(url, form(POLICY, VEHICLES), project=PROJECT)
    add_quote(url, vehicles=2)

    assert prepare_store(url, form(POLICY, VEHICLES), project=PROJECT) == []
    assert len(rows(url, "vehicles")) == 2


def test_dev_builds_again_each_table_the_form_changed_naming_those_whose_rows_it_dropped(
    url: str,
) -> None:
    prepare_store(url, form(POLICY, VEHICLES), project=PROJECT)
    add_quote(url, vehicles=2)
    seats = {**VEHICLES, "columns": [*VEHICLES["columns"], column("seats", "int")]}

    dropped = prepare_store(url, form(POLICY, seats), project=PROJECT)

    assert dropped == ["vehicles"]
    assert rows(url, "vehicles") == []
    # The quote itself stays: only the table that changed was built again.
    assert len(rows(url, "quote_details")) == 1
    columns = make_engine(url)
    try:
        with columns.connect() as connection:
            names = [c["name"] for c in inspect(connection).get_columns("vehicles")]
    finally:
        columns.dispose()
    assert "seats" in names


def test_dev_drops_a_table_the_form_no_longer_has(url: str) -> None:
    prepare_store(url, form(POLICY, VEHICLES), project=PROJECT)
    add_quote(url, vehicles=1)

    assert prepare_store(url, form(POLICY), project=PROJECT) == ["vehicles"]
    assert "vehicles" not in tables(url)


def test_outside_dev_a_database_of_another_forms_tables_is_refused_naming_them(
    url: str,
) -> None:
    prepare_store(url, form(POLICY, VEHICLES), project=PROJECT, environment="test")
    add_quote(url, vehicles=1)

    with pytest.raises(QuoteStoreMismatchError, match="vehicles has changed") as refused:
        prepare_store(
            url, form(POLICY, {**VEHICLES, "rows": "one"}), project=PROJECT, environment="test"
        )

    assert refused.value.differences == ["vehicles has changed"]
    # Nothing was dropped or built again.
    assert len(rows(url, "vehicles")) == 1


def test_a_database_that_does_not_hold_the_forms_tables_is_not_opened(url: str) -> None:
    with pytest.raises(QuoteStoreMismatchError, match="holds no quote store yet"):
        open_book(url, form(POLICY), project=PROJECT, haute_version="1.0")

    prepare_store(url, form(POLICY, VEHICLES), project=PROJECT)
    with pytest.raises(QuoteStoreMismatchError, match="policy is no longer the form's"):
        open_book(url, form(VEHICLES), project=PROJECT, haute_version="1.0")


@pytest.mark.parametrize(
    ("project", "environment"), [("home", "dev"), (PROJECT, "prod")], ids=["project", "environment"]
)
def test_a_database_recorded_for_another_project_or_environment_is_refused_naming_both(
    url: str, project: str, environment: str
) -> None:
    prepare_store(url, form(POLICY), project=PROJECT)

    with pytest.raises(
        QuoteStoreMismatchError, match=f"of 'motor' in 'dev', not of '{project}' in '{environment}'"
    ):
        open_book(url, form(POLICY), project=project, environment=environment, haute_version="1")
    with pytest.raises(QuoteStoreMismatchError):
        prepare_store(url, form(POLICY), project=project, environment=environment)


def test_tables_of_the_forms_names_the_store_did_not_make_are_never_adopted(url: str) -> None:
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        raw.execute("CREATE TABLE policy (region TEXT)")

    with pytest.raises(QuoteStoreMismatchError, match=r"did not make \(policy\)"):
        prepare_store(url, form(POLICY), project=PROJECT)
    assert tables(url) == {"policy"}


def test_an_earlier_haute_s_store_is_refused_naming_its_tables(url: str) -> None:
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        raw.execute("CREATE TABLE haute_quote_store_version (version_num TEXT)")
        raw.execute("CREATE TABLE haute_quotes (id TEXT)")

    for attempt in (
        lambda: prepare_store(url, form(POLICY), project=PROJECT),
        lambda: open_book(url, form(POLICY), project=PROJECT, haute_version="1"),
    ):
        with pytest.raises(
            QuoteStoreMismatchError, match=r"\(haute_quote_store_version, haute_quotes\)"
        ):
            attempt()


def test_ddl_in_a_write_that_fails_is_rolled_back_whole(url: str) -> None:
    engine = make_engine(url)
    try:
        with pytest.raises(RuntimeError), writing(engine) as connection:
            connection.execute(text("CREATE TABLE half_made (x INTEGER)"))
            raise RuntimeError("the write fails part way")
    finally:
        engine.dispose()

    assert "half_made" not in tables(url)


def test_foreign_keys_are_held_on_every_connection(url: str) -> None:
    prepare_store(url, form(VEHICLES), project=PROJECT)
    engine = make_engine(url)
    try:
        with pytest.raises(Exception, match="FOREIGN KEY"), writing(engine) as connection:
            connection.execute(
                text(
                    "INSERT INTO vehicles (quote_reference, quote_option, quote_row) "
                    "VALUES ('NONE', 1, 1)"
                )
            )
    finally:
        engine.dispose()
