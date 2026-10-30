"""The underwriter app's quotes (specs/workbench): the routes that list, open, save and change
the quotes the app keeps in the form's own tables, a save priced again on the server, the
version every change names, operation keys that make a repeated write write once, the release
a page was drawn for, writes from other sites refused, and ``haute app serve`` and ``haute app
migrate`` with the store."""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote as escaped

import pytest
from fastapi.testclient import TestClient

from haute import _workbench_app_quotes
from haute._quote_store import (
    DEV,
    HOSTED,
    QuoteBook,
    QuoteStoreMismatchError,
    open_book,
    prepare_store,
    store_url,
)
from haute._sandbox import _get_project_root, set_project_root
from haute._types import PipelineGraph
from haute._workbench_app import (
    EASY_AUTH_NAME,
    EASY_AUTH_PRINCIPAL,
    STORE_URL_ENV,
    create_app,
    easy_auth_roles,
    read_release,
    write_release,
)
from haute._workbench_form import QUOTE_DETAILS_ID, FormSpec, with_quote_details
from haute._workbench_tables import input_tables
from haute.deploy._utils import shipped_graph
from tests.test_workbench_app import _graph
from tests.test_workbench_routes import POLICY, PRICING, form

ANA = {EASY_AUTH_NAME: "ana@example.com"}
JO = {EASY_AUTH_NAME: "jo@example.com"}
DETAILS = {"qd_quote_name": "Fleet", "qd_insured": "Acme", "qd_inception_date": "2026-11-01"}
VALUES = {"t1": [{"c_state": "NY", "c_exposure": "1000"}], "quote_details": [DETAILS]}
#: The pipeline's answer for VALUES, as the page shows it.
PRICED = {"pricing_output": [{"premium": 500.0}]}
FORM = with_quote_details(FormSpec.model_validate(form(POLICY, PRICING))).model_dump(
    mode="json", by_alias=True
)


def _kept(reference: str = "Q-000001", option: int = 1) -> dict[str, Any]:
    """The pricing as the store keeps it for VALUES: the quote's identity on its row."""
    return {
        "pricing_output": [{"quote_reference": reference, "quote_option": option, "premium": 500.0}]
    }


def _priced_graph(spec: dict[str, Any]) -> PipelineGraph:
    """The policy priced as `_graph` prices it, its Workbench Input reading the quote's
    details too, as every project's does: a request names the quote's reference and
    option."""
    graph = _graph()
    details = next(
        table
        for table in input_tables(FormSpec.model_validate(spec))
        if table.name == QUOTE_DETAILS_ID
    )
    request = graph.nodes[0].data.config
    request["tables"] = [details.model_dump(mode="json"), *request["tables"]]
    return graph


def _write_release(
    directory: Path,
    spec: dict[str, Any] = FORM,
    edit_role: str | None = None,
    haute_version: str = "9.9.9",
) -> Path:
    directory.mkdir(exist_ok=True)
    manifest = {
        "haute_version": haute_version,
        "pipeline_name": "motor",
        "created_at": "2026-10-12T09:00:00+00:00",
        "created_by": "analyst",
        "input_node_ids": ["request"],
        "output_node_id": "response",
        "output_fields": None,
        "artifacts": {},
        "pruned_graph": shipped_graph(_priced_graph(spec)),
    }
    (directory / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_release(
        directory, FormSpec.model_validate(spec), "azure-container-apps", edit_role, runtime=[]
    )
    return directory


def _prepared(path: Path, release_dir: Path, environment: str = HOSTED) -> str:
    """The URL of a store at *path* holding the tables of *release_dir*'s form."""
    url = store_url(path)
    release, spec, _ = read_release(release_dir)
    prepare_store(url, spec, project=release.pipeline_name, environment=environment)
    return url


@pytest.fixture()
def release_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    yield _write_release(tmp_path / "release")
    set_project_root(original)


@pytest.fixture()
def path(tmp_path: Path) -> Path:
    return tmp_path / "store.sqlite"


@pytest.fixture()
def url(path: Path, release_dir: Path) -> str:
    return _prepared(path, release_dir)


@pytest.fixture()
def client(release_dir: Path, url: str) -> Iterator[TestClient]:
    with TestClient(create_app(release_dir, store_url=url)) as opened:
        yield opened


def sql(path: Path, query: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(path) as raw:
        return raw.execute(query).fetchall()


def address(quote: dict[str, Any]) -> str:
    """The quote's address under the app's routes, its reference escaped."""
    return f"/api/app/quotes/{escaped(quote['reference'], safe='')}/{quote['option']}"


class Page:
    """What the page knows: the sheets' revision and the release it was drawn for."""

    def __init__(self, client: TestClient) -> None:
        release = client.get("/api/app/release", headers=ANA).json()
        self.client = client
        self.revision = release["form_revision"]
        self.release = release["id"]
        self.operations = iter(range(10**6))
        # The references underwriters key, in turn.
        self.references = iter(range(1, 10**6))

    def key(self) -> str:
        return f"operation-{next(self.operations):06d}"

    def create(self, headers: dict[str, str] = ANA, **fields: Any) -> Any:
        """A new quote, made unpriced as the app makes one, keyed with the next reference
        unless it is an alternative or names its own."""
        source = fields.get("source")
        alternative = source is not None and source.get("relation") == "alternative"
        if "reference" not in fields and not alternative:
            fields["reference"] = f"Q-{next(self.references):06d}"
        body = {
            "operation": self.key(),
            "form_revision": self.revision,
            "release": self.release,
            "values": VALUES,
            **fields,
        }
        return self.client.post("/api/app/quotes", json=body, headers=headers)

    def body(self, quote: dict[str, Any], **fields: Any) -> dict[str, Any]:
        return {
            "operation": self.key(),
            "form_revision": self.revision,
            "release": self.release,
            "version": quote["version"],
            "values": VALUES,
            "shown": PRICED,
            **fields,
        }

    def save(self, quote: dict[str, Any], headers: dict[str, str] = ANA, **fields: Any) -> Any:
        return self.client.post(
            f"{address(quote)}/save", json=self.body(quote, **fields), headers=headers
        )

    def priced(self) -> dict[str, Any]:
        """A new quote of Ana's, saved priced."""
        saved = self.save(self.create().json()["quote"])
        assert saved.status_code == 200, saved.text
        quote: dict[str, Any] = saved.json()["quote"]
        return quote

    def change(
        self, quote: dict[str, Any], what: str, headers: dict[str, str] = ANA, **fields: Any
    ) -> Any:
        body = {"version": quote["version"], **fields}
        return self.client.post(f"{address(quote)}/{what}", json=body, headers=headers)

    def activity(self, quote: dict[str, Any]) -> dict[str, Any]:
        answered: dict[str, Any] = self.client.get(f"{address(quote)}/activity", headers=ANA).json()
        return answered


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


def test_a_priced_save_is_priced_again_on_the_server_and_kept_in_the_forms_tables(
    page: Page, path: Path
) -> None:
    # What the page sends of the details the app sets is overruled, its administration too.
    claimed = {
        "qd_assignee": "jo",
        "qd_stage": "final",
        "qd_created_by": "mallory",
        "qd_modified_date": "2020-01-01",
        "qd_haute_version": "0.0.1",
    }
    sent = {**VALUES, "quote_details": [{**DETAILS, **claimed}]}
    made = page.create(values=sent).json()["quote"]
    saved = page.save(made, values=sent)

    assert saved.status_code == 200, saved.text
    answered = saved.json()
    quote = answered["quote"]
    assert (quote["reference"], quote["option"], quote["label"], quote["title"]) == (
        "Q-000001",
        1,
        "Q-000001/1",
        "Fleet",
    )
    assert (quote["stage"], quote["assignee"], quote["version"]) == ("draft", "ana@example.com", 2)
    assert (quote["priced"], quote["priced_release"], quote["saved_by"]) == (
        True,
        page.release,
        "ana@example.com",
    )
    today = quote["saved_at"][:10]
    assert quote["details"] == {
        "quote_reference": "Q-000001",
        "quote_option": 1,
        "quote_name": "Fleet",
        "insured": "Acme",
        "inception_date": "2026-11-01",
        # An unticked box in a filled row is false, as the request priced it.
        "is_renewal": False,
        "stage": "draft",
        "assignee": "ana@example.com",
        "archived": False,
        "created_date": today,
        "created_by": "ana@example.com",
        "modified_date": today,
        "modified_by": "ana@example.com",
        "haute_version": "9.9.9",
    }
    # The values read back as cells, the app's details among them.
    assert answered["values"]["t1"] == [{"c_state": "NY", "c_exposure": "1000"}]
    assert answered["values"]["quote_details"][0]["qd_stage"] == "draft"
    assert answered["tables"] == _kept()
    assert (answered["moved"], answered["dropped"]) == (False, [])
    # Priced under the quote's own identity, which the request names with its creation; the
    # rest of its administration is the store's alone, in no request.
    request = answered["request"]["quote_details"]
    assert (request["quote_reference"], request["quote_option"]) == ("Q-000001", 1)
    assert (request["created_date"], request["created_by"]) == (today, "ana@example.com")
    assert not {"stage", "modified_date", "modified_by", "haute_version"} & request.keys()
    # Plain SQL over the store reads the quote as it was typed and priced.
    assert sql(
        path, "SELECT quote_reference, quote_option, state, exposure FROM policy_details"
    ) == [("Q-000001", 1, "NY", 1000.0)]
    assert sql(
        path,
        "SELECT d.insured, p.premium FROM quote_details d JOIN pricing_output p "
        "ON p.quote_reference = d.quote_reference AND p.quote_option = d.quote_option",
    ) == [("Acme", 500.0)]


def test_a_save_whose_prices_moved_on_the_server_keeps_the_servers_and_says_so(
    page: Page,
) -> None:
    made = page.create().json()["quote"]
    saved = page.save(made, shown={"pricing_output": [{"premium": 1.0}]}).json()

    assert saved["moved"] is True
    assert saved["tables"] == _kept()


def test_an_unpriced_save_keeps_no_prices(page: Page, path: Path) -> None:
    priced = page.priced()

    saved = page.save(priced, shown=None).json()

    assert (saved["quote"]["priced"], saved["tables"]) == (False, None)
    assert sql(path, "SELECT * FROM pricing_output") == []


def test_one_instant_stamps_a_save_its_request_and_its_rows(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = page.create().json()["quote"]
    # Read before pricing, a second before midnight UTC on a day long past: the store's own
    # clock, read again, would give today.
    instant = datetime(2026, 10, 12, 23, 59, 59, tzinfo=UTC)
    monkeypatch.setattr(QuoteBook, "now", lambda _book: instant)

    saved = page.save(made, headers=JO).json()

    assert saved["quote"]["saved_at"] == instant.isoformat()
    assert saved["quote"]["details"]["modified_date"] == "2026-10-12"
    assert saved["values"]["quote_details"][0]["qd_modified_by"] == "jo@example.com"
    assert page.activity(made)["entries"][-1]["at"] == instant.isoformat()


def test_what_the_app_sets_decides_what_shows_and_is_no_typing(
    release_dir: Path, tmp_path: Path
) -> None:
    # The policy shows on quotes Ana made, by a condition on the detail the app sets, which
    # its own Collection shows.
    spec = json.loads(json.dumps(FORM))
    spec["pages"][0]["sections"][0]["height"] = 240
    spec["pages"][0]["sections"][0]["widgets"] = [
        {
            "id": "w_made",
            "type": "collection",
            "x": 0,
            "y": 120,
            "w": 400,
            "h": 100,
            "fields": [{"table": "quote_details", "column": "qd_created_by"}],
        },
        {
            "id": "w_policy",
            "type": "collection",
            "x": 0,
            "y": 0,
            "w": 400,
            "h": 100,
            "fields": [{"table": "t1", "column": column} for column in ("c_state", "c_exposure")],
            "show_when": {
                "match": "all",
                "conditions": [
                    {
                        "table": "quote_details",
                        "column": "qd_created_by",
                        "op": "equals",
                        "values": ["ana@example.com"],
                    }
                ],
            },
        },
    ]
    shown = _write_release(tmp_path / "shown", spec)
    policy = {"t1": VALUES["t1"]}

    with TestClient(
        create_app(shown, store_url=_prepared(tmp_path / "shown.sqlite", shown))
    ) as client:
        page = Page(client)
        anas = page.create(values={}).json()["quote"]
        jos = page.create(headers=JO, values={}).json()["quote"]

        # Ana's quote shows the policy, so what she typed into it is saved.
        saved = page.save(anas, values=policy)
        assert saved.status_code == 200, saved.text
        # Jo's does not, whatever his page says of who made it: there is nothing to save.
        claimed = {**policy, "quote_details": [{"qd_created_by": "ana@example.com"}]}
        refused = page.save(jos, headers=JO, values=claimed)
        assert refused.status_code == 422
        assert refused.json()["detail"] == "Type the quote first"


def test_a_detail_the_app_sets_sent_unreadable_is_overruled_not_read(page: Page) -> None:
    # An option no number, sent by a page, is the app's to set, never read.
    sent = {**VALUES, "quote_details": [{**DETAILS, "qd_option": "invalid"}]}

    made = page.create(values=sent)
    assert made.status_code == 200, made.text
    saved = page.save(made.json()["quote"], values=sent)

    assert saved.status_code == 200, saved.text
    assert (saved.json()["quote"]["option"], saved.json()["quote"]["label"]) == (1, "Q-000001/1")


def test_a_value_not_of_its_columns_type_refuses_the_whole_save_naming_each_cell(
    page: Page, path: Path
) -> None:
    quote = page.priced()
    misfit = {**VALUES, "t1": [{"c_state": "NY", "c_exposure": "lots"}]}

    for shown in (None, PRICED):
        refused = page.save(quote, values=misfit, shown=shown)
        assert refused.status_code == 422, refused.text
        detail = refused.json()["detail"]
        assert detail["error_code"] == "quote_values_invalid"
        assert detail["cells"] == [{"table": "policy_details", "row": None, "column": "exposure"}]
    assert sql(path, "SELECT exposure FROM policy_details") == [(1000.0,)]


def test_a_new_quote_is_made_under_the_reference_the_underwriter_keyed(page: Page) -> None:
    made = page.create(reference=" ABC-1 ")
    assert made.status_code == 200, made.text
    quote = made.json()["quote"]
    assert (quote["reference"], quote["label"], quote["version"]) == ("ABC-1", "ABC-1/1", 1)

    # A reference is taken as keyed, case included: another case is another quote.
    assert page.create(reference="abc-1").status_code == 200
    taken = page.create(reference="ABC-1")
    assert taken.status_code == 409
    assert taken.json()["detail"] == {
        "message": "ABC-1/1 already holds the reference ABC-1: open it, or create an "
        "alternative of it.",
        "error_code": "reference_taken",
        "reference": "ABC-1",
        "option": 1,
        "label": "ABC-1/1",
    }
    # An alternative takes its source's, and names none.
    source = {"reference": "ABC-1", "option": 1, "relation": "alternative"}
    assert page.create(source=source).json()["quote"]["label"] == "ABC-1/2"
    named = page.create(source=source, reference="ABC-9")
    assert named.status_code == 422
    assert "takes its source's reference" in named.json()["detail"]["message"]


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        (None, "Key the quote's reference"),
        ("ABC/1", "holds no /"),
        ("A" * 41, "at most 40 characters"),
        ("..", "more than . or .."),
    ],
)
def test_a_new_quote_without_a_reference_that_fits_is_refused(
    page: Page, reference: Any, message: str
) -> None:
    refused = page.create(reference=reference)

    assert refused.status_code == 422
    assert message in refused.json()["detail"]["message"]
    assert page.client.get("/api/app/quotes?view=team", headers=ANA).json()["quotes"] == []


def test_a_quote_whose_reference_an_address_escapes_opens_and_saves(page: Page) -> None:
    made = page.create(reference="Fleet #7?").json()["quote"]

    opened = page.client.get(address(made), headers=ANA)
    saved = page.save(made)

    assert opened.status_code == 200, opened.text
    assert opened.json()["quote"]["label"] == "Fleet #7?/1"
    assert saved.status_code == 200, saved.text


def test_a_new_quote_leaving_a_required_detail_empty_is_made_and_saved(
    release_dir: Path, tmp_path: Path
) -> None:
    # Required marks what Mark final needs: a creation and a save take a quote without it.
    spec = json.loads(json.dumps(FORM))
    for column in spec["schema"]["tables"][0]["columns"]:
        if column["name"] == "insured":
            column["required"] = True
    required = _write_release(tmp_path / "required", spec)

    with TestClient(
        create_app(required, store_url=_prepared(tmp_path / "required.sqlite", required))
    ) as client:
        page = Page(client)
        blank = {**VALUES, "quote_details": [{**DETAILS, "qd_insured": "   "}]}
        made = page.create(values=blank)
        assert made.status_code == 200, made.text
        quote = made.json()["quote"]
        assert page.save(quote, values=blank).status_code == 200
        not_ready = page.change({**quote, "version": 2}, "final")
        assert not_ready.status_code == 409
        assert not_ready.json()["detail"]["missing"] == ["quote_details.insured"]


def test_a_quote_is_made_untyped_as_a_draft_and_an_empty_save_is_refused(page: Page) -> None:
    draft = page.create().json()
    # Made when the underwriter asks for it, holding nothing typed yet, with its identity.
    untyped = page.create(values={})

    assert (draft["quote"]["stage"], draft["tables"]) == ("draft", None)
    assert untyped.status_code == 200, untyped.text
    made = untyped.json()["quote"]
    assert (made["label"], made["stage"]) == ("Q-000002/1", "draft")
    # The details the app sets are not typing, whatever the page sends of them.
    for values in ({}, {"quote_details": [{"qd_reference": "Q-9", "qd_stage": "final"}]}):
        refused = page.save(made, shown=None, values=values)
        assert refused.status_code == 422
        assert refused.json()["detail"] == "Type the quote first"


def test_a_quote_name_past_its_length_is_refused_naming_the_limit(page: Page) -> None:
    named = {**VALUES, "quote_details": [{**DETAILS, "qd_quote_name": "x" * 240}]}

    refused = page.create(values=named)
    quote = page.create().json()["quote"]
    resaved = page.save(quote, values=named, shown=None)

    for each in (refused, resaved):
        assert each.status_code == 422
        assert "at most 200 characters" in each.json()["detail"]["message"]


def test_a_write_from_a_page_of_another_release_is_refused(page: Page) -> None:
    quote = page.create().json()["quote"]
    for fields in ({"release": "another"}, {"form_revision": "0" * 64}):
        for refused in (page.create(**fields), page.save(quote, **fields)):
            assert refused.status_code == 409
            assert "reload it" in refused.json()["detail"]
    assert len(page.client.get("/api/app/quotes?view=team", headers=ANA).json()["quotes"]) == 1


def test_a_quote_opens_with_what_it_holds(page: Page) -> None:
    quote = page.priced()

    opened = page.client.get(address(quote), headers=ANA).json()

    assert opened["quote"] == quote
    assert opened["values"]["t1"] == [{"c_state": "NY", "c_exposure": "1000"}]
    assert (opened["tables"], opened["dropped"], opened["moved"]) == (_kept(), [], False)
    assert opened["request"]["policy_details"] == {"state": "NY", "exposure": 1000.0}
    missing = page.client.get("/api/app/quotes/nope/1", headers=ANA)
    assert missing.status_code == 404
    assert missing.json()["detail"]["error_code"] == "quote_not_found"


# ── Versions and operation keys ───────────────────────────────────────────


def test_a_save_over_a_quote_moved_on_is_refused_with_the_quote_as_it_is(page: Page) -> None:
    quote = page.create().json()["quote"]
    page.save(quote, headers=JO)

    refused = page.save(quote)

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["error_code"] == "stale_quote"
    assert "jo@example.com saved it" in detail["message"]
    assert detail["quote"]["version"] == 2


def test_a_final_or_archived_quote_refuses_a_save(page: Page) -> None:
    final = page.change(page.priced(), "final").json()["quote"]
    archived = page.change(page.priced(), "archive").json()["quote"]

    for quote, says in ((final, "amend it"), (archived, "restore it")):
        refused = page.save(quote)
        assert refused.status_code == 409
        assert refused.json()["detail"]["error_code"] == "quote_locked"
        assert says in refused.json()["detail"]["message"]


def test_a_save_repeated_with_its_operation_key_writes_once(page: Page) -> None:
    quote = page.create().json()["quote"]
    body = page.body(quote)

    answers = [page.client.post(f"{address(quote)}/save", json=body, headers=ANA) for _ in range(2)]

    assert [answer.status_code for answer in answers] == [200, 200]
    assert answers[0].json() == answers[1].json()
    assert answers[1].json()["quote"]["version"] == 2
    actions = [entry["action"] for entry in page.activity(quote)["entries"]]
    assert actions == ["created", "saved"]


def test_a_save_repeated_after_a_colleague_saved_is_superseded_naming_both(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    quote = page.create().json()["quote"]
    body = page.body(quote)
    first = page.client.post(f"{address(quote)}/save", json=body, headers=ANA)
    assert page.save(first.json()["quote"], headers=JO).json()["quote"]["version"] == 3

    def written_again(*_: Any, **__: Any) -> Any:
        raise AssertionError("a save repeated with its key is neither priced nor written again")

    monkeypatch.setattr(QuoteBook, "save", written_again)
    # Repeated from a page drawn for another release: the key is answered first.
    again = page.client.post(
        f"{address(quote)}/save", json={**body, "release": "another"}, headers=ANA
    )

    assert again.status_code == 409, again.text
    detail = again.json()["detail"]
    assert detail["error_code"] == "write_superseded"
    assert detail["changed_by"] == "jo@example.com"
    assert detail["landed_at"] <= detail["changed_at"]


def test_a_creation_repeated_with_its_key_makes_one_quote_and_one_option(page: Page) -> None:
    body = {
        "operation": page.key(),
        "form_revision": page.revision,
        "release": page.release,
        "values": VALUES,
        "reference": "Q-000001",
    }
    made = [page.client.post("/api/app/quotes", json=body, headers=ANA) for _ in range(2)]
    alternative = {
        **body,
        "operation": page.key(),
        "reference": None,
        "source": {"reference": "Q-000001", "option": 1, "relation": "alternative"},
    }
    options = [page.client.post("/api/app/quotes", json=alternative, headers=ANA) for _ in range(2)]

    assert [answer.status_code for answer in made + options] == [200] * 4
    assert made[0].json()["quote"] == made[1].json()["quote"]
    assert [answer.json()["quote"]["label"] for answer in options] == ["Q-000001/2"] * 2
    listed = page.client.get("/api/app/quotes?view=team", headers=ANA).json()
    assert listed["counts"]["team"] == 2


def test_a_key_reused_for_another_quote_is_refused(page: Page) -> None:
    quote = page.create().json()["quote"]
    other = page.create().json()["quote"]
    body = page.body(quote)
    assert page.client.post(f"{address(quote)}/save", json=body, headers=ANA).status_code == 200

    reused = page.client.post(
        f"{address(other)}/save", json={**body, "version": other["version"]}, headers=ANA
    )

    assert reused.status_code == 422
    assert "used for another write" in reused.json()["detail"]["message"]


# ── Lists, activity and people ────────────────────────────────────────────


def test_the_lists_page_search_and_count_through_the_route(page: Page) -> None:
    mine = page.create().json()["quote"]
    page.create(
        headers=JO,
        values={
            "t1": [{"c_state": "CA", "c_exposure": "5"}],
            "quote_details": [{"qd_insured": "Cal"}],
        },
    )

    listed = page.client.get("/api/app/quotes", headers=ANA).json()
    team = page.client.get("/api/app/quotes?view=team&limit=1", headers=ANA).json()
    found = page.client.get("/api/app/quotes?view=team&search=cal&stage=draft", headers=ANA)

    assert [q["label"] for q in listed["quotes"]] == [mine["label"]]
    assert listed["counts"]["team"] == 2
    assert len(team["quotes"]) == 1 and team["next_cursor"] is not None
    following = page.client.get(
        "/api/app/quotes", params={"view": "team", "cursor": team["next_cursor"]}, headers=ANA
    ).json()
    assert following["quotes"][0]["reference"] == "Q-000001"
    assert [q["title"] for q in found.json()["quotes"]] == ["Cal"]
    assert page.client.get("/api/app/quotes?stage=priced", headers=ANA).status_code == 422
    bad = page.client.get("/api/app/quotes?cursor=x", headers=ANA)
    assert bad.status_code == 422
    assert bad.json()["detail"]["error_code"] == "quote_store_invalid"


def test_the_activity_names_who_did_what_and_the_quotes_made_from_it(page: Page) -> None:
    quote = page.create().json()["quote"]
    renewal = page.create(
        headers=JO, source={"reference": quote["reference"], "option": 1, "relation": "renewal"}
    ).json()["quote"]

    activity = page.activity(quote)
    made = page.activity(renewal)

    assert [(e["action"], e["actor"]) for e in activity["entries"]] == [
        ("created", "ana@example.com")
    ]
    assert [(d["label"], d["relation"]) for d in activity["derived"]] == [
        (renewal["label"], "renewal")
    ]
    assert made["source"]["label"] == quote["label"]
    assert (renewal["source_label"], renewal["relation"]) == (quote["label"], "renewal")
    people = page.client.get("/api/app/people", headers=ANA).json()["people"]
    assert people == ["ana@example.com", "jo@example.com"]


def test_an_alternative_is_the_next_option_of_its_sources_reference(page: Page) -> None:
    quote = page.create().json()["quote"]
    alternative = page.create(
        source={"reference": quote["reference"], "option": 1, "relation": "alternative"}
    ).json()["quote"]

    assert (alternative["reference"], alternative["option"], alternative["label"]) == (
        "Q-000001",
        2,
        "Q-000001/2",
    )
    assert (alternative["source_option"], alternative["source_label"]) == (1, "Q-000001/1")
    listed = page.client.get(
        "/api/app/quotes", params={"view": "team", "search": "Q-000001/2"}, headers=ANA
    ).json()
    assert [q["label"] for q in listed["quotes"]] == ["Q-000001/2"]


def test_a_price_differing_only_in_the_quotes_identity_has_not_moved() -> None:
    spec = FormSpec.model_validate(FORM)
    kept = _kept("Q-000002", 1)

    # A page priced under another quote's identity.
    shown = {
        "pricing_output": [{"quote_reference": "Q-000001", "quote_option": 3, "premium": 500.0}]
    }
    assert _workbench_app_quotes._moved(spec, kept, shown) is False
    shown["pricing_output"][0]["premium"] = 400.0
    assert _workbench_app_quotes._moved(spec, kept, shown) is True


# ── Changes ───────────────────────────────────────────────────────────────


def test_changes_through_the_routes_name_the_version_they_were_made_on(page: Page) -> None:
    quote = page.create().json()["quote"]

    assigned = page.change(quote, "assignee", assignee="jo@example.com").json()["quote"]
    noted = page.client.post(
        f"{address(quote)}/note", json={"note": "Waiting on the broker"}, headers=JO
    ).json()["quote"]
    stale = page.change(quote, "assignee", assignee="sam@example.com")

    assert (assigned["assignee"], assigned["version"]) == ("jo@example.com", 2)
    assert assigned["details"]["assignee"] == "jo@example.com"
    # A note moves no version, so it never conflicts.
    assert (noted["note"], noted["note_by"], noted["version"]) == (
        "Waiting on the broker",
        "jo@example.com",
        2,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["quote"]["version"] == 2
    entries = page.activity(quote)["entries"]
    assert [(e["action"], e["version"]) for e in entries] == [
        ("created", 1),
        ("assigned", 2),
        ("noted", None),
    ]


def test_mark_final_archive_restore_and_amend_through_the_routes(page: Page) -> None:
    quote = page.priced()

    final = page.change(quote, "final").json()["quote"]
    archived = page.change(final, "archive").json()["quote"]
    restored = page.change(archived, "restore").json()["quote"]
    unreasoned = page.change(restored, "amend", reason="")
    amended = page.change(restored, "amend", reason="Broker asked").json()["quote"]

    assert (final["stage"], final["final_by"]) == ("final", "ana@example.com")
    assert archived["archived"] and archived["archived_at"] is not None
    assert (restored["archived"], restored["stage"]) == (False, "final")
    assert unreasoned.status_code == 422
    assert amended["stage"] == "draft"
    assert page.save(amended).status_code == 200
    entries = page.activity(quote)["entries"]
    assert [e["action"] for e in entries][-5:] == [
        "finalised",
        "archived",
        "restored",
        "amended",
        "saved",
    ]
    assert entries[-2]["reason"] == "Broker asked"


def test_mark_final_needs_a_price_of_the_release_running(
    page: Page, url: str, tmp_path: Path
) -> None:
    unpriced = page.change(page.create().json()["quote"], "final")
    assert unpriced.status_code == 409
    assert unpriced.json()["detail"]["error_code"] == "quote_not_ready"
    assert "is not priced" in unpriced.json()["detail"]["message"]

    priced = page.priced()
    # The next release, on the same store, did not price it.
    upgraded = _write_release(tmp_path / "upgraded", haute_version="9.9.10")
    with TestClient(create_app(upgraded, store_url=url)) as client:
        refused = client.post(
            f"{address(priced)}/final", json={"version": priced["version"]}, headers=ANA
        )
    assert refused.status_code == 409
    assert (
        f"was priced on release {page.release}, not this one"
        in (refused.json()["detail"]["message"])
    )
    assert page.change(priced, "final").json()["quote"]["stage"] == "final"


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
        for path in ("/", "/quotes/new", "/quotes/ABC-1/2", "/quotes/Fleet%20%237/1"):
            assert client.get(path, headers=ANA).text == "<div id=app></div>", path
        assert client.get("/quotes/ABC-1/2").status_code == 401


def test_the_app_does_not_start_on_a_database_without_the_forms_tables(
    release_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(QuoteStoreMismatchError, match="holds no quote store yet"):
        create_app(release_dir, store_url=store_url(tmp_path / "fresh.sqlite"))
    # A store of the release's tables in dev is the analyst's laptop's, not a hosted app's.
    dev = _prepared(tmp_path / "dev.sqlite", release_dir, environment=DEV)
    with pytest.raises(QuoteStoreMismatchError, match="in 'dev', not of 'motor' in 'hosted'"):
        create_app(release_dir, store_url=dev)


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


def test_with_an_edit_role_only_those_holding_it_change_quotes(tmp_path: Path) -> None:
    directory = _write_release(tmp_path / "roles", edit_role="Quotes.Edit")
    editor = {**ANA, EASY_AUTH_PRINCIPAL: _principal("Quotes.Edit")}
    reader = {**JO, EASY_AUTH_PRINCIPAL: _principal("Quotes.Read")}
    original = _get_project_root()
    try:
        url = _prepared(tmp_path / "roles.sqlite", directory)
        with TestClient(create_app(directory, store_url=url)) as client:
            release = client.get("/api/app/release", headers=reader).json()
            assert (release["edit_role"], release["can_edit"]) == ("Quotes.Edit", False)
            assert client.get("/api/app/release", headers=editor).json()["can_edit"] is True
            body = {
                "operation": "operation-000001",
                "form_revision": release["form_revision"],
                "release": release["id"],
                "values": VALUES,
                "reference": "Q-000001",
            }

            made = client.post("/api/app/quotes", json=body, headers=editor)
            refused = client.post("/api/app/quotes", json=body, headers=reader)
            quote = made.json()["quote"]
            # Every route that writes refuses a reader, each by its own check.
            writes = {
                "save": {**body, "operation": "operation-000002", "version": 1, "shown": None},
                "note": {"note": "Call the broker"},
                "assignee": {"version": 1, "assignee": "jo@example.com"},
                "archive": {"version": 1},
                "restore": {"version": 1},
                "final": {"version": 1},
                "amend": {"version": 1, "reason": "Broker asked"},
            }
            changes = [
                client.post(f"{address(quote)}/{path}", json=json, headers=reader)
                for path, json in writes.items()
            ]
            # A reader prices the quote as it is shown, under its reference and option.
            shown = {k: body[k] for k in ("form_revision", "release")}
            priced = client.post(
                "/api/app/price", json={**shown, "values": made.json()["values"]}, headers=reader
            )

            assert made.status_code == 200
            for each in (refused, *changes):
                assert each.status_code == 403
                assert each.json()["detail"]["error_code"] == "quote_read_only"
            activity = client.get(f"{address(quote)}/activity", headers=reader).json()
            assert [entry["action"] for entry in activity["entries"]] == ["created"]
            assert client.get("/api/app/quotes?view=team", headers=reader).status_code == 200
            assert priced.status_code == 200
            for headers in (ANA, {**ANA, EASY_AUTH_PRINCIPAL: "%%%"}):
                unread = client.get("/api/app/release", headers=headers)
                assert unread.status_code == 401
                assert EASY_AUTH_PRINCIPAL in unread.json()["detail"]
    finally:
        set_project_root(original)


#: Where ``haute app serve`` is reached on the analyst's machine.
LOOPBACK = "http://127.0.0.1:8001"


@pytest.fixture()
def dev_url(release_dir: Path, tmp_path: Path) -> str:
    return _prepared(tmp_path / "dev.sqlite", release_dir, environment=DEV)


def _quotes_kept(url: str, release_dir: Path) -> int:
    release, spec, _ = read_release(release_dir)
    with open_book(
        url, spec, project=release.pipeline_name, environment=DEV, haute_version="9.9.9"
    ) as book:
        return book.page(actor="ana@example.com", view="team").counts["team"]


def test_serving_locally_refuses_another_host_and_forwarded_requests(
    release_dir: Path, dev_url: str
) -> None:
    app = create_app(release_dir, local_principal="ana@example.com", store_url=dev_url)
    foreign = {"host": "rebound.example:8001"}
    # A page of a site whose name resolves to the analyst's machine: the browser calls its
    # requests the page's own.
    rebound = {**foreign, "origin": "http://rebound.example:8001", "sec-fetch-site": "same-origin"}
    forwarded = ({"x-forwarded-for": "203.0.113.9"}, {"forwarded": "for=203.0.113.9"})
    with TestClient(app, base_url=LOOPBACK) as client:
        page = Page(client)
        priced = {"form_revision": page.revision, "release": page.release, "values": VALUES}
        for headers in (foreign, rebound, *forwarded):
            answers = [
                client.get("/health", headers=headers),
                client.get("/api/app/release", headers=headers),
                client.get("/api/app/form", headers=headers),
                client.get("/api/app/quotes", headers=headers),
                client.post("/api/app/price", json=priced, headers=headers),
                page.create(headers=headers),
            ]
            assert [answer.status_code for answer in answers] == [400] * 6, headers
            assert not any("ana@example.com" in answer.text for answer in answers)
        assert _quotes_kept(dev_url, release_dir) == 0

        for host in ("127.0.0.1:8001", "localhost:8001"):
            own = {"host": host}
            release = client.get("/api/app/release", headers=own)
            assert release.json()["principal"] == "ana@example.com", host
            made = page.create(headers=own)
            assert made.status_code == 200, made.text
            values = made.json()["values"]
            price = client.post("/api/app/price", json={**priced, "values": values}, headers=own)
            assert price.status_code == 200, price.text
            saved = page.save(made.json()["quote"], headers=own)
            assert saved.status_code == 200, saved.text
        assert _quotes_kept(dev_url, release_dir) == 2


def test_serving_locally_as_a_reader_changes_no_quote(release_dir: Path, dev_url: str) -> None:
    app = create_app(
        release_dir, local_principal="ana@example.com", store_url=dev_url, local_reader=True
    )
    with TestClient(app, base_url=LOOPBACK) as client:
        release = client.get("/api/app/release").json()
        refused = client.post(
            "/api/app/quotes",
            json={
                "operation": "operation-000001",
                "form_revision": release["form_revision"],
                "release": release["id"],
                "values": VALUES,
                "reference": "Q-000001",
            },
        )

    assert release["can_edit"] is False
    assert refused.status_code == 403


# ── The commands ──────────────────────────────────────────────────────────


def test_haute_app_migrate_makes_the_tables_of_a_built_releases_form(
    release_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from haute.cli import cli

    fresh = store_url(tmp_path / "hosted.sqlite")
    built = ["--release", str(release_dir)]
    monkeypatch.delenv(STORE_URL_ENV, raising=False)
    unnamed = CliRunner().invoke(cli, ["app", "migrate", *built])
    assert unnamed.exit_code != 0
    assert STORE_URL_ENV in unnamed.output
    unbuilt = CliRunner().invoke(
        cli, ["app", "migrate", "--url", fresh, "--release", str(tmp_path / "none")]
    )
    assert unbuilt.exit_code != 0
    assert "run haute app build first" in unbuilt.output

    by_option = CliRunner().invoke(cli, ["app", "migrate", "--url", fresh, *built])
    assert by_option.exit_code == 0, by_option.output
    release, spec, _ = read_release(release_dir)
    open_book(fresh, spec, project="motor", environment=HOSTED, haute_version="1").close()
    # Run again for the same form, it changes nothing.
    assert CliRunner().invoke(cli, ["app", "migrate", "--url", fresh, *built]).exit_code == 0

    # A release of another form is refused, naming the table that differs.
    other = json.loads(json.dumps(FORM))
    other["schema"]["tables"][1]["columns"].append(
        {"id": "c_seats", "name": "seats", "type": "int"}
    )
    changed = _write_release(tmp_path / "changed", other)
    refused = CliRunner().invoke(cli, ["app", "migrate", "--url", fresh, "--release", str(changed)])
    assert refused.exit_code == 1
    assert "policy_details has changed" in refused.output

    by_environment = store_url(tmp_path / "other.sqlite")
    monkeypatch.setenv(STORE_URL_ENV, by_environment)
    assert CliRunner().invoke(cli, ["app", "migrate", *built]).exit_code == 0
    open_book(by_environment, spec, project="motor", environment=HOSTED, haute_version="1").close()
