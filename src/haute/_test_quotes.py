"""The test quotes an analyst keeps in Preview (specs/workbench): the project's test-quote file
and what a test quote is saved with.

The file is ``test_quotes.sqlite`` in the test-quote directory, ``[test_quotes] dir`` of
``haute.toml`` (``tests/quotes`` when absent), beside the hand-written test-quote files deploy
reads, and the quote store holds it. A test quote keeps its values, the request Price builds
from them and the response its pricing gave, shaped as a deployed pipeline answers.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from haute._sandbox import contained_path
from haute._workbench_config import WorkbenchError
from haute._workbench_form import FormSpec
from haute._workbench_tables import output_tables, port_tables, sample_quote
from haute.errors import PathOutsideProjectError

#: The test-quote file's name, in the test-quote directory.
TEST_QUOTES_FILE = "test_quotes.sqlite"
#: The test-quote directory when ``haute.toml`` names none, as ``haute init`` writes it.
DEFAULT_TEST_QUOTES_DIR = "tests/quotes"
#: A test quote's accepted numbers are matched within one part in a billion, so a pricing
#: that differs only in a float's last bits is no change.
ACCEPTED_TOLERANCE = 1e-9


class WorkbenchTestQuoteError(WorkbenchError):
    """A test quote cannot be saved as asked: the message says why."""

    error_code = "test_quote_invalid"


def project_test_quotes_path(project_root: Path) -> Path:
    """The project's test-quote file: ``test_quotes.sqlite`` in its test-quote directory."""
    directory = DEFAULT_TEST_QUOTES_DIR
    toml_path = project_root / "haute.toml"
    if toml_path.is_file():
        try:
            with toml_path.open("rb") as handle:
                table = tomllib.load(handle).get("test_quotes", {})
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise WorkbenchTestQuoteError(f"haute.toml could not be read: {exc}") from exc
        configured = table.get("dir") if isinstance(table, dict) else None
        if isinstance(configured, str) and configured:
            directory = configured
    try:
        return contained_path(project_root.resolve(), directory) / TEST_QUOTES_FILE
    except PathOutsideProjectError as exc:
        raise WorkbenchTestQuoteError(
            f"[test_quotes] dir {directory!r} must be a folder inside the project."
        ) from exc


def quote_request(spec: FormSpec, values: Mapping[str, Any]) -> dict[str, Any]:
    """The request Price builds from *values* typed into *spec*'s sheets: each input table's
    filled rows by name, read as the server reads the sample, the cells the sheets hide left
    out."""
    return sample_quote(spec.model_copy(update={"sample": dict(values)}))


def response_document(
    spec: FormSpec, tables: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    """The priced *tables*, each output table's rows by name, as a deployed pipeline answers:
    a one-row table as an object of its columns and a many-row table as a list of them.

    Raises :class:`WorkbenchTestQuoteError` naming a table the pricing did not give, or a
    one-row table priced with other than one row.
    """
    document: dict[str, Any] = {}
    for table in port_tables(output_tables(spec)):
        rows = tables.get(table.name)
        if rows is None:
            raise WorkbenchTestQuoteError(
                f"The priced response has no {table.name!r} table: price the quote again."
            )
        if not table.one_row:
            document[table.name] = [dict(row) for row in rows]
            continue
        if len(rows) != 1:
            raise WorkbenchTestQuoteError(
                f"The {table.name!r} table has one row per quote, but the pricing gave it "
                f"{len(rows)} rows."
            )
        document[table.name] = dict(rows[0])
    return document
