"""The workbench's quote store (specs/workbench): one schema on a SQLite file, brought to it by
Haute's migrations; quotes and their numbered revisions, each with the form it was keyed into;
and a saved quote's values carried onto the current form."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from haute._quote_store import (
    QuoteNotFoundError,
    QuoteStore,
    QuoteStoreError,
    QuoteStoreNotMigratedError,
    StaleQuoteError,
    carry_values,
    migrate_store,
    open_store,
    store_url,
)
from haute._quote_store._store import _MIGRATIONS, _alembic_config
from haute._quote_store._tables import METADATA, VERSION_TABLE
from haute._workbench_form import FormSpec


def _form(*tables: dict[str, Any]) -> FormSpec:
    return FormSpec.model_validate(
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


def _table(id_: str, name: str, *columns: tuple[str, str], role: str = "input") -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "role": role,
        "rows": "one",
        "columns": [{"id": cid, "name": cname, "type": "str"} for cid, cname in columns],
    }


FORM = _form(_table("t_policy", "policy", ("c_region", "region"), ("c_cover", "cover")))
VALUES = {"t_policy": [{"c_region": "north", "c_cover": "full"}]}
REQUEST = {"policy": {"region": "north", "cover": "full"}}
RESPONSE = {"pricing": {"premium": 512.4}}


class Clock:
    """A clock moving a second on each reading, so saves are ordered."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def url(tmp_path: Path) -> str:
    return store_url(tmp_path / "quotes.sqlite")


@pytest.fixture
def store(url: str) -> Iterator[QuoteStore]:
    migrate_store(url)
    with open_store(url, clock=Clock()) as opened:
        yield opened


def _create(store: QuoteStore, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "name": "Northern fleet",
        "owner": "ana@example.com",
        "saved_by": "ana@example.com",
        "form": FORM,
        "inputs": VALUES,
        "request": REQUEST,
        "response": RESPONSE,
        "release": None,
    }
    return store.create(**{**fields, **overrides})


# ── The schema and its migrations ─────────────────────────────────────────


def test_a_new_file_is_migrated_to_the_schema_and_a_second_migration_changes_nothing(
    url: str,
) -> None:
    migrate_store(url)
    migrate_store(url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
            assert compare_metadata(context, METADATA) == []
            assert context.get_current_revision() == "0004"
    finally:
        engine.dispose()


def test_a_file_at_the_second_schema_takes_is_renewal_null_its_rows_kept_and_its_oldest_reader(
    url: str,
) -> None:
    # A database the second schema's Haute wrote, holding one app quote's details.
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            command.upgrade(_alembic_config(connection), "0002")
            now = datetime(2026, 10, 12, 9, 0)
            connection.execute(
                text("INSERT INTO haute_quote_forms VALUES ('f1', '{}')"),
            )
            connection.execute(
                text("INSERT INTO haute_quotes VALUES ('q1', 'Q-000001', 'ana', :now, 1)"),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO haute_quote_revisions VALUES "
                    "('q1', 1, 'f1', '{}', '{}', NULL, NULL, 'ana', :now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO haute_quote_details (quote_id, revision, reference, insured, "
                    "stage, assignee, archived) VALUES ('q1', 1, 'Q-000001', 'Acme', 'draft', "
                    "'ana', 0)"
                )
            )
    finally:
        engine.dispose()

    migrate_store(url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
            assert context.get_current_revision() == "0004"
            assert compare_metadata(context, METADATA) == []
            row = connection.execute(
                text("SELECT quote_reference, insured, is_renewal FROM haute_quote_details")
            ).one()
            assert tuple(row) == ("Q-000001", "Acme", None)
            # The fourth is now the oldest that can use it, as the third named itself before.
            oldest = connection.execute(
                text("SELECT oldest_reader FROM haute_quote_store_compat")
            ).scalar_one()
            assert oldest == "0004"
    finally:
        engine.dispose()


def test_a_file_at_the_third_schema_gives_each_app_quote_its_reference_and_option_1(
    url: str,
) -> None:
    # A database the third schema's Haute wrote: an app quote of two revisions, whose
    # reference its details hold, and a quote the store saved alone, with none.
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            command.upgrade(_alembic_config(connection), "0003")
            now = datetime(2026, 10, 20, 9, 0)
            connection.execute(text("INSERT INTO haute_quote_forms VALUES ('f1', '{}')"))
            for quote_id, name, latest in (("q1", "Fleet", 2), ("q2", "smoke", 1)):
                connection.execute(
                    text("INSERT INTO haute_quotes VALUES (:id, :name, 'ana', :now, :latest)"),
                    {"id": quote_id, "name": name, "now": now, "latest": latest},
                )
                for number in range(1, latest + 1):
                    connection.execute(
                        text(
                            "INSERT INTO haute_quote_revisions VALUES "
                            "(:id, :number, 'f1', '{}', '{}', NULL, NULL, 'ana', :now)"
                        ),
                        {"id": quote_id, "number": number, "now": now},
                    )
            for number in (1, 2):
                connection.execute(
                    text(
                        "INSERT INTO haute_quote_details (quote_id, revision, reference, stage, "
                        "assignee, archived) VALUES ('q1', :number, 'Q-000004', 'draft', 'ana', 0)"
                    ),
                    {"number": number},
                )
    finally:
        engine.dispose()

    migrate_store(url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
            assert context.get_current_revision() == "0004"
            assert compare_metadata(context, METADATA) == []
            quotes = connection.execute(
                text("SELECT id, reference, option FROM haute_quotes ORDER BY id")
            ).all()
            assert [tuple(row) for row in quotes] == [("q1", "Q-000004", 1), ("q2", None, None)]
            details = connection.execute(
                text("SELECT revision, quote_option FROM haute_quote_details ORDER BY revision")
            ).all()
            assert [tuple(row) for row in details] == [(1, 1), (2, 1)]
            # An alternative of Q-000004 would take option 2.
            counted = connection.execute(
                text("SELECT reference, last_option FROM haute_quote_options")
            ).all()
            assert [tuple(row) for row in counted] == [("Q-000004", 1)]
            oldest = connection.execute(
                text("SELECT oldest_reader FROM haute_quote_store_compat")
            ).scalar_one()
            assert oldest == "0004"
    finally:
        engine.dispose()


def test_a_haute_at_the_third_schema_refuses_a_database_migrated_to_the_fourth(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate_store(url)
    # The previous release, rolled back onto it: the first three migrations alone.
    previous = tmp_path / "previous_migrations"
    shutil.copytree(_MIGRATIONS, previous, ignore=shutil.ignore_patterns("0004_*", "__pycache__"))
    monkeypatch.setattr("haute._quote_store._store._MIGRATIONS", previous)

    with pytest.raises(
        QuoteStoreNotMigratedError,
        match="newer than this Haute's newest, 0003, and names 0004 as the oldest",
    ):
        open_store(url)
    with pytest.raises(QuoteStoreNotMigratedError, match="upgrade Haute"):
        migrate_store(url)


def test_a_file_not_at_the_newest_schema_is_refused_naming_what_to_run(url: str) -> None:
    with pytest.raises(QuoteStoreNotMigratedError, match="migrate_store") as refused:
        open_store(url)
    assert refused.value.error_code == "quote_store_not_migrated"


def _ahead(url: str, oldest_reader: str) -> None:
    """Put the migrated database at a schema past this Haute's, naming *oldest_reader*."""
    migrate_store(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"UPDATE {VERSION_TABLE} SET version_num = '0005'"))
            connection.execute(
                text("UPDATE haute_quote_store_compat SET oldest_reader = :oldest"),
                {"oldest": oldest_reader},
            )
    finally:
        engine.dispose()


def test_a_database_past_this_hautes_schema_that_names_it_able_opens_as_it_is(url: str) -> None:
    _ahead(url, oldest_reader="0002")

    migrate_store(url)
    with open_store(url, clock=Clock()) as store:
        _create(store)
        assert len(store.quotes()) == 1


def test_a_database_past_this_hautes_schema_that_needs_a_newer_one_is_refused(url: str) -> None:
    _ahead(url, oldest_reader="0005")

    with pytest.raises(QuoteStoreNotMigratedError, match="newer than this Haute's newest, 0004"):
        open_store(url)
    with pytest.raises(QuoteStoreNotMigratedError, match="upgrade Haute"):
        migrate_store(url)


def test_a_sqlite_store_keeps_foreign_keys_on_and_no_write_ahead_log(
    store: QuoteStore, tmp_path: Path
) -> None:
    _create(store)

    with store._engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            connection.exec_driver_sql(
                "INSERT INTO haute_quote_revisions (quote_id, number, form_revision, inputs, "
                "request, saved_by, saved_at) VALUES ('none', 1, 'none', '{}', '{}', 'a', "
                "'2026-10-12 09:00:00')"
            )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["quotes.sqlite"]
    with sqlite3.connect(tmp_path / "quotes.sqlite") as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone() == ("delete",)


def _write_ahead(path: Path) -> None:
    """A store's file put in write-ahead logging, as another tool might leave it."""
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    finally:
        raw.close()


def test_a_file_left_in_write_ahead_logging_is_put_back_to_a_rollback_journal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.sqlite"
    migrate_store(store_url(path))
    _write_ahead(path)

    with open_store(store_url(path), clock=Clock()) as store:
        _create(store)

    with sqlite3.connect(path) as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["quotes.sqlite"]


def test_a_file_held_in_write_ahead_logging_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.sqlite"
    migrate_store(store_url(path))
    _write_ahead(path)
    # Another connection holds the file in write-ahead logging, so it cannot be switched.
    holder = sqlite3.connect(path)
    try:
        holder.execute("SELECT COUNT(*) FROM haute_quotes").fetchone()
        with pytest.raises(QuoteStoreError, match="could not be put back to a rollback journal"):
            with open_store(store_url(path), clock=Clock()) as store:
                _create(store)
        assert holder.execute("SELECT COUNT(*) FROM haute_quotes").fetchone() == (0,)
    finally:
        holder.close()


# ── Quotes and revisions ──────────────────────────────────────────────────


def test_a_quote_is_created_and_read_back_whole(store: QuoteStore) -> None:
    created = _create(store, release="motor-2026.10.12+abc1234")

    read = store.revision(created.quote_id)

    assert read == created
    assert read.number == 1
    assert read.inputs == VALUES
    assert read.request == REQUEST
    assert read.response == RESPONSE
    assert read.release == "motor-2026.10.12+abc1234"
    assert read.saved_by == "ana@example.com"
    assert read.saved_at.tzinfo is UTC
    assert store.form(read.form_revision) == FORM


def test_a_revision_follows_the_one_it_names_and_leaves_it_as_it_was(store: QuoteStore) -> None:
    first = _create(store)
    repriced = {"pricing": {"premium": 530.0}}

    second = store.save_revision(
        first.quote_id,
        after=1,
        saved_by="ben@example.com",
        form=FORM,
        inputs=VALUES,
        request=REQUEST,
        response=repriced,
        release=None,
    )

    assert second.number == 2
    assert store.revision(first.quote_id) == second
    assert store.revision(first.quote_id, 1) == first


def test_a_revision_after_one_the_quote_has_moved_past_is_refused_with_nothing_written(
    store: QuoteStore, url: str
) -> None:
    first = _create(store)
    fields = {
        "saved_by": "ben@example.com",
        "form": FORM,
        "inputs": VALUES,
        "request": REQUEST,
        "response": None,
        "release": None,
    }
    store.save_revision(first.quote_id, after=1, **fields)

    other_form = _form(_table("t_policy", "policy", ("c_region", "region")))
    with pytest.raises(StaleQuoteError, match="revision 1.*newest is 2") as refused:
        store.save_revision(first.quote_id, after=1, **{**fields, "form": other_form})

    assert refused.value.error_code == "stale_quote_revision"
    assert store.revision(first.quote_id).number == 2
    [summary] = store.quotes()
    assert summary.latest == 2
    # The refused save's form was not kept either: the one transaction wrote nothing.
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        assert raw.execute("SELECT COUNT(*) FROM haute_quote_forms").fetchone() == (1,)


def test_an_unknown_quote_or_revision_is_refused_naming_it(store: QuoteStore) -> None:
    created = _create(store)

    with pytest.raises(QuoteNotFoundError, match="'nope'") as unknown:
        store.revision("nope")
    with pytest.raises(QuoteNotFoundError, match="revision 3"):
        store.revision(created.quote_id, 3)
    with pytest.raises(QuoteNotFoundError, match="form 'nope'"):
        store.form("nope")
    assert unknown.value.error_code == "quote_not_found"


def test_a_quote_saved_unpriced_has_no_response(store: QuoteStore) -> None:
    created = _create(store, response=None)

    assert store.revision(created.quote_id).response is None
    [summary] = store.quotes()
    assert summary.priced is False


def test_values_not_as_the_sheets_keep_them_are_refused(store: QuoteStore) -> None:
    with pytest.raises(QuoteStoreError, match="not as the sheets keep them"):
        _create(store, inputs={"t_policy": [{"c_region": 5}]})


def test_two_quotes_on_one_form_keep_it_once(store: QuoteStore, url: str) -> None:
    first = _create(store)
    second = _create(store, name="Southern fleet")

    assert first.form_revision == second.form_revision
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        assert raw.execute("SELECT COUNT(*) FROM haute_quote_forms").fetchone() == (1,)


def test_the_list_of_quotes_is_newest_first_for_one_owner_or_all(store: QuoteStore) -> None:
    ana = _create(store, name="Northern fleet")
    ben = _create(store, name="Southern fleet", owner="ben@example.com")
    store.save_revision(
        ana.quote_id,
        after=1,
        saved_by="ana@example.com",
        form=FORM,
        inputs=VALUES,
        request=REQUEST,
        response=RESPONSE,
        release=None,
    )

    assert [quote.name for quote in store.quotes()] == ["Northern fleet", "Southern fleet"]
    [only_ben] = store.quotes(owner="ben@example.com")
    assert (only_ben.id, only_ben.latest, only_ben.priced) == (ben.quote_id, 1, True)


def test_one_quote_is_read_as_the_list_shows_it(store: QuoteStore) -> None:
    ana = _create(store, name="Northern fleet")
    _create(store, name="Southern fleet", owner="ben@example.com")
    store.save_revision(
        ana.quote_id,
        after=1,
        saved_by="cy@example.com",
        form=FORM,
        inputs=VALUES,
        request=REQUEST,
        response=None,
        release=None,
    )

    summary = store.summary(ana.quote_id)

    assert summary == next(quote for quote in store.quotes() if quote.id == ana.quote_id)
    assert (summary.name, summary.latest, summary.saved_by, summary.priced) == (
        "Northern fleet",
        2,
        "cy@example.com",
        False,
    )
    with pytest.raises(QuoteNotFoundError, match="holds no quote 'nope'"):
        store.summary("nope")


# ── A saved quote's values on the current form ────────────────────────────


def test_values_are_carried_onto_the_current_form_through_the_stable_ids() -> None:
    saved = _form(
        _table("t_policy", "policy", ("c_region", "region"), ("c_cover", "cover")),
        _table("t_driver", "driver", ("c_age", "age")),
        _table("t_extra", "extras", ("c_note", "note")),
    )
    current = _form(
        # Renamed, with one column removed and a new one added.
        _table("t_policy", "policy_details", ("c_region", "area"), ("c_new", "excess")),
        # A table that is now an output.
        _table("t_driver", "driver", ("c_age", "age"), role="output"),
    )
    values = {
        "t_policy": [{"c_region": "north", "c_cover": "full"}],
        "t_driver": [{"c_age": "42"}],
        "t_extra": [{"c_note": " "}],
    }

    carried = carry_values(values, saved, current)

    assert carried.values == {"t_policy": [{"c_region": "north"}]}
    # By the saved form's names, in its order; the blank note is no loss.
    assert carried.dropped == ["policy.cover", "driver.age"]


def test_a_table_now_of_one_row_keeps_the_first_and_names_the_values_of_the_rest() -> None:
    saved = _form(
        {**_table("t_layer", "layers", ("c_limit", "limit"), ("c_note", "note")), "rows": "many"}
    )
    current = _form(_table("t_layer", "layer", ("c_limit", "limit")))
    values = {
        "t_layer": [
            {"c_limit": "500", "c_note": "first"},
            {"c_limit": "1000", "c_note": ""},
            {"c_limit": "", "c_note": "third"},
        ]
    }

    carried = carry_values(values, saved, current)

    assert carried.values == {"t_layer": [{"c_limit": "500"}]}
    assert carried.dropped == ["layers.note", "layers.limit row 2"]
