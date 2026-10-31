"""The underwriter app's quote book (specs/workbench): each quote's current state in the form's
own tables, written whole by a save and read with plain SQL; the two stages; who did what in
``haute_activity``; operation keys that make a repeated write write once; and the Quotes
page's lists. Each test runs on SQLite and on each SQL Server or PostgreSQL server the
environment names (tests/_store_servers.py)."""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import (
    PricedOutputs,
    QuoteBook,
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteNotReadyError,
    QuoteStoreError,
    QuoteValuesError,
    ReferenceTakenError,
    Source,
    StaleQuoteError,
    WriteSupersededError,
    open_book,
)
from haute._workbench_form import FormSpec, with_quote_details
from haute._workbench_tables import finite_document, priced_tables
from tests._store_servers import Target, backends, database

PROJECT = "motor"
ANA = "ana@example.com"
JO = "jo@example.com"
RELEASE = "release-1"
HAUTE = "9.9.9"


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"id": f"c_{name}", "name": name, "type": type_, **rest}


FORM = with_quote_details(
    FormSpec.model_validate(
        {
            "name": PROJECT,
            "schema": {
                "tables": [
                    {
                        "id": "t_policy",
                        "name": "policy",
                        "rows": "one",
                        "columns": [
                            column("region", label="Region", required=True),
                            column("drivers", "int", label="Drivers", required=True, min=1),
                            column("start", "date"),
                            column("telematics", "bool"),
                        ],
                    },
                    {
                        "id": "t_vehicles",
                        "name": "vehicles",
                        "rows": "many",
                        "columns": [
                            column("reg", key=True),
                            column("value", "float", label="Value", required=True),
                            column("garaged", "bool"),
                            column("mileage", "int", label="Mileage"),
                        ],
                    },
                    {
                        "id": "t_premium",
                        "name": "premium",
                        "role": "output",
                        "rows": "one",
                        "columns": [
                            column("total_premium", "float", label="Total premium", required=True),
                            column("referred", "bool", label="Referred", required=True),
                        ],
                    },
                    {
                        "id": "t_vehicle_premium",
                        "name": "vehicle_premium",
                        "role": "output",
                        "rows": "many",
                        "columns": [column("reg"), column("premium", "float")],
                    },
                ]
            },
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


class Clock:
    """A clock moving a second on each reading."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture(params=backends())
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    """A database of the test's own, on each backend the run has."""
    with database(request.param, tmp_path) as target:
        yield target


@pytest.fixture
def book(store: Target) -> Iterator[QuoteBook]:
    store.make(FORM, project=PROJECT)
    with open_book(
        store.url,
        FORM,
        project=PROJECT,
        environment=store.environment,
        haute_version=HAUTE,
        clock=Clock(),
        sign_in=store.sign_in,
    ) as opened:
        yield opened


_keys = iter(range(10**6))


def op() -> str:
    return f"operation-{next(_keys):06d}"


def values(**tables: Any) -> dict[str, Any]:
    """Cells by table and column id: a fully keyed motor quote, with *tables* over it."""
    base: dict[str, Any] = {
        "quote_details": [{"qd_quote_name": "Fleet", "qd_insured": "Acme Haulage"}],
        "t_policy": [{"c_region": "north", "c_drivers": "2", "c_start": "2026-11-01"}],
        "t_vehicles": [
            {"c_reg": "AB12 CDE", "c_value": "£12,500", "c_garaged": True},
            {},
            {"c_reg": "XY34 ZZZ", "c_value": "8000", "c_mileage": "9007199254740993"},
        ],
    }
    return {**base, **tables}


def document(total: Any = 512.4, referred: Any = False) -> dict[str, Any]:
    """The pricing's typed response document for a quote."""
    return {
        "premium": {"total_premium": total, "referred": referred},
        "vehicle_premium": [
            {"reg": "AB12 CDE", "premium": 300.0},
            {"reg": "XY34 ZZZ", "premium": 212.4},
        ],
    }


def make(book: QuoteBook, reference: str = "A-1", **kwargs: Any) -> Any:
    kwargs.setdefault("values", values())
    return book.create(
        operation=op(), actor=ANA, at=book.now(), reference=reference, **kwargs
    ).quote


def save(book: QuoteBook, quote: Any, *, priced: bool = True, by: str = ANA, **kwargs: Any) -> Any:
    kwargs.setdefault("values", values())
    return book.save(
        quote.reference,
        quote.option,
        operation=kwargs.pop("operation", op()),
        version=kwargs.pop("version", quote.version),
        actor=by,
        priced=PricedOutputs(RELEASE, kwargs.pop("document", document())) if priced else None,
        at=book.now(),
        **kwargs,
    ).quote


def sql(store: Target, query: str) -> list[tuple[Any, ...]]:
    return store.read(query)


# ── Saving ──────────────────────────────────────────────────────────────────


def test_a_saved_quote_is_rows_of_the_forms_own_tables_read_with_plain_sql(
    book: QuoteBook, store: Target
) -> None:
    saved = save(book, make(book))

    assert saved.version == 2 and saved.priced_release == RELEASE
    assert sql(
        store, "SELECT quote_reference, quote_option, region, drivers, start FROM policy"
    ) == [("A-1", 1, "north", 2, "2026-11-01")]
    # Each vehicle under its row on the sheet, the blank row between them counted; an
    # unticked box in a filled row is false, as the request priced it.
    assert sql(
        store, "SELECT quote_row, reg, value, garaged, mileage FROM vehicles ORDER BY quote_row"
    ) == [(1, "AB12 CDE", 12500.0, 1, None), (3, "XY34 ZZZ", 8000.0, 0, 9007199254740993)]
    # The priced outputs sit beside the inputs, joined on the quote's identity.
    assert sql(
        store,
        "SELECT d.insured, p.total_premium FROM quote_details d JOIN premium p "
        "ON p.quote_reference = d.quote_reference AND p.quote_option = d.quote_option",
    ) == [("Acme Haulage", 512.4)]
    assert sql(store, "SELECT quote_row, reg FROM vehicle_premium ORDER BY quote_row") == [
        (1, "AB12 CDE"),
        (2, "XY34 ZZZ"),
    ]


def test_saving_again_replaces_the_quotes_rows_and_an_unpriced_save_keeps_no_prices(
    book: QuoteBook, store: Target
) -> None:
    quote = save(book, make(book))
    one_vehicle = values(t_vehicles=[{"c_reg": "AB12 CDE", "c_value": "12500"}])

    saved = save(book, quote, priced=False, values=one_vehicle)

    assert sql(store, "SELECT reg FROM vehicles") == [("AB12 CDE",)]
    assert (
        sql(store, "SELECT * FROM premium") == []
        and sql(store, "SELECT * FROM vehicle_premium") == []
    )
    assert saved.priced_release is None and not saved.priced


def test_a_value_not_of_its_columns_type_refuses_the_whole_save_naming_each_cell(
    book: QuoteBook, store: Target
) -> None:
    quote = make(book)
    misfits = values(
        t_policy=[{"c_region": "north", "c_drivers": "two"}],
        t_vehicles=[{"c_reg": "A", "c_value": "1"}, {}, {"c_reg": "B", "c_mileage": "lots"}],
    )

    with pytest.raises(QuoteValuesError) as refused:
        save(book, quote, priced=False, values=misfits)

    assert [str(cell) for cell in refused.value.cells] == [
        "policy, Drivers",
        "vehicles row 3, Mileage",
    ]
    assert book.get("A-1", 1).version == quote.version
    assert sql(store, "SELECT drivers FROM policy") == [(2,)]


def test_nothing_else_a_quotes_cells_or_outputs_hold_refuses_a_save(
    book: QuoteBook, store: Target
) -> None:
    # An empty required input, a value below its column's minimum and a non-finite output.
    quote = make(book, values=values(t_policy=[{"c_drivers": "0"}]))

    saved = save(
        book,
        quote,
        values=values(t_policy=[{"c_drivers": "0"}]),
        document=document(total=float("nan")),
    )

    opened = book.read(saved.reference, saved.option)
    assert opened.outputs is not None
    assert opened.outputs["premium"] == [
        {"quote_reference": "A-1", "quote_option": 1, "referred": False}
    ]
    # The non-finite price is kept empty: no engine is handed one, SQL Server holding none.
    assert sql(store, "SELECT total_premium, referred FROM premium") == [(None, 0)]


# ── Repeated writes ─────────────────────────────────────────────────────────


def test_a_save_repeated_under_its_key_answers_the_quote_and_writes_once(
    book: QuoteBook, store: Target
) -> None:
    quote = make(book)
    key = op()
    first = save(book, quote, operation=key)

    again = save(book, quote, operation=key)

    assert again.version == first.version == 2
    assert sql(store, "SELECT COUNT(*) FROM haute_activity WHERE action = 'saved'") == [(1,)]


def test_a_save_repeated_after_a_colleagues_is_superseded_naming_both(book: QuoteBook) -> None:
    quote = make(book)
    key = op()
    mine = save(book, quote, operation=key)
    save(book, mine, by=JO)

    with pytest.raises(WriteSupersededError) as superseded:
        save(book, quote, operation=key)

    assert superseded.value.changed_by == JO
    assert superseded.value.landed_at < superseded.value.changed_at


def test_one_save_sent_twice_at_once_is_one_write_and_two_saves_land_once(book: QuoteBook) -> None:
    quote = make(book)
    key = op()
    answers: list[Any] = []

    def send(over: Any, operation: str) -> None:
        try:
            answers.append(save(book, over, operation=operation))
        except Exception as exc:  # noqa: BLE001 - each thread's outcome is the test's
            answers.append(exc)

    twice = [threading.Thread(target=send, args=(quote, key)) for _ in range(2)]
    for thread in twice:
        thread.start()
    for thread in twice:
        thread.join()
    assert [getattr(answer, "version", answer) for answer in answers] == [2, 2]

    current = book.get("A-1", 1)
    answers.clear()
    rivals = [threading.Thread(target=send, args=(current, op())) for _ in range(2)]
    for thread in rivals:
        thread.start()
    for thread in rivals:
        thread.join()
    assert sorted(type(answer).__name__ for answer in answers) == ["AppQuote", "StaleQuoteError"]
    assert book.get("A-1", 1).version == current.version + 1


def test_a_creation_repeated_under_its_key_makes_one_quote(book: QuoteBook, store: Target) -> None:
    key = op()
    made = book.create(operation=key, actor=ANA, values=values(), at=book.now(), reference="A-1")
    source = Source("A-1", 1, "alternative")
    alt_key = op()
    first = book.create(
        operation=alt_key, actor=ANA, values=values(), at=book.now(), reference=None, source=source
    )

    again = book.create(operation=key, actor=ANA, values=values(), at=book.now(), reference="A-1")
    alt_again = book.create(
        operation=alt_key, actor=ANA, values=values(), at=book.now(), reference=None, source=source
    )

    assert (again.quote.reference, again.quote.option) == (made.quote.reference, 1)
    assert (alt_again.quote.option, first.quote.option) == (2, 2)
    # A repeat answers what the quote holds, as the first write did.
    assert again.values == made.values
    assert sql(store, "SELECT COUNT(*) FROM quote_details") == [(2,)]


def test_a_key_reused_for_another_quote_or_person_is_refused(book: QuoteBook) -> None:
    key = op()
    quote = make(book)
    other = make(book, "B-1")
    save(book, quote, operation=key)

    with pytest.raises(QuoteStoreError, match="used for another write"):
        save(book, other, operation=key)
    with pytest.raises(QuoteStoreError, match="used for another write"):
        save(book, quote, operation=key, by=JO)


# ── Stages ──────────────────────────────────────────────────────────────────


def test_mark_final_needs_a_price_of_the_running_release_and_every_required_value(
    book: QuoteBook,
) -> None:
    quote = make(book, values=values(t_policy=[{"c_region": "north"}]))
    unpriced = save(book, quote, priced=False)
    with pytest.raises(QuoteNotReadyError, match="is not priced"):
        book.finalise("A-1", 1, version=unpriced.version, actor=ANA, release=RELEASE)

    priced = save(
        book,
        unpriced,
        values=values(t_policy=[{"c_region": "north"}]),
        document=document(total=None),
    )
    with pytest.raises(QuoteNotReadyError, match="not this one, release-2"):
        book.finalise("A-1", 1, version=priced.version, actor=ANA, release="release-2")
    with pytest.raises(QuoteNotReadyError) as not_ready:
        book.finalise("A-1", 1, version=priced.version, actor=ANA, release=RELEASE)
    assert not_ready.value.missing == ["policy.Drivers", "premium.Total premium"]

    ready = save(book, priced)
    final = book.finalise("A-1", 1, version=ready.version, actor=JO, release=RELEASE)
    assert final.stage == "final" and final.final_by == JO


@pytest.mark.parametrize(
    ("vehicles", "referred", "missing"),
    [
        # Filled in two rows and empty in the third.
        (
            [{"c_reg": "A", "c_value": "1"}, {"c_reg": "B", "c_value": "2"}, {"c_reg": "C"}],
            False,
            ["vehicles row 3.Value"],
        ),
        # A table without rows passes.
        ([], False, []),
        # Zero is a value.
        ([{"c_reg": "A", "c_value": "0"}], False, []),
        # A required True/false output holding null refuses it; false passes.
        ([{"c_reg": "A", "c_value": "1"}], None, ["premium.Referred"]),
    ],
    ids=["a row empty", "no rows", "zero", "a null tick"],
)
def test_a_required_column_holds_a_value_in_every_row(
    book: QuoteBook, vehicles: list[dict[str, Any]], referred: Any, missing: list[str]
) -> None:
    quote = save(
        book, make(book), values=values(t_vehicles=vehicles), document=document(referred=referred)
    )

    if missing:
        with pytest.raises(QuoteNotReadyError) as not_ready:
            book.finalise("A-1", 1, version=quote.version, actor=ANA, release=RELEASE)
        assert not_ready.value.missing == missing
    else:
        assert (
            book.finalise("A-1", 1, version=quote.version, actor=ANA, release=RELEASE).stage
            == "final"
        )


def test_text_of_spaces_alone_is_no_value(book: QuoteBook) -> None:
    spaces = values(t_policy=[{"c_region": "   ", "c_drivers": "2"}])
    quote = save(book, make(book, values=spaces), values=spaces)

    with pytest.raises(QuoteNotReadyError) as not_ready:
        book.finalise("A-1", 1, version=quote.version, actor=ANA, release=RELEASE)
    assert not_ready.value.missing == ["policy.Region"]


def test_a_final_quote_takes_no_save_until_it_is_amended_with_a_reason(
    book: QuoteBook, store: Target
) -> None:
    ready = save(book, make(book))
    final = book.finalise("A-1", 1, version=ready.version, actor=ANA, release=RELEASE)

    with pytest.raises(QuoteLockedError, match="is final: amend it"):
        save(book, final)
    with pytest.raises(QuoteStoreError, match="Give a reason"):
        book.amend("A-1", 1, version=final.version, actor=ANA, reason="  ")

    amended = book.amend(
        "A-1", 1, version=final.version, actor=JO, reason="Broker asked for a lower excess"
    )

    assert amended.stage == "draft"
    assert sql(
        store, "SELECT action, actor, reason FROM haute_activity WHERE action = 'amended'"
    ) == [("amended", JO, "Broker asked for a lower excess")]
    assert save(book, amended).version == amended.version + 1


def test_assigning_archiving_and_restoring_keep_the_values_and_the_prices(book: QuoteBook) -> None:
    saved = save(book, make(book))
    before = book.read("A-1", 1)

    assigned = book.assign("A-1", 1, version=saved.version, actor=ANA, assignee=JO)
    archived = book.archive("A-1", 1, version=assigned.version, actor=ANA)
    with pytest.raises(QuoteLockedError, match="archived: restore it"):
        save(book, archived)
    restored = book.restore("A-1", 1, version=archived.version, actor=JO)

    after = book.read("A-1", 1)

    def typed(opened: Any) -> dict[str, Any]:
        # What was typed and priced, the administration the changes set aside.
        return {table: rows for table, rows in opened.values.items() if table != "quote_details"}

    assert (typed(after), after.outputs) == (typed(before), before.outputs)
    assert (restored.assignee, restored.archived, archived.archived_by) == (JO, False, ANA)


def test_a_change_over_a_version_moved_on_is_refused_naming_who_moved_it(book: QuoteBook) -> None:
    quote = make(book)
    save(book, quote, by=JO)

    with pytest.raises(StaleQuoteError, match=f"{JO} saved it"):
        book.assign("A-1", 1, version=quote.version, actor=ANA, assignee=ANA)
    with pytest.raises(StaleQuoteError):
        save(book, quote)


# ── References and options ──────────────────────────────────────────────────


def test_references_are_one_only_when_they_are_the_same_case_included(
    book: QuoteBook, store: Target
) -> None:
    upper = make(book, "ABC-1")
    lower = make(book, "abc-1")
    make(book, unicodedata.normalize("NFD", "café"))

    with pytest.raises(ReferenceTakenError) as taken:
        make(book, unicodedata.normalize("NFC", "café"))
    assert taken.value.label == "café/1"
    for bad in (".", "..", "a/b", ""):
        with pytest.raises(QuoteStoreError):
            make(book, bad)
    # Saving one leaves the other's rows as they were, whatever the engine's collation.
    save(book, upper, values=values(t_policy=[{"c_region": "north", "c_drivers": "2"}]))
    save(book, lower, values=values(t_policy=[{"c_region": "south", "c_drivers": "3"}]))
    assert sql(
        store,
        "SELECT quote_reference, region, drivers FROM policy WHERE quote_reference "
        "IN ('ABC-1', 'abc-1') ORDER BY region",
    ) == [("ABC-1", "north", 2), ("abc-1", "south", 3)]


def test_two_alternatives_made_at_once_take_two_options(book: QuoteBook) -> None:
    make(book)
    made: list[Any] = []

    def alternative() -> None:
        made.append(
            book.create(
                operation=op(),
                actor=ANA,
                values=values(),
                at=book.now(),
                reference=None,
                source=Source("A-1", 1, "alternative"),
            ).quote
        )

    threads = [threading.Thread(target=alternative) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(quote.option for quote in made) == [2, 3]
    assert all(quote.source == Source("A-1", 1, "alternative") for quote in made)


def test_a_quote_made_from_one_the_app_does_not_keep_is_refused(book: QuoteBook) -> None:
    with pytest.raises(QuoteNotFoundError):
        make(book, "B-1", source=Source("A-1", 1, "duplicate"))


# ── Loading ────────────────────────────────────────────────────────────────


def test_a_quote_opens_with_its_cells_as_text_and_its_rows_in_their_places(
    book: QuoteBook,
) -> None:
    save(book, make(book))

    opened = book.read("A-1", 1)

    assert opened.values["t_policy"] == [
        {"c_region": "north", "c_drivers": "2", "c_start": "2026-11-01"}
    ]
    assert opened.values["t_vehicles"] == [
        {"c_reg": "AB12 CDE", "c_value": "12500", "c_garaged": True},
        {},
        {"c_reg": "XY34 ZZZ", "c_value": "8000", "c_mileage": "9007199254740993"},
    ]
    assert opened.dropped == []


def test_a_quotes_outputs_open_as_the_pricing_rendered_them(book: QuoteBook) -> None:
    priced = document(total=float("inf"))
    save(book, make(book), document=priced)

    opened = book.read("A-1", 1)

    identity = {"quote_reference": "A-1", "quote_option": 1}
    answered = finite_document(
        {
            "premium": {**identity, **priced["premium"]},
            "vehicle_premium": [{**identity, **row} for row in priced["vehicle_premium"]],
        }
    )
    assert opened.outputs == priced_tables(answered)
    assert opened.outputs is not None and "total_premium" not in opened.outputs["premium"][0]


def test_an_unpriced_quote_opens_with_no_outputs(book: QuoteBook) -> None:
    assert book.read(*_identity(make(book))).outputs is None


def test_a_load_that_raced_a_change_reads_the_quote_again(
    book: QuoteBook, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = save(book, make(book))
    real = QuoteBook._get
    reads: list[int] = []

    def raced(self: QuoteBook, connection: Any, reference: str, option: int) -> Any:
        # The first read sees the quote as it stood before a change that landed meanwhile.
        quote = real(self, connection, reference, option)
        reads.append(quote.version)
        return replace(quote, version=quote.version - 1) if len(reads) == 1 else quote

    monkeypatch.setattr(QuoteBook, "_get", raced)
    assert book.read("A-1", 1).quote.version == saved.version
    assert len(reads) == 2

    monkeypatch.setattr(
        QuoteBook,
        "_get",
        lambda self, connection, reference, option: replace(
            real(self, connection, reference, option), version=0
        ),
    )
    with pytest.raises(QuoteStoreError, match="kept changing while it was read"):
        book.read("A-1", 1)


def test_a_preset_row_whose_value_is_no_longer_allowed_is_named_as_dropped(
    store: Target,
) -> None:
    def perils_form(options: list[str]) -> FormSpec:
        return with_quote_details(
            FormSpec.model_validate(
                {
                    "name": PROJECT,
                    "schema": {
                        "tables": [
                            {
                                "id": "t_perils",
                                "name": "perils",
                                "rows": "many",
                                "columns": [
                                    column("peril", options=options, row_per_value=True, key=True),
                                    column("limit", "float"),
                                ],
                            }
                        ]
                    },
                    "pages": FORM.model_dump(by_alias=True)["pages"],
                }
            )
        )

    url, sign_in, environment = store.url, store.sign_in, store.environment
    store.make(perils_form(["AOP", "EQ"]), project=PROJECT)
    with open_book(
        url,
        perils_form(["AOP", "EQ"]),
        project=PROJECT,
        environment=environment,
        haute_version=HAUTE,
        sign_in=sign_in,
    ) as wide:
        typed = {
            "t_perils": [{"c_peril": "AOP", "c_limit": "100"}, {"c_peril": "EQ", "c_limit": "50"}]
        }
        wide.create(operation=op(), actor=ANA, values=typed, at=wide.now(), reference="P-1")

    # The allowed values are the form's rules, not its structure: the tables stay as they are.
    with open_book(
        url,
        perils_form(["AOP"]),
        project=PROJECT,
        environment=environment,
        haute_version=HAUTE,
        sign_in=sign_in,
    ) as narrow:
        opened = narrow.read("P-1", 1)

    assert opened.values["t_perils"] == [{"c_peril": "AOP", "c_limit": "100"}]
    assert opened.dropped == ["perils row 2 (EQ)"]


def _identity(quote: Any) -> tuple[str, int]:
    return quote.reference, quote.option


# ── The Quotes page, the activity and the people ────────────────────────────


def test_the_quotes_page_lists_searches_counts_and_pages(book: QuoteBook) -> None:
    for number in range(1, 4):
        make(
            book, f"Q-{number}", values=values(quote_details=[{"qd_insured": f"Insured {number}"}])
        )
    jo = make(book, "JO-1", values=values(quote_details=[{"qd_insured": "Zed"}]))
    book.assign("JO-1", 1, version=jo.version, actor=ANA, assignee=JO)

    mine = book.page(actor=ANA, view="mine")
    assert [quote.reference for quote in mine.quotes] == ["Q-3", "Q-2", "Q-1"]
    assert (
        mine.counts["mine"],
        mine.counts["team"],
        mine.counts["draft"],
        mine.counts["final"],
    ) == (3, 4, 3, 0)
    assert [q.reference for q in book.page(actor=ANA, view="team", search="q-2").quotes] == ["Q-2"]
    assert [q.reference for q in book.page(actor=ANA, view="team", search="zed").quotes] == ["JO-1"]
    first = book.page(actor=ANA, view="team", sort="reference", limit=2)
    second = book.page(actor=ANA, view="team", sort="reference", limit=2, cursor=first.next_cursor)
    assert [q.reference for q in first.quotes + second.quotes] == ["JO-1", "Q-1", "Q-2", "Q-3"]
    assert second.next_cursor is None


def test_a_quotes_activity_names_who_did_what_and_the_quotes_made_from_it(book: QuoteBook) -> None:
    quote = make(book)
    saved = save(book, quote)
    book.set_note("A-1", 1, actor=JO, note="Call the broker")
    book.create(
        operation=op(),
        actor=ANA,
        values=values(),
        at=book.now(),
        reference=None,
        source=Source("A-1", 1, "alternative"),
    )

    activity = book.activity("A-1", 1)

    assert [(entry.action, entry.actor) for entry in activity.entries] == [
        ("created", ANA),
        ("saved", ANA),
        ("noted", JO),
    ]
    assert [(related.label, related.relation) for related in activity.derived] == [
        ("A-1/2", "alternative")
    ]
    noted = book.get("A-1", 1)
    assert (noted.note, noted.note_by, noted.version) == ("Call the broker", JO, saved.version)
    # The people quotes are with, or were made, saved or changed by: a note's author alone
    # is none of them.
    assert book.people() == [ANA]
