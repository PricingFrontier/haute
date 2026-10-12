"""Test quotes kept in Preview (specs/workbench): the project's test-quote file, the request and
response saved with a quote, and the routes that save, list, open, compare, accept and delete
them, each write captured on the save ledger as the form's saves are."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store import migrate_store, store_url
from haute._test_quotes import (
    WorkbenchTestQuoteError,
    project_test_quotes_path,
    quote_request,
    response_document,
)
from haute._workbench_form import FormSpec
from haute.deploy._validators import response_changes
from tests.test_workbench_routes import (
    LEDGER,
    POLICY,
    PRICING,
    client,
    column,
    enable,
    form,
    git,
    init_repo,
)

ITEMS = {
    "id": "t3",
    "name": "items",
    "role": "output",
    "rows": "many",
    "columns": [column("item", "str"), column("value", "float")],
}
FORM = form(POLICY, PRICING, ITEMS)
VALUES = {"t1": [{"c_state": "NY", "c_exposure": "$1,000"}]}
TABLES = {
    "pricing_output": [{"premium": 512.4}],
    "items": [{"item": "van", "value": 7.0}, {"item": "car", "value": 3.0}],
}
PATH = "tests/quotes/test_quotes.sqlite"


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The working directory is the project, as it is for ``haute serve``."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def spec(document: dict[str, Any] = FORM) -> FormSpec:
    return FormSpec.model_validate(document)


def save(name: str = "Northern fleet", tables: dict[str, Any] = TABLES) -> Any:
    return client().post(
        "/api/workbench/test-quotes",
        json={"name": name, "form": FORM, "values": VALUES, "tables": tables},
    )


# ── The file, the request and the response ───────────────────────────────


def test_the_file_is_in_the_test_quote_directory(project: Path) -> None:
    assert project_test_quotes_path(project) == (project / PATH).resolve()

    (project / "haute.toml").write_text('[test_quotes]\ndir = "quotes"\n', encoding="utf-8")
    assert project_test_quotes_path(project) == (project / "quotes/test_quotes.sqlite").resolve()

    (project / "haute.toml").write_text('[test_quotes]\ndir = "../elsewhere"\n', encoding="utf-8")
    with pytest.raises(WorkbenchTestQuoteError, match="inside the project"):
        project_test_quotes_path(project)


def test_the_request_is_built_as_price_builds_it() -> None:
    # The number typed with a currency sign and a separator is read as Price reads it.
    assert quote_request(spec(), VALUES) == {"policy_details": {"state": "NY", "exposure": 1000.0}}


def test_the_response_is_the_document_a_deployed_pipeline_answers() -> None:
    assert response_document(spec(), TABLES) == {
        "pricing_output": {"premium": 512.4},
        "items": [{"item": "van", "value": 7.0}, {"item": "car", "value": 3.0}],
    }
    with pytest.raises(WorkbenchTestQuoteError, match="no 'items' table"):
        response_document(spec(), {"pricing_output": [{"premium": 1.0}]})
    with pytest.raises(WorkbenchTestQuoteError, match="'pricing_output'.*2 rows"):
        response_document(spec(), {**TABLES, "pricing_output": [{"premium": 1.0}] * 2})


def test_every_change_to_a_response_is_listed_by_its_place() -> None:
    accepted = {
        "pricing": {"premium": 100.0, "band": "A"},
        "items": [{"value": 1.0}, {"value": 2.0}],
    }
    priced = {
        "pricing": {"premium": 100.0 * (1 + 1e-12), "band": "B", "tax": 3.0},
        "items": [{"value": 1.0}],
    }

    changes = [tuple(change) for change in response_changes(priced, accepted, 1e-9)]

    assert changes == [
        ("pricing.band", "changed", "A", "B"),
        ("pricing.tax", "added", None, 3.0),
        ("items[1]", "removed", {"value": 2.0}, None),
    ]


# ── The routes ────────────────────────────────────────────────────────────


def test_a_save_creates_the_file_at_the_newest_schema_and_captures_it(project: Path) -> None:
    enable(project)
    init_repo(project)

    saved = save()

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["quote"]["name"] == "Northern fleet"
    assert body["quote"]["number"] == 1
    assert body["quote"]["priced"] is True
    assert body["quote"]["saved_by"] == "test@example.com"
    assert body["git_sha"] is not None
    assert PATH in git(project, "show", "--name-only", "--format=", LEDGER)
    assert sorted(path.name for path in (project / "tests/quotes").iterdir()) == [
        "test_quotes.sqlite"
    ]


def test_the_list_is_the_most_recently_saved_first(project: Path) -> None:
    enable(project)
    save("Northern fleet")
    save("Southern fleet")

    listed = client().get("/api/workbench/test-quotes")

    assert listed.status_code == 200, listed.text
    assert [quote["name"] for quote in listed.json()["quotes"]] == [
        "Southern fleet",
        "Northern fleet",
    ]


def test_an_open_carries_the_values_onto_the_views_form(project: Path) -> None:
    enable(project)
    quote_id = save().json()["quote"]["id"]
    # The view's form renamed the table and dropped the exposure column.
    renamed = {**POLICY, "name": "policy", "columns": [column("state", options=["CA", "NY"])]}

    opened = client().post(
        f"/api/workbench/test-quotes/{quote_id}/open", json={"form": form(renamed, PRICING)}
    )

    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["values"] == {"t1": [{"c_state": "NY"}]}
    assert body["dropped"] == ["policy_details.exposure"]
    assert body["number"] == 1
    assert body["response"]["pricing_output"] == {"premium": 512.4}


def test_a_compare_lists_what_changed_since_the_revision_the_run_read(project: Path) -> None:
    enable(project)
    quote_id = save().json()["quote"]["id"]
    repriced = {**TABLES, "pricing_output": [{"premium": 530.0}]}

    compared = client().post(
        f"/api/workbench/test-quotes/{quote_id}/compare",
        json={"form": FORM, "tables": repriced, "number": 1},
    )

    assert compared.status_code == 200, compared.text
    assert compared.json()["changes"] == [
        {
            "path": "pricing_output.premium",
            "kind": "changed",
            "accepted": 512.4,
            "priced": 530.0,
        }
    ]


def test_an_accept_follows_the_newest_and_a_stale_one_writes_nothing(project: Path) -> None:
    enable(project)
    quote_id = save().json()["quote"]["id"]
    repriced = {**TABLES, "pricing_output": [{"premium": 530.0}]}
    body = {"after": 1, "form": FORM, "values": VALUES, "tables": repriced}

    accepted = client().post(f"/api/workbench/test-quotes/{quote_id}/accept", json=body)
    stale = client().post(f"/api/workbench/test-quotes/{quote_id}/accept", json=body)

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["quote"]["number"] == 2
    assert stale.status_code == 409
    assert "stale_quote_revision" in stale.text
    [listed] = client().get("/api/workbench/test-quotes").json()["quotes"]
    assert listed["number"] == 2


def test_a_delete_removes_the_quote(project: Path) -> None:
    enable(project)
    quote_id = save().json()["quote"]["id"]

    deleted = client().delete(f"/api/workbench/test-quotes/{quote_id}")

    assert deleted.status_code == 200, deleted.text
    assert client().get("/api/workbench/test-quotes").json() == {"quotes": []}
    missing = client().delete(f"/api/workbench/test-quotes/{quote_id}")
    assert missing.status_code == 409
    assert "quote_not_found" in missing.text


def test_a_save_of_a_response_preview_cut_short_names_the_table(project: Path) -> None:
    enable(project)

    refused = save(tables={"pricing_output": [{"premium": 1.0}]})

    assert refused.status_code == 409
    assert "no 'items' table" in refused.json()["detail"]
    assert not (project / PATH).exists()


def test_a_read_of_a_project_with_no_file_lists_nothing_and_creates_none(project: Path) -> None:
    enable(project)

    assert client().get("/api/workbench/test-quotes").json() == {"quotes": []}
    assert not (project / "tests").exists()


def test_a_file_behind_the_schema_is_refused_by_a_read_and_brought_up_by_a_write(
    project: Path,
) -> None:
    enable(project)
    (project / "tests/quotes").mkdir(parents=True)
    sqlite3.connect(project / PATH).close()

    read = client().get("/api/workbench/test-quotes")
    assert read.status_code == 409
    assert "save a test quote" in read.json()["detail"].lower()

    assert save().status_code == 200
    assert len(client().get("/api/workbench/test-quotes").json()["quotes"]) == 1


def test_a_migration_of_a_current_file_leaves_its_bytes_alone(tmp_path: Path) -> None:
    path = tmp_path / "test_quotes.sqlite"
    migrate_store(store_url(path))
    before = path.read_bytes()

    migrate_store(store_url(path))

    assert path.read_bytes() == before


def test_every_test_quote_route_is_404_while_the_workbench_is_off(project: Path) -> None:
    write = {"form": FORM, "values": VALUES, "tables": TABLES}
    for method, url, body in (
        ("GET", "/api/workbench/test-quotes", None),
        ("POST", "/api/workbench/test-quotes", {"name": "n", **write}),
        ("POST", "/api/workbench/test-quotes/q/open", {"form": FORM}),
        (
            "POST",
            "/api/workbench/test-quotes/q/compare",
            {"form": FORM, "tables": TABLES, "number": 1},
        ),
        ("POST", "/api/workbench/test-quotes/q/accept", {"after": 1, **write}),
        ("DELETE", "/api/workbench/test-quotes/q", None),
    ):
        answered = client().request(method, url, json=body)
        assert answered.status_code == 404, (url, answered.text)
