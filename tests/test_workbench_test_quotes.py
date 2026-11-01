"""The analyst's test quotes (specs/workbench, Test quotes; Control flow 37): saved from the
sample in Preview as files of the test-quote folder, opened, run, accepted and deleted
through the workbench's routes, and checked by ``haute app build``, against a real project
whose sheets price through a generated pipeline."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from haute import _workbench_test_quotes as test_quotes
from haute._sandbox import _get_project_root, set_project_root
from haute._workbench_app import TestQuotesFailedError, build_release, resolve_release
from haute._workbench_form import FormSpec
from haute._workbench_test_quotes import (
    TestQuoteError,
    basis,
    check_folder,
    read_test_quote,
    render_test_quotes_report,
)
from haute.deploy._validators import score_test_quote_file
from haute.routes._error_handlers import install_exception_handlers
from haute.routes.workbench import router
from tests._workbench_helpers import form_files, write_files
from tests.test_workbench_output import (
    FORM_SAMPLE,
    FORM_TABLES,
    _priced,
    _write,
)
from tests.test_workbench_routes import LEDGER, capture, form, git, init_repo

FOLDER = Path("tests") / "quotes"


def _sheets(project: Path, tables: list[dict[str, Any]] = FORM_TABLES, sample: Any = None) -> None:
    """The project's form folder holding *tables* and *sample* (the priced sample unless
    given)."""
    import shutil

    spec = FormSpec.model_validate(
        {
            **form(),
            "name": "motor",
            "schema": {"tables": tables},
            "sample": FORM_SAMPLE if sample is None else sample,
        }
    )
    folder = project / "workbench"
    if folder.exists():
        shutil.rmtree(folder)
    write_files(folder, form_files(spec, folder))


def _pipeline(project: Path, priced: str | None = None) -> None:
    """The project's pipeline: `_priced`, its policy premium code *priced* when given."""
    graph = _priced()
    if priced is not None:
        node = next(node for node in graph.nodes if node.id == "priced")
        node.data.config["code"] = priced
    _write(graph, project, "main")


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A workbench project in a repository with a working branch and its ledger, whose
    sample prices through a generated pipeline; the working directory."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "motor"\npipeline = "main.py"\n\n[workbench]\nenabled = true\n\n'
        '[workbench.app]\nhost = "azure-container-apps"\n',
        encoding="utf-8",
    )
    _pipeline(tmp_path)
    _sheets(tmp_path)
    init_repo(tmp_path)
    yield tmp_path
    set_project_root(original)


def client() -> TestClient:
    application = FastAPI()
    install_exception_handlers(application)
    application.include_router(router)
    return TestClient(application)


def _state(http: TestClient) -> tuple[str, str]:
    """The branch the panel reads on and the form folder's revision it saved."""
    branch = http.get("/api/workbench/test-quotes").json()["branch"]
    revision = http.get("/api/workbench/form").json()["revision"]
    return branch, revision


def _save(http: TestClient, name: str = "Fleet", **body: Any) -> Any:
    branch, revision = _state(http)
    return http.post(
        "/api/workbench/test-quotes",
        json={"name": name, "branch": branch, "form_revision": revision, **body},
    )


def _file(project: Path, name: str = "Fleet") -> Path:
    return project / FOLDER / f"{name}.json"


def _rows(project: Path, name: str = "Fleet") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(_file(project, name).read_text(encoding="utf-8"))
    return rows


def _run(http: TestClient, name: str = "Fleet") -> Any:
    return http.post(f"/api/workbench/test-quotes/{name}.json/run")


# ── Saving ────────────────────────────────────────────────────────────────


def test_a_saved_quote_is_one_file_in_deploys_shape_captured_on_the_ledger(
    project: Path,
) -> None:
    http = client()

    saved = _save(http)

    assert saved.status_code == 200, saved.text
    [row] = _rows(project)
    assert set(row) == {"input", "expected"}
    # The request Preview prices: the sample's, under the stand-in identity.
    assert row["input"]["policy"] == {"state": "NY", "limit": 1000}
    assert [item["item_id"] for item in row["input"]["items"]] == ["A", "B"]
    assert row["input"]["quote_details"]["quote_reference"] == "PREVIEW"
    assert row["input"]["quote_details"]["quote_option"] == 1
    # The price route's shape: every table a list, a one-row table's included.
    assert [each["premium"] for each in row["expected"]["pricing"]] == [100.0]
    assert [each["premium"] for each in row["expected"]["item_prices"]] == [20.0, 60.0]
    assert (
        _file(project).read_bytes().endswith(b"]\n") and b"\r\n" not in _file(project).read_bytes()
    )
    body = saved.json()
    assert body["quote"]["file"] == "Fleet.json"
    assert capture(body)["git_sha"] == git(project, "rev-parse", LEDGER)
    assert git(project, "show", "--name-only", "--format=", body["git_sha"]).splitlines() == [
        "tests/quotes/Fleet.json"
    ]


@pytest.mark.parametrize(
    ("name", "status"),
    [("a/b", 422), ("..", 422), ("x" * 101, 422), ("fleet", 409), ("FLEET", 409)],
    ids=["separator", "dots", "too-long", "lower-case", "upper-case"],
)
def test_a_name_is_refused_for_its_characters_length_or_another_files_in_any_case(
    project: Path, name: str, status: int
) -> None:
    http = client()
    assert _save(http).status_code == 200

    refused = _save(http, name)

    assert refused.status_code == status, refused.text
    assert sorted(path.name for path in (project / FOLDER).iterdir()) == ["Fleet.json"]


def test_nothing_typed_is_refused(project: Path) -> None:
    _sheets(project, sample={})

    refused = _save(client())

    assert (refused.status_code, refused.json()["detail"]) == (422, "Type the quote first.")
    assert not (project / FOLDER).exists()


def test_a_save_is_refused_once_the_form_was_saved_again_from_another_tab(
    project: Path,
) -> None:
    http = client()
    branch, revision = _state(http)
    _sheets(project, sample={"t_policy": [{"c_state": "CA", "c_limit": "5"}]})

    refused = http.post(
        "/api/workbench/test-quotes",
        json={"name": "Fleet", "branch": branch, "form_revision": revision},
    )

    assert refused.status_code == 409
    assert "saved again" in refused.json()["detail"]
    assert not _file(project).exists()


def test_a_save_decided_on_another_branch_is_refused(project: Path) -> None:
    http = client()
    _, revision = _state(http)

    refused = http.post(
        "/api/workbench/test-quotes",
        json={"name": "Fleet", "branch": "elsewhere", "form_revision": revision},
    )

    assert refused.status_code == 409
    assert not _file(project).exists()


def test_replacing_is_refused_once_the_file_changed(project: Path) -> None:
    http = client()
    revision = _save(http).json()["quote"]["revision"]
    _file(project).write_text(_file(project).read_text() + " ", encoding="utf-8")
    edited = _file(project).read_bytes()

    branch, form_revision = _state(http)
    refused = http.post(
        "/api/workbench/test-quotes",
        json={
            "replace": {"file": "Fleet.json", "revision": revision},
            "branch": branch,
            "form_revision": form_revision,
        },
    )

    assert refused.status_code == 409
    assert _file(project).read_bytes() == edited


def test_a_request_past_the_limit_is_refused_on_save_and_failed_on_a_run(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = client()
    assert _save(http).status_code == 200
    monkeypatch.setattr(test_quotes, "request_limit", lambda: 10)

    refused = _save(http, "Other")
    run = _run(http).json()

    assert refused.status_code == 422 and "larger than the app takes" in refused.json()["detail"]
    assert run["outcome"] == "failed" and "larger than the app takes" in run["why"]


# ── Listing and opening ───────────────────────────────────────────────────


def test_a_file_of_deploys_own_shape_is_listed_as_one_the_panel_cannot_open(
    project: Path,
) -> None:
    (project / FOLDER).mkdir(parents=True)
    (project / FOLDER / "batch.json").write_text(
        json.dumps([{"input": {"policy": {"state": "NY"}}}, {"input": {}}]), encoding="utf-8"
    )
    (project / FOLDER / "broken.json").write_text("{", encoding="utf-8")
    (project / FOLDER / "loose.json").write_text(
        json.dumps([{"input": {}, "expected": {"pricing": []}, "tolerance_pct": 5}]),
        encoding="utf-8",
    )

    listed = client().get("/api/workbench/test-quotes").json()["quotes"]

    problems = {quote["file"]: quote["problem"] for quote in listed}
    assert "deploy's test quotes" in problems["batch.json"]
    assert problems["broken.json"] == "it is not JSON"
    # Deploy's own rule for a tolerance: a fraction, at most 1.
    assert "tolerance_pct is a fraction" in problems["loose.json"]


def test_opening_puts_values_back_by_name_onto_the_form_the_view_holds(
    project: Path,
) -> None:
    http = client()
    assert _save(http).status_code == 200
    held = http.get("/api/workbench/form").json()["form"]

    opened = http.post("/api/workbench/test-quotes/Fleet.json/open", json={"form": held}).json()

    assert opened["values"]["t_policy"] == [{"c_state": "NY", "c_limit": "1000"}]
    assert opened["values"]["t_items"] == [
        {"c_item_id": "A", "c_value": "10"},
        {"c_item_id": "B", "c_value": "30"},
    ]
    assert opened["not_opened"] == []

    # Onto an unsaved schema that dropped a column and made another true/false.
    edited = json.loads(json.dumps(held))
    policy = next(t for t in edited["schema"]["tables"] if t["name"] == "policy")
    policy["columns"] = [
        {**column, "type": "bool"} for column in policy["columns"] if column["name"] == "limit"
    ]
    reopened = http.post("/api/workbench/test-quotes/Fleet.json/open", json={"form": edited}).json()

    assert sorted(reopened["not_opened"]) == ["policy.limit", "policy.state"]
    assert reopened["values"]["t_policy"] == [{}]


@pytest.mark.parametrize(
    "file",
    ["../haute.json", "a\\b.json", "CON.json", "Fleet", "Fleet.json.bak"],
    ids=["dots", "backslash", "reserved", "no-suffix", "other-suffix"],
)
def test_a_file_named_outside_the_folder_or_not_a_test_quotes_is_refused(
    project: Path, file: str
) -> None:
    with pytest.raises(TestQuoteError):
        test_quotes.test_quote_file(project, project / FOLDER, file)


def test_a_folder_reached_through_a_link_is_refused(project: Path, tmp_path_factory: Any) -> None:
    from tests.test_workbench_routes import _link_directory

    elsewhere = tmp_path_factory.mktemp("elsewhere")
    (project / "tests").mkdir(exist_ok=True)
    _link_directory(elsewhere, project / FOLDER)

    with pytest.raises(TestQuoteError):
        test_quotes.test_quote_file(project, project / FOLDER, "Fleet.json")


# ── Running ───────────────────────────────────────────────────────────────


def test_a_run_is_unchanged_until_the_pipeline_moves_a_price(project: Path) -> None:
    http = client()
    assert _save(http).status_code == 200

    unchanged = _run(http).json()
    _pipeline(
        project, "df = policy.with_columns(premium=pl.col('limit') / 5, referral=pl.lit('none'))"
    )
    moved = _run(http).json()

    assert unchanged["outcome"] == "unchanged"
    assert unchanged["revision"] == read_test_quote(_file(project)).revision
    assert moved["outcome"] == "moved"
    assert moved["moves"] == [
        {"table": "pricing", "row": None, "column": "premium", "before": 100.0, "after": 200.0}
    ]
    assert moved["basis"] != unchanged["basis"]


def test_keyed_rows_reordered_and_numbers_within_tolerance_are_unchanged(
    project: Path,
) -> None:
    keyed = json.loads(json.dumps(FORM_TABLES))
    prices = next(t for t in keyed if t["name"] == "item_prices")
    prices["columns"][0]["key"] = True
    _sheets(project, keyed)
    http = client()
    assert _save(http).status_code == 200
    [row] = _rows(project)
    row["expected"]["item_prices"].reverse()
    row["expected"]["pricing"][0]["premium"] = 101.0
    row["tolerance_pct"] = 0.05
    row["_note"] = "kept by hand"
    _file(project).write_text(json.dumps([row]), encoding="utf-8")

    result = _run(http).json()

    assert result["outcome"] == "unchanged", result


def test_a_column_the_form_dropped_is_a_removed_field(project: Path) -> None:
    http = client()
    assert _save(http).status_code == 200
    [row] = _rows(project)
    row["expected"]["pricing"][0]["discount"] = 5.0
    _file(project).write_text(json.dumps([row]), encoding="utf-8")

    result = _run(http).json()

    assert (result["outcome"], result["removed"], result["moves"]) == (
        "moved",
        ["pricing.discount"],
        [],
    )


def test_a_key_held_twice_no_longer_compares(project: Path) -> None:
    keyed = json.loads(json.dumps(FORM_TABLES))
    next(t for t in keyed if t["name"] == "item_prices")["columns"][0]["key"] = True
    _sheets(project, keyed)
    http = client()
    assert _save(http).status_code == 200
    [row] = _rows(project)
    row["expected"]["item_prices"][1]["item_id"] = "A"
    _file(project).write_text(json.dumps([row]), encoding="utf-8")

    result = _run(http).json()

    assert result["outcome"] == "no_longer_compares"
    assert "holds the key item_id=A twice" in result["why"]


#: A pipeline that resolves, but fails on the quote's own values: a state read as a number.
FAILS_ON_THE_QUOTE = "df = policy.with_columns(premium=pl.col('state').cast(pl.Int64))"


def test_a_pipeline_failing_on_the_quote_fails_it(project: Path) -> None:
    http = client()
    assert _save(http).status_code == 200
    _pipeline(project, FAILS_ON_THE_QUOTE)

    result = _run(http).json()

    assert result["outcome"] == "failed" and result["why"]
    assert result["moves"] == []


def test_a_pricing_past_its_deadline_fails_and_closes_its_release_when_it_ends(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = client()
    assert _save(http).status_code == 200
    release_event = threading.Event()
    closed: list[bool] = []
    real_check = test_quotes.check_test_quote

    def slow(release: Any, quote: Any) -> Any:
        release_event.wait(5)
        original_close = release.close

        def close() -> None:
            closed.append(True)
            original_close()

        object.__setattr__(release, "close", close)
        return real_check(release, quote)

    import haute.routes.workbench as routes

    monkeypatch.setattr(routes, "check_test_quote", slow)
    monkeypatch.setattr(routes, "RUN_TIMEOUT_SECONDS", 0.05)

    result = _run(http).json()
    release_event.set()

    assert result["outcome"] == "failed" and "took longer than" in result["why"]
    before = _file(project).read_bytes()
    for _ in range(100):
        if closed:
            break
        threading.Event().wait(0.05)
    assert closed == [True]
    assert _file(project).read_bytes() == before


# ── Accepting and deleting ────────────────────────────────────────────────


def _accept(http: TestClient, run: dict[str, Any], **overrides: Any) -> Any:
    body = {
        "branch": run["branch"],
        "basis": run["basis"],
        "quotes": [{"file": run["file"], "revision": run["revision"]}],
        **overrides,
    }
    return http.post("/api/workbench/test-quotes/accept", json=body)


def test_accepting_prices_again_and_writes_the_new_answer(project: Path) -> None:
    http = client()
    assert _save(http).status_code == 200
    _pipeline(
        project, "df = policy.with_columns(premium=pl.col('limit') / 5, referral=pl.lit('none'))"
    )
    run = _run(http).json()

    accepted = _accept(http, run)

    assert accepted.status_code == 200, accepted.text
    assert [quote["file"] for quote in accepted.json()["accepted"]] == ["Fleet.json"]
    assert [each["premium"] for each in _rows(project)[0]["expected"]["pricing"]] == [200.0]
    assert _run(http).json()["outcome"] == "unchanged"


def test_accepting_is_refused_once_the_pipeline_changed_since_the_run(project: Path) -> None:
    http = client()
    assert _save(http).status_code == 200
    before = _file(project).read_bytes()
    run = _run(http).json()
    _pipeline(
        project, "df = policy.with_columns(premium=pl.col('limit') / 5, referral=pl.lit('none'))"
    )

    refused = _accept(http, run)

    assert refused.status_code == 409
    assert _file(project).read_bytes() == before


def test_accepting_names_a_file_changed_since_the_run_and_leaves_it(project: Path) -> None:
    http = client()
    assert _save(http).status_code == 200
    run = _run(http).json()
    _file(project).write_text(_file(project).read_text() + " ", encoding="utf-8")
    edited = _file(project).read_bytes()

    answered = _accept(http, run).json()

    assert answered["accepted"] == []
    assert answered["refused"] == [{"file": "Fleet.json", "why": "it changed since the run"}]
    assert _file(project).read_bytes() == edited


def test_an_edit_made_outside_haute_while_an_acceptance_priced_is_kept(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = client()
    assert _save(http).status_code == 200
    run = _run(http).json()
    import haute.routes.workbench as routes

    real_price = routes.price_request

    def edited_meanwhile(release: Any, request: Any) -> Any:
        answer = real_price(release, request)
        _file(project).write_text(_file(project).read_text() + " ", encoding="utf-8")
        return answer

    monkeypatch.setattr(routes, "price_request", edited_meanwhile)

    answered = _accept(http, run).json()

    assert answered["refused"] == [{"file": "Fleet.json", "why": "it changed while it priced"}]
    assert _file(project).read_text().endswith(" ")


def test_an_acceptance_decided_on_another_branch_is_refused(project: Path) -> None:
    http = client()
    assert _save(http).status_code == 200
    run = _run(http).json()

    assert _accept(http, run, branch="elsewhere").status_code == 409


def test_deleting_is_refused_once_the_file_changed_and_removes_it_otherwise(
    project: Path,
) -> None:
    http = client()
    saved = _save(http).json()
    branch = http.get("/api/workbench/test-quotes").json()["branch"]

    stale = http.delete(
        "/api/workbench/test-quotes/Fleet.json", params={"revision": "0" * 64, "branch": branch}
    )
    other = http.delete(
        "/api/workbench/test-quotes/Fleet.json",
        params={"revision": saved["quote"]["revision"], "branch": "elsewhere"},
    )
    deleted = http.delete(
        "/api/workbench/test-quotes/Fleet.json",
        params={"revision": saved["quote"]["revision"], "branch": branch},
    )

    assert (stale.status_code, other.status_code, deleted.status_code) == (409, 409, 200)
    assert not _file(project).exists()
    assert deleted.json()["git_sha"] == git(project, "rev-parse", LEDGER)


# ── The basis ─────────────────────────────────────────────────────────────


def test_the_basis_moves_with_helper_code_outputs_and_artifact_content(project: Path) -> None:
    release = resolve_release(project)
    try:
        first = basis(release)
        # The sample is typing: it changes nothing a test quote replays.
        assert basis(release) == first
        release.resolved.artifact_digests["model"] = "a" * 64
        with_artifact = basis(release)
        release.resolved.artifact_digests["model"] = "b" * 64
        before_outputs = basis(release)
        assert before_outputs != with_artifact != first
        release.resolved.config.output_fields = ["premium"]
        assert basis(release) != before_outputs
    finally:
        release.close()
    (project / "utility.py").write_text("RATE = 1\n", encoding="utf-8")
    (project / "main.py").write_text(
        "import utility  # noqa\n" + (project / "main.py").read_text(), encoding="utf-8"
    )
    helper = resolve_release(project)
    try:
        with_helper = basis(helper)
    finally:
        helper.close()
    (project / "utility.py").write_text("RATE = 2\n", encoding="utf-8")
    changed = resolve_release(project)
    try:
        assert basis(changed) != with_helper
    finally:
        changed.close()


# ── The build ─────────────────────────────────────────────────────────────


def test_the_build_passes_with_a_moved_quote_and_reports_it(project: Path) -> None:
    assert _save(client()).status_code == 200
    _pipeline(
        project, "df = policy.with_columns(premium=pl.col('limit') / 5, referral=pl.lit('none'))"
    )

    built = build_release(project, project / "build" / "app")

    [result] = built.test_quotes
    assert result.outcome == "moved"
    report = render_test_quotes_report(built.test_quotes)
    assert "| Moved | 1 |" in report and "`pricing.premium`" in report


def test_the_build_fails_on_a_quote_that_does_not_price_carrying_every_result(
    project: Path,
) -> None:
    assert _save(client()).status_code == 200
    _pipeline(project, FAILS_ON_THE_QUOTE)

    with pytest.raises(TestQuotesFailedError, match="Fleet.json") as failed:
        build_release(project, project / "build" / "app")

    assert [result.outcome for result in failed.value.results] == ["failed"]


def test_the_build_fails_on_a_deploy_test_quote_that_does_not_match(project: Path) -> None:
    (project / FOLDER).mkdir(parents=True)
    (project / FOLDER / "golden.json").write_text(
        json.dumps(
            [
                {
                    "input": {"policy": {"state": "NY", "limit": 1000}},
                    "expected": {"pricing": {"premium": 1.0}},
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TestQuotesFailedError, match="golden.json"):
        build_release(project, project / "build" / "app")
    # The same file, scored as an automated project's dry run scores it, fails as well.
    release = resolve_release(project)
    try:
        assert (
            score_test_quote_file(release.resolved, project / FOLDER / "golden.json")["status"]
            == "error"
        )
        assert [r.outcome for r in check_folder(release, project / FOLDER)] == ["failed"]
    finally:
        release.close()


def test_a_project_with_no_test_quotes_builds(project: Path) -> None:
    built = build_release(project, project / "build" / "app")

    assert built.test_quotes == ()
    assert render_test_quotes_report(()) == "# Test quotes\n\nThe project keeps no test quotes.\n"


def test_a_schedule_past_the_preview_routes_rows_is_kept_and_accepted_whole(
    project: Path,
) -> None:
    items = [{"c_item_id": f"I{n}", "c_value": str(n)} for n in range(10_050)]
    _sheets(project, sample={**FORM_SAMPLE, "t_items": items})
    http = client()
    assert _save(http).status_code == 200
    assert len(_rows(project)[0]["expected"]["item_prices"]) == 10_050

    graph = _priced()
    next(node for node in graph.nodes if node.id == "item_premiums").data.config["code"] = (
        "df = items.with_columns(premium=pl.col('value') * 3)"
    )
    _write(graph, project, "main")
    run = _run(http).json()
    assert run["outcome"] == "moved" and len(run["moves"]) == 10_049  # item 0 stays at 0
    assert _accept(http, run).status_code == 200

    expected = _rows(project)[0]["expected"]["item_prices"]
    assert len(expected) == 10_050 and expected[-1]["premium"] == 3 * 10_049


@pytest.mark.parametrize(
    ("tolerance", "says"),
    [(True, "non-negative number"), (5, "tolerance_pct is a fraction"), (float("inf"), "finite")],
    ids=["true", "five", "infinite"],
)
def test_a_tolerance_deploy_refuses_is_listed_and_fails_the_build(
    project: Path, tolerance: Any, says: str
) -> None:
    http = client()
    assert _save(http).status_code == 200
    [row] = _rows(project)
    row["tolerance_pct"] = tolerance
    _file(project).write_text(json.dumps([row]), encoding="utf-8")

    [listed] = http.get("/api/workbench/test-quotes").json()["quotes"]
    assert says in listed["problem"]
    with pytest.raises(TestQuotesFailedError, match=says):
        build_release(project, project / "build" / "app")


@pytest.mark.parametrize("fails", [False, True], ids=["moved", "fails"])
def test_haute_app_build_writes_the_report_whether_it_passes_or_fails(
    project: Path, runner: Any, fails: bool
) -> None:
    from haute.cli import cli

    assert _save(client()).status_code == 200
    _pipeline(
        project,
        FAILS_ON_THE_QUOTE
        if fails
        else "df = policy.with_columns(premium=pl.col('limit') / 5, referral=pl.lit('none'))",
    )

    result = runner.invoke(cli, ["app", "build"])

    report = (project / "test_quotes_report.md").read_bytes().decode("utf-8")
    if fails:
        assert result.exit_code == 1
        assert "Test quote Fleet.json failed" in result.output
        assert "## Failed" in report and "`Fleet.json`" in report
    else:
        assert result.exit_code == 0, result.output
        assert "Test quote Fleet.json moved: pricing.premium" in result.output
        assert "## Moved" in report
    assert b"\r\n" not in (project / "test_quotes_report.md").read_bytes()


def test_accepting_the_project_answers_fewer_outputs_since_the_run_is_refused(
    project: Path,
) -> None:
    http = client()
    assert _save(http).status_code == 200
    run = _run(http).json()
    before = _file(project).read_bytes()
    toml = project / "haute.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8") + '\n[deploy]\noutput_fields = ["pricing"]\n',
        encoding="utf-8",
    )
    # A projection the pipeline answers: the project still resolves.
    resolve_release(project).close()

    refused = _accept(http, run)

    assert refused.status_code == 409
    assert "What the run priced on changed since" in refused.json()["detail"]
    assert _file(project).read_bytes() == before


def test_a_helper_changed_while_a_run_priced_keeps_its_result_from_being_accepted(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes.workbench as routes

    (project / "utility.py").write_text("RATE = 1\n", encoding="utf-8")
    (project / "main.py").write_text(
        "import utility  # noqa\n" + (project / "main.py").read_text(), encoding="utf-8"
    )
    http = client()
    assert _save(http).status_code == 200
    real_check = routes.check_test_quote

    def helper_edited_meanwhile(release: Any, quote: Any) -> Any:
        checked = real_check(release, quote)
        (project / "utility.py").write_text("RATE = 2\n", encoding="utf-8")
        return checked

    monkeypatch.setattr(routes, "check_test_quote", helper_edited_meanwhile)
    run = _run(http).json()
    monkeypatch.setattr(routes, "check_test_quote", real_check)
    before = _file(project).read_bytes()

    refused = _accept(http, run)

    assert refused.status_code == 409
    assert "What the run priced on changed since" in refused.json()["detail"]
    assert _file(project).read_bytes() == before


def test_accepting_two_keeps_an_edit_made_to_the_first_while_the_second_priced(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes.workbench as routes

    http = client()
    assert _save(http).status_code == 200
    assert _save(http, "Second").status_code == 200
    first, second = _run(http).json(), _run(http, "Second").json()
    real_price = routes.price_request
    priced: list[str] = []

    def second_edits_the_first(release: Any, request: Any) -> Any:
        priced.append("priced")
        if len(priced) == 2:
            _file(project).write_text(_file(project).read_text() + " ", encoding="utf-8")
        return real_price(release, request)

    monkeypatch.setattr(routes, "price_request", second_edits_the_first)

    answered = _accept(
        http,
        first,
        quotes=[
            {"file": "Fleet.json", "revision": first["revision"]},
            {"file": "Second.json", "revision": second["revision"]},
        ],
    ).json()

    # The first was staged before the second priced: the edit after it is the file's now.
    assert [quote["file"] for quote in answered["accepted"]] == ["Fleet.json", "Second.json"]
    assert _file(project).read_text().endswith(" ")


def test_a_run_whose_resolution_runs_past_the_deadline_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes.workbench as routes

    http = client()
    assert _save(http).status_code == 200
    resolving = threading.Event()
    real_resolve = routes.resolve_release

    def slow_resolve(root: Path) -> Any:
        resolving.wait(5)
        return real_resolve(root)

    monkeypatch.setattr(routes, "resolve_release", slow_resolve)
    monkeypatch.setattr(routes, "RUN_TIMEOUT_SECONDS", 0.05)

    run = _run(http).json()
    saved = _save(http, "Other")
    resolving.set()

    assert run["outcome"] == "failed" and "took longer than" in run["why"]
    assert run["basis"] is None
    assert saved.status_code == 422 and "took longer than" in saved.json()["detail"]
