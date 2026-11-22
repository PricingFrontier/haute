"""The packaged underwriter app tested before any host sees it (specs/workbench, Control
flow 39, WB-08): ``haute app test`` serves a built release on a store made for the run alone
and takes each test quote through the app's own routes."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from haute._project_context import current_project
from haute._quote_store import StoreLocation, store_url
from haute._workbench_app import build_release, read_release
from haute._workbench_app_test import (
    ENVIRONMENT,
    run_app_test,
    server_store,
    sqlite_store,
)
from haute._workbench_form import FormSpec, with_quote_details
from haute._workbench_input import with_stand_in
from haute._workbench_tables import input_tables, quote_request
from tests._project import use_project
from tests._store_servers import Target, configured, server_sign_in
from tests._workbench_helpers import form_files, write_files
from tests.test_workbench_app import PROJECT_TABLES, PROJECT_VALUES
from tests.test_workbench_routes import form, section

#: The test quote the runs take through the app: Preview's sample, priced as the app prices it.
DETAILS = {"quote_details": [{"insured": "Acme"}]}


@pytest.fixture()
def project(tmp_path: Path) -> Iterator[Path]:
    """A workbench project whose sheets price through a generated pipeline, its working
    directory, as tests/test_workbench_app.py makes one; the project root put back after."""
    from tests.test_workbench_output import _priced, _write

    original = current_project().root
    use_project(tmp_path)
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "motor"\npipeline = "main.py"\n\n[workbench]\nenabled = true\n\n'
        '[workbench.app]\nhost = "azure-container-apps"\n',
        encoding="utf-8",
    )
    _write(_priced(), tmp_path, "main")
    yield tmp_path
    use_project(original)


def _export(cells: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "id": "w_slip",
        "name": "slip",
        "type": "export",
        "x": 0,
        "y": 0,
        "w": 160,
        "h": 40,
        "label": "Slip",
        "tables": [],
        "target": {
            "format": "excel_cells",
            "file_name": "{quote_reference}",
            "cells": cells,
            "blocks": [],
        },
    }


def _sheets(project: Path, *widgets: dict[str, Any]) -> FormSpec:
    """The project's form folder holding PROJECT_TABLES and *widgets* on its one sheet."""
    spec = FormSpec.model_validate(
        {
            **form(),
            "name": "motor",
            "schema": {"tables": PROJECT_TABLES},
            "pages": [
                {"id": "p", "title": "Sheet 1", "width": 1280, "sections": [section("s", *widgets)]}
            ],
        }
    )
    folder = project / "workbench"
    shutil.rmtree(folder, ignore_errors=True)
    write_files(folder, form_files(spec, folder))
    return spec


def _cell(table: str, column: str, address: str) -> dict[str, str]:
    return {"table": table, "column": column, "sheet": "Slip", "cell": address, "label_cell": ""}


def _run(project: Path, spec: FormSpec, quotes: list[tuple[str, Any]]) -> Any:
    build_release(project, project / "build" / "app")
    with sqlite_store(project / "build" / "app") as location:
        return run_app_test(project / "build" / "app", location, quotes)


def _request(spec: FormSpec) -> dict[str, Any]:
    """The request a test quote saved from Preview holds: the sample's, under the stand-in."""
    full = with_quote_details(spec)
    return with_stand_in(input_tables(full), quote_request(full, {**PROJECT_VALUES, **DETAILS}))


def test_a_test_quote_is_priced_saved_opened_and_exported_through_the_app(
    project: Path,
) -> None:
    spec = _sheets(
        project,
        _export(
            [_cell("quote_details", "quote_reference", "B1"), _cell("pricing", "premium", "B2")]
        ),
    )

    report = _run(project, spec, [("Fleet", _request(spec))])

    assert report.failed == []
    assert report.passed == [
        "the app opened its store",
        "Fleet: priced, saved and opened again",
        'Fleet: exported by "Slip"',
    ]


def test_an_export_that_cannot_be_written_fails_the_run_naming_it(project: Path) -> None:
    # A cell of a many-row table, which holds a value per row: a problem of the Export's.
    spec = _sheets(project, _export([_cell("items", "value", "B1")]))

    report = _run(project, spec, [("Fleet", _request(spec))])

    assert report.failed == [
        'Fleet: exported by "Slip", HTTP 422: Export "Slip" cannot be written: it writes '
        "items.value, a column of a many-row table."
    ]


def test_with_no_test_quotes_the_app_opens_its_store_and_saves_nothing(project: Path) -> None:
    spec = _sheets(project)

    report = _run(project, spec, [])

    assert (report.passed, report.failed) == (["the app opened its store"], [])
    assert report.notes == ["no test quotes: the app opened its store and saved no quote"]


def test_a_store_whose_tables_do_not_fit_fails_the_run(project: Path, tmp_path: Path) -> None:
    import sqlite3

    _sheets(project)
    release = project / "build" / "app"
    build_release(project, release)
    # The release's tables, but for a column renamed since.
    store = Target("sqlite", store_url(tmp_path / "other.sqlite"))
    store.make(read_release(release)[1])
    with sqlite3.connect(tmp_path / "other.sqlite") as raw:
        raw.execute('ALTER TABLE policy RENAME COLUMN "limit" TO limit_old')

    report = run_app_test(release, StoreLocation(ENVIRONMENT, store.url), [])

    assert report.passed == []
    assert len(report.failed) == 1
    assert report.failed[0].startswith(
        "the app opened its store: This database's tables do not fit"
    )


def test_haute_app_test_takes_the_projects_test_quotes_through_the_built_app(
    project: Path,
) -> None:
    import json

    from click.testing import CliRunner

    from haute.cli import cli

    spec = _sheets(project, _export([_cell("pricing", "premium", "B2")]))
    quotes = project / "tests" / "quotes"
    quotes.mkdir(parents=True)
    (quotes / "Fleet.json").write_text(
        json.dumps([{"input": _request(spec), "expected": {}}]), encoding="utf-8"
    )
    built = CliRunner().invoke(cli, ["app", "build", "out"])
    assert built.exit_code == 0, built.output

    passed = CliRunner().invoke(cli, ["app", "test", "out"])

    assert passed.exit_code == 0, passed.output
    assert "✓ A store of the release's tables made on SQLite for the run" in passed.output
    assert '✓ Fleet: exported by "Slip"' in passed.output

    _sheets(project, _export([_cell("items", "value", "B1")]))
    assert CliRunner().invoke(cli, ["app", "build", "out"]).exit_code == 0
    failed = CliRunner().invoke(cli, ["app", "test", "out"])
    assert failed.exit_code == 1
    assert "The packaged app failed 1 of its checks" in failed.output


@pytest.fixture(
    params=[pytest.param(name, marks=pytest.mark.store_servers) for name in configured()]
)
def server(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def test_on_a_server_engine_the_run_makes_its_own_database_and_drops_it(
    project: Path, server: str
) -> None:
    from sqlalchemy import text

    from haute._quote_store._store import make_engine

    spec = _sheets(project, _export([_cell("pricing", "premium", "B2")]))
    release = project / "build" / "app"
    build_release(project, release)
    url = os.environ[f"HAUTE_TEST_{server.upper()}_URL"]
    sign_in = server_sign_in(server)

    with server_store(url, sign_in, release) as location:
        report = run_app_test(release, location, [("Fleet", _request(spec))])
        made = make_url(location.url).database

    assert report.failed == [] and 'Fleet: exported by "Slip"' in report.passed
    engine = make_engine(url, sign_in=sign_in)
    try:
        with engine.connect() as connection:
            listed = (
                "SELECT name FROM sys.databases"
                if server == "sqlserver"
                else "SELECT datname FROM pg_database"
            )
            names = {row[0] for row in connection.execute(text(listed))}
    finally:
        engine.dispose()
    assert made not in names
