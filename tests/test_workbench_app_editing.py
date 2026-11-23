"""Who is editing each quote in the underwriter app (specs/workbench): one person at a time, a
lease the page renews while the quote is open to edit, lapsing when it stops, and every change
from anyone else refused while it is held."""

# ruff: noqa: F811 - the fixtures are imported by name, as pytest takes them
from __future__ import annotations

from typing import Any

from haute._workbench_app_editing import EDIT_LEASE_SECONDS, EditLeases
from tests.test_workbench_app_quotes import (  # noqa: F401 - the fixtures the routes run on
    ANA,
    JO,
    Page,
    address,
    client,
    page,
    path,
    release_dir,
    url,
)

QUOTE = ("Q-000001", 1)


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_quote_is_held_by_one_editor_until_they_let_it_go_or_its_lease_lapses() -> None:
    clock = Clock()
    leases = EditLeases(clock)

    assert leases.take(QUOTE, "ana") is None
    # Held: the same editor renews it, from any tab; anyone else is told who holds it.
    assert leases.take(QUOTE, "ana") is None
    assert leases.take(QUOTE, "jo") == "ana"
    assert (leases.other(QUOTE, "ana"), leases.other(QUOTE, "jo")) == (None, "ana")
    # Only its editor lets it go.
    leases.release(QUOTE, "jo")
    assert leases.editor(QUOTE) == "ana"
    leases.release(QUOTE, "ana")
    assert leases.take(QUOTE, "jo") is None

    # Not renewed, a lease lapses, and the quote is free for anyone.
    clock.now += EDIT_LEASE_SECONDS - 1
    assert leases.editor(QUOTE) == "jo"
    clock.now += 1
    assert leases.editor(QUOTE) is None
    assert leases.take(QUOTE, "ana") is None
    # Each quote is held on its own.
    assert leases.take(("Q-000001", 2), "jo") is None


def _edit(page: Page, quote: dict[str, Any], headers: dict[str, str]) -> Any:
    return page.client.post(f"{address(quote)}/edit", json={}, headers=headers)


def test_a_quote_held_by_one_editor_takes_no_save_or_change_from_anyone_else(page: Page) -> None:
    quote = page.create().json()["quote"]

    held = _edit(page, quote, ANA)
    assert held.status_code == 200, held.text
    assert held.json() == {"editor": "ana@example.com", "lease_seconds": EDIT_LEASE_SECONDS}
    refused = _edit(page, quote, JO)
    assert refused.status_code == 409
    assert refused.json()["detail"] == {
        "message": "ana@example.com is editing Q-000001/1.",
        "error_code": "quote_being_edited",
        "editor": "ana@example.com",
    }

    # Every change of Jo's is refused, naming who is editing it; Ana's go through.
    for answered in (
        page.save(quote, headers=JO, shown=None),
        page.client.post(f"{address(quote)}/archive", json={"version": 1}, headers=JO),
        page.client.post(f"{address(quote)}/note", json={"note": "Call back"}, headers=JO),
        page.client.post(
            f"{address(quote)}/assignee", json={"version": 1, "assignee": "jo"}, headers=JO
        ),
    ):
        assert answered.status_code == 409, answered.text
        assert answered.json()["detail"]["error_code"] == "quote_being_edited"
    saved = page.save(quote, shown=None)
    assert saved.status_code == 200, saved.text

    # Let go, the quote takes anyone's change, and anyone may hold it.
    gone = page.client.post(f"{address(quote)}/edit/release", json={}, headers=ANA)
    assert gone.json() == {"editor": None, "lease_seconds": EDIT_LEASE_SECONDS}
    assert page.client.post(f"{address(quote)}/note", json={"note": "x"}, headers=JO).is_success
    assert _edit(page, quote, JO).status_code == 200


def test_the_release_names_the_environment_its_quotes_are_kept_in(page: Page) -> None:
    assert page.client.get("/api/app/release", headers=ANA).json()["environment"] == "test"


def test_a_quote_no_one_holds_takes_anyones_change_and_a_missing_one_cannot_be_held(
    page: Page,
) -> None:
    quote = page.create().json()["quote"]

    assert page.save(quote, headers=JO, shown=None).status_code == 200
    missing = page.client.post("/api/app/quotes/Q-999999/1/edit", json={}, headers=ANA)
    assert missing.status_code == 404
    # A release of what the asker does not hold lets nothing go.
    assert _edit(page, quote, ANA).status_code == 200
    page.client.post(f"{address(quote)}/edit/release", json={}, headers=JO)
    assert _edit(page, quote, JO).status_code == 409
