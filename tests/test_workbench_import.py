"""An Import run (specs/workbench, Running an Import; WB-09): the rows a file, a database or an
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
from haute._workbench_form import FormSpec
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
        {"layer": "2", "limit": "2000"},
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
        {"layer": "2", "limit": "2000.5"},
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

    assert imported.rows == [{"layer": "1", "limit": "1000"}, {"layer": "2"}]
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
