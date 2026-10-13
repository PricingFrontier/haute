"""The underwriter app's quote book (specs/workbench): each app quote's revisions with their
``quote_details`` row held in the store's typed table, its reference, the app's details set
over what a page sends, every change a revision named by the one it follows, the events of
who did what, operation keys that make a repeated save write once, and the paged lists of the
Quotes page."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import (
    QuoteBook,
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteStoreError,
    Source,
    StaleQuoteError,
    details_of,
    migrate_store,
    open_store,
    store_url,
    with_details,
)
from haute._workbench_form import FormSpec, with_quote_details

FORM = with_quote_details(
    FormSpec.model_validate(
        {
            "name": "motor",
            "schema": {
                "tables": [
                    {
                        "id": "t_policy",
                        "name": "policy",
                        "rows": "one",
                        "columns": [{"id": "c_region", "name": "region", "type": "str"}],
                    }
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
TYPED = {
    "quote_name": "Fleet renewal",
    "insured": "Acme Engineering",
    "broker": "Marsh",
    "inception_date": "2026-11-01",
    "valid_until": "2026-11-30",
    "currency": "GBP",
}
VALUES = with_details({"t_policy": [{"c_region": "north"}]}, TYPED)
REQUEST = {"policy": {"region": "north"}}
RESPONSE = {"pricing": [{"premium": 512.4}]}
RELEASE = "release-1"
ANA = "ana@example.com"
JO = "jo@example.com"


class Clock:
    """A clock moving a second on each reading, or standing still when told to."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
        self.step = timedelta(seconds=1)

    def __call__(self) -> datetime:
        self.now += self.step
        return self.now


@pytest.fixture
def url(tmp_path: Path) -> str:
    return store_url(tmp_path / "quotes.sqlite")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def book(url: str, clock: Clock) -> Iterator[QuoteBook]:
    migrate_store(url)
    with QuoteBook(open_store(url, clock=clock)) as opened:
        yield opened


_operations = iter(range(10**6))


def _op() -> str:
    return f"operation-{next(_operations):06d}"


def _values(**details: Any) -> dict[str, Any]:
    return with_details(VALUES, {**TYPED, **details})


def _create(book: QuoteBook, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "operation": _op(),
        "actor": ANA,
        "form": FORM,
        "inputs": VALUES,
        "request": REQUEST,
        "response": RESPONSE,
        "release": RELEASE,
    }
    return book.create(**{**fields, **overrides})


def _save(book: QuoteBook, quote: Any, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "operation": _op(),
        "version": quote.latest,
        "actor": ANA,
        "form": FORM,
        "inputs": VALUES,
        "request": REQUEST,
        "response": RESPONSE,
        "release": RELEASE,
    }
    return book.save(quote.id, **{**fields, **overrides})


def _kinds(book: QuoteBook, quote_id: str) -> list[str]:
    return [event.kind for event in book.timeline(quote_id).events]


def _row(url: str, quote_id: str, revision: int) -> tuple[Any, ...]:
    with sqlite3.connect(url.removeprefix("sqlite:///")) as raw:
        return raw.execute(
            "SELECT reference, quote_name, insured, inception_date, stage, assignee, archived "
            "FROM haute_quote_details WHERE quote_id = ? AND revision = ?",
            (quote_id, revision),
        ).fetchone()


# ── The details and a quote's revisions ───────────────────────────────────


def test_a_new_quote_keeps_its_details_in_the_typed_table_alone(book: QuoteBook, url: str) -> None:
    quote = _create(book, inputs=_values(reference="Q-999", stage="final", assignee=JO))

    assert quote.reference == "Q-000001"
    assert (quote.stage, quote.assignee, quote.archived, quote.latest) == ("priced", ANA, False, 1)
    assert quote.title == "Fleet renewal"
    assert quote.details == {
        **TYPED,
        "reference": "Q-000001",
        "stage": "priced",
        "assignee": ANA,
        "archived": False,
    }
    assert _row(url, quote.id, 1) == (
        "Q-000001",
        "Fleet renewal",
        "Acme Engineering",
        "2026-11-01",
        "priced",
        ANA,
        0,
    )
    # The revision's values leave the details out, and read back with them.
    assert "quote_details" not in book.store.revision(quote.id).inputs
    assert details_of(book.revision(quote.id).inputs) == quote.details
    assert _kinds(book, quote.id) == ["created"]


def test_the_title_is_the_name_else_the_insured_else_the_reference(book: QuoteBook) -> None:
    insured = _create(book, inputs=with_details(VALUES, {"insured": "Harbour"}))
    blank = _create(book, inputs=with_details(VALUES, {}))

    assert insured.title == "Harbour"
    assert blank.title == blank.reference == "Q-000002"


def test_a_quote_saved_unpriced_is_a_draft(book: QuoteBook) -> None:
    assert _create(book, response=None, release=None).stage == "draft"


def test_a_date_that_is_not_one_is_refused_with_nothing_written(book: QuoteBook) -> None:
    with pytest.raises(QuoteStoreError, match="inception_date is not a date"):
        _create(book, inputs=_values(inception_date="next week"))

    assert _create(book).reference == "Q-000001"


def test_references_follow_on_and_a_failed_creation_keeps_none(book: QuoteBook) -> None:
    first = _create(book)
    with pytest.raises(QuoteStoreError, match="not as the sheets keep them"):
        _create(book, inputs={"t_policy": [{"c_region": 7}]})
    second = _create(book)

    assert (first.reference, second.reference) == ("Q-000001", "Q-000002")


def test_quotes_created_at_once_take_different_references(url: str) -> None:
    migrate_store(url)
    references: list[str] = []
    errors: list[BaseException] = []

    def make() -> None:
        try:
            with QuoteBook(open_store(url)) as own:
                references.append(_create(own).reference)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=make) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(references) == [f"Q-{n:06d}" for n in range(1, 7)]


def test_a_creation_or_save_repeated_with_its_operation_key_writes_once(book: QuoteBook) -> None:
    operation = _op()
    first = _create(book, operation=operation)
    assert _create(book, operation=operation).id == first.id
    saving = _op()
    saved = _save(book, first, operation=saving)
    again = _save(book, first, operation=saving)

    assert again.latest == saved.latest == 2
    assert _create(book).reference == "Q-000002"


def test_a_save_repeated_after_a_colleague_saved_answers_what_its_key_wrote(
    book: QuoteBook,
) -> None:
    quote = _create(book)
    saving = _op()
    mine = _save(book, quote, operation=saving, inputs=_values(broker="Marsh"))
    _save(book, mine, actor=JO, inputs=_values(broker="Aon"))
    book.set_note(quote.id, actor=JO, note="Aon quoted lower")

    again = _save(book, quote, operation=saving, inputs=_values(broker="Marsh"))

    # The quote as the key left it: its revision, its details, and so its version, which a
    # save over it now finds stale rather than writing over the colleague's; the note
    # written since is not yet its.
    assert (again.latest, again.details["broker"], again.last_saved_by) == (2, "Marsh", ANA)
    assert again.note == ""
    assert book.get(quote.id).latest == 3
    with pytest.raises(StaleQuoteError):
        _save(book, again)


def test_a_save_that_lost_a_race_to_its_own_key_answers_the_first_write(
    book: QuoteBook, monkeypatch: pytest.MonkeyPatch
) -> None:
    quote = _create(book)
    saving = _op()
    first = _save(book, quote, operation=saving)
    real = QuoteBook._operation
    looked: list[str] = []

    def unseen_once(self: QuoteBook, *args: Any) -> Any:
        # The second request's own look finds nothing, as one made before the first
        # committed would; its save is then refused as stale, and the key answers it.
        looked.append("look")
        return None if len(looked) == 1 else real(self, *args)

    monkeypatch.setattr(QuoteBook, "_operation", unseen_once)
    again = _save(book, quote, operation=saving)

    assert (again.id, again.latest) == (first.id, first.latest)
    assert book.get(quote.id).latest == 2


def test_an_operation_key_used_by_someone_else_or_another_quote_is_refused(
    book: QuoteBook,
) -> None:
    operation = _op()
    _create(book, operation=operation)
    other = _create(book)

    with pytest.raises(QuoteStoreError, match="used for another save"):
        _create(book, operation=operation, actor=JO)
    with pytest.raises(QuoteStoreError, match="used for another save"):
        _save(book, other, operation=operation)
    with pytest.raises(QuoteStoreError, match="operation key"):
        _create(book, operation="short")


def test_a_save_takes_the_typed_details_and_keeps_the_apps(book: QuoteBook, clock: Clock) -> None:
    quote = _create(book)
    typed = _values(quote_name="Fleet 2027", assignee=JO, reference="Q-1", archived=True)
    saved = _save(book, quote, actor=JO, inputs=typed, response=None, release=None)

    assert (saved.latest, saved.stage, saved.title) == (2, "draft", "Fleet 2027")
    assert (saved.reference, saved.assignee, saved.archived) == ("Q-000001", ANA, False)
    assert (saved.last_saved_by, saved.last_saved_at) == (JO, clock.now)
    assert details_of(book.revision(quote.id, 1).inputs)["quote_name"] == "Fleet renewal"
    assert _kinds(book, quote.id) == ["created", "saved"]


def test_a_save_over_a_revision_the_quote_has_moved_past_is_refused_naming_who(
    book: QuoteBook,
) -> None:
    quote = _create(book)
    book.assign(quote.id, version=1, actor=JO, assignee=JO)

    with pytest.raises(StaleQuoteError, match=r"Q-000001 has changed.*jo@example.com assigned it"):
        _save(book, quote)
    assert book.get(quote.id).latest == 2


def test_a_test_quote_has_no_details_and_is_not_listed(book: QuoteBook) -> None:
    test_quote = book.store.create(
        name="smoke",
        owner=ANA,
        saved_by=ANA,
        form=FORM,
        inputs=VALUES,
        request=REQUEST,
        response=None,
        release=None,
    )

    with pytest.raises(QuoteNotFoundError):
        book.get(test_quote.quote_id)
    # A test quote keeps its details among its values, as it is typed.
    assert "quote_details" in book.store.revision(test_quote.quote_id).inputs
    assert book.page(actor=ANA, view="team").quotes == []


# ── Lists ─────────────────────────────────────────────────────────────────


def test_the_lists_are_mine_the_teams_and_the_archive_with_their_counts(
    book: QuoteBook,
) -> None:
    mine = _create(book)
    theirs = _create(book, actor=JO, response=None, release=None)
    gone = _create(book)
    book.archive(gone.id, version=1, actor=ANA)

    assert [q.id for q in book.page(actor=ANA, view="mine").quotes] == [mine.id]
    assert [q.id for q in book.page(actor=ANA, view="team").quotes] == [theirs.id, mine.id]
    assert [q.id for q in book.page(actor=ANA, view="archived").quotes] == [gone.id]
    counts = book.page(actor=ANA, view="team").counts
    assert counts == {"mine": 1, "team": 2, "archived": 1, "draft": 1, "priced": 1, "final": 0}
    assert [q.id for q in book.page(actor=ANA, view="team", stage="draft").quotes] == [theirs.id]
    assert [q.id for q in book.page(actor=ANA, view="team", assignee=JO).quotes] == [theirs.id]


def test_a_search_matches_every_word_in_the_reference_name_insured_or_broker(
    book: QuoteBook,
) -> None:
    acme = _create(book)
    harbour = _create(book, inputs=_values(insured="Harbour Trading", broker="Aon", quote_name=""))

    def found(search: str) -> list[str]:
        return [q.reference for q in book.page(actor=ANA, view="team", search=search).quotes]

    assert found("acme") == [acme.reference]
    assert found("TRADING aon") == [harbour.reference]
    assert found("q-000002") == [harbour.reference]
    assert found("fleet marsh") == [acme.reference]
    assert found("acme aon") == []
    assert found("100%") == []
    assert found("q_0") == []


def test_pages_follow_on_without_repeating_or_missing_a_quote_saved_at_the_same_moment(
    book: QuoteBook, clock: Clock
) -> None:
    clock.step = timedelta(0)
    made = [_create(book).id for _ in range(5)]

    seen: list[str] = []
    cursor = None
    while True:
        page = book.page(actor=ANA, view="team", limit=2, cursor=cursor)
        seen += [q.id for q in page.quotes]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert sorted(seen) == sorted(made)
    assert len(seen) == 5


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("saved", ["Q-000003", "Q-000002", "Q-000001"]),
        ("reference", ["Q-000003", "Q-000002", "Q-000001"]),
        ("insured", ["Q-000002", "Q-000003", "Q-000001", "Q-000004", "Q-000005"]),
    ],
)
def test_a_list_is_read_newest_saved_newest_reference_or_by_insured(
    book: QuoteBook, sort: Any, expected: list[str]
) -> None:
    # Capitals with accents, which SQLite's lower() leaves as they are: a page's cursor
    # holds the key as the database ordered it, so paging one at a time skips none.
    for insured in ("zeta", "Alpha", "beta", "Ärzt", "Öresund"):
        _create(book, inputs=_values(insured=insured))
    if sort != "insured":
        expected = ["Q-000005", "Q-000004", *expected]

    seen: list[str] = []
    cursor = None
    while True:
        page = book.page(actor=ANA, view="team", sort=sort, limit=1, cursor=cursor)
        seen += [q.reference for q in page.quotes]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert seen == expected


def test_a_cursor_not_from_the_list_or_a_page_too_large_is_refused(book: QuoteBook) -> None:
    with pytest.raises(QuoteStoreError, match="not one this list gave"):
        book.page(actor=ANA, cursor="not-a-cursor")
    with pytest.raises(QuoteStoreError, match="1 to 200"):
        book.page(actor=ANA, limit=201)


# ── Changes, each a revision ──────────────────────────────────────────────


def test_assigning_writes_a_revision_keeping_the_values_and_price(
    book: QuoteBook, url: str
) -> None:
    quote = _create(book)
    assigned = book.assign(quote.id, version=1, actor=ANA, assignee=JO)

    assert (assigned.assignee, assigned.latest, assigned.stage) == (JO, 2, "priced")
    second = book.revision(quote.id, 2)
    assert (second.response, second.request, second.release) == (RESPONSE, REQUEST, RELEASE)
    assert details_of(second.inputs)["insured"] == "Acme Engineering"
    assert _row(url, quote.id, 1)[5] == ANA
    assert _row(url, quote.id, 2)[5] == JO
    event = book.timeline(quote.id).events[-1]
    assert (event.kind, event.revision, event.detail) == ("assigned", 2, {"from": ANA, "to": JO})


def test_an_archived_quote_takes_no_change_until_restored(book: QuoteBook) -> None:
    quote = _create(book)
    archived = book.archive(quote.id, version=1, actor=ANA)

    assert (archived.archived, archived.latest) == (True, 2)
    full = book.get(quote.id)
    assert full.archived_by == ANA and full.archived_at is not None
    with pytest.raises(QuoteLockedError, match="archived: restore it"):
        _save(book, archived)
    with pytest.raises(QuoteLockedError, match="archived: restore it"):
        book.assign(quote.id, version=2, actor=ANA, assignee=JO)
    with pytest.raises(QuoteLockedError, match="archived: restore it"):
        book.set_note(quote.id, actor=ANA, note="x")
    restored = book.restore(quote.id, version=2, actor=JO)
    assert (restored.archived, book.get(quote.id).archived_by) == (False, None)
    with pytest.raises(QuoteLockedError, match="not archived"):
        book.restore(quote.id, version=3, actor=JO)


def test_final_needs_the_newest_revision_priced_on_the_running_release(book: QuoteBook) -> None:
    unpriced = _create(book, response=None, release=None)
    elsewhere = _create(book, release="release-0")
    quote = _create(book)

    with pytest.raises(QuoteLockedError, match="not priced"):
        book.finalise(unpriced.id, version=1, actor=ANA, revision=1, release=RELEASE)
    with pytest.raises(QuoteLockedError, match="priced on release release-0"):
        book.finalise(elsewhere.id, version=1, actor=ANA, revision=1, release=RELEASE)
    saved = _save(book, quote)
    with pytest.raises(StaleQuoteError, match="moved on to revision 2"):
        book.finalise(quote.id, version=2, actor=ANA, revision=1, release=RELEASE)

    final = book.finalise(quote.id, version=saved.latest, actor=JO, revision=2, release=RELEASE)
    assert (final.stage, final.latest) == ("final", 3)
    full = book.get(quote.id)
    assert (full.final_revision, full.final_by) == (3, JO)
    with pytest.raises(QuoteLockedError, match="is final: reopen it"):
        _save(book, final)
    with pytest.raises(QuoteLockedError, match="final already"):
        book.finalise(quote.id, version=3, actor=ANA, revision=3, release=RELEASE)


def test_reopening_a_final_quote_needs_a_reason_and_keeps_the_final_in_its_history(
    book: QuoteBook,
) -> None:
    quote = _create(book)
    final = book.finalise(quote.id, version=1, actor=ANA, revision=1, release=RELEASE)

    with pytest.raises(QuoteStoreError, match="Give a reason"):
        book.reopen(quote.id, version=final.latest, actor=JO, reason="  ")
    reopened = book.reopen(quote.id, version=final.latest, actor=JO, reason="Broker asked")
    assert (reopened.stage, reopened.latest, book.get(quote.id).final_revision) == (
        "priced",
        3,
        None,
    )
    amended = _save(book, reopened, actor=JO)
    assert amended.latest == 4

    timeline = book.timeline(quote.id)
    assert [(e.kind, e.revision) for e in timeline.events] == [
        ("created", 1),
        ("finalised", 2),
        ("reopened", 3),
        ("saved", 4),
    ]
    assert timeline.events[2].detail == {"reason": "Broker asked", "final_revision": 2}
    assert [(r.number, r.stage) for r in timeline.revisions] == [
        (1, "priced"),
        (2, "final"),
        (3, "priced"),
        (4, "priced"),
    ]
    with pytest.raises(QuoteLockedError, match="not final"):
        book.reopen(quote.id, version=4, actor=JO, reason="again")
    # The final it reopens is the one pinned, whatever was written over it since.
    again = book.finalise(quote.id, version=4, actor=ANA, revision=4, release=RELEASE)
    archived = book.archive(quote.id, version=again.latest, actor=ANA)
    restored = book.restore(quote.id, version=archived.latest, actor=ANA)
    book.reopen(quote.id, version=restored.latest, actor=ANA, reason="Cover changed")
    assert book.timeline(quote.id).events[-1].detail["final_revision"] == 5


def test_an_archived_final_quote_stays_final_when_restored(book: QuoteBook) -> None:
    quote = _create(book)
    final = book.finalise(quote.id, version=1, actor=ANA, revision=1, release=RELEASE)
    archived = book.archive(quote.id, version=final.latest, actor=ANA)
    restored = book.restore(quote.id, version=archived.latest, actor=ANA)

    assert (archived.stage, restored.stage) == ("final", "final")


def test_a_note_is_an_event_not_a_revision(book: QuoteBook) -> None:
    quote = _create(book)

    noted = book.set_note(quote.id, actor=JO, note="  Waiting on the broker ")
    cleared = book.set_note(quote.id, actor=ANA, note="")

    assert (noted.note, noted.note_by, noted.latest) == ("Waiting on the broker", JO, 1)
    assert (cleared.note, cleared.note_by) == ("", None)


# ── Quotes made from quotes ───────────────────────────────────────────────


def test_a_renewal_is_a_new_quote_naming_the_quote_and_revision_it_came_from(
    book: QuoteBook,
) -> None:
    origin = _create(book)
    _save(book, origin)
    renewal = _create(
        book, actor=JO, response=None, release=None, source=Source(origin.id, 1, "renewal")
    )

    assert renewal.reference == "Q-000002"
    full = book.get(renewal.id)
    assert (full.source_quote_id, full.source_reference) == (origin.id, origin.reference)
    assert (full.source_revision, full.relation, full.stage) == (1, "renewal", "draft")
    timeline = book.timeline(origin.id)
    assert [(d.reference, d.relation, d.revision) for d in timeline.derived] == [
        ("Q-000002", "renewal", 1)
    ]
    made = book.timeline(renewal.id)
    assert made.source is not None
    assert (made.source.reference, made.source.relation) == (origin.reference, "renewal")


def test_a_quote_made_from_one_the_app_does_not_keep_is_refused(book: QuoteBook) -> None:
    origin = _create(book)

    with pytest.raises(QuoteNotFoundError):
        _create(book, source=Source("missing", 1, "duplicate"))
    with pytest.raises(QuoteNotFoundError, match="revision 4"):
        _create(book, source=Source(origin.id, 4, "duplicate"))
    assert _create(book).reference == "Q-000002"


def test_people_are_everyone_a_quote_is_with_or_was_saved_by(book: QuoteBook) -> None:
    quote = _create(book)
    book.assign(quote.id, version=1, actor=ANA, assignee="sam@example.com")
    _create(book, actor=JO)

    assert book.people() == [ANA, JO, "sam@example.com"]
