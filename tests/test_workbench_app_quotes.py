"""The underwriter app's quotes (specs/workbench): the routes that list, open, save and change
the quotes the app keeps in its quote store, a save priced again on the server, the version
every change names, the release a page was drawn for, writes from other sites refused, and
``haute app serve`` and ``haute app migrate`` with the store."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute import _workbench_app_quotes
from haute._quote_store import (
    QuoteStoreNotMigratedError,
    migrate_store,
    open_store,
    store_url,
)
from haute._sandbox import _get_project_root, set_project_root
from haute._workbench_app import (
    EASY_AUTH_NAME,
    EASY_AUTH_PRINCIPAL,
    STORE_URL_ENV,
    create_app,
    easy_auth_roles,
    write_release,
)
from haute._workbench_form import FormSpec, with_quote_details
from haute.deploy._utils import shipped_graph
from tests.test_workbench_app import _graph
from tests.test_workbench_routes import POLICY, PRICING, form

ANA = {EASY_AUTH_NAME: "ana@example.com"}
JO = {EASY_AUTH_NAME: "jo@example.com"}
DETAILS = {"qd_quote_name": "Fleet", "qd_insured": "Acme", "qd_inception_date": "2026-11-01"}
#: The details the app sets on a first save by Ana, priced.
SET = {"qd_reference": "Q-000001", "qd_stage": "priced", "qd_assignee": "ana@example.com"}
VALUES = {"t1": [{"c_state": "NY", "c_exposure": "1000"}], "quote_details": [DETAILS]}
#: VALUES as a priced quote of Ana's keeps them: the details with the app's set over them.
KEPT = {**VALUES, "quote_details": [{**DETAILS, **SET, "qd_archived": False}]}
PRICED = {"pricing_output": [{"premium": 500.0}]}
FORM = with_quote_details(FormSpec.model_validate(form(POLICY, PRICING))).model_dump(
    mode="json", by_alias=True
)


def _write_release(
    directory: Path, spec: dict[str, Any] = FORM, edit_role: str | None = None
) -> Path:
    directory.mkdir(exist_ok=True)
    manifest = {
        "haute_version": "9.9.9",
        "pipeline_name": "motor",
        "created_at": "2026-10-12T09:00:00+00:00",
        "created_by": "analyst",
        "input_node_ids": ["request"],
        "output_node_id": "response",
        "output_fields": None,
        "artifacts": {},
        "pruned_graph": shipped_graph(_graph()),
    }
    (directory / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_release(directory, FormSpec.model_validate(spec), "azure-container-apps", edit_role)
    return directory


@pytest.fixture()
def url(tmp_path: Path) -> str:
    url = store_url(tmp_path / "quotes.sqlite")
    migrate_store(url)
    return url


@pytest.fixture()
def release_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    yield _write_release(tmp_path / "release")
    set_project_root(original)


@pytest.fixture()
def client(release_dir: Path, url: str) -> Iterator[TestClient]:
    with TestClient(create_app(release_dir, store_url=url)) as opened:
        yield opened


class Page:
    """What the page knows: the sheets' revision and the release it was drawn for."""

    def __init__(self, client: TestClient) -> None:
        release = client.get("/api/app/release", headers=ANA).json()
        self.client = client
        self.revision = release["form_revision"]
        self.release = release["id"]
        self.operations = iter(range(10**6))

    def body(self, **fields: Any) -> dict[str, Any]:
        return {
            "operation": f"operation-{next(self.operations):06d}",
            "form_revision": self.revision,
            "release": self.release,
            "values": VALUES,
            "shown": PRICED,
            **fields,
        }

    def create(self, headers: dict[str, str] = ANA, **fields: Any) -> Any:
        return self.client.post("/api/app/quotes", json=self.body(**fields), headers=headers)

    def save(self, quote: dict[str, Any], headers: dict[str, str] = ANA, **fields: Any) -> Any:
        body = self.body(version=quote["version"], **fields)
        return self.client.post(
            f"/api/app/quotes/{quote['id']}/revisions", json=body, headers=headers
        )

    def change(
        self, quote: dict[str, Any], what: str, headers: dict[str, str] = ANA, **fields: Any
    ) -> Any:
        body = {"version": quote["version"], **fields}
        return self.client.post(f"/api/app/quotes/{quote['id']}/{what}", json=body, headers=headers)


@pytest.fixture()
def page(client: TestClient) -> Page:
    return Page(client)


# ── Saving and opening ────────────────────────────────────────────────────


def test_the_release_says_the_app_keeps_quotes(client: TestClient, release_dir: Path) -> None:
    assert client.get("/api/app/release", headers=ANA).json()["quotes"] is True
    with TestClient(create_app(release_dir)) as storeless:
        assert storeless.get("/api/app/release", headers=ANA).json()["quotes"] is False
        refused = storeless.get("/api/app/quotes", headers=ANA)
        assert refused.status_code == 503
        assert STORE_URL_ENV in refused.json()["detail"]


def test_a_priced_save_is_priced_again_on_the_server_and_kept(page: Page) -> None:
    sent = {**VALUES, "quote_details": [{**DETAILS, "qd_assignee": "jo", "qd_stage": "final"}]}
    created = page.create(values=sent)

    assert created.status_code == 200, created.text
    answered = created.json()
    quote = answered["quote"]
    assert (quote["reference"], quote["stage"], quote["assignee"]) == (
        "Q-000001",
        "priced",
        "ana@example.com",
    )
    assert quote["title"] == "Fleet"
    assert quote["details"] == {
        "reference": "Q-000001",
        "quote_name": "Fleet",
        "insured": "Acme",
        "inception_date": "2026-11-01",
        "stage": "priced",
        "assignee": "ana@example.com",
        "archived": False,
    }
    # The values read back hold the details, the app's set over what the page sent.
    assert answered["revision"]["values"]["quote_details"][0]["qd_stage"] == "priced"
    assert answered["revision"]["tables"] == PRICED
    assert answered["revision"]["release"] == page.release
    assert answered["moved"] is False
    assert answered["form"] is None


def test_a_save_whose_prices_moved_on_the_server_keeps_the_servers_and_says_so(
    page: Page,
) -> None:
    created = page.create(shown={"pricing_output": [{"premium": 1.0}]}).json()

    assert created["moved"] is True
    assert created["revision"]["tables"] == PRICED


def test_a_quote_saved_unpriced_is_a_draft_and_an_empty_one_is_refused(page: Page) -> None:
    draft = page.create(shown=None).json()

    assert (draft["quote"]["stage"], draft["revision"]["tables"]) == ("draft", None)
    # The details the app sets are not typing, whatever the page sends of them.
    for values in ({}, {"quote_details": [{"qd_reference": "Q-9", "qd_stage": "final"}]}):
        refused = page.create(shown=None, values=values)
        assert refused.status_code == 422
        assert refused.json()["detail"] == "Type the quote first"


def test_a_save_from_a_page_of_another_release_is_refused(page: Page) -> None:
    for fields in ({"release": "another"}, {"form_revision": "0" * 64}):
        refused = page.create(**fields)
        assert refused.status_code == 409
        assert "reload it" in refused.json()["detail"]
    assert page.client.get("/api/app/quotes?view=team", headers=ANA).json()["quotes"] == []


def test_a_quote_opens_with_its_newest_revision_and_each_revision_by_number(page: Page) -> None:
    quote = page.create(shown=None).json()["quote"]
    page.save(quote, headers=JO)

    opened = page.client.get(f"/api/app/quotes/{quote['id']}", headers=ANA).json()
    first = page.client.get(f"/api/app/quotes/{quote['id']}/revisions/1", headers=ANA).json()

    assert (opened["revision"]["number"], opened["revision"]["saved_by"]) == (2, "jo@example.com")
    assert opened["revision"]["values"] == KEPT
    assert (first["revision"]["number"], first["revision"]["tables"]) == (1, None)
    missing = page.client.get("/api/app/quotes/nope", headers=ANA)
    assert missing.status_code == 404
    assert missing.json()["detail"]["error_code"] == "quote_not_found"


def test_a_save_over_a_quote_moved_on_is_refused_with_the_quote_as_it_is(page: Page) -> None:
    quote = page.create().json()["quote"]
    page.save(quote, headers=JO)

    refused = page.save(quote)

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["error_code"] == "stale_quote_revision"
    assert "jo@example.com saved it" in detail["message"]
    assert (detail["quote"]["latest"], detail["quote"]["version"]) == (2, 2)


def test_a_save_repeated_with_its_operation_key_writes_one_revision(page: Page) -> None:
    quote = page.create().json()["quote"]
    body = page.body(version=quote["version"])

    for _ in range(2):
        saved = page.client.post(f"/api/app/quotes/{quote['id']}/revisions", json=body, headers=ANA)
        assert saved.status_code == 200
    assert saved.json()["quote"]["latest"] == 2


def test_a_save_repeated_after_a_colleague_saved_answers_what_its_key_wrote(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    quote = page.create().json()["quote"]
    body = page.body(version=quote["version"])
    first = page.client.post(f"/api/app/quotes/{quote['id']}/revisions", json=body, headers=ANA)
    assert page.save(first.json()["quote"], headers=JO).json()["quote"]["latest"] == 3

    def priced_again(*_: Any) -> Any:
        raise AssertionError("a save repeated with its key is neither priced nor written again")

    monkeypatch.setattr(_workbench_app_quotes, "_prepared", priced_again)
    shown = {"pricing_output": [{"premium": 1.0}]}
    again = page.client.post(
        f"/api/app/quotes/{quote['id']}/revisions", json={**body, "shown": shown}, headers=ANA
    )

    assert again.status_code == 200, again.text
    answered = again.json()
    # What the key wrote: revision 2 at version 2, so a save over it is refused as stale
    # rather than writing over Jo's; moved said of the prices that write kept.
    assert (answered["quote"]["version"], answered["revision"]["number"]) == (2, 2)
    assert answered["revision"]["saved_by"] == "ana@example.com"
    assert answered["moved"] is True


# ── Lists, history and people ─────────────────────────────────────────────


def test_the_lists_page_search_and_count_through_the_route(page: Page) -> None:
    mine = page.create().json()["quote"]
    page.create(
        headers=JO,
        values={
            "t1": [{"c_state": "CA", "c_exposure": "5"}],
            "quote_details": [{"qd_insured": "Cal"}],
        },
        shown=None,
    )

    listed = page.client.get("/api/app/quotes", headers=ANA).json()
    team = page.client.get("/api/app/quotes?view=team&limit=1", headers=ANA).json()
    found = page.client.get("/api/app/quotes?view=team&search=cal&stage=draft", headers=ANA)

    assert [q["id"] for q in listed["quotes"]] == [mine["id"]]
    assert listed["counts"]["team"] == 2
    assert len(team["quotes"]) == 1 and team["next_cursor"] is not None
    following = page.client.get(
        "/api/app/quotes", params={"view": "team", "cursor": team["next_cursor"]}, headers=ANA
    ).json()
    assert following["quotes"][0]["reference"] == "Q-000001"
    assert [q["title"] for q in found.json()["quotes"]] == ["Cal"]
    bad = page.client.get("/api/app/quotes?cursor=x", headers=ANA)
    assert bad.status_code == 422
    assert bad.json()["detail"]["error_code"] == "quote_store_invalid"


def test_the_history_lists_revisions_events_and_related_quotes(page: Page) -> None:
    quote = page.create().json()["quote"]
    renewal = page.create(
        headers=JO,
        shown=None,
        source={"quote_id": quote["id"], "revision": 1, "relation": "renewal"},
    ).json()["quote"]

    history = page.client.get(f"/api/app/quotes/{quote['id']}/history", headers=ANA).json()
    made = page.client.get(f"/api/app/quotes/{renewal['id']}/history", headers=ANA).json()

    assert [e["kind"] for e in history["events"]] == ["created", "derived"]
    assert history["derived"][0]["reference"] == renewal["reference"]
    assert made["source"]["reference"] == quote["reference"]
    assert renewal["source_reference"] == quote["reference"]
    assert [r["number"] for r in history["revisions"]] == [1]
    people = page.client.get("/api/app/people", headers=ANA).json()["people"]
    assert people == ["ana@example.com", "jo@example.com"]


def test_a_revision_is_carried_onto_the_releases_sheets(page: Page) -> None:
    quote = page.create().json()["quote"]

    carried = page.client.get(
        f"/api/app/quotes/{quote['id']}/revisions/1/carry", headers=ANA
    ).json()

    assert carried == {"values": KEPT, "dropped": []}


# ── Changes ───────────────────────────────────────────────────────────────


def test_changes_through_the_routes_name_the_revision_they_were_made_on(page: Page) -> None:
    quote = page.create().json()["quote"]

    made = page.change(quote, "assignee", assignee="jo@example.com").json()
    assigned = made["quote"]
    written = page.client.post(
        f"/api/app/quotes/{quote['id']}/note", json={"note": "Waiting on the broker"}, headers=ANA
    ).json()
    noted = written["quote"]
    stale = page.change(quote, "assignee", assignee="sam@example.com")

    # The change answers with the revision it wrote: the values kept, the details changed.
    assert made["revision"]["number"] == 2
    assert made["revision"]["values"]["quote_details"][0]["qd_assignee"] == "jo@example.com"
    assert made["revision"]["values"]["t1"] == VALUES["t1"]
    assert written["revision"] is None

    assert (assigned["assignee"], assigned["latest"], assigned["version"]) == (
        "jo@example.com",
        2,
        2,
    )
    assert (noted["note"], noted["latest"]) == ("Waiting on the broker", 2)
    assert stale.status_code == 409
    assert stale.json()["detail"]["quote"]["version"] == 2
    history = page.client.get(f"/api/app/quotes/{quote['id']}/history", headers=ANA).json()
    assert [r["number"] for r in history["revisions"]] == [1, 2]


def test_final_archive_restore_and_reopen_through_the_routes(page: Page) -> None:
    quote = page.create().json()["quote"]

    final = page.change(quote, "final", revision=1).json()["quote"]
    locked = page.save(final)
    archived = page.change(final, "archive").json()["quote"]
    restored = page.change(archived, "restore").json()["quote"]
    unreasoned = page.change(restored, "reopen", reason="")
    reopened = page.change(restored, "reopen", reason="Broker asked").json()["quote"]

    assert (final["stage"], final["final_revision"]) == ("final", 2)
    assert locked.status_code == 409
    assert locked.json()["detail"]["error_code"] == "quote_locked"
    assert archived["archived"] and archived["archived_at"] is not None
    assert (restored["archived"], restored["stage"]) == (False, "final")
    assert unreasoned.status_code == 422
    assert reopened["stage"] == "priced"
    assert page.save(reopened).status_code == 200


def test_a_draft_cannot_be_marked_final(page: Page) -> None:
    quote = page.create(shown=None).json()["quote"]

    refused = page.change(quote, "final", revision=1)

    assert refused.status_code == 409
    assert "not priced" in refused.json()["detail"]["message"]


# ── The app's guards and pages ────────────────────────────────────────────


def test_a_write_from_a_page_of_another_site_is_refused(page: Page) -> None:
    cross = page.create(headers={**ANA, "sec-fetch-site": "cross-site"})
    foreign = page.create(headers={**ANA, "origin": "https://elsewhere.example"})
    origin = str(page.client.base_url).rstrip("/")
    own = page.create(headers={**ANA, "origin": origin})
    # Behind a proxy that passes the app another host, the browser's word is taken.
    proxied = page.create(
        headers={**ANA, "sec-fetch-site": "same-origin", "origin": "https://quotes.example.com"}
    )

    assert (cross.status_code, foreign.status_code) == (403, 403)
    assert (own.status_code, proxied.status_code) == (200, 200), own.text
    read = page.client.get("/api/app/quotes", headers={**ANA, "sec-fetch-site": "cross-site"})
    assert read.status_code == 200


def test_the_apps_pages_are_its_one_page(release_dir: Path, url: str, tmp_path: Path) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "app.html").write_text("<div id=app></div>", encoding="utf-8")

    with TestClient(create_app(release_dir, store_url=url, static_dir=static)) as client:
        for path in ("/", "/quotes/new", "/quotes/abc", "/quotes/abc/revisions/2"):
            assert client.get(path, headers=ANA).text == "<div id=app></div>", path
        assert client.get("/quotes/abc").status_code == 401


def test_the_app_does_not_start_on_a_store_not_migrated(release_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(QuoteStoreNotMigratedError, match="migrate_store"):
        create_app(release_dir, store_url=store_url(tmp_path / "fresh.sqlite"))


# ── Who may change quotes ─────────────────────────────────────────────────


def _principal(*roles: str, role_typ: str | None = "roles") -> str:
    """The principal Azure's authentication passes, holding *roles*."""
    typ = role_typ or "roles"
    payload: dict[str, Any] = {
        "auth_typ": "aad",
        "name_typ": "name",
        "claims": [{"typ": "name", "val": "someone"}, *({"typ": typ, "val": r} for r in roles)],
    }
    if role_typ is not None:
        payload["role_typ"] = role_typ
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def test_the_roles_are_read_from_the_principal_azure_passes() -> None:
    assert easy_auth_roles(_principal("Quotes.Edit", "Other")) == {"Quotes.Edit", "Other"}
    custom = _principal("Quotes.Edit", role_typ="http://schemas.example/role")
    assert easy_auth_roles(custom) == {"Quotes.Edit"}
    assert easy_auth_roles(_principal("Quotes.Edit", role_typ=None)) == {"Quotes.Edit"}
    for broken in (
        "not base64!",
        base64.b64encode(b"[]").decode(),
        base64.b64encode(b"{}").decode(),
    ):
        with pytest.raises(ValueError):
            easy_auth_roles(broken)


def test_with_an_edit_role_only_those_holding_it_change_quotes(tmp_path: Path, url: str) -> None:
    directory = _write_release(tmp_path / "roles", edit_role="Quotes.Edit")
    editor = {**ANA, EASY_AUTH_PRINCIPAL: _principal("Quotes.Edit")}
    reader = {**JO, EASY_AUTH_PRINCIPAL: _principal("Quotes.Read")}
    original = _get_project_root()
    try:
        with TestClient(create_app(directory, store_url=url)) as client:
            release = client.get("/api/app/release", headers=reader).json()
            assert (release["edit_role"], release["can_edit"]) == ("Quotes.Edit", False)
            assert client.get("/api/app/release", headers=editor).json()["can_edit"] is True
            body = {
                "operation": "operation-000001",
                "form_revision": release["form_revision"],
                "release": release["id"],
                "values": VALUES,
                "shown": None,
            }

            made = client.post("/api/app/quotes", json=body, headers=editor)
            refused = client.post("/api/app/quotes", json=body, headers=reader)
            quote = made.json()["quote"]
            change = client.post(
                f"/api/app/quotes/{quote['id']}/archive", json={"version": 1}, headers=reader
            )
            priced = client.post(
                "/api/app/price",
                json={k: body[k] for k in ("form_revision", "release", "values")},
                headers=reader,
            )

            assert made.status_code == 200
            for each in (refused, change):
                assert each.status_code == 403
                assert each.json()["detail"]["error_code"] == "quote_read_only"
            assert client.get("/api/app/quotes?view=team", headers=reader).status_code == 200
            assert priced.status_code == 200
            for headers in (ANA, {**ANA, EASY_AUTH_PRINCIPAL: "%%%"}):
                unread = client.get("/api/app/release", headers=headers)
                assert unread.status_code == 401
                assert EASY_AUTH_PRINCIPAL in unread.json()["detail"]
    finally:
        set_project_root(original)


def test_serving_locally_as_a_reader_changes_no_quote(release_dir: Path, url: str) -> None:
    app = create_app(
        release_dir, local_principal="ana@example.com", store_url=url, local_reader=True
    )
    with TestClient(app) as client:
        release = client.get("/api/app/release").json()
        refused = client.post(
            "/api/app/quotes",
            json={
                "operation": "operation-000001",
                "form_revision": release["form_revision"],
                "release": release["id"],
                "values": VALUES,
                "shown": None,
            },
        )

    assert release["can_edit"] is False
    assert refused.status_code == 403


# ── The commands ──────────────────────────────────────────────────────────


def test_haute_app_migrate_brings_a_named_store_to_the_newest_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from haute.cli import cli

    fresh = store_url(tmp_path / "hosted.sqlite")
    monkeypatch.delenv(STORE_URL_ENV, raising=False)
    unnamed = CliRunner().invoke(cli, ["app", "migrate"])
    assert unnamed.exit_code != 0
    assert STORE_URL_ENV in unnamed.output

    by_option = CliRunner().invoke(cli, ["app", "migrate", "--url", fresh])
    assert by_option.exit_code == 0, by_option.output
    open_store(fresh).close()

    other = store_url(tmp_path / "other.sqlite")
    monkeypatch.setenv(STORE_URL_ENV, other)
    by_environment = CliRunner().invoke(cli, ["app", "migrate"])
    assert by_environment.exit_code == 0, by_environment.output
    open_store(other).close()
