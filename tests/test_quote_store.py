"""The workbench's quote store (specs/workbench): one schema on a SQLite file, brought to it by
Haute's migrations; quotes and their numbered revisions, each with the form it was keyed into;
and a saved quote's values carried onto the current form."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
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
            assert context.get_current_revision() == "0001"
    finally:
        engine.dispose()


def test_a_file_not_at_the_newest_schema_is_refused_naming_what_to_run(url: str) -> None:
    with pytest.raises(QuoteStoreNotMigratedError, match="migrate_store") as refused:
        open_store(url)
    assert refused.value.error_code == "quote_store_not_migrated"


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
