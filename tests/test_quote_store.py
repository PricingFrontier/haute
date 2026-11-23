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
from sqlalchemy.exc import OperationalError

from haute._quote_store import (
    QuoteStoreEnvironmentError,
    QuoteStoreMismatchError,
    open_book,
    store_url,
)
from haute._quote_store import _store as store_module
from haute._quote_store._catalogue import CatalogueMovedError
from haute._quote_store._store import RERUN_WAITS, make_engine, read_store, rerun_asked, writing
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
    return {"name": name, "type": type_, **rest}


POLICY = {"name": "policy", "rows": "one", "inputs": [column("region")]}
VEHICLES = {
    "name": "vehicles",
    "rows": "many",
    "inputs": [column("vehicle", "int", index="numbered"), column("reg"), column("value", "float")],
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


def test_a_database_that_does_not_hold_the_forms_tables_refuses_the_uses_that_need_them(
    url: str,
) -> None:
    with open_book(url, form(POLICY), environment="test", haute_version="1.0") as empty:
        listed = empty.mismatch("list")
        assert listed is not None and "the table quote_details is missing" in listed.message

    _hosted(url, form(POLICY))
    with open_book(url, form(POLICY, VEHICLES), environment="test", haute_version="1.0") as book:
        # The list reads quote_details alone, which fits; a quote reads every table.
        assert book.mismatch("list") is None
        quoted = book.mismatch("quote")
        assert quoted is not None and quoted.differences == ["the table vehicles is missing"]
        assert [(m.table, m.column, m.problem) for m in quoted.misfits] == [
            ("vehicles", None, "is missing")
        ]


def test_the_list_and_the_people_are_read_while_a_table_only_a_quote_reads_misfits(
    url: str,
) -> None:
    _hosted(url, form(POLICY, VEHICLES))
    _raw(url, "ALTER TABLE vehicles DROP COLUMN value")

    with open_book(url, form(POLICY, VEHICLES), environment="test", haute_version="1") as book:
        assert book.unfit("list") == ()
        assert book.page(actor="uw@example.com").quotes == []
        assert book.people() == []
        assert [m.text for m in book.unfit("quote")] == ["vehicles.value is missing"]


def _raw(url: str, *statements: str) -> None:
    """*statements* run on the file as someone working on the SQL side would run them."""
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        raw.execute("PRAGMA foreign_keys=OFF")
        for statement in statements:
            raw.execute(statement)


def _refusal(url: str, environment: str = "test") -> QuoteStoreMismatchError:
    """The refusal of a quote's use of the store at *url*, which must not fit."""
    with open_book(url, form(POLICY, VEHICLES), environment=environment, haute_version="1") as book:
        refused = book.mismatch("quote")
    assert refused is not None
    return refused


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
        "NOT NULL, vehicle BIGINT, reg TEXT, value TEXT, "
        "PRIMARY KEY (quote_reference, quote_option))",
    )

    # The index keys the table's rows with the identity, so it never takes a null.
    assert _refusal(url).differences == [
        "the table policy is missing",
        "vehicles.vehicle takes a null, unlike the store's",
        "vehicles.value holds string, not float",
        "vehicles is keyed by quote_reference, quote_option, not quote_reference, quote_option, "
        "vehicle",
        "vehicles does not refer by quote_reference, quote_option to quote_details "
        "(quote_reference, quote_option)",
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
                    "INSERT INTO vehicles (quote_reference, quote_option, vehicle) "
                    "VALUES ('NONE', 1, 1)"
                )
            )
    finally:
        engine.dispose()


def _ended(original: BaseException, statement: str | None = "SELECT 1") -> OperationalError:
    """*original*, a driver's error, as SQLAlchemy raises it from *statement*."""
    return OperationalError(statement, {}, original)


def _deadlock_victim() -> OperationalError:
    """SQL Server ending a catalogue read as a deadlock's victim, as mssql-python raises it."""
    from mssql_python.exceptions import OperationalError as DriverOperationalError

    return _ended(
        DriverOperationalError(
            driver_error="Serialization failure",
            ddbc_error="Transaction (Process ID 74) was deadlocked on lock resources with "
            "another process and has been chosen as the deadlock victim. Rerun the transaction.",
        )
    )


def test_a_transaction_the_database_asks_to_run_again_is_told_from_other_errors() -> None:
    import psycopg.errors
    from mssql_python.exceptions import ProgrammingError as DriverProgrammingError

    assert rerun_asked(_deadlock_victim())
    assert rerun_asked(_ended(psycopg.errors.DeadlockDetected("deadlock detected")))
    assert rerun_asked(_ended(psycopg.errors.SerializationFailure("could not serialize")))
    assert not rerun_asked(
        _ended(DriverProgrammingError(driver_error="Column not found", ddbc_error="no column"))
    )
    assert not rerun_asked(_ended(psycopg.errors.UndefinedTable("no such table")))
    # Refused before any statement, it is a database not reached, never run again.
    assert not rerun_asked(_ended(psycopg.errors.SerializationFailure("x"), statement=None))
    assert not rerun_asked(ValueError("not the database's"))


def test_a_read_the_database_ends_as_a_deadlock_s_victim_is_run_again(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    waited: list[float] = []
    monkeypatch.setattr(store_module.time, "sleep", waited.append)
    runs: list[Any] = []

    def read(connection: Any) -> Any:
        runs.append(connection)
        if len(runs) < 3:
            raise _deadlock_victim()
        return connection.exec_driver_sql("SELECT 1").scalar()

    engine = make_engine(url)
    try:
        assert read_store(engine, read) == 1
    finally:
        engine.dispose()

    assert len(runs) == 3
    assert waited == list(RERUN_WAITS[:2])


def test_a_read_ended_every_time_or_refused_otherwise_raises(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)
    runs: list[str] = []

    def always_a_victim(connection: Any) -> None:
        runs.append("victim")
        raise _deadlock_victim()

    def refused(connection: Any) -> None:
        runs.append("refused")
        raise _ended(ValueError("permission denied"))

    engine = make_engine(url)
    try:
        # Ended every time, the tables are still moving: never the database's refusal.
        with pytest.raises(CatalogueMovedError, match="changed while they were read"):
            read_store(engine, always_a_victim)
        assert runs == ["victim"] * (len(RERUN_WAITS) + 1)
        runs.clear()
        with pytest.raises(OperationalError, match="permission denied"):
            read_store(engine, refused)
        assert runs == ["refused"]
    finally:
        engine.dispose()


def test_the_check_and_the_schema_pane_run_a_read_again_when_the_database_asks(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._quote_store import _compare as compare_module
    from haute._quote_store._compare import read_store_catalogue
    from haute._quote_store._environment import StoreLocation

    _hosted(url, form(POLICY, VEHICLES))
    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)
    real = store_module.read_catalogue
    ended: list[str] = []

    def once_a_victim(*args: Any, **kwargs: Any) -> Any:
        if len(ended) % 2 == 0:
            ended.append("victim")
            raise _deadlock_victim()
        ended.append("read")
        return real(*args, **kwargs)

    monkeypatch.setattr(store_module, "read_catalogue", once_a_victim)
    monkeypatch.setattr(compare_module, "read_catalogue", once_a_victim)

    open_book(url, form(POLICY, VEHICLES), environment="test", haute_version="1").close()
    assert {table.name for table in read_store_catalogue(StoreLocation("test", url)).tables} >= {
        "policy",
        "vehicles",
    }
    assert ended == ["victim", "read", "victim", "read"]


def test_a_count_the_catalogue_moved_under_is_read_again(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._quote_store import _catalogue as catalogue_module
    from haute._quote_store._compare import read_store_catalogue
    from haute._quote_store._environment import StoreLocation

    _hosted(url, form(POLICY, VEHICLES))
    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)
    real = catalogue_module._counted
    counted: list[int] = []

    def moved_once(*args: Any) -> Any:
        counted.append(len(counted))
        if len(counted) == 1:
            # The owners drop a column between the catalogue's read and the count.
            _raw(url, "ALTER TABLE vehicles DROP COLUMN reg")
        return real(*args)

    monkeypatch.setattr(catalogue_module, "_counted", moved_once)

    read = read_store_catalogue(StoreLocation("test", url))

    vehicles = read.table("vehicles")
    assert vehicles is not None and vehicles.column("reg") is None
    assert len(counted) == 2


def test_a_count_a_column_dropped_and_added_back_under_is_read_again(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._quote_store import _catalogue as catalogue_module
    from haute._quote_store._compare import read_store_catalogue
    from haute._quote_store._environment import StoreLocation

    _hosted(url, form(POLICY, VEHICLES))
    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)
    real = catalogue_module._counted
    counted: list[int] = []

    def dropped_and_back(*args: Any) -> Any:
        counted.append(len(counted))
        if len(counted) > 1:
            return real(*args)
        # The table's last column, gone as the count runs and back in its place by the time
        # anything looks again.
        _raw(url, "ALTER TABLE vehicles DROP COLUMN value")
        try:
            return real(*args)
        finally:
            _raw(url, "ALTER TABLE vehicles ADD COLUMN value FLOAT")

    monkeypatch.setattr(catalogue_module, "_counted", dropped_and_back)

    read = read_store_catalogue(StoreLocation("test", url))

    vehicles = read.table("vehicles")
    assert vehicles is not None and vehicles.columns[-1].name == "value"
    assert len(counted) == 2


def test_a_count_refused_otherwise_is_not_read_again_whatever_moved_beside_it(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._quote_store import _catalogue as catalogue_module
    from haute._quote_store._compare import read_store_catalogue
    from haute._quote_store._environment import StoreLocation

    _hosted(url, form(POLICY, VEHICLES))
    counted: list[int] = []

    def refused(*args: Any) -> Any:
        counted.append(1)
        # An owner adds an unrelated column as the count is refused a permission.
        _raw(url, "ALTER TABLE vehicles ADD COLUMN unrelated TEXT")
        raise _ended(ValueError("permission denied"))

    monkeypatch.setattr(catalogue_module, "_counted", refused)

    with pytest.raises(QuoteStoreEnvironmentError, match="tables could not be read"):
        read_store_catalogue(StoreLocation("test", url))
    assert counted == [1]


def test_a_name_the_database_no_longer_holds_is_told_from_other_refusals() -> None:
    import psycopg.errors
    from mssql_python.exceptions import ProgrammingError as DriverProgrammingError

    from haute._quote_store._catalogue import names_missing

    def sqlite_refusal(query: str) -> OperationalError:
        with sqlite3.connect(":memory:") as raw:
            raw.execute("CREATE TABLE t (a)")
            try:
                raw.execute(query)
            except sqlite3.OperationalError as exc:
                return _ended(exc)
        raise AssertionError(query)

    assert names_missing(_ended(psycopg.errors.UndefinedColumn("column x does not exist")))
    assert names_missing(_ended(psycopg.errors.UndefinedTable("relation x does not exist")))
    for missing in ("Column not found", "Base table or view not found"):
        assert names_missing(_ended(DriverProgrammingError(driver_error=missing, ddbc_error="")))
    assert names_missing(sqlite_refusal("SELECT b FROM t"))
    assert names_missing(sqlite_refusal("SELECT a FROM u"))
    assert not names_missing(_ended(psycopg.errors.InsufficientPrivilege("permission denied")))
    assert not names_missing(
        _ended(
            DriverProgrammingError(driver_error="Syntax error or access violation", ddbc_error="")
        )
    )
    assert not names_missing(sqlite_refusal("SELEC a FROM t"))
