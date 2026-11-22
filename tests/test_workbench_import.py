"""An Import run (specs/workbench, Running an Import): the rows a file, a database or an
API gives the table an Import fills, read on the server and answered as the cells the sheets
hold. The databases run on SQLite and on each server the environment names
(tests/_store_servers.py); the API on a server of the test's own."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from haute import _workbench_import as imports
from haute._quote_store._store import make_engine
from haute._workbench_form import ExcelImport, FormSpec
from haute._workbench_import import (
    ImportRefusedError,
    ImportSourceError,
    Source,
    check_query,
    hosted_source,
    project_source,
    run_import,
)
from tests._store_servers import Target, configured, database
from tests.test_workbench_routes import form, section

POLICY = {
    "name": "policy",
    "rows": "one",
    "inputs": [{"name": "reference", "type": "str", "label": "Reference"}],
}
LAYERS = {
    "name": "layers",
    "rows": "many",
    "inputs": [
        {"name": "layer", "type": "int", "index": "typed"},
        {"name": "limit", "type": "float", "label": "Limit"},
        {"name": "excess", "type": "float"},
    ],
}
VALUES = {"policy": [{"reference": "ABC 1"}]}


def _import(source: dict[str, Any], **rest: Any) -> dict[str, Any]:
    return {
        "id": "w_in",
        "name": "import_layers",
        "type": "import",
        "x": 0,
        "y": 0,
        "w": 160,
        "h": 40,
        "label": "Layers",
        "source": source,
        "table": "layers",
        "mode": "replace",
        "column_names": [{"column": "excess", "name": "XS"}],
        **rest,
    }


def _form(source: dict[str, Any], **rest: Any) -> FormSpec:
    spec = form(POLICY, LAYERS)
    spec["pages"][0]["sections"] = [section("s", _import(source, **rest))]
    return FormSpec.model_validate(spec)


def _no_source(name: str) -> Source:
    raise AssertionError(f"no connection is reached: {name}")


def test_a_csv_is_read_by_its_columns_names_or_labels_whatever_their_case() -> None:
    spec = _form({"format": "csv", "delimiter": "auto"})
    data = "﻿Layer;LIMIT;XS;ignored\n1; 1000 ;500;x\n\n2;2000;;y\n".encode()

    imported = run_import(spec, "w_in", VALUES, source=_no_source, upload=data)

    assert (imported.table, imported.mode) == ("layers", "replace")
    assert imported.rows == [
        {"layer": "1", "limit": "1000", "excess": "500"},
        # Its XS is empty, which clears the cell.
        {"layer": "2", "limit": "2000", "excess": ""},
    ]


def test_a_workbook_is_read_from_its_sheet_under_its_header_row_to_its_first_empty_row() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.title = "Cover"
    sheet = workbook.create_sheet("Layers")
    sheet.append(["A schedule"])
    sheet.append(["layer", "limit", "XS"])
    sheet.append([1, 1000.0, 500])
    sheet.append([None, None, None])
    sheet.append([9, 9, 9])
    saved = io.BytesIO()
    workbook.save(saved)
    spec = _form({"format": "excel", "sheet": "Layers", "header_row": 2})

    imported = run_import(spec, "w_in", VALUES, source=_no_source, upload=saved.getvalue())

    assert imported.rows == [{"layer": "1", "limit": "1000", "excess": "500"}]


def test_a_file_is_picked_before_it_is_read() -> None:
    with pytest.raises(ImportRefusedError, match='Pick the file Import "Layers" reads'):
        run_import(
            _form({"format": "csv", "delimiter": "comma"}), "w_in", VALUES, source=_no_source
        )


@pytest.mark.parametrize(
    ("query", "said"),
    [
        ("DELETE FROM layers", "must start with SELECT or WITH"),
        ("SELECT 1; DROP TABLE layers", "one statement, with no ;"),
        ("SELECT 1 -- and", "no comment"),
        ("SELECT * INTO copy FROM layers", "holds INTO, which changes a database"),
        ("", "has no query"),
    ],
)
def test_a_query_that_is_not_one_read_is_refused_naming_why(query: str, said: str) -> None:
    with pytest.raises(ImportRefusedError, match=said):
        check_query(query)


def test_a_query_reading_with_a_common_table_expression_is_one_read() -> None:
    check_query("WITH held AS (SELECT 1 AS layer) SELECT layer FROM held")


def _rates(path: Path) -> None:
    with sqlite3.connect(path) as raw:
        raw.execute("CREATE TABLE rates (ref TEXT, layer INTEGER, amount REAL, xs REAL)")
        raw.executemany(
            "INSERT INTO rates VALUES (?, ?, ?, ?)",
            [("ABC 1", 1, 1000.0, 500.0), ("ABC 1", 2, 2000.5, None), ("Z", 3, 1, 1)],
        )


QUERY = 'SELECT layer, amount AS "limit", xs AS XS FROM rates WHERE ref = :ref ORDER BY layer'
SQL = {
    "format": "sql",
    "connection": "rates",
    "query": QUERY,
    "parameters": [{"name": "ref", "table": "policy", "column": "reference"}],
}


def test_a_query_runs_on_its_named_connection_its_parameter_bound_to_its_field(
    tmp_path: Path,
) -> None:
    _rates(tmp_path / "rates.sqlite")
    (tmp_path / "connections.toml").write_text(
        '[rates]\nprovider = "sqlite"\ndatabase = "rates.sqlite"\n', encoding="utf-8"
    )

    imported = run_import(
        _form(SQL),
        "w_in",
        VALUES,
        source=lambda name: project_source(tmp_path, name, {}),
    )

    assert imported.rows == [
        {"layer": "1", "limit": "1000", "excess": "500"},
        {"layer": "2", "limit": "2000.5", "excess": ""},
    ]


def test_a_parameter_holding_nothing_is_refused_naming_the_field_to_fill(tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError, match='Fill Reference first: Import "Layers" reads it'):
        run_import(_form(SQL), "w_in", {}, source=_no_source)


def test_more_rows_than_a_quotes_table_holds_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(imports, "IMPORT_ROW_LIMIT", 1)
    with pytest.raises(ImportRefusedError, match="more than 1 rows"):
        run_import(
            _form({"format": "csv", "delimiter": "comma"}),
            "w_in",
            VALUES,
            source=_no_source,
            upload=b"layer\n1\n2\n",
        )


def test_a_connection_the_file_does_not_hold_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError, match=r"has no \[rates\] connection"):
        run_import(_form(SQL), "w_in", VALUES, source=lambda n: project_source(tmp_path, n, {}))


@pytest.fixture(
    params=[pytest.param(name, marks=pytest.mark.store_servers) for name in configured()]
)
def server(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    with database(request.param, tmp_path) as target:
        yield target


def test_a_query_runs_on_a_server_database(server: Target) -> None:
    engine = make_engine(server.url, sign_in=server.sign_in)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE rates (ref VARCHAR(20), layer INT, amount FLOAT, xs FLOAT)")
            )
            connection.execute(
                text("INSERT INTO rates VALUES ('ABC 1', 1, 1000, 500), ('Z', 3, 1, 1)")
            )
    finally:
        engine.dispose()
    reached = Source("rates", server.backend, server.url, "rates", sign_in=server.sign_in)
    query = "SELECT layer, amount AS limit_, xs AS XS FROM rates WHERE ref = :ref"

    imported = run_import(
        _form(
            {**SQL, "query": query},
            column_names=[
                {"column": "excess", "name": "XS"},
                {"column": "limit", "name": "limit_"},
            ],
        ),
        "w_in",
        VALUES,
        source=lambda _name: reached,
    )

    assert imported.rows == [{"layer": "1", "limit": "1000", "excess": "500"}]


class _Api(BaseHTTPRequestHandler):
    """An API answering a policy's layers, and keeping what it was sent."""

    seen: list[dict[str, Any]] = []

    def _answer(self, body: Any, status: int = 200) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - the handler's name
        _Api.seen.append({"path": self.path, "authorization": self.headers.get("Authorization")})
        if self.path.startswith("/api/missing"):
            self._answer({"error": "no"}, 404)
            return
        self._answer({"data": {"layers": [{"layer": 1, "Limit": 1000, "XS": None}, {"layer": 2}]}})

    def do_POST(self) -> None:  # noqa: N802 - the handler's name
        length = int(self.headers.get("Content-Length", "0"))
        _Api.seen.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        self._answer({"layer": 7, "limit": 70})

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def api() -> Iterator[str]:
    _Api.seen = []
    served = HTTPServer(("127.0.0.1", 0), _Api)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{served.server_address[1]}/api"
    finally:
        served.shutdown()


def _http(**rest: Any) -> dict[str, Any]:
    return {
        "format": "http",
        "connection": "crm",
        "method": "GET",
        "path": "/policies/{ref}/layers",
        "parameters": [{"name": "ref", "table": "policy", "column": "reference"}],
        "rows": "data.layers",
        **rest,
    }


def test_a_request_reaches_its_connections_api_its_rows_found_where_it_says(
    api: str, tmp_path: Path
) -> None:
    (tmp_path / "connections.toml").write_text(
        f'[crm]\nprovider = "http"\nbase_url = "{api}"\n'
        'headers = { Authorization = "HAUTE_CRM_AUTHORIZATION" }\n',
        encoding="utf-8",
    )
    environ = {"HAUTE_CRM_AUTHORIZATION": "Bearer sekret"}

    imported = run_import(
        _form(_http()), "w_in", VALUES, source=lambda n: project_source(tmp_path, n, environ)
    )

    # A null clears its cell; a key an object does not hold leaves it as it is.
    assert imported.rows == [{"layer": "1", "limit": "1000", "excess": ""}, {"layer": "2"}]
    assert _Api.seen == [{"path": "/api/policies/ABC%201/layers", "authorization": "Bearer sekret"}]


def test_a_post_sends_its_parameters_and_one_object_is_one_row(api: str) -> None:
    reached = Source("crm", "http", api, "crm")

    imported = run_import(
        _form(_http(method="POST", path="/quote", rows="")),
        "w_in",
        VALUES,
        source=lambda _name: reached,
    )

    assert imported.rows == [{"layer": "7", "limit": "70"}]
    assert _Api.seen == [{"path": "/api/quote", "body": {"ref": "ABC 1"}}]


def test_an_apis_failure_and_an_answer_without_its_rows_are_said(api: str) -> None:
    reached = Source("crm", "http", api, "crm")
    with pytest.raises(ImportSourceError, match="The crm API answered HTTP 404"):
        run_import(_form(_http(path="/missing")), "w_in", VALUES, source=lambda _n: reached)
    with pytest.raises(ImportRefusedError, match="answer holds no data.items"):
        run_import(_form(_http(rows="data.items")), "w_in", VALUES, source=lambda _n: reached)


def test_a_hosted_app_reads_a_connection_from_the_variables_named_for_it() -> None:
    environ = {
        "HAUTE_CONNECTION_CRM_URL": "https://crm.example.com/api",
        "HAUTE_CONNECTION_CRM_TOKEN": "tok",
        "HAUTE_CONNECTION_POLICY_DB_URL": "postgresql+psycopg://db.example.com/policies",
        "HAUTE_CONNECTION_POLICY_DB_USERNAME": "app",
        "HAUTE_CONNECTION_POLICY_DB_PASSWORD": "pw",
        "HAUTE_CONNECTION_WAREHOUSE_URL": "databricks://adb-1.azuredatabricks.net/sql/1.0/w/abc",
    }

    crm = hosted_source("crm", environ)
    policies = hosted_source("policy-db", environ)
    warehouse = hosted_source("warehouse", environ)

    assert (crm.provider, crm.headers) == ("http", {"Authorization": "Bearer tok"})
    assert (policies.provider, policies.sign_in.username) == ("postgresql", "app")
    assert (warehouse.provider, warehouse.url, warehouse.http_path) == (
        "databricks",
        "adb-1.azuredatabricks.net",
        "/sql/1.0/w/abc",
    )
    assert "tok" not in repr(crm) and "pw" not in repr(policies)
    with pytest.raises(ImportRefusedError, match="sets no HAUTE_CONNECTION_OTHER_URL"):
        hosted_source("other", environ)
    with pytest.raises(ImportRefusedError, match="is no connection's name"):
        hosted_source("crm url", environ)


# ── Through the routes ─────────────────────────────────────────────────────


def _encoded(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def test_preview_runs_an_import_of_the_form_the_view_holds(tmp_path: Path) -> None:
    from tests._project import use_project
    from tests.test_workbench_routes import client, enable

    use_project(tmp_path)
    enable(tmp_path, "[workbench]\nenabled = true\n")
    _rates(tmp_path / "rates.sqlite")
    (tmp_path / "connections.toml").write_text(
        '[rates]\nprovider = "sqlite"\ndatabase = "rates.sqlite"\n', encoding="utf-8"
    )
    held = _form(SQL).model_dump(mode="json", by_alias=True)

    answered = client().post(
        "/api/workbench/import", json={"form": held, "component": "w_in", "values": VALUES}
    )
    refused = client().post(
        "/api/workbench/import", json={"form": held, "component": "w_in", "values": {}}
    )
    sent = client().post(
        "/api/workbench/import",
        json={
            "form": _form({"format": "csv", "delimiter": "comma"}).model_dump(
                mode="json", by_alias=True
            ),
            "component": "w_in",
            "values": VALUES,
            "file": _encoded(b"layer,limit\n4,40\n"),
        },
    )

    assert answered.status_code == 200, answered.text
    assert answered.json() == {
        "table": "layers",
        "mode": "replace",
        "rows": [
            {"layer": "1", "limit": "1000", "excess": "500"},
            {"layer": "2", "limit": "2000.5", "excess": ""},
        ],
    }
    assert refused.status_code == 422
    assert "Fill Reference first" in refused.text
    assert sent.json()["rows"] == [{"layer": "4", "limit": "40"}]


def _app_form(source: dict[str, Any]) -> dict[str, Any]:
    """The app tests' form with an Import filling its policy's one row."""
    from tests.test_workbench_app_quotes import FORM

    spec = json.loads(json.dumps(FORM))
    spec["pages"][0]["sections"][0]["widgets"] = [
        _import(source, table="policy_details", mode="replace", column_names=[])
    ]
    return spec


def _app_import(client: Any, component: str, headers: dict[str, str], **fields: Any) -> Any:
    from tests.test_workbench_app_quotes import ANA  # noqa: F401 - the headers' source

    release = client.get("/api/app/release", headers=headers).json()
    form = client.get("/api/app/form", headers=headers).json()["form"]
    (widget,) = [w for page in form["pages"] for s in page["sections"] for w in s["widgets"]]
    body = {
        "form_revision": release["form_revision"],
        "release": release["id"],
        "component": widget["id"],
        "values": {"policy_details": [{"state": "NY"}]},
        **fields,
    }
    return client.post("/api/app/import", json=body, headers=headers)


def test_the_app_runs_an_import_for_one_who_may_change_quotes(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from haute._quote_store import StoreLocation
    from haute._workbench_app import create_app
    from tests.test_workbench_app_quotes import ANA, TEST, _prepared, _write_release

    release = _write_release(tmp_path / "rel", _app_form({"format": "csv", "delimiter": "comma"}))
    url = _prepared(tmp_path / "store.sqlite", release)
    with TestClient(create_app(release, store=StoreLocation(TEST, url))) as client:
        answered = _app_import(
            client, "w_in", ANA, file=_encoded(b"state,exposure\nCA,2000\nNY,1\n")
        )

    assert answered.status_code == 200, answered.text
    assert answered.json() == {
        "table": "policy_details",
        "mode": "replace",
        "rows": [{"state": "CA", "exposure": "2000"}, {"state": "NY", "exposure": "1"}],
    }


def test_a_hosted_apps_failed_source_is_an_incident_its_words_logged(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from structlog.testing import capture_logs

    from haute._quote_store import StoreLocation
    from haute._workbench_app import create_app
    from tests._failure_assertions import incident_in
    from tests.test_workbench_app_quotes import ANA, TEST, _prepared, _write_release

    sql = {
        "format": "sql",
        "connection": "rates",
        "query": "SELECT missing FROM nowhere WHERE x = :state",
        "parameters": [{"name": "state", "table": "policy_details", "column": "state"}],
    }
    release = _write_release(tmp_path / "rel", _app_form(sql))
    url = _prepared(tmp_path / "store.sqlite", release)
    rates = tmp_path / "rates.sqlite"
    _rates(rates)
    reached = Source("rates", "sqlite", f"sqlite:///{rates}", "rates")
    hosted = create_app(release, store=StoreLocation(TEST, url), sources=lambda _n: reached)
    with TestClient(hosted) as client, capture_logs() as logs:
        failed = _app_import(client, "w_in", ANA)

    assert failed.status_code == 502
    incident = incident_in(failed.json()["detail"])
    assert "nowhere" not in failed.text
    assert "nowhere" in str([entry for entry in logs if entry.get("incident") == incident])


def test_the_apps_import_past_its_body_limit_is_refused_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from haute import _workbench_app
    from haute._quote_store import StoreLocation
    from haute._workbench_app import create_app
    from tests.test_workbench_app_quotes import ANA, TEST, _prepared, _write_release

    monkeypatch.setattr(_workbench_app, "MAX_IMPORT_FILE_TEXT", 0)
    monkeypatch.setattr(_workbench_app, "_IMPORT_BODY_HEADROOM", 100)
    monkeypatch.setattr(_workbench_app, "deploy_quote_request_body_limit_bytes", lambda: 100)
    release = _write_release(tmp_path / "rel", _app_form({"format": "csv", "delimiter": "comma"}))
    url = _prepared(tmp_path / "store.sqlite", release)
    with TestClient(create_app(release, store=StoreLocation(TEST, url))) as client:
        refused = _app_import(client, "w_in", ANA, file=_encoded(b"state\n" + b"CA\n" * 100))

    assert refused.status_code == 413


def test_one_who_may_only_read_quotes_runs_no_import(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from haute._quote_store import StoreLocation
    from haute._workbench_app import create_app
    from tests.test_workbench_app_quotes import DEV, _prepared, _write_release

    release = _write_release(tmp_path / "rel", _app_form({"format": "csv", "delimiter": "comma"}))
    url = _prepared(tmp_path / "store.sqlite", release)
    reading = create_app(
        release, store=StoreLocation(DEV, url), local_principal="ana@example.com", local_reader=True
    )
    with TestClient(reading, base_url="http://127.0.0.1") as client:
        refused = _app_import(client, "w_in", {}, file=_encoded(b"state\nCA\n"))

    assert refused.status_code == 403


def test_a_sqlite_source_is_read_and_never_written(tmp_path: Path) -> None:
    rates = tmp_path / "rates.sqlite"
    _rates(rates)
    with sqlite3.connect(rates) as raw:
        raw.execute("PRAGMA journal_mode=WAL")
    before = rates.read_bytes()
    reached = Source("rates", "sqlite", f"sqlite:///{rates}", "rates")
    missing = Source("rates", "sqlite", f"sqlite:///{tmp_path / 'none.sqlite'}", "rates")

    imported = run_import(_form(SQL), "w_in", VALUES, source=lambda _n: reached)
    with pytest.raises(ImportSourceError, match="did not answer the Import's query"):
        run_import(_form(SQL), "w_in", VALUES, source=lambda _n: missing)

    assert len(imported.rows) == 2
    assert rates.read_bytes() == before
    with sqlite3.connect(rates) as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert not (tmp_path / "none.sqlite").exists()


def test_an_apis_answer_past_its_limit_is_refused(
    api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(imports, "HTTP_ANSWER_LIMIT_BYTES", 10)
    reached = Source("crm", "http", api, "crm")

    with pytest.raises(ImportRefusedError, match="The crm API answered more than"):
        run_import(_form(_http()), "w_in", VALUES, source=lambda _n: reached)


# ── One query, and the cells it gives ──────────────────────────────────────


def test_sql_server_compiles_a_query_as_one_before_it_runs() -> None:
    assert imports.one_query_check("SELECT a FROM t ORDER BY a") == (
        "SELECT TOP (0) * FROM (SELECT a FROM t ORDER BY a OFFSET 0 ROWS) AS haute_import_check"
    )
    assert imports.one_query_check(
        "WITH c (x) AS (SELECT 1 AS x), d AS (SELECT x FROM c) SELECT x FROM d"
    ) == (
        "WITH c (x) AS (SELECT 1 AS x), d AS (SELECT x FROM c) "
        "SELECT TOP (0) * FROM (SELECT x FROM d) AS haute_import_check"
    )
    # A parenthesis in a string or a quoted name is no parenthesis.
    assert imports.one_query_check("SELECT N'it''s )' AS [a]]b] FROM t").endswith(
        "(SELECT N'it''s )' AS [a]]b] FROM t) AS haute_import_check"
    )


@pytest.mark.parametrize(
    ("query", "said"),
    [
        ("SELECT 1 AS n) AS x DENY SELECT ON t TO u SELECT (1", "never opened"),
        ("SELECT 'abc", "does not close"),
        ("SELECT (1", "leaves a parenthesis open"),
        ("WITH SELECT 1", "as AS (...)"),
    ],
)
def test_a_query_whose_parentheses_or_quotes_do_not_balance_is_refused(
    query: str, said: str
) -> None:
    with pytest.raises(ImportRefusedError, match=said):
        imports.one_query_check(query)


def test_a_parameter_is_found_outside_strings_and_quoted_names_as_the_view_finds_it() -> None:
    query = "select ':no', [x:y], \"a:b\", :ref, a::int, :ref, :Day_1"

    assert imports.query_parameters(query) == ["ref", "Day_1"]


@pytest.fixture(
    params=[
        pytest.param(name, marks=pytest.mark.store_servers)
        for name in configured()
        if name == "sqlserver"
    ]
)
def sql_server(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    with database(request.param, tmp_path) as target:
        yield target


def test_on_sql_server_a_batch_of_statements_runs_none_of_them(sql_server: Target) -> None:
    engine = make_engine(sql_server.url, sign_in=sql_server.sign_in)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE rates (ref VARCHAR(20), layer INT)"))
            connection.execute(text("INSERT INTO rates VALUES ('ABC 1', 2), ('ABC 1', 1)"))
    finally:
        engine.dispose()
    reached = Source("rates", "sqlserver", sql_server.url, "rates", sign_in=sql_server.sign_in)
    # No ; between them, and none of the words an Import refuses.
    batch = {**SQL, "query": "SELECT layer FROM rates DENY SELECT ON rates TO public"}
    ordered = {
        **SQL,
        "query": "WITH held AS (SELECT layer FROM rates WHERE ref = :ref) "
        "SELECT layer FROM held ORDER BY layer",
    }

    with pytest.raises(ImportSourceError, match="did not answer the Import's query"):
        run_import(_form(batch), "w_in", VALUES, source=lambda _n: reached)
    imported = run_import(_form(ordered), "w_in", VALUES, source=lambda _n: reached)

    engine = make_engine(sql_server.url, sign_in=sql_server.sign_in)
    try:
        with engine.connect() as connection:
            denied = connection.execute(
                text(
                    "SELECT COUNT(*) FROM sys.database_permissions "
                    "WHERE major_id = OBJECT_ID('rates') AND state = 'D'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert denied == 0
    assert imported.rows == [{"layer": "1"}, {"layer": "2"}]


DETAILS = {
    "name": "policy",
    "rows": "one",
    "inputs": [
        {"name": "inception", "type": "date"},
        {"name": "enabled", "type": "bool"},
        {"name": "note", "type": "str"},
    ],
}


def _details_form(source: dict[str, Any]) -> FormSpec:
    spec = form(DETAILS)
    spec["pages"][0]["sections"] = [section("s", _import(source, table="policy", column_names=[]))]
    return FormSpec.model_validate(spec)


def test_a_sources_empty_false_and_null_values_clear_cells_and_its_dates_are_days(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["Inception", "Enabled", "Note", "Ignored"])
    workbook.active.append([datetime(2026, 11, 22), False, None, "x"])
    saved = io.BytesIO()
    workbook.save(saved)
    rates = tmp_path / "rates.sqlite"
    with sqlite3.connect(rates) as raw:
        raw.execute("CREATE TABLE p (inception TEXT, enabled INTEGER, note TEXT)")
        raw.execute("INSERT INTO p VALUES ('2026-01-02', 1, '')")
    reached = Source("rates", "sqlite", f"sqlite:///{rates}", "rates")
    sql = {
        "format": "sql",
        "connection": "rates",
        "query": "SELECT inception, enabled, note FROM p",
        "parameters": [],
    }

    from_file = run_import(
        _details_form({"format": "excel", "sheet": "", "header_row": 1}),
        "w_in",
        {},
        source=_no_source,
        upload=saved.getvalue(),
    )
    from_database = run_import(_details_form(sql), "w_in", {}, source=lambda _n: reached)

    assert from_file.rows == [{"inception": "2026-11-22", "enabled": False, "note": ""}]
    assert from_database.rows == [{"inception": "2026-01-02", "enabled": True, "note": ""}]


def test_a_source_holding_none_of_the_columns_read_is_refused() -> None:
    with pytest.raises(ImportRefusedError, match="holds none of the columns"):
        run_import(
            _form({"format": "csv", "delimiter": "comma"}),
            "w_in",
            VALUES,
            source=_no_source,
            upload=b"other,more\n1,2\n",
        )


def test_a_workbook_is_read_only_as_wide_as_the_columns_read() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"], sheet["XFD1"] = "layer", "far"
    sheet["A2"], sheet["XFD2"] = 3, "x"
    saved = io.BytesIO()
    workbook.save(saved)
    seen: list[int] = []

    def keep(names: Any) -> list[int]:
        seen.append(len(names))
        return [0]

    names, rows = imports._excel_rows(
        ExcelImport(format="excel", sheet="", header_row=1), saved.getvalue(), keep
    )

    assert (names, rows) == (["layer"], [[3]])
    assert seen == [16384]


def test_a_hosted_warehouse_without_a_token_takes_credentials_of_its_own_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-2.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_TOKEN", "global")
    environ = {"HAUTE_CONNECTION_WAREHOUSE_URL": "databricks://adb-1.azuredatabricks.net/sql/1"}
    query = {**SQL, "connection": "warehouse"}

    with pytest.raises(ImportRefusedError, match="credentials are adb-2.azuredatabricks.net's"):
        run_import(_form(query), "w_in", VALUES, source=lambda n: hosted_source(n, environ))
