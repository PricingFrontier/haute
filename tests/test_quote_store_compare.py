"""The schema pane's Compare (specs/workbench): a database's own catalogue read with the quotes
holding values in each table and column, the form compared with it by the form's ids, renames
from the default branch's ids or the analyst's pairing, what the database holds beyond the form
listed, what no statement changes noted, and the SQL written from the tables as they are, which,
run on that database, leaves the app's check finding nothing, on every engine the store runs on.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import QuoteStoreEnvironmentError, StoreLocation, open_book, render_sql
from haute._quote_store._catalogue import Catalogue, read_catalogue
from haute._quote_store._compare import Comparison, Rename, compare, read_store_catalogue
from haute._quote_store._migrate import PROVIDER_DIALECTS
from haute._quote_store._providers import PROVIDERS
from haute._quote_store._store import make_engine
from haute._workbench_form import FormSpec, with_quote_details
from tests._store_servers import Target, backends, database

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


def column(name: str, type_: str = "str", id_: str | None = None) -> dict[str, Any]:
    return {"id": id_ or f"c_{name}", "name": name, "type": type_}


def table(
    name: str, *columns: dict[str, Any], rows: str = "one", id_: str | None = None
) -> dict[str, Any]:
    return {"id": id_ or f"t_{name}", "name": name, "rows": rows, "columns": list(columns)}


RELEASED = form(table("policy", column("region"), column("drivers"), column("value", "float")))


@pytest.fixture(params=backends())
def target(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
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
            values={"t_policy": [{f"c_{name}": value for name, value in policy.items()}]},
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
    # The form renames region (by its id, as the default branch's form names it), makes drivers
    # an Integer, adds seats and drops value; the database also holds a table the form never had.
    changed = form(
        table(
            "policy",
            column("area", id_="c_region"),
            column("drivers", "int"),
            column("seats", "int"),
        )
    )
    target.run(
        "CREATE TABLE old_policy (quote_reference VARCHAR(40), quote_option INTEGER, x INTEGER)"
    )

    comparison = compared(target, changed, base=RELEASED)

    assert {
        ("t_policy", "c_region", "renamed"),
        ("t_policy", "c_drivers", "type"),
        ("t_policy", "c_seats", "missing"),
    } <= kinds(comparison)
    typed = next(each for each in comparison.differences if each.kind == "type")
    assert "1 quote holds values in it" in typed.message
    extras = {(each.table, each.column, each.quotes) for each in comparison.extras}
    assert ("policy", "value", 1) in extras
    assert ("old_policy", None, 0) in extras
    written = sql(target, changed, comparison)
    assert "The form does not have policy.value (1 quote holds values in it)" in written
    if target.backend == "sqlite":
        # SQLite changes no column's type: its note says how, and the check still finds it.
        return
    target.script(written)
    opens(target, changed)
    assert target.read("SELECT area, drivers FROM policy") == [("north", 2)]


def test_a_column_the_analyst_pairs_is_renamed_keeping_its_values(target: Target) -> None:
    made_from_sql(target, RELEASED)
    quote(target, RELEASED, "A-1", region="north")
    paired = form(
        table("policy", column("zone", id_="c_zone"), column("drivers"), column("value", "float"))
    )

    unpaired = compared(target, paired)
    assert ("t_policy", "c_zone", "missing") in kinds(unpaired)
    assert ("policy", "region", "t_policy", 1) in {
        (each.table, each.column, each.table_id, each.quotes) for each in unpaired.extras
    }

    comparison = compared(target, paired, renames=[Rename("t_policy", "c_zone", "region")])
    assert comparison.renames == (Rename("t_policy", "c_zone", "region"),)
    assert ("t_policy", "c_zone", "renamed") in kinds(comparison)
    target.script(sql(target, paired, comparison))
    opens(target, paired)
    assert target.read("SELECT zone FROM policy") == [("north",)]


def test_a_pairing_the_database_no_longer_allows_is_not_taken(target: Target) -> None:
    made_from_sql(target, RELEASED)
    paired = form(table("policy", column("zone", id_="c_zone"), column("drivers")))

    comparison = compared(target, paired, renames=[Rename("t_policy", "c_zone", "no_such")])

    assert comparison.renames == ()
    assert ("t_policy", "c_zone", "missing") in kinds(comparison)


def test_a_database_holding_no_store_takes_every_table_and_the_activity(target: Target) -> None:
    comparison = compared(target, RELEASED)

    assert (None, None, "missing") in kinds(comparison)
    assert ("t_policy", None, "missing") in kinds(comparison)
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
    assert ("t_policy", None, "null") in kinds(comparison)
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
    target = Target("sqlite", f"sqlite:///{(tmp_path / 'store.sqlite').as_posix()}")
    made_from_sql(target, RELEASED)
    target.run(
        "DROP TABLE policy",
        "CREATE TABLE policy (region TEXT, drivers TEXT, value FLOAT)",
        "CREATE TABLE unrelated (quote_reference VARCHAR(80), x INTEGER)",
    )

    comparison = compared(target, RELEASED)

    assert ("t_policy", None, "missing") not in kinds(comparison)
    assert any(
        each.table == "t_policy" and each.column is not None and each.kind == "missing"
        for each in comparison.differences
    )
    assert comparison.extras == ()
    assert "CREATE TABLE policy" not in sql(target, RELEASED, comparison)
