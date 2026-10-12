"""The underwriter app's quote book (specs/workbench): each app quote's record beside the
store's quote, its reference, stage, assignee, archive and final revision, the events of who
did what, a version every change names, operation keys that make a repeated save write once,
the paged lists of the Quotes page, and projection again under a new setting."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import (
    Projection,
    QuoteBook,
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteStoreError,
    Source,
    StaleQuoteError,
    migrate_store,
    open_store,
    store_url,
)
from haute._workbench_form import FormSpec

FORM = FormSpec.model_validate(
    {
        "name": "motor",
        "schema": {
            "tables": [
                {
                    "id": "t_policy",
                    "name": "policy",
                    "rows": "one",
                    "columns": [
                        {"id": "c_insured", "name": "insured", "type": "str"},
                        {"id": "c_region", "name": "region", "type": "str"},
                    ],
                }
            ]
        },
        "pages": [
            {
                "id": "p",
                "title": "Sheet 1",
                "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
            }
        ],
    }
)
VALUES = {"t_policy": [{"c_insured": "Acme Engineering", "c_region": "north"}]}
REQUEST = {"policy": {"insured": "Acme Engineering", "region": "north"}}
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


def _projection(title: str | None = "Acme Engineering", **overrides: Any) -> Projection:
    fields: dict[str, Any] = {"title": title, "fields": ["north"], "headline": 512.4, "key": "k1"}
    return Projection(**{**fields, **overrides})


def _create(book: QuoteBook, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "operation": _op(),
        "actor": ANA,
        "name": "",
        "form": FORM,
        "inputs": VALUES,
        "request": REQUEST,
        "response": RESPONSE,
        "release": RELEASE,
        "projection": _projection(),
    }
    return book.create(**{**fields, **overrides})


def _save(book: QuoteBook, quote: Any, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "operation": _op(),
        "version": quote.version,
        "actor": ANA,
        "form": FORM,
        "inputs": VALUES,
        "request": REQUEST,
        "response": RESPONSE,
        "release": RELEASE,
        "projection": _projection(),
    }
    return book.save(quote.id, **{**fields, **overrides})


def _kinds(book: QuoteBook, quote_id: str) -> list[str]:
    return [event.kind for event in book.timeline(quote_id).events]


# ── Creating and saving ───────────────────────────────────────────────────


def test_a_new_quote_takes_a_reference_and_is_assigned_to_who_made_it(book: QuoteBook) -> None:
    quote = _create(book, name="Fleet renewal")

    assert quote.reference == "Q-000001"
    assert (quote.assignee, quote.owner, quote.last_saved_by) == (ANA, ANA, ANA)
    assert (quote.stage, quote.priced, quote.latest, quote.version) == ("priced", True, 1, 1)
    assert quote.title == "Acme Engineering"
    assert quote.summary == {"title": "Acme Engineering", "fields": ["north"], "headline": 512.4}
    assert quote.release == RELEASE
    assert _kinds(book, quote.id) == ["created"]
    assert book.store.revision(quote.id).inputs == VALUES


def test_the_title_is_the_title_field_else_the_name_else_the_reference(book: QuoteBook) -> None:
    named = _create(book, name="Fleet renewal", projection=_projection(title=None))
    blank = _create(book, name="  ", projection=_projection(title="  "))

    assert named.title == "Fleet renewal"
    assert blank.title == blank.reference == "Q-000002"


def test_a_quote_saved_unpriced_is_a_draft(book: QuoteBook) -> None:
    quote = _create(book, response=None, release=None)

    assert (quote.stage, quote.priced) == ("draft", False)


def test_references_follow_on_and_a_failed_creation_keeps_none(book: QuoteBook) -> None:
    first = _create(book)
    with pytest.raises(QuoteStoreError, match="not as the sheets keep them"):
        _create(book, inputs={"t_policy": [{"c_insured": 7}]})
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


def test_a_creation_repeated_with_its_operation_key_writes_nothing_more(book: QuoteBook) -> None:
    operation = _op()
    first = _create(book, operation=operation)
    again = _create(book, operation=operation)

    assert again.id == first.id
    assert [q.id for q in book.page(actor=ANA).quotes] == [first.id]
    assert _create(book).reference == "Q-000002"


def test_an_operation_key_used_by_someone_else_or_another_quote_is_refused(
    book: QuoteBook,
) -> None:
    operation = _op()
    quote = _create(book, operation=operation)
    other = _create(book)

    with pytest.raises(QuoteStoreError, match="used for another save"):
        _create(book, operation=operation, actor=JO)
    with pytest.raises(QuoteStoreError, match="used for another save"):
        _save(book, other, operation=operation)
    with pytest.raises(QuoteStoreError, match="operation key"):
        _create(book, operation="short")
    assert book.get(quote.id).latest == 1


def test_a_save_makes_the_next_revision_and_moves_the_version_on(
    book: QuoteBook, clock: Clock
) -> None:
    quote = _create(book)
    saved = _save(book, quote, actor=JO, response=None, release=None, projection=_projection("B"))

    assert (saved.latest, saved.version, saved.stage) == (2, 2, "draft")
    assert (saved.last_saved_by, saved.last_saved_at) == (JO, clock.now)
    assert saved.title == "B"
    assert saved.assignee == ANA
    assert book.store.revision(quote.id, 1).response == RESPONSE
    assert _kinds(book, quote.id) == ["created", "saved"]


def test_a_save_repeated_with_its_operation_key_writes_one_revision(book: QuoteBook) -> None:
    quote = _create(book)
    operation = _op()
    first = _save(book, quote, operation=operation)
    again = _save(book, quote, operation=operation)

    assert again.latest == first.latest == 2
    assert again.version == first.version


def test_a_save_over_a_version_the_quote_has_moved_past_is_refused_naming_who(
    book: QuoteBook,
) -> None:
    quote = _create(book)
    _save(book, quote, actor=JO)

    with pytest.raises(StaleQuoteError, match=r"Q-000001 has changed.*jo@example.com saved it"):
        _save(book, quote)
    assert book.get(quote.id).latest == 2


def test_a_test_quote_has_no_record_and_is_not_listed(book: QuoteBook) -> None:
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
    assert book.page(actor=ANA, view="team").quotes == []


# ── Lists ─────────────────────────────────────────────────────────────────


def test_the_lists_are_mine_the_teams_and_the_archive_with_their_counts(
    book: QuoteBook,
) -> None:
    mine = _create(book)
    theirs = _create(book, actor=JO, response=None, release=None)
    gone = _create(book)
    book.archive(gone.id, version=gone.version, actor=ANA)

    assert [q.id for q in book.page(actor=ANA, view="mine").quotes] == [mine.id]
    assert [q.id for q in book.page(actor=ANA, view="team").quotes] == [theirs.id, mine.id]
    assert [q.id for q in book.page(actor=ANA, view="archived").quotes] == [gone.id]
    counts = book.page(actor=ANA, view="team").counts
    assert counts == {"mine": 1, "team": 2, "archived": 1, "draft": 1, "priced": 1, "final": 0}
    assert [q.id for q in book.page(actor=ANA, view="team", stage="draft").quotes] == [theirs.id]
    assert [q.id for q in book.page(actor=ANA, view="team", assignee=JO).quotes] == [theirs.id]


def test_a_search_matches_every_word_in_the_reference_name_title_or_summary(
    book: QuoteBook,
) -> None:
    acme = _create(book, name="Fleet")
    harbour = _create(book, projection=_projection("Harbour Trading", fields=["south"]))

    def found(search: str) -> list[str]:
        return [q.reference for q in book.page(actor=ANA, view="team", search=search).quotes]

    assert found("acme") == [acme.reference]
    assert found("TRADING south") == [harbour.reference]
    assert found("q-000002") == [harbour.reference]
    assert found("fleet north") == [acme.reference]
    assert found("acme south") == []
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
        ("title", ["Q-000002", "Q-000003", "Q-000001"]),
    ],
)
def test_a_list_is_read_newest_saved_newest_reference_or_by_title(
    book: QuoteBook, sort: Any, expected: list[str]
) -> None:
    for title in ("zeta", "Alpha", "beta"):
        _create(book, projection=_projection(title))

    def pages() -> list[str]:
        seen: list[str] = []
        cursor = None
        while True:
            page = book.page(actor=ANA, view="team", sort=sort, limit=1, cursor=cursor)
            seen += [q.reference for q in page.quotes]
            if page.next_cursor is None:
                return seen
            cursor = page.next_cursor

    assert pages() == expected


def test_a_cursor_not_from_the_list_or_a_page_too_large_is_refused(book: QuoteBook) -> None:
    with pytest.raises(QuoteStoreError, match="not one this list gave"):
        book.page(actor=ANA, cursor="not-a-cursor")
    with pytest.raises(QuoteStoreError, match="1 to 200"):
        book.page(actor=ANA, limit=201)


# ── Changes ───────────────────────────────────────────────────────────────


def test_renaming_assigning_and_a_note_each_move_the_version_with_an_event(
    book: QuoteBook,
) -> None:
    quote = _create(book, projection=_projection(title=None))
    renamed = book.rename(quote.id, version=1, actor=ANA, name="Fleet")
    assigned = book.assign(quote.id, version=2, actor=ANA, assignee=JO)
    noted = book.set_note(quote.id, version=3, actor=JO, note="  Waiting on the broker ")
    cleared = book.set_note(quote.id, version=4, actor=JO, note="")

    assert (renamed.name, renamed.title) == ("Fleet", "Fleet")
    assert assigned.assignee == JO
    assert (noted.note, noted.note_by) == ("Waiting on the broker", JO)
    assert (cleared.note, cleared.note_by, cleared.version) == ("", None, 5)
    assert book.page(actor=ANA, view="team", search="fleet").quotes[0].id == quote.id
    events = book.timeline(quote.id).events
    assert [e.kind for e in events] == ["created", "renamed", "assigned", "noted", "noted"]
    assert events[2].detail == {"from": ANA, "to": JO}
    with pytest.raises(StaleQuoteError):
        book.rename(quote.id, version=1, actor=ANA, name="Again")


def test_an_archived_quote_takes_no_change_until_restored(book: QuoteBook) -> None:
    quote = _create(book)
    archived = book.archive(quote.id, version=1, actor=ANA)

    assert (archived.archived_by, archived.version) == (ANA, 2)
    with pytest.raises(QuoteLockedError, match="archived: restore it"):
        _save(book, archived)
    with pytest.raises(QuoteLockedError, match="archived: restore it"):
        book.rename(quote.id, version=2, actor=ANA, name="x")
    restored = book.restore(quote.id, version=2, actor=JO)
    assert restored.archived_at is None
    with pytest.raises(QuoteLockedError, match="not archived"):
        book.restore(quote.id, version=3, actor=JO)


def test_final_pins_the_newest_priced_revision_on_the_running_release(book: QuoteBook) -> None:
    unpriced = _create(book, response=None, release=None)
    elsewhere = _create(book, release="release-0")
    quote = _create(book)

    with pytest.raises(QuoteLockedError, match="not priced"):
        book.finalise(unpriced.id, version=1, actor=ANA, revision=1, release=RELEASE)
    with pytest.raises(QuoteLockedError, match="priced on release release-0"):
        book.finalise(elsewhere.id, version=1, actor=ANA, revision=1, release=RELEASE)
    saved = _save(book, quote)
    with pytest.raises(StaleQuoteError, match="moved on to revision 2"):
        book.finalise(quote.id, version=saved.version, actor=ANA, revision=1, release=RELEASE)

    final = book.finalise(quote.id, version=saved.version, actor=JO, revision=2, release=RELEASE)
    assert (final.stage, final.final_revision, final.final_by) == ("final", 2, JO)
    with pytest.raises(QuoteLockedError, match="is final: reopen it"):
        _save(book, final)
    with pytest.raises(QuoteLockedError, match="final at revision 2 already"):
        book.finalise(quote.id, version=final.version, actor=ANA, revision=2, release=RELEASE)


def test_reopening_a_final_quote_needs_a_reason_and_keeps_the_final_in_its_history(
    book: QuoteBook,
) -> None:
    quote = _create(book)
    final = book.finalise(quote.id, version=1, actor=ANA, revision=1, release=RELEASE)

    with pytest.raises(QuoteStoreError, match="Give a reason"):
        book.reopen(quote.id, version=final.version, actor=JO, reason="  ")
    reopened = book.reopen(quote.id, version=final.version, actor=JO, reason="Broker asked")
    assert (reopened.stage, reopened.final_revision) == ("priced", None)
    amended = _save(book, reopened, actor=JO)
    assert amended.latest == 2

    events = book.timeline(quote.id).events
    assert [(e.kind, e.revision) for e in events] == [
        ("created", 1),
        ("finalised", 1),
        ("reopened", 1),
        ("saved", 2),
    ]
    assert events[2].detail == {"reason": "Broker asked", "final_revision": 1}
    with pytest.raises(QuoteLockedError, match="not final"):
        book.reopen(quote.id, version=amended.version, actor=JO, reason="again")


def test_an_archived_final_quote_stays_final_when_restored(book: QuoteBook) -> None:
    quote = _create(book)
    final = book.finalise(quote.id, version=1, actor=ANA, revision=1, release=RELEASE)
    archived = book.archive(quote.id, version=final.version, actor=ANA)
    restored = book.restore(quote.id, version=archived.version, actor=ANA)

    assert (archived.stage, restored.stage) == ("final", "final")


# ── Quotes made from quotes ───────────────────────────────────────────────


def test_a_renewal_is_a_new_quote_naming_the_quote_and_revision_it_came_from(
    book: QuoteBook,
) -> None:
    origin = _create(book)
    _save(book, origin)
    renewal = _create(
        book,
        actor=JO,
        response=None,
        release=None,
        source=Source(origin.id, 1, "renewal"),
    )

    assert renewal.reference == "Q-000002"
    assert (renewal.source_quote_id, renewal.source_reference) == (origin.id, origin.reference)
    assert (renewal.source_revision, renewal.relation, renewal.stage) == (1, "renewal", "draft")
    timeline = book.timeline(origin.id)
    assert [(d.reference, d.relation, d.revision) for d in timeline.derived] == [
        ("Q-000002", "renewal", 1)
    ]
    assert timeline.events[-1].kind == "derived"
    assert timeline.events[-1].detail["reference"] == "Q-000002"
    made = book.timeline(renewal.id)
    assert made.source is not None
    assert (made.source.reference, made.source.relation) == (origin.reference, "renewal")
    assert made.events[0].detail["source"]["reference"] == origin.reference


def test_a_quote_made_from_one_the_app_does_not_keep_is_refused(book: QuoteBook) -> None:
    origin = _create(book)

    with pytest.raises(QuoteNotFoundError):
        _create(book, source=Source("missing", 1, "duplicate"))
    with pytest.raises(QuoteNotFoundError, match="revision 4"):
        _create(book, source=Source(origin.id, 4, "duplicate"))
    assert _create(book).reference == "Q-000002"


# ── History, people and projection ────────────────────────────────────────


def test_the_timeline_lists_revisions_in_order_with_who_saved_them(book: QuoteBook) -> None:
    quote = _create(book, response=None, release=None)
    _save(book, quote, actor=JO)

    revisions = book.timeline(quote.id).revisions
    assert [(r.number, r.saved_by, r.priced, r.release) for r in revisions] == [
        (1, ANA, False, None),
        (2, JO, True, RELEASE),
    ]


def test_people_are_everyone_a_quote_is_with_or_was_made_or_saved_by(book: QuoteBook) -> None:
    quote = _create(book)
    book.assign(quote.id, version=1, actor=ANA, assignee="sam@example.com")
    _create(book, actor=JO)

    assert book.people() == [ANA, JO, "sam@example.com"]


def test_reprojection_rewrites_what_the_list_shows_and_nothing_else(book: QuoteBook) -> None:
    quote = _create(book)
    current = _create(book, projection=_projection(key="k2"))

    def project(revision: Any) -> Projection:
        return Projection(title="Projected", fields=[revision.number], headline=1, key="k2")

    assert book.reproject("k2", project) == 1
    again = book.get(quote.id)
    assert (again.title, again.summary["fields"], again.version) == ("Projected", [1], 1)
    assert (again.last_saved_at, again.latest) == (quote.last_saved_at, 1)
    assert _kinds(book, quote.id) == ["created"]
    assert book.get(current.id).title == "Acme Engineering"
    assert book.reproject("k2", project) == 0


def test_a_reprojection_giving_another_setting_is_refused(book: QuoteBook) -> None:
    _create(book)

    with pytest.raises(QuoteStoreError, match="another setting"):
        book.reproject("k2", lambda _revision: Projection(key="k3"))
