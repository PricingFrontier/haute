"""Test quotes kept in Preview (specs/workbench): the project's test-quote file, the request and
response saved with a quote, and the routes that save, list, open, compare, accept and delete
them, each write captured on the save ledger as the form's saves are."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

from haute import _git
from haute._quote_store import migrate_store, open_store, store_url
from haute._test_quotes import (
    WorkbenchTestQuoteError,
    project_gitattributes_path,
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
    """A save on the branch the branch route names, as the panel makes one."""
    branch = client().get("/api/workbench/test-quotes/branch").json()["branch"]
    return client().post(
        "/api/workbench/test-quotes",
        json={"name": name, "form": FORM, "values": VALUES, "tables": tables, "branch": branch},
    )


# ── The file, the request and the response ───────────────────────────────


def test_the_file_is_in_the_test_quote_directory(project: Path) -> None:
    assert project_test_quotes_path(project) == (project / PATH).resolve()

    (project / "haute.toml").write_text('[test_quotes]\ndir = "quotes"\n', encoding="utf-8")
    assert project_test_quotes_path(project) == (project / "quotes/test_quotes.sqlite").resolve()

    (project / "haute.toml").write_text('[test_quotes]\ndir = "../elsewhere"\n', encoding="utf-8")
    with pytest.raises(WorkbenchTestQuoteError, match="inside the project"):
        project_test_quotes_path(project)


def _link_outside(link: Path, outside: Path) -> None:
    """A link at *link* to a new folder in *outside*: a junction on Windows, which anyone
    can make, a symlink elsewhere."""
    target = outside / f"{link.name}-target"
    target.mkdir()
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_test_quote_file_leading_outside_the_project_is_refused_and_nothing_written(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    enable(project)
    outside = tmp_path_factory.mktemp("outside")
    (project / "tests" / "quotes").mkdir(parents=True)
    _link_outside(project / PATH, outside)

    with pytest.raises(WorkbenchTestQuoteError, match="a file inside it"):
        project_test_quotes_path(project)
    refused = save()

    assert refused.status_code == 409, refused.text
    assert [entry.name for entry in outside.rglob("*")] == ["test_quotes.sqlite-target"]


def test_a_gitattributes_leading_outside_the_project_refuses_the_write_before_it_is_made(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    enable(project)
    outside = tmp_path_factory.mktemp("outside")
    _link_outside(project / ".gitattributes", outside)

    with pytest.raises(WorkbenchTestQuoteError, match=".gitattributes is reached through a link"):
        project_gitattributes_path(project)
    refused = save()

    # Refused before the quote was written: nothing to save twice on a retry.
    assert refused.status_code == 409, refused.text
    assert ".gitattributes is reached through a link" in refused.json()["detail"]
    assert not (project / PATH).exists()
    assert [entry.name for entry in outside.rglob("*")] == [".gitattributes-target"]


def test_a_hard_linked_test_quote_file_is_refused_and_the_file_it_shares_left_alone(
    project: Path,
) -> None:
    enable(project)
    (project / "tests" / "quotes").mkdir(parents=True)
    shared = project / "shared.sqlite"
    shared.write_bytes(b"another file's bytes")
    os.link(shared, project / PATH)

    with pytest.raises(WorkbenchTestQuoteError, match=f"{PATH} is a hard link"):
        project_test_quotes_path(project)
    refused = save()

    assert refused.status_code == 409, refused.text
    assert shared.read_bytes() == b"another file's bytes"


def test_a_hard_linked_gitattributes_refuses_the_write_before_it_is_made(project: Path) -> None:
    enable(project)
    shared = project / "shared-attributes"
    shared.write_text("*.csv text\n", encoding="utf-8")
    os.link(shared, project / ".gitattributes")

    with pytest.raises(WorkbenchTestQuoteError, match=".gitattributes is a hard link"):
        project_gitattributes_path(project)
    refused = save()

    assert refused.status_code == 409, refused.text
    assert not (project / PATH).exists()
    assert shared.read_text(encoding="utf-8") == "*.csv text\n"


def test_a_save_started_on_another_branch_is_refused_and_nothing_written(
    project: Path,
) -> None:
    enable(project)
    init_repo(project)
    named = client().get("/api/workbench/test-quotes/branch").json()
    assert named["branch"] == git(project, "symbolic-ref", "--short", "HEAD")

    # Checked out elsewhere while the quote was priced: the save names the branch it began on.
    refused = client().post(
        "/api/workbench/test-quotes",
        json={"name": "n", "form": FORM, "values": VALUES, "tables": TABLES, "branch": "other"},
    )

    assert refused.status_code == 409
    assert "branch changed while the quote was priced" in refused.json()["detail"]
    assert not (project / PATH).exists()
    assert save().status_code == 200


def test_a_gitattributes_that_cannot_be_written_refuses_the_save_before_it_is_made(
    project: Path,
) -> None:
    enable(project)
    init_repo(project)
    attributes = project / ".gitattributes"
    attributes.write_text("* text=auto\n", encoding="utf-8")
    attributes.chmod(stat.S_IREAD)
    try:
        refused = save()
    finally:
        attributes.chmod(stat.S_IREAD | stat.S_IWRITE)

    # Refused before the quote was written: a retry saves it once.
    assert refused.status_code == 409, refused.text
    assert ".gitattributes could not be written, so nothing was saved" in refused.text
    assert not (project / PATH).exists()
    assert save().status_code == 200
    [listed] = client().get("/api/workbench/test-quotes").json()["quotes"]
    assert listed["number"] == 1


def test_a_test_quote_folder_with_a_space_keeps_its_drivers(project: Path) -> None:
    enable(project, '[workbench]\nenabled = true\n\n[test_quotes]\ndir = "test quotes"\n')
    init_repo(project)

    assert save().status_code == 200
    saved = "test quotes/test_quotes.sqlite"

    assert (project / ".gitattributes").read_text(encoding="utf-8") == (
        f'"{saved}" diff=haute-test-quotes merge=haute-test-quotes\n'
    )
    assert git(project, "check-attr", "diff", "merge", "--", saved).splitlines() == [
        f"{saved}: diff: haute-test-quotes",
        f"{saved}: merge: haute-test-quotes",
    ]
    # A second save finds the line it wrote, quoted, and adds no other.
    assert save("Southern fleet").status_code == 200
    assert (project / ".gitattributes").read_text(encoding="utf-8").count("diff=") == 1


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


def test_responses_are_compared_as_a_deployed_pipeline_renders_them() -> None:
    # Preview keeps a null and an empty table; a deployed pipeline leaves both out.
    accepted = {"pricing": {"premium": 100.0, "note": None}, "items": [], "extras": {}}
    rendered = {"pricing": {"premium": 100.0}}

    assert response_changes(rendered, accepted, 1e-9) == []
    assert response_changes(accepted, rendered, 1e-9) == []
    assert [tuple(change) for change in response_changes({"pricing": {}}, accepted, 1e-9)] == [
        ("pricing", "removed", {"premium": 100.0}, None)
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
    captured = git(project, "show", "--name-only", "--format=", LEDGER).splitlines()
    assert sorted(captured) == [".gitattributes", PATH]
    # The clone's drivers show the file as text in a diff and merge it quote by quote.
    assert (project / ".gitattributes").read_text(encoding="utf-8") == (
        f"{PATH} diff=haute-test-quotes merge=haute-test-quotes\n"
    )
    assert "haute.cli._test_quotes merge %O %A %B" in git(
        project, "config", "--local", "merge.haute-test-quotes.driver"
    )
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
        json={
            "form": FORM,
            "values": VALUES,
            "tables": repriced,
            "number": 1,
            "revision_digest": opened(quote_id)["revision_digest"],
        },
    )

    assert compared.status_code == 200, compared.text
    assert compared.json()["revision_digest"] == opened(quote_id)["revision_digest"]
    assert compared.json()["changes"] == [
        {
            "path": "pricing_output.premium",
            "kind": "changed",
            "accepted": 512.4,
            "priced": 530.0,
        }
    ]


def opened(quote_id: str) -> dict[str, Any]:
    answered = client().post(f"/api/workbench/test-quotes/{quote_id}/open", json={"form": FORM})
    assert answered.status_code == 200, answered.text
    return dict(answered.json())


def test_a_compare_of_a_revision_the_run_did_not_open_is_refused(project: Path) -> None:
    # The run opened revision 1 as another branch saved it.
    enable(project)
    quote_id = save().json()["quote"]["id"]

    refused = client().post(
        f"/api/workbench/test-quotes/{quote_id}/compare",
        json={
            "form": FORM,
            "values": VALUES,
            "tables": TABLES,
            "number": 1,
            "revision_digest": "another-branch",
        },
    )

    assert refused.status_code == 409
    assert "stale_quote_revision" in refused.text
    assert "not the one the run opened" in refused.text


def test_a_compare_asks_for_an_accept_when_the_request_a_deploy_prices_has_moved(
    project: Path,
) -> None:
    """A column renamed since the save: Preview's request uses the new name, and the one the
    revision keeps, which a deploy prices, the old; the compare names both places, and the
    accept keeps the request the run priced."""
    enable(project)
    quote_id = save().json()["quote"]["id"]
    renamed = form(
        {**POLICY, "columns": [POLICY["columns"][0], column("sum_insured", "float")]},
        PRICING,
        ITEMS,
    )
    # The column kept its id, so the value typed under its old name is carried onto it.
    renamed["schema"]["tables"][0]["columns"][1]["id"] = "c_exposure"
    carried = (
        client().post(f"/api/workbench/test-quotes/{quote_id}/open", json={"form": renamed}).json()
    )

    compared = client().post(
        f"/api/workbench/test-quotes/{quote_id}/compare",
        json={
            "form": renamed,
            "values": carried["values"],
            "tables": TABLES,
            "number": 1,
            "revision_digest": carried["revision_digest"],
        },
    )

    assert compared.status_code == 200, compared.text
    assert {(change["path"], change["kind"]) for change in compared.json()["changes"]} == {
        ("request.policy_details.sum_insured", "added"),
        ("request.policy_details.exposure", "removed"),
    }
    accepted = client().post(
        f"/api/workbench/test-quotes/{quote_id}/accept",
        json={
            "after": 1,
            "revision_digest": compared.json()["revision_digest"],
            "form": renamed,
            "values": carried["values"],
            "tables": TABLES,
        },
    )
    assert accepted.status_code == 200, accepted.text
    with open_store(store_url(project / PATH)) as store:
        assert store.revision(quote_id).request == {
            "policy_details": {"state": "NY", "sum_insured": 1000.0}
        }


def test_an_accept_follows_the_newest_and_a_stale_one_writes_nothing(project: Path) -> None:
    enable(project)
    quote_id = save().json()["quote"]["id"]
    repriced = {**TABLES, "pricing_output": [{"premium": 530.0}]}
    digest = opened(quote_id)["revision_digest"]
    body = {
        "after": 1,
        "revision_digest": digest,
        "form": FORM,
        "values": VALUES,
        "tables": repriced,
    }

    # A revision 1 read as another branch saved it is not the one here.
    other = client().post(
        f"/api/workbench/test-quotes/{quote_id}/accept",
        json={**body, "revision_digest": "elsewhere"},
    )
    assert other.status_code == 409
    assert "saved apart, on another branch" in other.text

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
    assert client().get("/api/workbench/test-quotes").json()["quotes"] == []
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

    assert client().get("/api/workbench/test-quotes").json()["quotes"] == []
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
        ("POST", "/api/workbench/test-quotes", {"name": "n", "branch": None, **write}),
        ("POST", "/api/workbench/test-quotes/q/open", {"form": FORM}),
        (
            "POST",
            "/api/workbench/test-quotes/q/compare",
            {"form": FORM, "values": VALUES, "tables": TABLES, "number": 1, "revision_digest": "d"},
        ),
        (
            "POST",
            "/api/workbench/test-quotes/q/accept",
            {"after": 1, "revision_digest": "t", **write},
        ),
        ("DELETE", "/api/workbench/test-quotes/q", None),
        ("POST", "/api/workbench/test-quotes/capture", None),
        ("GET", "/api/workbench/test-quotes/branch", None),
    ):
        answered = client().request(method, url, json=body)
        assert answered.status_code == 404, (url, answered.text)


def test_a_write_the_ledger_did_not_capture_is_captured_again_without_writing(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(project)
    init_repo(project)
    # Nothing to capture before there is a file, and capturing makes none.
    nothing = client().post("/api/workbench/test-quotes/capture")
    assert nothing.status_code == 200, nothing.text
    assert not (project / PATH).exists()

    # No commit identity: the save lands, and the response says the capture waits on one.
    identity = _git.get_identity
    monkeypatch.setattr(_git, "get_identity", lambda cwd=None: (None, None))
    saved = save()
    assert saved.status_code == 200, saved.text
    assert saved.json()["identity_required"] is True
    assert PATH not in git(project, "ls-tree", "-r", "--name-only", LEDGER).splitlines()
    monkeypatch.setattr(_git, "get_identity", identity)

    captured = client().post("/api/workbench/test-quotes/capture")

    assert captured.status_code == 200, captured.text
    assert captured.json()["git_sha"] is not None
    assert captured.json()["quote"] is None
    assert PATH in git(project, "ls-tree", "-r", "--name-only", LEDGER).splitlines()
    # The retry wrote nothing: still the one test quote, at its first revision.
    [listed] = client().get("/api/workbench/test-quotes").json()["quotes"]
    assert listed["number"] == 1
