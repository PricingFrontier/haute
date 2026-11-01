"""The analyst's test quotes (specs/workbench, Test quotes; Control flow 37).

A test quote is one JSON file in the project's test-quote folder, ``tests/quotes`` unless
``[test_quotes] dir`` names another, in the one-row shape deploy's test quotes take:
``[{"input": request, "expected": answer}]``. ``input`` is the request Preview prices (the
sample's request, under the stand-in identity), ``expected`` the Workbench Output's answer
as the underwriter app's price route gives it, accepted by the analyst. A test quote is
priced on the project as saved, as ``haute app build`` prices it in CI, and always from its
stored request, so Preview and CI price the same request and agree on what moved.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from haute._output_assembler import render_output_document
from haute._workbench_compare import (
    Move,
    SharedFields,
    compare_quote,
    form_fields,
    is_number,
)
from haute._workbench_config import WorkbenchError
from haute._workbench_form import QUOTE_DETAILS_ID, FormSpec, SchemaColumn, SchemaTable
from haute._workbench_input import IDENTITY_SHAPE, with_stand_in
from haute._workbench_tables import (
    finite_document,
    input_tables,
    preset_rows,
    priced_tables,
    quote_request,
)

if TYPE_CHECKING:
    from haute._workbench_app import ResolvedRelease

#: The longest name a test quote takes.
NAME_LIMIT = 100
#: What a test quote's name may hold: letters, digits, spaces, hyphens and underscores.
TEST_QUOTE_NAME = re.compile(r"[A-Za-z0-9 _-]+")
#: The report ``haute app build`` writes in the working directory.
REPORT_FILE = "test_quotes_report.md"
#: How long the editor's routes wait on one test quote's pricing: the underwriter app's own
#: bound on a pricing (``haute._workbench_app.PRICE_TIMEOUT_SECONDS``).
RUN_TIMEOUT_SECONDS = 60

#: How a reason names each answer.
_SIDES = ("in expected", "in the new answer")
#: The quote's identity's names, which a test quote is priced under the stand-in of.
_IDENTITY = frozenset(name for name, _ in IDENTITY_SHAPE)

Outcome = Literal["unchanged", "moved", "no_longer_compares", "failed"]


class TestQuoteError(WorkbenchError):
    """A test quote that cannot be saved, read or written as asked: what is wrong."""

    __test__ = False


class TestQuoteTakenError(TestQuoteError):
    """A new test quote's name another file of the folder already takes, in any case."""

    __test__ = False


@dataclass(frozen=True, slots=True)
class TestQuoteFile:
    """A file of the test-quote folder: its name (the file's without ``.json``), the file's
    name, its revision (a digest of its bytes), and, for a Preview test quote, its request,
    its expected answer and its tolerance; for any other, why the panel cannot open it."""

    __test__ = False

    name: str
    file: str
    revision: str
    input: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    tolerance: float | None = None
    #: The row's keys starting ``_``, deploy's metadata, kept as read when it is written again.
    metadata: dict[str, Any] = field(default_factory=dict)
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class Checked:
    """A test quote priced and compared: its outcome, the fields that moved, the tables and
    columns its ``expected`` holds that the form no longer has, why it failed or no longer
    compares, and the answer it was given (None when it failed)."""

    name: str
    file: str
    outcome: Outcome
    moves: tuple[Move, ...] = ()
    removed: tuple[str, ...] = ()
    why: str | None = None
    answer: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def revision_of(data: bytes) -> str:
    """A test quote file's revision: the sha256 of its bytes."""
    return hashlib.sha256(data).hexdigest()


def is_answer(value: Any) -> bool:
    """Whether *value* is an answer in the price route's shape: an object of tables, each a
    list of rows."""
    return isinstance(value, dict) and all(
        isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        for rows in value.values()
    )


def _shaped(rows: Any) -> bool:
    """Whether *rows* are a Preview test quote's shape: one row whose ``input`` is an object
    and whose ``expected`` is an answer, beside them only ``tolerance_pct`` and keys starting
    ``_`` (deploy's metadata, which deploy strips)."""
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return False
    row = rows[0]
    keys = {key for key in row if not key.startswith("_")}
    if not {"input", "expected"} <= keys or keys - {"input", "expected", "tolerance_pct"}:
        return False
    return isinstance(row["input"], dict) and is_answer(row["expected"])


def _tolerance_problem(row: Mapping[str, Any]) -> str | None:
    """What is wrong with a row's ``tolerance_pct`` by deploy's own rule, None when nothing
    is (or it gives none)."""
    from haute.deploy._validators import _parse_tolerance_pct

    if "tolerance_pct" not in row:
        return None
    try:
        _parse_tolerance_pct(row["tolerance_pct"], row_index=0)
    except ValueError as exc:
        return str(exc)
    return None


def is_test_quote(rows: Any) -> bool:
    """Whether a test-quote file's rows are one Preview test quote: its shape (one row, an
    object ``input``, an ``expected`` in the price route's shape, beside them only
    ``tolerance_pct`` and keys starting ``_``) with a tolerance deploy takes."""
    return _shaped(rows) and _tolerance_problem(rows[0]) is None


def read_test_quote(path: Path) -> TestQuoteFile:
    """The test quote at *path*, or, for a file that is not one, why."""
    data = path.read_bytes()
    name, file, revision = path.stem, path.name, revision_of(data)
    try:
        rows = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return TestQuoteFile(name, file, revision, problem="it is not JSON")
    if not _shaped(rows):
        return TestQuoteFile(
            name, file, revision, problem="it holds deploy's test quotes, not one saved here"
        )
    row = rows[0]
    problem = _tolerance_problem(row)
    if problem is not None:
        return TestQuoteFile(name, file, revision, problem=problem)
    tolerance = row.get("tolerance_pct")
    return TestQuoteFile(
        name,
        file,
        revision,
        input=row["input"],
        expected=row["expected"],
        tolerance=None if tolerance is None else float(tolerance),
        metadata={key: value for key, value in row.items() if key.startswith("_")},
    )


def read_test_quotes(directory: Path | None) -> list[TestQuoteFile]:
    """The folder's test-quote files by name, none when it has no folder."""
    from haute.deploy._config import test_quote_files

    if directory is None or not directory.is_dir():
        return []
    return [read_test_quote(path) for path in test_quote_files(directory)]


def check_name(name: str, taken: Iterable[str]) -> str:
    """*name* as a new test quote's, refused for what ``TEST_QUOTE_NAME`` does not take, past
    ``NAME_LIMIT`` characters, or matching a file of *taken* (file names) in any case."""
    name = name.strip()
    if not name:
        raise TestQuoteError("Name the test quote.")
    if len(name) > NAME_LIMIT:
        raise TestQuoteError(f"A test quote's name is at most {NAME_LIMIT} characters.")
    if not TEST_QUOTE_NAME.fullmatch(name):
        raise TestQuoteError(
            "A test quote's name holds letters, digits, spaces, hyphens and underscores only."
        )
    wanted = f"{name}.json".casefold()
    if any(file.casefold() == wanted for file in taken):
        raise TestQuoteTakenError(
            f"A test quote named {name!r} is already kept: pick another name."
        )
    return name


def test_quote_file(project_root: Path, directory: Path, file: str) -> Path:
    """The file *file* names in the test-quote *directory*: a test quote's name with
    ``.json`` (no separator, ``..`` or drive), a direct child of the folder and an ordinary
    path of the project's, else :class:`TestQuoteError` saying why."""
    from haute._artifact_paths import PATH_PROBLEMS, ordinary_path_problem

    stem = file.removesuffix(".json")
    if (
        not file.endswith(".json")
        or len(stem) > NAME_LIMIT
        or not TEST_QUOTE_NAME.fullmatch(stem)
        or stem.strip() != stem
    ):
        raise TestQuoteError(f"{file!r} is not a test quote's file name.")
    path = directory / file
    try:
        relative = path.relative_to(project_root).as_posix()
    except ValueError:
        raise TestQuoteError(f"{file} is not in the project's test-quote folder.") from None
    _, problem = ordinary_path_problem(project_root, relative)
    if problem is not None:
        raise TestQuoteError(f"{relative} {PATH_PROBLEMS[problem]}.")
    return path


def test_quote_bytes(
    request: Mapping[str, Any],
    expected: Mapping[str, Any],
    tolerance: float | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> bytes:
    """A test quote as its file holds it: the one-row array, indented two spaces, LF."""
    row: dict[str, Any] = {"input": request, "expected": expected}
    if tolerance is not None:
        row["tolerance_pct"] = tolerance
    row.update(metadata or {})
    return (json.dumps([row], indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# The request and its pricing
# ---------------------------------------------------------------------------


def request_limit() -> int:
    """The most bytes a stored request's JSON takes: what the underwriter app's price route
    takes in a request's body."""
    from haute.deploy._request_limits import deploy_quote_request_body_limit_bytes

    return deploy_quote_request_body_limit_bytes()


def request_bytes(request: Mapping[str, Any]) -> int:
    """The size of *request*'s JSON, as a price route's body would hold it."""
    return len(json.dumps(request, ensure_ascii=False).encode("utf-8"))


def too_large(request: Mapping[str, Any]) -> str | None:
    """Why *request* is too large to price, None when it is not."""
    size, limit = request_bytes(request), request_limit()
    if size <= limit:
        return None
    return f"it is larger than the app takes ({size:,} bytes; the app takes {limit:,})"


def effective_request(form: FormSpec) -> dict[str, Any]:
    """The request Preview prices for the form's sample: the request its values build, under
    the stand-in identity; empty when nothing is typed."""
    request = quote_request(form, form.sample)
    if not request:
        return {}
    return with_stand_in(input_tables(form), request)


def price_request(release: ResolvedRelease, request: Mapping[str, Any]) -> dict[str, Any]:
    """*request* priced on *release* through the scorer the underwriter app prices with, its
    answer rendered as the app's price route renders it, whole."""
    from haute.deploy._scorer import QuoteRequest, admit_deploy_execution, score_graph

    resolved = release.resolved
    context = admit_deploy_execution(operation="workbench_test_quote", row_count=1)
    failed = False
    try:
        result = score_graph(
            graph=resolved.pruned_graph,
            input_df=QuoteRequest((dict(request),)),
            input_node_ids=resolved.input_node_ids,
            output_node_id=resolved.output_node_id,
            artifact_paths={name: str(path) for name, path in resolved.artifacts.items()},
            output_fields=resolved.config.output_fields,
            execution_context=context,
            retain_admission_on_success=True,
        )
        [document] = render_output_document(result)
        return priced_tables(finite_document(document))
    except BaseException:
        failed = True
        raise
    finally:
        context.release_admission(preserve_primary_error=failed)


def basis(release: ResolvedRelease) -> str:
    """What *release* prices on, as a digest: its graph as shipped, its form's revision with
    the allowed values its params files give, ``[deploy] output_fields``, each artifact's
    content digest as resolution recorded it, and the bundled ``utility`` code. A run's basis
    that differs from the project's now means the pricing may have changed."""
    from haute._workbench_form import form_revision, render_form
    from haute.deploy._utils import shipped_graph

    resolved = release.resolved
    held = {
        "graph": shipped_graph(resolved.pruned_graph),
        # The sample is left out: typing in Preview changes no pricing a test quote replays.
        "form": form_revision(
            render_form(release.form.model_copy(update={"sample": {}})).encode("utf-8")
        ),
        "output_fields": resolved.config.output_fields,
        "artifacts": dict(sorted(resolved.artifact_digests.items())),
        "utility": _utility_digest(resolved.project_modules.utility),
    }
    return hashlib.sha256(json.dumps(held, sort_keys=True, default=str).encode()).hexdigest()


def _utility_digest(utility: Path | None) -> str | None:
    """The bundled ``utility`` package's or file's bytes, as one digest; None without one."""
    if utility is None:
        return None
    files = sorted(utility.rglob("*.py")) if utility.is_dir() else [utility]
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.relative_to(utility.parent).as_posix().encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def _fields(form: FormSpec) -> SharedFields:
    return form_fields(form.model_dump(mode="json", by_alias=True))


def _removed(fields: SharedFields, expected: Mapping[str, Any]) -> tuple[str, ...]:
    """The tables and columns *expected* holds that the form's output tables no longer
    have."""
    tables = {table.name: table for table in fields.tables}
    removed: list[str] = []
    for name, rows in expected.items():
        table = tables.get(name)
        if table is None:
            removed.append(name)
            continue
        seen: list[str] = []
        for row in rows if isinstance(rows, list) else [rows]:
            for column in row if isinstance(row, Mapping) else ():
                # Every output row holds the quote's identity, which no form lists.
                if column not in table.columns and column not in _IDENTITY and column not in seen:
                    seen.append(column)
        removed.extend(f"{name}.{column}" for column in seen)
    return tuple(removed)


def _said(error: BaseException) -> str:
    message = getattr(error, "message", None)
    return str(message or error) or type(error).__name__


def check_test_quote(release: ResolvedRelease, quote: TestQuoteFile) -> Checked:
    """*quote* priced on *release* from its stored request and compared with its expected
    answer on the form's output fields, with its tolerance."""
    assert quote.input is not None and quote.expected is not None
    large = too_large(quote.input)
    if large is not None:
        return Checked(quote.name, quote.file, "failed", why=large)
    try:
        answer = price_request(release, quote.input)
    # A pipeline's own code can raise anything on a request: the quote fails, saying why.
    except Exception as error:  # noqa: BLE001
        return Checked(quote.name, quote.file, "failed", why=_said(error))
    fields = _fields(release.form)
    compared = compare_quote(
        fields, quote.expected, answer, sides=_SIDES, tolerance=quote.tolerance
    )
    if compared.why is not None:
        return Checked(
            quote.name, quote.file, "no_longer_compares", why=compared.why, answer=answer
        )
    removed = _removed(fields, quote.expected)
    outcome: Outcome = "moved" if compared.moves or removed else "unchanged"
    return Checked(
        quote.name, quote.file, outcome, moves=compared.moves, removed=removed, answer=answer
    )


def check_folder(release: ResolvedRelease, directory: Path | None) -> tuple[Checked, ...]:
    """Every file of the test-quote folder priced: a Preview test quote checked, any other
    scored as deploy scores it, a file that does not read as test quotes failed."""
    from haute.deploy._validators import score_test_quote_file

    results: list[Checked] = []
    for quote in read_test_quotes(directory):
        if quote.input is not None:
            results.append(check_test_quote(release, quote))
            continue
        assert directory is not None
        scored = score_test_quote_file(release.resolved, directory / quote.file)
        if scored["status"] == "ok":
            results.append(Checked(quote.name, quote.file, "unchanged"))
        else:
            results.append(Checked(quote.name, quote.file, "failed", why=str(scored["error"])))
    return tuple(results)


# ---------------------------------------------------------------------------
# Opening a test quote on the sheets
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str | bool | None:
    """A request's value as the cell text it reads as, as the quote store puts a stored value
    back: true a tick, false and null no cell, a whole number its digits, a decimal the
    shortest text that reads back as it, anything else as its text."""
    if value is None or value is False:
        return None
    if value is True:
        return True
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value).removesuffix(".0")
    return str(value)


def _fits(column: SchemaColumn, value: Any) -> bool:
    """Whether *column* takes *value* as it stands: a tick in a True/false column and nothing
    else there, a number in a number column, one of its allowed values when it has them."""
    if column.type == "bool":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if column.type in ("int", "float") and not is_number(value):
        return False
    if column.type == "int" and isinstance(value, float) and not value.is_integer():
        return False
    return not column.options or _cell(value) in column.options


def _row_values(
    table: SchemaTable, row: Any, where: str, not_opened: list[str]
) -> dict[str, str | bool]:
    columns = {column.name: column for column in table.columns}
    cells: dict[str, str | bool] = {}
    if not isinstance(row, Mapping):
        not_opened.append(where)
        return cells
    for name, value in row.items():
        column = columns.get(name)
        if table.id == QUOTE_DETAILS_ID and name in _IDENTITY:
            continue
        if column is None:
            not_opened.append(f"{where}.{name}")
            continue
        if column.index:
            continue
        if value is not None and not _fits(column, value):
            not_opened.append(f"{where}.{name}")
            continue
        cell = _cell(value)
        if cell is not None:
            cells[column.id] = cell
    return cells


def opened_values(
    form: FormSpec, request: Mapping[str, Any]
) -> tuple[dict[str, list[dict[str, str | bool]]], list[str]]:
    """*request* put back on *form*'s sheets: each input table's rows by table and column
    name as the sheets hold values, and each value it cannot place, named."""
    tables = {table.name: table for table in form.data_schema.tables if table.role == "input"}
    values: dict[str, list[dict[str, str | bool]]] = {}
    not_opened: list[str] = []
    for name, held in request.items():
        table = tables.get(name)
        if table is None:
            not_opened.append(name)
            continue
        if table.rows == "one":
            rows: Sequence[Any] = [held]
        elif isinstance(held, list):
            rows = held
        else:
            not_opened.append(name)
            continue
        cells = [
            _row_values(table, row, name if table.rows == "one" else f"{name}[{place}]", not_opened)
            for place, row in enumerate(rows, 1)
        ]
        values[table.id] = [dict(row) for row in preset_rows(table, cells)]
    return values, not_opened


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    if value is None:
        text = "empty"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _move_line(move: Move) -> str:
    row = _text(move.row) if move.row is not None else ""
    if move.column is None:
        held = "row added" if move.after else "row removed"
        return f"| `{move.table}` | {row} | {held} | |"
    return f"| `{move.table}.{move.column}` | {row} | {_text(move.before)} | {_text(move.after)} |"


def render_test_quotes_report(results: Sequence[Checked]) -> str:
    """The test quotes' results as Markdown: the counts, each quote that moved or no longer
    compares with its fields, and each failure with why."""
    counts = {
        outcome: sum(1 for result in results if result.outcome == outcome)
        for outcome in ("unchanged", "moved", "no_longer_compares", "failed")
    }
    lines = ["# Test quotes", ""]
    if not results:
        lines.append("The project keeps no test quotes.")
        return "\n".join(lines) + "\n"
    lines += [
        "| Outcome | Test quotes |",
        "|---|---|",
        f"| Unchanged | {counts['unchanged']} |",
        f"| Moved | {counts['moved']} |",
        f"| No longer compare | {counts['no_longer_compares']} |",
        f"| Failed | {counts['failed']} |",
        "",
    ]
    moving = [result for result in results if result.outcome == "moved"]
    if moving:
        lines += ["## Moved", ""]
        for result in moving:
            lines += [
                f"### `{result.file}`",
                "",
                "| Field | Row | Expected | New |",
                "|---|---|---|---|",
            ]
            lines += [_move_line(move) for move in result.moves]
            lines += [f"| `{field}` | | removed from the form | |" for field in result.removed]
            lines.append("")
    for outcome, title in (("no_longer_compares", "No longer compare"), ("failed", "Failed")):
        named = [result for result in results if result.outcome == outcome]
        if named:
            lines += [f"## {title}", ""]
            lines += [f"- `{result.file}`: {_text(result.why)}" for result in named]
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
