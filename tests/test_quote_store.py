"""The quote store's database (specs/workbench): its tables made from the SQL Haute writes, as
the people who own a database make them, opened only when its catalogue holds the form's
tables as the store reads and writes them, and refused naming each difference otherwise, in
every environment alike."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, text

from haute._quote_store import QuoteStoreMismatchError, open_book, store_url
from haute._quote_store._store import make_engine, writing
from haute._workbench_form import FormSpec, with_quote_details
from tests._store_servers import Target

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


def _hosted(url: str, spec: FormSpec) -> None:
    """*spec*'s tables made in the file at *url* from the SQL Haute writes, as the people who
    own a database make them."""
    Target("sqlite", url).make(spec)


def test_a_database_opens_from_its_catalogue_alone_in_any_environment(url: str) -> None:
    _hosted(url, form(POLICY, VEHICLES))

    # Nothing records the shape, the project or the environment: a copy of one environment's
    # tables serves another as it stands, dev's as any other's.
    assert tables(url) == {"quote_details", "policy", "vehicles", "haute_activity"}
    for environment in ("dev", "test", "prod"):
        open_book(url, form(POLICY, VEHICLES), environment=environment, haute_version="1").close()


def test_a_database_that_does_not_hold_the_forms_tables_is_not_opened(url: str) -> None:
    with pytest.raises(QuoteStoreMismatchError, match="the table quote_details is missing"):
        open_book(url, form(POLICY), environment="test", haute_version="1.0")

    _hosted(url, form(POLICY))
    with pytest.raises(QuoteStoreMismatchError, match="the table vehicles is missing"):
        open_book(url, form(POLICY, VEHICLES), environment="test", haute_version="1.0")


def _raw(url: str, *statements: str) -> None:
    """*statements* run on the file as someone working on the SQL side would run them."""
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        raw.execute("PRAGMA foreign_keys=OFF")
        for statement in statements:
            raw.execute(statement)


def _refusal(url: str, environment: str = "test") -> QuoteStoreMismatchError:
    with pytest.raises(QuoteStoreMismatchError) as refused:
        open_book(url, form(POLICY, VEHICLES), environment=environment, haute_version="1")
    return refused.value


def test_the_check_refuses_a_database_whose_catalogue_does_not_fit(url: str) -> None:
    _hosted(url, form(POLICY, VEHICLES))
    _raw(
        url,
        "ALTER TABLE vehicles DROP COLUMN value",
        "ALTER TABLE policy ADD COLUMN reviewed TEXT NOT NULL DEFAULT 'no'",
    )

    refused = _refusal(url)

    assert refused.differences == [
        "policy.reviewed is no column of the release's and takes no null, so no row of it could "
        "be saved",
        "vehicles.value is missing",
    ]
    assert str(refused).endswith(
        ". Change the test database's tables in SQL to fit this release's form: the Workbench "
        "view's schema pane writes the SQL to start from."
    )


def test_a_table_gone_retyped_or_keyed_otherwise_is_named(url: str) -> None:
    _hosted(url, form(POLICY, VEHICLES))
    _raw(
        url,
        "DROP TABLE policy",
        "DROP TABLE vehicles",
        "CREATE TABLE vehicles (quote_reference VARCHAR(40) NOT NULL, quote_option BIGINT "
        "NOT NULL, quote_row BIGINT NOT NULL, reg TEXT, value TEXT, "
        "PRIMARY KEY (quote_reference, quote_option))",
    )

    assert _refusal(url).differences == [
        "the table policy is missing",
        "vehicles.value holds string, not float",
        "vehicles is keyed by quote_reference, quote_option, not quote_reference, quote_option, "
        "quote_row",
        "vehicles does not refer to its quote's quote_details row",
    ]


def test_a_column_the_sql_side_added_that_takes_a_null_is_no_difference(url: str) -> None:
    _hosted(url, form(POLICY, VEHICLES))
    _raw(url, "ALTER TABLE policy ADD COLUMN notes TEXT")

    open_book(url, form(POLICY, VEHICLES), environment="test", haute_version="1").close()


def test_dev_s_refusal_is_any_environment_s_naming_its_database(url: str) -> None:
    _hosted(url, form(POLICY, VEHICLES))
    _raw(url, "ALTER TABLE vehicles DROP COLUMN value")

    refused = _refusal(url, environment="dev")

    assert str(refused) == (
        "This database's tables do not fit the form: vehicles.value is missing. Change the dev "
        "database's tables in SQL to fit this release's form: the Workbench view's schema pane "
        "writes the SQL to start from."
    )


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
    _hosted(url, form(VEHICLES))
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
