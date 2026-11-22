"""An Import run (specs/workbench, Running an Import; Control flow 40): the rows a file, a
database or an API gives the table an Import fills, read on the server and answered as the
cells the sheets hold, for the view to land as typing does.

An Import is declared, never code: a CSV or a workbook the underwriter sent, a query run on a
named connection, or a request to one. Its connection is resolved where it runs: from the
project's ``connections.toml`` in the editor and ``haute app serve`` (:func:`project_source`),
from the host's variables named for it in a hosted app (:func:`hosted_source`). Nothing here
prices: the rows land on the sheets and are priced as typed values are. No credential is
answered, logged or kept past the run.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quoted
from urllib.parse import urlsplit

from haute._workbench_config import WorkbenchError
from haute._workbench_form import (
    QUOTE_DETAILS_MANAGED,
    QUOTE_DETAILS_NAME,
    CsvImport,
    ExcelImport,
    FormSpec,
    HttpImport,
    ImportMode,
    ImportWidget,
    SchemaColumn,
    SchemaTable,
    SqlImport,
    with_quote_details,
)
from haute._workbench_tables import quote_request, sheet_cell

#: The most rows an Import fills a table with.
IMPORT_ROW_LIMIT = 10_000
#: How long an API is given to answer, in seconds.
HTTP_TIMEOUT_SECONDS = 30
#: The most of an API's answer read, in bytes.
HTTP_ANSWER_LIMIT_BYTES = 32 * 1024 * 1024
#: The variables a hosted app reads a connection from, ``<NAME>`` the connection's name in
#: capitals, dashes as underscores.
HOSTED_PREFIX = "HAUTE_CONNECTION_"
#: The words that change a database, refused in a query.
_CHANGING = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|"
    r"CALL|INTO)\b",
    re.IGNORECASE,
)
#: A query's parameter, ``:name``, outside a ``::`` cast.
_QUERY_PARAMETER = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")
#: A path's parameter, ``{name}``.
_PATH_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: What a CSV's fields are split by, as Auto finds one.
_DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t"}

Cell = str | bool


class ImportRefusedError(WorkbenchError):
    """An Import that cannot run as asked: its settings, its parameters, its query or its
    source's answer; the message says what to put right."""


class ImportSourceError(WorkbenchError):
    """A source that failed to answer: a database's or an API's own failure, its words kept
    for whoever runs the server, which a hosted app logs rather than shows."""


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """A connection an Import reaches, resolved: its name, its provider, where it is (a
    database's SQLAlchemy URL, an API's base URL, a Databricks workspace's host), and how it
    signs in, none of it ever shown; and what names it, as a refusal says it."""

    name: str
    provider: str
    url: str = field(repr=False)
    where: str
    http_path: str | None = None
    sign_in: Any = field(default=None, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    token: str | None = field(default=None, repr=False)


def project_source(project_root: Path, name: str, environ: Mapping[str, str]) -> Source:
    """The connection *name* of the project's ``connections.toml``, its sign-in read from
    *environ* (the clone's ``.env`` under the process's own environment)."""
    from haute._connections import CONNECTIONS_FILE, read_connections
    from haute._quote_store._environment import _read_sign_in
    from haute._quote_store._providers import PROVIDERS, server_url
    from haute._quote_store._store import store_url

    connection = read_connections(project_root).get(name)
    if connection is None:
        raise ImportRefusedError(
            f"{CONNECTIONS_FILE} has no [{name}] connection: add one saying where the source "
            "is and how to sign in to it."
        )
    where = connection.where
    if connection.provider == "http":
        headers = {
            header: _variable(environ, variable, where)
            for header, variable in connection.headers.items()
        }
        return Source(name, "http", str(connection.base_url), where, headers=headers)
    if connection.provider == "databricks":
        return Source(name, "databricks", "", where, http_path=connection.http_path)
    if connection.provider == "sqlite":
        return Source(name, "sqlite", store_url(project_root / connection.database), where)
    if not connection.server or not connection.database:
        raise ImportRefusedError(f"{where} names no server or database: fill them in.")
    sign_in = _read_sign_in(str(connection.auth), connection.variables, environ, where, " in .env")
    url = server_url(PROVIDERS[connection.provider], connection.server, connection.database)
    return Source(name, connection.provider, url, where, sign_in=sign_in)


def hosted_source(name: str, environ: Mapping[str, str]) -> Source:
    """The connection *name* as a hosted app's host gives it, by the variables named for it
    (:data:`HOSTED_PREFIX`): ``_URL``, a database's credential-free SQLAlchemy URL, an API's
    base URL or ``databricks://<host>/<http_path>``; ``_USERNAME`` and ``_PASSWORD`` a
    password sign-in, else the app's own managed identity; ``_TOKEN`` a Databricks token or
    an API's bearer token."""
    from haute._credential_security import validate_credential_free_uri
    from haute._quote_store._providers import SignIn, provider_of

    stem = HOSTED_PREFIX + name.upper().replace("-", "_")
    where = f"{stem}_URL"
    url = environ.get(where, "").strip()
    if not url:
        raise ImportRefusedError(
            f"This app reaches no {name} connection: its host sets no {where}."
        )
    token = environ.get(f"{stem}_TOKEN", "").strip() or None
    scheme = urlsplit(url).scheme
    if scheme in ("https", "http"):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return Source(name, "http", url, where, headers=headers)
    if scheme == "databricks":
        parts = urlsplit(url)
        return Source(
            name,
            "databricks",
            str(parts.hostname),
            where,
            http_path=parts.path or None,
            token=token,
        )
    try:
        validate_credential_free_uri(url)
        provider = provider_of(url)
    except Exception as exc:
        raise ImportRefusedError(f"{where} is no URL of a database this app reaches.") from exc
    user = environ.get(f"{stem}_USERNAME", "").strip()
    password = environ.get(f"{stem}_PASSWORD", "")
    sign_in = (
        SignIn("password", username=user, secret=password)
        if user
        else None
        if provider.name == "sqlite"
        else SignIn("managed-identity")
    )
    return Source(name, provider.name, url, where, sign_in=sign_in)


def _variable(environ: Mapping[str, str], variable: str, where: str) -> str:
    value = environ.get(variable, "")
    if not value.strip():
        raise ImportRefusedError(f"{where} reads {variable}, which holds nothing: set it in .env.")
    return value


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Imported:
    """What an Import gives its table: the table by name, how its rows land, and its rows,
    each the cells the sheets hold by column name."""

    table: str
    mode: ImportMode
    rows: list[dict[str, Cell]]


def import_of(form: FormSpec, component: str) -> ImportWidget:
    """The Import of id *component* of *form*, else a refusal naming why."""
    found = next(
        (
            widget
            for page in form.pages
            for section in page.sections
            for widget in section.widgets
            if widget.id == component
        ),
        None,
    )
    if not isinstance(found, ImportWidget):
        raise ImportRefusedError(f"The form has no Import {component!r} to run.")
    return found


def run_import(
    form: FormSpec,
    component: str,
    values: Mapping[str, Any],
    *,
    source: Callable[[str], Source],
    upload: bytes | None = None,
) -> Imported:
    """The Import of id *component* of *form* run on the quote typed, *values*: its rows read
    from the file sent (*upload*), the database or the API its connection, resolved by
    *source*, names, and answered as the cells its table's columns hold
    (:func:`imported_rows`). Refused (:class:`ImportRefusedError`) for settings it cannot run
    on, a parameter with no value, a query not a single read, a host not its connection's,
    and more rows than :data:`IMPORT_ROW_LIMIT`; a source that fails is an
    :class:`ImportSourceError`."""
    spec = with_quote_details(form)
    widget = import_of(spec, component)
    label = widget.label.strip() or widget.name
    table = _table(spec, widget, label)
    read = widget.source
    if isinstance(read, CsvImport | ExcelImport):
        if upload is None:
            raise ImportRefusedError(f'Pick the file Import "{label}" reads.')
        names, rows = (
            _csv_rows(read, upload) if isinstance(read, CsvImport) else _excel_rows(read, upload)
        )
    elif isinstance(read, SqlImport):
        if not read.connection.strip():
            raise ImportRefusedError(f'Import "{label}" names no connection.')
        check_query(read.query)
        held = _QUERY_PARAMETER.findall(read.query)
        parameters = _parameters(spec, read.parameters, held, values, label)
        names, rows = _sql_rows(source(read.connection), read.query, parameters)
    else:
        assert isinstance(read, HttpImport)
        if not read.connection.strip():
            raise ImportRefusedError(f'Import "{label}" names no connection.')
        held = _PATH_PARAMETER.findall(read.path)
        if read.method == "POST":
            held = [*held, *(given.name for given in read.parameters)]
        parameters = _parameters(spec, read.parameters, held, values, label)
        names, rows = _http_rows(source(read.connection), read, parameters)
    return Imported(table.name, widget.mode, imported_rows(table, widget, names, rows))


def check_query(query: str) -> None:
    """Refuse (:class:`ImportRefusedError`) a query that is not one statement reading a
    database: one starting ``SELECT`` or ``WITH``, with no ``;``, no comment and no word that
    changes a database."""
    stripped = query.strip()
    if not stripped:
        raise ImportRefusedError("The Import has no query.")
    if not re.match(r"(?is)(select|with)\b", stripped):
        raise ImportRefusedError("The Import's query must start with SELECT or WITH.")
    if ";" in stripped:
        raise ImportRefusedError("The Import's query must be one statement, with no ;.")
    if "--" in stripped or "/*" in stripped:
        raise ImportRefusedError("The Import's query must hold no comment.")
    changing = _CHANGING.search(stripped)
    if changing is not None:
        raise ImportRefusedError(
            f"The Import's query holds {changing.group(0).upper()}, which changes a database: "
            "an Import only reads."
        )


def _table(spec: FormSpec, widget: ImportWidget, label: str) -> SchemaTable:
    table = next((held for held in spec.data_schema.tables if held.name == widget.table), None)
    if widget.table is None:
        raise ImportRefusedError(f'Import "{label}" fills no table.')
    if table is None:
        raise ImportRefusedError(
            f'Import "{label}" fills {widget.table}, which is no longer in the schema.'
        )
    if not table.has_inputs:
        raise ImportRefusedError(f'Import "{label}" fills {table.name}, which has no inputs.')
    for named in widget.column_names:
        if named.column not in {column.name for column in table.columns}:
            raise ImportRefusedError(
                f'Import "{label}" reads into {table.name}.{named.column}, which is no longer '
                "in the schema."
            )
    return table


def _parameters(
    spec: FormSpec,
    given: Sequence[Any],
    held: Iterable[str],
    values: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    """Each parameter *held* given the value its column holds in the quote typed, read as
    pricing reads it; one given no column, or holding nothing, refused naming it."""
    request = quote_request(spec, values)
    by_name = {parameter.name: parameter for parameter in given}
    parameters: dict[str, Any] = {}
    for name in dict.fromkeys(held):
        parameter = by_name.get(name)
        if parameter is None:
            raise ImportRefusedError(f'Import "{label}" gives :{name} no column.')
        table = next((t for t in spec.data_schema.tables if t.name == parameter.table), None)
        column = (
            None
            if table is None
            else next((c for c in table.columns if c.name == parameter.column), None)
        )
        if table is None or column is None or table.rows != "one":
            raise ImportRefusedError(
                f'Import "{label}" gives :{name} {parameter.table}.{parameter.column}, which '
                "is not a field of a one-row table."
            )
        row = request.get(table.name)
        value = row.get(column.name) if isinstance(row, Mapping) else None
        if value is None or value == "":
            raise ImportRefusedError(
                f'Fill {column.label or column.name} first: Import "{label}" reads it.'
            )
        parameters[name] = value
    return parameters


# ---------------------------------------------------------------------------
# Reading each source
# ---------------------------------------------------------------------------

Rows = list[list[Any]]


def _limited(rows: Iterator[list[Any]]) -> Rows:
    taken: Rows = []
    for row in rows:
        taken.append(row)
        if len(taken) > IMPORT_ROW_LIMIT:
            raise ImportRefusedError(
                f"The Import read more than {IMPORT_ROW_LIMIT:,} rows; a quote's table holds "
                "at most that."
            )
    return taken


def _csv_rows(read: CsvImport, data: bytes) -> tuple[list[str], Rows]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportRefusedError("The CSV is not UTF-8 text: save it as UTF-8 CSV.") from None
    if read.delimiter == "auto":
        first = text.split("\n", 1)[0]
        delimiter = max(_DELIMITERS.values(), key=first.count)
    else:
        delimiter = _DELIMITERS[read.delimiter]
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = next(reader, None)
    if header is None:
        raise ImportRefusedError("The CSV is empty.")
    rows = (row for row in reader if any(field.strip() for field in row))
    return [name.strip() for name in header], _limited(iter(rows))


def _excel_rows(read: ExcelImport, data: bytes) -> tuple[list[str], Rows]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - any file openpyxl cannot read is no workbook
        raise ImportRefusedError("The file is not an Excel workbook (.xlsx).") from None
    try:
        if read.sheet:
            if read.sheet not in workbook.sheetnames:
                raise ImportRefusedError(f'The workbook has no sheet "{read.sheet}".')
            sheet = workbook[read.sheet]
        else:
            sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(min_row=read.header_row, values_only=True)
        header = next(rows, None)
        if header is None:
            raise ImportRefusedError(f"The sheet has no row {read.header_row} to name its columns.")
        names = ["" if name is None else str(name).strip() for name in header]

        def body() -> Iterator[list[Any]]:
            for row in rows:
                if all(value is None or str(value).strip() == "" for value in row):
                    return
                yield list(row)

        return names, _limited(body())
    finally:
        workbook.close()


def _sql_rows(source: Source, query: str, parameters: Mapping[str, Any]) -> tuple[list[str], Rows]:
    if source.provider == "databricks":
        return _databricks_rows(source, query, parameters)
    if source.provider not in ("sqlite", "sqlserver", "postgresql"):
        raise ImportRefusedError(f"{source.where} is no database: an Import's query runs on one.")
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    engine = _source_engine(source)
    try:
        with engine.connect() as connection:
            # Held read-only where the engine holds a transaction so.
            if source.provider == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            result = connection.execute(text(query), dict(parameters))
            names = [str(name) for name in result.keys()]
            rows = _limited(list(row) for row in result)
            connection.rollback()
            return names, rows
    except SQLAlchemyError as exc:
        raise ImportSourceError(
            f"The {source.name} connection did not answer the Import's query: "
            f"{getattr(exc, 'orig', exc)}"
        ) from exc
    finally:
        engine.dispose()


def _source_engine(source: Source) -> Any:
    """An engine reading *source*: a server's as the quote store makes one, signed in and
    each statement within its deadline; a SQLite file's opened read-only, so nothing is
    written to it, its journal included, and a file that is not there is not made."""
    if source.provider != "sqlite":
        from haute._quote_store._store import make_engine

        return make_engine(source.url, sign_in=source.sign_in)
    import sqlite3

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    located = Path(str(make_url(source.url).database)).resolve()
    return create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(f"{located.as_uri()}?mode=ro", uri=True),
    )


def _databricks_rows(
    source: Source, query: str, parameters: Mapping[str, Any]
) -> tuple[list[str], Rows]:
    from haute._databricks_credentials import resolve_databricks_credentials
    from haute._databricks_io import _sql_auth_kwargs

    if not source.http_path:
        raise ImportRefusedError(f"{source.where} names no http_path.")
    try:
        from databricks import sql as databricks_sql
    except ImportError:
        raise ImportRefusedError(
            "A Databricks connection needs the databricks extra: install haute[databricks]."
        ) from None
    if source.token is not None:
        host, auth = source.url, {"access_token": source.token}
    else:
        credentials = resolve_databricks_credentials()
        host, auth = credentials.server_hostname, _sql_auth_kwargs(credentials)
    try:
        with (
            databricks_sql.connect(
                server_hostname=host, http_path=source.http_path, **auth
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(query, parameters=dict(parameters))
            names = [str(column[0]) for column in cursor.description or ()]
            return names, _limited(list(row) for row in cursor.fetchmany(IMPORT_ROW_LIMIT + 1))
    except ImportRefusedError:
        raise
    except Exception as exc:
        raise ImportSourceError(
            f"The {source.name} connection did not answer the Import's query: {exc}"
        ) from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _http_rows(
    source: Source, read: HttpImport, parameters: Mapping[str, Any]
) -> tuple[list[str], Rows]:
    if source.provider != "http":
        raise ImportRefusedError(f"{source.where} is no API: an Import's request reaches one.")
    path = _PATH_PARAMETER.sub(
        lambda found: url_quoted(str(parameters[found.group(1)]), safe=""), read.path
    )
    url = source.url.rstrip("/") + "/" + path.lstrip("/")
    base, sent = urlsplit(source.url), urlsplit(url)
    if (sent.scheme, sent.hostname, sent.port) != (base.scheme, base.hostname, base.port):
        raise ImportRefusedError(
            f"The Import's request reaches {sent.hostname}, not its connection's host."
        )
    body = None
    headers = {"Accept": "application/json", **source.headers}
    if read.method == "POST":
        body = json.dumps(dict(parameters), default=str).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=read.method)
    try:
        with urllib.request.build_opener(_NoRedirect).open(
            request, timeout=HTTP_TIMEOUT_SECONDS
        ) as response:
            body = response.read(HTTP_ANSWER_LIMIT_BYTES + 1)
            if len(body) > HTTP_ANSWER_LIMIT_BYTES:
                raise ImportRefusedError(
                    f"The {source.name} API answered more than "
                    f"{HTTP_ANSWER_LIMIT_BYTES // (1024 * 1024)} MiB."
                )
            answer = json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ImportSourceError(f"The {source.name} API answered HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ImportSourceError(f"The {source.name} API did not answer: {exc}") from None
    except (UnicodeDecodeError, ValueError):
        raise ImportRefusedError(f"The {source.name} API answered no JSON.") from None
    found: Any = answer
    for key in [part for part in read.rows.split(".") if part]:
        if not isinstance(found, Mapping) or key not in found:
            raise ImportRefusedError(f"The {source.name} API's answer holds no {read.rows}.")
        found = found[key]
    records = [found] if isinstance(found, Mapping) else found
    if not isinstance(records, list) or not all(isinstance(each, Mapping) for each in records):
        raise ImportRefusedError(
            f"The {source.name} API's answer at {read.rows or 'its top'} is no list of objects."
        )
    names = list(dict.fromkeys(str(key) for record in records for key in record))
    return names, _limited([record.get(name) for name in names] for record in records)


# ---------------------------------------------------------------------------
# The rows as the sheets hold them
# ---------------------------------------------------------------------------


def read_columns(table: SchemaTable, widget: ImportWidget) -> list[SchemaColumn]:
    """The columns of *table* an Import reads: each input an underwriter types (the details
    the app sets aside), and the index, which an update by key matches rows by."""
    managed = QUOTE_DETAILS_MANAGED if table.name == QUOTE_DETAILS_NAME else frozenset()
    return [
        column
        for column in table.inputs
        if column.name not in managed
        and (column.index in (None, "typed") or (widget.mode == "update" and column.index))
    ]


def imported_rows(
    table: SchemaTable, widget: ImportWidget, names: Sequence[str], rows: Rows
) -> list[dict[str, Cell]]:
    """*rows*, a source's rows under its columns' *names*, as *table*'s cells: each column the
    Import reads (:func:`read_columns`) from the source's column named for it, else of its
    name or its label, whatever its case; a column the source does not hold left out; text
    trimmed, any other value as a saved quote's comes back (``sheet_cell``), an empty one
    left out."""
    places = {name.casefold(): place for place, name in reversed(list(enumerate(names)))}
    renamed = {named.column: named.name for named in widget.column_names}
    taken: list[tuple[SchemaColumn, int]] = []
    for column in read_columns(table, widget):
        wanted = [renamed[column.name]] if column.name in renamed else [column.name, column.label]
        place = next((places[w.casefold()] for w in wanted if w and w.casefold() in places), None)
        if place is not None:
            taken.append((column, place))
    cells: list[dict[str, Cell]] = []
    for row in rows:
        held: dict[str, Cell] = {}
        for column, place in taken:
            value = row[place] if place < len(row) else None
            cell: Cell | None = (
                value.strip() if isinstance(value, str) else sheet_cell(column, value)
            )
            if cell is not None and cell != "":
                held[column.name] = cell
        cells.append(held)
    return cells
