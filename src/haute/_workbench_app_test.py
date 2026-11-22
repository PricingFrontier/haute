"""The packaged underwriter app tested before any host sees it (specs/workbench, Control
flow 39): ``haute app test``.

The release ``haute app build`` wrote is served on loopback through the app it ships
(:func:`haute._workbench_app.create_app`, as its ``app.py`` serves it), on a store of its
own made for the run alone from the SQL Haute writes for the release's form: a SQLite file,
or a database made on a server engine, as a CI job's service container runs it, and dropped
after. Through the app's own routes, each of the project's test quotes is put on the sheets,
priced, made a quote, saved priced, opened again and checked to hold what was saved, marked
final, and exported by each Export to an Excel workbook of cells the form holds. Nothing of a
hosted environment is reached: no host, no environment's database.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote as escaped

from haute._quote_store import SignIn, StoreLocation, offline_plan, render_sql, store_url
from haute._quote_store._migrate import PROVIDER_DIALECTS
from haute._quote_store._providers import provider_of
from haute._workbench_config import WorkbenchError
from haute._workbench_form import ExcelCellsExport, ExportWidget, FormSpec
from haute._workbench_rollout import HttpAnswer, fetch, post

#: Who the app signs in every request of the run as.
PRINCIPAL = "app-test@haute.invalid"
#: The environment the run's store is said to be in, which no release declares.
ENVIRONMENT = "app-test"
#: How long the app is given to start answering, in seconds.
START_SECONDS = 30.0
#: How long one request of the run may take, in seconds: a pricing's own bound and more.
REQUEST_SECONDS = 90.0
#: A workbook's first bytes: a zip's.
_WORKBOOK = b"PK"


class AppTestError(WorkbenchError):
    """The packaged app could not be tested as asked: the message says what to fix."""


@dataclass
class AppTestReport:
    """What the run found: each step done, in order, and each that failed, by what it
    checked."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The store, made for the run alone
# ---------------------------------------------------------------------------


def _script(location: StoreLocation, script: str) -> None:
    """Run *script*, the SQL Haute writes, on *location*'s database as its owners run it: in
    autocommit, a statement's error raised."""
    if location.url.startswith("sqlite:"):
        database = location.url.removeprefix("sqlite:///")
        connection = sqlite3.connect(database)
        try:
            connection.executescript(script)
        finally:
            connection.close()
        return
    from haute._quote_store._store import make_engine

    engine = make_engine(location.url, sign_in=location.sign_in)
    raw = engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True  # type: ignore[union-attr]
        cursor = raw.cursor()
        cursor.execute(script)
        # SQL Server's driver raises a statement's error once its result is reached.
        while cursor.nextset():
            pass
    finally:
        raw.close()
        engine.dispose()


def _made(location: StoreLocation, form: FormSpec) -> StoreLocation:
    """*location* holding *form*'s tables, made from the SQL Haute writes for its engine."""
    provider = provider_of(location.url)
    plan = offline_plan(None, form, provider=provider)
    _script(location, render_sql(plan, form, PROVIDER_DIALECTS[provider.name]))
    return location


def _release_form(release_dir: Path) -> FormSpec:
    from haute._workbench_app import read_release

    return read_release(release_dir)[1]


@contextlib.contextmanager
def sqlite_store(release_dir: Path) -> Iterator[StoreLocation]:
    """A SQLite store of the tables of the form the release in *release_dir* ships, in a
    folder of the run's own, removed after."""
    form = _release_form(release_dir)
    with tempfile.TemporaryDirectory(prefix="haute-app-test-") as folder:
        yield _made(StoreLocation(ENVIRONMENT, store_url(Path(folder) / "quotes.sqlite")), form)


@contextlib.contextmanager
def server_store(url: str, sign_in: SignIn, release_dir: Path) -> Iterator[StoreLocation]:
    """A database of the run's own made on the server *url* names, a database its login may
    make and drop databases from (SQL Server's ``master`` or PostgreSQL's ``postgres``),
    holding the tables of the form the release in *release_dir* ships, dropped after."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from haute._quote_store._store import make_engine

    provider = provider_of(url)
    if provider.name == "sqlite":
        raise AppTestError("A SQLite URL is no server engine: pass a server's URL.")
    form = _release_form(release_dir)
    name = f"haute_app_test_{uuid.uuid4().hex[:12]}"
    admin = make_engine(url, sign_in=sign_in).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {name}"))
        try:
            made = make_url(url).set(database=name).render_as_string(hide_password=False)
            yield _made(StoreLocation(ENVIRONMENT, made, sign_in=sign_in), form)
        finally:
            with admin.connect() as connection:
                if provider.name == "sqlserver":
                    connection.execute(
                        text(f"ALTER DATABASE {name} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
                    )
                    connection.execute(text(f"DROP DATABASE {name}"))
                else:
                    connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
    finally:
        admin.engine.dispose()


# ---------------------------------------------------------------------------
# The app, served on loopback
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def served(release_dir: Path, location: StoreLocation) -> Iterator[str]:
    """The release's app served on a free loopback port, signed in as :data:`PRINCIPAL`, its
    quotes kept at *location*: its address while the block runs."""
    import uvicorn

    from haute._workbench_app import create_app

    application = create_app(release_dir, local_principal=PRINCIPAL, store=location)
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, name="haute-app-test", daemon=True)
    thread.start()
    deadline = time.monotonic() + START_SECONDS
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            server.should_exit = True
            raise AppTestError("The release's app did not start.")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(START_SECONDS)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _App:
    """The served app as the run reaches it: its address, and the release and sheets' revision
    every write names."""

    address: str
    release: str
    revision: str

    def get(self, path: str) -> HttpAnswer:
        return fetch(f"{self.address}{path}", {})

    def post(self, path: str, body: Mapping[str, Any]) -> HttpAnswer:
        return post(
            f"{self.address}{path}", {}, json.dumps(body).encode("utf-8"), timeout=REQUEST_SECONDS
        )


def _body(answer: HttpAnswer) -> Any:
    try:
        return json.loads(answer.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _why(answer: HttpAnswer) -> str:
    """What the app said of a request it did not answer 200, for the analyst reading CI."""
    if answer.status == 0:
        return f"no answer: {answer.body.decode('utf-8', 'replace')}"
    body = _body(answer)
    detail = body.get("detail") if isinstance(body, dict) else None
    said = detail.get("message") if isinstance(detail, Mapping) else detail
    return f"HTTP {answer.status}" + (f": {said}" if isinstance(said, str) else "")


def cells_exports(form: FormSpec) -> list[ExportWidget]:
    """The form's Exports to an Excel workbook of cells, the exports the app runs."""
    return [
        widget
        for page in form.pages
        for section in page.sections
        for widget in section.widgets
        if isinstance(widget, ExportWidget) and isinstance(widget.target, ExcelCellsExport)
    ]


def _quote_run(
    app: _App,
    form: FormSpec,
    name: str,
    values: Mapping[str, Any],
    reference: str,
    report: AppTestReport,
) -> None:
    """One test quote through the app as the page takes one: made under *reference*, priced
    under the identity the app gave it, saved priced, opened again and checked to hold what
    was saved, then marked final and exported, each step said in *report*."""
    operations = iter(f"{reference}-{step}" for step in range(1_000))
    made = app.post(
        "/api/app/quotes",
        {
            "operation": next(operations),
            "form_revision": app.revision,
            "release": app.release,
            "values": values,
            "reference": reference,
        },
    )
    if made.status != 200:
        report.failed.append(f"{name}: made a quote, {_why(made)}")
        return
    quote = _body(made)["quote"]
    # The values as the store holds them, the details the app set on the quote among them.
    held = _body(made)["values"]
    priced = app.post(
        "/api/app/price", {"form_revision": app.revision, "release": app.release, "values": held}
    )
    if priced.status != 200:
        report.failed.append(f"{name}: priced in the app, {_why(priced)}")
        return
    address = f"/api/app/quotes/{escaped(quote['reference'], safe='')}/{quote['option']}"
    saved = app.post(
        f"{address}/save",
        {
            "operation": next(operations),
            "form_revision": app.revision,
            "release": app.release,
            "version": quote["version"],
            "values": held,
            "shown": _body(priced)["tables"],
        },
    )
    if saved.status != 200:
        report.failed.append(f"{name}: saved priced, {_why(saved)}")
        return
    kept = _body(saved)
    opened = app.get(address)
    if opened.status != 200:
        report.failed.append(f"{name}: opened again, {_why(opened)}")
        return
    if _body(opened)["values"] != kept["values"]:
        report.failed.append(f"{name}: opened again, its values are not those saved")
        return
    report.passed.append(f"{name}: priced, saved and opened again")
    exports = cells_exports(form)
    if not exports:
        return
    final = app.post(f"{address}/final", {"version": kept["quote"]["version"]})
    if final.status != 200:
        report.notes.append(f"{name}: not exported, as it was not marked final: {_why(final)}")
        return
    version = _body(final)["quote"]["version"]
    for export in exports:
        exported = app.post(f"{address}/export", {"component": export.id, "version": version})
        label = export.label.strip() or export.name
        if exported.status != 200 or not exported.body.startswith(_WORKBOOK):
            report.failed.append(f'{name}: exported by "{label}", {_why(exported)}')
        else:
            report.passed.append(f'{name}: exported by "{label}"')


def run_app_test(
    release_dir: Path,
    location: StoreLocation,
    quotes: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    say: Callable[[str], None] = lambda _line: None,
) -> AppTestReport:
    """The release in *release_dir* served on *location*'s store, which holds its form's
    tables and nothing else, and each of *quotes*, a test quote's name and its request, run
    through it (:func:`_quote_run`); each line said through *say* as it is found."""
    from haute._workbench_app import read_release
    from haute._workbench_test_quotes import opened_values

    _release, form, _manifest = read_release(release_dir)
    report = AppTestReport()
    with served(release_dir, location) as address:
        shown = fetch(f"{address}/api/app/release", {})
        state = _body(shown) if shown.status == 200 else None
        if state is None or not state["quotes"]["open"]:
            why = _why(shown) if state is None else state["quotes"]["message"]
            report.failed.append(f"the app opened its store: {why}")
            return report
        report.passed.append("the app opened its store")
        app = _App(address, state["id"], state["form_revision"])
        for place, (name, request) in enumerate(quotes, 1):
            values, not_opened = opened_values(form, request)
            if not_opened:
                report.failed.append(f"{name}: put on the sheets, but not " + ", ".join(not_opened))
                continue
            _quote_run(app, form, name, values, f"APP-TEST-{place}", report)
    if not quotes:
        report.notes.append("no test quotes: the app opened its store and saved no quote")
    for line in report.passed:
        say(f"  ✓ {line}")
    for line in report.notes:
        say(f"  ! {line}")
    for line in report.failed:
        say(f"  ✗ {line}")
    return report
