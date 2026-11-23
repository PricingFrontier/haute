"""Who is editing each quote in the underwriter app (specs/workbench): one person at a time, a
lease each page renews while the quote is open to edit, lapsing when it stops, and every change
from anyone else refused while it is held."""

# ruff: noqa: F811 - the fixtures are imported by name, as pytest takes them
from __future__ import annotations

from typing import Any

import pytest

from haute._workbench_app_editing import EDIT_LEASE_SECONDS, EditHeldError, EditLeases
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
TAB = "session-tab-one"
OTHER_TAB = "session-tab-two"


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_quote_is_held_by_one_editor_until_they_let_it_go_or_its_lease_lapses() -> None:
    clock = Clock()
    leases = EditLeases(clock)

    assert leases.take(QUOTE, "ana", TAB) is None
    # Held: the same editor renews it; anyone else is told who holds it.
    assert leases.take(QUOTE, "ana", TAB) is None
    assert leases.take(QUOTE, "jo", TAB) == "ana"
    # Only its editor lets it go.
    leases.release(QUOTE, "jo", TAB)
    assert leases.editor(QUOTE) == "ana"
    leases.release(QUOTE, "ana", TAB)
    assert leases.take(QUOTE, "jo", TAB) is None

    # Not renewed, a lease lapses, and the quote is free for anyone.
    clock.now += EDIT_LEASE_SECONDS - 1
    assert leases.editor(QUOTE) == "jo"
    clock.now += 1
    assert leases.editor(QUOTE) is None
    assert leases.take(QUOTE, "ana", TAB) is None
    # Each quote is held on its own.
    assert leases.take(("Q-000001", 2), "jo", TAB) is None


def test_one_tab_leaving_or_lapsing_lets_go_of_no_other_tabs_hold() -> None:
    clock = Clock()
    leases = EditLeases(clock)
    leases.take(QUOTE, "ana", TAB)
    clock.now += 100
    leases.take(QUOTE, "ana", OTHER_TAB)

    # The first tab closes: the quote is still Ana's, by the other.
    leases.release(QUOTE, "ana", TAB)
    assert leases.take(QUOTE, "jo", TAB) == "ana"
    # A late release of a session since gone lets go of nothing newer.
    leases.release(QUOTE, "ana", TAB)
    assert leases.editor(QUOTE) == "ana"
    # The first tab's lease would have lapsed by now; the other's has not.
    clock.now += 100
    assert leases.editor(QUOTE) == "ana"
    leases.release(QUOTE, "ana", OTHER_TAB)
    assert leases.editor(QUOTE) is None


def test_a_write_holds_the_quote_for_its_actor_until_it_ends() -> None:
    leases = EditLeases(Clock())

    # Ana's save is under way on a quote no one is editing: Jo cannot take it meanwhile, nor
    # change it, and may once it ends.
    with leases.writing(QUOTE, "ana"):
        assert leases.take(QUOTE, "jo", TAB) == "ana"
        with pytest.raises(EditHeldError) as held, leases.writing(QUOTE, "jo"):
            pass
        assert held.value.editor == "ana"
        # Ana's own writes and her page's lease go on.
        with leases.writing(QUOTE, "ana"):
            assert leases.take(QUOTE, "ana", TAB) is None
    assert leases.editor(QUOTE) == "ana"
    leases.release(QUOTE, "ana", TAB)
    assert leases.take(QUOTE, "jo", TAB) is None

    # Held by Jo, the quote takes no write of Ana's, and a refused write holds nothing.
    with pytest.raises(EditHeldError), leases.writing(QUOTE, "ana"):
        pass
    leases.release(QUOTE, "jo", TAB)
    assert leases.editor(QUOTE) is None


def _edit(page: Page, quote: dict[str, Any], headers: dict[str, str], session: str = TAB) -> Any:
    return page.client.post(f"{address(quote)}/edit", json={"session": session}, headers=headers)


def _release(page: Page, quote: dict[str, Any], headers: dict[str, str], session: str = TAB) -> Any:
    return page.client.post(
        f"{address(quote)}/edit/release", json={"session": session}, headers=headers
    )


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

    # A second tab of Ana's holds it too: the first leaving does not free it.
    assert _edit(page, quote, ANA, OTHER_TAB).status_code == 200
    assert _release(page, quote, ANA).json() == {
        "editor": None,
        "lease_seconds": EDIT_LEASE_SECONDS,
    }
    assert _edit(page, quote, JO).status_code == 409
    # Let go by the last, the quote takes anyone's change, and anyone may hold it.
    _release(page, quote, ANA, OTHER_TAB)
    assert page.client.post(f"{address(quote)}/note", json={"note": "x"}, headers=JO).is_success
    assert _edit(page, quote, JO).status_code == 200


def test_the_release_names_the_environment_its_quotes_are_kept_in(page: Page) -> None:
    assert page.client.get("/api/app/release", headers=ANA).json()["environment"] == "test"


def test_a_quote_no_one_holds_takes_anyones_change_and_a_missing_one_cannot_be_held(
    page: Page,
) -> None:
    quote = page.create().json()["quote"]

    assert page.save(quote, headers=JO, shown=None).status_code == 200
    missing = page.client.post(
        "/api/app/quotes/Q-999999/1/edit", json={"session": TAB}, headers=ANA
    )
    assert missing.status_code == 404
    # A page names its session, and a release of what the asker does not hold lets nothing go.
    assert page.client.post(f"{address(quote)}/edit", json={}, headers=ANA).status_code == 422
    assert _edit(page, quote, ANA).status_code == 200
    _release(page, quote, JO)
    assert _edit(page, quote, JO).status_code == 409
